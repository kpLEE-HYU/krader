"""Async event bus for pub/sub communication."""

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine

from krader.events.types import Event

logger = logging.getLogger(__name__)

EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus:
    """Async event bus supporting multiple subscribers per event type."""

    def __init__(self) -> None:
        self._handlers: dict[type[Event], list[EventHandler]] = defaultdict(list)
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._running = False
        self._task: asyncio.Task[None] | None = None

    def subscribe(self, event_type: type[Event], handler: EventHandler) -> None:
        """Register a handler for an event type."""
        self._handlers[event_type].append(handler)
        logger.debug("Subscribed %s to %s", handler.__name__, event_type.__name__)

    def unsubscribe(self, event_type: type[Event], handler: EventHandler) -> None:
        """Unregister a handler for an event type."""
        if handler in self._handlers[event_type]:
            self._handlers[event_type].remove(handler)
            logger.debug("Unsubscribed %s from %s", handler.__name__, event_type.__name__)

    async def publish(self, event: Event) -> None:
        """Publish an event to the bus."""
        await self._queue.put(event)
        logger.debug("Published %s", type(event).__name__)

    def publish_nowait(self, event: Event) -> None:
        """Publish an event without waiting (for sync contexts)."""
        self._queue.put_nowait(event)
        logger.debug("Published (nowait) %s", type(event).__name__)

    async def _process_event(self, event: Event) -> None:
        """Process a single event by calling all registered handlers."""
        event_type = type(event)
        handlers = self._handlers.get(event_type, [])

        if not handlers:
            logger.debug("No handlers for %s", event_type.__name__)
            return

        tasks = [asyncio.create_task(handler(event)) for handler in handlers]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for handler, result in zip(handlers, results):
            if isinstance(result, Exception):
                logger.error(
                    "Handler %s failed for %s: %s",
                    handler.__name__,
                    event_type.__name__,
                    result,
                )

    async def _run_loop(self) -> None:
        """Main event processing loop."""
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=0.1)
                await self._process_event(event)
                self._queue.task_done()
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Event loop error: %s", e)

    async def start(self) -> None:
        """Start the event bus processing loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Event bus started")

    async def stop(self) -> None:
        """Stop the event bus and drain remaining events."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        while not self._queue.empty():
            try:
                event = self._queue.get_nowait()
                await self._process_event(event)
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

        logger.info("Event bus stopped")

    async def wait_empty(self) -> None:
        """Wait until all events have been processed."""
        await self._queue.join()
