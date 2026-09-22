"""线程安全事件总线与工作队列（单消费者语义）。

架构铁律（architecture §3/§6）：所有跨线程通信只经本模块的队列，没有共享可变
状态的裸访问。每个队列**恰好一个消费者线程**读取，生产端可多线程。

- ``EventBus``：领域线程 → 确定性核心（唯一消费者 = core 线程）。
- ``WorkQueue``：核心 → 领域工作线程（每类工作线程各持一个）。
"""
from __future__ import annotations

import queue
from typing import Generic, TypeVar

from host.core.events import Event, EventKind

T = TypeVar("T")


class WorkQueue(Generic[T]):
    """单消费者线程安全队列（薄封装，语义单一）。"""

    def __init__(self, maxsize: int = 0, name: str = "work") -> None:
        self._q: queue.Queue[T] = queue.Queue(maxsize=maxsize)
        self.name = name
        self._closed = False

    def put(self, item: T, *, block: bool = True, timeout: float | None = None) -> None:
        self._q.put(item, block=block, timeout=timeout)

    def get(self, timeout: float | None = None) -> T | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self, limit: int | None = None) -> list[T]:
        items: list[T] = []
        while limit is None or len(items) < limit:
            try:
                items.append(self._q.get_nowait())
            except queue.Empty:
                break
        return items

    def empty(self) -> bool:
        return self._q.empty()

    def qsize(self) -> int:
        return self._q.qsize()

    def close(self) -> None:
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed


class EventBus(WorkQueue[Event]):
    """核心事件的单消费者队列；``close`` 投递 ``SHUTDOWN`` 哨兵。"""

    def __init__(self, maxsize: int = 0) -> None:
        super().__init__(maxsize=maxsize, name="events")

    def publish(self, event: Event) -> None:
        self.put(event)

    def publish_many(self, events: list[Event]) -> None:
        for event in events:
            self.put(event)

    def shutdown(self) -> None:
        self.put(Event(kind=EventKind.SHUTDOWN))
        self.close()
