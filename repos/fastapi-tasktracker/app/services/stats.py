from __future__ import annotations

from collections import Counter

from app.models.task import TaskStatus


def progress_summary(statuses: list[TaskStatus | str]) -> dict:
    """Aggregate a project's task statuses into a progress report."""
    counts = Counter(TaskStatus(s).value for s in statuses)
    total = sum(counts.values())
    done = counts.get(TaskStatus.done.value, 0)
    percent = round(100 * done / total, 1) if total else 0.0
    return {
        "total": total,
        "todo": counts.get(TaskStatus.todo.value, 0),
        "in_progress": counts.get(TaskStatus.in_progress.value, 0),
        "done": done,
        "percent_done": percent,
    }
