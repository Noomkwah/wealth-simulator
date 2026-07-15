from collections import defaultdict
from dataclasses import dataclass, field
import datetime
from enum import IntEnum
from heapq import heappop, heappush
from itertools import count
from typing import Any, Generic, Iterable, Protocol, runtime_checkable, TypeVar
import uuid


__all__ = [
    "Event",
    "Module",
    "SimulationContext",
    "SimulationEngine",
]

class EventPriority(IntEnum):
    """Lower numbers are processed first on the same date."""
    SYSTEM = 0
    DEFAULT = 100

T = TypeVar("T")
@dataclass(frozen=True, slots=True)
class Event(Generic[T]):
    """
    An immutable message routed by the simulation engine.
    """

    date: datetime.date
    source: str
    target: str
    
    kind: str

    payload: T

    uid: str = field(default_factory=lambda: uuid.uuid4().hex)
    priority: int = EventPriority.DEFAULT



@runtime_checkable
class Module(Protocol):
    name: str

    def handle(self, event: Event[Any], ctx: SimulationContext) -> None:
        ...


@dataclass(order=True)
class _QueuedEvent:
    sort_key: tuple = field(compare=True)
    event: Event[Any] = field(compare=False)

class SimulationEngine:
    """Discrete Event Simulation (DES) engine."""

    def __init__(self, start_date: datetime.date) -> None:
        self.start_date = start_date
        self.today = start_date
        self.modules: dict[str, Module] = {}
        self.event_log: list[Event[Any]] = []

        self._queue: list[_QueuedEvent] = []
        self._sequence = count()
        self._cancelled_event_uids: set[str] = set()
        self._subscribers: dict[str, list[str]]= defaultdict(list)
        self._ctx: SimulationContext = SimulationContext(self)

    def register(self, module: Module) -> Module:
        if module.name in self.modules:
            raise ValueError(f"Duplicate module name: {module.name}")
        self.modules[module.name] = module
        return module
    
    def register_many(self, modules: Iterable[Module]) -> None:
        for module in modules:
            self.register(module)

    def get_module(self, name: str) -> Module:
        try:
            return self.modules[name]
        except KeyError as exc:
            raise KeyError(f"No module registered with name: {name}") from exc
        
    def schedule(self, event: Event[Any]) -> None:
        if event.date < self.today:
            raise ValueError(f"Cannot schedule event in the past: {event.date} < today (= {self.today})")
        sort_key = (event.date, event.priority, next(self._sequence))
        heappush(self._queue, _QueuedEvent(sort_key, event))
    
    def subscribe(self, module_name: str, event_kind: str) -> None:
        subscribers = self._subscribers[event_kind]
        if module_name not in subscribers:
            subscribers.append(module_name)

    def publish(self, event: Event[Any]) -> None:
        for target in self._subscribers.get(event.kind, ()):
            self.schedule(
                Event(
                    date=event.date,
                    source=event.source,
                    target=target,
                    kind=event.kind,
                    payload=event.payload,
                    priority=event.priority,
                    uid=event.uid, # Ensure all events get the same uid as they are the same event.
                )
            )

    def cancel(self, uid: str) -> None:
        self._cancelled_event_uids.add(uid)
    
    def is_event_cancelled(self, uid: str) -> bool:
        return uid in self._cancelled_event_uids

    def start(self) -> None:
        for module in list(self.modules.values()):
            start_module = getattr(module, "start", None)
            if callable(start_module):
                start_module(self._ctx)
            
    def step(self) -> Event[Any] | None:
        """Process the next event in the queue. Returns the event if processed, None if the queue is empty."""
        while self._queue:
            queued = heappop(self._queue)
            event = queued.event

            if self.is_event_cancelled(event.uid):
                continue  # Skip cancelled events

            # Update the current date and log the event
            self.today = event.date
            self.event_log.append(event)

            # Handle the event
            module = self.modules.get(event.target)
            if module is None:
                raise ValueError(f"No module registered for target: {event.target}")
            module.handle(event, self._ctx)

            return event

        return None
            
    def run_until(self, end_date: datetime.date) -> None:
        if end_date < self.today:
            raise ValueError(f"end_date {end_date} is before today {self.today}")
        
        # Process events until the queue is empty or the next event is beyond end_date
        while self._queue:
            # Peek at the next event's date without popping it
            if self._queue[0].event.date > end_date:
                break
            if self.step() is None:
                break
    
    @property
    def queued_event_count(self) -> int:
        return len(self._queue)
    
    @property
    def active_queued_event_count(self) -> int:
        return sum(not self.is_event_cancelled(q.event.uid) for q in self._queue)


class SimulationContext:
    
    def __init__(self, engine: SimulationEngine) -> None:
        self._engine = engine
    
    @property
    def today(self) -> datetime.date:
        return self._engine.today
    
    def emit[T](
        self,
        *,
        date: datetime.date,
        source: str,
        target: str,
        kind: str,
        payload: T,
        uid: str | None = None,
        priority: int = EventPriority.DEFAULT
    ) -> Event[T]:
        
        if uid is None:
            event = Event(
                date=date, source=source, target=target,
                kind=kind, payload=payload, priority=priority,
            )
        else:
            event = Event(
                date=date, source=source, target=target,
                kind=kind, payload=payload, priority=priority,
                uid=uid or uuid.uuid4().hex,
            )
        self._engine.schedule(event)
        return event
    
    def subscribe(self, module_name: str, event_kind: str) -> None:
        self._engine.subscribe(module_name, event_kind)

    def publish[T](
        self,
        *,
        date: datetime.date,
        source: str,
        kind: str,
        payload: T,
        priority: int = EventPriority.DEFAULT,
    ) -> None:
        self._engine.publish(
            Event(
                date=date,
                source=source,
                target="",  # ignored for published facts
                kind=kind,
                payload=payload,
                priority=priority,
            )
        )
    
    def cancel(self, uid: str) -> None:
        self._engine.cancel(uid)
    
    def is_event_cancelled(self, uid: str) -> bool:
        return self._engine.is_event_cancelled(uid)
    
    def iter_modules(self) -> Iterable[Module]:
        return self._engine.modules.values()
    
    def get_module(self, name: str) -> Module:
        return self._engine.get_module(name)
    
    def has_module(self, name: str) -> bool:
        return name in self._engine.modules
