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

MOCK_SERVICES: dict[str, tuple[type[MockService], dict[str, dict]]] = {
    "task_tracker": (TaskTrackerService, _TASK_TRACKER_SCHEMAS),
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
