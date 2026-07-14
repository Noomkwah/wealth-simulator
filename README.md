# DES Wealth Simulator

A **Discrete Event Simulation (DES)** framework for modeling personal wealth, cash flows, and financial scenarios over time. Built in Python with a clean, modular architecture.

## Features

- **Event-Driven Simulation**: Priority-based event queue for accurate temporal sequencing
- **Modular Design**: Pluggable components (agents, contracts, schedulers, rules)
- **Precision Arithmetic**: Uses Decimal for exact financial calculations
- **Flexible Scheduling**: Monthly, daily, and custom payment schedules
- **Behavioral Rules**: Define custom logic for economic agents
- **Comprehensive Recording**: Full simulation history with snapshots
- **Interest Calculation**: Daily compounding with configurable rates

## Project Structure

simulator/
+-- engine/
|   +-- __init__.py      # Core DES engine: Event, Module, SimulationEngine, SimulationContext
+-- wealth/
    +-- __init__.py      # Domain models: Accounts, Agents, Contracts, Schedules, Payment System

N003_sandbox.ipynb       # Example notebook demonstrating usage

## Quick Start

### Prerequisites

- Python 3.10+
- No external dependencies (pure Python)

### Installation

```bash
# Clone or navigate to the project directory
cd "2026 - Projet perso - Simulateur de patrimoine"

# Create virtual environment (optional)
python -m venv .venv
# On Windows:
.\.venv\Scripts\activate

# No pip install required - uses only standard library
```

## Core Concepts

### 1. Simulation Engine

The heart of the framework. Manages event scheduling, dispatching, and module lifecycle.

```python
from simulator.engine import SimulationEngine
import datetime

engine = SimulationEngine(start_date=datetime.date(2026, 1, 1))
engine.register_many([...])  # Register all modules
engine.start()              # Initialize modules
engine.run_until(datetime.date(2026, 12, 31))  # Run simulation
```

### 2. Economic Agents

Entities that own accounts and participate in transactions.

```python
from simulator.wealth import EconomicAgent, CashAccount

alice = EconomicAgent(
    name="Alice",
    accounts={
        "cash": CashAccount(name="cash", initial_balance=10000),
        "savings": CashAccount(name="savings", initial_balance=5000)
    }
)
```

### 3. Accounts

Cash accounts with configurable constraints.

```python
# Standard account (cannot go negative)
account = CashAccount(name="checking", initial_balance=1000)

# Overdraft allowed
account = CashAccount(name="credit", initial_balance=0, allow_negative=True)
```

### 4. Payment Contracts and Obligations

Define recurring payments between accounts.

```python
from simulator.wealth import (
    PaymentContract, PaymentObligation, AccountRef,
    MonthlySchedule, FixedAmountRule, DailyInterestAmountRule
)
from decimal import Decimal

# Monthly salary
salary = PaymentObligation(
    name="Monthly salary",
    payer=AccountRef(module_name="Tesla", account_name="cash"),
    receiver=AccountRef(module_name="Alice", account_name="cash"),
    schedule=MonthlySchedule(day=25),
    amount_rule=FixedAmountRule(Decimal("3000"))
)

# Daily interest on savings
interest = PaymentObligation(
    name="savings-interest",
    payer=AccountRef(module_name="Bank", account_name="interest_expense"),
    receiver=AccountRef(module_name="Alice", account_name="savings"),
    schedule=DailySchedule(keep_weekend=True),
    amount_rule=DailyInterestAmountRule(
        account=AccountRef(module_name="Alice", account_name="savings"),
        annual_nominal_rate=Decimal("0.0341")  # 3.41%
    )
)

# Bundle into contract
employment = PaymentContract(
    name="employment-contract",
    start_date=datetime.date(2026, 1, 1),
    obligations=[salary]
)
```

### 5. Schedules

- **MonthlySchedule(day=N)**: Recurs on the Nth day of each month (clamped to month length)
- **DailySchedule(keep_weekend=True)**: Recurs daily, with optional weekend skipping

### 6. Amount Rules

- **FixedAmountRule(amount)**: Constant payment amount
- **DailyInterestAmountRule(account, annual_nominal_rate, day_count=365)**: Calculates interest based on account balance

### 7. Behavioral Rules

Custom logic that triggers on events.

```python
from dataclasses import dataclass
from typing import Any, cast
from simulator.engine import Event, SimulationContext
from simulator.wealth import WealthEventKind, PaymentResultPayload, PaymentPayload, AccountRef, EconomicAgent

@dataclass
class SweepOldCashOnSalary:
    salary_description: str = "Monthly salary"
    cash_account: str = "cash"
    savings_account: str = "savings"

    def start(self, owner: EconomicAgent, ctx: SimulationContext) -> None:
        ctx.subscribe(owner.name, WealthEventKind.PAYMENT_SUCCEEDED)

    def on_event(self, owner: EconomicAgent, event: Event[Any], ctx: SimulationContext) -> None:
        result = cast(PaymentResultPayload, event.payload)
        if not result.success:
            return

        payment = result.request
        if (payment.receiver.module_name != owner.name or
            payment.receiver.account_name != self.cash_account or
            payment.description != self.salary_description):
            return

        cash = owner.get_account(self.cash_account)
        leftover = cash.balance - payment.amount

        if leftover <= 0:
            return

        ctx.emit(
            date=ctx.today,
            source=owner.name,
            target="payment-system",
            kind=WealthEventKind.PAYMENT_REQUIRED,
            payload=PaymentPayload(
                payer=AccountRef(owner.name, self.cash_account),
                receiver=AccountRef(owner.name, self.savings_account),
                amount=leftover,
                description="automatic monthly sweep",
            ),
            priority=200,
        )

# Attach behavior to agent
alice = EconomicAgent(
    name="Alice",
    accounts={...},
    behaviors=[SweepOldCashOnSalary()]
)
```

### 8. Recording

Capture simulation state at regular intervals.

```python
from simulator.wealth import SimulationRecorder, DailySchedule

recorder = SimulationRecorder(
    name="daily-recorder",
    start_date=datetime.date(2026, 1, 1),
    schedule=DailySchedule()
)

# Later, access recorded data
history = recorder.history_of("Alice")
snapshot = recorder.latest_snapshot("Alice")
```

## Complete Example

See N003_sandbox.ipynb for a full working example that demonstrates:

1. Creating economic agents (Alice, Landlord, Tesla, Bank)
2. Setting up accounts with initial balances
3. Configuring salary, rent, and interest payments
4. Adding behavioral rules (automatic cash sweeping)
5. Running the simulation for a year
6. Recording daily snapshots

## Architecture

```
+-----------------------------+
|       SimulationEngine      |
|  +-----------------------+  |
|  |      Event Queue      |  |
|  +-----------------------+  |
|  +-----------------------+  |
|  |     Modules Registry  |  |
|  +-----------------------+  |
+-----------------------------+
            |
            v
+-----------------------------+
|      SimulationContext      |
|  - today: datetime.date    |
|  - emit() -> Event         |
|  - publish() -> None       |
|  - subscribe() -> None     |
|  - get_module() -> Module  |
+-----------------------------+
            |
    +-------+-------+
    v               v       v
+-------+   +-------+   +-------+
|Agent  |   |Contract|   |System |
| -accounts|  | -obligations| | -handles |
| -net_worth()| | -start()     | |PAYMENT_REQ|
| -snapshot()| | -handle()    | +-------+
+-------+   +-------+       |
                            v
+-----------------------------+
|       SimulationRecorder    |
|  - history: SimulationHistory|
|  - latest_snapshot()        |
|  - history_of()             |
+-----------------------------+
```

## Event Flow

```
1. Contract starts -> schedules PAYMENT_DUE events
2. PAYMENT_DUE -> Contract calculates amount, emits PAYMENT_REQUIRED
3. PAYMENT_REQUIRED -> PaymentSystem validates and processes
4. PaymentSystem emits PAYMENT_SUCCEEDED or PAYMENT_FAILED
5. Subscribers (e.g., agents with behaviors) react to events
6. Recorder captures snapshots on schedule
```

## Event Types

| Event Kind | Description | Payload |
|------------|-------------|---------|
| payment.required | Request to transfer funds | PaymentPayload |
| payment.succeeded | Payment completed successfully | PaymentResultPayload |
| payment.failed | Payment failed | PaymentResultPayload |
| payment.due | Internal contract reminder | PaymentContractPayload |
| payment.scheduled | Published notification of scheduled payment | PaymentContractPayload |
| engine.record | Trigger for recorder | RecordPayload |

## Customization

### Creating Custom Schedules

```python
from simulator.wealth import Schedule
import datetime

class WeeklySchedule:
    def __init__(self, day_of_week: int):  # 0=Monday, 6=Sunday
        self.day_of_week = day_of_week

    def first_due_date(self, *, agreement_start: datetime.date) -> datetime.date | None:
        days_ahead = (self.day_of_week - agreement_start.weekday()) % 7
        return agreement_start + datetime.timedelta(days=days_ahead)

    def next_due_date(self, *, previous_due_date: datetime.date, agreement_start: datetime.date) -> datetime.date:
        return previous_due_date + datetime.timedelta(days=7)
```

### Creating Custom Amount Rules

```python
from simulator.wealth import AmountRule
from decimal import Decimal

class InflationAdjustedRule:
    def __init__(self, base_amount: Decimal, annual_inflation: Decimal):
        self.base_amount = base_amount
        self.annual_inflation = annual_inflation

    def amount_on(self, due_date: datetime.date, ctx: SimulationContext) -> Decimal:
        start = datetime.date(2026, 1, 1)
        days_passed = (due_date - start).days
        years = days_passed / 365.25
        inflation_factor = (1 + self.annual_inflation) ** years
        return self.base_amount * inflation_factor
```

## Performance Considerations

- Events are stored in a priority queue (heapq) for O(log n) insertion
- Modules are looked up by name in a dictionary (O(1))
- Use slots=True on dataclasses for memory efficiency
- For long simulations, consider increasing event priority for critical operations

## Testing

The framework is designed for correctness:

- **Deterministic**: Same inputs produce same outputs
- **Traceable**: Full event log available via engine.event_log
- **Verifiable**: Each payment success/failure is explicitly tracked

## License

MIT License - Feel free to use, modify, and distribute.

## Contributing

Contributions welcome! Please ensure:

1. All financial calculations use Decimal for precision
2. New modules implement the appropriate protocols
3. Events are properly typed with payload classes
4. Tests cover edge cases (negative balances, weekends, etc.)

---

*Built for personal wealth simulation and financial planning scenarios.*
