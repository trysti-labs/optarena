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

    @staticmethod
    def _as_list(paths) -> list[str]:
        """Real models sometimes pass a single path as a bare string
        instead of a one-element list - normalize rather than let it
        silently iterate character-by-character."""
        if isinstance(paths, str):
            return [paths]
        return list(paths or [])

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

MOCK_SERVICES: dict[str, tuple[type[MockService], dict[str, dict]]] = {
    "task_tracker": (TaskTrackerService, _TASK_TRACKER_SCHEMAS),
    "git_repo": (GitRepoService, _GIT_REPO_SCHEMAS),
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
