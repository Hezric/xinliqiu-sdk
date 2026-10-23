# -*- coding: utf-8 -*-
"""Background task tracking for long-running device operations.

Long operations (recording a fixed window, syncing a range) exceed typical
agent tool timeouts, so they return a task id immediately and are polled.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Task:
    task_id: str
    name: str
    state: str = "running"  # running | succeeded | failed
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "state": self.state,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
        }


class TaskRegistry:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()

    def submit(self, name: str, fn: Callable[[], dict[str, Any]]) -> Task:
        task = Task(task_id=uuid.uuid4().hex[:12], name=name)
        with self._lock:
            self._tasks[task.task_id] = task

        def runner() -> None:
            try:
                result = fn()
                task.result = result
                task.state = "succeeded"
            except Exception as exc:  # noqa: BLE001 - surfaced to callers
                task.error = f"{type(exc).__name__}: {exc}"
                task.state = "failed"
            finally:
                task.finished_at = time.time()

        threading.Thread(target=runner, name=f"xinliqiu-{task.task_id}", daemon=True).start()
        return task

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def prune(self, max_age: float = 3600.0) -> int:
        cutoff = time.time() - max_age
        with self._lock:
            dead = [k for k, t in self._tasks.items() if (t.finished_at or 0) < cutoff]
            for key in dead:
                del self._tasks[key]
            return len(dead)
