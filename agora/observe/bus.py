from __future__ import annotations

import logging
from collections import deque
from typing import Callable, Deque, List

from agora.core.events import Event

log = logging.getLogger("agora.bus")
Subscriber = Callable[[Event], None]

class EventBus:
    def __init__(self, history: int = 4000) -> None:
        self._subs: List[Subscriber] = []
        self._seq = 0
        self.history: Deque[Event] = deque(maxlen=history)

    def subscribe(self, fn: Subscriber) -> Callable[[], None]:
        self._subs.append(fn)

        def unsubscribe() -> None:
            if fn in self._subs:
                self._subs.remove(fn)

        return unsubscribe

    def publish(self, ev: Event) -> Event:
        self._seq += 1
        ev.id = self._seq
        self.history.append(ev)
        for fn in list(self._subs):
            try:
                fn(ev)
            except Exception:
                log.exception("event subscriber failed")
        return ev
