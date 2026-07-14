from bisect import bisect_right
import calendar
from collections import defaultdict
from dataclasses import dataclass, field
import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, cast, Generic, Literal, Mapping, Protocol, runtime_checkable, TypeAlias, TypeVar
import uuid

from ..engine import Event, SimulationContext, Module


################################################################
#                           Accounts                           #
################################################################

MoneyInput: TypeAlias = float | int | str | Decimal

@runtime_checkable
class Account(Protocol):
    def deposit(self, amount: MoneyInput) -> None: ...
    def withdraw(self, amount: MoneyInput) -> None: ...
    def can_withdraw(self, amount: MoneyInput) -> bool: ...
    def can_deposit(self, amount: MoneyInput) -> bool: ...
    @property
    def balance(self) -> Decimal: ...
    @property
    def name(self) -> str: ...


def as_money(x: MoneyInput) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


class CashAccount:

    def __init__(self, initial_balance: MoneyInput, name: str, allow_negative: bool = False) -> None:

        initial_balance = as_money(initial_balance)
        if initial_balance < 0:
            raise ValueError("Cannot open account with negative initial balance. Got {initial_balance} < 0.")
        self._balance = initial_balance
        self._name = name

        self.allow_negative = allow_negative

    def deposit(self, amount: MoneyInput) -> None:
        amount = as_money(amount)
        if amount < 0:
            raise ValueError(f"Cannot deposit a negative amount (got amount={amount}).")
        self._balance += amount

    def withdraw(self, amount: MoneyInput) -> None:
        if not self.can_withdraw(amount):
            raise ValueError(f"Cannot withdraw amount={amount} from the account (either amount is negative or balance={self._balance} is insuficient).")
        self._balance -= as_money(amount)

    def can_deposit(self, amount: MoneyInput) -> bool:
        return True

    def can_withdraw(self, amount: MoneyInput) -> bool:
        amount = as_money(amount)
        if amount < 0:
            return False
        if not self.allow_negative  and amount > self._balance:
            return False
        
        return True

    @property
    def balance(self) -> Decimal:
        return self._balance
    
    @property
    def name(self) -> str:
        return self._name

@dataclass
class AccountRef:
    module_name: str
    account_name: str


################################################################
#                        Event kinds                           #
################################################################

class WealthEventKind(StrEnum):
    PAYMENT_REQUIRED = "payment.required"
    PAYMENT_SUCCEEDED = "payment.succeeded"
    PAYMENT_FAILED = "payment.failed"
    PAYMENT_DUE = "payment.due"
    PAYMENT_SCHEDULED = "payment.scheduled"

class EngineEventKind(StrEnum):
    RECORD = "engine.record"

################################################################
#                      System modules                          #
################################################################

class SystemModules(StrEnum):
    PAYMENT_SYSTEM = "payment-system"


################################################################
#                    Recording modules                         #
################################################################

class Snapshot(Protocol):
    ...

@runtime_checkable
class Snapshotable(Protocol):
    name: str

    def snapshot(self) -> Snapshot:
        ...

S = TypeVar("S", bound=Snapshot)
@dataclass(frozen=True, slots=True)
class Record(Generic[S]):
    date: datetime.date
    module_id: int
    module_name: str
    snapshot: S

@dataclass(frozen=True, slots=True)
class RecordPayload:
    pass

class SimulationHistory:

    def __init__(self) -> None:
        self._records: list[Record[Any]] = []
        self._by_module: dict[str, list[Record[Any]]] = defaultdict(list)
        self._by_date: dict[datetime.date, list[Record[Any]]] = defaultdict(list)

    def append(self, record: Record[Any]) -> None:

        self._records.append(record)
        self._by_module[record.module_name].append(record)
        self._by_date[record.date].append(record)

    @property
    def records(self) -> tuple[Record[Any], ...]:
        return tuple(self._records)

    def records_on(self, date: datetime.date) -> tuple[Record[Any], ...]:
        return tuple(self._by_date.get(date, ()))
    
    def history(self, module_name: str) -> tuple[Record[Any], ...]:
        return tuple(self._by_module.get(module_name, ()))

    def latest(self, module_name: str) -> Record[Any] | None:
        history = self._by_module.get(module_name)
        if not history:
            return None
        return history[-1]

    def latest_before(self, module_name: str, date: datetime.date) -> Record[Any] | None:
        history = self._by_module.get(module_name)
        if not history:
            return None
        dates = [record.date for record in history]
        idx = bisect_right(dates, date) - 1
        if idx < 0:
            return None

        return history[idx]

    def between(self, module_name: str, start: datetime.date, end: datetime.date) -> tuple[Record[Any], ...]:
        history = self._by_module.get(module_name)
        if history is None:
            return ()
        return tuple(record for record in history if start <= record.date <= end)

@dataclass
class SimulationRecorder(Module):
    name: str
    start_date: datetime.date
    schedule: Schedule

    history: SimulationHistory = field(default_factory=SimulationHistory)

    def start(self, ctx: SimulationContext) -> None:

        first = self.schedule.first_due_date(agreement_start=self.start_date)

        if first is None:
            return

        ctx.emit(
            date=first,
            source=self.name,
            target=self.name,
            kind=EngineEventKind.RECORD,
            payload=RecordPayload(),
        )

    def handle(self, event: Event[Any], ctx: SimulationContext) -> None:

        if event.kind != EngineEventKind.RECORD:
            raise ValueError(f"Module {self.name} cannot handle {event.kind}.")

        self.record(ctx)

        next_date = self.schedule.next_due_date(previous_due_date=event.date, agreement_start=self.start_date)

        if next_date is not None:
            ctx.emit(
                date=next_date,
                source=self.name,
                target=self.name,
                kind=EngineEventKind.RECORD,
                payload=RecordPayload(),
                priority=900
            )

    def record(self, ctx: SimulationContext) -> None:

        for module in ctx.iter_modules():
            if not isinstance(module, Snapshotable):
                continue
            self.history.append(Record(date=ctx.today, module_id=id(module), module_name=module.name, snapshot=module.snapshot()))

    def history_of(self, module_name: str) -> tuple[Record[Any], ...]:
        return self.history.history(module_name)
    
    def latest_snapshot(self, module_name: str) -> Snapshot | None:
        record = self.history.latest(module_name)
        if record is None:
            return None
        return record.snapshot
    
    def snapshot_before(self, module_name: str, date: datetime.date) -> Snapshot | None:
        record = self.history.latest_before(module_name, date)
        if record is None:
            return None
        return record.snapshot
    
    def snapshots_on(self, date: datetime.date) -> tuple[Record[Any], ...]:
        return self.history.records_on(date)
    
################################################################
#                           Payments                           #
################################################################

@dataclass(frozen=True, slots=True)
class PaymentPayload:
    payer: AccountRef
    receiver: AccountRef
    amount: Decimal
    description: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class PaymentResultPayload:
    request: PaymentPayload
    success: bool
    reason: str | None = None

@dataclass(frozen=True)
class PaymentSystem(Module):
    name: str = SystemModules.PAYMENT_SYSTEM

    def handle(self, event: Event[Any], ctx: SimulationContext) -> None:
        if event.kind == WealthEventKind.PAYMENT_REQUIRED:
            return self._handle_payment(event, ctx)
        raise ValueError(f"{self.name} cannot handle event kind: {event.kind}.")
    
    def _handle_payment(self, event: Event, ctx: SimulationContext) -> None:
        payload = cast(PaymentPayload, event.payload)
        amount = payload.amount

        payer = self._resolve_account(ctx, payload.payer)
        receiver = self._resolve_account(ctx, payload.receiver)

        try:
            if not payer.can_withdraw(amount):
                raise ValueError(f"Account '{payer.name} cannot pay {amount}.")

            if not receiver.can_deposit(amount):
                raise ValueError(f"Account{receiver.name} cannot receive {amount}.")
            
            payer.withdraw(amount)
            receiver.deposit(amount)

            ctx.publish(
                date=event.date, source=self.name, kind=WealthEventKind.PAYMENT_SUCCEEDED, payload=PaymentResultPayload(payload, True)
            )
        
        except Exception as exc:
            ctx.publish(
                date=event.date, source=self.name, kind=WealthEventKind.PAYMENT_SUCCEEDED, payload=PaymentResultPayload(payload, False, str(exc))
            )

    def _resolve_account(self, ctx: SimulationContext, ref: AccountRef) -> Account:
        module = ctx.get_module(ref.module_name)
        
        if not isinstance(module, EconomicAgent):
            raise TypeError(f"Module {ref.module_name!r} is not an EconomicAgent.")

        return module.get_account(ref.account_name)


class Schedule(Protocol):
    def first_due_date(self, *, agreement_start: datetime.date) -> datetime.date | None: ...
    def next_due_date(self, *, previous_due_date: datetime.date, agreement_start: datetime.date) -> datetime.date | None: ...

class AmountRule(Protocol):
    def amount_on(self, due_date: datetime.date, ctx: SimulationContext) -> Decimal: ...

@dataclass(frozen=True)
class PaymentObligation:
    name: str
    payer: AccountRef
    receiver: AccountRef
    schedule: Schedule
    amount_rule: AmountRule
    uid: str = field(default_factory=lambda: uuid.uuid4().hex)

def make_payment_instruction(obligation: PaymentObligation, due_date: datetime.date) -> PaymentPayload: ...

################################################################
#                           Schedules                          #
################################################################

def _clamp_day(year: int, month: int, day: int) -> int:
    """Return the requested day, capped to the month's last day."""
    return min(day, calendar.monthrange(year, month)[1])

def _is_weekend(date: datetime.date) -> bool:
    return date.weekday() >= 5 # Saturday = 5, Sunday = 6

@dataclass(frozen=True)
class MonthlySchedule:
    day: int

    def __post_init__(self) -> None:
        if not 1 <= self.day <= 31:
            raise ValueError("day must be between 1 and 31.")
        
    def first_due_date(self, agreement_start: datetime.date) -> datetime.date | None:
        due_day = _clamp_day(agreement_start.year, agreement_start.month, self.day)
        due_date = datetime.date(agreement_start.year, agreement_start.month, due_day)

        if due_date >= agreement_start:
            return due_date

        return self.next_due_date(previous_due_date=due_date, agreement_start=agreement_start)

    def next_due_date(self, *, previous_due_date: datetime.date, agreement_start: datetime.date) -> datetime.date:
        year = previous_due_date.year
        month = previous_due_date.month + 1
        if month == 13:
            month = 1
            year += 1
        return datetime.date(year, month, _clamp_day(year, month, self.day))
    
@dataclass(frozen=True)
class DailySchedule:
    keep_weekend: bool = True

    def first_due_date(self, agreement_start: datetime.date) -> datetime.date | None:
        if self.keep_weekend:
            return agreement_start
        
        due_date = agreement_start
        while _is_weekend(due_date):
            due_date += datetime.timedelta(days=1)
        return due_date

    def next_due_date(self, *, previous_due_date: datetime.date, agreement_start: datetime.date) -> datetime.date:
        due_date = previous_due_date + datetime.timedelta(days=1)
        if self.keep_weekend:
            return due_date

        while _is_weekend(due_date):
            due_date += datetime.timedelta(days=1)
        return due_date
    
    

################################################################
#                        Amount Rules                          #
################################################################

@dataclass(frozen=True)
class FixedAmountRule:
    amount: Decimal

    def amount_on(self, due_date: datetime.date, ctx: SimulationContext) -> Decimal:
        return self.amount


@dataclass(frozen=True, slots=True)
class DailyInterestAmountRule:
    account: AccountRef
    annual_nominal_rate: Decimal
    day_count: Decimal = Decimal("365")
    allow_negative_interest: bool = False

    def amount_on(self, due_date: datetime.date, ctx: SimulationContext) -> Decimal:
        account = self._resolve_account(ctx, self.account)
        balance = account.balance

        if balance <= 0 and not self.allow_negative_interest:
            return Decimal("0")
        
        daily_rate = self.annual_nominal_rate / self.day_count
        interest = balance * daily_rate

        if interest <= 0 and not self.allow_negative_interest:
            return Decimal("0")
        
        return interest


    def _resolve_account(self, ctx: SimulationContext, ref: AccountRef) -> Account:
        module = ctx.get_module(ref.module_name)
        
        if not isinstance(module, EconomicAgent):
            raise TypeError(f"Module {ref.module_name!r} is not an EconomicAgent.")

        return module.get_account(ref.account_name)
    
################################################################
#                        Economic Agents                       #
################################################################

@dataclass(frozen=True, slots=True)
class EconomicAgentSnapshot:
    accounts: dict[str, Decimal]

@dataclass
class EconomicAgent:
    name: str
    accounts: dict[str, Account] = field(default_factory=dict)

    @property
    def net_worth(self) -> Decimal:
        _net_worth = 0
        for account in self.accounts.values():
            _net_worth += account.balance
        return Decimal(_net_worth)

    def add_account(self, account: Account):
        
        # Ensure account not already present
        if account.name in self.accounts.keys():
            raise KeyError("Account with name {account.name} already exist. Cannot register.")

        self.accounts[account.name] = account
    
    def get_account(self, account_name: str) -> Account:
        if account_name in self.accounts.keys():
            return self.accounts[account_name]
        raise KeyError(f"No account {account_name} registered for this agent.")
    
    def handle(self, event: Event[Any], ctx: SimulationContext) -> None:
        raise ValueError(f"{self.name} cannot handle event kind {event.kind}.")

    def snapshot(self) -> EconomicAgentSnapshot:
        return EconomicAgentSnapshot(accounts={name: account.balance for name, account in self.accounts.items()})


################################################################
#                          Contracts                           #
################################################################

@dataclass(frozen=True, slots=True)
class PaymentContractPayload:
    obligation_uid: str

@dataclass
class PaymentContract(Module):
    name: str
    start_date: datetime.date
    obligations: list[PaymentObligation] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._obligations_by_uid: dict[str, PaymentObligation] = {}
        for obligation in self.obligations:
            self.add_obligation(obligation)
    
    def add_obligation(self, obligation: PaymentObligation) -> None:
        uid = obligation.uid
        if uid in self._obligations_by_uid.keys():
            raise ValueError(f"Cannot add obligation with uid={uid!r} (already one existing).")
        self._obligations_by_uid[uid] = obligation

    def start(self, ctx: SimulationContext) -> None:
        for obligation in self.obligations:
            due_date = obligation.schedule.first_due_date(agreement_start=self.start_date)
            if due_date is None:
                continue

            # Schedule internal reminder
            ctx.emit(
                date=due_date,
                source=self.name,
                target=self.name,
                kind=WealthEventKind.PAYMENT_DUE,
                payload=PaymentContractPayload(obligation_uid=obligation.uid)
            )

            # Publish external information
            ctx.publish(
                date=due_date,
                source=self.name,
                kind=WealthEventKind.PAYMENT_SCHEDULED,
                payload=PaymentContractPayload(obligation_uid=obligation.uid),
            )
    
    def handle(self, event: Event[Any], ctx: SimulationContext) -> None:
        if event.kind != WealthEventKind.PAYMENT_DUE:
            raise ValueError(f"{self.name} cannot handle {event.kind!r}.")
        payload = cast(PaymentContractPayload, event.payload)
        obligation = self._obligations_by_uid[payload.obligation_uid]
        due_date = event.date
        amount = obligation.amount_rule.amount_on(due_date, ctx)
        ctx.emit(
            date=due_date,
            source=self.name,
            target=SystemModules.PAYMENT_SYSTEM,
            kind=WealthEventKind.PAYMENT_REQUIRED,
            payload=PaymentPayload(
                payer=obligation.payer,
                receiver=obligation.receiver,
                amount=amount,
                description=obligation.name,
            )
        )

        next_due_date = obligation.schedule.next_due_date(
            previous_due_date=due_date,
            agreement_start=self.start_date
        )
        if next_due_date is not None:
            ctx.emit(
                date=next_due_date,
                source=self.name,
                target=self.name,
                kind=WealthEventKind.PAYMENT_DUE,
                payload=PaymentContractPayload(obligation_uid=obligation.uid),
            )