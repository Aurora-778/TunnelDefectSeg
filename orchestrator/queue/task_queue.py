"""Small in-memory task queues."""

from __future__ import annotations

from collections import deque


class TaskQueue:
    def __init__(self) -> None:
        self.ready = deque()
        self.retry_queue = deque()
        self.failed_queue = deque()

    def enqueue(self, task_name: str) -> None:
        self.ready.append(task_name)

    def dequeue(self) -> str | None:
        return self.ready.popleft() if self.ready else None
