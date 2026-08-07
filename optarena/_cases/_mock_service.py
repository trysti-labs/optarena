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

MOCK_SERVICES: dict[str, tuple[type[MockService], dict[str, dict]]] = {
    "task_tracker": (TaskTrackerService, _TASK_TRACKER_SCHEMAS),
    "git_repo": (GitRepoService, _GIT_REPO_SCHEMAS),
    "filesystem": (FilesystemService, _FILESYSTEM_SCHEMAS),
    "docker": (DockerService, _DOCKER_SCHEMAS),
    "kubernetes": (KubernetesService, _KUBERNETES_SCHEMAS),
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
