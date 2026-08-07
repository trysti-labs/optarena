"""
optarena/_cases/_mock_service.py
─────────────────────────────
The oracle backend for tool-use cases: an in-process, stdlib-only mock API a
tool-calling driver dispatches function calls against, instead of a
filesystem. Same role `DockerSandbox`/`check_command` play for coding cases -
a deterministic, tool-neutral ground truth - just for "did the agent call the
right tools with the right arguments" instead of "did the agent write the
right file".

A fresh service instance is created per case run (never shared across cases
or trials), so state never leaks between them - the same isolation
`prepare_workspace`'s fresh-workspace-per-case gives the filesystem oracle.

Adding a service = one class + one registry entry, mirroring how adding a
driver is one module + one registry entry (drivers/__init__.py) and adding a
sandbox track is one Dockerfile + one DOCKER_IMAGES entry (_sandbox.py).
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable


class MockService:
    """Base contract every mock service implements.

    Subclasses expose their tools as plain methods; ``TOOLS`` maps the
    tool name a case/schema uses to the bound-method-returning attribute
    name, so ``dispatch`` never needs a subclass-specific branch.
    """

    #: tool name -> method name on the instance. Subclasses set this.
    TOOLS: dict[str, str] = {}

    def __init__(self) -> None:
        self.call_log: list[dict] = []

    def seed(self, spec: dict) -> None:
        """Establish initial state from a case's ``tool_service_seed``,
        called once right after construction, BEFORE the conversation
        starts and BEFORE anything is dispatched - never logged to
        ``call_log``, so it never shows up in what the oracle grades the
        agent on. Default no-op; a service with no meaningful "starting
        state" concept (task_tracker: every case starts empty) doesn't
        need to override this. Subclasses define their own spec shape -
        there's no shared schema across services, same as tool arguments
        already aren't shared across services.
        """

    @staticmethod
    def _as_list(value) -> list:
        """Real models sometimes pass a single item as a bare scalar
        instead of a one-element list (git_add's ``paths``,
        read_multiple_files's ``paths``, ...) - normalize rather than let
        it silently iterate character-by-character (for a string) or fail
        outright."""
        if isinstance(value, str):
            return [value]
        return list(value or [])

    def dispatch(self, tool_name: str, arguments: dict) -> Any:
        """Call ``tool_name`` with ``arguments``, log it, and return its
        result - never raises. An unknown tool name or a tool that raises
        internally both become an ``{"error": ...}`` result, exactly what a
        real function-calling loop hands back to the model as the tool's
        response - the model (and the oracle, via ``forbidden_calls``) sees
        a hallucinated/failed call as data, not a crash.
        """
        arguments = dict(arguments or {})
        method_name = self.TOOLS.get(tool_name)
        if method_name is None:
            result: Any = {"error": f"unknown tool {tool_name!r}"}
        else:
            method: Callable = getattr(self, method_name)
            try:
                result = method(**arguments)
            except TypeError as exc:
                # Wrong/missing arguments - the model's own mistake, not a
                # code bug, so this is a modeled result, not a driver crash.
                result = {"error": f"invalid arguments for {tool_name!r}: {exc}"}
            except Exception as exc:  # noqa: BLE001 - a bad call is data, not a crash
                result = {"error": f"{type(exc).__name__}: {exc}"}
        self.call_log.append({"tool": tool_name, "arguments": arguments, "result": result})
        return result

    def summary(self) -> dict:
        """A flat, assertable snapshot of final state - what
        ``expected_final_state`` in a case checks against. Subclasses
        override; the base returns just the call count."""
        return {"n_calls": len(self.call_log)}


class TaskTrackerService(MockService):
    """A minimal task tracker: create/complete/list/delete. Deliberately
    small (four tools, one flat resource) - the point is a clean, obviously-
    correct ground truth, not a realistic app."""

    TOOLS = {
        "create_task": "create_task",
        "complete_task": "complete_task",
        "list_tasks": "list_tasks",
        "delete_task": "delete_task",
    }

    def __init__(self) -> None:
        super().__init__()
        self._tasks: dict[int, dict] = {}
        self._next_id = 1

    def create_task(self, title: str, assignee: str | None = None) -> dict:
        if not title or not isinstance(title, str):
            return {"error": "title is required"}
        task_id = self._next_id
        self._next_id += 1
        task = {"id": task_id, "title": title, "assignee": assignee, "status": "open"}
        self._tasks[task_id] = task
        return dict(task)

    def complete_task(self, task_id: int) -> dict:
        task = self._tasks.get(int(task_id))
        if task is None:
            return {"error": f"no such task_id {task_id!r}"}
        task["status"] = "done"
        return dict(task)

    def list_tasks(self, status: str | None = None) -> list[dict]:
        tasks = self._tasks.values()
        if status is not None:
            tasks = (t for t in tasks if t["status"] == status)
        return [dict(t) for t in sorted(tasks, key=lambda t: t["id"])]

    def delete_task(self, task_id: int) -> dict:
        task = self._tasks.pop(int(task_id), None)
        if task is None:
            return {"error": f"no such task_id {task_id!r}"}
        return {"deleted": task_id}

    def summary(self) -> dict:
        tasks = self._tasks.values()
        return {
            "n_calls": len(self.call_log),
            "task_count": len(self._tasks),
            "open_count": sum(1 for t in tasks if t["status"] == "open"),
            "completed_count": sum(1 for t in self._tasks.values() if t["status"] == "done"),
        }


class GitRepoService(MockService):
    """A mock git repository - every tool the official + widely-used
    extended MCP git servers expose (status/add/reset/commit/diff variants/
    log/show/branch operations/blame/remotes/tags/push/pull), covering all
    18 tools catalogued in DEV_NOTES/TOOL_CATALOG_COMPLETE.md §1.

    Scope decision: git tools alone never CREATE file content (no
    filesystem service in this domain yet - see TOOL_USE_EXPANSION_PLAN.md
    §2) - so the working tree is fixed at ``seed()`` time and never changes
    during a case. That's not a limitation for what this service tests:
    every one of these tools is about git WORKFLOW judgment (stage the
    right things, commit at the right granularity, branch before editing,
    don't push before committing, don't pull-then-overwrite blindly) - none
    of it depends on the agent authoring content itself.
    """

    TOOLS = {name: name for name in (
        "git_status", "git_add", "git_reset", "git_commit",
        "git_diff_unstaged", "git_diff_staged", "git_diff",
        "git_log", "git_show", "git_branch", "git_create_branch", "git_checkout",
        "git_blame", "git_remotes", "git_tags", "git_tag", "git_push", "git_pull",
    )}

    def __init__(self) -> None:
        super().__init__()
        self._commits: dict[str, dict] = {}          # commit_id -> {message, files, parent, seq}
        self._branches: dict[str, str | None] = {"main": None}
        self._current_branch = "main"
        self._working_dir: dict[str, str] = {}        # fixed at seed() time - see class docstring
        self._staged: dict[str, str] = {}
        self._remotes: dict[str, str] = {"origin": "https://example.invalid/repo.git"}
        self._remote_branches: dict[str, dict[str, str]] = {"origin": {}}
        self._tags: dict[str, str] = {}
        self._next_seq = 1

    # ── seeding (test-fixture setup, never logged/graded) ──────────────

    def seed(self, spec: dict) -> None:
        """``spec`` keys, all optional:
        - ``committed`` ({path: content}): creates one initial commit with
          this snapshot on the current branch. Sugar for the common single-
          commit-history case; see ``initial_commits`` for more than one.
        - ``committed_message``: message for that initial commit (default
          "Initial commit").
        - ``initial_commits`` ([{message, files}, ...]): builds LOCAL
          history from a sequence of commits (each ``files`` dict merges
          onto the previous snapshot, same partial-update semantics as
          ``remote_ahead_commits`` below) - for cases that need real,
          inspectable multi-commit history (git_log/git_show/git_blame).
          Applied after ``committed`` if both are given, continuing from it.
        - ``working_dir`` ({path: content}): overlaid on top of the
          resulting HEAD snapshot as the working tree - a key with the SAME
          content as HEAD is clean/untouched; different content is a
          modification; a key absent from HEAD is untracked/new.
        - ``remote_ahead_commits`` ([{message, files}, ...]): additional
          commits that exist on ``origin``'s current branch but not yet
          locally - for git_pull/git_push cases. Each ``files`` dict is
          merged onto the previous snapshot (a partial update, not a full
          replacement), same as a real commit only changing what it touches.
        """
        committed = spec.get("committed")
        if committed:
            commit_id = self._new_commit(dict(committed), spec.get("committed_message", "Initial commit"), None)
            self._branches[self._current_branch] = commit_id
            self._working_dir = dict(committed)
        initial_commits = spec.get("initial_commits")
        if initial_commits:
            parent = self._branches[self._current_branch]
            files = dict(self._commits[parent]["files"]) if parent else {}
            for entry in initial_commits:
                files = dict(files)
                files.update(entry.get("files", {}))
                parent = self._new_commit(files, entry["message"], parent)
            self._branches[self._current_branch] = parent
            self._working_dir = dict(files)
        working_dir_overlay = spec.get("working_dir")
        if working_dir_overlay:
            self._working_dir.update(working_dir_overlay)
        remote_ahead = spec.get("remote_ahead_commits")
        if remote_ahead:
            parent = self._branches[self._current_branch]
            files = dict(self._commits[parent]["files"]) if parent else {}
            for entry in remote_ahead:
                files = dict(files)
                files.update(entry.get("files", {}))
                parent = self._new_commit(files, entry["message"], parent)
            self._remote_branches["origin"][self._current_branch] = parent

    def _new_commit(self, files: dict, message: str, parent: str | None) -> str:
        commit_id = f"c{self._next_seq}"
        self._next_seq += 1
        self._commits[commit_id] = {"message": message, "files": files, "parent": parent, "seq": self._next_seq - 1}
        return commit_id

    # ── internal helpers ────────────────────────────────────────────────

    def _head_commit_id(self, branch: str | None = None) -> str | None:
        return self._branches.get(branch or self._current_branch)

    def _head_files(self, branch: str | None = None) -> dict[str, str]:
        cid = self._head_commit_id(branch)
        return dict(self._commits[cid]["files"]) if cid else {}

    def _resolve_ref(self, ref: str | None) -> str | None:
        """A ref is a branch name, a tag name, a literal commit id, or the
        literal string "HEAD" (current branch's tip) - the same alias real
        git supports, so a model that reaches for it isn't penalized for
        not knowing an opaque mock commit id it was never shown."""
        if ref is None or ref == "HEAD":
            return self._head_commit_id()
        if ref in self._branches:
            return self._branches[ref]
        if ref in self._tags:
            return self._tags[ref]
        if ref in self._commits:
            return ref
        return None

    def _ancestors(self, commit_id: str | None) -> list[str]:
        out = []
        while commit_id is not None:
            out.append(commit_id)
            commit_id = self._commits[commit_id]["parent"]
        return out

    # ── tools ────────────────────────────────────────────────────────────

    def git_status(self) -> dict:
        head = self._head_files()
        staged = sorted(self._staged)
        unstaged_modified = sorted(
            p for p, content in self._working_dir.items()
            if p in head and content != head[p] and p not in self._staged
        )
        untracked = sorted(
            p for p in self._working_dir if p not in head and p not in self._staged
        )
        return {"staged": staged, "unstaged_modified": unstaged_modified, "untracked": untracked}

    def git_add(self, paths) -> dict:
        paths = self._as_list(paths)
        added, missing = [], []
        for p in paths:
            if p not in self._working_dir:
                missing.append(p)
                continue
            self._staged[p] = self._working_dir[p]
            added.append(p)
        result = {"staged": added}
        if missing:
            result["error"] = f"no such path(s) in working directory: {missing}"
        return result

    def git_reset(self) -> dict:
        count = len(self._staged)
        self._staged.clear()
        return {"unstaged": count}

    def git_commit(self, message: str) -> dict:
        if not self._staged:
            return {"error": "nothing staged to commit"}
        if not message or not isinstance(message, str):
            return {"error": "commit message is required"}
        files = self._head_files()
        files.update(self._staged)
        parent = self._head_commit_id()
        commit_id = self._new_commit(files, message, parent)
        self._branches[self._current_branch] = commit_id
        self._staged.clear()
        return {"commit_id": commit_id, "message": message, "files_committed": sorted(files)}

    def git_diff_unstaged(self) -> dict:
        head = self._head_files()
        diff = {
            p: {"before": head[p], "after": self._working_dir[p]}
            for p in self._working_dir
            if p in head and self._working_dir[p] != head[p] and p not in self._staged
        }
        return {"files": diff}

    def git_diff_staged(self) -> dict:
        head = self._head_files()
        diff = {
            p: {"before": head.get(p), "after": content}
            for p, content in self._staged.items()
            if head.get(p) != content
        }
        return {"files": diff}

    def git_diff(self, ref_a: str | None = None, ref_b: str | None = None) -> dict:
        a_files = self._commits[self._resolve_ref(ref_a)]["files"] if ref_a and self._resolve_ref(ref_a) else {}
        b_files = self._commits[self._resolve_ref(ref_b)]["files"] if ref_b and self._resolve_ref(ref_b) else self._working_dir
        paths = set(a_files) | set(b_files)
        diff = {p: {"before": a_files.get(p), "after": b_files.get(p)}
                for p in paths if a_files.get(p) != b_files.get(p)}
        return {"files": diff}

    def git_log(self, max_count: int | None = None, since: str | None = None) -> dict:
        chain = self._ancestors(self._head_commit_id())
        if since:
            since_id = self._resolve_ref(since)
            if since_id in chain:
                chain = chain[:chain.index(since_id)]
        if max_count:
            chain = chain[:int(max_count)]
        return {"commits": [{"commit_id": c, "message": self._commits[c]["message"]} for c in chain]}

    def git_show(self, ref: str) -> dict:
        commit_id = self._resolve_ref(ref)
        if commit_id is None:
            return {"error": f"no such ref {ref!r}"}
        commit = self._commits[commit_id]
        return {"commit_id": commit_id, "message": commit["message"], "files": commit["files"]}

    def git_branch(self) -> dict:
        return {"branches": [
            {"name": name, "current": name == self._current_branch, "commit_id": cid}
            for name, cid in sorted(self._branches.items())
        ]}

    def git_create_branch(self, name: str, start_point: str | None = None) -> dict:
        if name in self._branches:
            return {"error": f"branch {name!r} already exists"}
        base = self._resolve_ref(start_point) if start_point else self._head_commit_id()
        self._branches[name] = base
        return {"name": name, "commit_id": base}

    def git_checkout(self, ref: str) -> dict:
        if ref not in self._branches:
            return {"error": f"no such branch {ref!r}"}
        self._current_branch = ref
        return {"current_branch": ref, "commit_id": self._branches[ref]}

    def git_blame(self, path: str) -> dict:
        content = self._working_dir.get(path)
        if content is None:
            return {"error": f"no such path {path!r} in working directory"}
        lines = content.split("\n")
        if lines and lines[-1] == "" and content.endswith("\n"):
            lines = lines[:-1]   # a trailing newline is not a blamable line
        # Oldest first: a line's owner is the commit that (re)introduced it -
        # i.e. the first commit, walking forward, where it appears but did
        # NOT already appear in that same path's immediately-prior snapshot.
        # An unchanged line stays attributed to wherever it first showed up,
        # even if later commits' full snapshots still "contain" it too.
        history = list(reversed(self._ancestors(self._head_commit_id())))
        out = []
        for line in lines:
            attributed = "working directory (uncommitted)"
            prev_lines: list[str] | None = None
            for commit_id in history:
                file_content = self._commits[commit_id]["files"].get(path)
                file_lines = file_content.split("\n") if file_content is not None else []
                if line in file_lines and (prev_lines is None or line not in prev_lines):
                    attributed = commit_id
                prev_lines = file_lines
            out.append({"content": line, "commit_id": attributed})
        return {"path": path, "lines": out}

    def git_remotes(self) -> dict:
        return {"remotes": [{"name": n, "url": u} for n, u in sorted(self._remotes.items())]}

    def git_tags(self) -> dict:
        return {"tags": [{"name": n, "commit_id": c} for n, c in sorted(self._tags.items())]}

    def git_tag(self, name: str, ref: str | None = None) -> dict:
        target = self._resolve_ref(ref) if ref else self._head_commit_id()
        if target is None:
            return {"error": "cannot tag - no commits yet"}
        self._tags[name] = target
        return {"name": name, "commit_id": target}

    def git_push(self, remote: str = "origin", branch: str | None = None) -> dict:
        branch = branch or self._current_branch
        local_commit = self._branches.get(branch)
        if local_commit is None:
            return {"error": f"nothing to push - branch {branch!r} has no commits"}
        if remote not in self._remotes:
            return {"error": f"no such remote {remote!r}"}
        self._remote_branches.setdefault(remote, {})[branch] = local_commit
        return {"remote": remote, "branch": branch, "pushed_commit_id": local_commit}

    def git_pull(self, remote: str = "origin", branch: str | None = None) -> dict:
        branch = branch or self._current_branch
        if remote not in self._remotes:
            return {"error": f"no such remote {remote!r}"}
        remote_commit = self._remote_branches.get(remote, {}).get(branch)
        if remote_commit is None:
            return {"remote": remote, "branch": branch, "fast_forwarded": False, "reason": "remote has no such branch"}
        local_commit = self._branches.get(branch)
        if remote_commit in self._ancestors(local_commit):
            return {"remote": remote, "branch": branch, "fast_forwarded": False, "reason": "already up to date"}
        # Simplified fast-forward-only mock: no divergent-history merge modeling.
        old_head = self._head_files(branch)
        self._branches[branch] = remote_commit
        # A real fast-forward pull also updates the working tree - but only
        # for paths the user hasn't locally modified; a genuinely dirty file
        # is left alone (a mock stand-in for real git refusing to clobber
        # uncommitted local changes without a merge/stash).
        new_head = self._head_files(branch)
        for path, new_content in new_head.items():
            if self._working_dir.get(path) == old_head.get(path):
                self._working_dir[path] = new_content
        return {"remote": remote, "branch": branch, "fast_forwarded": True, "commit_id": remote_commit}

    def summary(self) -> dict:
        head = self._head_files()
        head_id = self._head_commit_id()
        return {
            "n_calls": len(self.call_log),
            "current_branch": self._current_branch,
            "branch_count": len(self._branches),
            "commit_count": len(self._commits),
            "commits_on_current_branch": len(self._ancestors(head_id)),
            # Reachable from "main" specifically, regardless of what's
            # currently checked out - lets a case verify a change landed on
            # a feature branch and NOT main, without needing to know (or
            # constrain) whatever name the agent picked for that branch.
            "main_commit_count": len(self._ancestors(self._branches.get("main"))),
            "staged_count": len(self._staged),
            "unstaged_modified_count": len(self.git_status()["unstaged_modified"]),
            "untracked_count": len(self.git_status()["untracked"]),
            "tag_count": len(self._tags),
            "last_commit_message": self._commits[head_id]["message"] if head_id else None,
            "last_commit_files": sorted(self._commits[head_id]["files"]) if head_id else [],
            "pushed_matches_local": self._remote_branches.get("origin", {}).get(self._current_branch) == head_id,
        }


class FilesystemService(MockService):
    """A mock virtual filesystem - all 13 tools the official MCP filesystem
    server exposes, catalogued in DEV_NOTES/TOOL_CATALOG_COMPLETE.md §2.

    Inverse of GitRepoService's scope decision: git tools never author
    content, so its working tree is fixed at seed() time. Filesystem tools
    ARE content authorship - write_file/edit_file/move_file/create_directory
    actively mutate state during the conversation, which is the whole point
    of this domain (does the agent read before overwriting, edit
    surgically instead of blind-rewriting, check existence before
    clobbering). ``seed()`` only establishes what exists BEFORE the
    conversation starts, same role as it plays for git_repo.
    """

    TOOLS = {name: name for name in (
        "read_text_file", "read_media_file", "read_multiple_files",
        "write_file", "edit_file", "create_directory",
        "list_directory", "list_directory_with_sizes",
        "move_file", "search_files", "directory_tree",
        "get_file_info", "list_allowed_directories",
    )}

    def __init__(self) -> None:
        super().__init__()
        self._files: dict[str, str] = {}
        self._media: dict[str, dict] = {}          # path -> {"mime_type": str} (content itself not modeled)
        self._directories: set[str] = set()         # explicit + auto-registered parents, no trailing slash
        self._allowed_directories = ["/workspace"]

    # ── seeding ──────────────────────────────────────────────────────────

    def seed(self, spec: dict) -> None:
        """``spec`` keys, all optional:
        - ``files`` ({path: content}): pre-existing text files.
        - ``media_files`` ({path: mime_type}): pre-existing "binary" files -
          content itself isn't modeled, only that the path exists and what
          kind of file it is, enough to test ``read_media_file`` calls.
        - ``directories`` ([path, ...]): pre-existing EMPTY directories
          (a file's own parent directories are always implied automatically
          - this is only needed for a directory with nothing in it yet).
        """
        for path, content in (spec.get("files") or {}).items():
            self._touch_parents(path)
            self._files[path] = content
        for path, mime in (spec.get("media_files") or {}).items():
            self._touch_parents(path)
            self._media[path] = {"mime_type": mime}
        for d in spec.get("directories") or []:
            self._directories.add(d.strip("/"))

    # ── path normalization ──────────────────────────────────────────────

    _PATH_KEYS = ("path", "source", "destination")

    def dispatch(self, tool_name: str, arguments: dict) -> Any:
        """A trailing slash on a directory path ("src" vs "src/") is a
        stylistic choice no real filesystem tool treats as a different
        path - live-verified: a real model reasonably wrote "src/" where a
        case asserted "src" and failed for a reason that had nothing to do
        with whether the agent did the right thing. Normalized here, once,
        before the base class logs/dispatches, rather than inside every
        individual tool method (which wouldn't fix what the oracle sees -
        the call log records what's passed to dispatch, not what a method
        does internally with it)."""
        normalized = {
            k: (v.rstrip("/") if isinstance(v, str) and k in self._PATH_KEYS else v)
            for k, v in (arguments or {}).items()
        }
        return super().dispatch(tool_name, normalized)

    # ── internal helpers ────────────────────────────────────────────────

    def _touch_parents(self, path: str) -> None:
        """Writing a nested path implicitly creates its ancestor
        directories - matches how every real write_file-style tool
        behaves, so a case doesn't need a separate create_directory seed
        entry just to make a nested write_file's target reachable."""
        parts = path.strip("/").split("/")[:-1]
        prefix = ""
        for part in parts:
            prefix = f"{prefix}/{part}" if prefix else part
            self._directories.add(prefix)

    def _dir_exists(self, path: str) -> bool:
        path = path.strip("/")
        if path == "":
            return True
        if path in self._directories:
            return True
        prefix = f"{path}/"
        return any(p.startswith(prefix) for p in list(self._files) + list(self._media))

    def _children(self, dir_path: str) -> list[tuple[str, str]]:
        """Immediate children of ``dir_path`` as ``(name, "file"|"directory")``,
        computed on demand from the flat path sets rather than maintained
        as a live tree - simple and correct at mock scale, no separate
        structure to keep in sync on every write/move."""
        dir_path = dir_path.strip("/")
        prefix = f"{dir_path}/" if dir_path else ""
        out: dict[str, str] = {}
        for p in list(self._files) + list(self._media):
            if not p.startswith(prefix):
                continue
            rest = p[len(prefix):]
            if not rest:
                continue
            if "/" in rest:
                out[rest.split("/", 1)[0]] = "directory"
            else:
                out[rest] = "file"
        for d in self._directories:
            if d == dir_path or not d.startswith(prefix):
                continue
            top = d[len(prefix):].split("/", 1)[0]
            if top and top not in out:
                out[top] = "directory"
        return sorted(out.items())

    # ── tools ────────────────────────────────────────────────────────────

    def read_text_file(self, path: str, head: int | None = None, tail: int | None = None) -> dict:
        if path not in self._files:
            return {"error": f"no such file {path!r}"}
        lines = self._files[path].split("\n")
        if head:
            lines = lines[:int(head)]
        elif tail:
            lines = lines[-int(tail):]
        return {"path": path, "content": "\n".join(lines)}

    def read_media_file(self, path: str) -> dict:
        if path not in self._media:
            return {"error": f"no such media file {path!r}"}
        return {"path": path, "mime_type": self._media[path]["mime_type"], "data": "<mock: base64 content not modeled>"}

    def read_multiple_files(self, paths) -> dict:
        out = {}
        for p in self._as_list(paths):
            out[p] = self._files[p] if p in self._files else {"error": f"no such file {p!r}"}
        return {"files": out}

    def write_file(self, path: str, content: str) -> dict:
        self._touch_parents(path)
        self._files[path] = content
        return {"path": path, "bytes_written": len(content)}

    def edit_file(self, path: str, edits, dry_run: bool = False) -> dict:
        if path not in self._files:
            return {"error": f"no such file {path!r}"}
        content = self._files[path]
        applied = []
        for edit in edits or []:
            old_text, new_text = edit.get("old_text", ""), edit.get("new_text", "")
            if old_text not in content:
                return {"error": f"old_text not found in {path!r}: {old_text!r}", "applied": applied}
            content = content.replace(old_text, new_text, 1)
            applied.append({"old_text": old_text, "new_text": new_text})
        if dry_run:
            return {"path": path, "dry_run": True, "preview": content}
        self._files[path] = content
        return {"path": path, "edits_applied": len(applied)}

    def create_directory(self, path: str) -> dict:
        self._directories.add(path.strip("/"))
        return {"path": path, "created": True}

    def list_directory(self, path: str = "") -> dict:
        if path and not self._dir_exists(path):
            return {"error": f"no such directory {path!r}"}
        entries = [{"name": n, "type": t} for n, t in self._children(path)]
        return {"path": path, "entries": entries}

    def list_directory_with_sizes(self, path: str = "", sort_by: str | None = None) -> dict:
        if path and not self._dir_exists(path):
            return {"error": f"no such directory {path!r}"}
        prefix = f"{path.strip('/')}/" if path.strip("/") else ""
        entries = []
        for name, typ in self._children(path):
            full = f"{prefix}{name}"
            size = len(self._files.get(full, "")) if typ == "file" else 0
            entries.append({"name": name, "type": typ, "size": size})
        entries.sort(key=(lambda e: -e["size"]) if sort_by == "size" else (lambda e: e["name"]))
        return {"path": path, "entries": entries}

    def move_file(self, source: str, destination: str) -> dict:
        if source not in self._files and source not in self._media:
            return {"error": f"no such file {source!r}"}
        if destination in self._files or destination in self._media:
            return {"error": f"destination {destination!r} already exists"}
        self._touch_parents(destination)
        if source in self._files:
            self._files[destination] = self._files.pop(source)
        else:
            self._media[destination] = self._media.pop(source)
        return {"source": source, "destination": destination}

    def search_files(self, path: str = "", pattern: str = "", exclude_patterns=None) -> dict:
        prefix = f"{path.strip('/')}/" if path.strip("/") else ""
        exclude_patterns = exclude_patterns or []
        matches = []
        for p in list(self._files) + list(self._media):
            if not p.startswith(prefix):
                continue
            name = p.rsplit("/", 1)[-1]
            if pattern and pattern.lower() not in name.lower():
                continue
            if any(ex.lower() in p.lower() for ex in exclude_patterns):
                continue
            matches.append(p)
        return {"matches": sorted(matches)}

    def directory_tree(self, path: str = "", exclude_patterns=None) -> dict:
        exclude_patterns = exclude_patterns or []

        def build(dir_path: str) -> dict:
            node = {"name": dir_path.rsplit("/", 1)[-1] if dir_path else "/", "type": "directory", "children": []}
            for name, typ in self._children(dir_path):
                full = f"{dir_path}/{name}" if dir_path else name
                if any(ex.lower() in full.lower() for ex in exclude_patterns):
                    continue
                node["children"].append(build(full) if typ == "directory" else {"name": name, "type": "file"})
            return node

        return build(path.strip("/"))

    def get_file_info(self, path: str) -> dict:
        if path in self._files:
            return {"path": path, "type": "file", "size": len(self._files[path])}
        if path in self._media:
            return {"path": path, "type": "file", "size": 0, "mime_type": self._media[path]["mime_type"]}
        if self._dir_exists(path):
            return {"path": path, "type": "directory"}
        return {"error": f"no such path {path!r}"}

    def list_allowed_directories(self) -> dict:
        return {"directories": list(self._allowed_directories)}

    def summary(self) -> dict:
        return {
            "n_calls": len(self.call_log),
            "file_count": len(self._files) + len(self._media),
            "directory_count": len(self._directories),
            "files": dict(sorted(self._files.items())),
        }


class DockerService(MockService):
    """A mock Docker daemon - 25 tools spanning containers, images,
    networks, volumes, and system info, catalogued in
    DEV_NOTES/TOOL_CATALOG_COMPLETE.md §4 ("comprehensive" tier).

    Unlike git_repo/filesystem, several of these tools enforce real
    Docker-like PRECONDITIONS rather than always succeeding - removing a
    running container, or an image a container still uses, are refused
    (matching real `docker rm`/`docker rmi` behaviour) - because "does the
    agent respect these preconditions instead of forcing past them" is
    exactly the workflow-discipline skill this domain exists to test, the
    same role git_repo's "no commit without staging" plays there.
    """

    TOOLS = {name: name for name in (
        "list_containers", "create_container", "start_container", "stop_container",
        "restart_container", "pause_container", "remove_container",
        "inspect_container", "get_container_logs", "get_container_stats",
        "list_images", "pull_image", "build_image", "tag_image",
        "remove_image", "prune_images",
        "list_networks", "create_network", "connect_network", "disconnect_network",
        "list_volumes", "create_volume", "remove_volume", "prune_volumes",
        "docker_info",
    )}

    def __init__(self) -> None:
        super().__init__()
        self._containers: dict[str, dict] = {}   # name -> {image, status, ports, env, logs, networks}
        self._images: dict[str, dict] = {}        # tag -> {source: "pulled"|"built"}
        self._networks: dict[str, dict] = {"bridge": {"driver": "bridge", "containers": set()}}
        self._volumes: dict[str, dict] = {}        # name -> {containers: set}

    # ── seeding ──────────────────────────────────────────────────────────

    def seed(self, spec: dict) -> None:
        """``spec`` keys, all optional:
        - ``images`` ([tag, ...]): pre-existing images, as if already pulled.
        - ``volumes`` ([name, ...]): pre-existing volumes.
        - ``containers`` ({name: {image, status, logs, volumes}}):
          pre-existing containers - ``status`` one of "running"/"stopped"
          (default "running"); ``logs`` a list of pre-existing log lines;
          ``volumes`` a list of volume names to mount (must already be
          listed in this spec's own ``volumes`` key - processed first).
        - ``networks`` ([name, ...]): pre-existing custom networks.
        """
        for tag in spec.get("images") or []:
            self._images[tag] = {"source": "pulled"}
        for name in spec.get("volumes") or []:
            self._volumes.setdefault(name, {"containers": set()})
        for name, cfg in (spec.get("containers") or {}).items():
            self._images.setdefault(cfg.get("image", "unknown"), {"source": "pulled"})
            vols = list(cfg.get("volumes", []))
            self._containers[name] = {
                "image": cfg.get("image"), "status": cfg.get("status", "running"),
                "ports": cfg.get("ports", {}), "env": cfg.get("env", {}),
                "logs": list(cfg.get("logs", [])), "networks": {"bridge"}, "volumes": vols,
            }
            self._networks["bridge"]["containers"].add(name)
            for vol in vols:
                self._volumes.setdefault(vol, {"containers": set()})
                self._volumes[vol]["containers"].add(name)
        for name in spec.get("networks") or []:
            self._networks.setdefault(name, {"driver": "bridge", "containers": set()})

    # ── containers ───────────────────────────────────────────────────────

    def list_containers(self, all: bool = True) -> dict:
        items = self._containers.items() if all else (
            (n, c) for n, c in self._containers.items() if c["status"] == "running")
        return {"containers": [{"name": n, "image": c["image"], "status": c["status"]} for n, c in sorted(items)]}

    def create_container(self, name: str, image: str, ports: dict | None = None,
                          env: dict | None = None, volumes: list | None = None) -> dict:
        if name in self._containers:
            return {"error": f"container {name!r} already exists"}
        if image not in self._images:
            return {"error": f"no such image {image!r} - pull or build it first"}
        volumes = self._as_list(volumes)
        missing = [v for v in volumes if v not in self._volumes]
        if missing:
            return {"error": f"no such volume(s) {missing} - create them first"}
        self._containers[name] = {"image": image, "status": "running", "ports": ports or {},
                                  "env": env or {}, "logs": [], "networks": {"bridge"}, "volumes": volumes}
        self._networks["bridge"]["containers"].add(name)
        for vol in volumes:
            self._volumes[vol]["containers"].add(name)
        return {"name": name, "image": image, "status": "running", "volumes": volumes}

    def _require_container(self, name: str) -> dict | None:
        if name not in self._containers:
            return {"error": f"no such container {name!r}"}
        return None

    def start_container(self, name: str) -> dict:
        err = self._require_container(name)
        if err:
            return err
        self._containers[name]["status"] = "running"
        return {"name": name, "status": "running"}

    def stop_container(self, name: str) -> dict:
        err = self._require_container(name)
        if err:
            return err
        self._containers[name]["status"] = "stopped"
        return {"name": name, "status": "stopped"}

    def restart_container(self, name: str) -> dict:
        err = self._require_container(name)
        if err:
            return err
        self._containers[name]["status"] = "running"
        return {"name": name, "status": "running", "restarted": True}

    def pause_container(self, name: str) -> dict:
        err = self._require_container(name)
        if err:
            return err
        if self._containers[name]["status"] != "running":
            return {"error": f"container {name!r} is not running"}
        self._containers[name]["status"] = "paused"
        return {"name": name, "status": "paused"}

    def remove_container(self, name: str, force: bool = False) -> dict:
        err = self._require_container(name)
        if err:
            return err
        if self._containers[name]["status"] == "running" and not force:
            return {"error": f"container {name!r} is running - stop it first, or pass force=true"}
        for net in self._containers[name]["networks"]:
            self._networks[net]["containers"].discard(name)
        for vol in self._containers[name].get("volumes", []):
            if vol in self._volumes:
                self._volumes[vol]["containers"].discard(name)
        del self._containers[name]
        return {"name": name, "removed": True}

    def inspect_container(self, name: str) -> dict:
        err = self._require_container(name)
        if err:
            return err
        return {"name": name, **self._containers[name], "networks": sorted(self._containers[name]["networks"])}

    def get_container_logs(self, name: str, tail: int | None = None) -> dict:
        err = self._require_container(name)
        if err:
            return err
        logs = self._containers[name]["logs"]
        if tail:
            logs = logs[-int(tail):]
        return {"name": name, "logs": logs}

    def get_container_stats(self, name: str) -> dict:
        err = self._require_container(name)
        if err:
            return err
        # Fixed, deterministic mock figures - real stats aren't modeled,
        # only that the tool was called and returns something plausible.
        return {"name": name, "cpu_percent": 5.0, "memory_mb": 128}

    # ── images ───────────────────────────────────────────────────────────

    def list_images(self) -> dict:
        return {"images": [{"tag": t, "source": v["source"]} for t, v in sorted(self._images.items())]}

    def pull_image(self, tag: str) -> dict:
        self._images[tag] = {"source": "pulled"}
        return {"tag": tag, "source": "pulled"}

    def build_image(self, tag: str, dockerfile_path: str | None = None) -> dict:
        self._images[tag] = {"source": "built"}
        return {"tag": tag, "source": "built"}

    def tag_image(self, source: str, target: str) -> dict:
        if source not in self._images:
            return {"error": f"no such image {source!r}"}
        self._images[target] = dict(self._images[source])
        return {"source": source, "target": target}

    def remove_image(self, tag: str) -> dict:
        if tag not in self._images:
            return {"error": f"no such image {tag!r}"}
        in_use = [n for n, c in self._containers.items() if c["image"] == tag]
        if in_use:
            return {"error": f"image {tag!r} is in use by container(s) {in_use} - remove them first"}
        del self._images[tag]
        return {"tag": tag, "removed": True}

    def prune_images(self) -> dict:
        used = {c["image"] for c in self._containers.values()}
        unused = [t for t in self._images if t not in used]
        for t in unused:
            del self._images[t]
        return {"removed": sorted(unused)}

    # ── networks ─────────────────────────────────────────────────────────

    def list_networks(self) -> dict:
        return {"networks": [{"name": n, "driver": v["driver"]} for n, v in sorted(self._networks.items())]}

    def create_network(self, name: str, driver: str = "bridge") -> dict:
        if name in self._networks:
            return {"error": f"network {name!r} already exists"}
        self._networks[name] = {"driver": driver, "containers": set()}
        return {"name": name, "driver": driver}

    def connect_network(self, network: str, container: str) -> dict:
        if network not in self._networks:
            return {"error": f"no such network {network!r}"}
        err = self._require_container(container)
        if err:
            return err
        self._networks[network]["containers"].add(container)
        self._containers[container]["networks"].add(network)
        return {"network": network, "container": container, "connected": True}

    def disconnect_network(self, network: str, container: str) -> dict:
        if network not in self._networks:
            return {"error": f"no such network {network!r}"}
        self._networks[network]["containers"].discard(container)
        if container in self._containers:
            self._containers[container]["networks"].discard(network)
        return {"network": network, "container": container, "disconnected": True}

    # ── volumes ──────────────────────────────────────────────────────────

    def list_volumes(self) -> dict:
        return {"volumes": sorted(self._volumes)}

    def create_volume(self, name: str) -> dict:
        if name in self._volumes:
            return {"error": f"volume {name!r} already exists"}
        self._volumes[name] = {"containers": set()}
        return {"name": name}

    def remove_volume(self, name: str) -> dict:
        if name not in self._volumes:
            return {"error": f"no such volume {name!r}"}
        if self._volumes[name]["containers"]:
            return {"error": f"volume {name!r} is in use - remove the container(s) using it first"}
        del self._volumes[name]
        return {"name": name, "removed": True}

    def prune_volumes(self) -> dict:
        unused = [n for n, v in self._volumes.items() if not v["containers"]]
        for n in unused:
            del self._volumes[n]
        return {"removed": sorted(unused)}

    # ── system ───────────────────────────────────────────────────────────

    def docker_info(self) -> dict:
        return {
            "containers": len(self._containers),
            "containers_running": sum(1 for c in self._containers.values() if c["status"] == "running"),
            "images": len(self._images),
            "networks": len(self._networks),
            "volumes": len(self._volumes),
        }

    def summary(self) -> dict:
        return {
            "n_calls": len(self.call_log),
            "container_count": len(self._containers),
            "running_container_count": sum(1 for c in self._containers.values() if c["status"] == "running"),
            "stopped_container_count": sum(1 for c in self._containers.values() if c["status"] == "stopped"),
            "image_count": len(self._images),
            "network_count": len(self._networks),
            "volume_count": len(self._volumes),
            "containers": {n: c["status"] for n, c in sorted(self._containers.items())},
        }


class KubernetesService(MockService):
    """A mock Kubernetes cluster plus Helm - 23 of the 24 tools the
    reference `Flux159/mcp-server-kubernetes` implementation exposes,
    catalogued (at an "~16+" estimate later confirmed low by reading the
    actual source tree this session) in DEV_NOTES/TOOL_CATALOG_COMPLETE.md
    §5. The one omission is deliberate: `kubectl_generic` takes an
    arbitrary kubectl command string with no fixed shape - mocking it
    honestly would mean either parsing arbitrary CLI syntax (turning this
    into a shell simulator) or silently no-op'ing it, neither of which
    tests anything - the same reasoning that keeps a raw-shell-exec tool
    out of filesystem/docker.

    Shares git_repo/docker's core skill under test - workflow discipline
    over a stateful system with real preconditions - but adds two
    dimensions neither prior service had: (1) a `namespace` scopes nearly
    every call, so "did the agent operate on the right namespace" is
    itself gradable; and (2) `kubectl_apply`/`helm_template_apply` UPSERT
    while `kubectl_create`/`install_helm_chart` REFUSE a duplicate - a
    real, sharp distinction (matching real kubectl/helm) an agent can get
    wrong by reaching for the non-idempotent tool a second time, or by
    never reaching for the namespace-scoped `kubectl_create(kind="namespace")`
    at all before deploying into a namespace that doesn't exist yet.

    Scope decision: manifests/patches are modeled as explicit keyword
    fields (kind, name, namespace, replicas, image, labels) rather than a
    raw YAML/JSON manifest blob - the same "explicit named params over an
    opaque blob" choice DockerService made for `create_container`, and for
    the same reason: an opaque blob isn't something `expected_calls` can
    usefully assert against.

    `kubectl_delete` on a namespace refuses while it still contains
    resources or Helm releases - the same in-use precondition docker's
    `remove_image`/`remove_volume` enforce - rather than a silent real-k8s-
    style cascade, so "clean up what's inside first" has a real consequence
    to test instead of being trivially bypassable.
    """

    TOOLS = {name: name for name in (
        "kubectl_get", "kubectl_describe", "kubectl_create", "kubectl_apply",
        "kubectl_delete", "kubectl_logs", "kubectl_context", "kubectl_scale",
        "kubectl_patch", "kubectl_rollout",
        "explain_resource", "list_api_resources",
        "port_forward", "stop_port_forward", "exec_in_pod",
        "install_helm_chart", "upgrade_helm_chart", "uninstall_helm_chart",
        "helm_template_apply", "helm_template_uninstall",
        "cleanup_pods", "node_management", "ping",
    )}

    _EXPLAIN_DOCS = {
        "pod": "Pod: the smallest deployable unit - one or more containers sharing storage/network.",
        "deployment": "Deployment: manages a replicated, self-healing set of Pods via a ReplicaSet.",
        "statefulset": "StatefulSet: manages Pods with stable identities and persistent storage.",
        "daemonset": "DaemonSet: ensures one Pod copy runs on every (or a subset of) node.",
        "service": "Service: a stable network endpoint load-balancing across a set of Pods.",
        "configmap": "ConfigMap: non-secret key/value configuration data for Pods to consume.",
        "secret": "Secret: sensitive key/value data (credentials, tokens, keys) for Pods to consume.",
        "job": "Job: runs Pods to completion for a finite task.",
        "cronjob": "CronJob: runs a Job on a repeating schedule.",
        "ingress": "Ingress: HTTP(S) routing rules exposing Services outside the cluster.",
        "namespace": "Namespace: a virtual cluster partitioning names and resource scope.",
    }

    _SCALABLE_KINDS = {"deployment", "statefulset", "replicaset"}
    _TERMINAL_POD_STATUSES = {"Error", "CrashLoopBackOff", "Completed", "Evicted"}

    def __init__(self) -> None:
        super().__init__()
        self._namespaces: set[str] = {"default"}
        self._contexts: set[str] = {"default"}
        self._current_context = "default"
        self._nodes: dict[str, dict] = {"node-1": {"schedulable": True}}
        self._resources: dict[tuple[str, str, str], dict] = {}   # (namespace, kind, name) -> resource
        self._history: dict[tuple[str, str, str], list[dict]] = {}  # same key -> [{revision, image}, ...]
        self._helm_releases: dict[tuple[str, str], dict] = {}     # (namespace, name) -> release
        self._port_forwards: dict[str, dict] = {}
        self._next_forward_id = 1

    # ── seeding ──────────────────────────────────────────────────────────

    def seed(self, spec: dict) -> None:
        """``spec`` keys, all optional:
        - ``namespaces`` ([name, ...]): extra namespaces beyond "default".
        - ``nodes`` ({name: {"schedulable": bool}}): extra/overridden nodes
          beyond the always-present "node-1".
        - ``resources`` ([{kind, name, namespace, replicas, image, status,
          labels, node, logs, history}, ...]): pre-existing resources, as
          if already applied - ``namespace`` defaults to "default",
          ``status`` defaults to "Running" for pods. ``history`` ([{revision,
          image}, ...]) pre-populates multi-revision rollout history
          directly, for cases that need a real prior revision to roll back
          to (``kubectl_rollout`` undo/history) - without it, a single
          revision is synthesized from ``image`` alone, same as
          git_repo's ``committed`` vs ``initial_commits`` distinction.
        - ``helm_releases`` ([{name, namespace, chart, revision, values},
          ...]): pre-existing releases, as if already installed.
        - ``contexts`` ([name, ...]): extra kube contexts beyond "default"
          (which is always the initially-current one).
        """
        for ns in spec.get("namespaces") or []:
            self._namespaces.add(ns)
        for name, cfg in (spec.get("nodes") or {}).items():
            self._nodes[name] = {"schedulable": cfg.get("schedulable", True)}
        for ctx in spec.get("contexts") or []:
            self._contexts.add(ctx)
        for entry in spec.get("resources") or []:
            kind, name = entry["kind"], entry["name"]
            namespace = entry.get("namespace", "default")
            self._namespaces.add(namespace)
            key = (namespace, kind, name)
            self._resources[key] = {
                "kind": kind, "name": name, "namespace": namespace,
                "replicas": entry.get("replicas"), "image": entry.get("image"),
                "status": entry.get("status", "Running" if kind == "pod" else None),
                "labels": entry.get("labels", {}), "node": entry.get("node"),
                "logs": list(entry.get("logs", [])),
            }
            history = entry.get("history")
            if history:
                self._history[key] = [dict(h) for h in history]
            elif entry.get("image"):
                self._history[key] = [{"revision": 1, "image": entry["image"]}]
        for entry in spec.get("helm_releases") or []:
            namespace = entry.get("namespace", "default")
            self._namespaces.add(namespace)
            self._helm_releases[(namespace, entry["name"])] = {
                "chart": entry["chart"], "namespace": namespace,
                "revision": entry.get("revision", 1), "values": entry.get("values", {}),
            }

    # ── internal helpers ────────────────────────────────────────────────

    def _require_namespace(self, namespace: str) -> dict | None:
        if namespace not in self._namespaces:
            return {"error": f"no such namespace {namespace!r}"}
        return None

    def _require_resource(self, namespace: str, kind: str, name: str) -> dict | None:
        if (namespace, kind, name) not in self._resources:
            return {"error": f"no such {kind} {name!r} in namespace {namespace!r}"}
        return None

    def _bump_history(self, key: tuple[str, str, str], image: str | None) -> None:
        if image is None:
            return
        history = self._history.setdefault(key, [])
        if not history or history[-1]["image"] != image:
            history.append({"revision": len(history) + 1, "image": image})

    # ── core kubectl ─────────────────────────────────────────────────────

    @staticmethod
    def _without_logs(resource: dict) -> dict:
        """Neither ``kubectl get`` nor ``kubectl describe`` ever surface a
        container's actual log output in real Kubernetes - logs are streamed
        live from the kubelet, not part of the resource object at all - only
        ``kubectl_logs`` does. Live-verified this matters: with logs left in
        (an earlier version of this method), a model investigating a failing
        pod called only ``kubectl_describe`` and correctly found everything
        it needed (including the log line proving the OOM), never calling
        ``kubectl_logs`` at all - not a discipline failure, just this mock
        handing over information no real ``describe`` call would give it."""
        return {k: v for k, v in resource.items() if k != "logs"}

    def kubectl_get(self, kind: str, name: str | None = None, namespace: str = "default",
                     all_namespaces: bool = False, selector: dict | None = None) -> dict:
        if kind == "namespace":
            if name is not None:
                if name not in self._namespaces:
                    return {"error": f"no such namespace {name!r}"}
                return {"kind": "namespace", "name": name}
            return {"items": [{"kind": "namespace", "name": n} for n in sorted(self._namespaces)]}
        if not all_namespaces:
            err = self._require_namespace(namespace)
            if err:
                return err
        if name is not None:
            key = (namespace, kind, name)
            if key not in self._resources:
                return {"error": f"no such {kind} {name!r} in namespace {namespace!r}"}
            return self._without_logs(self._resources[key])
        items = [
            self._without_logs(r) for (ns, k, _), r in self._resources.items()
            if k == kind and (all_namespaces or ns == namespace)
            and (not selector or all(r.get("labels", {}).get(sk) == sv for sk, sv in selector.items()))
        ]
        return {"items": items}

    def kubectl_describe(self, kind: str, name: str, namespace: str = "default") -> dict:
        if kind == "namespace":
            if name not in self._namespaces:
                return {"error": f"no such namespace {name!r}"}
            resource_count = sum(1 for (ns, _, _) in self._resources if ns == name)
            return {"kind": "namespace", "name": name, "resource_count": resource_count}
        err = self._require_resource(namespace, kind, name)
        if err:
            return err
        return self._without_logs(self._resources[(namespace, kind, name)])

    def kubectl_create(self, kind: str, name: str, namespace: str = "default",
                        replicas: int | None = None, image: str | None = None,
                        labels: dict | None = None) -> dict:
        if kind == "namespace":
            if name in self._namespaces:
                return {"error": f"namespace {name!r} already exists"}
            self._namespaces.add(name)
            return {"kind": "namespace", "name": name}
        err = self._require_namespace(namespace)
        if err:
            return err
        key = (namespace, kind, name)
        if key in self._resources:
            return {"error": f"{kind} {name!r} already exists in namespace {namespace!r} - use kubectl_apply to update it"}
        self._resources[key] = {
            "kind": kind, "name": name, "namespace": namespace, "replicas": replicas,
            "image": image, "status": "Running" if kind == "pod" else None,
            "labels": labels or {}, "node": None, "logs": [],
        }
        self._bump_history(key, image)
        return dict(self._resources[key])

    def kubectl_apply(self, kind: str, name: str, namespace: str = "default",
                       replicas: int | None = None, image: str | None = None,
                       labels: dict | None = None) -> dict:
        if kind == "namespace":
            self._namespaces.add(name)
            return {"kind": "namespace", "name": name}
        err = self._require_namespace(namespace)
        if err:
            return err
        key = (namespace, kind, name)
        existing = self._resources.get(key)
        if existing is None:
            self._resources[key] = {
                "kind": kind, "name": name, "namespace": namespace, "replicas": replicas,
                "image": image, "status": "Running" if kind == "pod" else None,
                "labels": labels or {}, "node": None, "logs": [],
            }
        else:
            if replicas is not None:
                existing["replicas"] = replicas
            if image is not None:
                existing["image"] = image
            if labels is not None:
                existing["labels"] = labels
        self._bump_history(key, image)
        return dict(self._resources[key])

    def kubectl_delete(self, kind: str, name: str, namespace: str = "default") -> dict:
        if kind == "namespace":
            if name not in self._namespaces:
                return {"error": f"no such namespace {name!r}"}
            if name == "default":
                return {"error": "cannot delete the default namespace"}
            in_use = [f"{k}/{n}" for (ns, k, n) in self._resources if ns == name] + \
                     [f"helm/{n}" for (ns, n) in self._helm_releases if ns == name]
            if in_use:
                return {"error": f"namespace {name!r} still contains {in_use} - remove them first"}
            self._namespaces.discard(name)
            return {"kind": "namespace", "name": name, "deleted": True}
        err = self._require_resource(namespace, kind, name)
        if err:
            return err
        del self._resources[(namespace, kind, name)]
        return {"kind": kind, "name": name, "namespace": namespace, "deleted": True}

    def kubectl_logs(self, name: str, namespace: str = "default", container: str | None = None,
                      tail: int | None = None) -> dict:
        err = self._require_resource(namespace, "pod", name)
        if err:
            return err
        logs = self._resources[(namespace, "pod", name)]["logs"]
        if tail:
            logs = logs[-int(tail):]
        return {"name": name, "logs": logs}

    def kubectl_context(self, operation: str = "get", name: str | None = None) -> dict:
        if operation == "get":
            return {"current_context": self._current_context}
        if operation == "list":
            return {"contexts": sorted(self._contexts)}
        if operation == "use":
            if name not in self._contexts:
                return {"error": f"no such context {name!r}"}
            self._current_context = name
            return {"current_context": name}
        return {"error": f"unknown operation {operation!r} (expected get, list, or use)"}

    def kubectl_scale(self, name: str, replicas: int, kind: str = "deployment", namespace: str = "default") -> dict:
        if kind not in self._SCALABLE_KINDS:
            return {"error": f"{kind} is not scalable"}
        err = self._require_resource(namespace, kind, name)
        if err:
            return err
        self._resources[(namespace, kind, name)]["replicas"] = replicas
        return {"kind": kind, "name": name, "namespace": namespace, "replicas": replicas}

    def kubectl_patch(self, kind: str, name: str, patch: dict, namespace: str = "default") -> dict:
        err = self._require_resource(namespace, kind, name)
        if err:
            return err
        key = (namespace, kind, name)
        resource = self._resources[key]
        patch = patch or {}
        for field in ("replicas", "image", "labels", "status"):
            if field in patch:
                resource[field] = patch[field]
        if "image" in patch:
            self._bump_history(key, patch["image"])
        return dict(resource)

    def kubectl_rollout(self, subcommand: str, name: str, kind: str = "deployment", namespace: str = "default") -> dict:
        err = self._require_resource(namespace, kind, name)
        if err:
            return err
        key = (namespace, kind, name)
        history = self._history.get(key, [])
        if subcommand == "status":
            return {"kind": kind, "name": name,
                    "status": "complete", "revision": history[-1]["revision"] if history else 0}
        if subcommand == "history":
            return {"kind": kind, "name": name, "history": list(history)}
        if subcommand == "undo":
            if len(history) < 2:
                return {"error": f"no previous revision to undo to for {kind} {name!r}"}
            history.pop()
            self._resources[key]["image"] = history[-1]["image"]
            return {"kind": kind, "name": name, "reverted_to_revision": history[-1]["revision"],
                     "image": history[-1]["image"]}
        if subcommand == "restart":
            image = self._resources[key].get("image")
            history.append({"revision": len(history) + 1, "image": image})
            return {"kind": kind, "name": name, "restarted": True, "revision": history[-1]["revision"]}
        return {"error": f"unknown subcommand {subcommand!r} (expected status, history, undo, or restart)"}

    # ── resource info ────────────────────────────────────────────────────

    def explain_resource(self, resource: str) -> dict:
        doc = self._EXPLAIN_DOCS.get(resource.lower())
        if doc is None:
            return {"error": f"no documentation for resource {resource!r}"}
        return {"resource": resource, "description": doc}

    def list_api_resources(self) -> dict:
        return {"resources": sorted(self._EXPLAIN_DOCS)}

    # ── advanced ─────────────────────────────────────────────────────────

    def port_forward(self, name: str, local_port: int, remote_port: int, namespace: str = "default") -> dict:
        err = self._require_resource(namespace, "pod", name)
        if err:
            return err
        if self._resources[(namespace, "pod", name)]["status"] != "Running":
            return {"error": f"pod {name!r} is not Running"}
        forward_id = f"pf{self._next_forward_id}"
        self._next_forward_id += 1
        self._port_forwards[forward_id] = {
            "id": forward_id, "name": name, "namespace": namespace,
            "local_port": local_port, "remote_port": remote_port,
        }
        return dict(self._port_forwards[forward_id])

    def stop_port_forward(self, id: str) -> dict:
        if id not in self._port_forwards:
            return {"error": f"no such port-forward {id!r}"}
        del self._port_forwards[id]
        return {"id": id, "stopped": True}

    def exec_in_pod(self, name: str, command, namespace: str = "default", container: str | None = None) -> dict:
        err = self._require_resource(namespace, "pod", name)
        if err:
            return err
        if self._resources[(namespace, "pod", name)]["status"] != "Running":
            return {"error": f"pod {name!r} is not Running"}
        command = self._as_list(command)
        return {"name": name, "command": command, "output": f"<mock output of: {' '.join(command)}>"}

    # ── helm ─────────────────────────────────────────────────────────────

    def install_helm_chart(self, name: str, chart: str, namespace: str = "default",
                            values: dict | None = None, create_namespace: bool = True) -> dict:
        key = (namespace, name)
        if key in self._helm_releases:
            return {"error": f"release {name!r} already exists in namespace {namespace!r} - use upgrade_helm_chart"}
        if namespace not in self._namespaces:
            if not create_namespace:
                return {"error": f"no such namespace {namespace!r} (create_namespace is false)"}
            self._namespaces.add(namespace)
        self._helm_releases[key] = {"chart": chart, "namespace": namespace, "revision": 1, "values": values or {}}
        return dict(self._helm_releases[key])

    def upgrade_helm_chart(self, name: str, chart: str, namespace: str = "default",
                            values: dict | None = None) -> dict:
        key = (namespace, name)
        if key not in self._helm_releases:
            return {"error": f"no such release {name!r} in namespace {namespace!r} - install it first"}
        release = self._helm_releases[key]
        release["chart"] = chart
        release["revision"] += 1
        if values is not None:
            release["values"] = values
        return dict(release)

    def uninstall_helm_chart(self, name: str, namespace: str = "default") -> dict:
        key = (namespace, name)
        if key not in self._helm_releases:
            return {"error": f"no such release {name!r} in namespace {namespace!r}"}
        del self._helm_releases[key]
        return {"name": name, "namespace": namespace, "uninstalled": True}

    def helm_template_apply(self, name: str, chart: str, namespace: str = "default",
                             values: dict | None = None) -> dict:
        # Unlike install_helm_chart, template+apply is idempotent - it
        # upserts instead of refusing a duplicate, the same
        # kubectl_apply-vs-kubectl_create distinction at the Helm layer.
        key = (namespace, name)
        release = self._helm_releases.get(key)
        if release is None:
            self._namespaces.add(namespace)
            self._helm_releases[key] = {"chart": chart, "namespace": namespace, "revision": 1, "values": values or {}}
        else:
            release["chart"] = chart
            release["revision"] += 1
            if values is not None:
                release["values"] = values
        return dict(self._helm_releases[key])

    def helm_template_uninstall(self, name: str, namespace: str = "default") -> dict:
        return self.uninstall_helm_chart(name, namespace)

    # ── cleanup ──────────────────────────────────────────────────────────

    def cleanup_pods(self, namespace: str = "default", all_namespaces: bool = False) -> dict:
        removed = []
        for key, resource in list(self._resources.items()):
            ns, kind, name = key
            if kind != "pod":
                continue
            if not all_namespaces and ns != namespace:
                continue
            if resource["status"] in self._TERMINAL_POD_STATUSES:
                del self._resources[key]
                removed.append({"namespace": ns, "name": name})
        return {"removed": removed}

    def node_management(self, operation: str, node_name: str, confirm_drain: bool = False) -> dict:
        if node_name not in self._nodes:
            return {"error": f"no such node {node_name!r}"}
        if operation == "cordon":
            self._nodes[node_name]["schedulable"] = False
            return {"node": node_name, "schedulable": False}
        if operation == "uncordon":
            self._nodes[node_name]["schedulable"] = True
            return {"node": node_name, "schedulable": True}
        if operation == "drain":
            if not confirm_drain:
                return {"error": "drain is destructive - pass confirm_drain=true to proceed"}
            self._nodes[node_name]["schedulable"] = False
            evicted = []
            for resource in self._resources.values():
                if resource.get("kind") == "pod" and resource.get("node") == node_name:
                    resource["status"] = "Evicted"
                    resource["node"] = None
                    evicted.append(resource["name"])
            return {"node": node_name, "schedulable": False, "evicted_pods": sorted(evicted)}
        return {"error": f"unknown operation {operation!r} (expected cordon, drain, or uncordon)"}

    # ── connectivity ─────────────────────────────────────────────────────

    def ping(self) -> dict:
        return {"connected": True, "context": self._current_context}

    def summary(self) -> dict:
        return {
            "n_calls": len(self.call_log),
            "namespace_count": len(self._namespaces),
            "namespaces": sorted(self._namespaces),
            "current_context": self._current_context,
            "resource_count": len(self._resources),
            "pod_count": sum(1 for (_, k, _) in self._resources if k == "pod"),
            "running_pod_count": sum(
                1 for (_, k, _), r in self._resources.items() if k == "pod" and r.get("status") == "Running"
            ),
            "helm_release_count": len(self._helm_releases),
            "resources": {
                f"{ns}/{kind}/{name}": {"status": r.get("status"), "replicas": r.get("replicas"), "image": r.get("image")}
                for (ns, kind, name), r in sorted(self._resources.items())
            },
            "helm_releases": {
                f"{ns}/{name}": {"chart": r["chart"], "revision": r["revision"]}
                for (ns, name), r in sorted(self._helm_releases.items())
            },
            "node_schedulable": {n: v["schedulable"] for n, v in sorted(self._nodes.items())},
            "port_forward_count": len(self._port_forwards),
        }


class ForgeService(MockService):
    """A mock issue/PR forge (GitHub-MCP-inspired) - all 77 tools the
    official `github/github-mcp-server` exposes across its 17 toolsets
    (`DEV_NOTES/TOOL_CATALOG_COMPLETE.md` §3: Actions, Code Quality, Code
    Security, Context, Copilot, Dependabot, Discussions, Gists, Git,
    Issues, Labels, Notifications, Organizations, Projects, Pull Requests,
    Repositories, Secret Protection).

    Every repo-scoped tool takes explicit ``owner``/``repo`` params, same
    as the real server - "did the agent operate on the right repo" is
    itself gradable, the same role `namespace` plays for
    `KubernetesService`. File/commit/tree content is modeled as a flat
    ``{path: content}`` map per repo rather than a real git object graph -
    `git_repo`/`filesystem` already own "does the agent use git/filesystem
    tools correctly"; this service exists to test forge-specific judgment
    (issue/PR/review/label/notification workflow), so file content here is
    just enough state for `get_file_contents`/`create_or_update_file`/
    `push_files` to have somewhere real to act, not a second git
    implementation.

    `pull_request_review_write` models GitHub's real two-shape review
    flow: called with no ``event`` (or ``event="PENDING"``), it starts (or
    resumes) a pending review that `add_comment_to_pending_review` can
    then attach line comments to across several calls; called with a real
    ``event`` (APPROVE/REQUEST_CHANGES/COMMENT), it submits that pending
    review if one is open, or creates and submits a review directly in
    one shot if not - `add_comment_to_pending_review` refuses if no
    pending review is open, so "start a review before attaching line
    comments to it" is a real, testable precondition. `merge_pull_request`
    refuses a draft, an already-merged/closed PR, or one whose most recent
    review is an unresolved REQUEST_CHANGES not yet superseded by an
    APPROVE - "don't merge over open change requests" is the same
    workflow-discipline skill `git_repo`'s "no commit without staging" and
    `docker`'s precondition-enforcing tools test.
    """

    TOOLS = {name: name for name in (
        "actions_list", "actions_get", "actions_run_trigger", "get_job_logs",
        "get_code_quality_finding",
        "get_code_scanning_alert", "list_code_scanning_alerts",
        "get_me", "get_teams", "get_team_members",
        "assign_copilot_to_issue", "assign_copilot_to_issue_with_intent", "request_copilot_review",
        "get_dependabot_alert", "list_dependabot_alerts",
        "list_discussions", "get_discussion", "list_discussion_categories",
        "get_discussion_comments", "discussion_comment_write",
        "create_gist", "get_gist", "list_gists", "update_gist",
        "get_repository_tree",
        "list_issues", "search_issues", "issue_read", "issue_write",
        "add_issue_comment", "get_label", "list_issue_fields", "list_issue_types",
        "sub_issue_write",
        "label_write", "list_label",
        "list_notifications", "get_notification_details", "dismiss_notification",
        "mark_all_notifications_read", "manage_notification_subscription",
        "manage_repository_notification_subscription",
        "search_orgs",
        "projects_list", "projects_get", "projects_write",
        "list_pull_requests", "search_pull_requests", "pull_request_read",
        "create_pull_request", "update_pull_request", "merge_pull_request",
        "update_pull_request_branch", "pull_request_review_write",
        "add_comment_to_pending_review", "add_reply_to_pull_request_comment",
        "create_repository", "fork_repository", "search_repositories",
        "get_file_contents", "create_or_update_file", "delete_file", "push_files",
        "create_branch", "list_branches", "get_commit", "list_commits",
        "search_commits", "search_code", "get_tag", "list_tags",
        "get_latest_release", "get_release_by_tag", "list_releases",
        "list_repository_collaborators",
        "get_secret_scanning_alert", "list_secret_scanning_alerts",
    )}

    _CURRENT_USER = "octo-agent"

    def __init__(self) -> None:
        super().__init__()
        self._repos: dict[str, dict] = {}
        self._files: dict[str, dict[str, str]] = {}
        self._branches: dict[str, dict[str, str]] = {}
        self._commits: dict[str, dict[str, dict]] = {}
        self._tags: dict[str, dict[str, str]] = {}
        self._releases: dict[str, dict[str, dict]] = {}
        self._issues: dict[tuple[str, int], dict] = {}
        self._pull_requests: dict[tuple[str, int], dict] = {}
        self._pending_reviews: dict[tuple[str, int], dict] = {}
        self._labels: dict[str, dict[str, dict]] = {}
        self._gists: dict[str, dict] = {}
        self._discussions: dict[tuple[str, int], dict] = {}
        self._discussion_categories: dict[str, list[str]] = {}
        self._notifications: dict[str, dict] = {}
        self._projects: dict[str, dict] = {}
        self._actions_runs: dict[tuple[str, int], dict] = {}
        self._code_scanning_alerts: dict[tuple[str, int], dict] = {}
        self._dependabot_alerts: dict[tuple[str, int], dict] = {}
        self._secret_scanning_alerts: dict[tuple[str, int], dict] = {}
        self._code_quality_findings: dict[tuple[str, str], dict] = {}
        self._teams: dict[str, list[str]] = {}
        self._orgs: set[str] = set()
        self._next_number: dict[str, int] = {}   # "owner/repo" -> shared issue+PR counter
        self._next_run_id = 1
        self._next_gist_id = 1
        self._next_notification_id = 1
        self._next_project_id = 1
        self._next_discussion_number: dict[str, int] = {}

    # ── seeding ──────────────────────────────────────────────────────────

    def seed(self, spec: dict) -> None:
        """``spec`` keys, all optional - repo-scoped ones keyed by
        ``"owner/repo"``:
        - ``repos`` ({"owner/repo": {description, private, default_branch,
          collaborators: {user: role}}}).
        - ``files`` ({"owner/repo": {path: content}}).
        - ``branches`` ({"owner/repo": {branch: sha}}) - ``main`` always
          exists once its repo is seeded, even with an empty dict.
        - ``labels`` ({"owner/repo": {name: {color, description}}}).
        - ``tags`` ({"owner/repo": {tag: sha}}).
        - ``releases`` ({"owner/repo": {tag: {name, body, draft,
          prerelease}}}).
        - ``issues`` ([{owner, repo, number, title, body, state, labels,
          assignees, type, comments}, ...]).
        - ``pull_requests`` ([{owner, repo, number, title, body, state,
          head, base, draft, merged, reviews: [{event}, ...]}, ...]).
        - ``gists`` ([{id, description, files, public}, ...]).
        - ``discussions`` ([{owner, repo, number, title, body, category,
          comments}, ...]).
        - ``discussion_categories`` ({"owner/repo": [name, ...]}).
        - ``notifications`` ([{id, owner, repo, reason, unread, subject},
          ...]).
        - ``projects`` ([{id, owner, title, body}, ...]).
        - ``commits`` ({"owner/repo": {sha: {message, files}}}).
        - ``actions_runs`` ([{owner, repo, run_id, workflow_id, status,
          conclusion, jobs: [{id, name, logs}]}, ...]).
        - ``code_scanning_alerts`` / ``dependabot_alerts`` /
          ``secret_scanning_alerts`` ([{owner, repo, number, ...}, ...]).
        - ``code_quality_findings`` ([{owner, repo, id, ...}, ...]).
        - ``teams`` ({"org/team_slug": [member, ...]}).
        - ``orgs`` ([name, ...]).
        """
        for full_name, cfg in (spec.get("repos") or {}).items():
            self._repos[full_name] = {
                "description": cfg.get("description", ""), "private": cfg.get("private", False),
                "default_branch": cfg.get("default_branch", "main"),
                "collaborators": dict(cfg.get("collaborators", {})),
            }
            self._branches.setdefault(full_name, {"main": "sha-main-0"})
            self._files.setdefault(full_name, {})
            self._labels.setdefault(full_name, {})
            self._next_number.setdefault(full_name, 0)
        for full_name, files in (spec.get("files") or {}).items():
            self._ensure_repo(full_name)
            self._files.setdefault(full_name, {}).update(files)
        for full_name, branches in (spec.get("branches") or {}).items():
            self._ensure_repo(full_name)
            self._branches.setdefault(full_name, {}).update(branches)
        for full_name, labels in (spec.get("labels") or {}).items():
            self._ensure_repo(full_name)
            self._labels.setdefault(full_name, {}).update(labels)
        for full_name, tags in (spec.get("tags") or {}).items():
            self._ensure_repo(full_name)
            self._tags.setdefault(full_name, {}).update(tags)
        for full_name, releases in (spec.get("releases") or {}).items():
            self._ensure_repo(full_name)
            self._releases.setdefault(full_name, {}).update(releases)
        for full_name, commits in (spec.get("commits") or {}).items():
            self._ensure_repo(full_name)
            self._commits.setdefault(full_name, {}).update(commits)
        for entry in spec.get("issues") or []:
            full_name = f"{entry['owner']}/{entry['repo']}"
            self._ensure_repo(full_name)
            number = entry["number"]
            self._issues[(full_name, number)] = {
                "number": number, "title": entry["title"], "body": entry.get("body", ""),
                "state": entry.get("state", "open"), "labels": list(entry.get("labels", [])),
                "assignees": list(entry.get("assignees", [])), "type": entry.get("type"),
                "comments": list(entry.get("comments", [])), "sub_issues": [],
            }
            self._next_number[full_name] = max(self._next_number.get(full_name, 0), number)
        for entry in spec.get("pull_requests") or []:
            full_name = f"{entry['owner']}/{entry['repo']}"
            self._ensure_repo(full_name)
            number = entry["number"]
            head, base = entry.get("head", "feature"), entry.get("base", "main")
            self._branches[full_name].setdefault(head, f"sha-{head}-0")
            self._branches[full_name].setdefault(base, f"sha-{base}-0")
            self._pull_requests[(full_name, number)] = {
                "number": number, "title": entry["title"], "body": entry.get("body", ""),
                "state": entry.get("state", "open"), "head": head, "base": base,
                "draft": entry.get("draft", False),
                "merged": entry.get("merged", False), "reviews": list(entry.get("reviews", [])),
                "review_comments": list(entry.get("review_comments", [])),
            }
            self._next_number[full_name] = max(self._next_number.get(full_name, 0), number)
        for entry in spec.get("gists") or []:
            self._gists[entry["id"]] = {
                "id": entry["id"], "description": entry.get("description", ""),
                "files": dict(entry.get("files", {})), "public": entry.get("public", False),
            }
        for entry in spec.get("discussions") or []:
            full_name = f"{entry['owner']}/{entry['repo']}"
            self._ensure_repo(full_name)
            number = entry["number"]
            self._discussions[(full_name, number)] = {
                "number": number, "title": entry["title"], "body": entry.get("body", ""),
                "category": entry.get("category", "General"), "comments": list(entry.get("comments", [])),
            }
            self._next_discussion_number[full_name] = max(self._next_discussion_number.get(full_name, 0), number)
        for full_name, categories in (spec.get("discussion_categories") or {}).items():
            self._ensure_repo(full_name)
            self._discussion_categories[full_name] = list(categories)
        for entry in spec.get("notifications") or []:
            self._notifications[entry["id"]] = {
                "id": entry["id"], "owner": entry.get("owner"), "repo": entry.get("repo"),
                "reason": entry.get("reason", "mention"), "unread": entry.get("unread", True),
                "subject": entry.get("subject", ""),
            }
        for entry in spec.get("projects") or []:
            self._projects[entry["id"]] = {
                "id": entry["id"], "owner": entry["owner"], "title": entry["title"],
                "body": entry.get("body", ""),
            }
        for entry in spec.get("actions_runs") or []:
            full_name = f"{entry['owner']}/{entry['repo']}"
            self._ensure_repo(full_name)
            self._actions_runs[(full_name, entry["run_id"])] = {
                "run_id": entry["run_id"], "workflow_id": entry.get("workflow_id", "ci.yml"),
                "status": entry.get("status", "completed"), "conclusion": entry.get("conclusion"),
                "jobs": list(entry.get("jobs", [])),
            }
        for entry in spec.get("code_scanning_alerts") or []:
            full_name = f"{entry['owner']}/{entry['repo']}"
            self._ensure_repo(full_name)
            self._code_scanning_alerts[(full_name, entry["number"])] = dict(entry)
        for entry in spec.get("dependabot_alerts") or []:
            full_name = f"{entry['owner']}/{entry['repo']}"
            self._ensure_repo(full_name)
            self._dependabot_alerts[(full_name, entry["number"])] = dict(entry)
        for entry in spec.get("secret_scanning_alerts") or []:
            full_name = f"{entry['owner']}/{entry['repo']}"
            self._ensure_repo(full_name)
            self._secret_scanning_alerts[(full_name, entry["number"])] = dict(entry)
        for entry in spec.get("code_quality_findings") or []:
            full_name = f"{entry['owner']}/{entry['repo']}"
            self._ensure_repo(full_name)
            self._code_quality_findings[(full_name, entry["id"])] = dict(entry)
        for key, members in (spec.get("teams") or {}).items():
            self._teams[key] = list(members)
        for org in spec.get("orgs") or []:
            self._orgs.add(org)

    # ── internal helpers ────────────────────────────────────────────────

    def _ensure_repo(self, full_name: str) -> None:
        """Auto-registers a bare repo entry when a seed section references
        ``owner/repo`` without an explicit ``repos`` entry for it - seeding
        an issue/PR/alert/etc. obviously implies its repo exists, the same
        "referencing it is enough to seed it" convenience `DockerService`
        gives a container's image."""
        if full_name not in self._repos:
            self._repos[full_name] = {"description": "", "private": False,
                                       "default_branch": "main", "collaborators": {}}
        self._branches.setdefault(full_name, {"main": "sha-main-0"})
        self._files.setdefault(full_name, {})
        self._labels.setdefault(full_name, {})
        self._next_number.setdefault(full_name, 0)

    @staticmethod
    def _full(owner: str, repo: str) -> str:
        return f"{owner}/{repo}"

    def _require_repo(self, owner: str, repo: str) -> dict | None:
        if self._full(owner, repo) not in self._repos:
            return {"error": f"no such repository {owner}/{repo}"}
        return None

    def _require_issue(self, owner: str, repo: str, number: int) -> dict | None:
        if (self._full(owner, repo), number) not in self._issues:
            return {"error": f"no such issue {owner}/{repo}#{number}"}
        return None

    def _require_pr(self, owner: str, repo: str, number: int) -> dict | None:
        if (self._full(owner, repo), number) not in self._pull_requests:
            return {"error": f"no such pull request {owner}/{repo}#{number}"}
        return None

    def _next_issue_or_pr_number(self, full_name: str) -> int:
        self._next_number[full_name] = self._next_number.get(full_name, 0) + 1
        return self._next_number[full_name]

    # ── actions ──────────────────────────────────────────────────────────

    def actions_list(self, owner: str, repo: str, workflow_id: str | None = None) -> dict:
        err = self._require_repo(owner, repo)
        if err:
            return err
        full = self._full(owner, repo)
        runs = [r for (fn, _), r in self._actions_runs.items()
                if fn == full and (workflow_id is None or r["workflow_id"] == workflow_id)]
        return {"runs": runs}

    def actions_get(self, owner: str, repo: str, run_id: int) -> dict:
        key = (self._full(owner, repo), run_id)
        if key not in self._actions_runs:
            return {"error": f"no such run {run_id} in {owner}/{repo}"}
        return dict(self._actions_runs[key])

    def actions_run_trigger(self, owner: str, repo: str, workflow_id: str, ref: str = "main",
                             inputs: dict | None = None) -> dict:
        err = self._require_repo(owner, repo)
        if err:
            return err
        full = self._full(owner, repo)
        run_id = self._next_run_id
        self._next_run_id += 1
        self._actions_runs[(full, run_id)] = {
            "run_id": run_id, "workflow_id": workflow_id, "ref": ref, "status": "queued",
            "conclusion": None, "jobs": [],
        }
        return dict(self._actions_runs[(full, run_id)])

    def get_job_logs(self, owner: str, repo: str, job_id: str) -> dict:
        full = self._full(owner, repo)
        for (fn, _run_id), run in self._actions_runs.items():
            if fn != full:
                continue
            for job in run.get("jobs", []):
                if job.get("id") == job_id:
                    return {"job_id": job_id, "logs": job.get("logs", [])}
        return {"error": f"no such job {job_id!r} in {owner}/{repo}"}

    # ── code quality / security / dependabot / secret protection ───────

    def get_code_quality_finding(self, owner: str, repo: str, finding_id: str) -> dict:
        key = (self._full(owner, repo), finding_id)
        if key not in self._code_quality_findings:
            return {"error": f"no such finding {finding_id!r} in {owner}/{repo}"}
        return dict(self._code_quality_findings[key])

    def get_code_scanning_alert(self, owner: str, repo: str, alert_number: int) -> dict:
        key = (self._full(owner, repo), alert_number)
        if key not in self._code_scanning_alerts:
            return {"error": f"no such alert #{alert_number} in {owner}/{repo}"}
        return dict(self._code_scanning_alerts[key])

    def list_code_scanning_alerts(self, owner: str, repo: str, state: str | None = None) -> dict:
        full = self._full(owner, repo)
        alerts = [a for (fn, _), a in self._code_scanning_alerts.items()
                  if fn == full and (state is None or a.get("state") == state)]
        return {"alerts": alerts}

    def get_dependabot_alert(self, owner: str, repo: str, alert_number: int) -> dict:
        key = (self._full(owner, repo), alert_number)
        if key not in self._dependabot_alerts:
            return {"error": f"no such alert #{alert_number} in {owner}/{repo}"}
        return dict(self._dependabot_alerts[key])

    def list_dependabot_alerts(self, owner: str, repo: str, state: str | None = None) -> dict:
        full = self._full(owner, repo)
        alerts = [a for (fn, _), a in self._dependabot_alerts.items()
                  if fn == full and (state is None or a.get("state") == state)]
        return {"alerts": alerts}

    def get_secret_scanning_alert(self, owner: str, repo: str, alert_number: int) -> dict:
        key = (self._full(owner, repo), alert_number)
        if key not in self._secret_scanning_alerts:
            return {"error": f"no such alert #{alert_number} in {owner}/{repo}"}
        return dict(self._secret_scanning_alerts[key])

    def list_secret_scanning_alerts(self, owner: str, repo: str, state: str | None = None) -> dict:
        full = self._full(owner, repo)
        alerts = [a for (fn, _), a in self._secret_scanning_alerts.items()
                  if fn == full and (state is None or a.get("state") == state)]
        return {"alerts": alerts}

    # ── context / copilot / organizations ───────────────────────────────

    def get_me(self) -> dict:
        return {"login": self._CURRENT_USER}

    def get_teams(self, org: str) -> dict:
        return {"teams": sorted({k.split("/", 1)[1] for k in self._teams if k.startswith(f"{org}/")})}

    def get_team_members(self, org: str, team_slug: str) -> dict:
        key = f"{org}/{team_slug}"
        if key not in self._teams:
            return {"error": f"no such team {team_slug!r} in org {org!r}"}
        return {"members": list(self._teams[key])}

    def assign_copilot_to_issue(self, owner: str, repo: str, issue_number: int) -> dict:
        err = self._require_issue(owner, repo, issue_number)
        if err:
            return err
        self._issues[(self._full(owner, repo), issue_number)]["assignees"].append("copilot")
        return {"owner": owner, "repo": repo, "issue_number": issue_number, "assigned": "copilot"}

    def assign_copilot_to_issue_with_intent(self, owner: str, repo: str, issue_number: int, intent: str) -> dict:
        result = self.assign_copilot_to_issue(owner, repo, issue_number)
        if "error" not in result:
            result["intent"] = intent
        return result

    def request_copilot_review(self, owner: str, repo: str, pull_number: int) -> dict:
        err = self._require_pr(owner, repo, pull_number)
        if err:
            return err
        return {"owner": owner, "repo": repo, "pull_number": pull_number, "requested_reviewer": "copilot"}

    def search_orgs(self, query: str) -> dict:
        q = (query or "").lower()
        return {"organizations": sorted(o for o in self._orgs if q in o.lower())}

    # ── discussions ──────────────────────────────────────────────────────

    def list_discussions(self, owner: str, repo: str) -> dict:
        full = self._full(owner, repo)
        return {"discussions": [d for (fn, _), d in self._discussions.items() if fn == full]}

    def get_discussion(self, owner: str, repo: str, discussion_number: int) -> dict:
        key = (self._full(owner, repo), discussion_number)
        if key not in self._discussions:
            return {"error": f"no such discussion #{discussion_number} in {owner}/{repo}"}
        return dict(self._discussions[key])

    def list_discussion_categories(self, owner: str, repo: str) -> dict:
        return {"categories": list(self._discussion_categories.get(self._full(owner, repo), ["General"]))}

    def get_discussion_comments(self, owner: str, repo: str, discussion_number: int) -> dict:
        key = (self._full(owner, repo), discussion_number)
        if key not in self._discussions:
            return {"error": f"no such discussion #{discussion_number} in {owner}/{repo}"}
        return {"comments": list(self._discussions[key]["comments"])}

    def discussion_comment_write(self, owner: str, repo: str, discussion_number: int, body: str) -> dict:
        key = (self._full(owner, repo), discussion_number)
        if key not in self._discussions:
            return {"error": f"no such discussion #{discussion_number} in {owner}/{repo}"}
        comment = {"body": body, "author": self._CURRENT_USER}
        self._discussions[key]["comments"].append(comment)
        return comment

    # ── gists ────────────────────────────────────────────────────────────

    def create_gist(self, description: str, files: dict, public: bool = False) -> dict:
        gist_id = f"gist{self._next_gist_id}"
        self._next_gist_id += 1
        self._gists[gist_id] = {"id": gist_id, "description": description, "files": dict(files), "public": public}
        return dict(self._gists[gist_id])

    def get_gist(self, gist_id: str) -> dict:
        if gist_id not in self._gists:
            return {"error": f"no such gist {gist_id!r}"}
        return dict(self._gists[gist_id])

    def list_gists(self) -> dict:
        return {"gists": list(self._gists.values())}

    def update_gist(self, gist_id: str, files: dict | None = None, description: str | None = None) -> dict:
        if gist_id not in self._gists:
            return {"error": f"no such gist {gist_id!r}"}
        gist = self._gists[gist_id]
        if files is not None:
            gist["files"].update(files)
        if description is not None:
            gist["description"] = description
        return dict(gist)

    # ── git ──────────────────────────────────────────────────────────────

    def get_repository_tree(self, owner: str, repo: str, ref: str | None = None) -> dict:
        err = self._require_repo(owner, repo)
        if err:
            return err
        return {"paths": sorted(self._files.get(self._full(owner, repo), {}))}

    # ── issues ───────────────────────────────────────────────────────────

    def list_issues(self, owner: str, repo: str, state: str | None = None, labels: list | None = None) -> dict:
        err = self._require_repo(owner, repo)
        if err:
            return err
        full = self._full(owner, repo)
        labels = self._as_list(labels)
        issues = [
            i for (fn, _), i in self._issues.items() if fn == full
            and (state is None or i["state"] == state)
            and (not labels or all(l in i["labels"] for l in labels))
        ]
        return {"issues": issues}

    def search_issues(self, query: str) -> dict:
        q = (query or "").lower()
        matches = [dict(i, repo=fn) for (fn, _), i in self._issues.items()
                   if q in i["title"].lower() or q in i.get("body", "").lower()]
        return {"issues": matches}

    def issue_read(self, owner: str, repo: str, issue_number: int) -> dict:
        err = self._require_issue(owner, repo, issue_number)
        if err:
            return err
        return dict(self._issues[(self._full(owner, repo), issue_number)])

    def issue_write(self, owner: str, repo: str, issue_number: int | None = None, title: str | None = None,
                     body: str | None = None, state: str | None = None, labels: list | None = None,
                     assignees: list | None = None, type: str | None = None) -> dict:
        err = self._require_repo(owner, repo)
        if err:
            return err
        full = self._full(owner, repo)
        if issue_number is None:
            if not title:
                return {"error": "title is required to create an issue"}
            issue_number = self._next_issue_or_pr_number(full)
            self._issues[(full, issue_number)] = {
                "number": issue_number, "title": title, "body": body or "", "state": "open",
                "labels": self._as_list(labels), "assignees": self._as_list(assignees),
                "type": type, "comments": [], "sub_issues": [],
            }
            return dict(self._issues[(full, issue_number)])
        key = (full, issue_number)
        if key not in self._issues:
            return {"error": f"no such issue {owner}/{repo}#{issue_number}"}
        issue = self._issues[key]
        if title is not None:
            issue["title"] = title
        if body is not None:
            issue["body"] = body
        if state is not None:
            issue["state"] = state
        if labels is not None:
            issue["labels"] = self._as_list(labels)
        if assignees is not None:
            issue["assignees"] = self._as_list(assignees)
        if type is not None:
            issue["type"] = type
        return dict(issue)

    def add_issue_comment(self, owner: str, repo: str, issue_number: int, body: str) -> dict:
        err = self._require_issue(owner, repo, issue_number)
        if err:
            return err
        comment = {"body": body, "author": self._CURRENT_USER}
        self._issues[(self._full(owner, repo), issue_number)]["comments"].append(comment)
        return comment

    def get_label(self, owner: str, repo: str, name: str) -> dict:
        label = self._labels.get(self._full(owner, repo), {}).get(name)
        if label is None:
            return {"error": f"no such label {name!r} in {owner}/{repo}"}
        return {"name": name, **label}

    def list_issue_fields(self, owner: str, repo: str) -> dict:
        return {"fields": ["title", "body", "state", "labels", "assignees", "type"]}

    def list_issue_types(self, owner: str, repo: str) -> dict:
        return {"types": ["Bug", "Feature", "Task"]}

    def sub_issue_write(self, owner: str, repo: str, issue_number: int, sub_issue_number: int,
                         operation: str = "add") -> dict:
        err = self._require_issue(owner, repo, issue_number)
        if err:
            return err
        err2 = self._require_issue(owner, repo, sub_issue_number)
        if err2:
            return err2
        sub_issues = self._issues[(self._full(owner, repo), issue_number)]["sub_issues"]
        if operation == "add":
            if sub_issue_number not in sub_issues:
                sub_issues.append(sub_issue_number)
        elif operation == "remove":
            if sub_issue_number in sub_issues:
                sub_issues.remove(sub_issue_number)
        else:
            return {"error": f"unknown operation {operation!r} (expected add or remove)"}
        return {"issue_number": issue_number, "sub_issues": list(sub_issues)}

    # ── labels ───────────────────────────────────────────────────────────

    def label_write(self, owner: str, repo: str, name: str, color: str | None = None,
                     description: str | None = None, delete: bool = False) -> dict:
        err = self._require_repo(owner, repo)
        if err:
            return err
        labels = self._labels.setdefault(self._full(owner, repo), {})
        if delete:
            if name not in labels:
                return {"error": f"no such label {name!r} in {owner}/{repo}"}
            del labels[name]
            return {"name": name, "deleted": True}
        labels[name] = {"color": color or labels.get(name, {}).get("color", "ededed"),
                         "description": description or labels.get(name, {}).get("description", "")}
        return {"name": name, **labels[name]}

    def list_label(self, owner: str, repo: str) -> dict:
        err = self._require_repo(owner, repo)
        if err:
            return err
        return {"labels": [{"name": n, **v} for n, v in sorted(self._labels.get(self._full(owner, repo), {}).items())]}

    # ── notifications ────────────────────────────────────────────────────

    def list_notifications(self, owner: str | None = None, repo: str | None = None, unread_only: bool = True) -> dict:
        notifications = [
            n for n in self._notifications.values()
            if (owner is None or n.get("owner") == owner)
            and (repo is None or n.get("repo") == repo)
            and (not unread_only or n["unread"])
        ]
        return {"notifications": notifications}

    def get_notification_details(self, notification_id: str) -> dict:
        if notification_id not in self._notifications:
            return {"error": f"no such notification {notification_id!r}"}
        return dict(self._notifications[notification_id])

    def dismiss_notification(self, notification_id: str) -> dict:
        if notification_id not in self._notifications:
            return {"error": f"no such notification {notification_id!r}"}
        self._notifications[notification_id]["unread"] = False
        return {"id": notification_id, "unread": False}

    def mark_all_notifications_read(self) -> dict:
        count = 0
        for n in self._notifications.values():
            if n["unread"]:
                n["unread"] = False
                count += 1
        return {"marked_read": count}

    def manage_notification_subscription(self, notification_id: str, action: str) -> dict:
        if notification_id not in self._notifications:
            return {"error": f"no such notification {notification_id!r}"}
        if action not in ("ignore", "watch", "delete"):
            return {"error": f"unknown action {action!r} (expected ignore, watch, or delete)"}
        if action == "delete":
            del self._notifications[notification_id]
            return {"id": notification_id, "deleted": True}
        self._notifications[notification_id]["subscription"] = action
        return {"id": notification_id, "subscription": action}

    def manage_repository_notification_subscription(self, owner: str, repo: str, action: str) -> dict:
        err = self._require_repo(owner, repo)
        if err:
            return err
        if action not in ("ignore", "watch", "delete"):
            return {"error": f"unknown action {action!r} (expected ignore, watch, or delete)"}
        self._repos[self._full(owner, repo)]["notification_subscription"] = action
        return {"owner": owner, "repo": repo, "subscription": action}

    # ── projects ─────────────────────────────────────────────────────────

    def projects_list(self, owner: str) -> dict:
        return {"projects": [p for p in self._projects.values() if p["owner"] == owner]}

    def projects_get(self, project_id: str) -> dict:
        if project_id not in self._projects:
            return {"error": f"no such project {project_id!r}"}
        return dict(self._projects[project_id])

    def projects_write(self, owner: str, project_id: str | None = None, title: str | None = None,
                        body: str | None = None) -> dict:
        if project_id is None:
            if not title:
                return {"error": "title is required to create a project"}
            project_id = f"proj{self._next_project_id}"
            self._next_project_id += 1
            self._projects[project_id] = {"id": project_id, "owner": owner, "title": title, "body": body or ""}
            return dict(self._projects[project_id])
        if project_id not in self._projects:
            return {"error": f"no such project {project_id!r}"}
        project = self._projects[project_id]
        if title is not None:
            project["title"] = title
        if body is not None:
            project["body"] = body
        return dict(project)

    # ── pull requests ────────────────────────────────────────────────────

    def list_pull_requests(self, owner: str, repo: str, state: str | None = None) -> dict:
        err = self._require_repo(owner, repo)
        if err:
            return err
        full = self._full(owner, repo)
        prs = [p for (fn, _), p in self._pull_requests.items() if fn == full and (state is None or p["state"] == state)]
        return {"pull_requests": prs}

    def search_pull_requests(self, query: str) -> dict:
        q = (query or "").lower()
        matches = [dict(p, repo=fn) for (fn, _), p in self._pull_requests.items()
                   if q in p["title"].lower() or q in p.get("body", "").lower()]
        return {"pull_requests": matches}

    def pull_request_read(self, owner: str, repo: str, pull_number: int) -> dict:
        err = self._require_pr(owner, repo, pull_number)
        if err:
            return err
        return dict(self._pull_requests[(self._full(owner, repo), pull_number)])

    def create_pull_request(self, owner: str, repo: str, title: str, head: str, base: str,
                             body: str | None = None, draft: bool = False) -> dict:
        err = self._require_repo(owner, repo)
        if err:
            return err
        full = self._full(owner, repo)
        branches = self._branches.get(full, {})
        if head not in branches:
            return {"error": f"no such branch {head!r} in {owner}/{repo}"}
        if base not in branches:
            return {"error": f"no such branch {base!r} in {owner}/{repo}"}
        number = self._next_issue_or_pr_number(full)
        self._pull_requests[(full, number)] = {
            "number": number, "title": title, "body": body or "", "state": "open",
            "head": head, "base": base, "draft": draft, "merged": False,
            "reviews": [], "review_comments": [],
        }
        return dict(self._pull_requests[(full, number)])

    def update_pull_request(self, owner: str, repo: str, pull_number: int, title: str | None = None,
                             body: str | None = None, state: str | None = None, base: str | None = None) -> dict:
        err = self._require_pr(owner, repo, pull_number)
        if err:
            return err
        pr = self._pull_requests[(self._full(owner, repo), pull_number)]
        if title is not None:
            pr["title"] = title
        if body is not None:
            pr["body"] = body
        if state is not None:
            pr["state"] = state
        if base is not None:
            pr["base"] = base
        return dict(pr)

    @staticmethod
    def _pr_has_unresolved_change_request(pr: dict) -> bool:
        for review in reversed(pr["reviews"]):
            if review["event"] == "REQUEST_CHANGES":
                return True
            if review["event"] == "APPROVE":
                return False
        return False

    def merge_pull_request(self, owner: str, repo: str, pull_number: int, merge_method: str = "merge") -> dict:
        err = self._require_pr(owner, repo, pull_number)
        if err:
            return err
        pr = self._pull_requests[(self._full(owner, repo), pull_number)]
        if pr["merged"]:
            return {"error": f"pull request #{pull_number} is already merged"}
        if pr["state"] != "open":
            return {"error": f"pull request #{pull_number} is not open"}
        if pr["draft"]:
            return {"error": f"pull request #{pull_number} is a draft - mark it ready for review first"}
        if self._pr_has_unresolved_change_request(pr):
            return {"error": f"pull request #{pull_number} has an unresolved REQUEST_CHANGES review"}
        pr["merged"] = True
        pr["state"] = "closed"
        return {"number": pull_number, "merged": True, "merge_method": merge_method}

    def update_pull_request_branch(self, owner: str, repo: str, pull_number: int) -> dict:
        err = self._require_pr(owner, repo, pull_number)
        if err:
            return err
        return {"number": pull_number, "synced_with_base": True}

    def pull_request_review_write(self, owner: str, repo: str, pull_number: int,
                                   event: str | None = None, body: str | None = None) -> dict:
        err = self._require_pr(owner, repo, pull_number)
        if err:
            return err
        key = (self._full(owner, repo), pull_number)
        if event is None or event == "PENDING":
            self._pending_reviews.setdefault(key, {"comments": []})
            return {"pull_number": pull_number, "review_state": "pending"}
        if event not in ("APPROVE", "REQUEST_CHANGES", "COMMENT"):
            return {"error": f"unknown event {event!r} (expected APPROVE, REQUEST_CHANGES, or COMMENT)"}
        pending = self._pending_reviews.pop(key, None)
        review = {"event": event, "body": body or "", "author": self._CURRENT_USER,
                  "comments": pending["comments"] if pending else []}
        self._pull_requests[key]["reviews"].append(review)
        return dict(review)

    def add_comment_to_pending_review(self, owner: str, repo: str, pull_number: int, path: str,
                                       line: int, body: str) -> dict:
        err = self._require_pr(owner, repo, pull_number)
        if err:
            return err
        key = (self._full(owner, repo), pull_number)
        if key not in self._pending_reviews:
            return {"error": f"no pending review open for pull request #{pull_number} - start one first"}
        comment = {"path": path, "line": line, "body": body}
        self._pending_reviews[key]["comments"].append(comment)
        return comment

    def add_reply_to_pull_request_comment(self, owner: str, repo: str, pull_number: int,
                                           comment_id: int, body: str) -> dict:
        err = self._require_pr(owner, repo, pull_number)
        if err:
            return err
        reply = {"in_reply_to": comment_id, "body": body, "author": self._CURRENT_USER}
        self._pull_requests[(self._full(owner, repo), pull_number)]["review_comments"].append(reply)
        return reply

    # ── repositories ─────────────────────────────────────────────────────

    def create_repository(self, name: str, description: str | None = None, private: bool = False) -> dict:
        full = self._full(self._CURRENT_USER, name)
        if full in self._repos:
            return {"error": f"repository {full!r} already exists"}
        self._repos[full] = {"description": description or "", "private": private,
                              "default_branch": "main", "collaborators": {}}
        self._branches[full] = {"main": "sha-main-0"}
        self._files[full] = {}
        self._labels[full] = {}
        self._next_number[full] = 0
        return {"owner": self._CURRENT_USER, "repo": name, "full_name": full}

    def fork_repository(self, owner: str, repo: str) -> dict:
        err = self._require_repo(owner, repo)
        if err:
            return err
        src = self._full(owner, repo)
        dst = self._full(self._CURRENT_USER, repo)
        if dst in self._repos:
            return {"error": f"repository {dst!r} already exists"}
        self._repos[dst] = dict(self._repos[src])
        self._branches[dst] = dict(self._branches.get(src, {}))
        self._files[dst] = dict(self._files.get(src, {}))
        self._labels[dst] = dict(self._labels.get(src, {}))
        self._next_number[dst] = 0
        return {"owner": self._CURRENT_USER, "repo": repo, "full_name": dst, "forked_from": src}

    def search_repositories(self, query: str) -> dict:
        q = (query or "").lower()
        return {"repositories": [n for n in sorted(self._repos) if q in n.lower()]}

    def get_file_contents(self, owner: str, repo: str, path: str, ref: str | None = None) -> dict:
        err = self._require_repo(owner, repo)
        if err:
            return err
        content = self._files.get(self._full(owner, repo), {}).get(path)
        if content is None:
            return {"error": f"no such path {path!r} in {owner}/{repo}"}
        return {"path": path, "content": content}

    def create_or_update_file(self, owner: str, repo: str, path: str, content: str, message: str,
                               branch: str | None = None) -> dict:
        err = self._require_repo(owner, repo)
        if err:
            return err
        self._files.setdefault(self._full(owner, repo), {})[path] = content
        return {"path": path, "branch": branch or self._repos[self._full(owner, repo)]["default_branch"],
                "message": message}

    def delete_file(self, owner: str, repo: str, path: str, message: str, branch: str | None = None) -> dict:
        err = self._require_repo(owner, repo)
        if err:
            return err
        files = self._files.get(self._full(owner, repo), {})
        if path not in files:
            return {"error": f"no such path {path!r} in {owner}/{repo}"}
        del files[path]
        return {"path": path, "deleted": True, "message": message}

    def push_files(self, owner: str, repo: str, branch: str, files: dict, message: str) -> dict:
        err = self._require_repo(owner, repo)
        if err:
            return err
        full = self._full(owner, repo)
        if branch not in self._branches.get(full, {}):
            return {"error": f"no such branch {branch!r} in {owner}/{repo}"}
        self._files.setdefault(full, {}).update(files)
        return {"branch": branch, "files_changed": sorted(files), "message": message}

    def create_branch(self, owner: str, repo: str, branch: str, from_branch: str | None = None) -> dict:
        err = self._require_repo(owner, repo)
        if err:
            return err
        full = self._full(owner, repo)
        branches = self._branches.setdefault(full, {})
        if branch in branches:
            return {"error": f"branch {branch!r} already exists in {owner}/{repo}"}
        source = from_branch or self._repos[full]["default_branch"]
        if source not in branches:
            return {"error": f"no such source branch {source!r} in {owner}/{repo}"}
        branches[branch] = branches[source]
        return {"branch": branch, "from": source, "sha": branches[branch]}

    def list_branches(self, owner: str, repo: str) -> dict:
        err = self._require_repo(owner, repo)
        if err:
            return err
        return {"branches": sorted(self._branches.get(self._full(owner, repo), {}))}

    def get_commit(self, owner: str, repo: str, sha: str) -> dict:
        commit = self._commits.get(self._full(owner, repo), {}).get(sha)
        if commit is None:
            return {"error": f"no such commit {sha!r} in {owner}/{repo}"}
        return {"sha": sha, **commit}

    def list_commits(self, owner: str, repo: str, branch: str | None = None) -> dict:
        err = self._require_repo(owner, repo)
        if err:
            return err
        commits = self._commits.get(self._full(owner, repo), {})
        return {"commits": [{"sha": sha, **c} for sha, c in commits.items()]}

    def search_commits(self, owner: str, repo: str, query: str) -> dict:
        err = self._require_repo(owner, repo)
        if err:
            return err
        q = (query or "").lower()
        commits = self._commits.get(self._full(owner, repo), {})
        return {"commits": [{"sha": sha, **c} for sha, c in commits.items() if q in c.get("message", "").lower()]}

    def search_code(self, query: str) -> dict:
        q = (query or "").lower()
        matches = []
        for full, files in self._files.items():
            for path, content in files.items():
                if q in content.lower() or q in path.lower():
                    matches.append({"repo": full, "path": path})
        return {"matches": matches}

    def get_tag(self, owner: str, repo: str, tag: str) -> dict:
        sha = self._tags.get(self._full(owner, repo), {}).get(tag)
        if sha is None:
            return {"error": f"no such tag {tag!r} in {owner}/{repo}"}
        return {"tag": tag, "sha": sha}

    def list_tags(self, owner: str, repo: str) -> dict:
        err = self._require_repo(owner, repo)
        if err:
            return err
        return {"tags": sorted(self._tags.get(self._full(owner, repo), {}))}

    def get_latest_release(self, owner: str, repo: str) -> dict:
        releases = self._releases.get(self._full(owner, repo), {})
        if not releases:
            return {"error": f"no releases in {owner}/{repo}"}
        tag = max(releases)   # simplified: lexicographic "latest", no semver parsing
        return {"tag": tag, **releases[tag]}

    def get_release_by_tag(self, owner: str, repo: str, tag: str) -> dict:
        release = self._releases.get(self._full(owner, repo), {}).get(tag)
        if release is None:
            return {"error": f"no such release {tag!r} in {owner}/{repo}"}
        return {"tag": tag, **release}

    def list_releases(self, owner: str, repo: str) -> dict:
        err = self._require_repo(owner, repo)
        if err:
            return err
        return {"releases": [{"tag": t, **r} for t, r in sorted(self._releases.get(self._full(owner, repo), {}).items())]}

    def list_repository_collaborators(self, owner: str, repo: str) -> dict:
        err = self._require_repo(owner, repo)
        if err:
            return err
        return {"collaborators": dict(self._repos[self._full(owner, repo)]["collaborators"])}

    # ── summary ──────────────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "n_calls": len(self.call_log),
            "repo_count": len(self._repos),
            "issue_count": len(self._issues),
            "open_issue_count": sum(1 for i in self._issues.values() if i["state"] == "open"),
            "pull_request_count": len(self._pull_requests),
            "open_pull_request_count": sum(1 for p in self._pull_requests.values() if p["state"] == "open"),
            "merged_pull_request_count": sum(1 for p in self._pull_requests.values() if p["merged"]),
            "gist_count": len(self._gists),
            "discussion_count": len(self._discussions),
            "project_count": len(self._projects),
            "unread_notification_count": sum(1 for n in self._notifications.values() if n["unread"]),
            "issues": {f"{fn}#{n}": {"state": i["state"], "labels": i["labels"], "assignees": i["assignees"]}
                       for (fn, n), i in sorted(self._issues.items())},
            "pull_requests": {f"{fn}#{n}": {"state": p["state"], "merged": p["merged"], "draft": p["draft"]}
                               for (fn, n), p in sorted(self._pull_requests.items())},
            "labels": {fn: dict(labels) for fn, labels in sorted(self._labels.items()) if labels},
            "branches": {fn: sorted(b) for fn, b in sorted(self._branches.items())},
            "notifications": {nid: {"unread": n["unread"]} for nid, n in sorted(self._notifications.items())},
            "files": {fn: dict(files) for fn, files in sorted(self._files.items()) if files},
        }


class PackageRegistryService(MockService):
    """A mock npm-style registry plus a single local project - all 38
    tools the real `npm-mcp` (mikusnuz/npm-mcp) reference implementation
    registers, extracted directly from its source this session
    (`DEV_NOTES/TOOL_CATALOG_COMPLETE.md` §8 cited a lower "32 tools"
    figure taken from its README; the actual `server.tool()` call count
    is 38 - the same "the real count runs higher than the survey
    estimate" pattern already hit once for the whole catalog and again
    for kubernetes).

    Single-project design, the same "one thing at a time" scope
    `git_repo`/`filesystem` use (not multi-repo like `kubernetes`/
    `forge`) - npm itself always operates against one project directory.
    A package is only INSTALLABLE if the mock registry already has at
    least one published version of it - installing something never
    published is a real 404, the same "must exist before you can act on
    it" precondition `create_container`'s image check and
    `create_pull_request`'s branch check already enforce elsewhere.
    `ci` refuses without a lockfile present (matches real `npm ci`);
    `publish` refuses a version that's already published (matches real
    npm's hard immutable-version rule - you cannot overwrite a published
    version, only deprecate or unpublish it); `run_script`/`explain`/
    `uninstall`/`unpublish`/`deprecate`/`owner`/`dist-tag`/`view`/`bugs`/
    `repo`/`docs` all refuse a script/package/version that doesn't exist
    rather than silently no-op'ing.
    """

    TOOLS: dict[str, str] = {name: name for name in (
        "publish", "version", "view", "search", "unpublish", "deprecate",
        "owner", "pack", "whoami", "init", "audit", "outdated", "ls",
        "install", "uninstall", "update", "access", "token", "ping", "bugs",
        "repo", "docs", "diff", "pkg", "fund", "dedupe", "explain", "sbom",
        "profile", "ci", "doctor", "cache", "config", "prune", "link", "query",
    )}
    TOOLS["dist-tag"] = "dist_tag"
    TOOLS["run-script"] = "run_script"

    def __init__(self) -> None:
        super().__init__()
        self._current_user = "npm-agent"
        self._registry: dict[str, dict] = {}
        self._vulnerabilities: dict[str, dict[str, dict]] = {}
        self._project: dict | None = None
        self._installed: dict[str, dict] = {}
        self._lockfile_present = False
        self._linked: set[str] = set()
        self._tokens: dict[str, dict] = {}
        self._config: dict[str, str] = {"registry": "https://registry.npmjs.org/"}
        self._profile: dict[str, str] = {"email": "agent@example.invalid"}
        self._next_token_id = 1

    # ── seeding ──────────────────────────────────────────────────────────

    def seed(self, spec: dict) -> None:
        """``spec`` keys, all optional:
        - ``registry`` ({name: {versions: [version, ...] or
          {version: {published_by, deprecated, files}}, owners: [...],
          dist_tags: {tag: version}, access, bugs_url, repo_url,
          docs_url, funding}}): pre-existing published packages.
        - ``vulnerabilities`` ({name: {version: {severity, advisory}}}):
          seeded `audit` findings.
        - ``project`` ({name, version, dependencies, devDependencies,
          scripts}): the current project's package.json - a project
          exists once this key is given (even as ``{}``); omitted means
          no package.json yet, so most tools refuse until `init` is called.
        - ``installed`` ({name: {version, dev}}): pre-existing installed
          packages (need not match a registry version - e.g. installed
          from a tarball).
        - ``lockfile_present`` (bool): whether a lockfile exists, for `ci`.
        """
        for name, cfg in (spec.get("registry") or {}).items():
            versions_spec = cfg.get("versions", [])
            if isinstance(versions_spec, list):
                versions = {v: {"published_by": self._current_user, "deprecated": None, "files": []}
                            for v in versions_spec}
            else:
                versions = {v: {"published_by": vc.get("published_by", self._current_user),
                                 "deprecated": vc.get("deprecated"), "files": list(vc.get("files", []))}
                            for v, vc in versions_spec.items()}
            self._registry[name] = {
                "versions": versions, "owners": set(cfg.get("owners", [self._current_user])),
                "dist_tags": {}, "access": cfg.get("access", "public"),
                "bugs_url": cfg.get("bugs_url"), "repo_url": cfg.get("repo_url"),
                "docs_url": cfg.get("docs_url"), "funding": cfg.get("funding"),
            }
            default_tags = dict(cfg.get("dist_tags", {}))
            latest = self._latest_version(name)
            if latest and "latest" not in default_tags:
                default_tags["latest"] = latest
            self._registry[name]["dist_tags"] = default_tags
        for name, vulns in (spec.get("vulnerabilities") or {}).items():
            self._vulnerabilities[name] = dict(vulns)
        if "project" in spec:
            p = spec["project"] or {}
            self._project = {
                "name": p.get("name", "my-project"), "version": p.get("version", "1.0.0"),
                "dependencies": dict(p.get("dependencies", {})),
                "devDependencies": dict(p.get("devDependencies", {})),
                "scripts": dict(p.get("scripts", {})),
            }
        for name, info in (spec.get("installed") or {}).items():
            self._installed[name] = {"version": info["version"], "dev": info.get("dev", False)}
        self._lockfile_present = bool(spec.get("lockfile_present", False))

    # ── value normalization ─────────────────────────────────────────────

    def dispatch(self, tool_name: str, arguments: dict) -> Any:
        """``pkg``'s ``value`` field is deliberately untyped in its schema
        (a package.json field can hold a string, bool, number, object, or
        array) - live-verified: a model reasonably represented a boolean
        as the string ``"true"`` the same way real npm's CLI-style
        `pkg set field=value` takes it, not as a literal JSON boolean.
        Normalized here, once, before the base class logs/dispatches -
        same reasoning as `FilesystemService`'s trailing-slash
        normalization: the call log records what's passed to dispatch,
        not what the method does internally with it."""
        if tool_name == "pkg" and isinstance((arguments or {}).get("value"), str):
            lowered = arguments["value"].lower()
            if lowered in ("true", "false"):
                arguments = dict(arguments)
                arguments["value"] = lowered == "true"
        return super().dispatch(tool_name, arguments)

    # ── internal helpers ────────────────────────────────────────────────

    @staticmethod
    def _version_key(v: str) -> tuple:
        parts = []
        for p in (v or "0").split("."):
            try:
                parts.append(int(p))
            except ValueError:
                parts.append(0)
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts[:3])

    def _latest_version(self, name: str) -> str | None:
        versions = self._registry.get(name, {}).get("versions", {})
        if not versions:
            return None
        return sorted(versions, key=self._version_key)[-1]

    def _require_project(self) -> dict | None:
        if self._project is None:
            return {"error": "no package.json in this project - run init first"}
        return None

    # ── project setup ────────────────────────────────────────────────────

    def init(self, name: str = "my-project", version: str = "1.0.0") -> dict:
        if self._project is not None:
            return {"error": "package.json already exists"}
        self._project = {"name": name, "version": version, "dependencies": {},
                          "devDependencies": {}, "scripts": {}}
        return dict(self._project)

    def pkg(self, operation: str, field: str | None = None, value=None) -> dict:
        err = self._require_project()
        if err:
            return err
        if operation == "get":
            return dict(self._project) if field is None else {field: self._project.get(field)}
        if operation == "set":
            if field is None:
                return {"error": "field is required for set"}
            self._project[field] = value
            return {field: value}
        if operation == "delete":
            if field is None:
                return {"error": "field is required for delete"}
            self._project.pop(field, None)
            return {"deleted": field}
        return {"error": f"unknown operation {operation!r} (expected get, set, or delete)"}

    def ci(self) -> dict:
        err = self._require_project()
        if err:
            return err
        if not self._lockfile_present:
            return {"error": "no lockfile present - ci requires an existing lockfile (use install instead)"}
        installed = []
        for name, ver in {**self._project["dependencies"], **self._project["devDependencies"]}.items():
            self._installed[name] = {"version": ver, "dev": name in self._project["devDependencies"]}
            installed.append({"name": name, "version": ver})
        return {"installed": installed}

    def link(self, package: str) -> dict:
        err = self._require_project()
        if err:
            return err
        self._linked.add(package)
        return {"name": package, "linked": True}

    # ── dependency management ───────────────────────────────────────────

    def install(self, packages, dev: bool = False) -> dict:
        err = self._require_project()
        if err:
            return err
        installed, missing = [], []
        for spec in self._as_list(packages):
            name, _, ver = spec.partition("@")
            ver = ver or self._latest_version(name)
            if name not in self._registry or ver is None or ver not in self._registry[name]["versions"]:
                missing.append(spec)
                continue
            self._installed[name] = {"version": ver, "dev": dev}
            (self._project["devDependencies"] if dev else self._project["dependencies"])[name] = ver
            installed.append({"name": name, "version": ver})
        result = {"installed": installed}
        if missing:
            result["error"] = f"not found in registry: {missing}"
        return result

    def uninstall(self, packages) -> dict:
        err = self._require_project()
        if err:
            return err
        removed, missing = [], []
        for name in self._as_list(packages):
            if name not in self._installed:
                missing.append(name)
                continue
            del self._installed[name]
            self._project["dependencies"].pop(name, None)
            self._project["devDependencies"].pop(name, None)
            removed.append(name)
        result = {"removed": removed}
        if missing:
            result["error"] = f"not installed: {missing}"
        return result

    def update(self, packages=None) -> dict:
        err = self._require_project()
        if err:
            return err
        names = self._as_list(packages) or list(self._installed)
        updated = []
        for name in names:
            if name not in self._installed:
                continue
            latest = self._latest_version(name)
            if latest and latest != self._installed[name]["version"]:
                self._installed[name]["version"] = latest
                dev = self._installed[name]["dev"]
                (self._project["devDependencies"] if dev else self._project["dependencies"])[name] = latest
                updated.append({"name": name, "version": latest})
        return {"updated": updated}

    def ls(self) -> dict:
        return {"installed": {n: dict(v) for n, v in sorted(self._installed.items())}}

    def outdated(self) -> dict:
        out = []
        for name, info in self._installed.items():
            latest = self._latest_version(name)
            if latest and latest != info["version"]:
                out.append({"name": name, "current": info["version"], "latest": latest})
        return {"outdated": out}

    def prune(self) -> dict:
        err = self._require_project()
        if err:
            return err
        declared = set(self._project["dependencies"]) | set(self._project["devDependencies"])
        orphans = sorted(n for n in self._installed if n not in declared)
        for n in orphans:
            del self._installed[n]
        return {"removed": orphans}

    def dedupe(self) -> dict:
        err = self._require_project()
        if err:
            return err
        return {"deduped": 0}

    def fund(self) -> dict:
        return {"funding": [{"name": n} for n in sorted(self._installed) if self._registry.get(n, {}).get("funding")]}

    def explain(self, package: str) -> dict:
        if package not in self._installed:
            return {"error": f"{package!r} is not installed"}
        err = self._require_project()
        if err:
            return err
        direct = package in self._project["dependencies"] or package in self._project["devDependencies"]
        return {"name": package, "direct_dependency": direct}

    def sbom(self, format: str = "cyclonedx") -> dict:
        return {"format": format, "components": [{"name": n, "version": v["version"]}
                                                   for n, v in sorted(self._installed.items())]}

    def query(self, selector: str) -> dict:
        s = (selector or "").lower()
        return {"matches": [n for n in sorted(self._installed) if s in n.lower()]}

    def run_script(self, name: str) -> dict:
        err = self._require_project()
        if err:
            return err
        script = self._project["scripts"].get(name)
        if script is None:
            return {"error": f"no such script {name!r} in package.json"}
        return {"name": name, "command": script, "output": f"<mock output of: {script}>"}

    # ── security & diagnostics ──────────────────────────────────────────

    def audit(self, fix: bool = False) -> dict:
        err = self._require_project()
        if err:
            return err
        findings, fixed = [], []
        for name, info in list(self._installed.items()):
            vuln = self._vulnerabilities.get(name, {}).get(info["version"])
            if not vuln:
                continue
            if fix:
                latest = self._latest_version(name)
                if latest and latest not in self._vulnerabilities.get(name, {}):
                    info["version"] = latest
                    deps = self._project["devDependencies"] if info["dev"] else self._project["dependencies"]
                    deps[name] = latest
                    fixed.append({"name": name, "fixed_to": latest})
                    continue
            findings.append({"name": name, "version": info["version"], **vuln})
        result = {"vulnerabilities": findings}
        if fix:
            result["fixed"] = fixed
        return result

    def doctor(self) -> dict:
        return {"registry_reachable": True, "npm_version": "10.9.0", "node_version": "22.10.0", "issues": []}

    def ping(self) -> dict:
        return {"connected": True, "registry": self._config.get("registry")}

    # ── configuration & auth ─────────────────────────────────────────────

    def whoami(self) -> dict:
        return {"username": self._current_user}

    def token(self, operation: str, token_id: str | None = None) -> dict:
        if operation == "list":
            return {"tokens": sorted(self._tokens)}
        if operation == "create":
            token_id = f"tok{self._next_token_id}"
            self._next_token_id += 1
            self._tokens[token_id] = {"created_by": self._current_user}
            return {"id": token_id}
        if operation == "revoke":
            if token_id not in self._tokens:
                return {"error": f"no such token {token_id!r}"}
            del self._tokens[token_id]
            return {"id": token_id, "revoked": True}
        return {"error": f"unknown operation {operation!r} (expected list, create, or revoke)"}

    def access(self, operation: str, package: str, level: str | None = None) -> dict:
        if package not in self._registry:
            return {"error": f"no such package {package!r}"}
        if operation == "get":
            return {"package": package, "access": self._registry[package]["access"]}
        if operation == "set":
            if level not in ("public", "restricted"):
                return {"error": f"unknown access level {level!r} (expected public or restricted)"}
            self._registry[package]["access"] = level
            return {"package": package, "access": level}
        return {"error": f"unknown operation {operation!r} (expected get or set)"}

    def owner(self, operation: str, package: str, user: str | None = None) -> dict:
        if package not in self._registry:
            return {"error": f"no such package {package!r}"}
        owners = self._registry[package]["owners"]
        if operation == "ls":
            return {"package": package, "owners": sorted(owners)}
        if operation == "add":
            owners.add(user)
            return {"package": package, "owners": sorted(owners)}
        if operation == "rm":
            owners.discard(user)
            return {"package": package, "owners": sorted(owners)}
        return {"error": f"unknown operation {operation!r} (expected ls, add, or rm)"}

    def dist_tag(self, operation: str, package: str, tag: str | None = None, version: str | None = None) -> dict:
        if package not in self._registry:
            return {"error": f"no such package {package!r}"}
        tags = self._registry[package]["dist_tags"]
        if operation == "ls":
            return {"package": package, "dist_tags": dict(tags)}
        if operation == "add":
            if version not in self._registry[package]["versions"]:
                return {"error": f"no such version {version!r} of {package!r}"}
            tags[tag] = version
            return {"package": package, "tag": tag, "version": version}
        if operation == "rm":
            tags.pop(tag, None)
            return {"package": package, "tag": tag, "removed": True}
        return {"error": f"unknown operation {operation!r} (expected ls, add, or rm)"}

    def profile(self, operation: str = "view", field: str | None = None, value: str | None = None) -> dict:
        if operation == "view":
            return dict(self._profile)
        if operation == "set":
            if field is None:
                return {"error": "field is required for set"}
            self._profile[field] = value
            return {field: value}
        return {"error": f"unknown operation {operation!r} (expected view or set)"}

    def config(self, operation: str, key: str | None = None, value: str | None = None) -> dict:
        if operation == "get":
            if key is None:
                return dict(self._config)
            if key not in self._config:
                return {"error": f"no such config key {key!r}"}
            return {key: self._config[key]}
        if operation == "set":
            if key is None:
                return {"error": "key is required for set"}
            self._config[key] = value
            return {key: value}
        if operation == "delete":
            if key is None or key not in self._config:
                return {"error": f"no such config key {key!r}"}
            del self._config[key]
            return {"deleted": key}
        return {"error": f"unknown operation {operation!r} (expected get, set, or delete)"}

    def cache(self, operation: str = "verify") -> dict:
        if operation not in ("verify", "clean", "ls"):
            return {"error": f"unknown operation {operation!r} (expected verify, clean, or ls)"}
        if operation == "ls":
            return {"entries": sorted(self._installed)}
        return {"operation": operation, "ok": True}

    # ── publishing & versioning ──────────────────────────────────────────

    def publish(self, name: str, version: str, files: list | None = None) -> dict:
        entry = self._registry.setdefault(name, {
            "versions": {}, "owners": {self._current_user}, "dist_tags": {}, "access": "public",
            "bugs_url": None, "repo_url": None, "docs_url": None, "funding": None,
        })
        if version in entry["versions"]:
            return {"error": f"cannot publish over already-published version {name}@{version}"}
        entry["versions"][version] = {"published_by": self._current_user, "deprecated": None,
                                       "files": self._as_list(files)}
        entry["dist_tags"]["latest"] = version
        return {"name": name, "version": version, "published": True}

    def unpublish(self, package: str, version: str) -> dict:
        entry = self._registry.get(package)
        if entry is None or version not in entry["versions"]:
            return {"error": f"no such version {version!r} of {package!r}"}
        del entry["versions"][version]
        return {"package": package, "version": version, "unpublished": True}

    def deprecate(self, package: str, version: str, message: str | None = None, undo: bool = False) -> dict:
        entry = self._registry.get(package)
        if entry is None or version not in entry["versions"]:
            return {"error": f"no such version {version!r} of {package!r}"}
        entry["versions"][version]["deprecated"] = None if undo else (message or "deprecated")
        return {"package": package, "version": version, "deprecated": entry["versions"][version]["deprecated"]}

    def version(self, bump: str = "patch") -> dict:
        err = self._require_project()
        if err:
            return err
        if bump not in ("patch", "minor", "major"):
            return {"error": f"unknown bump {bump!r} (expected patch, minor, or major)"}
        major, minor, patch = self._version_key(self._project["version"])
        if bump == "major":
            major, minor, patch = major + 1, 0, 0
        elif bump == "minor":
            minor, patch = minor + 1, 0
        else:
            patch += 1
        new_version = f"{major}.{minor}.{patch}"
        self._project["version"] = new_version
        return {"version": new_version}

    def pack(self) -> dict:
        err = self._require_project()
        if err:
            return err
        return {"files": ["package.json", "index.js", "README.md"]}

    # ── package info ─────────────────────────────────────────────────────

    def view(self, package: str, field: str | None = None) -> dict:
        entry = self._registry.get(package)
        if entry is None:
            return {"error": f"no such package {package!r}"}
        info = {"name": package, "version": self._latest_version(package),
                "versions": sorted(entry["versions"]), "access": entry["access"]}
        return info if field is None else {field: info.get(field)}

    def search(self, query: str) -> dict:
        q = (query or "").lower()
        return {"packages": sorted(n for n in self._registry if q in n.lower())}

    def bugs(self, package: str) -> dict:
        entry = self._registry.get(package)
        if entry is None:
            return {"error": f"no such package {package!r}"}
        return {"package": package, "url": entry.get("bugs_url")}

    def repo(self, package: str) -> dict:
        entry = self._registry.get(package)
        if entry is None:
            return {"error": f"no such package {package!r}"}
        return {"package": package, "url": entry.get("repo_url")}

    def docs(self, package: str) -> dict:
        entry = self._registry.get(package)
        if entry is None:
            return {"error": f"no such package {package!r}"}
        return {"package": package, "url": entry.get("docs_url")}

    def diff(self, package: str, from_version: str, to_version: str) -> dict:
        entry = self._registry.get(package)
        if entry is None or from_version not in entry["versions"] or to_version not in entry["versions"]:
            return {"error": f"no such version pair for {package!r}"}
        from_files = set(entry["versions"][from_version]["files"])
        to_files = set(entry["versions"][to_version]["files"])
        return {"package": package, "added": sorted(to_files - from_files),
                "removed": sorted(from_files - to_files)}

    # ── summary ──────────────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "n_calls": len(self.call_log),
            "has_project": self._project is not None,
            "project_version": self._project["version"] if self._project else None,
            "installed_count": len(self._installed),
            "installed": {n: dict(v) for n, v in sorted(self._installed.items())},
            "dependencies": dict(self._project["dependencies"]) if self._project else {},
            "dev_dependencies": dict(self._project["devDependencies"]) if self._project else {},
            "scripts": dict(self._project["scripts"]) if self._project else {},
            "registry_package_count": len(self._registry),
            "published_versions": {n: sorted(e["versions"]) for n, e in sorted(self._registry.items())},
            "linked": sorted(self._linked),
            "token_count": len(self._tokens),
        }


class TerraformService(MockService):
    """A mock Terraform Cloud/Enterprise plus public registry - all 55
    tools the official `hashicorp/terraform-mcp-server` registers,
    extracted directly from its source this session (`server.tool()`/
    `AddTool()` registrations, not the README) - `TOOL_CATALOG_COMPLETE.md`
    §6 cited an archived third-party server's "~10 tools" for this
    category; the real official server is over 5x that, the same "the
    real count runs higher than the survey estimate" pattern already hit
    for the whole catalog, kubernetes, forge, and package_registry.

    Two toolsets, matching the real server's own split: 9 read-only
    **registry** tools (search/get-details for public providers, modules,
    and Sentinel policies - no auth, no state) and 46 **TFE** tools
    (Terraform Cloud/Enterprise's own API surface - organizations,
    projects, workspaces, runs/plans/applies, state versions, teams,
    variable sets, policy sets, stacks, private registry). The TFE half
    is where the real workflow discipline lives: `create_run` refuses a
    locked workspace; `action_run("apply")` refuses a run that isn't in a
    plannable-to-apply state (can't apply a run twice, can't apply one
    that was discarded); `delete_workspace_safely` refuses a locked
    workspace; `force_unlock_workspace` refuses one that isn't locked -
    the same "respect the precondition, don't force past it" skill
    `git_repo`/`docker`/`forge` already test, here specifically over
    infrastructure-changing operations where forcing past a lock is a
    real, high-consequence mistake.
    """

    TOOLS = {name: name for name in (
        "search_providers", "get_provider_details", "get_latest_provider_version",
        "get_provider_capabilities", "search_modules", "get_module_details",
        "get_latest_module_version", "search_policies", "get_policy_details",
        "list_terraform_orgs", "list_terraform_projects", "create_project", "delete_project",
        "list_teams", "create_team", "get_token_permissions",
        "list_workspaces", "get_workspace_details", "create_workspace", "update_workspace",
        "delete_workspace_safely", "force_unlock_workspace", "create_workspace_tags", "read_workspace_tags",
        "list_workspace_variables", "create_workspace_variable", "update_workspace_variable",
        "list_variable_sets", "create_variable_set", "create_variable_in_variable_set",
        "delete_variable_in_variable_set", "attach_variable_set_to_workspaces", "detach_variable_set_from_workspaces",
        "list_workspace_policy_sets", "attach_policy_set_to_workspaces",
        "create_run", "list_runs", "get_run_details", "get_run_comments", "action_run",
        "get_plan_details", "get_plan_json_output", "get_plan_logs",
        "get_apply_details", "get_apply_logs",
        "list_state_versions", "get_state_version",
        "list_stacks", "get_stack_details",
        "create_no_code_workspace", "get_sentinel_mock",
        "search_private_modules", "get_private_module_details",
        "search_private_providers", "get_private_provider_details",
    )}

    def __init__(self) -> None:
        super().__init__()
        self._orgs: set[str] = set()
        self._projects: dict[tuple[str, str], dict] = {}
        self._teams: dict[tuple[str, str], dict] = {}
        self._workspaces: dict[tuple[str, str], dict] = {}
        self._workspace_variables: dict[tuple[str, str], dict[str, dict]] = {}
        self._variable_sets: dict[tuple[str, str], dict] = {}
        self._policy_sets: dict[tuple[str, str], dict] = {}
        self._runs: dict[str, dict] = {}
        self._plans: dict[str, dict] = {}
        self._applies: dict[str, dict] = {}
        self._state_versions: dict[tuple[str, str], list[dict]] = {}
        self._stacks: dict[tuple[str, str], dict] = {}
        self._sentinel_mocks: dict[str, dict] = {}
        self._tokens: dict[str, dict] = {}
        self._providers: dict[str, dict] = {}
        self._modules: dict[str, dict] = {}
        self._policies: dict[str, dict] = {}
        self._private_modules: dict[tuple[str, str], dict] = {}
        self._private_providers: dict[tuple[str, str], dict] = {}
        self._next_id = 1

    # ── seeding ──────────────────────────────────────────────────────────

    def seed(self, spec: dict) -> None:
        """``spec`` keys, all optional:
        - ``orgs`` ([name, ...]).
        - ``projects``/``teams`` ([{org, id, name}, ...]).
        - ``workspaces`` ([{org, id, name, project_id, locked, tags,
          terraform_version, variables: [{id, key, value, category,
          sensitive}, ...]}, ...]).
        - ``variable_sets`` ([{org, id, name, variables: {key: value},
          workspaces: [workspace_id, ...]}, ...]).
        - ``policy_sets`` ([{org, id, name, workspaces: [workspace_id,
          ...]}, ...]).
        - ``runs`` ([{org, id, workspace_id, status, message, plan_id,
          apply_id, comments}, ...]) / ``plans`` ([{id, run_id, status,
          log, json_output}, ...]) / ``applies`` ([{id, run_id, status,
          log}, ...]).
        - ``state_versions`` ([{org, workspace_id, serial, resources},
          ...]).
        - ``stacks`` ([{org, id, name}, ...]).
        - ``sentinel_mocks`` ([{id, data}, ...]).
        - ``tokens`` ([{token, permissions}, ...]).
        - ``providers`` ({"namespace/name": {latest_version, capabilities,
          docs}}) / ``modules`` ({"namespace/name/provider":
          {latest_version, docs}}) / ``policies`` ({"namespace/name":
          {docs}}) - the public registry.
        - ``private_modules``/``private_providers`` ([{org, key, docs},
          ...]) - the org-scoped private registry.
        """
        for org in spec.get("orgs") or []:
            self._orgs.add(org)
        for entry in spec.get("projects") or []:
            self._orgs.add(entry["org"])
            self._projects[(entry["org"], entry["id"])] = {"name": entry["name"]}
        for entry in spec.get("teams") or []:
            self._orgs.add(entry["org"])
            self._teams[(entry["org"], entry["id"])] = {"name": entry["name"]}
        for entry in spec.get("workspaces") or []:
            self._orgs.add(entry["org"])
            key = (entry["org"], entry["id"])
            self._workspaces[key] = {
                "name": entry["name"], "project_id": entry.get("project_id"),
                "locked": entry.get("locked", False), "tags": set(entry.get("tags", [])),
                "terraform_version": entry.get("terraform_version", "1.9.0"),
            }
            for var in entry.get("variables", []):
                var_id = var.get("id") or self._new_id("var")
                self._workspace_variables.setdefault(key, {})[var_id] = {
                    "key": var["key"], "value": var["value"],
                    "category": var.get("category", "terraform"), "sensitive": var.get("sensitive", False),
                }
        for entry in spec.get("variable_sets") or []:
            self._orgs.add(entry["org"])
            self._variable_sets[(entry["org"], entry["id"])] = {
                "name": entry["name"], "variables": dict(entry.get("variables", {})),
                "workspaces": set(entry.get("workspaces", [])),
            }
        for entry in spec.get("policy_sets") or []:
            self._orgs.add(entry["org"])
            self._policy_sets[(entry["org"], entry["id"])] = {
                "name": entry["name"], "workspaces": set(entry.get("workspaces", [])),
            }
        for entry in spec.get("runs") or []:
            self._orgs.add(entry["org"])
            self._runs[entry["id"]] = {
                "org": entry["org"], "workspace_id": entry["workspace_id"],
                "status": entry.get("status", "planned"), "message": entry.get("message", ""),
                "plan_id": entry.get("plan_id"), "apply_id": entry.get("apply_id"),
                "comments": list(entry.get("comments", [])),
            }
        for entry in spec.get("plans") or []:
            self._plans[entry["id"]] = {
                "run_id": entry.get("run_id"), "status": entry.get("status", "finished"),
                "log": list(entry.get("log", [])), "json_output": entry.get("json_output", {}),
            }
        for entry in spec.get("applies") or []:
            self._applies[entry["id"]] = {
                "run_id": entry.get("run_id"), "status": entry.get("status", "finished"),
                "log": list(entry.get("log", [])),
            }
        for entry in spec.get("state_versions") or []:
            self._orgs.add(entry["org"])
            key = (entry["org"], entry["workspace_id"])
            self._state_versions.setdefault(key, []).append(
                {"serial": entry.get("serial", len(self._state_versions.get(key, [])) + 1),
                 "resources": list(entry.get("resources", []))})
        for entry in spec.get("stacks") or []:
            self._orgs.add(entry["org"])
            self._stacks[(entry["org"], entry["id"])] = {"name": entry["name"]}
        for entry in spec.get("sentinel_mocks") or []:
            self._sentinel_mocks[entry["id"]] = {"data": entry.get("data", {})}
        for entry in spec.get("tokens") or []:
            self._tokens[entry["token"]] = {"permissions": entry.get("permissions", {})}
        for key, cfg in (spec.get("providers") or {}).items():
            self._providers[key] = {"latest_version": cfg.get("latest_version", "1.0.0"),
                                     "capabilities": list(cfg.get("capabilities", [])),
                                     "docs": cfg.get("docs", "")}
        for key, cfg in (spec.get("modules") or {}).items():
            self._modules[key] = {"latest_version": cfg.get("latest_version", "1.0.0"), "docs": cfg.get("docs", "")}
        for key, cfg in (spec.get("policies") or {}).items():
            self._policies[key] = {"docs": cfg.get("docs", "")}
        for entry in spec.get("private_modules") or []:
            self._orgs.add(entry["org"])
            self._private_modules[(entry["org"], entry["key"])] = {"docs": entry.get("docs", "")}
        for entry in spec.get("private_providers") or []:
            self._orgs.add(entry["org"])
            self._private_providers[(entry["org"], entry["key"])] = {"docs": entry.get("docs", "")}

    # ── internal helpers ────────────────────────────────────────────────

    def _new_id(self, prefix: str) -> str:
        result = f"{prefix}-{self._next_id}"
        self._next_id += 1
        return result

    def _require_org(self, org: str) -> dict | None:
        if org not in self._orgs:
            return {"error": f"no such organization {org!r}"}
        return None

    def _require_workspace(self, org: str, workspace_id: str) -> dict | None:
        if (org, workspace_id) not in self._workspaces:
            return {"error": f"no such workspace {workspace_id!r} in org {org!r}"}
        return None

    @staticmethod
    def _matches_query(query: str, target: str) -> bool:
        """Real registry search is tokenized full-text search, not a single
        whole-string substring check - live-verified: a model reasonably
        searched with a natural multi-word phrase ("tag enforcement",
        "internal platform") that was never going to substring-match a
        hyphenated key like "hashicorp/require-tags" as one literal string.
        Matching if ANY word of the query appears in the target is closer
        to how a real search box behaves, and turns a search that silently
        (and unfairly) returned nothing into one that finds the obvious
        result."""
        words = (query or "").lower().split()
        if not words:
            return True
        target = target.lower()
        return any(w in target for w in words)

    def search_providers(self, query: str) -> dict:
        return {"providers": sorted(k for k in self._providers if self._matches_query(query, k))}

    def get_provider_details(self, namespace: str, name: str, version: str | None = None) -> dict:
        p = self._providers.get(f"{namespace}/{name}")
        if p is None:
            return {"error": f"no such provider {namespace}/{name}"}
        return {"namespace": namespace, "name": name, "version": version or p["latest_version"], "docs": p["docs"]}

    def get_latest_provider_version(self, namespace: str, name: str) -> dict:
        p = self._providers.get(f"{namespace}/{name}")
        if p is None:
            return {"error": f"no such provider {namespace}/{name}"}
        return {"namespace": namespace, "name": name, "version": p["latest_version"]}

    def get_provider_capabilities(self, namespace: str, name: str) -> dict:
        p = self._providers.get(f"{namespace}/{name}")
        if p is None:
            return {"error": f"no such provider {namespace}/{name}"}
        return {"namespace": namespace, "name": name, "capabilities": p["capabilities"]}

    def search_modules(self, query: str) -> dict:
        return {"modules": sorted(k for k in self._modules if self._matches_query(query, k))}

    def get_module_details(self, namespace: str, name: str, provider: str) -> dict:
        m = self._modules.get(f"{namespace}/{name}/{provider}")
        if m is None:
            return {"error": f"no such module {namespace}/{name}/{provider}"}
        return {"namespace": namespace, "name": name, "provider": provider, "version": m["latest_version"], "docs": m["docs"]}

    def get_latest_module_version(self, namespace: str, name: str, provider: str) -> dict:
        m = self._modules.get(f"{namespace}/{name}/{provider}")
        if m is None:
            return {"error": f"no such module {namespace}/{name}/{provider}"}
        return {"namespace": namespace, "name": name, "provider": provider, "version": m["latest_version"]}

    def search_policies(self, query: str) -> dict:
        return {"policies": sorted(k for k in self._policies if self._matches_query(query, k))}

    def get_policy_details(self, namespace: str, name: str) -> dict:
        p = self._policies.get(f"{namespace}/{name}")
        if p is None:
            return {"error": f"no such policy {namespace}/{name}"}
        return {"namespace": namespace, "name": name, "docs": p["docs"]}

    # ── orgs / projects / teams / tokens ────────────────────────────────

    def list_terraform_orgs(self) -> dict:
        return {"organizations": sorted(self._orgs)}

    def list_terraform_projects(self, org: str) -> dict:
        err = self._require_org(org)
        if err:
            return err
        return {"projects": [{"id": pid, **p} for (o, pid), p in sorted(self._projects.items()) if o == org]}

    def create_project(self, org: str, name: str) -> dict:
        err = self._require_org(org)
        if err:
            return err
        if any(o == org and p["name"] == name for (o, _), p in self._projects.items()):
            return {"error": f"project {name!r} already exists in org {org!r}"}
        project_id = self._new_id("prj")
        self._projects[(org, project_id)] = {"name": name}
        return {"id": project_id, "name": name}

    def delete_project(self, org: str, project_id: str) -> dict:
        key = (org, project_id)
        if key not in self._projects:
            return {"error": f"no such project {project_id!r} in org {org!r}"}
        in_use = [wid for (o, wid), w in self._workspaces.items() if o == org and w.get("project_id") == project_id]
        if in_use:
            return {"error": f"project {project_id!r} still has workspace(s) {in_use} - remove them first"}
        del self._projects[key]
        return {"id": project_id, "deleted": True}

    def list_teams(self, org: str) -> dict:
        err = self._require_org(org)
        if err:
            return err
        return {"teams": [{"id": tid, **t} for (o, tid), t in sorted(self._teams.items()) if o == org]}

    def create_team(self, org: str, name: str) -> dict:
        err = self._require_org(org)
        if err:
            return err
        if any(o == org and t["name"] == name for (o, _), t in self._teams.items()):
            return {"error": f"team {name!r} already exists in org {org!r}"}
        team_id = self._new_id("team")
        self._teams[(org, team_id)] = {"name": name}
        return {"id": team_id, "name": name}

    def get_token_permissions(self, token: str) -> dict:
        t = self._tokens.get(token)
        if t is None:
            return {"error": f"no such token {token!r}"}
        return {"token": token, "permissions": t["permissions"]}

    # ── workspaces ───────────────────────────────────────────────────────

    def list_workspaces(self, org: str) -> dict:
        err = self._require_org(org)
        if err:
            return err
        return {"workspaces": [{"id": wid, "name": w["name"], "locked": w["locked"]}
                                for (o, wid), w in sorted(self._workspaces.items()) if o == org]}

    def get_workspace_details(self, org: str, workspace_id: str) -> dict:
        err = self._require_workspace(org, workspace_id)
        if err:
            return err
        w = self._workspaces[(org, workspace_id)]
        return {"id": workspace_id, "name": w["name"], "project_id": w["project_id"], "locked": w["locked"],
                "tags": sorted(w["tags"]), "terraform_version": w["terraform_version"]}

    def create_workspace(self, org: str, name: str, project_id: str | None = None,
                          terraform_version: str | None = None) -> dict:
        err = self._require_org(org)
        if err:
            return err
        if project_id is not None and (org, project_id) not in self._projects:
            return {"error": f"no such project {project_id!r} in org {org!r}"}
        if any(o == org and w["name"] == name for (o, _), w in self._workspaces.items()):
            return {"error": f"workspace {name!r} already exists in org {org!r}"}
        workspace_id = self._new_id("ws")
        self._workspaces[(org, workspace_id)] = {"name": name, "project_id": project_id, "locked": False,
                                                   "tags": set(), "terraform_version": terraform_version or "1.9.0"}
        return {"id": workspace_id, "name": name}

    def update_workspace(self, org: str, workspace_id: str, name: str | None = None,
                          terraform_version: str | None = None) -> dict:
        err = self._require_workspace(org, workspace_id)
        if err:
            return err
        w = self._workspaces[(org, workspace_id)]
        if name is not None:
            w["name"] = name
        if terraform_version is not None:
            w["terraform_version"] = terraform_version
        return {"id": workspace_id, "name": w["name"], "terraform_version": w["terraform_version"]}

    def delete_workspace_safely(self, org: str, workspace_id: str) -> dict:
        err = self._require_workspace(org, workspace_id)
        if err:
            return err
        if self._workspaces[(org, workspace_id)]["locked"]:
            return {"error": f"workspace {workspace_id!r} is locked - unlock it first"}
        del self._workspaces[(org, workspace_id)]
        self._workspace_variables.pop((org, workspace_id), None)
        return {"id": workspace_id, "deleted": True}

    def force_unlock_workspace(self, org: str, workspace_id: str) -> dict:
        err = self._require_workspace(org, workspace_id)
        if err:
            return err
        if not self._workspaces[(org, workspace_id)]["locked"]:
            return {"error": f"workspace {workspace_id!r} is not locked"}
        self._workspaces[(org, workspace_id)]["locked"] = False
        return {"id": workspace_id, "locked": False}

    def create_workspace_tags(self, org: str, workspace_id: str, tags) -> dict:
        err = self._require_workspace(org, workspace_id)
        if err:
            return err
        self._workspaces[(org, workspace_id)]["tags"].update(self._as_list(tags))
        return {"id": workspace_id, "tags": sorted(self._workspaces[(org, workspace_id)]["tags"])}

    def read_workspace_tags(self, org: str, workspace_id: str) -> dict:
        err = self._require_workspace(org, workspace_id)
        if err:
            return err
        return {"id": workspace_id, "tags": sorted(self._workspaces[(org, workspace_id)]["tags"])}

    # ── workspace variables ──────────────────────────────────────────────

    def list_workspace_variables(self, org: str, workspace_id: str) -> dict:
        err = self._require_workspace(org, workspace_id)
        if err:
            return err
        variables = self._workspace_variables.get((org, workspace_id), {})
        return {"variables": [{"id": vid, **v} for vid, v in sorted(variables.items())]}

    def create_workspace_variable(self, org: str, workspace_id: str, key: str, value: str,
                                   category: str = "terraform", sensitive: bool = False) -> dict:
        err = self._require_workspace(org, workspace_id)
        if err:
            return err
        variables = self._workspace_variables.setdefault((org, workspace_id), {})
        if any(v["key"] == key for v in variables.values()):
            return {"error": f"variable {key!r} already exists on workspace {workspace_id!r}"}
        var_id = self._new_id("var")
        variables[var_id] = {"key": key, "value": value, "category": category, "sensitive": sensitive}
        return {"id": var_id, "key": key}

    def update_workspace_variable(self, org: str, workspace_id: str, variable_id: str,
                                   value: str | None = None, key: str | None = None) -> dict:
        err = self._require_workspace(org, workspace_id)
        if err:
            return err
        variables = self._workspace_variables.get((org, workspace_id), {})
        if variable_id not in variables:
            return {"error": f"no such variable {variable_id!r} on workspace {workspace_id!r}"}
        if value is not None:
            variables[variable_id]["value"] = value
        if key is not None:
            variables[variable_id]["key"] = key
        return {"id": variable_id, **variables[variable_id]}

    # ── variable sets ────────────────────────────────────────────────────

    def list_variable_sets(self, org: str) -> dict:
        err = self._require_org(org)
        if err:
            return err
        return {"variable_sets": [{"id": vsid, "name": vs["name"]}
                                   for (o, vsid), vs in sorted(self._variable_sets.items()) if o == org]}

    def create_variable_set(self, org: str, name: str) -> dict:
        err = self._require_org(org)
        if err:
            return err
        if any(o == org and vs["name"] == name for (o, _), vs in self._variable_sets.items()):
            return {"error": f"variable set {name!r} already exists in org {org!r}"}
        varset_id = self._new_id("varset")
        self._variable_sets[(org, varset_id)] = {"name": name, "variables": {}, "workspaces": set()}
        return {"id": varset_id, "name": name}

    def create_variable_in_variable_set(self, org: str, varset_id: str, key: str, value: str) -> dict:
        vs = self._variable_sets.get((org, varset_id))
        if vs is None:
            return {"error": f"no such variable set {varset_id!r} in org {org!r}"}
        if key in vs["variables"]:
            return {"error": f"variable {key!r} already exists in variable set {varset_id!r}"}
        vs["variables"][key] = value
        return {"id": varset_id, "key": key}

    def delete_variable_in_variable_set(self, org: str, varset_id: str, key: str) -> dict:
        vs = self._variable_sets.get((org, varset_id))
        if vs is None:
            return {"error": f"no such variable set {varset_id!r} in org {org!r}"}
        if key not in vs["variables"]:
            return {"error": f"no such variable {key!r} in variable set {varset_id!r}"}
        del vs["variables"][key]
        return {"id": varset_id, "key": key, "deleted": True}

    def attach_variable_set_to_workspaces(self, org: str, varset_id: str, workspace_ids) -> dict:
        vs = self._variable_sets.get((org, varset_id))
        if vs is None:
            return {"error": f"no such variable set {varset_id!r} in org {org!r}"}
        workspace_ids = self._as_list(workspace_ids)
        missing = [wid for wid in workspace_ids if (org, wid) not in self._workspaces]
        if missing:
            return {"error": f"no such workspace(s) {missing} in org {org!r}"}
        vs["workspaces"].update(workspace_ids)
        return {"id": varset_id, "workspaces": sorted(vs["workspaces"])}

    def detach_variable_set_from_workspaces(self, org: str, varset_id: str, workspace_ids) -> dict:
        vs = self._variable_sets.get((org, varset_id))
        if vs is None:
            return {"error": f"no such variable set {varset_id!r} in org {org!r}"}
        vs["workspaces"].difference_update(self._as_list(workspace_ids))
        return {"id": varset_id, "workspaces": sorted(vs["workspaces"])}

    # ── policy sets ──────────────────────────────────────────────────────

    def list_workspace_policy_sets(self, org: str, workspace_id: str) -> dict:
        err = self._require_workspace(org, workspace_id)
        if err:
            return err
        attached = [{"id": psid, "name": ps["name"]} for (o, psid), ps in self._policy_sets.items()
                    if o == org and workspace_id in ps["workspaces"]]
        return {"policy_sets": attached}

    def attach_policy_set_to_workspaces(self, org: str, policyset_id: str, workspace_ids) -> dict:
        ps = self._policy_sets.get((org, policyset_id))
        if ps is None:
            return {"error": f"no such policy set {policyset_id!r} in org {org!r}"}
        workspace_ids = self._as_list(workspace_ids)
        missing = [wid for wid in workspace_ids if (org, wid) not in self._workspaces]
        if missing:
            return {"error": f"no such workspace(s) {missing} in org {org!r}"}
        ps["workspaces"].update(workspace_ids)
        return {"id": policyset_id, "workspaces": sorted(ps["workspaces"])}

    # ── runs / plans / applies ───────────────────────────────────────────

    def create_run(self, org: str, workspace_id: str, message: str | None = None) -> dict:
        err = self._require_workspace(org, workspace_id)
        if err:
            return err
        if self._workspaces[(org, workspace_id)]["locked"]:
            return {"error": f"workspace {workspace_id!r} is locked - unlock it or wait for the pending run first"}
        run_id = self._new_id("run")
        plan_id = self._new_id("plan")
        self._runs[run_id] = {"org": org, "workspace_id": workspace_id, "status": "planned",
                               "message": message or "", "plan_id": plan_id, "apply_id": None, "comments": []}
        self._plans[plan_id] = {"run_id": run_id, "status": "finished",
                                 "log": ["Refreshing state...", "Plan: 1 to add, 0 to change, 0 to destroy."],
                                 "json_output": {"resource_changes": [{"action": "create"}]}}
        self._workspaces[(org, workspace_id)]["locked"] = True
        return {"id": run_id, "plan_id": plan_id, "status": "planned"}

    def list_runs(self, org: str, workspace_id: str) -> dict:
        err = self._require_workspace(org, workspace_id)
        if err:
            return err
        return {"runs": [{"id": rid, "status": r["status"]} for rid, r in sorted(self._runs.items())
                          if r["org"] == org and r["workspace_id"] == workspace_id]}

    def get_run_details(self, run_id: str) -> dict:
        run = self._runs.get(run_id)
        if run is None:
            return {"error": f"no such run {run_id!r}"}
        return {"id": run_id, **run}

    def get_run_comments(self, run_id: str) -> dict:
        run = self._runs.get(run_id)
        if run is None:
            return {"error": f"no such run {run_id!r}"}
        return {"id": run_id, "comments": run["comments"]}

    def action_run(self, run_id: str, action: str) -> dict:
        run = self._runs.get(run_id)
        if run is None:
            return {"error": f"no such run {run_id!r}"}
        if action == "apply":
            if run["status"] != "planned":
                return {"error": f"run {run_id!r} is not in a plannable-to-apply state (status={run['status']!r})"}
            apply_id = self._new_id("apply")
            self._applies[apply_id] = {"run_id": run_id, "status": "finished",
                                        "log": ["Apply complete! Resources: 1 added, 0 changed, 0 destroyed."]}
            run["apply_id"] = apply_id
            run["status"] = "applied"
            ws = self._workspaces.get((run["org"], run["workspace_id"]))
            if ws:
                ws["locked"] = False
            key = (run["org"], run["workspace_id"])
            versions = self._state_versions.setdefault(key, [])
            versions.append({"serial": len(versions) + 1, "resources": ["mock_resource.this"]})
            return {"id": run_id, "apply_id": apply_id, "status": "applied"}
        if action in ("discard", "cancel"):
            if run["status"] != "planned":
                return {"error": f"run {run_id!r} cannot be {action}ed from status {run['status']!r}"}
            run["status"] = "discarded" if action == "discard" else "canceled"
            ws = self._workspaces.get((run["org"], run["workspace_id"]))
            if ws:
                ws["locked"] = False
            return {"id": run_id, "status": run["status"]}
        return {"error": f"unknown action {action!r} (expected apply, discard, or cancel)"}

    def get_plan_details(self, plan_id: str) -> dict:
        plan = self._plans.get(plan_id)
        if plan is None:
            return {"error": f"no such plan {plan_id!r}"}
        return {"id": plan_id, "run_id": plan["run_id"], "status": plan["status"]}

    def get_plan_json_output(self, plan_id: str) -> dict:
        plan = self._plans.get(plan_id)
        if plan is None:
            return {"error": f"no such plan {plan_id!r}"}
        return {"id": plan_id, "json_output": plan["json_output"]}

    def get_plan_logs(self, plan_id: str) -> dict:
        plan = self._plans.get(plan_id)
        if plan is None:
            return {"error": f"no such plan {plan_id!r}"}
        return {"id": plan_id, "log": plan["log"]}

    def get_apply_details(self, apply_id: str) -> dict:
        apply = self._applies.get(apply_id)
        if apply is None:
            return {"error": f"no such apply {apply_id!r}"}
        return {"id": apply_id, "run_id": apply["run_id"], "status": apply["status"]}

    def get_apply_logs(self, apply_id: str) -> dict:
        apply = self._applies.get(apply_id)
        if apply is None:
            return {"error": f"no such apply {apply_id!r}"}
        return {"id": apply_id, "log": apply["log"]}

    # ── state ────────────────────────────────────────────────────────────

    def list_state_versions(self, org: str, workspace_id: str) -> dict:
        err = self._require_workspace(org, workspace_id)
        if err:
            return err
        return {"state_versions": self._state_versions.get((org, workspace_id), [])}

    def get_state_version(self, org: str, workspace_id: str, serial: int | None = None) -> dict:
        err = self._require_workspace(org, workspace_id)
        if err:
            return err
        versions = self._state_versions.get((org, workspace_id), [])
        if not versions:
            return {"error": f"no state versions for workspace {workspace_id!r}"}
        if serial is None:
            return versions[-1]
        for v in versions:
            if v["serial"] == serial:
                return v
        return {"error": f"no such state version serial {serial!r} for workspace {workspace_id!r}"}

    # ── stacks ───────────────────────────────────────────────────────────

    def list_stacks(self, org: str) -> dict:
        err = self._require_org(org)
        if err:
            return err
        return {"stacks": [{"id": sid, "name": s["name"]} for (o, sid), s in sorted(self._stacks.items()) if o == org]}

    def get_stack_details(self, org: str, stack_id: str) -> dict:
        s = self._stacks.get((org, stack_id))
        if s is None:
            return {"error": f"no such stack {stack_id!r} in org {org!r}"}
        return {"id": stack_id, "name": s["name"]}

    # ── no-code workspaces / sentinel ────────────────────────────────────

    def create_no_code_workspace(self, org: str, name: str, module_source: str) -> dict:
        err = self._require_org(org)
        if err:
            return err
        if any(o == org and w["name"] == name for (o, _), w in self._workspaces.items()):
            return {"error": f"workspace {name!r} already exists in org {org!r}"}
        workspace_id = self._new_id("ws")
        self._workspaces[(org, workspace_id)] = {"name": name, "project_id": None, "locked": False,
                                                   "tags": set(), "terraform_version": "1.9.0",
                                                   "module_source": module_source}
        return {"id": workspace_id, "name": name, "module_source": module_source}

    def get_sentinel_mock(self, policy_id: str) -> dict:
        m = self._sentinel_mocks.get(policy_id)
        if m is None:
            return {"error": f"no such sentinel mock {policy_id!r}"}
        return {"id": policy_id, "data": m["data"]}

    # ── private registry ─────────────────────────────────────────────────

    def search_private_modules(self, org: str, query: str) -> dict:
        err = self._require_org(org)
        if err:
            return err
        return {"modules": sorted(k for (o, k) in self._private_modules if o == org and self._matches_query(query, k))}

    def get_private_module_details(self, org: str, namespace: str, name: str, provider: str) -> dict:
        key = f"{namespace}/{name}/{provider}"
        m = self._private_modules.get((org, key))
        if m is None:
            return {"error": f"no such private module {key!r} in org {org!r}"}
        return {"namespace": namespace, "name": name, "provider": provider, "docs": m["docs"]}

    def search_private_providers(self, org: str, query: str) -> dict:
        err = self._require_org(org)
        if err:
            return err
        return {"providers": sorted(k for (o, k) in self._private_providers if o == org and self._matches_query(query, k))}

    def get_private_provider_details(self, org: str, namespace: str, name: str) -> dict:
        key = f"{namespace}/{name}"
        p = self._private_providers.get((org, key))
        if p is None:
            return {"error": f"no such private provider {key!r} in org {org!r}"}
        return {"namespace": namespace, "name": name, "docs": p["docs"]}

    # ── summary ──────────────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "n_calls": len(self.call_log),
            "org_count": len(self._orgs),
            "project_count": len(self._projects),
            "workspace_count": len(self._workspaces),
            "workspaces": {f"{o}/{wid}": {"name": w["name"], "locked": w["locked"]}
                            for (o, wid), w in sorted(self._workspaces.items())},
            "run_count": len(self._runs),
            "applied_run_count": sum(1 for r in self._runs.values() if r["status"] == "applied"),
            "runs": {rid: {"status": r["status"]} for rid, r in sorted(self._runs.items())},
            "state_version_counts": {f"{o}/{wid}": len(v) for (o, wid), v in sorted(self._state_versions.items())},
            "variable_set_count": len(self._variable_sets),
            "policy_set_count": len(self._policy_sets),
            "team_count": len(self._teams),
        }


class DatabaseService(MockService):
    """A mock PostgreSQL instance - the 9 tools the real `crystaldba/
    postgres-mcp` ("Postgres MCP Pro", 3000+ stars) registers, extracted
    directly from its source this session (`TOOL_CATALOG_COMPLETE.md`
    §11's "~5-8 core tools" estimate undercounted by one, the same
    pattern hit for every other category surveyed).

    The real server's most interesting design decision, and the one
    this mock exists to test: `execute_sql` is gated by an `access_mode`
    ("unrestricted" or "restricted") set for the whole session, not
    discoverable through any other tool - in restricted mode only
    read-only statements are allowed (matches the real server's
    SafeSqlDriver). Unlike `git_repo`/`docker`/`terraform`'s precondition
    tests, an agent has no way to learn the session's access mode in
    advance, so this isn't modeled as an agent-facing case (there's
    nothing to discover first) - it's a correctness property of the
    service itself, covered by direct unit tests instead.
    """

    TOOLS = {name: name for name in (
        "list_schemas", "list_objects", "get_object_details", "explain_query",
        "execute_sql", "analyze_workload_indexes", "analyze_query_indexes",
        "analyze_db_health", "get_top_queries",
    )}

    _WRITE_KEYWORDS = ("insert", "update", "delete", "drop", "alter", "create", "truncate", "grant", "revoke")
    _HEALTH_TYPES = {"index", "connection", "vacuum", "sequence", "replication", "buffer", "constraint", "all"}

    def __init__(self) -> None:
        super().__init__()
        self._access_mode = "unrestricted"
        self._schemas: dict[str, dict] = {}
        self._tables: dict[tuple[str, str], dict] = {}
        self._views: dict[tuple[str, str], dict] = {}
        self._sequences: dict[tuple[str, str], dict] = {}
        self._extensions: dict[str, dict] = {}
        self._top_queries: list[dict] = []
        self._health_findings: dict[str, list[str]] = {}

    # ── seeding ──────────────────────────────────────────────────────────

    def seed(self, spec: dict) -> None:
        """``spec`` keys, all optional:
        - ``access_mode`` ("unrestricted" or "restricted", default
          "unrestricted") - restricted refuses write statements in
          `execute_sql`.
        - ``schemas`` ({name: {owner, schema_type}}).
        - ``tables``/``views`` ([{schema, name, columns, row_count,
          indexes} or {schema, name, definition}, ...]) - referencing a
          schema not in ``schemas`` auto-registers a bare one.
        - ``sequences`` ([{schema, name, data_type}, ...]).
        - ``extensions`` ({name: {version, relocatable}}).
        - ``top_queries`` ([{query, total_time, mean_time, calls}, ...]).
        - ``health_findings`` ({health_type: [finding, ...]}).
        """
        for name, cfg in (spec.get("schemas") or {}).items():
            self._schemas[name] = {"owner": cfg.get("owner", "postgres"),
                                    "schema_type": cfg.get("schema_type", "User Schema")}
        for entry in spec.get("tables") or []:
            self._schemas.setdefault(entry["schema"], {"owner": "postgres", "schema_type": "User Schema"})
            self._tables[(entry["schema"], entry["name"])] = {
                "columns": list(entry.get("columns", [])), "row_count": entry.get("row_count", 0),
                "indexes": list(entry.get("indexes", [])),
            }
        for entry in spec.get("views") or []:
            self._schemas.setdefault(entry["schema"], {"owner": "postgres", "schema_type": "User Schema"})
            self._views[(entry["schema"], entry["name"])] = {"definition": entry.get("definition", "")}
        for entry in spec.get("sequences") or []:
            self._schemas.setdefault(entry["schema"], {"owner": "postgres", "schema_type": "User Schema"})
            self._sequences[(entry["schema"], entry["name"])] = {"data_type": entry.get("data_type", "bigint")}
        for name, cfg in (spec.get("extensions") or {}).items():
            self._extensions[name] = {"version": cfg.get("version", "1.0"), "relocatable": cfg.get("relocatable", False)}
        if "access_mode" in spec:
            self._access_mode = spec["access_mode"]
        for entry in spec.get("top_queries") or []:
            self._top_queries.append(dict(entry))
        for health_type, findings in (spec.get("health_findings") or {}).items():
            self._health_findings[health_type] = list(findings)

    # ── schema introspection ─────────────────────────────────────────────

    def list_schemas(self) -> dict:
        return {"schemas": [{"name": n, **s} for n, s in sorted(self._schemas.items())]}

    def list_objects(self, schema_name: str, object_type: str = "table") -> dict:
        if object_type != "extension" and schema_name not in self._schemas:
            return {"error": f"no such schema {schema_name!r}"}
        if object_type in ("table", "view"):
            store = self._tables if object_type == "table" else self._views
            return {"objects": [{"schema": s, "name": n, "type": object_type}
                                 for (s, n) in sorted(store) if s == schema_name]}
        if object_type == "sequence":
            return {"objects": [{"schema": s, "name": n, "data_type": self._sequences[(s, n)]["data_type"]}
                                 for (s, n) in sorted(self._sequences) if s == schema_name]}
        if object_type == "extension":
            return {"objects": [{"name": n, **e} for n, e in sorted(self._extensions.items())]}
        return {"error": f"unsupported object type {object_type!r} (expected table, view, sequence, or extension)"}

    def get_object_details(self, schema_name: str, object_name: str, object_type: str = "table") -> dict:
        if object_type in ("table", "view"):
            store = self._tables if object_type == "table" else self._views
            obj = store.get((schema_name, object_name))
            if obj is None:
                return {"error": f"no such {object_type} {schema_name}.{object_name}"}
            return {"schema": schema_name, "name": object_name, "type": object_type, **obj}
        if object_type == "sequence":
            seq = self._sequences.get((schema_name, object_name))
            if seq is None:
                return {"error": f"no such sequence {schema_name}.{object_name}"}
            return {"schema": schema_name, "name": object_name, **seq}
        if object_type == "extension":
            ext = self._extensions.get(object_name)
            if ext is None:
                return {"error": f"no such extension {object_name!r}"}
            return {"name": object_name, **ext}
        return {"error": f"unsupported object type {object_type!r} (expected table, view, sequence, or extension)"}

    # ── query execution ──────────────────────────────────────────────────

    def explain_query(self, sql: str, analyze: bool = False, hypothetical_indexes: list | None = None) -> dict:
        hypothetical_indexes = hypothetical_indexes or []
        if hypothetical_indexes and analyze:
            return {"error": "cannot use analyze and hypothetical_indexes together"}
        plan = {"sql": sql, "estimated_cost": 100.0, "analyze": analyze}
        if hypothetical_indexes:
            plan["hypothetical_indexes_considered"] = hypothetical_indexes
            plan["estimated_cost"] = 20.0
        return plan

    def execute_sql(self, sql: str) -> dict:
        first_word = sql.strip().split(None, 1)[0].lower() if sql and sql.strip() else ""
        if self._access_mode == "restricted" and first_word in self._WRITE_KEYWORDS:
            return {"error": f"write statement refused - session is in restricted (read-only) mode: {sql!r}"}
        return {"sql": sql, "rows": [], "row_count": 0}

    # ── index tuning ─────────────────────────────────────────────────────

    def analyze_workload_indexes(self, max_index_size_mb: int = 10000, method: str = "dta") -> dict:
        if method not in ("dta", "llm"):
            return {"error": f"unknown method {method!r} (expected dta or llm)"}
        return {"method": method, "max_index_size_mb": max_index_size_mb,
                "recommendations": [{"table": "orders", "columns": ["user_id"], "estimated_improvement": "40%"}]}

    def analyze_query_indexes(self, queries, max_index_size_mb: int = 10000, method: str = "dta") -> dict:
        queries = self._as_list(queries)
        if not queries:
            return {"error": "provide a non-empty list of queries to analyze"}
        if len(queries) > 10:
            return {"error": "provide at most 10 queries to analyze"}
        if method not in ("dta", "llm"):
            return {"error": f"unknown method {method!r} (expected dta or llm)"}
        return {"method": method, "queries_analyzed": len(queries),
                "recommendations": [{"table": "orders", "columns": ["created_at"], "estimated_improvement": "25%"}]}

    # ── health / performance ─────────────────────────────────────────────

    def analyze_db_health(self, health_type: str = "all") -> dict:
        types_ = [t.strip() for t in health_type.split(",")]
        unknown = [t for t in types_ if t not in self._HEALTH_TYPES]
        if unknown:
            return {"error": f"unknown health check type(s) {unknown} (expected one of {sorted(self._HEALTH_TYPES)})"}
        if "all" in types_:
            types_ = sorted(self._HEALTH_TYPES - {"all"})
        return {"checked": types_, "findings": {t: self._health_findings.get(t, []) for t in types_}}

    def get_top_queries(self, sort_by: str = "resources", limit: int = 10) -> dict:
        if sort_by not in ("total_time", "mean_time", "resources"):
            return {"error": f"unknown sort_by {sort_by!r} (expected total_time, mean_time, or resources)"}
        key = "total_time" if sort_by == "resources" else sort_by
        queries = sorted(self._top_queries, key=lambda q: q.get(key, 0), reverse=True)
        return {"sort_by": sort_by, "queries": queries[:limit]}

    # ── summary ──────────────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "n_calls": len(self.call_log),
            "access_mode": self._access_mode,
            "schema_count": len(self._schemas),
            "table_count": len(self._tables),
            "view_count": len(self._views),
            "sequence_count": len(self._sequences),
            "extension_count": len(self._extensions),
        }


class CIPipelineService(MockService):
    """A mock CircleCI instance - all 13 tools the official
    `CircleCI-Public/mcp-server-circleci` registers, extracted directly
    from its `CCI_TOOLS`/`CCI_HANDLERS` source this session. Jenkins was
    the originally planned CI/CD category, but no Jenkins MCP server has
    real traction (best is 29 stars, most are single digits); CircleCI's
    is vendor-published and the clear best fit for this domain's
    "one authoritative real implementation" rule, the same "official
    vendor server wins" choice already made for `forge` (GitHub) and
    `terraform` (HashiCorp).

    The real server's defining design decision, and the one this mock
    exists to test: most project-scoped tools accept THREE mutually
    exclusive ways to identify a project - `projectSlug` (+`branch` for
    most tools), a `projectURL` to parse, or `workspaceRoot`+
    `gitRemoteURL` (+`branch`) for local-checkout detection - and the
    real server's own docs recommend calling `list_followed_projects`
    first to get the exact slug. None of these are opaque IDs (unlike
    Terraform Cloud's workspaces), so there's no id-vs-name pitfall here;
    the discipline being tested is "did the agent supply a complete
    identification method", not "did it use the right identifier".
    """

    TOOLS = {name: name for name in (
        "list_followed_projects", "get_latest_pipeline_status", "get_build_failure_logs",
        "get_job_test_results", "find_flaky_tests", "list_artifacts", "config_helper",
        "run_pipeline", "rerun_workflow", "run_rollback_pipeline", "list_component_versions",
        "download_usage_api_data", "find_underused_resource_classes",
    )}

    def __init__(self) -> None:
        super().__init__()
        self._projects: dict[str, dict] = {}
        self._project_ids: dict[str, str] = {}
        self._git_remotes: dict[str, str] = {}
        self._pipeline_status: dict[tuple[str, str], dict] = {}
        self._failure_logs: dict[tuple[str, str], str] = {}
        self._flaky_tests: dict[str, list] = {}
        self._test_results: dict[tuple[str, str], list] = {}
        self._artifacts: dict[tuple[str, str], list] = {}
        self._pipeline_definitions: dict[str, list] = {}
        self._workflows: dict[str, dict] = {}
        self._rollback_configured: set[str] = set()
        self._environments: dict[str, list] = {}
        self._components: dict[str, list] = {}
        self._component_versions: dict[tuple[str, str, str], list] = {}
        self._orgs: set[str] = set()
        self._usage_csvs: dict[str, list] = {}
        self._next_id = 1

    # ── seeding ──────────────────────────────────────────────────────────

    def seed(self, spec: dict) -> None:
        """``spec`` keys, all optional:
        - ``projects`` ({slug: {name, org, id}}) - followed projects;
          ``id`` (a UUID-shaped string) is optional and only needed for
          cases exercising the projectID identification path.
        - ``git_remotes`` ({remote_url: slug}) - for workspaceRoot/
          gitRemoteURL project detection.
        - ``pipeline_status`` ([{project_slug, branch, status}, ...]).
        - ``failure_logs`` ([{project_slug, branch, logs}, ...]).
        - ``flaky_tests`` ({project_slug: [test name, ...]}).
        - ``test_results`` ([{project_slug, branch, tests: [{name, result}]}]).
        - ``artifacts`` ([{project_slug, branch, artifacts: [...]}]).
        - ``pipeline_definitions`` ({project_slug: [pipeline name, ...]}).
        - ``workflows`` ({workflow_id: {status, project_slug}}).
        - ``rollback_configured`` ([project_slug, ...]).
        - ``environments`` ({project_slug: [{id, name}, ...]}).
        - ``components`` ({project_slug: [{id, name}, ...]}).
        - ``component_versions`` ([{project_slug, environment_id,
          component_id, versions: [...]}]).
        - ``orgs`` ([org_id, ...]).
        - ``usage_csvs`` ({path: [row dict, ...]}).
        """
        for slug, cfg in (spec.get("projects") or {}).items():
            self._projects[slug] = {"name": cfg.get("name", slug.rsplit("/", 1)[-1]),
                                     "org": cfg.get("org", slug.split("/")[1] if "/" in slug else "")}
            if cfg.get("id"):
                self._project_ids[cfg["id"]] = slug
        for url, slug in (spec.get("git_remotes") or {}).items():
            self._git_remotes[url] = slug
        for entry in spec.get("pipeline_status") or []:
            self._pipeline_status[(entry["project_slug"], entry["branch"])] = {
                "status": entry.get("status", "success"), "pipeline_number": entry.get("pipeline_number", 1)}
        for entry in spec.get("failure_logs") or []:
            self._failure_logs[(entry["project_slug"], entry["branch"])] = entry["logs"]
        for slug, tests in (spec.get("flaky_tests") or {}).items():
            self._flaky_tests[slug] = list(tests)
        for entry in spec.get("test_results") or []:
            self._test_results[(entry["project_slug"], entry["branch"])] = list(entry.get("tests", []))
        for entry in spec.get("artifacts") or []:
            self._artifacts[(entry["project_slug"], entry["branch"])] = list(entry.get("artifacts", []))
        for slug, names in (spec.get("pipeline_definitions") or {}).items():
            self._pipeline_definitions[slug] = list(names)
        for wid, cfg in (spec.get("workflows") or {}).items():
            self._workflows[wid] = {"status": cfg.get("status", "failed"), "project_slug": cfg.get("project_slug", "")}
        for slug in spec.get("rollback_configured") or []:
            self._rollback_configured.add(slug)
        for slug, envs in (spec.get("environments") or {}).items():
            self._environments[slug] = list(envs)
        for slug, comps in (spec.get("components") or {}).items():
            self._components[slug] = list(comps)
        for entry in spec.get("component_versions") or []:
            self._component_versions[(entry["project_slug"], entry["environment_id"], entry["component_id"])] = \
                list(entry.get("versions", []))
        for org in spec.get("orgs") or []:
            self._orgs.add(org)
        for path, rows in (spec.get("usage_csvs") or {}).items():
            self._usage_csvs[path] = list(rows)

    # ── project identification (the real design decision this mock tests) ─

    @staticmethod
    def _parse_project_url(url: str) -> str | None:
        m = re.search(r"/pipelines/([^/?]+/[^/?]+/[^/?]+)", url or "")
        return m.group(1) if m else None

    def _resolve_project(self, *, projectSlug=None, branch=None, projectURL=None,
                          workspaceRoot=None, gitRemoteURL=None, require_branch: bool = True) -> tuple[str | None, dict | None]:
        if projectSlug:
            if require_branch and not branch:
                return None, {"error": "branch is required when identifying a project by projectSlug"}
            if projectSlug not in self._projects:
                return None, {"error": f"{projectSlug!r} is not a followed project - call list_followed_projects first"}
            return projectSlug, None
        if projectURL:
            slug = self._parse_project_url(projectURL)
            if slug is None:
                return None, {"error": f"could not parse a project slug from {projectURL!r}"}
            return slug, None
        if workspaceRoot and gitRemoteURL:
            if require_branch and not branch:
                return None, {"error": "branch is required when identifying a project by workspaceRoot/gitRemoteURL"}
            slug = self._git_remotes.get(gitRemoteURL)
            if slug is None:
                return None, {"error": f"no followed project matches git remote {gitRemoteURL!r}"}
            return slug, None
        return None, {"error": "provide projectSlug+branch, projectURL, or workspaceRoot+gitRemoteURL(+branch) to identify the project"}

    def _resolve_project_no_branch(self, *, projectSlug=None, projectID=None) -> tuple[str | None, dict | None]:
        if projectSlug:
            if projectSlug not in self._projects:
                return None, {"error": f"{projectSlug!r} is not a followed project - call list_followed_projects first"}
            return projectSlug, None
        if projectID:
            slug = self._project_ids.get(projectID)
            if slug is None:
                return None, {"error": f"unknown projectID {projectID!r}"}
            return slug, None
        return None, {"error": "either projectSlug or projectID must be provided"}

    # ── tools ────────────────────────────────────────────────────────────

    def list_followed_projects(self) -> dict:
        return {"projects": [{"name": p["name"], "project_slug": slug}
                              for slug, p in sorted(self._projects.items())]}

    def get_latest_pipeline_status(self, projectSlug=None, branch=None, projectURL=None,
                                    workspaceRoot=None, gitRemoteURL=None) -> dict:
        slug, err = self._resolve_project(projectSlug=projectSlug, branch=branch, projectURL=projectURL,
                                           workspaceRoot=workspaceRoot, gitRemoteURL=gitRemoteURL)
        if err:
            return err
        status = self._pipeline_status.get((slug, branch))
        if status is None:
            return {"error": f"no pipeline found for {slug} on branch {branch!r}"}
        return {"project_slug": slug, "branch": branch, **status}

    def get_build_failure_logs(self, projectSlug=None, branch=None, projectURL=None,
                                workspaceRoot=None, gitRemoteURL=None, outputDir=None) -> dict:
        slug, err = self._resolve_project(projectSlug=projectSlug, branch=branch, projectURL=projectURL,
                                           workspaceRoot=workspaceRoot, gitRemoteURL=gitRemoteURL)
        if err:
            return err
        logs = self._failure_logs.get((slug, branch))
        if logs is None:
            return {"error": f"no failed build found for {slug} on branch {branch!r}"}
        return {"project_slug": slug, "branch": branch, "logs": logs}

    def get_job_test_results(self, projectSlug=None, branch=None, projectURL=None, workspaceRoot=None,
                              gitRemoteURL=None, filterByTestsResult=None) -> dict:
        slug, err = self._resolve_project(projectSlug=projectSlug, branch=branch, projectURL=projectURL,
                                           workspaceRoot=workspaceRoot, gitRemoteURL=gitRemoteURL)
        if err:
            return err
        tests = self._test_results.get((slug, branch), [])
        if filterByTestsResult:
            tests = [t for t in tests if t.get("result") == filterByTestsResult]
        return {"project_slug": slug, "branch": branch, "tests": tests}

    def find_flaky_tests(self, projectSlug=None, projectURL=None, workspaceRoot=None, gitRemoteURL=None) -> dict:
        slug, err = self._resolve_project(projectSlug=projectSlug, projectURL=projectURL,
                                           workspaceRoot=workspaceRoot, gitRemoteURL=gitRemoteURL, require_branch=False)
        if err:
            return err
        return {"project_slug": slug, "flaky_tests": self._flaky_tests.get(slug, [])}

    def list_artifacts(self, projectSlug=None, branch=None, projectURL=None,
                        workspaceRoot=None, gitRemoteURL=None) -> dict:
        slug, err = self._resolve_project(projectSlug=projectSlug, branch=branch, projectURL=projectURL,
                                           workspaceRoot=workspaceRoot, gitRemoteURL=gitRemoteURL)
        if err:
            return err
        return {"project_slug": slug, "branch": branch, "artifacts": self._artifacts.get((slug, branch), [])}

    def config_helper(self, configFile: str) -> dict:
        errors = []
        if "version" not in configFile:
            errors.append("missing required top-level 'version' key")
        if "jobs" not in configFile and "workflows" not in configFile:
            errors.append("config defines neither 'jobs' nor 'workflows'")
        if errors:
            return {"valid": False, "errors": errors, "config": configFile}
        return {"valid": True}

    def run_pipeline(self, projectSlug=None, branch=None, projectURL=None, workspaceRoot=None,
                      gitRemoteURL=None, pipelineChoiceName=None, configContent=None) -> dict:
        slug, err = self._resolve_project(projectSlug=projectSlug, branch=branch, projectURL=projectURL,
                                           workspaceRoot=workspaceRoot, gitRemoteURL=gitRemoteURL)
        if err:
            return err
        defs = self._pipeline_definitions.get(slug, [])
        if len(defs) > 1 and not pipelineChoiceName:
            return {"error": "multiple pipeline definitions - specify pipelineChoiceName",
                    "available_pipelines": defs}
        if pipelineChoiceName and defs and pipelineChoiceName not in defs:
            return {"error": f"unknown pipeline {pipelineChoiceName!r} (expected one of {defs})"}
        pid = f"pipeline-{self._next_id}"
        self._next_id += 1
        return {"pipeline_id": pid, "project_slug": slug, "branch": branch,
                "pipeline_url": f"https://app.circleci.com/pipelines/{slug}/{pid}"}

    def rerun_workflow(self, workflowId=None, workflowURL=None, fromFailed=None) -> dict:
        wid = workflowId
        if not wid and workflowURL:
            m = re.search(r"/workflows/([^/?]+)", workflowURL)
            wid = m.group(1) if m else None
        if not wid:
            return {"error": "either workflowId or workflowURL must be provided"}
        wf = self._workflows.get(wid)
        if wf is None:
            return {"error": f"no such workflow {wid!r}"}
        return {"workflow_id": wid, "status": "rerunning", "from_failed": bool(fromFailed)}

    def run_rollback_pipeline(self, environmentName: str, componentName: str, currentVersion: str,
                               targetVersion: str, namespace: str, projectSlug=None, projectID=None,
                               reason=None, parameters=None) -> dict:
        slug, err = self._resolve_project_no_branch(projectSlug=projectSlug, projectID=projectID)
        if err:
            return err
        if slug not in self._rollback_configured:
            return {"error": f"{slug} has no rollback pipeline configured"}
        rid = f"rollback-{self._next_id}"
        self._next_id += 1
        return {"rollback_id": rid, "project_slug": slug, "status": "started",
                "environment": environmentName, "component": componentName,
                "from_version": currentVersion, "to_version": targetVersion}

    def list_component_versions(self, projectSlug=None, projectID=None, orgID=None,
                                 environmentID=None, componentID=None) -> dict:
        slug, err = self._resolve_project_no_branch(projectSlug=projectSlug, projectID=projectID)
        if err:
            return err
        if not environmentID:
            return {"environments": self._environments.get(slug, [])}
        if not componentID:
            return {"components": self._components.get(slug, [])}
        return {"versions": self._component_versions.get((slug, environmentID, componentID), [])}

    def download_usage_api_data(self, orgId: str, outputDir: str, startDate=None, endDate=None, jobId=None) -> dict:
        if orgId not in self._orgs:
            return {"error": f"unknown organization {orgId!r}"}
        path = f"{outputDir.rstrip('/')}/usage_{orgId}.csv"
        return {"org_id": orgId, "csv_path": path}

    def find_underused_resource_classes(self, csvFilePath, threshold: float = 40) -> dict:
        paths = csvFilePath if isinstance(csvFilePath, list) else [csvFilePath]
        rows = []
        for p in paths:
            data = self._usage_csvs.get(p)
            if data is None:
                return {"error": f"no usage data CSV found at {p!r} - call download_usage_api_data first"}
            rows.extend(data)
        underused = [r for r in rows if r.get("median_cpu_utilization_pct", 100) < threshold
                     or r.get("max_cpu_utilization_pct", 100) < threshold]
        return {"threshold": threshold, "underused": underused}

    # ── summary ──────────────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "n_calls": len(self.call_log),
            "project_count": len(self._projects),
            "workflow_count": len(self._workflows),
            "rollback_configured_count": len(self._rollback_configured),
        }


class BuildToolsService(MockService):
    """A mock Nx workspace plus Nx Cloud - all 13 tools the official
    `nrwl/nx-console`'s bundled `nx-mcp` server registers (the real
    constants in its `tool-names.ts`), extracted directly from its
    source. No build-tool ecosystem surveyed this session (Gradle,
    Maven, Bazel, Cargo, CMake, Python packaging, ...) had an official
    or genuinely dominant real implementation - Nx is the one outlier,
    official (Nrwl is the company behind Nx) and two orders of magnitude
    more adopted than anything else found
    (`DEV_NOTES/MCP_IMPLEMENTATION_GAPS.md` has the full survey). Its
    real tool surface skews toward monorepo workspace *introspection*
    (project graph, generators, task-run monitoring, Nx Cloud
    self-healing CI) rather than directly triggering builds/tests - a
    genuinely different shape of "build tool" server than Gradle's or
    npm's, and this mock mirrors that shape faithfully rather than
    inventing a `run_build` tool the real server doesn't have.
    """

    TOOLS = {name: name for name in (
        "nx_docs", "nx_available_plugins", "nx_workspace", "nx_workspace_path",
        "nx_project_details", "nx_generators", "nx_generator_schema", "nx_visualize_graph",
        "nx_current_running_tasks_details", "nx_current_running_task_output",
        "ci_information", "ci_task_output", "update_self_healing_fix",
    )}

    def __init__(self) -> None:
        super().__init__()
        self._workspace_path = ""
        self._plugins: list[dict] = []
        self._docs: dict[str, str] = {}
        self._projects: dict[str, dict] = {}
        self._dependencies: dict[str, list[str]] = {}
        self._nx_json: dict = {}
        self._generators: dict[str, dict] = {}
        self._running_tasks: dict[str, dict] = {}
        self._cipes: dict[str, dict] = {}
        self._ci_task_outputs: dict[str, str] = {}
        self._self_healing_fixes: dict[str, dict] = {}
        self._short_link_index: dict[str, str] = {}
        self._current_branch = "main"

    # ── seeding ──────────────────────────────────────────────────────────

    def seed(self, spec: dict) -> None:
        """``spec`` keys, all optional:
        - ``workspace_path`` (str).
        - ``current_branch`` (str, default "main") - used when a CI tool's
          ``branch`` argument is omitted.
        - ``plugins`` ([{name, description}, ...]).
        - ``docs`` ({keyword: doc section text}) - matched via tokenized
          substring search against ``nx_docs``'s ``userQuery``.
        - ``projects`` ({name: {targets: {...}, tags: [...], root: ...}}).
        - ``dependencies`` ({name: [dep project name, ...]}) - a dep not
          present in ``projects`` counts as an external dependency.
        - ``nx_json`` (dict, the workspace-level config).
        - ``generators`` ({name: {description, schema}}).
        - ``running_tasks`` ({taskId: {status, output, continuous}}).
        - ``cipes`` ({branch: {cipe_url, status, failed_tasks: [...],
          self_healing_status, fixes: [{ai_fix_id, short_link, status}]}}).
        - ``ci_task_outputs`` ({taskId: output text}).
        """
        self._workspace_path = spec.get("workspace_path", self._workspace_path)
        self._current_branch = spec.get("current_branch", self._current_branch)
        for entry in spec.get("plugins") or []:
            self._plugins.append(dict(entry))
        for keyword, text in (spec.get("docs") or {}).items():
            self._docs[keyword] = text
        for name, cfg in (spec.get("projects") or {}).items():
            self._projects[name] = {"targets": dict(cfg.get("targets", {})),
                                     "tags": list(cfg.get("tags", [])),
                                     "root": cfg.get("root", name)}
        for name, deps in (spec.get("dependencies") or {}).items():
            self._dependencies[name] = list(deps)
        if "nx_json" in spec:
            self._nx_json = dict(spec["nx_json"])
        for name, cfg in (spec.get("generators") or {}).items():
            self._generators[name] = {"description": cfg.get("description", ""),
                                       "schema": cfg.get("schema", {})}
        for tid, cfg in (spec.get("running_tasks") or {}).items():
            self._running_tasks[tid] = {"status": cfg.get("status", "running"),
                                         "output": cfg.get("output", ""),
                                         "continuous": cfg.get("continuous", False)}
        for branch, cfg in (spec.get("cipes") or {}).items():
            fixes = {}
            for fix in cfg.get("fixes") or []:
                fid = fix["ai_fix_id"]
                fixes[fid] = {"short_link": fix.get("short_link", ""), "status": fix.get("status", "pending")}
                if fix.get("short_link"):
                    self._short_link_index[fix["short_link"]] = fid
                self._self_healing_fixes[fid] = {**fixes[fid], "branch": branch}
            self._cipes[branch] = {"cipe_url": cfg.get("cipe_url", f"https://cloud.nx.app/cipes/{branch}"),
                                    "status": cfg.get("status", "success"),
                                    "failed_tasks": list(cfg.get("failed_tasks", [])),
                                    "self_healing_status": cfg.get("self_healing_status", "none")}
        for tid, text in (spec.get("ci_task_outputs") or {}).items():
            self._ci_task_outputs[tid] = text

    # ── tools ────────────────────────────────────────────────────────────

    def nx_docs(self, userQuery: str) -> dict:
        words = (userQuery or "").lower().split()
        sections = [text for kw, text in self._docs.items() if any(w in kw.lower() for w in words)]
        return {"sections": sections}

    def nx_available_plugins(self) -> dict:
        return {"plugins": list(self._plugins)}

    def nx_workspace(self, filter=None, select=None, pageToken=None) -> dict:
        names = sorted(self._projects)
        if filter:
            patterns = [p.strip().lower() for p in filter.split(",")]
            names = [n for n in names if any(
                p in n.lower() or p in [t.lower() for t in self._projects[n]["tags"]] for p in patterns)]
        return {"projects": [{"name": n, **self._projects[n]} for n in names], "nxJson": self._nx_json}

    def nx_workspace_path(self) -> dict:
        return {"path": self._workspace_path or "No workspace path set"}

    def nx_project_details(self, projectName: str, select=None, pageToken=None) -> dict:
        if projectName not in self._projects:
            return {"error": f"Project {projectName} not found"}
        deps = self._dependencies.get(projectName, [])
        project_deps = [d for d in deps if d in self._projects]
        external_deps = [d for d in deps if d not in self._projects]
        return {"name": projectName, **self._projects[projectName],
                "projectDependencies": project_deps, "externalDependencies": external_deps}

    def nx_generators(self) -> dict:
        return {"generators": [{"name": n, "description": g["description"]} for n, g in sorted(self._generators.items())]}

    def nx_generator_schema(self, generatorName: str) -> dict:
        gen = self._generators.get(generatorName)
        if gen is None:
            return {"error": f"Generator {generatorName!r} not found"}
        return {"name": generatorName, "schema": gen["schema"]}

    def nx_visualize_graph(self, visualizationType: str, projectName=None, taskName=None) -> dict:
        if visualizationType == "project":
            if not projectName:
                return {"error": "Project name is required"}
            return {"visualizationType": visualizationType, "projectName": projectName}
        if visualizationType == "project-task":
            if not taskName:
                return {"error": "Task name is required for task graph visualization"}
            if not projectName:
                return {"error": "Project name is required"}
            return {"visualizationType": visualizationType, "projectName": projectName, "taskName": taskName}
        if visualizationType == "full-project-graph":
            return {"visualizationType": visualizationType}
        return {"error": f"unknown visualizationType {visualizationType!r} "
                          f"(expected project, project-task, or full-project-graph)"}

    def nx_current_running_tasks_details(self) -> dict:
        return {"tasks": [{"taskId": tid, **t} for tid, t in sorted(self._running_tasks.items())]}

    def nx_current_running_task_output(self, taskId: str, pageToken=None) -> dict:
        task = self._running_tasks.get(taskId)
        if task is None:
            task = next((t for tid, t in self._running_tasks.items() if taskId in tid), None)
        if task is None:
            return {"error": f"No task found with ID {taskId}"}
        return {"taskId": taskId, **task}

    def ci_information(self, url=None, branch=None, select=None, pageToken=None) -> dict:
        key = branch or self._current_branch
        cipe = self._cipes.get(key)
        if cipe is None:
            return {"error": f"no CI pipeline execution found for branch {key!r}"}
        return {"branch": key, **cipe}

    def ci_task_output(self, taskId: str, runId=None, url=None, branch=None, pageToken=None) -> dict:
        output = self._ci_task_outputs.get(taskId)
        if output is None:
            return {"error": f"no CI task output found for task {taskId!r}"}
        return {"taskId": taskId, "output": output}

    def update_self_healing_fix(self, action: str, aiFixId=None, shortLink=None, branch=None) -> dict:
        if action not in ("APPLY", "REJECT", "RERUN_ENVIRONMENT_STATE"):
            return {"error": f"unknown action {action!r} (expected APPLY, REJECT, or RERUN_ENVIRONMENT_STATE)"}
        fix_id = aiFixId
        if not fix_id and shortLink:
            fix_id = self._short_link_index.get(shortLink)
        if not fix_id:
            key = branch or self._current_branch
            fix_id = next((fid for fid, f in self._self_healing_fixes.items() if f["branch"] == key), None)
        if not fix_id or fix_id not in self._self_healing_fixes:
            return {"error": "could not identify a self-healing fix from aiFixId, shortLink, or branch"}
        self._self_healing_fixes[fix_id]["status"] = action
        return {"aiFixId": fix_id, "action": action, "status": action}

    # ── summary ──────────────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "n_calls": len(self.call_log),
            "project_count": len(self._projects),
            "generator_count": len(self._generators),
            "fix_statuses": {fid: f["status"] for fid, f in sorted(self._self_healing_fixes.items())},
        }


class CodeIntelService(MockService):
    """A mock language server - all 6 tools the real `isaacphi/
    mcp-language-server` (1,572 stars, by far the dominant real
    implementation surveyed - next best found was 192 stars) actually
    registers in `tools.go`, not the earlier catalog survey's "~4 core
    tools" estimate (undercounted by 2, the same pattern hit for every
    category surveyed this session). Two more tools (`get_codelens`,
    `execute_codelens`) exist in the source but are commented out and
    never registered - correctly excluded here, matching the "extract
    actual registrations, not aspirational code" discipline used
    throughout this domain.

    The real server has no file-reading tool of its own at all - `hover`/
    `rename_symbol`/`edit_file` all take a `line`/`column` the calling
    agent is expected to already know from separate file-reading
    (normally the client's own file tools, out of scope for this single-
    service-per-case domain - see `DEV_NOTES/TOOL_USE_EXPANSION_PLAN.md`
    §4's still-open cross-service question). Cases here therefore state
    the relevant file/line directly in the prompt, the same "already told
    directly" pattern used elsewhere in this domain when discovery isn't
    the case's own teaching point.
    """

    TOOLS = {name: name for name in (
        "edit_file", "definition", "references", "diagnostics", "hover", "rename_symbol",
    )}

    def __init__(self) -> None:
        super().__init__()
        self._files: dict[str, list[str]] = {}
        self._definitions: dict[str, dict] = {}
        self._references: dict[str, list[dict]] = {}
        self._diagnostics: dict[str, list[dict]] = {}
        self._hover: dict[tuple[str, int], str] = {}
        self._symbols_at: dict[tuple[str, int], str] = {}

    # ── seeding ──────────────────────────────────────────────────────────

    def seed(self, spec: dict) -> None:
        """``spec`` keys, all optional:
        - ``files`` ({path: "line1\\nline2\\n..." or [line, ...]}).
        - ``definitions`` ({symbolName: {filePath, code}}).
        - ``references`` ({symbolName: [{filePath, line}, ...]}).
        - ``diagnostics`` ({filePath: [{line, severity, message}, ...]}) -
          referencing a file not in ``files`` auto-registers an empty one.
        - ``hover`` ([{filePath, line, text, symbol}, ...]) - ``symbol``
          (optional) also registers this position for ``rename_symbol``.
        """
        for path, content in (spec.get("files") or {}).items():
            self._files[path] = content.split("\n") if isinstance(content, str) else list(content)
        for name, cfg in (spec.get("definitions") or {}).items():
            self._definitions[name] = {"filePath": cfg["filePath"], "code": cfg.get("code", "")}
        for name, refs in (spec.get("references") or {}).items():
            self._references[name] = [dict(r) for r in refs]
        for path, entries in (spec.get("diagnostics") or {}).items():
            self._files.setdefault(path, [])
            self._diagnostics[path] = [dict(e) for e in entries]
        for entry in spec.get("hover") or []:
            key = (entry["filePath"], entry["line"])
            self._hover[key] = entry.get("text", "")
            if entry.get("symbol"):
                self._symbols_at[key] = entry["symbol"]

    # ── tools ────────────────────────────────────────────────────────────

    def definition(self, symbolName: str) -> dict:
        d = self._definitions.get(symbolName)
        if d is None:
            return {"error": f"no definition found for symbol {symbolName!r}"}
        return {"symbolName": symbolName, **d}

    def references(self, symbolName: str) -> dict:
        refs = self._references.get(symbolName)
        if not refs:
            return {"error": f"no references found for symbol {symbolName!r}"}
        return {"symbolName": symbolName, "references": refs}

    def diagnostics(self, filePath: str, contextLines: int = 5, showLineNumbers: bool = True) -> dict:
        if filePath not in self._files:
            return {"error": f"file not found: {filePath!r}"}
        return {"filePath": filePath, "diagnostics": self._diagnostics.get(filePath, [])}

    def hover(self, filePath: str, line: int, column: int) -> dict:
        text = self._hover.get((filePath, line))
        if text is None:
            return {"error": f"no hover information available at {filePath}:{line}"}
        return {"filePath": filePath, "line": line, "column": column, "info": text}

    def rename_symbol(self, filePath: str, line: int, column: int, newName: str) -> dict:
        symbol = self._symbols_at.get((filePath, line))
        if symbol is None:
            return {"error": f"no symbol found at {filePath}:{line} to rename"}
        refs = self._references.get(symbol, [])
        files_changed = sorted({r["filePath"] for r in refs} | {filePath})
        return {"symbol": symbol, "newName": newName, "filesChanged": files_changed, "referenceCount": len(refs)}

    def edit_file(self, filePath: str, edits: list) -> dict:
        if filePath not in self._files:
            return {"error": f"file not found: {filePath!r}"}
        lines = self._files[filePath]
        for e in edits:
            start, end = e.get("startLine"), e.get("endLine")
            if start is None or end is None or start < 1 or end < start or end > len(lines):
                return {"error": f"invalid edit range startLine={start}, endLine={end} for a {len(lines)}-line file"}
        for e in sorted(edits, key=lambda e: e["startLine"], reverse=True):
            new_lines = e.get("newText", "").split("\n") if e.get("newText") else []
            lines[e["startLine"] - 1:e["endLine"]] = new_lines
        return {"filePath": filePath, "editsApplied": len(edits)}

    # ── summary ──────────────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "n_calls": len(self.call_log),
            "file_count": len(self._files),
            "definition_count": len(self._definitions),
            "files": {path: "\n".join(lines) for path, lines in sorted(self._files.items())},
        }



class ObservabilityService(MockService):
    """A mock Grafana instance - all 105 unique tools the official
    `grafana/mcp-grafana` (3,332 stars) actually registers across its 30
    tool-category files, extracted directly from its `tools/*.go` source.
    Six tools (`alerting_manage_rules`, `agento11y_manage_evaluators`,
    `agento11y_manage_eval_rules`, `agento11y_manage_eval_collections`,
    `grafana_api_request`, `generate_deeplink`) are registered twice in
    the real server under the same name - a read-only vs. read-write
    variant selected at startup by an `enableWriteTools` flag - and this
    mock models the full read-write variant of each, the same "model the
    complete capability" choice every other service in this domain makes.

    By far the largest service in this domain (105 vs. forge's 77), and
    the first where a deliberate two-tier depth choice was made rather
    than giving every tool equal precondition depth: roughly 22
    "interactive" categories (dashboards, alerting, datasources,
    annotations, folders, snapshots, plugins, provisioning, incidents,
    on-call, Sift investigations, admin/RBAC, assertions, navigation,
    rendering, config generation, panel-query execution, the generic API
    passthrough, Agent Observability, and the Assistant transport) get
    real, source-verified precondition logic (mutual exclusion, XOR
    requirements, operation-gated required fields, two-step confirmation
    flows, UUID/regex validation). The other ~12 categories are all
    structurally the same underlying skill repeated across vendors -
    "query this specific datasource type" (Prometheus, Loki, Pyroscope,
    Elasticsearch, InfluxDB, Graphite, Quickwit, CloudWatch, Athena,
    ClickHouse, Snowflake) - and share one real precondition every one of
    them enforces in the actual source: the resolved datasource must
    actually be of the expected type, or the call is refused. Full
    per-tool extraction (names, parameters, real precondition notes) is
    in `DEV_NOTES/MCP_IMPLEMENTATION_GAPS.md`'s companion research, not
    committed; this docstring records the scope decision, not the data.
    """

    TOOLS = {name: name for name in (
        # admin.go
        "list_teams", "list_users_by_org", "list_all_roles", "get_role_details",
        "get_role_assignments", "list_user_roles", "list_team_roles",
        "get_resource_permissions", "get_resource_description",
        # dashboard.go
        "get_dashboard_by_uid", "update_dashboard", "get_dashboard_panel_queries",
        "get_dashboard_property", "get_dashboard_summary",
        # alerting.go
        "alerting_manage_rules", "alerting_manage_routing",
        # search.go
        "search_dashboards", "search_folders",
        # datasources.go
        "list_datasources", "create_datasource", "update_datasource", "get_datasource",
        "check_datasources_health",
        # annotations.go
        "get_annotations", "create_annotation", "update_annotation", "get_annotation_tags",
        # folder.go
        "create_folder",
        # snapshot.go
        "list_snapshots", "get_snapshot", "create_snapshot", "delete_snapshot",
        # plugins.go
        "get_plugin", "install_plugin", "search_plugin_information",
        # provisioning.go
        "list_provisioning_repositories", "validate_provisioning_file",
        # incident.go
        "list_incidents", "create_incident", "add_activity_to_incident", "get_incident",
        # oncall.go
        "list_oncall_schedules", "get_oncall_shift", "get_current_oncall_users",
        "list_oncall_teams", "list_oncall_users", "list_alert_groups", "get_alert_group",
        # sift.go
        "get_sift_investigation", "get_sift_analysis", "list_sift_investigations",
        "find_error_pattern_logs", "find_slow_requests",
        # asserts.go
        "get_assertions",
        # navigation.go
        "generate_deeplink",
        # config.go
        "suggest_loki_alloy_label_config",
        # rendering.go
        "get_panel_image",
        # examples.go
        "get_query_examples",
        # run_panel_query.go
        "run_panel_query",
        # api.go
        "grafana_api_request",
        # agento11y*.go
        "agento11y_manage_conversations", "agento11y_manage_generations",
        "agento11y_manage_agents", "agento11y_manage_evaluators",
        "agento11y_manage_eval_rules", "agento11y_manage_eval_collections",
        # assistant.go
        "ask_assistant",
        # prometheus.go
        "list_prometheus_metric_metadata", "query_prometheus", "list_prometheus_metric_names",
        "list_prometheus_label_names", "list_prometheus_label_values", "query_prometheus_histogram",
        # loki.go / loki_label_analyzer.go
        "list_loki_label_names", "list_loki_label_values", "query_loki_logs",
        "query_loki_stats", "query_loki_patterns", "analyze_loki_labels",
        # elasticsearch.go
        "query_elasticsearch",
        # influxdb.go
        "query_influxdb",
        # graphite.go
        "query_graphite", "list_graphite_metrics", "list_graphite_tags", "query_graphite_density",
        # quickwit.go
        "query_quickwit",
        # cloudwatch.go
        "query_cloudwatch", "list_cloudwatch_namespaces", "list_cloudwatch_metrics",
        "list_cloudwatch_dimensions",
        # athena.go
        "list_athena_catalogs", "list_athena_databases", "list_athena_tables",
        "describe_athena_table", "query_athena",
        # clickhouse.go
        "query_clickhouse", "list_clickhouse_tables", "describe_clickhouse_table",
        # snowflake.go
        "query_snowflake", "list_snowflake_tables", "describe_snowflake_table",
        # pyroscope.go
        "list_pyroscope_label_names", "list_pyroscope_label_values",
        "list_pyroscope_profile_types", "query_pyroscope",
    )}

    _DATASOURCE_TYPES = {
        "query_prometheus": "prometheus", "query_influxdb": "influxdb",
        "query_graphite": "graphite", "list_graphite_metrics": "graphite",
        "list_graphite_tags": "graphite", "query_graphite_density": "graphite",
        "query_quickwit": "quickwit-quickwit-datasource",
        "query_cloudwatch": "cloudwatch", "list_cloudwatch_namespaces": "cloudwatch",
        "list_cloudwatch_metrics": "cloudwatch", "list_cloudwatch_dimensions": "cloudwatch",
        "list_athena_catalogs": "grafana-athena-datasource", "list_athena_databases": "grafana-athena-datasource",
        "list_athena_tables": "grafana-athena-datasource", "describe_athena_table": "grafana-athena-datasource",
        "query_athena": "grafana-athena-datasource",
        "query_clickhouse": "grafana-clickhouse-datasource",
        "query_snowflake": "grafana-snowflake-datasource",
    }

    def __init__(self) -> None:
        super().__init__()
        self._datasources: dict[str, dict] = {}
        self._dashboards: dict[str, dict] = {}
        self._folders: dict[str, dict] = {}
        self._teams: dict[str, dict] = {}
        self._org_users: list[dict] = []
        self._roles: dict[str, dict] = {}
        self._role_assignments: dict[str, dict] = {}
        self._user_roles: dict[str, list] = {}
        self._team_roles: dict[str, list] = {}
        self._resource_permissions: dict[tuple, list] = {}
        self._alert_rules: dict[str, dict] = {}
        self._contact_points: dict[str, dict] = {}
        self._notification_policies: dict = {}
        self._time_intervals: dict[str, dict] = {}
        self._annotations: dict[int, dict] = {}
        self._next_annotation_id = 1
        self._snapshots: dict[str, dict] = {}
        self._plugins: dict[str, dict] = {}
        self._plugin_catalog: dict[str, dict] = {}
        self._provisioning_repos: dict[str, dict] = {}
        self._provisioning_files: dict[tuple, dict] = {}
        self._incidents: dict[str, dict] = {}
        self._incident_activities: dict[str, list] = {}
        self._oncall_schedules: dict[str, dict] = {}
        self._oncall_shifts: dict[str, dict] = {}
        self._oncall_teams: list[dict] = []
        self._oncall_users: dict[str, dict] = {}
        self._alert_groups: dict[str, dict] = {}
        self._sift_investigations: dict[str, dict] = {}
        self._sift_analyses: dict[tuple, dict] = {}
        self._assertions: list[dict] = []
        self._agento11y_conversations: dict[str, dict] = {}
        self._agento11y_generations: dict[str, dict] = {}
        self._agento11y_agents: dict[str, dict] = {}
        self._agento11y_evaluators: dict[str, dict] = {}
        self._agento11y_eval_rules: dict[str, dict] = {}
        self._agento11y_eval_collections: dict[str, dict] = {}
        self._agento11y_saved_conversations: dict[str, dict] = {}
        self._assistant_conversations: dict[str, list] = {}
        self._metric_data: dict[str, dict] = {}
        self._next_id = 1

    # ── seeding ──────────────────────────────────────────────────────────

    def seed(self, spec: dict) -> None:
        """``spec`` keys, all optional - one per category, each a dict or
        list of dicts matching the shapes returned/consumed by that
        category's tools. See individual tool methods for exact field
        names. Any tool taking a ``datasourceUid`` (or ``data_source_uid``)
        requires a matching entry in ``datasources`` for the type-check
        precondition shared across the ~12 query-connector categories.
        """
        for uid, cfg in (spec.get("datasources") or {}).items():
            self._datasources[uid] = {"name": cfg.get("name", uid), "type": cfg.get("type", "prometheus")}
        for uid, cfg in (spec.get("dashboards") or {}).items():
            self._dashboards[uid] = {"title": cfg.get("title", uid), "panels": list(cfg.get("panels", [])),
                                      "folderUid": cfg.get("folderUid"), "isV2": cfg.get("isV2", False),
                                      "variables": dict(cfg.get("variables", {}))}
        for uid, cfg in (spec.get("folders") or {}).items():
            self._folders[uid] = {"title": cfg.get("title", uid), "parentUid": cfg.get("parentUid")}
        for tid, cfg in (spec.get("teams") or {}).items():
            self._teams[tid] = {"name": cfg.get("name", tid)}
        for entry in spec.get("org_users") or []:
            self._org_users.append(dict(entry))
        for uid, cfg in (spec.get("roles") or {}).items():
            self._roles[uid] = dict(cfg)
        for uid, cfg in (spec.get("role_assignments") or {}).items():
            self._role_assignments[uid] = dict(cfg)
        for uid, roles in (spec.get("user_roles") or {}).items():
            self._user_roles[uid] = list(roles)
        for tid, roles in (spec.get("team_roles") or {}).items():
            self._team_roles[tid] = list(roles)
        for entry in spec.get("resource_permissions") or []:
            key = (entry["resource"], entry["resourceId"])
            self._resource_permissions[key] = list(entry.get("permissions", []))
        for uid, cfg in (spec.get("alert_rules") or {}).items():
            self._alert_rules[uid] = dict(cfg)
        for title, cfg in (spec.get("contact_points") or {}).items():
            self._contact_points[title] = dict(cfg)
        for name, cfg in (spec.get("time_intervals") or {}).items():
            self._time_intervals[name] = dict(cfg)
        for entry in spec.get("annotations") or []:
            aid = entry.get("id", self._next_annotation_id)
            self._annotations[aid] = dict(entry)
            self._next_annotation_id = max(self._next_annotation_id, aid + 1)
        for key, cfg in (spec.get("snapshots") or {}).items():
            self._snapshots[key] = dict(cfg)
        for pid, cfg in (spec.get("plugins") or {}).items():
            self._plugins[pid] = dict(cfg)
        for pid, cfg in (spec.get("plugin_catalog") or {}).items():
            self._plugin_catalog[pid] = dict(cfg)
        for slug, cfg in (spec.get("provisioning_repos") or {}).items():
            self._provisioning_repos[slug] = dict(cfg)
        for entry in spec.get("provisioning_files") or []:
            key = (entry.get("namespace", "default"), entry["repo"], entry["path"])
            self._provisioning_files[key] = dict(entry)
        for iid, cfg in (spec.get("incidents") or {}).items():
            self._incidents[iid] = dict(cfg)
        for iid, activities in (spec.get("incident_activities") or {}).items():
            self._incident_activities[iid] = list(activities)
        for sid, cfg in (spec.get("oncall_schedules") or {}).items():
            self._oncall_schedules[sid] = dict(cfg)
        for sid, cfg in (spec.get("oncall_shifts") or {}).items():
            self._oncall_shifts[sid] = dict(cfg)
        for entry in spec.get("oncall_teams") or []:
            self._oncall_teams.append(dict(entry))
        for uid, cfg in (spec.get("oncall_users") or {}).items():
            self._oncall_users[uid] = dict(cfg)
        for gid, cfg in (spec.get("alert_groups") or {}).items():
            self._alert_groups[gid] = dict(cfg)
        for iid, cfg in (spec.get("sift_investigations") or {}).items():
            self._sift_investigations[iid] = dict(cfg)
        for entry in spec.get("sift_analyses") or []:
            key = (entry["investigationId"], entry["analysisId"])
            self._sift_analyses[key] = dict(entry)
        for entry in spec.get("assertions") or []:
            self._assertions.append(dict(entry))
        for cid, cfg in (spec.get("agento11y_conversations") or {}).items():
            self._agento11y_conversations[cid] = dict(cfg)
        for gid, cfg in (spec.get("agento11y_generations") or {}).items():
            self._agento11y_generations[gid] = dict(cfg)
        for name, cfg in (spec.get("agento11y_agents") or {}).items():
            self._agento11y_agents[name] = dict(cfg)
        for eid, cfg in (spec.get("agento11y_evaluators") or {}).items():
            self._agento11y_evaluators[eid] = dict(cfg)
        for rid, cfg in (spec.get("agento11y_eval_rules") or {}).items():
            self._agento11y_eval_rules[rid] = dict(cfg)
        for cid, cfg in (spec.get("agento11y_eval_collections") or {}).items():
            self._agento11y_eval_collections[cid] = dict(cfg)
        for sid, cfg in (spec.get("agento11y_saved_conversations") or {}).items():
            self._agento11y_saved_conversations[sid] = dict(cfg)
        for uid, cfg in (spec.get("metric_data") or {}).items():
            self._metric_data[uid] = dict(cfg)

    # ── shared helper for the datasource-query-connector categories ────────

    def _check_datasource_type(self, tool_name: str, datasourceUid: str) -> dict | None:
        """Every one of the ~12 real query-connector categories refuses if
        the resolved datasource isn't actually of the expected plugin
        type - the one precondition all of them share in the real
        source. Returns an error dict if the check fails, else None."""
        ds = self._datasources.get(datasourceUid)
        if ds is None:
            return {"error": f"datasource {datasourceUid!r} not found"}
        want = self._DATASOURCE_TYPES.get(tool_name)
        if want and ds["type"] != want:
            return {"error": f"datasource {datasourceUid!r} is type {ds['type']!r}, expected {want!r}"}
        return None

    # ── admin.go: RBAC/admin (read-only) ────────────────────────────────

    def list_teams(self, query: str = "") -> dict:
        teams = [{"id": tid, **t} for tid, t in sorted(self._teams.items())]
        if query:
            teams = [t for t in teams if query.lower() in t["name"].lower()]
        return {"teams": teams}

    def list_users_by_org(self) -> dict:
        return {"users": list(self._org_users)}

    def list_all_roles(self, delegatableOnly: bool = False) -> dict:
        roles = [{"uid": uid, **r} for uid, r in sorted(self._roles.items())]
        if delegatableOnly:
            roles = [r for r in roles if r.get("delegatable")]
        return {"roles": roles}

    def get_role_details(self, roleUID: str) -> dict:
        role = self._roles.get(roleUID)
        if role is None:
            return {"error": f"role {roleUID!r} not found"}
        return {"uid": roleUID, **role}

    def get_role_assignments(self, roleUID: str) -> dict:
        assignment = self._role_assignments.get(roleUID)
        if assignment is None:
            return {"error": f"role {roleUID!r} not found"}
        return {"uid": roleUID, **assignment}

    def list_user_roles(self, userIds: list) -> dict:
        return {"roles": {str(uid): self._user_roles.get(str(uid), []) for uid in userIds}}

    def list_team_roles(self, teamIds: list) -> dict:
        return {"roles": {str(tid): self._team_roles.get(str(tid), []) for tid in teamIds}}

    def get_resource_permissions(self, resource: str, resourceId: str) -> dict:
        return {"permissions": self._resource_permissions.get((resource, resourceId), [])}

    _RESOURCE_TYPES = {"dashboards", "datasources", "folders", "teams", "users", "serviceaccounts"}

    def get_resource_description(self, resourceType: str) -> dict:
        if resourceType not in self._RESOURCE_TYPES:
            return {"error": f"unknown resourceType {resourceType!r} (expected one of {sorted(self._RESOURCE_TYPES)})"}
        return {"resourceType": resourceType, "permissions": ["View", "Edit", "Admin"]}

    # ── dashboard.go ─────────────────────────────────────────────────────

    def get_dashboard_by_uid(self, uid: str) -> dict:
        d = self._dashboards.get(uid)
        if d is None:
            return {"error": f"dashboard {uid!r} not found"}
        return {"uid": uid, **d}

    def update_dashboard(self, dashboard: dict | None = None, uid: str | None = None,
                          operations: list | None = None, folderUid: str | None = None,
                          message: str | None = None, overwrite: bool = False, userId: int | None = None) -> dict:
        if uid and not operations:
            return {"error": "uid given without operations - patch mode requires both"}
        if operations and not uid:
            return {"error": "operations given without uid - patch mode requires both"}
        if not dashboard and not (uid and operations):
            return {"error": "provide either a full dashboard, or uid+operations for a patch"}
        if uid and operations:
            existing = self._dashboards.get(uid)
            if existing is None:
                return {"error": f"dashboard {uid!r} not found"}
            for op in operations:
                pass  # patch application intentionally not deep-simulated; existence/shape already validated
            existing["message"] = message
            return {"uid": uid, "operationsApplied": len(operations)}
        new_uid = uid or f"dash-{self._next_id}"
        if new_uid in self._dashboards and not overwrite:
            return {"error": f"dashboard {new_uid!r} already exists - set overwrite=true to replace it"}
        self._next_id += 1
        self._dashboards[new_uid] = {"title": dashboard.get("title", new_uid), "panels": dashboard.get("panels", []),
                                      "folderUid": folderUid, "isV2": False, "variables": {}}
        return {"uid": new_uid, "created": True}

    def get_dashboard_panel_queries(self, uid: str, panelId: int | None = None, variables: dict | None = None) -> dict:
        d = self._dashboards.get(uid)
        if d is None:
            return {"error": f"dashboard {uid!r} not found"}
        panels = d["panels"]
        if panelId is not None:
            panels = [p for p in panels if p.get("id") == panelId]
            if not panels:
                return {"error": f"panel {panelId} not found in dashboard {uid!r}"}
        return {"uid": uid, "panels": panels}

    def get_dashboard_property(self, uid: str, jsonPath: str) -> dict:
        d = self._dashboards.get(uid)
        if d is None:
            return {"error": f"dashboard {uid!r} not found"}
        return {"uid": uid, "jsonPath": jsonPath, "value": d.get(jsonPath.lstrip("$.").split(".")[0])}

    def get_dashboard_summary(self, uid: str) -> dict:
        d = self._dashboards.get(uid)
        if d is None:
            return {"error": f"dashboard {uid!r} not found"}
        return {"uid": uid, "title": d["title"], "panelCount": len(d["panels"]), "variables": list(d["variables"])}

    # ── alerting.go ──────────────────────────────────────────────────────

    _ALERT_STATES = {"firing", "pending", "normal", "recovering", "nodata", "error"}

    def alerting_manage_rules(self, operation: str, rule_uid: str | None = None, title: str | None = None,
                               rule_group: str | None = None, folder_uid: str | None = None,
                               search_folder: str | None = None, condition: str | None = None,
                               data: list | None = None, no_data_state: str | None = None,
                               exec_err_state: str | None = None, for_: str | None = None,
                               org_id: int | None = None, states: list | None = None, **kwargs) -> dict:
        if folder_uid and search_folder:
            return {"error": "folder_uid and search_folder are mutually exclusive"}
        if states:
            unknown = [s for s in states if s not in self._ALERT_STATES]
            if unknown:
                return {"error": f"unknown state(s) {unknown} (expected one of {sorted(self._ALERT_STATES)})"}
        if operation == "list":
            return {"rules": [{"uid": u, **r} for u, r in sorted(self._alert_rules.items())]}
        if operation in ("get", "versions"):
            if not rule_uid:
                return {"error": f"rule_uid is required for operation {operation!r}"}
            rule = self._alert_rules.get(rule_uid)
            if rule is None:
                return {"error": f"alert rule {rule_uid!r} not found"}
            return {"uid": rule_uid, **rule}
        if operation == "delete":
            if not rule_uid:
                return {"error": "rule_uid is required for operation 'delete'"}
            if rule_uid not in self._alert_rules:
                return {"error": f"alert rule {rule_uid!r} not found"}
            del self._alert_rules[rule_uid]
            return {"uid": rule_uid, "deleted": True}
        if operation in ("create", "update"):
            missing = [n for n, v in (("title", title), ("rule_group", rule_group), ("folder_uid", folder_uid),
                                       ("condition", condition), ("data", data), ("no_data_state", no_data_state),
                                       ("exec_err_state", exec_err_state), ("org_id", org_id)) if not v]
            if missing:
                return {"error": f"missing required fields for {operation}: {missing}"}
            new_uid = rule_uid or f"rule-{self._next_id}"
            self._next_id += 1
            self._alert_rules[new_uid] = {"title": title, "rule_group": rule_group, "folder_uid": folder_uid,
                                           "condition": condition, "state": "normal"}
            return {"uid": new_uid, operation + "d": True}
        return {"error": f"unknown operation {operation!r}"}

    _ROUTING_OPS = {"get_notification_policies", "get_contact_points", "get_contact_point",
                     "get_time_intervals", "get_time_interval"}

    def alerting_manage_routing(self, operation: str, datasource_uid: str | None = None, name: str | None = None,
                                 contact_point_title: str | None = None, time_interval_name: str | None = None,
                                 limit: int = 100) -> dict:
        if operation not in self._ROUTING_OPS:
            return {"error": f"unknown operation {operation!r} (expected one of {sorted(self._ROUTING_OPS)})"}
        if operation == "get_contact_point":
            if not contact_point_title:
                return {"error": "contact_point_title is required for get_contact_point"}
            cp = self._contact_points.get(contact_point_title)
            if cp is None:
                return {"error": f"contact point {contact_point_title!r} not found"}
            return {"title": contact_point_title, **cp}
        if operation == "get_time_interval":
            if not time_interval_name:
                return {"error": "time_interval_name is required for get_time_interval"}
            ti = self._time_intervals.get(time_interval_name)
            if ti is None:
                return {"error": f"time interval {time_interval_name!r} not found"}
            return {"name": time_interval_name, **ti}
        if operation == "get_contact_points":
            if limit < 0:
                return {"error": "limit must be >= 0"}
            points = [{"title": t, **c} for t, c in sorted(self._contact_points.items())]
            if name:
                points = [p for p in points if p["title"] == name]
            return {"contactPoints": points[:limit]}
        if operation == "get_time_intervals":
            return {"timeIntervals": [{"name": n, **t} for n, t in sorted(self._time_intervals.items())]}
        return {"notificationPolicies": self._notification_policies}

    # ── search.go ────────────────────────────────────────────────────────

    def search_dashboards(self, query: str = "", limit: int = 50, page: int = 1) -> dict:
        limit = min(limit, 100) if limit > 0 else 50
        page = page if page > 0 else 1
        results = [{"uid": u, "title": d["title"]} for u, d in sorted(self._dashboards.items())
                   if not query or query.lower() in d["title"].lower()]
        return {"dashboards": results[:limit], "hasMore": len(results) > limit}

    def search_folders(self, query: str = "") -> dict:
        results = [{"uid": u, "title": f["title"]} for u, f in sorted(self._folders.items())
                   if not query or query.lower() in f["title"].lower()]
        return {"folders": results}

    # ── datasources.go ───────────────────────────────────────────────────

    def list_datasources(self, type: str | None = None, limit: int = 50, offset: int = 0) -> dict:
        limit = min(limit, 100) if limit > 0 else 50
        offset = max(offset, 0)
        results = [{"uid": u, **d} for u, d in sorted(self._datasources.items())
                   if not type or type.lower() in d["type"].lower()]
        page = results[offset:offset + limit]
        return {"datasources": page, "hasMore": offset + len(page) < len(results)}

    def create_datasource(self, type: str, name: str | None = None, url: str | None = None,
                           fields: dict | None = None, schemaReviewed: bool = False, **kwargs) -> dict:
        if not schemaReviewed or not name:
            return {"guidance": f"call again with schemaReviewed=true and a name to create a {type!r} datasource",
                     "schemaReviewRequired": True}
        new_uid = f"ds-{self._next_id}"
        self._next_id += 1
        self._datasources[new_uid] = {"name": name, "type": type}
        return {"uid": new_uid, "created": True, "healthCheck": {"status": "OK"}}

    def update_datasource(self, uid: str, schemaReviewed: bool = False, name: str | None = None,
                           url: str | None = None, fields: dict | None = None, **kwargs) -> dict:
        ds = self._datasources.get(uid)
        if ds is None:
            return {"error": f"datasource {uid!r} not found"}
        if not schemaReviewed:
            return {"guidance": f"call again with schemaReviewed=true to apply changes to {ds['type']!r} datasource {uid!r}",
                     "schemaReviewRequired": True}
        if name:
            ds["name"] = name
        return {"uid": uid, "updated": True, "healthCheck": {"status": "OK"}}

    def get_datasource(self, uid: str | None = None, name: str | None = None) -> dict:
        if not uid and not name:
            return {"error": "either uid or name must be provided"}
        if uid:
            ds = self._datasources.get(uid)
            if ds is None:
                return {"error": f"datasource {uid!r} not found"}
            return {"uid": uid, **ds}
        for u, d in self._datasources.items():
            if d["name"] == name:
                return {"uid": u, **d}
        return {"error": f"datasource named {name!r} not found"}

    def check_datasources_health(self, type: str | None = None, uids: list | None = None, offset: int = 0) -> dict:
        if uids:
            targets = [u for u in uids if u in self._datasources]
        else:
            targets = [u for u, d in self._datasources.items() if not type or type.lower() in d["type"].lower()]
        page = targets[offset:offset + 10]
        results = {u: {"status": "OK"} for u in page}
        return {"results": results, "healthy": len(results), "unhealthy": 0}

    # ── annotations.go ───────────────────────────────────────────────────

    def get_annotations(self, from_: int | None = None, to: int | None = None, limit: int = 100,
                         dashboardUid: str | None = None, tags: list | None = None, **kwargs) -> dict:
        results = [{"id": aid, **a} for aid, a in sorted(self._annotations.items())]
        if dashboardUid:
            results = [a for a in results if a.get("dashboardUid") == dashboardUid]
        return {"annotations": results[:limit]}

    def create_annotation(self, dashboardUid: str | None = None, panelId: int | None = None,
                           text: str | None = None, format: str | None = None, what: str | None = None,
                           tags: list | None = None, **kwargs) -> dict:
        if format == "graphite":
            if not what:
                return {"error": "'what' is required when format is 'graphite'"}
        elif not text:
            return {"error": "'text' is required unless format is 'graphite'"}
        aid = self._next_annotation_id
        self._next_annotation_id += 1
        self._annotations[aid] = {"dashboardUid": dashboardUid, "panelId": panelId,
                                   "text": text or what, "tags": list(tags or [])}
        return {"id": aid, "created": True}

    def update_annotation(self, id: int, text: str | None = None, tags: list | None = None, **kwargs) -> dict:
        a = self._annotations.get(id)
        if a is None:
            return {"error": f"annotation {id} not found"}
        if text is not None:
            a["text"] = text
        if tags is not None:
            a["tags"] = list(tags)
        return {"id": id, "updated": True}

    def get_annotation_tags(self, tag: str | None = None, limit: int = 100) -> dict:
        all_tags = sorted({t for a in self._annotations.values() for t in a.get("tags", [])})
        if tag:
            all_tags = [t for t in all_tags if tag.lower() in t.lower()]
        return {"tags": all_tags[:limit]}

    # ── folder.go ────────────────────────────────────────────────────────

    def create_folder(self, title: str, uid: str | None = None, parentUid: str | None = None) -> dict:
        if not title:
            return {"error": "title must not be empty"}
        new_uid = uid or f"folder-{self._next_id}"
        self._next_id += 1
        self._folders[new_uid] = {"title": title, "parentUid": parentUid}
        return {"uid": new_uid, "created": True}

    # ── snapshot.go ──────────────────────────────────────────────────────

    def list_snapshots(self, query: str | None = None, limit: int | None = None) -> dict:
        results = [{"key": k, "name": s.get("name", k)} for k, s in sorted(self._snapshots.items())
                   if not query or query.lower() in s.get("name", "").lower()]
        return {"snapshots": results[:limit] if limit else results}

    def get_snapshot(self, key: str) -> dict:
        key = key.strip()
        if not key:
            return {"error": "key must not be empty"}
        s = self._snapshots.get(key)
        if s is None:
            return {"error": f"snapshot {key!r} not found"}
        return {"key": key, **s}

    def create_snapshot(self, dashboard: dict, name: str | None = None, expires: int | None = None,
                         external: bool = False, key: str | None = None, deleteKey: str | None = None) -> dict:
        if not dashboard:
            return {"error": "dashboard must not be empty"}
        if external and not (key and deleteKey):
            return {"error": "external snapshots require both key and deleteKey"}
        new_key = key or f"snap-{self._next_id}"
        self._next_id += 1
        self._snapshots[new_key] = {"name": name or new_key, "dashboard": dashboard, "external": external}
        return {"key": new_key, "created": True}

    def delete_snapshot(self, key: str) -> dict:
        key = key.strip()
        if not key:
            return {"error": "key must not be empty"}
        if key not in self._snapshots:
            return {"error": f"snapshot {key!r} not found"}
        del self._snapshots[key]
        return {"key": key, "deleted": True}

    # ── plugins.go ───────────────────────────────────────────────────────

    def get_plugin(self, pluginId: str) -> dict:
        pluginId = pluginId.strip()
        if not pluginId:
            return {"error": "pluginId must not be empty"}
        p = self._plugins.get(pluginId)
        if p is None:
            return {"installed": False, "pluginId": pluginId, "suggestion": "call install_plugin to install it"}
        return {"installed": True, "pluginId": pluginId, **p}

    def install_plugin(self, pluginId: str, version: str | None = None) -> dict:
        if not pluginId:
            return {"error": "pluginId must not be empty"}
        if not version:
            catalog = self._plugin_catalog.get(pluginId)
            if catalog is None:
                return {"error": f"plugin {pluginId!r} not found in catalog"}
            return {"confirmationRequired": True, "latestVersion": catalog.get("latest_version"),
                     "message": "call again with an explicit version once confirmed"}
        self._plugins[pluginId] = {"version": version, "enabled": True}
        return {"pluginId": pluginId, "version": version, "installed": True}

    def search_plugin_information(self, query: str) -> dict:
        query = query.strip().lower()
        if not query:
            return {"error": "query must not be empty"}
        matches = [{"pluginId": pid, **c} for pid, c in self._plugin_catalog.items()
                   if query in pid.lower() or query in c.get("description", "").lower()]
        return {"plugins": matches[:10], "note": f"{max(0, len(matches) - 10)} more matches not shown"}

    # ── provisioning.go ──────────────────────────────────────────────────

    @staticmethod
    def _validate_repo_slug(value: str) -> str | None:
        if not value or "/" in value or "\\" in value or value in (".", ".."):
            return f"invalid repository/namespace slug {value!r}"
        return None

    @staticmethod
    def _validate_repo_path(value: str) -> str | None:
        normalized = (value or "").replace("\\", "/")
        if not normalized or normalized.strip("/") == "":
            return f"invalid path {value!r}"
        if any(seg in (".", "..") for seg in normalized.split("/")):
            return f"invalid path {value!r} (contains '.' or '..' segment)"
        return None

    def list_provisioning_repositories(self, namespace: str = "default") -> dict:
        err = self._validate_repo_slug(namespace)
        if err:
            return {"error": err}
        return {"repositories": [{"slug": s, **r} for s, r in sorted(self._provisioning_repos.items())]}

    def validate_provisioning_file(self, repo: str, path: str, namespace: str = "default", ref: str | None = None) -> dict:
        for value, err in ((namespace, self._validate_repo_slug(namespace)), (repo, self._validate_repo_slug(repo))):
            if err:
                return {"error": err}
        path_err = self._validate_repo_path(path)
        if path_err:
            return {"error": path_err}
        key = (namespace, repo, path)
        result = self._provisioning_files.get(key)
        if result is None:
            return {"error": f"no provisioning file found at {repo}/{path}"}
        return {"repo": repo, "path": path, **result}

    # ── incident.go ──────────────────────────────────────────────────────

    def list_incidents(self, limit: int = 10, drill: bool = False, status: str | None = None) -> dict:
        limit = limit if limit > 0 else 10
        results = [{"id": iid, **i} for iid, i in sorted(self._incidents.items())
                   if drill or not i.get("isDrill")]
        if status:
            results = [i for i in results if i.get("status") == status]
        return {"incidents": results[:limit]}

    def create_incident(self, title: str, severity: str, roomPrefix: str, isDrill: bool = False,
                         status: str | None = None, **kwargs) -> dict:
        new_id = f"incident-{self._next_id}"
        self._next_id += 1
        self._incidents[new_id] = {"title": title, "severity": severity, "roomPrefix": roomPrefix,
                                    "isDrill": isDrill, "status": status or "active"}
        return {"id": new_id, "created": True}

    def add_activity_to_incident(self, incidentId: str, body: str, eventTime: str | None = None) -> dict:
        if incidentId not in self._incidents:
            return {"error": f"incident {incidentId!r} not found"}
        self._incident_activities.setdefault(incidentId, []).append({"kind": "userNote", "body": body})
        return {"incidentId": incidentId, "activityAdded": True}

    def get_incident(self, id: str) -> dict:
        i = self._incidents.get(id)
        if i is None:
            return {"error": f"incident {id!r} not found"}
        return {"id": id, **i}

    # ── oncall.go ────────────────────────────────────────────────────────

    def list_oncall_schedules(self, teamId: str | None = None, scheduleId: str | None = None, page: int = 1) -> dict:
        if scheduleId:
            s = self._oncall_schedules.get(scheduleId)
            if s is None:
                return {"error": f"schedule {scheduleId!r} not found"}
            return {"schedules": [{"id": scheduleId, **s}]}
        results = [{"id": sid, **s} for sid, s in sorted(self._oncall_schedules.items())
                   if not teamId or s.get("teamId") == teamId]
        return {"schedules": results}

    def get_oncall_shift(self, shiftId: str) -> dict:
        s = self._oncall_shifts.get(shiftId)
        if s is None:
            return {"error": f"shift {shiftId!r} not found"}
        return {"id": shiftId, **s}

    def get_current_oncall_users(self, scheduleId: str) -> dict:
        s = self._oncall_schedules.get(scheduleId)
        if s is None:
            return {"error": f"schedule {scheduleId!r} not found"}
        return {"scheduleId": scheduleId, "users": s.get("currentUsers", [])}

    def list_oncall_teams(self, page: int = 1) -> dict:
        return {"teams": list(self._oncall_teams)}

    def list_oncall_users(self, userId: str | None = None, username: str | None = None, page: int = 1) -> dict:
        if userId:
            u = self._oncall_users.get(userId)
            if u is None:
                return {"error": f"user {userId!r} not found"}
            return {"users": [{"id": userId, **u}]}
        results = [{"id": uid, **u} for uid, u in sorted(self._oncall_users.items())
                   if not username or u.get("username") == username]
        return {"users": results}

    def list_alert_groups(self, page: int = 1, id: str | None = None, teamId: str | None = None,
                           state: str | None = None, **kwargs) -> dict:
        results = [{"id": gid, **g} for gid, g in sorted(self._alert_groups.items())]
        if id:
            results = [g for g in results if g["id"] == id]
        if teamId:
            results = [g for g in results if g.get("teamId") == teamId]
        if state:
            results = [g for g in results if g.get("state") == state]
        return {"alertGroups": results}

    def get_alert_group(self, alertGroupId: str) -> dict:
        g = self._alert_groups.get(alertGroupId)
        if g is None:
            return {"error": f"alert group {alertGroupId!r} not found"}
        return {"id": alertGroupId, **g}

    # ── sift.go ──────────────────────────────────────────────────────────

    @staticmethod
    def _is_uuid(value: str) -> bool:
        import uuid as _uuid
        try:
            _uuid.UUID(value)
            return True
        except (ValueError, AttributeError, TypeError):
            return False

    def get_sift_investigation(self, id: str) -> dict:
        if not self._is_uuid(id):
            return {"error": f"{id!r} is not a valid investigation UUID"}
        inv = self._sift_investigations.get(id)
        if inv is None:
            return {"error": f"investigation {id!r} not found"}
        return {"id": id, **inv}

    def get_sift_analysis(self, investigationId: str, analysisId: str) -> dict:
        if not self._is_uuid(investigationId) or not self._is_uuid(analysisId):
            return {"error": "investigationId and analysisId must both be valid UUIDs"}
        analysis = self._sift_analyses.get((investigationId, analysisId))
        if analysis is None:
            return {"error": f"analysis with ID {analysisId} not found"}
        return {"investigationId": investigationId, "analysisId": analysisId, **analysis}

    def list_sift_investigations(self, limit: int = 10) -> dict:
        limit = limit if limit > 0 else 10
        return {"investigations": [{"id": iid, **i} for iid, i in sorted(self._sift_investigations.items())][:limit]}

    def find_error_pattern_logs(self, name: str, labels: dict, start: str | None = None, end: str | None = None) -> dict:
        new_id = f"00000000-0000-0000-0000-{self._next_id:012d}"
        self._next_id += 1
        inv = {"name": name, "labels": labels, "status": "finished", "checkType": "ErrorPatternLogs"}
        self._sift_investigations[new_id] = inv
        return {"investigationId": new_id, "status": "finished", "patterns": []}

    def find_slow_requests(self, name: str, labels: dict, start: str | None = None, end: str | None = None) -> dict:
        new_id = f"00000000-0000-0000-0000-{self._next_id:012d}"
        self._next_id += 1
        inv = {"name": name, "labels": labels, "status": "finished", "checkType": "SlowRequests"}
        self._sift_investigations[new_id] = inv
        return {"investigationId": new_id, "status": "finished", "slowRequests": []}

    # ── asserts.go ───────────────────────────────────────────────────────

    def get_assertions(self, startTime: str, endTime: str, entityType: str | None = None,
                        entityName: str | None = None, env: str | None = None, site: str | None = None,
                        namespace: str | None = None) -> dict:
        if not startTime or not endTime:
            return {"error": "startTime and endTime are required"}
        results = list(self._assertions)
        if entityName:
            results = [a for a in results if a.get("entityName") == entityName]
        return {"assertions": results}

    # ── navigation.go ────────────────────────────────────────────────────

    def generate_deeplink(self, resourceType: str, dashboardUid: str | None = None,
                           provisioningPreview: dict | None = None, datasourceUid: str | None = None,
                           panelId: int | None = None, shorten: bool = False, **kwargs) -> dict:
        if resourceType not in ("dashboard", "panel", "explore"):
            return {"error": f"unknown resourceType {resourceType!r} (expected dashboard, panel, or explore)"}
        if resourceType in ("dashboard", "panel"):
            if bool(dashboardUid) == bool(provisioningPreview):
                return {"error": "exactly one of dashboardUid or provisioningPreview is required"}
            if resourceType == "panel" and panelId is None:
                return {"error": "panelId is required for resourceType 'panel'"}
        if resourceType == "explore" and not datasourceUid:
            return {"error": "datasourceUid is required for resourceType 'explore'"}
        url = f"/d/{dashboardUid}" if dashboardUid else f"/explore?ds={datasourceUid}"
        if shorten:
            url = f"/goto/{self._next_id:x}"
            self._next_id += 1
        return {"url": url}

    # ── config.go ────────────────────────────────────────────────────────

    def suggest_loki_alloy_label_config(self, approvedLabels: list, requiredLabels: list | None = None,
                                         normalizeLogLevel: bool = False, componentName: str = "enforce_labels",
                                         forwardTo: str = "loki.write.default.receiver") -> dict:
        if not approvedLabels:
            return {"error": "approvedLabels must not be empty"}
        kept = sorted(set(approvedLabels) | set(requiredLabels or []))
        snippet = f"loki.process \"{componentName}\" {{\n  stage.label_keep {{ values = {kept} }}\n  forward_to = [{forwardTo}]\n}}"
        return {"config": snippet, "keptLabels": kept}

    # ── rendering.go ─────────────────────────────────────────────────────

    def get_panel_image(self, dashboardUid: str | None = None, provisioningPreview: dict | None = None,
                         panelId: int | None = None, width: int = 1000, height: int = 500,
                         theme: str = "dark", scale: int = 1, timeout: int = 60, **kwargs) -> dict:
        if bool(dashboardUid) == bool(provisioningPreview):
            return {"error": "exactly one of dashboardUid or provisioningPreview is required"}
        if dashboardUid and dashboardUid not in self._dashboards:
            return {"error": f"dashboard {dashboardUid!r} not found"}
        if scale < 1 or scale > 3:
            scale = 1
        return {"imageBase64": "iVBORw0KGgo=", "width": width, "height": height}

    # ── examples.go ──────────────────────────────────────────────────────

    _EXAMPLE_DATASOURCE_TYPES = {"prometheus", "loki", "clickhouse", "cloudwatch", "influxdb"}

    def get_query_examples(self, datasourceType: str) -> dict:
        if datasourceType.lower() not in self._EXAMPLE_DATASOURCE_TYPES:
            return {"error": f"unsupported datasource type {datasourceType!r} "
                              f"(expected one of {sorted(self._EXAMPLE_DATASOURCE_TYPES)})"}
        return {"datasourceType": datasourceType, "examples": [{"query": "example", "description": "placeholder"}]}

    # ── run_panel_query.go ───────────────────────────────────────────────

    def run_panel_query(self, dashboardUid: str, panelIds: list, queryIndex: int = 0, start: str = "now-1h",
                         end: str = "now", variables: dict | None = None, datasourceUid: str | None = None,
                         datasourceType: str | None = None) -> dict:
        if not panelIds:
            return {"error": "panelIds must not be empty"}
        d = self._dashboards.get(dashboardUid)
        if d is None:
            return {"error": f"dashboard {dashboardUid!r} not found"}
        results = {}
        for pid in panelIds:
            panel = next((p for p in d["panels"] if p.get("id") == pid), None)
            if panel is None:
                results[str(pid)] = {"error": f"panel {pid} not found"}
            else:
                results[str(pid)] = {"data": []}
        return {"results": results}

    # ── api.go ───────────────────────────────────────────────────────────

    _API_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

    def grafana_api_request(self, endpoint: str, method: str = "GET", body: str | None = None,
                             headers: dict | None = None, jq: str | None = None) -> dict:
        if not endpoint.startswith("/"):
            return {"error": "endpoint must start with '/'"}
        if method not in self._API_METHODS:
            return {"error": f"unsupported method {method!r} (expected one of {sorted(self._API_METHODS)})"}
        return {"endpoint": endpoint, "method": method, "status": 200, "data": {}}

    # ── agento11y*.go ────────────────────────────────────────────────────

    def agento11y_manage_conversations(self, operation: str, conversation_id: str | None = None,
                                        filters: str | None = None, cursor: str | None = None,
                                        limit: int = 50, **kwargs) -> dict:
        if operation not in ("list", "search", "get"):
            return {"error": f"unknown operation {operation!r}"}
        if operation == "get":
            if not conversation_id:
                return {"error": "conversation_id is required for operation 'get'"}
            c = self._agento11y_conversations.get(conversation_id)
            if c is None:
                return {"error": f"conversation {conversation_id!r} not found"}
            return {"id": conversation_id, **c}
        return {"conversations": [{"id": cid, **c} for cid, c in sorted(self._agento11y_conversations.items())][:limit]}

    def agento11y_manage_generations(self, operation: str, generation_id: str, limit: int = 50,
                                      cursor: str | None = None) -> dict:
        if not generation_id:
            return {"error": "generation_id is required"}
        if operation not in ("get", "scores"):
            return {"error": f"unknown operation {operation!r}"}
        g = self._agento11y_generations.get(generation_id)
        if g is None:
            return {"error": f"generation {generation_id!r} not found"}
        if operation == "scores":
            return {"id": generation_id, "scores": g.get("scores", [])}
        return {"id": generation_id, **g}

    def agento11y_manage_agents(self, operation: str, agent_name: str | None = None, version: str | None = None,
                                 name_prefix: str | None = None, limit: int = 50, **kwargs) -> dict:
        if operation not in ("list", "get", "list_versions", "list_version_scores"):
            return {"error": f"unknown operation {operation!r}"}
        if operation in ("get", "list_versions", "list_version_scores") and agent_name is None:
            return {"error": f"agent_name is required for operation {operation!r}"}
        if operation == "list_version_scores" and not (agent_name or "").strip():
            return {"error": "agent_name must not be blank for operation 'list_version_scores'"}
        if operation == "list":
            agents = [{"name": n, **a} for n, a in sorted(self._agento11y_agents.items())]
            if name_prefix:
                agents = [a for a in agents if a["name"].lower().startswith(name_prefix.lower())]
            return {"agents": agents[:limit]}
        agent = self._agento11y_agents.get(agent_name)
        if agent is None:
            return {"error": f"agent {agent_name!r} not found"}
        return {"name": agent_name, **agent}

    _EVALUATOR_OPS = {"list_evaluators", "get_evaluator", "list_templates", "get_template", "list_template_versions",
                       "list_judge_providers", "list_judge_models", "upsert_evaluator", "delete_evaluator",
                       "fork_template", "test_evaluator"}

    def agento11y_manage_evaluators(self, operation: str, evaluator_id: str | None = None,
                                     template_id: str | None = None, definition: dict | None = None,
                                     generation_id: str | None = None, limit: int = 50, **kwargs) -> dict:
        if operation not in self._EVALUATOR_OPS:
            return {"error": f"unknown operation {operation!r}"}
        if operation in ("get_evaluator", "delete_evaluator") and not evaluator_id:
            return {"error": f"evaluator_id is required for operation {operation!r}"}
        if operation in ("get_template", "list_template_versions", "fork_template") and not template_id:
            return {"error": f"template_id is required for operation {operation!r}"}
        if operation == "test_evaluator" and not generation_id:
            return {"error": "generation_id is required for operation 'test_evaluator'"}
        if operation == "upsert_evaluator":
            if not definition:
                return {"error": "definition must not be empty for upsert_evaluator"}
            def_id = definition.get("evaluator_id")
            if not def_id:
                return {"error": "definition.evaluator_id must be a non-empty string"}
            if evaluator_id and evaluator_id != def_id:
                return {"error": "top-level evaluator_id conflicts with definition.evaluator_id"}
            self._agento11y_evaluators[def_id] = dict(definition)
            return {"evaluatorId": def_id, "upserted": True}
        if operation == "delete_evaluator":
            if evaluator_id not in self._agento11y_evaluators:
                return {"error": f"evaluator {evaluator_id!r} not found"}
            del self._agento11y_evaluators[evaluator_id]
            return {"evaluatorId": evaluator_id, "deleted": True}
        if operation == "list_evaluators":
            return {"evaluators": [{"id": eid, **e} for eid, e in sorted(self._agento11y_evaluators.items())][:limit]}
        if operation == "get_evaluator":
            e = self._agento11y_evaluators.get(evaluator_id)
            if e is None:
                return {"error": f"evaluator {evaluator_id!r} not found"}
            return {"id": evaluator_id, **e}
        return {"result": []}

    _EVAL_RULE_OPS = {"list_rules", "get_rule", "list_guards", "get_guard", "create_rule", "update_rule",
                       "delete_rule", "preview_rule", "create_guard", "update_guard", "delete_guard"}

    def agento11y_manage_eval_rules(self, operation: str, rule_id: str | None = None,
                                     definition: dict | None = None, limit: int = 50, **kwargs) -> dict:
        if operation not in self._EVAL_RULE_OPS:
            return {"error": f"unknown operation {operation!r}"}
        if operation in ("create_rule", "create_guard", "preview_rule") and not definition:
            return {"error": f"definition must not be empty for operation {operation!r}"}
        if operation in ("update_rule", "update_guard") and not (rule_id and definition):
            return {"error": f"operation {operation!r} requires both rule_id and definition"}
        if operation in ("get_rule", "get_guard", "delete_rule", "delete_guard") and not rule_id:
            return {"error": f"rule_id is required for operation {operation!r}"}
        if operation in ("create_rule", "update_rule", "create_guard", "update_guard"):
            new_id = rule_id or definition.get("rule_id") or f"rule-{self._next_id}"
            self._next_id += 1
            self._agento11y_eval_rules[new_id] = dict(definition)
            return {"ruleId": new_id, "saved": True}
        if operation in ("delete_rule", "delete_guard"):
            if rule_id not in self._agento11y_eval_rules:
                return {"error": f"rule {rule_id!r} not found"}
            del self._agento11y_eval_rules[rule_id]
            return {"ruleId": rule_id, "deleted": True}
        if operation in ("get_rule", "get_guard"):
            r = self._agento11y_eval_rules.get(rule_id)
            if r is None:
                return {"error": f"rule {rule_id!r} not found"}
            return {"id": rule_id, **r}
        return {"rules": [{"id": rid, **r} for rid, r in sorted(self._agento11y_eval_rules.items())][:limit]}

    _EVAL_COLLECTION_OPS = {"list_saved_conversations", "get_saved_conversation", "list_collections_for_saved_conversation",
                             "list_collections", "get_collection", "list_collection_members", "save_conversation",
                             "delete_saved_conversation", "create_collection", "update_collection", "delete_collection",
                             "add_collection_members", "remove_collection_member"}

    def agento11y_manage_eval_collections(self, operation: str, saved_id: str | None = None,
                                           collection_id: str | None = None, conversation_id: str | None = None,
                                           name: str | None = None, saved_ids: list | None = None,
                                           limit: int = 50, **kwargs) -> dict:
        if operation not in self._EVAL_COLLECTION_OPS:
            return {"error": f"unknown operation {operation!r}"}
        if operation == "save_conversation":
            if not conversation_id or not name:
                return {"error": "conversation_id and name are required for save_conversation"}
            if collection_id:
                return {"error": "collection_id must not be set for save_conversation - bookmark first, then add to a collection separately"}
            new_saved_id = saved_id or f"saved-{conversation_id}"
            self._agento11y_saved_conversations[new_saved_id] = {"conversation_id": conversation_id, "name": name}
            return {"savedId": new_saved_id, "saved": True}
        if operation == "create_collection":
            if not name:
                return {"error": "name is required for create_collection"}
            if collection_id:
                return {"error": "collection_id must not be supplied for create_collection - it is server-assigned"}
            if saved_ids:
                return {"error": "saved_ids must not be supplied for create_collection - collections are created empty"}
            new_id = f"collection-{self._next_id}"
            self._next_id += 1
            self._agento11y_eval_collections[new_id] = {"name": name, "members": []}
            return {"collectionId": new_id, "created": True}
        if operation == "add_collection_members":
            if not collection_id or not saved_ids:
                return {"error": "collection_id and a non-empty saved_ids are required"}
            c = self._agento11y_eval_collections.get(collection_id)
            if c is None:
                return {"error": f"collection {collection_id!r} not found"}
            unknown = [s for s in saved_ids if s not in self._agento11y_saved_conversations]
            if unknown:
                return {"error": f"unknown saved conversation id(s): {unknown}"}
            c["members"].extend(saved_ids)
            return {"collectionId": collection_id, "added": len(saved_ids)}
        if operation == "get_saved_conversation":
            if not saved_id:
                return {"error": "saved_id is required"}
            s = self._agento11y_saved_conversations.get(saved_id)
            if s is None:
                return {"error": f"saved conversation {saved_id!r} not found"}
            return {"id": saved_id, **s}
        if operation == "list_saved_conversations":
            return {"savedConversations": [{"id": sid, **s} for sid, s in sorted(self._agento11y_saved_conversations.items())][:limit]}
        if operation == "list_collections":
            return {"collections": [{"id": cid, "name": c["name"]} for cid, c in sorted(self._agento11y_eval_collections.items())][:limit]}
        return {"result": []}

    # ── assistant.go ─────────────────────────────────────────────────────

    def ask_assistant(self, prompt: str, contextId: str | None = None) -> dict:
        if not prompt.strip():
            return {"error": "prompt must not be blank"}
        ctx = contextId or f"ctx-{self._next_id}"
        self._next_id += 1
        self._assistant_conversations.setdefault(ctx, []).append(prompt)
        return {"contextId": ctx, "reply": "This is a mock assistant reply."}

    # ── prometheus.go (query-connector: shared datasource-type check) ──────

    def list_prometheus_metric_metadata(self, datasourceUid: str, limit: int = 10,
                                         limitPerMetric: int | None = None, metric: str | None = None,
                                         projectName: str | None = None) -> dict:
        err = self._check_datasource_type("list_prometheus_metric_metadata", datasourceUid)
        if err:
            return err
        data = self._metric_data.get(datasourceUid, {})
        return {"metadata": data.get("metadata", [])[:limit]}

    def query_prometheus(self, datasourceUid: str, expr: str, startTime: str | None = None,
                          endTime: str | None = None, stepSeconds: int | None = None,
                          queryType: str = "range", projectName: str | None = None) -> dict:
        err = self._check_datasource_type("query_prometheus", datasourceUid)
        if err:
            return err
        if queryType == "range" and not stepSeconds:
            return {"error": "stepSeconds is required when queryType is 'range'"}
        data = self._metric_data.get(datasourceUid, {})
        return {"expr": expr, "results": data.get("query_results", [])}

    def list_prometheus_metric_names(self, datasourceUid: str, regex: str | None = None, limit: int = 10,
                                      page: int = 1, startRfc3339: str | None = None, endRfc3339: str | None = None,
                                      projectName: str | None = None) -> dict:
        err = self._check_datasource_type("list_prometheus_metric_names", datasourceUid)
        if err:
            return err
        if regex:
            try:
                re.compile(regex)
            except re.error as e:
                return {"error": f"invalid regex {regex!r}: {e}"}
        data = self._metric_data.get(datasourceUid, {})
        return {"metricNames": data.get("metric_names", [])[:limit]}

    def list_prometheus_label_names(self, datasourceUid: str, matches: list | None = None,
                                     startRfc3339: str | None = None, endRfc3339: str | None = None,
                                     limit: int = 100, projectName: str | None = None) -> dict:
        err = self._check_datasource_type("list_prometheus_label_names", datasourceUid)
        if err:
            return err
        data = self._metric_data.get(datasourceUid, {})
        return {"labelNames": data.get("label_names", [])[:limit]}

    def list_prometheus_label_values(self, datasourceUid: str, labelName: str, matches: list | None = None,
                                      startRfc3339: str | None = None, endRfc3339: str | None = None,
                                      limit: int = 100, projectName: str | None = None) -> dict:
        err = self._check_datasource_type("list_prometheus_label_values", datasourceUid)
        if err:
            return err
        data = self._metric_data.get(datasourceUid, {})
        return {"labelValues": data.get("label_values", {}).get(labelName, [])[:limit]}

    def query_prometheus_histogram(self, datasourceUid: str, metric: str, percentile: float,
                                    labels: str | None = None, rateInterval: str = "5m",
                                    startTime: str = "now-1h", endTime: str = "now", stepSeconds: int = 60,
                                    projectName: str | None = None) -> dict:
        err = self._check_datasource_type("query_prometheus_histogram", datasourceUid)
        if err:
            return err
        if not (0 <= percentile <= 100):
            return {"error": f"percentile {percentile} must be between 0 and 100"}
        return {"metric": metric, "percentile": percentile, "results": []}

    # ── loki.go / loki_label_analyzer.go ────────────────────────────────

    def list_loki_label_names(self, datasourceUid: str, startRfc3339: str | None = None,
                               endRfc3339: str | None = None) -> dict:
        err = self._check_datasource_type("list_loki_label_names", datasourceUid)
        if err:
            return err
        data = self._metric_data.get(datasourceUid, {})
        return {"labelNames": data.get("label_names", [])}

    def list_loki_label_values(self, datasourceUid: str, labelName: str, startRfc3339: str | None = None,
                                endRfc3339: str | None = None) -> dict:
        err = self._check_datasource_type("list_loki_label_values", datasourceUid)
        if err:
            return err
        data = self._metric_data.get(datasourceUid, {})
        return {"labelValues": data.get("label_values", {}).get(labelName, [])}

    def query_loki_logs(self, datasourceUid: str, logql: str, startRfc3339: str | None = None,
                         endRfc3339: str | None = None, limit: int = 10, direction: str = "backward",
                         queryType: str = "range", stepSeconds: int | None = None) -> dict:
        err = self._check_datasource_type("query_loki_logs", datasourceUid)
        if err:
            return err
        data = self._metric_data.get(datasourceUid, {})
        lines = data.get("log_lines", [])
        return {"logql": logql, "lines": lines[:limit], "resultsTruncated": len(lines) > limit}

    def query_loki_stats(self, datasourceUid: str, logql: str, startRfc3339: str | None = None,
                          endRfc3339: str | None = None) -> dict:
        err = self._check_datasource_type("query_loki_stats", datasourceUid)
        if err:
            return err
        data = self._metric_data.get(datasourceUid, {})
        return {"logql": logql, "stats": data.get("stats", {"streams": 0, "chunks": 0, "entries": 0, "bytes": 0})}

    def query_loki_patterns(self, datasourceUid: str, logql: str, startRfc3339: str | None = None,
                             endRfc3339: str | None = None, step: str | None = None) -> dict:
        err = self._check_datasource_type("query_loki_patterns", datasourceUid)
        if err:
            return err
        data = self._metric_data.get(datasourceUid, {})
        return {"logql": logql, "patterns": data.get("patterns", [])}

    def analyze_loki_labels(self, datasourceUid: str | None = None, labels: list | None = None,
                             selector: str | None = None, maxLabels: int = 50, startRfc3339: str | None = None,
                             endRfc3339: str | None = None, expectedBaseLabels: list | None = None,
                             perfMetrics: dict | None = None) -> dict:
        if not datasourceUid and not labels:
            return {"error": "either datasourceUid (live mode) or labels (static mode) is required"}
        always_remove = {"user_id", "request_id", "trace_id", "span_id", "session_id", "transaction", "uuid"}
        prefer_metadata = {"pod", "node", "container_id", "instance", "version", "image", "tag", "process_id", "filename"}
        verdicts = []
        for label in (labels or []):
            n = label["name"].lower()
            if n in always_remove:
                verdicts.append({"name": label["name"], "verdict": "remove"})
            elif n in prefer_metadata:
                verdicts.append({"name": label["name"], "verdict": "prefer_metadata"})
            else:
                verdicts.append({"name": label["name"], "verdict": "keep"})
        return {"verdicts": verdicts, "recommendedLabelSet": [v["name"] for v in verdicts if v["verdict"] == "keep"]}

    # ── elasticsearch.go ─────────────────────────────────────────────────

    def query_elasticsearch(self, datasourceUid: str, index: str, query: str, startTime: str | None = None,
                             endTime: str | None = None, limit: int = 10) -> dict:
        ds = self._datasources.get(datasourceUid)
        if ds is None:
            return {"error": f"datasource {datasourceUid!r} not found"}
        if ds["type"] not in ("elasticsearch", "opensearch"):
            return {"error": f"datasource {datasourceUid!r} is type {ds['type']!r}, expected elasticsearch or opensearch"}
        limit = min(limit, 100) if limit > 0 else 10
        data = self._metric_data.get(datasourceUid, {})
        return {"index": index, "hits": data.get("hits", [])[:limit]}

    # ── influxdb.go ──────────────────────────────────────────────────────

    def query_influxdb(self, datasourceUid: str, query: str, dialect: str | None = None,
                        start: str = "now-1h", end: str = "now", maxDataPoints: int = 1000) -> dict:
        if not query.strip():
            return {"error": "query must not be blank"}
        ds = self._datasources.get(datasourceUid)
        if ds is None:
            return {"error": f"datasource {datasourceUid!r} not found"}
        if ds["type"] != "influxdb":
            return {"error": f"datasource {datasourceUid!r} is type {ds['type']!r}, expected 'influxdb'"}
        if dialect and dialect not in ("influxql", "flux"):
            return {"error": f"unknown dialect {dialect!r} (expected influxql or flux)"}
        data = self._metric_data.get(datasourceUid, {})
        return {"columns": data.get("columns", []), "rows": data.get("rows", [])}

    # ── graphite.go ──────────────────────────────────────────────────────

    def query_graphite(self, datasourceUid: str, target: str, from_: str = "-1h", until: str = "now",
                        maxDataPoints: int | None = None) -> dict:
        err = self._check_datasource_type("query_graphite", datasourceUid)
        if err:
            return err
        data = self._metric_data.get(datasourceUid, {})
        return {"target": target, "series": data.get("series", [])}

    def list_graphite_metrics(self, datasourceUid: str, query: str = "*") -> dict:
        err = self._check_datasource_type("list_graphite_metrics", datasourceUid)
        if err:
            return err
        data = self._metric_data.get(datasourceUid, {})
        return {"nodes": data.get("nodes", [])}

    def list_graphite_tags(self, datasourceUid: str, prefix: str | None = None) -> dict:
        err = self._check_datasource_type("list_graphite_tags", datasourceUid)
        if err:
            return err
        data = self._metric_data.get(datasourceUid, {})
        tags = data.get("tags", [])
        if prefix:
            tags = [t for t in tags if t.startswith(prefix)]
        return {"tags": tags}

    def query_graphite_density(self, datasourceUid: str, target: str, from_: str = "-1h", until: str = "now") -> dict:
        err = self._check_datasource_type("query_graphite_density", datasourceUid)
        if err:
            return err
        data = self._metric_data.get(datasourceUid, {})
        return {"target": target, "density": data.get("density", {"fillRatio": 0, "lastSeen": None})}

    # ── quickwit.go ──────────────────────────────────────────────────────

    def query_quickwit(self, datasourceUid: str, query: str, index: str | None = None,
                        startTime: str | None = None, endTime: str | None = None, limit: int = 10) -> dict:
        err = self._check_datasource_type("query_quickwit", datasourceUid)
        if err:
            return err
        ds = self._datasources[datasourceUid]
        configured_index = ds.get("index")
        if index and configured_index and index != configured_index:
            return {"error": f"index {index!r} does not match the datasource's configured index {configured_index!r}"}
        limit = min(limit, 100) if limit > 0 else 10
        data = self._metric_data.get(datasourceUid, {})
        return {"documents": data.get("documents", [])[:limit]}

    # ── cloudwatch.go ────────────────────────────────────────────────────

    def query_cloudwatch(self, datasourceUid: str, namespace: str, metricName: str, region: str,
                          dimensions: dict | None = None, statistic: str = "Average", period: int = 300,
                          start: str = "now-1h", end: str = "now", accountId: str | None = None) -> dict:
        err = self._check_datasource_type("query_cloudwatch", datasourceUid)
        if err:
            return err
        data = self._metric_data.get(datasourceUid, {})
        values = data.get("values", [])
        return {"namespace": namespace, "metricName": metricName, "values": values,
                "sum": sum(values), "min": min(values) if values else None, "max": max(values) if values else None}

    def list_cloudwatch_namespaces(self, datasourceUid: str, region: str, accountId: str | None = None) -> dict:
        err = self._check_datasource_type("list_cloudwatch_namespaces", datasourceUid)
        if err:
            return err
        data = self._metric_data.get(datasourceUid, {})
        return {"namespaces": data.get("namespaces", [])}

    def list_cloudwatch_metrics(self, datasourceUid: str, namespace: str, region: str, accountId: str | None = None) -> dict:
        err = self._check_datasource_type("list_cloudwatch_metrics", datasourceUid)
        if err:
            return err
        data = self._metric_data.get(datasourceUid, {})
        return {"metrics": data.get("metrics", {}).get(namespace, [])}

    def list_cloudwatch_dimensions(self, datasourceUid: str, namespace: str, metricName: str, region: str,
                                    accountId: str | None = None) -> dict:
        err = self._check_datasource_type("list_cloudwatch_dimensions", datasourceUid)
        if err:
            return err
        data = self._metric_data.get(datasourceUid, {})
        return {"dimensions": data.get("dimensions", {}).get(metricName, [])}

    # ── athena.go ────────────────────────────────────────────────────────

    def list_athena_catalogs(self, datasourceUid: str, region: str | None = None) -> dict:
        err = self._check_datasource_type("list_athena_catalogs", datasourceUid)
        if err:
            return err
        data = self._metric_data.get(datasourceUid, {})
        return {"catalogs": data.get("catalogs", [])}

    def list_athena_databases(self, datasourceUid: str, region: str | None = None, catalog: str | None = None) -> dict:
        err = self._check_datasource_type("list_athena_databases", datasourceUid)
        if err:
            return err
        data = self._metric_data.get(datasourceUid, {})
        return {"databases": data.get("databases", [])}

    def list_athena_tables(self, datasourceUid: str, region: str | None = None, catalog: str | None = None,
                            database: str | None = None) -> dict:
        err = self._check_datasource_type("list_athena_tables", datasourceUid)
        if err:
            return err
        data = self._metric_data.get(datasourceUid, {})
        return {"tables": data.get("tables", [])}

    def describe_athena_table(self, datasourceUid: str, table: str, region: str | None = None,
                               catalog: str | None = None, database: str | None = None) -> dict:
        err = self._check_datasource_type("describe_athena_table", datasourceUid)
        if err:
            return err
        if not table:
            return {"error": "table must not be empty"}
        data = self._metric_data.get(datasourceUid, {})
        return {"table": table, "columns": data.get("columns_by_table", {}).get(table, [])}

    def query_athena(self, datasourceUid: str, query: str, start: str = "now-1h", end: str = "now",
                      region: str | None = None, catalog: str | None = None, database: str | None = None,
                      variables: dict | None = None, limit: int = 100, resultReuseEnabled: bool = False,
                      resultReuseMaxAgeInMinutes: int | None = None) -> dict:
        err = self._check_datasource_type("query_athena", datasourceUid)
        if err:
            return err
        data = self._metric_data.get(datasourceUid, {})
        return {"query": query, "rows": data.get("rows", [])}

    # ── clickhouse.go ────────────────────────────────────────────────────

    _IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9_]+$")

    def query_clickhouse(self, datasourceUid: str, query: str, start: str = "now-1h", end: str = "now",
                          variables: dict | None = None, limit: int = 100) -> dict:
        err = self._check_datasource_type("query_clickhouse", datasourceUid)
        if err:
            return err
        data = self._metric_data.get(datasourceUid, {})
        return {"query": query, "rows": data.get("rows", [])}

    def list_clickhouse_tables(self, datasourceUid: str, database: str | None = None) -> dict:
        err = self._check_datasource_type("list_clickhouse_tables", datasourceUid)
        if err:
            return err
        if database and not self._IDENTIFIER_RE.match(database):
            return {"error": f"invalid database identifier {database!r}"}
        data = self._metric_data.get(datasourceUid, {})
        return {"tables": data.get("tables", [])}

    def describe_clickhouse_table(self, datasourceUid: str, table: str, database: str = "default") -> dict:
        err = self._check_datasource_type("describe_clickhouse_table", datasourceUid)
        if err:
            return err
        if not table:
            return {"error": "table must not be empty"}
        if not self._IDENTIFIER_RE.match(database) or not self._IDENTIFIER_RE.match(table):
            return {"error": "database and table must be valid SQL identifiers (letters, digits, underscore only)"}
        data = self._metric_data.get(datasourceUid, {})
        return {"table": table, "columns": data.get("columns_by_table", {}).get(table, [])}

    # ── snowflake.go ─────────────────────────────────────────────────────

    def query_snowflake(self, datasourceUid: str, query: str, start: str = "now-1h", end: str = "now",
                         variables: dict | None = None, limit: int = 100) -> dict:
        err = self._check_datasource_type("query_snowflake", datasourceUid)
        if err:
            return err
        data = self._metric_data.get(datasourceUid, {})
        return {"query": query, "rows": data.get("rows", [])}

    def list_snowflake_tables(self, datasourceUid: str, database: str | None = None, schema: str | None = None) -> dict:
        err = self._check_datasource_type("list_snowflake_tables", datasourceUid)
        if err:
            return err
        for value in (database, schema):
            if value and not self._IDENTIFIER_RE.match(value):
                return {"error": f"invalid identifier {value!r}"}
        data = self._metric_data.get(datasourceUid, {})
        return {"tables": data.get("tables", [])}

    def describe_snowflake_table(self, datasourceUid: str, table: str, schema: str = "PUBLIC",
                                  database: str | None = None) -> dict:
        err = self._check_datasource_type("describe_snowflake_table", datasourceUid)
        if err:
            return err
        if not table:
            return {"error": "table must not be empty"}
        for value in (database, schema, table):
            if value and not self._IDENTIFIER_RE.match(value):
                return {"error": f"invalid identifier {value!r}"}
        data = self._metric_data.get(datasourceUid, {})
        return {"table": table, "columns": data.get("columns_by_table", {}).get(table, [])}

    # ── pyroscope.go ─────────────────────────────────────────────────────

    @staticmethod
    def _validate_pyroscope_time_range(start: str | None, end: str | None) -> dict | None:
        if start and end and start >= end:
            return {"error": f"start {start!r} must be strictly before end {end!r}"}
        return None

    def list_pyroscope_label_names(self, data_source_uid: str, matchers: str = "{}",
                                    start_rfc_3339: str | None = None, end_rfc_3339: str | None = None) -> dict:
        err = self._validate_pyroscope_time_range(start_rfc_3339, end_rfc_3339)
        if err:
            return err
        data = self._metric_data.get(data_source_uid, {})
        return {"labelNames": data.get("label_names", [])}

    def list_pyroscope_label_values(self, data_source_uid: str, name: str, matchers: str = "{}",
                                     start_rfc_3339: str | None = None, end_rfc_3339: str | None = None) -> dict:
        if not name.strip():
            return {"error": "name must not be blank"}
        err = self._validate_pyroscope_time_range(start_rfc_3339, end_rfc_3339)
        if err:
            return err
        data = self._metric_data.get(data_source_uid, {})
        return {"labelValues": data.get("label_values", {}).get(name, [])}

    def list_pyroscope_profile_types(self, data_source_uid: str, start_rfc_3339: str | None = None,
                                      end_rfc_3339: str | None = None) -> dict:
        err = self._validate_pyroscope_time_range(start_rfc_3339, end_rfc_3339)
        if err:
            return err
        data = self._metric_data.get(data_source_uid, {})
        return {"profileTypes": data.get("profile_types", [])}

    def query_pyroscope(self, data_source_uid: str, profile_type: str, query_type: str = "both",
                         format: str = "table", matchers: str = "{}", group_by: list | None = None,
                         step: float | None = None, max_node_depth: int = 100,
                         start_rfc_3339: str | None = None, end_rfc_3339: str | None = None) -> dict:
        err = self._validate_pyroscope_time_range(start_rfc_3339, end_rfc_3339)
        if err:
            return err
        data = self._metric_data.get(data_source_uid, {})
        return {"profileType": profile_type, "profile": data.get("profile", []), "metrics": data.get("metrics_series", [])}

    # ── summary ──────────────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "n_calls": len(self.call_log),
            "datasource_count": len(self._datasources),
            "dashboard_count": len(self._dashboards),
            "folder_count": len(self._folders),
            "alert_rule_count": len(self._alert_rules),
            "annotation_count": len(self._annotations),
            "snapshot_count": len(self._snapshots),
            "incident_count": len(self._incidents),
        }


class CloudInfraService(MockService):
    """A mock AWS Infrastructure-as-Code assistant - all 9 tools the
    official `awslabs/aws-iac-mcp-server` registers (8 static
    `@mcp.tool()` functions plus one dynamically-proxied
    `read_iac_documentation_page`, wired up from a remote AWS knowledge
    endpoint at server startup - extracted directly from `server.py`).

    AWS's real MCP landscape is unlike every other category built this
    session: not one server but ~59 separate ones under one `awslabs/mcp`
    monorepo, with no single dominant "the" implementation. Three real
    candidates were considered for this slot: `aws-api-mcp-server` (a
    thin ~3-tool generic AWS-CLI-string passthrough, a poor fit for this
    domain's structured-tool methodology), `ccapi-mcp-server` (rich
    resource CRUD across 1,100+ AWS resource types with genuine
    token-enforced workflow security - explain before create, deletion
    double-confirmation, IAM wildcard-policy blocking - but explicitly
    deprecated in its own source), and `aws-iac-mcp-server` (the current,
    actively-maintained official replacement, CloudFormation/CDK
    authoring-and-validation focused rather than live resource
    management). The user chose the third, consistent with this session
    never having picked a deprecated implementation as "the" real one
    anywhere else, even though it is a thinner, more docs/validation-
    flavored tool than "cloud infra management" originally implied.
    """

    TOOLS = {name: name for name in (
        "validate_cloudformation_template", "check_cloudformation_template_compliance",
        "troubleshoot_cloudformation_deployment", "get_cloudformation_pre_deploy_validation_instructions",
        "search_cdk_documentation", "search_cloudformation_documentation",
        "search_cdk_samples_and_constructs", "cdk_best_practices", "read_iac_documentation_page",
    )}

    _CDK_LANGUAGES = {"typescript", "python", "java", "csharp", "go"}

    def __init__(self) -> None:
        super().__init__()
        self._stacks: dict[tuple[str, str], dict] = {}
        self._cdk_docs: dict[str, str] = {}
        self._cfn_docs: dict[str, str] = {}
        self._cdk_samples: dict[str, dict] = {}
        self._doc_pages: dict[str, str] = {}
        self._best_practices: str = "Follow least-privilege IAM, enable encryption at rest, use CDK-NAG."

    # ── seeding ──────────────────────────────────────────────────────────

    def seed(self, spec: dict) -> None:
        """``spec`` keys, all optional:
        - ``stacks`` ([{name, region, failed_resources: [...], cloudtrail_events: [...]}, ...]) -
          only a stack seeded here can be troubleshot; anything else refuses.
        - ``cdk_docs`` / ``cfn_docs`` ({keyword: doc text}) - matched via
          tokenized substring search, same helper pattern used elsewhere
          in this domain.
        - ``cdk_samples`` ({keyword: {language: sample text}}).
        - ``doc_pages`` ({url: page text}) - only a URL seeded here can be
          read via ``read_iac_documentation_page``.
        - ``best_practices`` (str, overrides the default canned guidance).
        """
        for entry in spec.get("stacks") or []:
            key = (entry["name"], entry["region"])
            self._stacks[key] = {"failed_resources": list(entry.get("failed_resources", [])),
                                  "cloudtrail_events": list(entry.get("cloudtrail_events", []))}
        for keyword, text in (spec.get("cdk_docs") or {}).items():
            self._cdk_docs[keyword] = text
        for keyword, text in (spec.get("cfn_docs") or {}).items():
            self._cfn_docs[keyword] = text
        for keyword, cfg in (spec.get("cdk_samples") or {}).items():
            self._cdk_samples[keyword] = dict(cfg)
        for url, text in (spec.get("doc_pages") or {}).items():
            self._doc_pages[url] = text
        if "best_practices" in spec:
            self._best_practices = spec["best_practices"]

    @staticmethod
    def _matches_query(query: str, target: str) -> bool:
        words = (query or "").lower().split()
        if not words:
            return True
        target = target.lower()
        return any(w in target for w in words)

    # ── tools ────────────────────────────────────────────────────────────

    def validate_cloudformation_template(self, template_content: str, regions: list | None = None,
                                          ignore_checks: list | None = None) -> dict:
        try:
            template = json.loads(template_content)
        except (json.JSONDecodeError, TypeError):
            return {"valid": False, "error_count": 1, "warning_count": 0,
                    "issues": [{"message": "template is not valid JSON"}]}
        resources = template.get("Resources")
        if not resources:
            return {"valid": False, "error_count": 1, "warning_count": 0,
                    "issues": [{"message": "template has no Resources section"}]}
        issues = []
        for name, res in resources.items():
            rtype = res.get("Type", "")
            if "::" not in rtype:
                issues.append({"resource": name, "message": f"invalid or missing resource Type {rtype!r}"})
        ignored = set(ignore_checks or [])
        return {"valid": not issues, "error_count": len(issues), "warning_count": 0, "issues": issues}

    def check_cloudformation_template_compliance(self, template_content: str) -> dict:
        try:
            template = json.loads(template_content)
        except (json.JSONDecodeError, TypeError):
            return {"is_compliant": False, "violation_count": 1,
                    "violations": [{"message": "template is not valid JSON"}]}
        violations = []
        for name, res in (template.get("Resources") or {}).items():
            props = res.get("Properties", {}) or {}
            if props.get("PubliclyAccessible") is True:
                violations.append({"resource": name, "message": "resource is publicly accessible"})
            policy = props.get("PolicyDocument") or {}
            for stmt in (policy.get("Statement") or []):
                if stmt.get("Effect") == "Allow" and stmt.get("Action") == "*" and stmt.get("Resource") == "*":
                    violations.append({"resource": name, "message": "overly permissive IAM policy (Action=* Resource=*)"})
        return {"is_compliant": not violations, "violation_count": len(violations), "violations": violations}

    def troubleshoot_cloudformation_deployment(self, stack_name: str, region: str, include_cloudtrail: bool = True) -> dict:
        stack = self._stacks.get((stack_name, region))
        if stack is None:
            return {"error": f"no stack named {stack_name!r} found in region {region!r}"}
        result = {"stackName": stack_name, "region": region, "failedResources": stack["failed_resources"]}
        if include_cloudtrail:
            result["cloudtrailEvents"] = stack["cloudtrail_events"]
        return result

    def get_cloudformation_pre_deploy_validation_instructions(self) -> dict:
        return {"overview": "CloudFormation change sets validate templates against three common failure "
                             "causes before provisioning: invalid property syntax, resource name conflicts, "
                             "and S3 bucket emptiness constraints on delete."}

    def search_cdk_documentation(self, query: str) -> dict:
        results = [{"title": kw, "context": text} for kw, text in self._cdk_docs.items() if self._matches_query(query, kw)]
        return {"results": results}

    def search_cloudformation_documentation(self, query: str) -> dict:
        results = [{"title": kw, "context": text} for kw, text in self._cfn_docs.items() if self._matches_query(query, kw)]
        return {"results": results}

    def search_cdk_samples_and_constructs(self, query: str, language: str = "typescript") -> dict:
        if language not in self._CDK_LANGUAGES:
            return {"error": f"unknown language {language!r} (expected one of {sorted(self._CDK_LANGUAGES)})"}
        results = [{"title": kw, "context": cfg[language]} for kw, cfg in self._cdk_samples.items()
                   if self._matches_query(query, kw) and language in cfg]
        return {"results": results}

    def cdk_best_practices(self) -> dict:
        return {"results": [{"title": "CDK Best Practices", "context": self._best_practices}]}

    def read_iac_documentation_page(self, url: str, starting_index: int = 0) -> dict:
        page = self._doc_pages.get(url)
        if page is None:
            return {"error": f"no documentation page found at {url!r}"}
        return {"url": url, "content": page[starting_index:]}

    # ── summary ──────────────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "n_calls": len(self.call_log),
            "stack_count": len(self._stacks),
            "doc_page_count": len(self._doc_pages),
        }


# name -> (service class, {tool_name: OpenAI-function-schema dict})
# One registry entry per service; a case names the service via
# ``tool_service`` and (optionally) which of its tools to expose via
# ``tools`` - defaulting to all of them when omitted.
_TASK_TRACKER_SCHEMAS: dict[str, dict] = {
    "create_task": {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Create a new task in the tracker.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short task title."},
                    "assignee": {"type": "string", "description": "Who the task is assigned to (optional)."},
                },
                "required": ["title"],
            },
        },
    },
    "complete_task": {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Mark an existing task as done, by its id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "The id of the task to complete."},
                },
                "required": ["task_id"],
            },
        },
    },
    "list_tasks": {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List tasks, optionally filtered by status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["open", "done"],
                               "description": "Only return tasks with this status (optional)."},
                },
                "required": [],
            },
        },
    },
    "delete_task": {
        "type": "function",
        "function": {
            "name": "delete_task",
            "description": "Permanently delete a task by its id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "The id of the task to delete."},
                },
                "required": ["task_id"],
            },
        },
    },
}

def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    """Terser construction for the git schema table below - 18 tools of
    the same {type:function, function:{...}} shape made the literal-dict
    form (used above for task_tracker's 4) too repetitive to stay readable."""
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }}


_GIT_REPO_SCHEMAS: dict[str, dict] = {
    "git_status": _fn(
        "git_status", "Show the working tree status: staged, unstaged-modified, and untracked files.",
        {}, []),
    "git_add": _fn(
        "git_add", "Stage one or more files for the next commit.",
        {"paths": {"type": "array", "items": {"type": "string"}, "description": "Paths to stage."}},
        ["paths"]),
    "git_reset": _fn(
        "git_reset", "Unstage all currently staged changes (does not touch the working tree).",
        {}, []),
    "git_commit": _fn(
        "git_commit", "Record the currently staged changes as a new commit.",
        {"message": {"type": "string", "description": "The commit message."}},
        ["message"]),
    "git_diff_unstaged": _fn(
        "git_diff_unstaged", "Show changes in the working directory that are not yet staged.",
        {}, []),
    "git_diff_staged": _fn(
        "git_diff_staged", "Show changes that are staged for the next commit.",
        {}, []),
    "git_diff": _fn(
        "git_diff", "Compare two refs (branch/tag/commit id), or a ref against the working tree if only ref_a is given.",
        {"ref_a": {"type": "string", "description": "First ref (optional)."},
         "ref_b": {"type": "string", "description": "Second ref (optional)."}},
        []),
    "git_log": _fn(
        "git_log", "Show commit history for the current branch, most recent first.",
        {"max_count": {"type": "integer", "description": "Limit the number of commits returned."},
         "since": {"type": "string", "description": "Only show commits after this ref (optional)."}},
        []),
    "git_show": _fn(
        "git_show", "Show the message and full file snapshot of a specific commit.",
        {"ref": {"type": "string", "description": "Commit id, branch, or tag to show."}},
        ["ref"]),
    "git_branch": _fn(
        "git_branch", "List branches, indicating which one is current.",
        {}, []),
    "git_create_branch": _fn(
        "git_create_branch", "Create a new branch (does not switch to it).",
        {"name": {"type": "string", "description": "Name of the new branch."},
         "start_point": {"type": "string", "description": "Ref to branch from (defaults to current HEAD)."}},
        ["name"]),
    "git_checkout": _fn(
        "git_checkout", "Switch the current branch to an existing branch.",
        {"ref": {"type": "string", "description": "Branch name to switch to."}},
        ["ref"]),
    "git_blame": _fn(
        "git_blame", "Show line-by-line commit attribution for a file's current content.",
        {"path": {"type": "string", "description": "File path to blame."}},
        ["path"]),
    "git_remotes": _fn(
        "git_remotes", "List configured remotes.",
        {}, []),
    "git_tags": _fn(
        "git_tags", "List tags.",
        {}, []),
    "git_tag": _fn(
        "git_tag", "Create a tag pointing at a commit (defaults to current HEAD).",
        {"name": {"type": "string", "description": "Tag name."},
         "ref": {"type": "string", "description": "Commit/branch to tag (optional, defaults to HEAD)."}},
        ["name"]),
    "git_push": _fn(
        "git_push", "Push the current (or given) branch to a remote.",
        {"remote": {"type": "string", "description": "Remote name (defaults to 'origin')."},
         "branch": {"type": "string", "description": "Branch to push (defaults to current branch)."}},
        []),
    "git_pull": _fn(
        "git_pull", "Fetch and fast-forward the current (or given) branch from a remote.",
        {"remote": {"type": "string", "description": "Remote name (defaults to 'origin')."},
         "branch": {"type": "string", "description": "Branch to pull (defaults to current branch)."}},
        []),
}

_FILESYSTEM_SCHEMAS: dict[str, dict] = {
    "read_text_file": _fn(
        "read_text_file", "Read the complete contents of a text file, optionally limited to the first/last N lines.",
        {"path": {"type": "string", "description": "File to read."},
         "head": {"type": "integer", "description": "Only return the first N lines (optional)."},
         "tail": {"type": "integer", "description": "Only return the last N lines (optional)."}},
        ["path"]),
    "read_media_file": _fn(
        "read_media_file", "Read a binary/media file (image, etc.) and return its content with MIME type.",
        {"path": {"type": "string", "description": "Media file to read."}},
        ["path"]),
    "read_multiple_files": _fn(
        "read_multiple_files", "Read several files at once in a single call.",
        {"paths": {"type": "array", "items": {"type": "string"}, "description": "Files to read."}},
        ["paths"]),
    "write_file": _fn(
        "write_file", "Create a new file or overwrite an existing one with the given content.",
        {"path": {"type": "string", "description": "File to write."},
         "content": {"type": "string", "description": "Full content to write."}},
        ["path", "content"]),
    "edit_file": _fn(
        "edit_file", "Make one or more targeted search-and-replace edits to an existing file, without rewriting it wholesale.",
        {"path": {"type": "string", "description": "File to edit."},
         "edits": {"type": "array", "description": "List of {old_text, new_text} replacements, applied in order.",
                   "items": {"type": "object", "properties": {
                       "old_text": {"type": "string"}, "new_text": {"type": "string"}}}},
         "dry_run": {"type": "boolean", "description": "Preview the result without actually applying it (optional)."}},
        ["path", "edits"]),
    "create_directory": _fn(
        "create_directory", "Create a directory (and any missing parent directories).",
        {"path": {"type": "string", "description": "Directory to create."}},
        ["path"]),
    "list_directory": _fn(
        "list_directory", "List the immediate contents of a directory, marking each entry as a file or a directory.",
        {"path": {"type": "string", "description": "Directory to list (empty/omitted for the root)."}},
        []),
    "list_directory_with_sizes": _fn(
        "list_directory_with_sizes", "Like list_directory, but includes each entry's size.",
        {"path": {"type": "string", "description": "Directory to list (empty/omitted for the root)."},
         "sort_by": {"type": "string", "enum": ["name", "size"], "description": "Sort order (optional, defaults to name)."}},
        []),
    "move_file": _fn(
        "move_file", "Move or rename a file to a new path.",
        {"source": {"type": "string", "description": "Existing path."},
         "destination": {"type": "string", "description": "New path."}},
        ["source", "destination"]),
    "search_files": _fn(
        "search_files", "Recursively search for files whose name matches a pattern, under a given directory.",
        {"path": {"type": "string", "description": "Directory to search under (empty/omitted for the root)."},
         "pattern": {"type": "string", "description": "Substring/pattern to match against each file's name."},
         "exclude_patterns": {"type": "array", "items": {"type": "string"}, "description": "Paths containing any of these are skipped (optional)."}},
        ["pattern"]),
    "directory_tree": _fn(
        "directory_tree", "Return the full recursive directory structure as a nested tree, starting at the given path.",
        {"path": {"type": "string", "description": "Root to build the tree from (empty/omitted for the whole tree)."},
         "exclude_patterns": {"type": "array", "items": {"type": "string"}, "description": "Paths containing any of these are skipped (optional)."}},
        []),
    "get_file_info": _fn(
        "get_file_info", "Get metadata (type, size) for a file or directory.",
        {"path": {"type": "string", "description": "Path to inspect."}},
        ["path"]),
    "list_allowed_directories": _fn(
        "list_allowed_directories", "List the directories this tool is allowed to access.",
        {}, []),
}

_DOCKER_SCHEMAS: dict[str, dict] = {
    "list_containers": _fn(
        "list_containers", "List containers.",
        {"all": {"type": "boolean", "description": "Include stopped containers too (default true)."}},
        []),
    "create_container": _fn(
        "create_container", "Create and start a new container from an image (the image must already be pulled or built; any listed volumes must already exist).",
        {"name": {"type": "string", "description": "Name for the new container."},
         "image": {"type": "string", "description": "Image to run it from."},
         "ports": {"type": "object", "description": "Port mappings (optional)."},
         "env": {"type": "object", "description": "Environment variables (optional)."},
         "volumes": {"type": "array", "items": {"type": "string"}, "description": "Volume names to mount (optional)."}},
        ["name", "image"]),
    "start_container": _fn(
        "start_container", "Start a stopped or paused container.",
        {"name": {"type": "string", "description": "Container to start."}},
        ["name"]),
    "stop_container": _fn(
        "stop_container", "Stop a running container.",
        {"name": {"type": "string", "description": "Container to stop."}},
        ["name"]),
    "restart_container": _fn(
        "restart_container", "Restart a container.",
        {"name": {"type": "string", "description": "Container to restart."}},
        ["name"]),
    "pause_container": _fn(
        "pause_container", "Pause a running container's processes.",
        {"name": {"type": "string", "description": "Container to pause."}},
        ["name"]),
    "remove_container": _fn(
        "remove_container", "Remove a container. Refuses if it's currently running, unless force is set.",
        {"name": {"type": "string", "description": "Container to remove."},
         "force": {"type": "boolean", "description": "Remove even if running (optional, default false)."}},
        ["name"]),
    "inspect_container": _fn(
        "inspect_container", "Get full details for a container (image, status, ports, env, networks).",
        {"name": {"type": "string", "description": "Container to inspect."}},
        ["name"]),
    "get_container_logs": _fn(
        "get_container_logs", "Get a container's log output.",
        {"name": {"type": "string", "description": "Container to get logs from."},
         "tail": {"type": "integer", "description": "Only return the last N lines (optional)."}},
        ["name"]),
    "get_container_stats": _fn(
        "get_container_stats", "Get live resource usage (CPU, memory) for a container.",
        {"name": {"type": "string", "description": "Container to get stats for."}},
        ["name"]),
    "list_images": _fn(
        "list_images", "List locally available images.",
        {}, []),
    "pull_image": _fn(
        "pull_image", "Pull an image from a registry.",
        {"tag": {"type": "string", "description": "Image tag to pull, e.g. 'nginx:latest'."}},
        ["tag"]),
    "build_image": _fn(
        "build_image", "Build an image from a Dockerfile.",
        {"tag": {"type": "string", "description": "Tag to give the built image."},
         "dockerfile_path": {"type": "string", "description": "Path to the Dockerfile (optional)."}},
        ["tag"]),
    "tag_image": _fn(
        "tag_image", "Give an existing image an additional tag.",
        {"source": {"type": "string", "description": "Existing image tag."},
         "target": {"type": "string", "description": "New tag to add."}},
        ["source", "target"]),
    "remove_image": _fn(
        "remove_image", "Remove an image. Refuses if any container still uses it.",
        {"tag": {"type": "string", "description": "Image tag to remove."}},
        ["tag"]),
    "prune_images": _fn(
        "prune_images", "Remove every image not used by any existing container.",
        {}, []),
    "list_networks": _fn(
        "list_networks", "List networks.",
        {}, []),
    "create_network": _fn(
        "create_network", "Create a new network.",
        {"name": {"type": "string", "description": "Name for the new network."},
         "driver": {"type": "string", "description": "Network driver (optional, defaults to 'bridge')."}},
        ["name"]),
    "connect_network": _fn(
        "connect_network", "Attach a container to a network.",
        {"network": {"type": "string", "description": "Network to connect to."},
         "container": {"type": "string", "description": "Container to attach."}},
        ["network", "container"]),
    "disconnect_network": _fn(
        "disconnect_network", "Detach a container from a network.",
        {"network": {"type": "string", "description": "Network to disconnect from."},
         "container": {"type": "string", "description": "Container to detach."}},
        ["network", "container"]),
    "list_volumes": _fn(
        "list_volumes", "List volumes.",
        {}, []),
    "create_volume": _fn(
        "create_volume", "Create a new named volume.",
        {"name": {"type": "string", "description": "Name for the new volume."}},
        ["name"]),
    "remove_volume": _fn(
        "remove_volume", "Remove a volume. Refuses if any container still uses it.",
        {"name": {"type": "string", "description": "Volume to remove."}},
        ["name"]),
    "prune_volumes": _fn(
        "prune_volumes", "Remove every volume not used by any existing container.",
        {}, []),
    "docker_info": _fn(
        "docker_info", "Get a system-wide summary (container/image/network/volume counts).",
        {}, []),
}

_KUBERNETES_SCHEMAS: dict[str, dict] = {
    "kubectl_get": _fn(
        "kubectl_get", "Get one resource by name, or list all resources of a kind in a namespace.",
        {"kind": {"type": "string", "description": "Resource kind, e.g. 'pod', 'deployment', 'namespace'."},
         "name": {"type": "string", "description": "Specific resource name (optional - omit to list)."},
         "namespace": {"type": "string", "description": "Namespace (defaults to 'default')."},
         "all_namespaces": {"type": "boolean", "description": "List across every namespace (optional)."},
         "selector": {"type": "object", "description": "Label key/value filters to apply when listing (optional)."}},
        ["kind"]),
    "kubectl_describe": _fn(
        "kubectl_describe", "Show detailed information about a specific resource.",
        {"kind": {"type": "string", "description": "Resource kind."},
         "name": {"type": "string", "description": "Resource name."},
         "namespace": {"type": "string", "description": "Namespace (defaults to 'default')."}},
        ["kind", "name"]),
    "kubectl_create": _fn(
        "kubectl_create", "Create a new resource. Refuses if one with the same kind/name/namespace already exists - use kubectl_apply to update it.",
        {"kind": {"type": "string", "description": "Resource kind, e.g. 'deployment', 'pod', 'namespace'."},
         "name": {"type": "string", "description": "Name for the new resource."},
         "namespace": {"type": "string", "description": "Namespace (defaults to 'default'; must already exist)."},
         "replicas": {"type": "integer", "description": "Replica count, for deployment/statefulset kinds (optional)."},
         "image": {"type": "string", "description": "Container image (optional)."},
         "labels": {"type": "object", "description": "Labels to attach (optional)."}},
        ["kind", "name"]),
    "kubectl_apply": _fn(
        "kubectl_apply", "Create a resource if it doesn't exist, or update it in place if it does (idempotent).",
        {"kind": {"type": "string", "description": "Resource kind, e.g. 'deployment', 'pod', 'namespace'."},
         "name": {"type": "string", "description": "Resource name."},
         "namespace": {"type": "string", "description": "Namespace (defaults to 'default'; must already exist)."},
         "replicas": {"type": "integer", "description": "Replica count, for deployment/statefulset kinds (optional)."},
         "image": {"type": "string", "description": "Container image (optional)."},
         "labels": {"type": "object", "description": "Labels to attach (optional)."}},
        ["kind", "name"]),
    "kubectl_delete": _fn(
        "kubectl_delete", "Delete a resource. Deleting a namespace refuses while it still contains resources or Helm releases.",
        {"kind": {"type": "string", "description": "Resource kind."},
         "name": {"type": "string", "description": "Resource name."},
         "namespace": {"type": "string", "description": "Namespace (defaults to 'default')."}},
        ["kind", "name"]),
    "kubectl_logs": _fn(
        "kubectl_logs", "Get a pod's log output.",
        {"name": {"type": "string", "description": "Pod name."},
         "namespace": {"type": "string", "description": "Namespace (defaults to 'default')."},
         "container": {"type": "string", "description": "Specific container within the pod (optional)."},
         "tail": {"type": "integer", "description": "Only return the last N lines (optional)."}},
        ["name"]),
    "kubectl_context": _fn(
        "kubectl_context", "Get the current kubeconfig context, list all contexts, or switch to a different one.",
        {"operation": {"type": "string", "enum": ["get", "list", "use"], "description": "What to do (defaults to 'get')."},
         "name": {"type": "string", "description": "Context name (required for 'use')."}},
        []),
    "kubectl_scale": _fn(
        "kubectl_scale", "Set the replica count for a deployment or statefulset.",
        {"name": {"type": "string", "description": "Resource name."},
         "replicas": {"type": "integer", "description": "Desired replica count."},
         "kind": {"type": "string", "description": "Resource kind (defaults to 'deployment')."},
         "namespace": {"type": "string", "description": "Namespace (defaults to 'default')."}},
        ["name", "replicas"]),
    "kubectl_patch": _fn(
        "kubectl_patch", "Modify specific fields of an existing resource without replacing the whole thing.",
        {"kind": {"type": "string", "description": "Resource kind."},
         "name": {"type": "string", "description": "Resource name."},
         "patch": {"type": "object", "description": "Fields to merge in, e.g. {\"replicas\": 3, \"image\": \"app:v2\"}."},
         "namespace": {"type": "string", "description": "Namespace (defaults to 'default')."}},
        ["kind", "name", "patch"]),
    "kubectl_rollout": _fn(
        "kubectl_rollout", "Control or inspect a deployment's rollout: status, history, undo (revert to the previous revision), or restart.",
        {"subcommand": {"type": "string", "enum": ["status", "history", "undo", "restart"], "description": "Which rollout operation to perform."},
         "name": {"type": "string", "description": "Resource name."},
         "kind": {"type": "string", "description": "Resource kind (defaults to 'deployment')."},
         "namespace": {"type": "string", "description": "Namespace (defaults to 'default')."}},
        ["subcommand", "name"]),
    "explain_resource": _fn(
        "explain_resource", "Get documentation for a Kubernetes resource type.",
        {"resource": {"type": "string", "description": "Resource kind to explain, e.g. 'pod' or 'deployment'."}},
        ["resource"]),
    "list_api_resources": _fn(
        "list_api_resources", "List the resource kinds available in the cluster.",
        {}, []),
    "port_forward": _fn(
        "port_forward", "Forward a local port to a running pod's port.",
        {"name": {"type": "string", "description": "Pod to forward to."},
         "local_port": {"type": "integer", "description": "Local port to listen on."},
         "remote_port": {"type": "integer", "description": "Port on the pod to forward to."},
         "namespace": {"type": "string", "description": "Namespace (defaults to 'default')."}},
        ["name", "local_port", "remote_port"]),
    "stop_port_forward": _fn(
        "stop_port_forward", "Stop an active port-forward session by its id.",
        {"id": {"type": "string", "description": "Id returned by port_forward."}},
        ["id"]),
    "exec_in_pod": _fn(
        "exec_in_pod", "Execute a command inside a running pod's container.",
        {"name": {"type": "string", "description": "Pod to execute in."},
         "command": {"type": "array", "items": {"type": "string"}, "description": "Command and arguments to run."},
         "namespace": {"type": "string", "description": "Namespace (defaults to 'default')."},
         "container": {"type": "string", "description": "Specific container within the pod (optional)."}},
        ["name", "command"]),
    "install_helm_chart": _fn(
        "install_helm_chart", "Install a new Helm release. Refuses if a release with this name already exists in the namespace - use upgrade_helm_chart to update it.",
        {"name": {"type": "string", "description": "Name for the new release."},
         "chart": {"type": "string", "description": "Chart name or path."},
         "namespace": {"type": "string", "description": "Target namespace (defaults to 'default')."},
         "values": {"type": "object", "description": "Values to override chart defaults (optional)."},
         "create_namespace": {"type": "boolean", "description": "Create the namespace if it doesn't exist (optional, default true)."}},
        ["name", "chart"]),
    "upgrade_helm_chart": _fn(
        "upgrade_helm_chart", "Upgrade an existing Helm release. Refuses if no release with this name exists yet - use install_helm_chart first.",
        {"name": {"type": "string", "description": "Release name."},
         "chart": {"type": "string", "description": "Chart name or path."},
         "namespace": {"type": "string", "description": "Namespace (defaults to 'default')."},
         "values": {"type": "object", "description": "Values to override chart defaults (optional)."}},
        ["name", "chart"]),
    "uninstall_helm_chart": _fn(
        "uninstall_helm_chart", "Uninstall a Helm release.",
        {"name": {"type": "string", "description": "Release name."},
         "namespace": {"type": "string", "description": "Namespace (defaults to 'default')."}},
        ["name"]),
    "helm_template_apply": _fn(
        "helm_template_apply", "Install or update a chart via template rendering + apply - unlike install_helm_chart, this upserts instead of refusing a duplicate.",
        {"name": {"type": "string", "description": "Release name."},
         "chart": {"type": "string", "description": "Chart name or path."},
         "namespace": {"type": "string", "description": "Namespace (defaults to 'default')."},
         "values": {"type": "object", "description": "Values to override chart defaults (optional)."}},
        ["name", "chart"]),
    "helm_template_uninstall": _fn(
        "helm_template_uninstall", "Remove a chart that was installed via helm_template_apply.",
        {"name": {"type": "string", "description": "Release name."},
         "namespace": {"type": "string", "description": "Namespace (defaults to 'default')."}},
        ["name"]),
    "cleanup_pods": _fn(
        "cleanup_pods", "Remove pods stuck in a terminal error state (Error, CrashLoopBackOff, Completed, Evicted).",
        {"namespace": {"type": "string", "description": "Namespace to clean up (defaults to 'default')."},
         "all_namespaces": {"type": "boolean", "description": "Clean up across every namespace (optional)."}},
        []),
    "node_management": _fn(
        "node_management", "Cordon (mark unschedulable), drain (evict pods and mark unschedulable), or uncordon a node. Drain requires confirm_drain=true.",
        {"operation": {"type": "string", "enum": ["cordon", "drain", "uncordon"], "description": "Which operation to perform."},
         "node_name": {"type": "string", "description": "Node to operate on."},
         "confirm_drain": {"type": "boolean", "description": "Required (true) to actually perform a drain (optional, default false)."}},
        ["operation", "node_name"]),
    "ping": _fn(
        "ping", "Check connectivity to the cluster.",
        {}, []),
}

_FORGE_SCHEMAS: dict[str, dict] = {
    # actions
    "actions_list": _fn(
        "actions_list", "List workflow runs for a repository.",
        {"owner": {"type": "string"}, "repo": {"type": "string"},
         "workflow_id": {"type": "string", "description": "Filter to one workflow file (optional)."}},
        ["owner", "repo"]),
    "actions_get": _fn(
        "actions_get", "Get details for one workflow run.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "run_id": {"type": "integer"}},
        ["owner", "repo", "run_id"]),
    "actions_run_trigger": _fn(
        "actions_run_trigger", "Manually trigger a workflow run.",
        {"owner": {"type": "string"}, "repo": {"type": "string"},
         "workflow_id": {"type": "string", "description": "Workflow file to trigger, e.g. 'ci.yml'."},
         "ref": {"type": "string", "description": "Branch/tag to run on (optional, defaults to 'main')."},
         "inputs": {"type": "object", "description": "Workflow input parameters (optional)."}},
        ["owner", "repo", "workflow_id"]),
    "get_job_logs": _fn(
        "get_job_logs", "Get the log output for one job within a workflow run.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "job_id": {"type": "string"}},
        ["owner", "repo", "job_id"]),
    # code quality / security / dependabot / secret protection
    "get_code_quality_finding": _fn(
        "get_code_quality_finding", "Get details for one code quality finding.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "finding_id": {"type": "string"}},
        ["owner", "repo", "finding_id"]),
    "get_code_scanning_alert": _fn(
        "get_code_scanning_alert", "Get details for one code scanning alert.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "alert_number": {"type": "integer"}},
        ["owner", "repo", "alert_number"]),
    "list_code_scanning_alerts": _fn(
        "list_code_scanning_alerts", "List code scanning alerts for a repository.",
        {"owner": {"type": "string"}, "repo": {"type": "string"},
         "state": {"type": "string", "description": "Filter by state, e.g. 'open' (optional)."}},
        ["owner", "repo"]),
    "get_dependabot_alert": _fn(
        "get_dependabot_alert", "Get details for one Dependabot alert.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "alert_number": {"type": "integer"}},
        ["owner", "repo", "alert_number"]),
    "list_dependabot_alerts": _fn(
        "list_dependabot_alerts", "List Dependabot alerts for a repository.",
        {"owner": {"type": "string"}, "repo": {"type": "string"},
         "state": {"type": "string", "description": "Filter by state, e.g. 'open' (optional)."}},
        ["owner", "repo"]),
    "get_secret_scanning_alert": _fn(
        "get_secret_scanning_alert", "Get details for one secret scanning alert.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "alert_number": {"type": "integer"}},
        ["owner", "repo", "alert_number"]),
    "list_secret_scanning_alerts": _fn(
        "list_secret_scanning_alerts", "List secret scanning alerts for a repository.",
        {"owner": {"type": "string"}, "repo": {"type": "string"},
         "state": {"type": "string", "description": "Filter by state, e.g. 'open' (optional)."}},
        ["owner", "repo"]),
    # context / copilot / organizations
    "get_me": _fn("get_me", "Get the currently authenticated user.", {}, []),
    "get_teams": _fn(
        "get_teams", "List teams in an organization.",
        {"org": {"type": "string"}}, ["org"]),
    "get_team_members": _fn(
        "get_team_members", "List members of one team.",
        {"org": {"type": "string"}, "team_slug": {"type": "string"}}, ["org", "team_slug"]),
    "assign_copilot_to_issue": _fn(
        "assign_copilot_to_issue", "Assign Copilot as a worker on an issue.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "issue_number": {"type": "integer"}},
        ["owner", "repo", "issue_number"]),
    "assign_copilot_to_issue_with_intent": _fn(
        "assign_copilot_to_issue_with_intent", "Assign Copilot to an issue with explicit guidance on what to do.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "issue_number": {"type": "integer"},
         "intent": {"type": "string", "description": "Guidance for what Copilot should do."}},
        ["owner", "repo", "issue_number", "intent"]),
    "request_copilot_review": _fn(
        "request_copilot_review", "Request a Copilot code review on a pull request.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "pull_number": {"type": "integer"}},
        ["owner", "repo", "pull_number"]),
    "search_orgs": _fn(
        "search_orgs", "Search organizations by name.",
        {"query": {"type": "string"}}, ["query"]),
    # discussions
    "list_discussions": _fn(
        "list_discussions", "List discussions in a repository.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}}, ["owner", "repo"]),
    "get_discussion": _fn(
        "get_discussion", "Get one discussion's details.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "discussion_number": {"type": "integer"}},
        ["owner", "repo", "discussion_number"]),
    "list_discussion_categories": _fn(
        "list_discussion_categories", "List a repository's discussion categories.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}}, ["owner", "repo"]),
    "get_discussion_comments": _fn(
        "get_discussion_comments", "List comments on a discussion.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "discussion_number": {"type": "integer"}},
        ["owner", "repo", "discussion_number"]),
    "discussion_comment_write": _fn(
        "discussion_comment_write", "Add a comment to a discussion.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "discussion_number": {"type": "integer"},
         "body": {"type": "string"}},
        ["owner", "repo", "discussion_number", "body"]),
    # gists
    "create_gist": _fn(
        "create_gist", "Create a new gist.",
        {"description": {"type": "string"},
         "files": {"type": "object", "description": "{filename: content}."},
         "public": {"type": "boolean", "description": "Optional, defaults to false."}},
        ["description", "files"]),
    "get_gist": _fn(
        "get_gist", "Get a gist's contents.",
        {"gist_id": {"type": "string"}}, ["gist_id"]),
    "list_gists": _fn("list_gists", "List the authenticated user's gists.", {}, []),
    "update_gist": _fn(
        "update_gist", "Update an existing gist's files and/or description.",
        {"gist_id": {"type": "string"},
         "files": {"type": "object", "description": "{filename: content} to add/overwrite (optional)."},
         "description": {"type": "string", "description": "New description (optional)."}},
        ["gist_id"]),
    # git
    "get_repository_tree": _fn(
        "get_repository_tree", "Get the full list of file paths in a repository.",
        {"owner": {"type": "string"}, "repo": {"type": "string"},
         "ref": {"type": "string", "description": "Branch/tag/sha (optional, defaults to the default branch)."}},
        ["owner", "repo"]),
    # issues
    "list_issues": _fn(
        "list_issues", "List issues in a repository.",
        {"owner": {"type": "string"}, "repo": {"type": "string"},
         "state": {"type": "string", "description": "Filter by state, e.g. 'open' (optional)."},
         "labels": {"type": "array", "items": {"type": "string"}, "description": "Only issues with all these labels (optional)."}},
        ["owner", "repo"]),
    "search_issues": _fn(
        "search_issues", "Search issues across repositories by title/body text.",
        {"query": {"type": "string"}}, ["query"]),
    "issue_read": _fn(
        "issue_read", "Get one issue's details.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "issue_number": {"type": "integer"}},
        ["owner", "repo", "issue_number"]),
    "issue_write": _fn(
        "issue_write", "Create a new issue (omit issue_number) or update an existing one (include issue_number).",
        {"owner": {"type": "string"}, "repo": {"type": "string"},
         "issue_number": {"type": "integer", "description": "Omit to create a new issue."},
         "title": {"type": "string"}, "body": {"type": "string"},
         "state": {"type": "string", "enum": ["open", "closed"]},
         "labels": {"type": "array", "items": {"type": "string"}},
         "assignees": {"type": "array", "items": {"type": "string"}},
         "type": {"type": "string", "description": "Issue type, e.g. 'Bug' (optional)."}},
        ["owner", "repo"]),
    "add_issue_comment": _fn(
        "add_issue_comment", "Add a comment to an issue.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "issue_number": {"type": "integer"},
         "body": {"type": "string"}},
        ["owner", "repo", "issue_number", "body"]),
    "get_label": _fn(
        "get_label", "Get one label's color/description.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "name": {"type": "string"}},
        ["owner", "repo", "name"]),
    "list_issue_fields": _fn(
        "list_issue_fields", "List the custom fields available on issues in this repository.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}}, ["owner", "repo"]),
    "list_issue_types": _fn(
        "list_issue_types", "List the issue types available in this repository.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}}, ["owner", "repo"]),
    "sub_issue_write": _fn(
        "sub_issue_write", "Add or remove a sub-issue relationship between two issues.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "issue_number": {"type": "integer"},
         "sub_issue_number": {"type": "integer"},
         "operation": {"type": "string", "enum": ["add", "remove"], "description": "Optional, defaults to 'add'."}},
        ["owner", "repo", "issue_number", "sub_issue_number"]),
    # labels
    "label_write": _fn(
        "label_write", "Create or update a label (pass delete=true to remove it instead).",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "name": {"type": "string"},
         "color": {"type": "string"}, "description": {"type": "string"},
         "delete": {"type": "boolean", "description": "Optional, defaults to false."}},
        ["owner", "repo", "name"]),
    "list_label": _fn(
        "list_label", "List labels in a repository.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}}, ["owner", "repo"]),
    # notifications
    "list_notifications": _fn(
        "list_notifications", "List notifications, optionally scoped to one repository.",
        {"owner": {"type": "string"}, "repo": {"type": "string"},
         "unread_only": {"type": "boolean", "description": "Optional, defaults to true."}},
        []),
    "get_notification_details": _fn(
        "get_notification_details", "Get one notification's details.",
        {"notification_id": {"type": "string"}}, ["notification_id"]),
    "dismiss_notification": _fn(
        "dismiss_notification", "Mark one notification as read.",
        {"notification_id": {"type": "string"}}, ["notification_id"]),
    "mark_all_notifications_read": _fn(
        "mark_all_notifications_read", "Mark every notification as read.", {}, []),
    "manage_notification_subscription": _fn(
        "manage_notification_subscription", "Ignore, watch, or delete the subscription for one notification's thread.",
        {"notification_id": {"type": "string"}, "action": {"type": "string", "enum": ["ignore", "watch", "delete"]}},
        ["notification_id", "action"]),
    "manage_repository_notification_subscription": _fn(
        "manage_repository_notification_subscription", "Ignore, watch, or delete the notification subscription for an entire repository.",
        {"owner": {"type": "string"}, "repo": {"type": "string"},
         "action": {"type": "string", "enum": ["ignore", "watch", "delete"]}},
        ["owner", "repo", "action"]),
    # projects
    "projects_list": _fn(
        "projects_list", "List projects owned by a user or org.",
        {"owner": {"type": "string"}}, ["owner"]),
    "projects_get": _fn(
        "projects_get", "Get one project's details.",
        {"project_id": {"type": "string"}}, ["project_id"]),
    "projects_write": _fn(
        "projects_write", "Create a new project (omit project_id) or update an existing one (include project_id).",
        {"owner": {"type": "string"}, "project_id": {"type": "string", "description": "Omit to create a new project."},
         "title": {"type": "string"}, "body": {"type": "string"}},
        ["owner"]),
    # pull requests
    "list_pull_requests": _fn(
        "list_pull_requests", "List pull requests in a repository.",
        {"owner": {"type": "string"}, "repo": {"type": "string"},
         "state": {"type": "string", "description": "Filter by state, e.g. 'open' (optional)."}},
        ["owner", "repo"]),
    "search_pull_requests": _fn(
        "search_pull_requests", "Search pull requests across repositories by title/body text.",
        {"query": {"type": "string"}}, ["query"]),
    "pull_request_read": _fn(
        "pull_request_read", "Get one pull request's details.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "pull_number": {"type": "integer"}},
        ["owner", "repo", "pull_number"]),
    "create_pull_request": _fn(
        "create_pull_request", "Open a new pull request. Both head and base branches must already exist.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "title": {"type": "string"},
         "head": {"type": "string", "description": "Branch with the changes."},
         "base": {"type": "string", "description": "Branch to merge into."},
         "body": {"type": "string"}, "draft": {"type": "boolean", "description": "Optional, defaults to false."}},
        ["owner", "repo", "title", "head", "base"]),
    "update_pull_request": _fn(
        "update_pull_request", "Update a pull request's title, body, state, or base branch.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "pull_number": {"type": "integer"},
         "title": {"type": "string"}, "body": {"type": "string"},
         "state": {"type": "string", "enum": ["open", "closed"]}, "base": {"type": "string"}},
        ["owner", "repo", "pull_number"]),
    "merge_pull_request": _fn(
        "merge_pull_request", "Merge a pull request. Refuses a draft, an already-closed/merged PR, or one with an unresolved REQUEST_CHANGES review.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "pull_number": {"type": "integer"},
         "merge_method": {"type": "string", "enum": ["merge", "squash", "rebase"], "description": "Optional, defaults to 'merge'."}},
        ["owner", "repo", "pull_number"]),
    "update_pull_request_branch": _fn(
        "update_pull_request_branch", "Sync a pull request's branch with its base branch.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "pull_number": {"type": "integer"}},
        ["owner", "repo", "pull_number"]),
    "pull_request_review_write": _fn(
        "pull_request_review_write", "Start/resume a pending review (omit event), or submit one with a decision (event=APPROVE/REQUEST_CHANGES/COMMENT).",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "pull_number": {"type": "integer"},
         "event": {"type": "string", "enum": ["PENDING", "APPROVE", "REQUEST_CHANGES", "COMMENT"],
                   "description": "Omit or 'PENDING' to start/resume a pending review without submitting it."},
         "body": {"type": "string"}},
        ["owner", "repo", "pull_number"]),
    "add_comment_to_pending_review": _fn(
        "add_comment_to_pending_review", "Attach a line comment to the currently-open pending review on a pull request. Requires a pending review to already be open.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "pull_number": {"type": "integer"},
         "path": {"type": "string"}, "line": {"type": "integer"}, "body": {"type": "string"}},
        ["owner", "repo", "pull_number", "path", "line", "body"]),
    "add_reply_to_pull_request_comment": _fn(
        "add_reply_to_pull_request_comment", "Reply to an existing pull request review comment.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "pull_number": {"type": "integer"},
         "comment_id": {"type": "integer"}, "body": {"type": "string"}},
        ["owner", "repo", "pull_number", "comment_id", "body"]),
    # repositories
    "create_repository": _fn(
        "create_repository", "Create a new repository owned by the authenticated user.",
        {"name": {"type": "string"}, "description": {"type": "string"},
         "private": {"type": "boolean", "description": "Optional, defaults to false."}},
        ["name"]),
    "fork_repository": _fn(
        "fork_repository", "Fork a repository into the authenticated user's account.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}}, ["owner", "repo"]),
    "search_repositories": _fn(
        "search_repositories", "Search repositories by name.",
        {"query": {"type": "string"}}, ["query"]),
    "get_file_contents": _fn(
        "get_file_contents", "Read a file's contents from a repository.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "path": {"type": "string"},
         "ref": {"type": "string", "description": "Branch/tag/sha (optional)."}},
        ["owner", "repo", "path"]),
    "create_or_update_file": _fn(
        "create_or_update_file", "Create a new file or overwrite an existing one with a commit message.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "path": {"type": "string"},
         "content": {"type": "string"}, "message": {"type": "string", "description": "Commit message."},
         "branch": {"type": "string", "description": "Optional, defaults to the default branch."}},
        ["owner", "repo", "path", "content", "message"]),
    "delete_file": _fn(
        "delete_file", "Delete an existing file with a commit message.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "path": {"type": "string"},
         "message": {"type": "string", "description": "Commit message."},
         "branch": {"type": "string", "description": "Optional, defaults to the default branch."}},
        ["owner", "repo", "path", "message"]),
    "push_files": _fn(
        "push_files", "Push several file changes to a branch in a single commit.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "branch": {"type": "string"},
         "files": {"type": "object", "description": "{path: content}."},
         "message": {"type": "string", "description": "Commit message."}},
        ["owner", "repo", "branch", "files", "message"]),
    "create_branch": _fn(
        "create_branch", "Create a new branch from an existing one.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "branch": {"type": "string"},
         "from_branch": {"type": "string", "description": "Optional, defaults to the default branch."}},
        ["owner", "repo", "branch"]),
    "list_branches": _fn(
        "list_branches", "List branches in a repository.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}}, ["owner", "repo"]),
    "get_commit": _fn(
        "get_commit", "Get one commit's details.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "sha": {"type": "string"}},
        ["owner", "repo", "sha"]),
    "list_commits": _fn(
        "list_commits", "List commits in a repository.",
        {"owner": {"type": "string"}, "repo": {"type": "string"},
         "branch": {"type": "string", "description": "Optional, defaults to the default branch."}},
        ["owner", "repo"]),
    "search_commits": _fn(
        "search_commits", "Search commits in a repository by message text.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "query": {"type": "string"}},
        ["owner", "repo", "query"]),
    "search_code": _fn(
        "search_code", "Search file contents/paths across every repository.",
        {"query": {"type": "string"}}, ["query"]),
    "get_tag": _fn(
        "get_tag", "Get the commit a tag points to.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "tag": {"type": "string"}},
        ["owner", "repo", "tag"]),
    "list_tags": _fn(
        "list_tags", "List tags in a repository.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}}, ["owner", "repo"]),
    "get_latest_release": _fn(
        "get_latest_release", "Get the most recent release.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}}, ["owner", "repo"]),
    "get_release_by_tag": _fn(
        "get_release_by_tag", "Get one release by its tag.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}, "tag": {"type": "string"}},
        ["owner", "repo", "tag"]),
    "list_releases": _fn(
        "list_releases", "List releases in a repository.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}}, ["owner", "repo"]),
    "list_repository_collaborators": _fn(
        "list_repository_collaborators", "List a repository's collaborators and their roles.",
        {"owner": {"type": "string"}, "repo": {"type": "string"}}, ["owner", "repo"]),
}

_PACKAGE_REGISTRY_SCHEMAS: dict[str, dict] = {
    "init": _fn(
        "init", "Create a new package.json for the project. Refuses if one already exists.",
        {"name": {"type": "string", "description": "Package name (optional, defaults to 'my-project')."},
         "version": {"type": "string", "description": "Initial version (optional, defaults to '1.0.0')."}},
        []),
    "pkg": _fn(
        "pkg", "Get, set, or delete a field in package.json.",
        {"operation": {"type": "string", "enum": ["get", "set", "delete"]},
         "field": {"type": "string", "description": "Field name (optional for get)."},
         "value": {"description": "New value (for set)."}},
        ["operation"]),
    "ci": _fn(
        "ci", "Clean install every dependency exactly as locked. Refuses if no lockfile is present.",
        {}, []),
    "link": _fn(
        "link", "Symlink a local package into the project for development.",
        {"package": {"type": "string"}}, ["package"]),
    "install": _fn(
        "install", "Install one or more packages into the project. Each must already exist in the registry.",
        {"packages": {"type": "array", "items": {"type": "string"},
                      "description": "Package names, optionally as 'name@version' (defaults to latest)."},
         "dev": {"type": "boolean", "description": "Install as a devDependency (optional, default false)."}},
        ["packages"]),
    "uninstall": _fn(
        "uninstall", "Remove one or more installed packages from the project.",
        {"packages": {"type": "array", "items": {"type": "string"}}}, ["packages"]),
    "update": _fn(
        "update", "Upgrade installed packages to their latest registry version.",
        {"packages": {"type": "array", "items": {"type": "string"},
                      "description": "Specific packages to update (optional, defaults to all installed)."}},
        []),
    "ls": _fn("ls", "List installed packages.", {}, []),
    "outdated": _fn("outdated", "List installed packages that have a newer registry version available.", {}, []),
    "prune": _fn(
        "prune", "Remove installed packages that are no longer listed in package.json.",
        {}, []),
    "dedupe": _fn("dedupe", "Eliminate duplicate dependencies in the install tree.", {}, []),
    "fund": _fn("fund", "List funding information for installed dependencies.", {}, []),
    "explain": _fn(
        "explain", "Show why a package is in the dependency tree (direct vs transitive).",
        {"package": {"type": "string"}}, ["package"]),
    "sbom": _fn(
        "sbom", "Generate a Software Bill of Materials for installed packages.",
        {"format": {"type": "string", "enum": ["cyclonedx", "spdx"], "description": "Optional, defaults to 'cyclonedx'."}},
        []),
    "query": _fn(
        "query", "Filter installed packages by a name selector.",
        {"selector": {"type": "string"}}, ["selector"]),
    "run-script": _fn(
        "run-script", "Run a script defined in package.json. Refuses if the script isn't defined.",
        {"name": {"type": "string"}}, ["name"]),
    "audit": _fn(
        "audit", "Scan installed packages for known vulnerabilities, optionally auto-fixing by upgrading.",
        {"fix": {"type": "boolean", "description": "Automatically upgrade vulnerable packages where a safe version exists (optional, default false)."}},
        []),
    "doctor": _fn("doctor", "Diagnose the local npm/node environment.", {}, []),
    "ping": _fn("ping", "Check connectivity to the registry.", {}, []),
    "whoami": _fn("whoami", "Show the currently authenticated registry user.", {}, []),
    "token": _fn(
        "token", "List, create, or revoke access tokens.",
        {"operation": {"type": "string", "enum": ["list", "create", "revoke"]},
         "token_id": {"type": "string", "description": "Required for revoke."}},
        ["operation"]),
    "access": _fn(
        "access", "Get or set a published package's access level (public/restricted).",
        {"operation": {"type": "string", "enum": ["get", "set"]}, "package": {"type": "string"},
         "level": {"type": "string", "enum": ["public", "restricted"], "description": "Required for set."}},
        ["operation", "package"]),
    "owner": _fn(
        "owner", "List, add, or remove owners of a published package.",
        {"operation": {"type": "string", "enum": ["ls", "add", "rm"]}, "package": {"type": "string"},
         "user": {"type": "string", "description": "Required for add/rm."}},
        ["operation", "package"]),
    "dist-tag": _fn(
        "dist-tag", "List, add, or remove a dist-tag (e.g. 'latest', 'beta') on a published package.",
        {"operation": {"type": "string", "enum": ["ls", "add", "rm"]}, "package": {"type": "string"},
         "tag": {"type": "string", "description": "Required for add/rm."},
         "version": {"type": "string", "description": "Required for add."}},
        ["operation", "package"]),
    "profile": _fn(
        "profile", "View or update the authenticated user's account profile.",
        {"operation": {"type": "string", "enum": ["view", "set"], "description": "Optional, defaults to 'view'."},
         "field": {"type": "string", "description": "Required for set."},
         "value": {"type": "string", "description": "Required for set."}},
        []),
    "config": _fn(
        "config", "Get, set, or delete an npm configuration value.",
        {"operation": {"type": "string", "enum": ["get", "set", "delete"]},
         "key": {"type": "string", "description": "Optional for get (omit to get all)."},
         "value": {"type": "string", "description": "Required for set."}},
        ["operation"]),
    "cache": _fn(
        "cache", "Verify, clean, or list the local package cache.",
        {"operation": {"type": "string", "enum": ["verify", "clean", "ls"], "description": "Optional, defaults to 'verify'."}},
        []),
    "publish": _fn(
        "publish", "Publish a new package version to the registry. Refuses if that exact version is already published.",
        {"name": {"type": "string"}, "version": {"type": "string"},
         "files": {"type": "array", "items": {"type": "string"}, "description": "Files included in the published package (optional)."}},
        ["name", "version"]),
    "unpublish": _fn(
        "unpublish", "Remove a published version from the registry.",
        {"package": {"type": "string"}, "version": {"type": "string"}}, ["package", "version"]),
    "deprecate": _fn(
        "deprecate", "Mark (or un-mark) a published version as deprecated.",
        {"package": {"type": "string"}, "version": {"type": "string"},
         "message": {"type": "string", "description": "Deprecation message (optional)."},
         "undo": {"type": "boolean", "description": "Remove the deprecation instead (optional, default false)."}},
        ["package", "version"]),
    "version": _fn(
        "version", "Bump the current project's own version (patch/minor/major).",
        {"bump": {"type": "string", "enum": ["patch", "minor", "major"], "description": "Optional, defaults to 'patch'."}},
        []),
    "pack": _fn("pack", "Show which files would be included if the project were published now.", {}, []),
    "view": _fn(
        "view", "View a published package's registry metadata.",
        {"package": {"type": "string"}, "field": {"type": "string", "description": "Only return this field (optional)."}},
        ["package"]),
    "search": _fn(
        "search", "Search the registry for packages by name.",
        {"query": {"type": "string"}}, ["query"]),
    "bugs": _fn(
        "bugs", "Get a published package's bug tracker URL.",
        {"package": {"type": "string"}}, ["package"]),
    "repo": _fn(
        "repo", "Get a published package's repository URL.",
        {"package": {"type": "string"}}, ["package"]),
    "docs": _fn(
        "docs", "Get a published package's documentation URL.",
        {"package": {"type": "string"}}, ["package"]),
    "diff": _fn(
        "diff", "Show which files were added/removed between two published versions of a package.",
        {"package": {"type": "string"}, "from_version": {"type": "string"}, "to_version": {"type": "string"}},
        ["package", "from_version", "to_version"]),
}

_TERRAFORM_SCHEMAS: dict[str, dict] = {
    "search_providers": _fn(
        "search_providers", "Search the public Terraform Registry for providers.",
        {"query": {"type": "string"}}, ["query"]),
    "get_provider_details": _fn(
        "get_provider_details", "Get documentation for a public provider.",
        {"namespace": {"type": "string"}, "name": {"type": "string"},
         "version": {"type": "string", "description": "Optional, defaults to latest."}},
        ["namespace", "name"]),
    "get_latest_provider_version": _fn(
        "get_latest_provider_version", "Get the latest published version of a public provider.",
        {"namespace": {"type": "string"}, "name": {"type": "string"}}, ["namespace", "name"]),
    "get_provider_capabilities": _fn(
        "get_provider_capabilities", "Get the capabilities a public provider declares.",
        {"namespace": {"type": "string"}, "name": {"type": "string"}}, ["namespace", "name"]),
    "search_modules": _fn(
        "search_modules", "Search the public Terraform Registry for modules.",
        {"query": {"type": "string"}}, ["query"]),
    "get_module_details": _fn(
        "get_module_details", "Get documentation for a public module.",
        {"namespace": {"type": "string"}, "name": {"type": "string"}, "provider": {"type": "string"}},
        ["namespace", "name", "provider"]),
    "get_latest_module_version": _fn(
        "get_latest_module_version", "Get the latest published version of a public module.",
        {"namespace": {"type": "string"}, "name": {"type": "string"}, "provider": {"type": "string"}},
        ["namespace", "name", "provider"]),
    "search_policies": _fn(
        "search_policies", "Search the public Terraform Registry for Sentinel policies.",
        {"query": {"type": "string"}}, ["query"]),
    "get_policy_details": _fn(
        "get_policy_details", "Get documentation for a public Sentinel policy.",
        {"namespace": {"type": "string"}, "name": {"type": "string"}}, ["namespace", "name"]),
    "list_terraform_orgs": _fn("list_terraform_orgs", "List Terraform Cloud/Enterprise organizations.", {}, []),
    "list_terraform_projects": _fn(
        "list_terraform_projects", "List projects in an organization.",
        {"org": {"type": "string"}}, ["org"]),
    "create_project": _fn(
        "create_project", "Create a new project in an organization.",
        {"org": {"type": "string"}, "name": {"type": "string"}}, ["org", "name"]),
    "delete_project": _fn(
        "delete_project", "Delete a project. Refuses while it still has workspaces.",
        {"org": {"type": "string"}, "project_id": {"type": "string"}}, ["org", "project_id"]),
    "list_teams": _fn(
        "list_teams", "List teams in an organization.",
        {"org": {"type": "string"}}, ["org"]),
    "create_team": _fn(
        "create_team", "Create a new team in an organization.",
        {"org": {"type": "string"}, "name": {"type": "string"}}, ["org", "name"]),
    "get_token_permissions": _fn(
        "get_token_permissions", "Get the permissions granted by an API token.",
        {"token": {"type": "string"}}, ["token"]),
    "list_workspaces": _fn(
        "list_workspaces", "List workspaces in an organization.",
        {"org": {"type": "string"}}, ["org"]),
    "get_workspace_details": _fn(
        "get_workspace_details", "Get a workspace's details.",
        {"org": {"type": "string"}, "workspace_id": {"type": "string"}}, ["org", "workspace_id"]),
    "create_workspace": _fn(
        "create_workspace", "Create a new workspace.",
        {"org": {"type": "string"}, "name": {"type": "string"},
         "project_id": {"type": "string", "description": "Optional."},
         "terraform_version": {"type": "string", "description": "Optional, defaults to a recent version."}},
        ["org", "name"]),
    "update_workspace": _fn(
        "update_workspace", "Update a workspace's name or Terraform version.",
        {"org": {"type": "string"}, "workspace_id": {"type": "string"},
         "name": {"type": "string"}, "terraform_version": {"type": "string"}},
        ["org", "workspace_id"]),
    "delete_workspace_safely": _fn(
        "delete_workspace_safely", "Delete a workspace. Refuses while it's locked.",
        {"org": {"type": "string"}, "workspace_id": {"type": "string"}}, ["org", "workspace_id"]),
    "force_unlock_workspace": _fn(
        "force_unlock_workspace", "Forcibly unlock a workspace. Refuses if it isn't locked.",
        {"org": {"type": "string"}, "workspace_id": {"type": "string"}}, ["org", "workspace_id"]),
    "create_workspace_tags": _fn(
        "create_workspace_tags", "Add tags to a workspace.",
        {"org": {"type": "string"}, "workspace_id": {"type": "string"},
         "tags": {"type": "array", "items": {"type": "string"}}},
        ["org", "workspace_id", "tags"]),
    "read_workspace_tags": _fn(
        "read_workspace_tags", "List a workspace's tags.",
        {"org": {"type": "string"}, "workspace_id": {"type": "string"}}, ["org", "workspace_id"]),
    "list_workspace_variables": _fn(
        "list_workspace_variables", "List a workspace's variables.",
        {"org": {"type": "string"}, "workspace_id": {"type": "string"}}, ["org", "workspace_id"]),
    "create_workspace_variable": _fn(
        "create_workspace_variable", "Add a variable to a workspace. Refuses a duplicate key.",
        {"org": {"type": "string"}, "workspace_id": {"type": "string"}, "key": {"type": "string"},
         "value": {"type": "string"}, "category": {"type": "string", "enum": ["terraform", "env"], "description": "Optional, defaults to 'terraform'."},
         "sensitive": {"type": "boolean", "description": "Optional, default false."}},
        ["org", "workspace_id", "key", "value"]),
    "update_workspace_variable": _fn(
        "update_workspace_variable", "Update an existing workspace variable's key or value.",
        {"org": {"type": "string"}, "workspace_id": {"type": "string"}, "variable_id": {"type": "string"},
         "value": {"type": "string"}, "key": {"type": "string"}},
        ["org", "workspace_id", "variable_id"]),
    "list_variable_sets": _fn(
        "list_variable_sets", "List variable sets in an organization.",
        {"org": {"type": "string"}}, ["org"]),
    "create_variable_set": _fn(
        "create_variable_set", "Create a new variable set.",
        {"org": {"type": "string"}, "name": {"type": "string"}}, ["org", "name"]),
    "create_variable_in_variable_set": _fn(
        "create_variable_in_variable_set", "Add a variable to a variable set.",
        {"org": {"type": "string"}, "varset_id": {"type": "string"}, "key": {"type": "string"}, "value": {"type": "string"}},
        ["org", "varset_id", "key", "value"]),
    "delete_variable_in_variable_set": _fn(
        "delete_variable_in_variable_set", "Remove a variable from a variable set.",
        {"org": {"type": "string"}, "varset_id": {"type": "string"}, "key": {"type": "string"}},
        ["org", "varset_id", "key"]),
    "attach_variable_set_to_workspaces": _fn(
        "attach_variable_set_to_workspaces", "Attach a variable set to one or more workspaces.",
        {"org": {"type": "string"}, "varset_id": {"type": "string"},
         "workspace_ids": {"type": "array", "items": {"type": "string"}}},
        ["org", "varset_id", "workspace_ids"]),
    "detach_variable_set_from_workspaces": _fn(
        "detach_variable_set_from_workspaces", "Detach a variable set from one or more workspaces.",
        {"org": {"type": "string"}, "varset_id": {"type": "string"},
         "workspace_ids": {"type": "array", "items": {"type": "string"}}},
        ["org", "varset_id", "workspace_ids"]),
    "list_workspace_policy_sets": _fn(
        "list_workspace_policy_sets", "List policy sets attached to a workspace.",
        {"org": {"type": "string"}, "workspace_id": {"type": "string"}}, ["org", "workspace_id"]),
    "attach_policy_set_to_workspaces": _fn(
        "attach_policy_set_to_workspaces", "Attach a policy set to one or more workspaces.",
        {"org": {"type": "string"}, "policyset_id": {"type": "string"},
         "workspace_ids": {"type": "array", "items": {"type": "string"}}},
        ["org", "policyset_id", "workspace_ids"]),
    "create_run": _fn(
        "create_run", "Start a new plan run on a workspace. Refuses if the workspace is already locked by a pending run.",
        {"org": {"type": "string"}, "workspace_id": {"type": "string"}, "message": {"type": "string"}},
        ["org", "workspace_id"]),
    "list_runs": _fn(
        "list_runs", "List runs on a workspace.",
        {"org": {"type": "string"}, "workspace_id": {"type": "string"}}, ["org", "workspace_id"]),
    "get_run_details": _fn(
        "get_run_details", "Get a run's details.",
        {"run_id": {"type": "string"}}, ["run_id"]),
    "get_run_comments": _fn(
        "get_run_comments", "List comments on a run.",
        {"run_id": {"type": "string"}}, ["run_id"]),
    "action_run": _fn(
        "action_run", "Apply, discard, or cancel a run. Applying refuses unless the run is in a plannable-to-apply state.",
        {"run_id": {"type": "string"}, "action": {"type": "string", "enum": ["apply", "discard", "cancel"]}},
        ["run_id", "action"]),
    "get_plan_details": _fn(
        "get_plan_details", "Get a plan's status.",
        {"plan_id": {"type": "string"}}, ["plan_id"]),
    "get_plan_json_output": _fn(
        "get_plan_json_output", "Get a plan's structured JSON output.",
        {"plan_id": {"type": "string"}}, ["plan_id"]),
    "get_plan_logs": _fn(
        "get_plan_logs", "Get a plan's log output.",
        {"plan_id": {"type": "string"}}, ["plan_id"]),
    "get_apply_details": _fn(
        "get_apply_details", "Get an apply's status.",
        {"apply_id": {"type": "string"}}, ["apply_id"]),
    "get_apply_logs": _fn(
        "get_apply_logs", "Get an apply's log output.",
        {"apply_id": {"type": "string"}}, ["apply_id"]),
    "list_state_versions": _fn(
        "list_state_versions", "List state versions for a workspace.",
        {"org": {"type": "string"}, "workspace_id": {"type": "string"}}, ["org", "workspace_id"]),
    "get_state_version": _fn(
        "get_state_version", "Get a specific (or the latest) state version for a workspace.",
        {"org": {"type": "string"}, "workspace_id": {"type": "string"},
         "serial": {"type": "integer", "description": "Optional, defaults to the latest."}},
        ["org", "workspace_id"]),
    "list_stacks": _fn(
        "list_stacks", "List stacks in an organization.",
        {"org": {"type": "string"}}, ["org"]),
    "get_stack_details": _fn(
        "get_stack_details", "Get a stack's details.",
        {"org": {"type": "string"}, "stack_id": {"type": "string"}}, ["org", "stack_id"]),
    "create_no_code_workspace": _fn(
        "create_no_code_workspace", "Create a new workspace from a no-code-ready module.",
        {"org": {"type": "string"}, "name": {"type": "string"}, "module_source": {"type": "string"}},
        ["org", "name", "module_source"]),
    "get_sentinel_mock": _fn(
        "get_sentinel_mock", "Get mock input data for testing a Sentinel policy.",
        {"policy_id": {"type": "string"}}, ["policy_id"]),
    "search_private_modules": _fn(
        "search_private_modules", "Search an organization's private module registry.",
        {"org": {"type": "string"}, "query": {"type": "string"}}, ["org", "query"]),
    "get_private_module_details": _fn(
        "get_private_module_details", "Get documentation for a private module.",
        {"org": {"type": "string"}, "namespace": {"type": "string"}, "name": {"type": "string"}, "provider": {"type": "string"}},
        ["org", "namespace", "name", "provider"]),
    "search_private_providers": _fn(
        "search_private_providers", "Search an organization's private provider registry.",
        {"org": {"type": "string"}, "query": {"type": "string"}}, ["org", "query"]),
    "get_private_provider_details": _fn(
        "get_private_provider_details", "Get documentation for a private provider.",
        {"org": {"type": "string"}, "namespace": {"type": "string"}, "name": {"type": "string"}},
        ["org", "namespace", "name"]),
}

_DATABASE_SCHEMAS: dict[str, dict] = {
    "list_schemas": _fn("list_schemas", "List all schemas in the database.", {}, []),
    "list_objects": _fn(
        "list_objects", "List objects of a given type in a schema.",
        {"schema_name": {"type": "string"},
         "object_type": {"type": "string", "enum": ["table", "view", "sequence", "extension"], "description": "Optional, defaults to 'table'."}},
        ["schema_name"]),
    "get_object_details": _fn(
        "get_object_details", "Show detailed information about a database object.",
        {"schema_name": {"type": "string"}, "object_name": {"type": "string"},
         "object_type": {"type": "string", "enum": ["table", "view", "sequence", "extension"], "description": "Optional, defaults to 'table'."}},
        ["schema_name", "object_name"]),
    "explain_query": _fn(
        "explain_query", "Explain a SQL query's execution plan, optionally with real execution stats or hypothetical indexes.",
        {"sql": {"type": "string"},
         "analyze": {"type": "boolean", "description": "Run the query for real statistics instead of estimates (optional, default false)."},
         "hypothetical_indexes": {"type": "array", "description": "Indexes to simulate, e.g. [{\"table\":\"users\",\"columns\":[\"email\"]}] (optional).",
                                   "items": {"type": "object"}}},
        ["sql"]),
    "execute_sql": _fn(
        "execute_sql", "Execute a SQL statement. Refuses write statements while the session is in restricted (read-only) mode.",
        {"sql": {"type": "string"}}, ["sql"]),
    "analyze_workload_indexes": _fn(
        "analyze_workload_indexes", "Analyze frequently executed queries in the database and recommend optimal indexes.",
        {"max_index_size_mb": {"type": "integer", "description": "Optional, defaults to 10000."},
         "method": {"type": "string", "enum": ["dta", "llm"], "description": "Optional, defaults to 'dta'."}},
        []),
    "analyze_query_indexes": _fn(
        "analyze_query_indexes", "Analyze a list of (up to 10) SQL queries and recommend optimal indexes.",
        {"queries": {"type": "array", "items": {"type": "string"}},
         "max_index_size_mb": {"type": "integer", "description": "Optional, defaults to 10000."},
         "method": {"type": "string", "enum": ["dta", "llm"], "description": "Optional, defaults to 'dta'."}},
        ["queries"]),
    "analyze_db_health": _fn(
        "analyze_db_health", "Analyze database health (index, connection, vacuum, sequence, replication, buffer, constraint, or 'all').",
        {"health_type": {"type": "string", "description": "Comma-separated list of checks, or 'all' (optional, default 'all')."}},
        []),
    "get_top_queries": _fn(
        "get_top_queries", "Report the slowest or most resource-intensive queries.",
        {"sort_by": {"type": "string", "enum": ["total_time", "mean_time", "resources"], "description": "Optional, defaults to 'resources'."},
         "limit": {"type": "integer", "description": "Optional, defaults to 10."}},
        []),
}

_PROJECT_ID_PARAMS = {
    "projectSlug": {"type": "string", "description": "The project slug from list_followed_projects (e.g. 'gh/organization/project')."},
    "projectURL": {"type": "string", "description": "A CircleCI project/pipeline/workflow/job URL to parse the project slug from."},
    "workspaceRoot": {"type": "string", "description": "The absolute path to the local project workspace root."},
    "gitRemoteURL": {"type": "string", "description": "The git remote URL of the local checkout."},
}

_CI_PIPELINE_SCHEMAS: dict[str, dict] = {
    "list_followed_projects": _fn(
        "list_followed_projects", "List all CircleCI projects the user follows.", {}, []),
    "get_latest_pipeline_status": _fn(
        "get_latest_pipeline_status",
        "Get the status of the latest pipeline for a project. Identify the project via projectSlug+branch, "
        "projectURL, or workspaceRoot+gitRemoteURL+branch.",
        {**_PROJECT_ID_PARAMS, "branch": {"type": "string", "description": "Required when identifying by projectSlug or workspaceRoot/gitRemoteURL."}},
        []),
    "get_build_failure_logs": _fn(
        "get_build_failure_logs",
        "Retrieve failure logs for a project's latest failed build. Identify the project via projectSlug+branch, "
        "projectURL, or workspaceRoot+gitRemoteURL+branch.",
        {**_PROJECT_ID_PARAMS, "branch": {"type": "string", "description": "Required when identifying by projectSlug or workspaceRoot/gitRemoteURL."},
         "outputDir": {"type": "string", "description": "Optional directory to write full logs to instead of returning them inline."}},
        []),
    "get_job_test_results": _fn(
        "get_job_test_results",
        "Get test result metadata for a project's jobs, optionally filtered by pass/fail. Identify the project via "
        "projectSlug+branch, projectURL, or workspaceRoot+gitRemoteURL+branch.",
        {**_PROJECT_ID_PARAMS, "branch": {"type": "string", "description": "Required when identifying by projectSlug or workspaceRoot/gitRemoteURL."},
         "filterByTestsResult": {"type": "string", "enum": ["failure", "success"], "description": "Optional test result filter."}},
        []),
    "find_flaky_tests": _fn(
        "find_flaky_tests",
        "List flaky tests for a project. Identify the project via projectSlug, projectURL, or "
        "workspaceRoot+gitRemoteURL (no branch needed).",
        {k: v for k, v in _PROJECT_ID_PARAMS.items()},
        []),
    "list_artifacts": _fn(
        "list_artifacts",
        "List artifacts produced by a project's job. Identify the project via projectSlug+branch, projectURL, or "
        "workspaceRoot+gitRemoteURL+branch.",
        {**_PROJECT_ID_PARAMS, "branch": {"type": "string", "description": "Required when identifying by projectSlug or workspaceRoot/gitRemoteURL."}},
        []),
    "config_helper": _fn(
        "config_helper", "Analyze and validate a CircleCI config.yml's contents.",
        {"configFile": {"type": "string", "description": "The full contents of the .circleci/config.yml file."}},
        ["configFile"]),
    "run_pipeline": _fn(
        "run_pipeline",
        "Trigger a new CircleCI pipeline. Identify the project via projectSlug+branch, projectURL, or "
        "workspaceRoot+gitRemoteURL+branch. If the project has multiple pipeline definitions, pipelineChoiceName "
        "is required.",
        {**_PROJECT_ID_PARAMS, "branch": {"type": "string", "description": "Required when identifying by projectSlug or workspaceRoot/gitRemoteURL."},
         "pipelineChoiceName": {"type": "string", "description": "Which pipeline definition to run, if the project has more than one."},
         "configContent": {"type": "string", "description": "Optional CircleCI config content to override the default."}},
        []),
    "rerun_workflow": _fn(
        "rerun_workflow", "Rerun a workflow from the start or from its failed job.",
        {"workflowId": {"type": "string", "description": "The UUID of the workflow to rerun."},
         "workflowURL": {"type": "string", "description": "The URL of the workflow to rerun (alternative to workflowId)."},
         "fromFailed": {"type": "boolean", "description": "Rerun from the failed job instead of from the start (optional)."}},
        []),
    "run_rollback_pipeline": _fn(
        "run_rollback_pipeline", "Run a rollback pipeline for a component in an environment.",
        {"projectSlug": {"type": "string", "description": "The project slug (alternative to projectID)."},
         "projectID": {"type": "string", "description": "The project's UUID (alternative to projectSlug)."},
         "environmentName": {"type": "string"}, "componentName": {"type": "string"},
         "currentVersion": {"type": "string"}, "targetVersion": {"type": "string"}, "namespace": {"type": "string"},
         "reason": {"type": "string", "description": "Optional reason for the rollback."},
         "parameters": {"type": "object", "description": "Optional extra rollback pipeline parameters."}},
        ["environmentName", "componentName", "currentVersion", "targetVersion", "namespace"]),
    "list_component_versions": _fn(
        "list_component_versions",
        "List versions of a component in an environment. Omitting environmentID lists available environments; "
        "omitting componentID (with environmentID given) lists available components.",
        {"projectSlug": {"type": "string", "description": "The project slug (alternative to projectID)."},
         "projectID": {"type": "string", "description": "The project's UUID (alternative to projectSlug)."},
         "orgID": {"type": "string", "description": "Optional, resolved from the project if omitted."},
         "environmentID": {"type": "string"}, "componentID": {"type": "string"}},
        []),
    "download_usage_api_data": _fn(
        "download_usage_api_data", "Download usage data from the CircleCI Usage API for an organization and date range.",
        {"orgId": {"type": "string"}, "outputDir": {"type": "string", "description": "Directory to save the usage CSV to."},
         "startDate": {"type": "string", "description": "Optional, e.g. '2026-01-01' or '5 days ago'."},
         "endDate": {"type": "string", "description": "Optional."},
         "jobId": {"type": "string", "description": "Optional, for resuming a previously started export job."}},
        ["orgId", "outputDir"]),
    "find_underused_resource_classes": _fn(
        "find_underused_resource_classes",
        "Analyze a usage data CSV (from download_usage_api_data) to find jobs/resource classes below a CPU/RAM "
        "usage threshold.",
        {"csvFilePath": {"type": "string", "description": "Path to a usage data CSV file (or array of paths)."},
         "threshold": {"type": "number", "description": "Usage percentage threshold, optional, default 40."}},
        ["csvFilePath"]),
}

_BUILD_TOOLS_SCHEMAS: dict[str, dict] = {
    "nx_docs": _fn(
        "nx_docs", "Returns documentation sections relevant to a query. Always use this before answering "
        "questions about Nx rather than assuming knowledge about it.",
        {"userQuery": {"type": "string"}}, ["userQuery"]),
    "nx_available_plugins": _fn(
        "nx_available_plugins", "List available Nx plugins from the core team and the local workspace.", {}, []),
    "nx_workspace": _fn(
        "nx_workspace", "Return the Nx project graph and nx.json workspace configuration.",
        {"filter": {"type": "string", "description": "Optional. Filter which projects to include, e.g. project names, glob patterns, or tag:X."},
         "select": {"type": "string", "description": "Optional dot-notation path to select specific properties."},
         "pageToken": {"type": "integer", "description": "Optional pagination token."}},
        []),
    "nx_workspace_path": _fn(
        "nx_workspace_path", "Return the path to the Nx workspace root.", {}, []),
    "nx_project_details": _fn(
        "nx_project_details", "Return the project configuration (targets, tags, dependencies) for a specific Nx project.",
        {"projectName": {"type": "string"},
         "select": {"type": "string", "description": "Optional dot-notation path, e.g. 'targets.build'."},
         "pageToken": {"type": "integer", "description": "Optional pagination token."}},
        ["projectName"]),
    "nx_generators": _fn(
        "nx_generators", "List all available Nx generators, both plugin-provided and local workspace generators.", {}, []),
    "nx_generator_schema": _fn(
        "nx_generator_schema", "Return the full JSON schema (options, types, defaults) for a specific Nx generator.",
        {"generatorName": {"type": "string", "description": "Use the name from nx_generators."}}, ["generatorName"]),
    "nx_visualize_graph": _fn(
        "nx_visualize_graph", "Visualize the Nx project graph or task graph. 'project' requires projectName; "
        "'project-task' requires both projectName and taskName; 'full-project-graph' requires neither.",
        {"visualizationType": {"type": "string", "enum": ["project", "project-task", "full-project-graph"]},
         "projectName": {"type": "string", "description": "Required for 'project' and 'project-task'."},
         "taskName": {"type": "string", "description": "Required for 'project-task'."}},
        ["visualizationType"]),
    "nx_current_running_tasks_details": _fn(
        "nx_current_running_tasks_details", "List currently running (or recently stopped) Nx CLI tasks.", {}, []),
    "nx_current_running_task_output": _fn(
        "nx_current_running_task_output", "Return the terminal output for a specific currently-running (or recently run) task.",
        {"taskId": {"type": "string"}, "pageToken": {"type": "integer", "description": "Optional pagination token."}},
        ["taskId"]),
    "ci_information": _fn(
        "ci_information", "Retrieve CI pipeline execution information from Nx Cloud for a branch (defaults to the current branch).",
        {"url": {"type": "string", "description": "Optional Nx Cloud URL to resolve instead of branch."},
         "branch": {"type": "string", "description": "Optional, defaults to the current git branch."},
         "select": {"type": "string", "description": "Optional comma-separated field names to select."},
         "pageToken": {"type": "integer", "description": "Optional pagination token."}},
        []),
    "ci_task_output": _fn(
        "ci_task_output", "Retrieve the terminal output (logs) for a CI task.",
        {"taskId": {"type": "string", "description": "e.g. 'myapp:build'."},
         "runId": {"type": "string", "description": "Optional, fetches logs directly from this run if given."},
         "url": {"type": "string", "description": "Optional Nx Cloud URL to resolve the run from."},
         "branch": {"type": "string", "description": "Optional, defaults to the current git branch."},
         "pageToken": {"type": "integer", "description": "Optional pagination token."}},
        ["taskId"]),
    "update_self_healing_fix": _fn(
        "update_self_healing_fix", "Apply or reject a self-healing CI fix suggested by Nx Cloud. Identify the fix "
        "via aiFixId, shortLink, or branch (defaults to the current branch).",
        {"aiFixId": {"type": "string", "description": "Direct AI fix ID to apply or reject."},
         "shortLink": {"type": "string", "description": "Human-readable short link for the fix."},
         "branch": {"type": "string", "description": "Optional, defaults to the current git branch."},
         "action": {"type": "string", "enum": ["APPLY", "REJECT", "RERUN_ENVIRONMENT_STATE"]}},
        ["action"]),
}

_CODE_INTEL_SCHEMAS: dict[str, dict] = {
    "definition": _fn(
        "definition", "Read the source code definition of a symbol (function, type, constant, etc.) from the "
        "codebase. Returns the complete implementation code where the symbol is defined.",
        {"symbolName": {"type": "string", "description": "e.g. 'mypackage.MyFunction', 'MyType.MyMethod'."}},
        ["symbolName"]),
    "references": _fn(
        "references", "Find all usages and references of a symbol throughout the codebase.",
        {"symbolName": {"type": "string", "description": "e.g. 'mypackage.MyFunction', 'MyType'."}},
        ["symbolName"]),
    "diagnostics": _fn(
        "diagnostics", "Get diagnostic information (errors, warnings) for a specific file from the language server.",
        {"filePath": {"type": "string"},
         "contextLines": {"type": "integer", "description": "Optional lines of context around each diagnostic, default 5."},
         "showLineNumbers": {"type": "boolean", "description": "Optional, default true."}},
        ["filePath"]),
    "hover": _fn(
        "hover", "Get hover information (type, documentation) for a symbol at a specific file position.",
        {"filePath": {"type": "string"}, "line": {"type": "integer", "description": "1-indexed."},
         "column": {"type": "integer", "description": "1-indexed."}},
        ["filePath", "line", "column"]),
    "rename_symbol": _fn(
        "rename_symbol", "Rename a symbol (variable, function, class, etc.) at a specific position and update all "
        "references throughout the codebase.",
        {"filePath": {"type": "string"}, "line": {"type": "integer", "description": "1-indexed."},
         "column": {"type": "integer", "description": "1-indexed."}, "newName": {"type": "string"}},
        ["filePath", "line", "column", "newName"]),
    "edit_file": _fn(
        "edit_file", "Apply multiple line-range text edits to a file.",
        {"filePath": {"type": "string"},
         "edits": {"type": "array", "description": "List of {startLine, endLine, newText} edits (1-indexed, inclusive).",
                    "items": {"type": "object"}}},
        ["filePath", "edits"]),
}

_OBSERVABILITY_SCHEMAS: dict[str, dict] = {
    'add_activity_to_incident': _fn('add_activity_to_incident', "Add a note to an existing incident's timeline.", {'incidentId': {"type": 'string'}, 'body': {"type": 'string'}, 'eventTime': {"type": 'string'}}, ['incidentId', 'body']),
    'agento11y_manage_agents': _fn('agento11y_manage_agents', "Read the telemetry-derived Agent Observability agent catalog.", {'operation': {"type": 'string'}, 'agent_name': {"type": 'string'}, 'version': {"type": 'string'}, 'name_prefix': {"type": 'string'}, 'limit': {"type": 'integer'}}, ['operation']),
    'agento11y_manage_conversations': _fn('agento11y_manage_conversations', "List, search, and fetch LLM conversations from Agent Observability.", {'operation': {"type": 'string'}, 'conversation_id': {"type": 'string'}, 'filters': {"type": 'string'}, 'cursor': {"type": 'string'}, 'limit': {"type": 'integer'}}, ['operation']),
    'agento11y_manage_eval_collections': _fn('agento11y_manage_eval_collections', "Manage Agent Observability saved-conversation bookmarks and collections.", {'operation': {"type": 'string'}, 'saved_id': {"type": 'string'}, 'collection_id': {"type": 'string'}, 'conversation_id': {"type": 'string'}, 'name': {"type": 'string'}, 'saved_ids': {"type": 'array'}, 'limit': {"type": 'integer'}}, ['operation']),
    'agento11y_manage_eval_rules': _fn('agento11y_manage_eval_rules', "Manage Agent Observability async eval rules and inline guards.", {'operation': {"type": 'string'}, 'rule_id': {"type": 'string'}, 'definition': {"type": 'object'}, 'limit': {"type": 'integer'}}, ['operation']),
    'agento11y_manage_evaluators': _fn('agento11y_manage_evaluators', "Manage the Agent Observability evaluator catalog and templates.", {'operation': {"type": 'string'}, 'evaluator_id': {"type": 'string'}, 'template_id': {"type": 'string'}, 'definition': {"type": 'object'}, 'generation_id': {"type": 'string'}, 'limit': {"type": 'integer'}}, ['operation']),
    'agento11y_manage_generations': _fn('agento11y_manage_generations', "Fetch a single LLM generation or its evaluation scores.", {'operation': {"type": 'string'}, 'generation_id': {"type": 'string'}, 'limit': {"type": 'integer'}, 'cursor': {"type": 'string'}}, ['operation', 'generation_id']),
    'alerting_manage_routing': _fn('alerting_manage_routing', "Inspect Grafana alerting routing configuration (notification policies, contact points, time intervals).", {'operation': {"type": 'string'}, 'datasource_uid': {"type": 'string'}, 'name': {"type": 'string'}, 'contact_point_title': {"type": 'string'}, 'time_interval_name': {"type": 'string'}, 'limit': {"type": 'integer'}}, ['operation']),
    'alerting_manage_rules': _fn('alerting_manage_rules', "List/inspect/create/update/delete Grafana alert rules.", {'operation': {"type": 'string'}, 'rule_uid': {"type": 'string'}, 'title': {"type": 'string'}, 'rule_group': {"type": 'string'}, 'folder_uid': {"type": 'string'}, 'search_folder': {"type": 'string'}, 'condition': {"type": 'string'}, 'data': {"type": 'array'}, 'no_data_state': {"type": 'string'}, 'exec_err_state': {"type": 'string'}, 'for_': {"type": 'string'}, 'org_id': {"type": 'integer'}, 'states': {"type": 'array'}}, ['operation']),
    'analyze_loki_labels': _fn('analyze_loki_labels', "Audit a Loki label strategy and, optionally, diagnose query performance.", {'datasourceUid': {"type": 'string'}, 'labels': {"type": 'array'}, 'selector': {"type": 'string'}, 'maxLabels': {"type": 'integer'}, 'startRfc3339': {"type": 'string'}, 'endRfc3339': {"type": 'string'}, 'expectedBaseLabels': {"type": 'array'}, 'perfMetrics': {"type": 'object'}}, []),
    'ask_assistant': _fn('ask_assistant', "Send a message to Grafana Assistant and block until the full text reply is ready.", {'prompt': {"type": 'string'}, 'contextId': {"type": 'string'}}, ['prompt']),
    'check_datasources_health': _fn('check_datasources_health', "Bulk health-check datasources, optionally filtered by type or UID list.", {'type': {"type": 'string'}, 'uids': {"type": 'array'}, 'offset': {"type": 'integer'}}, []),
    'create_annotation': _fn('create_annotation', "Create a new annotation on a dashboard/panel, or a Graphite-format annotation.", {'dashboardUid': {"type": 'string'}, 'panelId': {"type": 'integer'}, 'text': {"type": 'string'}, 'format': {"type": 'string'}, 'what': {"type": 'string'}, 'tags': {"type": 'array'}}, []),
    'create_datasource': _fn('create_datasource', "Create a datasource, using a schema-confirmation flow before writing.", {'type': {"type": 'string'}, 'name': {"type": 'string'}, 'url': {"type": 'string'}, 'fields': {"type": 'object'}, 'schemaReviewed': {"type": 'boolean'}}, ['type']),
    'create_folder': _fn('create_folder', "Create a Grafana folder.", {'title': {"type": 'string'}, 'uid': {"type": 'string'}, 'parentUid': {"type": 'string'}}, ['title']),
    'create_incident': _fn('create_incident', "Create a new Grafana incident.", {'title': {"type": 'string'}, 'severity': {"type": 'string'}, 'roomPrefix': {"type": 'string'}, 'isDrill': {"type": 'boolean'}, 'status': {"type": 'string'}}, ['title', 'severity', 'roomPrefix']),
    'create_snapshot': _fn('create_snapshot', "Create a snapshot from a full dashboard JSON payload.", {'dashboard': {"type": 'object'}, 'name': {"type": 'string'}, 'expires': {"type": 'integer'}, 'external': {"type": 'boolean'}, 'key': {"type": 'string'}, 'deleteKey': {"type": 'string'}}, ['dashboard']),
    'delete_snapshot': _fn('delete_snapshot', "Delete a snapshot by its key.", {'key': {"type": 'string'}}, ['key']),
    'describe_athena_table': _fn('describe_athena_table', "Get column names for an Athena table.", {'datasourceUid': {"type": 'string'}, 'table': {"type": 'string'}, 'region': {"type": 'string'}, 'catalog': {"type": 'string'}, 'database': {"type": 'string'}}, ['datasourceUid', 'table']),
    'describe_clickhouse_table': _fn('describe_clickhouse_table', "Get column schema for a ClickHouse table.", {'datasourceUid': {"type": 'string'}, 'table': {"type": 'string'}, 'database': {"type": 'string'}}, ['datasourceUid', 'table']),
    'describe_snowflake_table': _fn('describe_snowflake_table', "Get column schema for a Snowflake table.", {'datasourceUid': {"type": 'string'}, 'table': {"type": 'string'}, 'schema': {"type": 'string'}, 'database': {"type": 'string'}}, ['datasourceUid', 'table']),
    'find_error_pattern_logs': _fn('find_error_pattern_logs', "Run a Sift investigation for elevated error patterns in Loki logs.", {'name': {"type": 'string'}, 'labels': {"type": 'object'}, 'start': {"type": 'string'}, 'end': {"type": 'string'}}, ['name', 'labels']),
    'find_slow_requests': _fn('find_slow_requests', "Run a Sift investigation for slow requests in Tempo traces.", {'name': {"type": 'string'}, 'labels': {"type": 'object'}, 'start': {"type": 'string'}, 'end': {"type": 'string'}}, ['name', 'labels']),
    'generate_deeplink': _fn('generate_deeplink', "Generate a deeplink URL to a Grafana dashboard, panel, or Explore query.", {'resourceType': {"type": 'string'}, 'dashboardUid': {"type": 'string'}, 'provisioningPreview': {"type": 'object'}, 'datasourceUid': {"type": 'string'}, 'panelId': {"type": 'integer'}, 'shorten': {"type": 'boolean'}}, ['resourceType']),
    'get_alert_group': _fn('get_alert_group', "Get a specific OnCall alert group by ID.", {'alertGroupId': {"type": 'string'}}, ['alertGroupId']),
    'get_annotation_tags': _fn('get_annotation_tags', "List annotation tags, optionally filtered by name substring.", {'tag': {"type": 'string'}, 'limit': {"type": 'integer'}}, []),
    'get_annotations': _fn('get_annotations', "Fetch Grafana annotations filtered by time range, dashboard/panel/user/tags.", {'from_': {"type": 'integer'}, 'to': {"type": 'integer'}, 'limit': {"type": 'integer'}, 'dashboardUid': {"type": 'string'}, 'tags': {"type": 'array'}}, []),
    'get_assertions': _fn('get_assertions', "Get an assertion (SLO/health-check) summary for an entity over a time range.", {'startTime': {"type": 'string'}, 'endTime': {"type": 'string'}, 'entityType': {"type": 'string'}, 'entityName': {"type": 'string'}, 'env': {"type": 'string'}, 'site': {"type": 'string'}, 'namespace': {"type": 'string'}}, ['startTime', 'endTime']),
    'get_current_oncall_users': _fn('get_current_oncall_users', "Get the users currently on-call for a schedule.", {'scheduleId': {"type": 'string'}}, ['scheduleId']),
    'get_dashboard_by_uid': _fn('get_dashboard_by_uid', "Retrieve the complete dashboard (panels, variables, settings) for a given UID.", {'uid': {"type": 'string'}}, ['uid']),
    'get_dashboard_panel_queries': _fn('get_dashboard_panel_queries', "Retrieve panel queries from a dashboard, optionally filtered to one panel.", {'uid': {"type": 'string'}, 'panelId': {"type": 'integer'}, 'variables': {"type": 'object'}}, ['uid']),
    'get_dashboard_property': _fn('get_dashboard_property', "Get specific parts of a dashboard via a JSONPath expression.", {'uid': {"type": 'string'}, 'jsonPath': {"type": 'string'}}, ['uid', 'jsonPath']),
    'get_dashboard_summary': _fn('get_dashboard_summary', "Get a compact summary of a dashboard (title, panel count, variables).", {'uid': {"type": 'string'}}, ['uid']),
    'get_datasource': _fn('get_datasource', "Retrieve full details of a datasource by UID or name.", {'uid': {"type": 'string'}, 'name': {"type": 'string'}}, []),
    'get_incident': _fn('get_incident', "Get a single incident by ID.", {'id': {"type": 'string'}}, ['id']),
    'get_oncall_shift': _fn('get_oncall_shift', "Get detailed information for a specific OnCall shift.", {'shiftId': {"type": 'string'}}, ['shiftId']),
    'get_panel_image': _fn('get_panel_image', "Render a Grafana dashboard panel or full dashboard as a PNG image.", {'dashboardUid': {"type": 'string'}, 'provisioningPreview': {"type": 'object'}, 'panelId': {"type": 'integer'}, 'width': {"type": 'integer'}, 'height': {"type": 'integer'}, 'theme': {"type": 'string'}, 'scale': {"type": 'integer'}, 'timeout': {"type": 'integer'}}, []),
    'get_plugin': _fn('get_plugin', "Check whether a Grafana plugin is installed and return its details.", {'pluginId': {"type": 'string'}}, ['pluginId']),
    'get_query_examples': _fn('get_query_examples', "Return curated example queries for a specific datasource type.", {'datasourceType': {"type": 'string'}}, ['datasourceType']),
    'get_resource_description': _fn('get_resource_description', "List available permissions for a Grafana resource type.", {'resourceType': {"type": 'string'}}, ['resourceType']),
    'get_resource_permissions': _fn('get_resource_permissions', "List all permissions set on a specific Grafana resource.", {'resource': {"type": 'string'}, 'resourceId': {"type": 'string'}}, ['resource', 'resourceId']),
    'get_role_assignments': _fn('get_role_assignments', "List all assignments (users, teams) for a specific role.", {'roleUID': {"type": 'string'}}, ['roleUID']),
    'get_role_details': _fn('get_role_details', "Get detailed information about a specific Grafana role by UID.", {'roleUID': {"type": 'string'}}, ['roleUID']),
    'get_sift_analysis': _fn('get_sift_analysis', "Retrieve a specific analysis from a Sift investigation.", {'investigationId': {"type": 'string'}, 'analysisId': {"type": 'string'}}, ['investigationId', 'analysisId']),
    'get_sift_investigation': _fn('get_sift_investigation', "Retrieve an existing Sift investigation by its UUID.", {'id': {"type": 'string'}}, ['id']),
    'get_snapshot': _fn('get_snapshot', "Get a snapshot by key, including its dashboard payload.", {'key': {"type": 'string'}}, ['key']),
    'grafana_api_request': _fn('grafana_api_request', "Make an authenticated HTTP request to any Grafana API endpoint.", {'endpoint': {"type": 'string'}, 'method': {"type": 'string'}, 'body': {"type": 'string'}, 'headers': {"type": 'object'}, 'jq': {"type": 'string'}}, ['endpoint']),
    'install_plugin': _fn('install_plugin', "Install a Grafana plugin by ID and version.", {'pluginId': {"type": 'string'}, 'version': {"type": 'string'}}, ['pluginId']),
    'list_alert_groups': _fn('list_alert_groups', "List OnCall alert groups with filtering.", {'page': {"type": 'integer'}, 'id': {"type": 'string'}, 'teamId': {"type": 'string'}, 'state': {"type": 'string'}}, []),
    'list_all_roles': _fn('list_all_roles', "List all roles in Grafana, optionally filtered to delegatable-only.", {'delegatableOnly': {"type": 'boolean'}}, []),
    'list_athena_catalogs': _fn('list_athena_catalogs', "List available Athena data catalogs.", {'datasourceUid': {"type": 'string'}, 'region': {"type": 'string'}}, ['datasourceUid']),
    'list_athena_databases': _fn('list_athena_databases', "List databases within an Athena catalog.", {'datasourceUid': {"type": 'string'}, 'region': {"type": 'string'}, 'catalog': {"type": 'string'}}, ['datasourceUid']),
    'list_athena_tables': _fn('list_athena_tables', "List tables within an Athena database.", {'datasourceUid': {"type": 'string'}, 'region': {"type": 'string'}, 'catalog': {"type": 'string'}, 'database': {"type": 'string'}}, ['datasourceUid']),
    'list_clickhouse_tables': _fn('list_clickhouse_tables', "List tables in a ClickHouse instance.", {'datasourceUid': {"type": 'string'}, 'database': {"type": 'string'}}, ['datasourceUid']),
    'list_cloudwatch_dimensions': _fn('list_cloudwatch_dimensions', "List dimension keys available for a CloudWatch namespace+metric pair.", {'datasourceUid': {"type": 'string'}, 'namespace': {"type": 'string'}, 'metricName': {"type": 'string'}, 'region': {"type": 'string'}, 'accountId': {"type": 'string'}}, ['datasourceUid', 'namespace', 'metricName', 'region']),
    'list_cloudwatch_metrics': _fn('list_cloudwatch_metrics', "List metric names available in a given CloudWatch namespace.", {'datasourceUid': {"type": 'string'}, 'namespace': {"type": 'string'}, 'region': {"type": 'string'}, 'accountId': {"type": 'string'}}, ['datasourceUid', 'namespace', 'region']),
    'list_cloudwatch_namespaces': _fn('list_cloudwatch_namespaces', "List available CloudWatch namespaces.", {'datasourceUid': {"type": 'string'}, 'region': {"type": 'string'}, 'accountId': {"type": 'string'}}, ['datasourceUid', 'region']),
    'list_datasources': _fn('list_datasources', "List configured datasources, with optional type filtering and pagination.", {'type': {"type": 'string'}, 'limit': {"type": 'integer'}, 'offset': {"type": 'integer'}}, []),
    'list_graphite_metrics': _fn('list_graphite_metrics', "Browse the Graphite metric hierarchy via wildcard path patterns.", {'datasourceUid': {"type": 'string'}, 'query': {"type": 'string'}}, ['datasourceUid']),
    'list_graphite_tags': _fn('list_graphite_tags', "List tag names available in a tag-enabled Graphite datasource.", {'datasourceUid': {"type": 'string'}, 'prefix': {"type": 'string'}}, ['datasourceUid']),
    'list_incidents': _fn('list_incidents', "List Grafana incidents, optionally filtered by status.", {'limit': {"type": 'integer'}, 'drill': {"type": 'boolean'}, 'status': {"type": 'string'}}, []),
    'list_loki_label_names': _fn('list_loki_label_names', "List all label names present in logs within a Loki datasource and time range.", {'datasourceUid': {"type": 'string'}, 'startRfc3339': {"type": 'string'}, 'endRfc3339': {"type": 'string'}}, ['datasourceUid']),
    'list_loki_label_values': _fn('list_loki_label_values', "Get all unique values for a specific label within a Loki datasource.", {'datasourceUid': {"type": 'string'}, 'labelName': {"type": 'string'}, 'startRfc3339': {"type": 'string'}, 'endRfc3339': {"type": 'string'}}, ['datasourceUid', 'labelName']),
    'list_oncall_schedules': _fn('list_oncall_schedules', "List Grafana OnCall schedules, optionally filtered by team.", {'teamId': {"type": 'string'}, 'scheduleId': {"type": 'string'}, 'page': {"type": 'integer'}}, []),
    'list_oncall_teams': _fn('list_oncall_teams', "List teams configured in Grafana OnCall.", {'page': {"type": 'integer'}}, []),
    'list_oncall_users': _fn('list_oncall_users', "List OnCall users.", {'userId': {"type": 'string'}, 'username': {"type": 'string'}, 'page': {"type": 'integer'}}, []),
    'list_prometheus_label_names': _fn('list_prometheus_label_names', "List label names in a PromQL-compatible datasource.", {'datasourceUid': {"type": 'string'}, 'matches': {"type": 'array'}, 'startRfc3339': {"type": 'string'}, 'endRfc3339': {"type": 'string'}, 'limit': {"type": 'integer'}, 'projectName': {"type": 'string'}}, ['datasourceUid']),
    'list_prometheus_label_values': _fn('list_prometheus_label_values', "Get the values for a specific label name.", {'datasourceUid': {"type": 'string'}, 'labelName': {"type": 'string'}, 'matches': {"type": 'array'}, 'startRfc3339': {"type": 'string'}, 'endRfc3339': {"type": 'string'}, 'limit': {"type": 'integer'}, 'projectName': {"type": 'string'}}, ['datasourceUid', 'labelName']),
    'list_prometheus_metric_metadata': _fn('list_prometheus_metric_metadata', "List Prometheus metric metadata (type, help text, unit).", {'datasourceUid': {"type": 'string'}, 'limit': {"type": 'integer'}, 'limitPerMetric': {"type": 'integer'}, 'metric': {"type": 'string'}, 'projectName': {"type": 'string'}}, ['datasourceUid']),
    'list_prometheus_metric_names': _fn('list_prometheus_metric_names', "Discover available metric names via a regex filter.", {'datasourceUid': {"type": 'string'}, 'regex': {"type": 'string'}, 'limit': {"type": 'integer'}, 'page': {"type": 'integer'}, 'startRfc3339': {"type": 'string'}, 'endRfc3339': {"type": 'string'}, 'projectName': {"type": 'string'}}, ['datasourceUid']),
    'list_provisioning_repositories': _fn('list_provisioning_repositories', "List provisioning (git-sync) repositories configured on the instance.", {'namespace': {"type": 'string'}}, []),
    'list_pyroscope_label_names': _fn('list_pyroscope_label_names', "List all available label names found in profiles within a datasource.", {'data_source_uid': {"type": 'string'}, 'matchers': {"type": 'string'}, 'start_rfc_3339': {"type": 'string'}, 'end_rfc_3339': {"type": 'string'}}, ['data_source_uid']),
    'list_pyroscope_label_values': _fn('list_pyroscope_label_values', "List all unique values for a specific Pyroscope label.", {'data_source_uid': {"type": 'string'}, 'name': {"type": 'string'}, 'matchers': {"type": 'string'}, 'start_rfc_3339': {"type": 'string'}, 'end_rfc_3339': {"type": 'string'}}, ['data_source_uid', 'name']),
    'list_pyroscope_profile_types': _fn('list_pyroscope_profile_types', "List all profile types available in a datasource.", {'data_source_uid': {"type": 'string'}, 'start_rfc_3339': {"type": 'string'}, 'end_rfc_3339': {"type": 'string'}}, ['data_source_uid']),
    'list_sift_investigations': _fn('list_sift_investigations', "List Sift investigations.", {'limit': {"type": 'integer'}}, []),
    'list_snapshots': _fn('list_snapshots', "List Grafana dashboard snapshots.", {'query': {"type": 'string'}, 'limit': {"type": 'integer'}}, []),
    'list_snowflake_tables': _fn('list_snowflake_tables', "List tables via Snowflake's INFORMATION_SCHEMA.", {'datasourceUid': {"type": 'string'}, 'database': {"type": 'string'}, 'schema': {"type": 'string'}}, ['datasourceUid']),
    'list_team_roles': _fn('list_team_roles', "List all roles assigned to one or more teams.", {'teamIds': {"type": 'array'}}, ['teamIds']),
    'list_teams': _fn('list_teams', "Search for Grafana teams by a query string.", {'query': {"type": 'string'}}, []),
    'list_user_roles': _fn('list_user_roles', "List all roles assigned to one or more users.", {'userIds': {"type": 'array'}}, ['userIds']),
    'list_users_by_org': _fn('list_users_by_org', "List users in the current Grafana organization.", {}, []),
    'query_athena': _fn('query_athena', "Execute a raw SQL query against Athena via Grafana.", {'datasourceUid': {"type": 'string'}, 'query': {"type": 'string'}, 'start': {"type": 'string'}, 'end': {"type": 'string'}, 'region': {"type": 'string'}, 'catalog': {"type": 'string'}, 'database': {"type": 'string'}, 'variables': {"type": 'object'}, 'limit': {"type": 'integer'}, 'resultReuseEnabled': {"type": 'boolean'}, 'resultReuseMaxAgeInMinutes': {"type": 'integer'}}, ['datasourceUid', 'query']),
    'query_clickhouse': _fn('query_clickhouse', "Execute a raw SQL query against ClickHouse via Grafana.", {'datasourceUid': {"type": 'string'}, 'query': {"type": 'string'}, 'start': {"type": 'string'}, 'end': {"type": 'string'}, 'variables': {"type": 'object'}, 'limit': {"type": 'integer'}}, ['datasourceUid', 'query']),
    'query_cloudwatch': _fn('query_cloudwatch', "Query a specific AWS CloudWatch metric over a time range.", {'datasourceUid': {"type": 'string'}, 'namespace': {"type": 'string'}, 'metricName': {"type": 'string'}, 'region': {"type": 'string'}, 'dimensions': {"type": 'object'}, 'statistic': {"type": 'string'}, 'period': {"type": 'integer'}, 'start': {"type": 'string'}, 'end': {"type": 'string'}, 'accountId': {"type": 'string'}}, ['datasourceUid', 'namespace', 'metricName', 'region']),
    'query_elasticsearch': _fn('query_elasticsearch', "Execute a search query against an index pattern in an Elasticsearch/OpenSearch datasource.", {'datasourceUid': {"type": 'string'}, 'index': {"type": 'string'}, 'query': {"type": 'string'}, 'startTime': {"type": 'string'}, 'endTime': {"type": 'string'}, 'limit': {"type": 'integer'}}, ['datasourceUid', 'index', 'query']),
    'query_graphite': _fn('query_graphite', "Execute a Graphite render-API query and return matching series.", {'datasourceUid': {"type": 'string'}, 'target': {"type": 'string'}, 'from_': {"type": 'string'}, 'until': {"type": 'string'}, 'maxDataPoints': {"type": 'integer'}}, ['datasourceUid', 'target']),
    'query_graphite_density': _fn('query_graphite_density', "Analyze data density/staleness for Graphite series.", {'datasourceUid': {"type": 'string'}, 'target': {"type": 'string'}, 'from_': {"type": 'string'}, 'until': {"type": 'string'}}, ['datasourceUid', 'target']),
    'query_influxdb': _fn('query_influxdb', "Run a raw InfluxQL or Flux query against an InfluxDB datasource.", {'datasourceUid': {"type": 'string'}, 'query': {"type": 'string'}, 'dialect': {"type": 'string'}, 'start': {"type": 'string'}, 'end': {"type": 'string'}, 'maxDataPoints': {"type": 'integer'}}, ['datasourceUid', 'query']),
    'query_loki_logs': _fn('query_loki_logs', "Execute a LogQL query and return matching log entries or metric samples.", {'datasourceUid': {"type": 'string'}, 'logql': {"type": 'string'}, 'startRfc3339': {"type": 'string'}, 'endRfc3339': {"type": 'string'}, 'limit': {"type": 'integer'}, 'direction': {"type": 'string'}, 'queryType': {"type": 'string'}, 'stepSeconds': {"type": 'integer'}}, ['datasourceUid', 'logql']),
    'query_loki_patterns': _fn('query_loki_patterns', "Retrieve Loki's automatically detected log patterns.", {'datasourceUid': {"type": 'string'}, 'logql': {"type": 'string'}, 'startRfc3339': {"type": 'string'}, 'endRfc3339': {"type": 'string'}, 'step': {"type": 'string'}}, ['datasourceUid', 'logql']),
    'query_loki_stats': _fn('query_loki_stats', "Get index-level statistics for a simple label-selector query.", {'datasourceUid': {"type": 'string'}, 'logql': {"type": 'string'}, 'startRfc3339': {"type": 'string'}, 'endRfc3339': {"type": 'string'}}, ['datasourceUid', 'logql']),
    'query_prometheus': _fn('query_prometheus', "Run a PromQL instant or range query against a PromQL-compatible datasource.", {'datasourceUid': {"type": 'string'}, 'expr': {"type": 'string'}, 'startTime': {"type": 'string'}, 'endTime': {"type": 'string'}, 'stepSeconds': {"type": 'integer'}, 'queryType': {"type": 'string'}, 'projectName': {"type": 'string'}}, ['datasourceUid', 'expr']),
    'query_prometheus_histogram': _fn('query_prometheus_histogram', "Compute a histogram percentile for a base histogram metric.", {'datasourceUid': {"type": 'string'}, 'metric': {"type": 'string'}, 'percentile': {"type": 'number'}, 'labels': {"type": 'string'}, 'rateInterval': {"type": 'string'}, 'startTime': {"type": 'string'}, 'endTime': {"type": 'string'}, 'stepSeconds': {"type": 'integer'}, 'projectName': {"type": 'string'}}, ['datasourceUid', 'metric', 'percentile']),
    'query_pyroscope': _fn('query_pyroscope', "Fetch Pyroscope profile and/or metrics data.", {'data_source_uid': {"type": 'string'}, 'profile_type': {"type": 'string'}, 'query_type': {"type": 'string'}, 'format': {"type": 'string'}, 'matchers': {"type": 'string'}, 'group_by': {"type": 'array'}, 'step': {"type": 'number'}, 'max_node_depth': {"type": 'integer'}, 'start_rfc_3339': {"type": 'string'}, 'end_rfc_3339': {"type": 'string'}}, ['data_source_uid', 'profile_type']),
    'query_quickwit': _fn('query_quickwit', "Execute a search against a Quickwit datasource/index pattern.", {'datasourceUid': {"type": 'string'}, 'query': {"type": 'string'}, 'index': {"type": 'string'}, 'startTime': {"type": 'string'}, 'endTime': {"type": 'string'}, 'limit': {"type": 'integer'}}, ['datasourceUid', 'query']),
    'query_snowflake': _fn('query_snowflake', "Execute a raw SQL query against Snowflake via Grafana.", {'datasourceUid': {"type": 'string'}, 'query': {"type": 'string'}, 'start': {"type": 'string'}, 'end': {"type": 'string'}, 'variables': {"type": 'object'}, 'limit': {"type": 'integer'}}, ['datasourceUid', 'query']),
    'run_panel_query': _fn('run_panel_query', "Execute one or more existing dashboard panels' queries directly.", {'dashboardUid': {"type": 'string'}, 'panelIds': {"type": 'array'}, 'queryIndex': {"type": 'integer'}, 'start': {"type": 'string'}, 'end': {"type": 'string'}, 'variables': {"type": 'object'}, 'datasourceUid': {"type": 'string'}, 'datasourceType': {"type": 'string'}}, ['dashboardUid', 'panelIds']),
    'search_dashboards': _fn('search_dashboards', "Search for Grafana dashboards by query string.", {'query': {"type": 'string'}, 'limit': {"type": 'integer'}, 'page': {"type": 'integer'}}, []),
    'search_folders': _fn('search_folders', "Search for Grafana folders by query string.", {'query': {"type": 'string'}}, []),
    'search_plugin_information': _fn('search_plugin_information', "Search the public Grafana plugin catalog by keyword.", {'query': {"type": 'string'}}, ['query']),
    'suggest_loki_alloy_label_config': _fn('suggest_loki_alloy_label_config', "Generate an Alloy loki.process pipeline snippet enforcing a label allowlist.", {'approvedLabels': {"type": 'array'}, 'requiredLabels': {"type": 'array'}, 'normalizeLogLevel': {"type": 'boolean'}, 'componentName': {"type": 'string'}, 'forwardTo': {"type": 'string'}}, ['approvedLabels']),
    'update_annotation': _fn('update_annotation', "Update an existing annotation by ID using partial-update semantics.", {'id': {"type": 'integer'}, 'text': {"type": 'string'}, 'tags': {"type": 'array'}}, ['id']),
    'update_dashboard': _fn('update_dashboard', "Create or update a dashboard via full JSON or targeted JSON-patch operations.", {'dashboard': {"type": 'object'}, 'uid': {"type": 'string'}, 'operations': {"type": 'array'}, 'folderUid': {"type": 'string'}, 'message': {"type": 'string'}, 'overwrite': {"type": 'boolean'}, 'userId': {"type": 'integer'}}, []),
    'update_datasource': _fn('update_datasource', "Update an existing datasource by UID, using the same schema-confirmation flow.", {'uid': {"type": 'string'}, 'schemaReviewed': {"type": 'boolean'}, 'name': {"type": 'string'}, 'url': {"type": 'string'}, 'fields': {"type": 'object'}}, ['uid']),
    'validate_provisioning_file': _fn('validate_provisioning_file', "Dry-run validate a file inside a provisioning repository.", {'repo': {"type": 'string'}, 'path': {"type": 'string'}, 'namespace': {"type": 'string'}, 'ref': {"type": 'string'}}, ['repo', 'path']),
}

_CLOUD_INFRA_SCHEMAS: dict[str, dict] = {
    "validate_cloudformation_template": _fn(
        "validate_cloudformation_template", "Validate CloudFormation template syntax, schema, and resource "
        "properties using cfn-lint.",
        {"template_content": {"type": "string", "description": "CloudFormation template as a JSON string."},
         "regions": {"type": "array", "items": {"type": "string"}, "description": "Optional AWS regions to validate against."},
         "ignore_checks": {"type": "array", "items": {"type": "string"}, "description": "Optional rule IDs to ignore."}},
        ["template_content"]),
    "check_cloudformation_template_compliance": _fn(
        "check_cloudformation_template_compliance", "Validate a CloudFormation template against security and "
        "compliance rules using cfn-guard.",
        {"template_content": {"type": "string"}}, ["template_content"]),
    "troubleshoot_cloudformation_deployment": _fn(
        "troubleshoot_cloudformation_deployment", "Diagnose a failed CloudFormation stack with root cause "
        "analysis and optional CloudTrail integration.",
        {"stack_name": {"type": "string"}, "region": {"type": "string"},
         "include_cloudtrail": {"type": "boolean", "description": "Optional, defaults to true."}},
        ["stack_name", "region"]),
    "get_cloudformation_pre_deploy_validation_instructions": _fn(
        "get_cloudformation_pre_deploy_validation_instructions", "Get instructions for CloudFormation's "
        "pre-deployment change-set validation feature.", {}, []),
    "search_cdk_documentation": _fn(
        "search_cdk_documentation", "Search AWS CDK documentation knowledge bases.",
        {"query": {"type": "string"}}, ["query"]),
    "search_cloudformation_documentation": _fn(
        "search_cloudformation_documentation", "Search AWS CloudFormation documentation knowledge bases.",
        {"query": {"type": "string"}}, ["query"]),
    "search_cdk_samples_and_constructs": _fn(
        "search_cdk_samples_and_constructs", "Search CDK code samples, examples, constructs, and patterns.",
        {"query": {"type": "string"},
         "language": {"type": "string", "enum": ["typescript", "python", "java", "csharp", "go"],
                       "description": "Optional, defaults to 'typescript'."}},
        ["query"]),
    "cdk_best_practices": _fn(
        "cdk_best_practices", "Get CDK best practices and security guidelines.", {}, []),
    "read_iac_documentation_page": _fn(
        "read_iac_documentation_page", "Fetch and convert a specific CDK or CloudFormation documentation page "
        "to markdown, with pagination support.",
        {"url": {"type": "string", "description": "URL from a prior search result."},
         "starting_index": {"type": "integer", "description": "Optional pagination offset, defaults to 0."}},
        ["url"]),
}

MOCK_SERVICES: dict[str, tuple[type[MockService], dict[str, dict]]] = {
    "task_tracker": (TaskTrackerService, _TASK_TRACKER_SCHEMAS),
    "git_repo": (GitRepoService, _GIT_REPO_SCHEMAS),
    "filesystem": (FilesystemService, _FILESYSTEM_SCHEMAS),
    "docker": (DockerService, _DOCKER_SCHEMAS),
    "kubernetes": (KubernetesService, _KUBERNETES_SCHEMAS),
    "forge": (ForgeService, _FORGE_SCHEMAS),
    "package_registry": (PackageRegistryService, _PACKAGE_REGISTRY_SCHEMAS),
    "terraform": (TerraformService, _TERRAFORM_SCHEMAS),
    "database": (DatabaseService, _DATABASE_SCHEMAS),
    "ci_pipeline": (CIPipelineService, _CI_PIPELINE_SCHEMAS),
    "build_tools": (BuildToolsService, _BUILD_TOOLS_SCHEMAS),
    "code_intel": (CodeIntelService, _CODE_INTEL_SCHEMAS),
    "observability": (ObservabilityService, _OBSERVABILITY_SCHEMAS),
    "cloud_infra": (CloudInfraService, _CLOUD_INFRA_SCHEMAS),
}


def get_mock_service(name: str) -> type[MockService]:
    entry = MOCK_SERVICES.get(name)
    if entry is None:
        raise KeyError(
            f"Unknown tool_service {name!r}. Available: {', '.join(sorted(MOCK_SERVICES))}"
        )
    return entry[0]


def get_tool_schemas(name: str, tool_names: list[str] | None = None) -> list[dict]:
    """OpenAI-function-schema list for ``name``'s tools, in ``tool_names``
    order when given (a case's own subset/order - lets a case expose
    distractor tools alongside the ones it actually needs), else every tool
    the service defines, in registry order."""
    entry = MOCK_SERVICES.get(name)
    if entry is None:
        raise KeyError(
            f"Unknown tool_service {name!r}. Available: {', '.join(sorted(MOCK_SERVICES))}"
        )
    _, schemas = entry
    names = tool_names if tool_names is not None else list(schemas.keys())
    try:
        return [schemas[n] for n in names]
    except KeyError as exc:
        raise KeyError(
            f"tool_service {name!r} has no tool named {exc.args[0]!r} "
            f"(available: {', '.join(sorted(schemas))})"
        ) from None
