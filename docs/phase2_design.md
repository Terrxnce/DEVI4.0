# Phase 2 Live Execution Design Review

**Status: Design Only — No Implementation Approved Yet**

This document specifies the live execution architecture for D.E.V.I. All items remain design-only until explicitly approved. No live order placement code may be written until all 10 sections are reviewed and the "First Live Test Plan" is signed off.

---

## 1. Live Arming Token Flow

### Concept

Live mode is a two-step process:
1. **System boot** into eval/paper mode (default)
2. **Explicit arming** required before any live execution

No automatic transition to live is possible. Arming is a deliberate operator action with time limits and audit logging.

### Arming Requirements

```
To arm live mode, the operator must:
1. Confirm config live_confirmed=true (already in config)
2. Call the arming endpoint explicitly
3. Provide a reason string (min 10 chars)
4. Verify MT5 connection is alive
5. Verify account balance > 0
```

### Token Design

```python
@dataclass(frozen=True)
class LiveArmingToken:
    token_id: str           # UUID
    run_id: str             # bound to specific run
    armed_at: datetime      # UTC timestamp
    expires_at: datetime    # armed_at + TTL (default 30 min)
    armed_by: str           # operator identifier
    reason: str             # operator-provided reason
    symbols: list[str]      # symbols authorized for live trading
    max_orders: int         # max orders allowed under this token

    @property
    def is_expired(self) -> bool:
        return datetime.now(tz=UTC) > self.expires_at

    @property
    def is_valid(self) -> bool:
        return not self.is_expired
```

### Arming Flow

```
Operator Request -> Arming Service
  |
  v
[1] Check: config execution.live_confirmed == true
    If FALSE -> reject "live_not_confirmed_in_config"
  |
  v
[2] Check: MT5 connection alive (ping)
    If FALSE -> reject "mt5_not_connected"
  |
  v
[3] Check: account balance > 0
    If FALSE -> reject "account_balance_zero"
  |
  v
[4] Check: no existing valid token for this run_id
    If TRUE -> reject "already_armed"; require disarm first
  |
  v
[5] Generate LiveArmingToken
    - token_id = uuid4()
    - expires_at = now + 30 minutes
    - max_orders = config.execution.max_orders_per_run
    - symbols = config.instrument.symbols (or explicit subset)
  |
  v
[6] Log arming event to logs/prod/arming_*.jsonl
  |
  v
[7] Return token to operator (display only, not persisted)
```

### Disarm Flow

```
Operator Request -> Disarm Service
  |
  v
[1] Invalidate token (remove from in-memory store)
  |
  v
[2] Enable kill switch (prevent any new decisions)
  |
  v
[3] Log disarm event with reason
  |
  v
[4] Flush all pending telemetry
  |
  v
[5] Confirm: no valid tokens remain
```

### Token Storage

- **In-memory only** — never written to disk
- **Per-process** — token dies with process restart (by design)
- **No recovery** — if process crashes, operator must re-arm
- **Single token** — only one valid token per process at any time

### Token TTL

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| TTL | 30 minutes | Limits window of exposure |
| Auto-disarm on session end | true | Never leave armed overnight |
| Max re-arms per hour | 10 | Prevents automation abuse |

---

## 2. Kill Switch

### Concept

An immediate, irreversible halt of all new trade decisions. Already partially in config (`execution.kill_switch_enabled: true`). Phase 2 wires it into the live execution path.

### Kill Switch Triggers

| Trigger | Source | Action |
|---------|--------|--------|
| Config flag | `execution.kill_switch_enabled` | Checked at top of every decision |
| Manual operator | CLI: `devi kill-switch --reason "manual"` | Immediate halt |
| Drawdown breach | Account equity drops below threshold | Auto-halt + log |
| Broker error rate | >N failed orders in M minutes | Auto-halt |
| Emergency stop | See Section 8 | Immediate halt + close |

### Kill Switch Behavior

```python
def evaluate_kill_switch(config: dict, state: RuntimeState) -> KillSwitchVerdict:
    if config["execution"]["kill_switch_enabled"]:
        return KillSwitchVerdict(triggered=True, reason="config_kill_switch")

    if state.failed_orders_last_10m > 3:
        return KillSwitchVerdict(triggered=True, reason="broker_error_rate_exceeded")

    # drawdown check
    equity = fetch_account_equity()
    if equity < config["risk"]["max_drawdown_threshold"] * config["account"]["initial_balance"]:
        return KillSwitchVerdict(triggered=True, reason="drawdown_breach")

    return KillSwitchVerdict(triggered=False, reason="")
```

### Kill Switch Enforcement

- Checked **before** `evaluate_supervisor()` in the decision pipeline
- If triggered: return `FinalDecision.REJECTED_EXECUTION`, failure_code=`kill_switch_active`
- Cannot be overridden by any profile, any arming token, any operator command
- Logging: every kill switch trigger writes to `logs/prod/killswitch_*.jsonl`
- After trigger: system remains in HOLD state until explicitly disarmed and re-armed

---

## 3. Max Orders Per Run

### Current State (Phase 1.5)

- `RuntimeState.orders_this_run` tracks in-memory
- Config `execution.max_orders_per_run` is the ceiling
- Supervisor gate enforces: `orders_this_run >= max_orders` → `REJECTED_COMPLIANCE`

### Phase 2 Enhancement

```
Runtime State Tracking:
- orders_this_run: incremented on each EXECUTE decision
- orders_today: incremented across all runs in same UTC day
- orders_this_symbol: per-symbol counter

Config limits:
- max_orders_per_run: default 1 (per run)
- max_orders_per_day: default 5 (safety cap)
- max_orders_per_symbol_per_day: default 2 (concentration limit)

Enforcement order:
1. Check orders_this_run >= max_orders_per_run
2. Check orders_today >= max_orders_per_day
3. Check orders_this_symbol >= max_orders_per_symbol_per_day
4. Any breach -> REJECTED_COMPLIANCE with specific reason
```

### First Live Test Override

For the first live test (Section 10):
- `max_orders_per_run = 1` (hardcoded, cannot be overridden)
- `max_orders_per_day = 1` (hardcoded)
- `max_orders_per_symbol_per_day = 1` (hardcoded)

---

## 4. Pre-Trade Rechecks

Before calling `order_send()`, the LiveOrderWrapper must re-validate all conditions. MT5 state may have changed during the decision pipeline execution time.

### 4.1 Spread Recheck

```python
def recheck_spread(intent: TradeIntent, decision_spread: float) -> RecheckVerdict:
    tick = mt5.symbol_info_tick(intent.symbol)
    current_spread = abs(tick.ask - tick.bid)

    # If spread widened beyond 2x decision-time spread, reject
    if current_spread > decision_spread * 2.0:
        return RecheckVerdict(
            passed=False,
            reason=f"spread_widened:{decision_spread:.5f}->{current_spread:.5f}"
        )

    # If spread is zero (suspicious), reject
    if current_spread == 0:
        return RecheckVerdict(passed=False, reason="spread_zero")

    return RecheckVerdict(passed=True, reason="")
```

### 4.2 Account Recheck

```python
def recheck_account(intent: TradeIntent) -> RecheckVerdict:
    account = mt5.account_info()

    if account.balance <= 0:
        return RecheckVerdict(passed=False, reason="account_balance_zero")

    if account.equity < account.balance * 0.5:
        return RecheckVerdict(passed=False, reason="equity_below_50pct")

    # Margin check: rough estimate
    margin_required = intent.entry_price * intent.risk_verdict.lot_size * 100000.0 / 100.0
    if account.margin_free < margin_required:
        return RecheckVerdict(passed=False, reason="insufficient_margin")

    return RecheckVerdict(passed=True, reason="")
```

### 4.3 Risk Sizing Recheck

```python
def recheck_risk(intent: TradeIntent) -> RecheckVerdict:
    # Re-run lot sizing with current account balance
    current_balance = mt5.account_info().balance
    recalculated_lot = calculate_lot_size(
        balance=current_balance,
        entry=intent.entry_price,
        stop_loss=intent.exit_plan.stop_loss,
        point=profile.point,
        contract_size=profile.contract_size,
        lot_step=profile.lot_step,
        min_lot=profile.min_lot,
        max_lot=profile.max_lot,
    )

    deviation = abs(recalculated_lot - intent.risk_verdict.lot_size) / intent.risk_verdict.lot_size
    if deviation > 0.20:
        return RecheckVerdict(
            passed=False,
            reason=f"lot_size_deviation:{deviation:.2%}"
        )

    return RecheckVerdict(passed=True, reason="")
```

### 4.4 Symbol Tradability Recheck

```python
def recheck_symbol(symbol: str) -> RecheckVerdict:
    info = mt5.symbol_info(symbol)

    if not info.trade_allowed:
        return RecheckVerdict(passed=False, reason="symbol_trade_disabled")

    if not info.trade_mode == 0:  # 0 = full access
        return RecheckVerdict(passed=False, reason=f"symbol_trade_mode:{info.trade_mode}")

    return RecheckVerdict(passed=True, reason="")
```

### 4.5 Market Open Recheck

```python
def recheck_market_open(symbol: str) -> RecheckVerdict:
    info = mt5.symbol_info(symbol)

    # MT5 session flag
    if not info.session_deals:
        return RecheckVerdict(passed=False, reason="market_closed")

    # Time-based check (optional)
    from src.context.session import get_current_session
    session = get_current_session()
    if session.value == "CLOSED":
        return RecheckVerdict(passed=False, reason="session_closed")

    return RecheckVerdict(passed=True, reason="")
```

### Pre-Trade Recheck Order

```
All rechecks must pass in sequence:

1. Kill switch OFF
2. Live token VALID
3. Symbol tradability
4. Market open
5. Spread recheck
6. Account recheck
7. Risk sizing recheck
8. Max orders recheck

Any failure -> REJECTED_EXECUTION with specific reason
No retry on recheck failure
```

---

## 5. MT5 order_send Wrapper

### Design Principle

**All real orders go through exactly one function.** No direct `mt5.order_send()` anywhere in the codebase except inside `LiveOrderWrapper.send_order()`.

### LiveOrderWrapper

```python
class LiveOrderWrapper:
    """Single entry point for all live broker orders.

    Safety:
    - Only instantiated in live mode
    - Paper mode never creates this wrapper
    - All calls logged with full audit trail
    """

    def __init__(
        self,
        *,
        arming_token: LiveArmingToken,
        telemetry_writer: TelemetryWriter,
    ) -> None:
        self._token = arming_token
        self._writer = telemetry_writer
        self._orders_sent: int = 0

    def send_order(self, intent: TradeIntent) -> LiveOrderResult:
        """Send a single order to MT5 with full safety checks.

        Steps:
        1. Verify token valid
        2. Verify kill switch OFF
        3. Run all pre-trade rechecks
        4. Build MT5 order request
        5. Call mt5.order_send()
        6. Log broker retcode
        7. Handle success/failure
        8. Return LiveOrderResult
        """
        # 1. Token check
        if self._token.is_expired:
            return LiveOrderResult(
                status="REJECTED",
                reason="arming_token_expired",
                broker_retcode=None,
                mt5_ticket=None,
            )

        # 2. Kill switch
        # (checked upstream, but double-check here)

        # 3. Pre-trade rechecks
        # (passed in via caller or re-run here)

        # 4. Build request
        request = self._build_request(intent)

        # 5. Send
        result = mt5.order_send(request)

        # 6. Log
        self._writer.write_broker_order({
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "trade_id": intent.trade_id,
            "decision_id": intent.trade_id,  # Phase 2 cleanup: separate
            "request": request,
            "result": result,
        })

        # 7. Handle
        if result.retcode == 10009:  # TRADE_RETCODE_DONE
            self._orders_sent += 1
            return LiveOrderResult(
                status="FILLED",
                reason="success",
                broker_retcode=result.retcode,
                mt5_ticket=result.order,
            )

        # Failure handled by retcode processor
        return self._handle_retcode(result)

    def _build_request(self, intent: TradeIntent) -> dict:
        """Build MT5 OrderSend request from TradeIntent."""
        return {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": intent.symbol,
            "volume": float(intent.risk_verdict.lot_size),
            "type": mt5.ORDER_TYPE_BUY if intent.direction.value == "BULLISH" else mt5.ORDER_TYPE_SELL,
            "price": float(intent.entry_price),
            "sl": float(intent.exit_plan.stop_loss),
            "tp": float(intent.exit_plan.take_profit),
            "deviation": 10,  # points
            "magic": 123456,  # D.E.V.I identifier
            "comment": f"DEVI_{intent.trade_id[:8]}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

    def _handle_retcode(self, result) -> LiveOrderResult:
        """Map MT5 retcode to internal result."""
        # See Section 6 for full matrix
        status = RETCODE_MAP.get(result.retcode, "UNKNOWN")
        return LiveOrderResult(
            status=status,
            reason=f"broker_retcode:{result.retcode}",
            broker_retcode=result.retcode,
            mt5_ticket=getattr(result, "order", None),
        )
```

### Safety Rules

- `LiveOrderWrapper` is only created if `mode == "live"` AND `arming_token.is_valid`
- Paper mode uses `PaperExecutionAdapter` exclusively
- `mt5.order_send` is never called outside this wrapper
- Every call is logged to `logs/prod/broker_orders_*.jsonl`

---

## 6. Broker Retcode Handling

### Retcode Matrix

| MT5 Retcode | Meaning | Internal Status | Action |
|-------------|---------|-----------------|--------|
| 10009 | TRADE_RETCODE_DONE | `FILLED` | Proceed, log ticket |
| 10008 | TRADE_RETCODE_PLACED | `PLACED` | Proceed (pending), log ticket |
| 10007 | TRADE_RETCODE_PARTIAL | `PARTIAL` | Log, mark as partial fill |
| 10014 | TRADE_RETCODE_INVALID_STOPS | `REJECTED` | Reject, do not retry |
| 10015 | TRADE_RETCODE_INVALID_PRICE | `REJECTED` | Reject, do not retry |
| 10016 | TRADE_RETCODE_INVALID_VOLUME | `REJECTED` | Reject, do not retry |
| 10019 | TRADE_RETCODE_NO_MONEY | `REJECTED` | Reject, trigger kill switch review |
| 10021 | TRADE_RETCODE_MARKET_CLOSED | `REJECTED` | Reject, do not retry |
| 10027 | TRADE_RETCODE_TRADE_DISABLED | `REJECTED` | Reject, do not retry |
| 10031 | TRADE_RETCODE_TOO_MANY_REQUESTS | `RATE_LIMITED` | Wait 1s, retry once, then reject |
| 10032 | TRADE_RETCODE_TIMEOUT | `TIMEOUT` | Retry once, then reject |
| 10033 | TRADE_RETCODE_INVALID_FILL | `REJECTED` | Reject, do not retry |
| 10034 | TRADE_RETCODE_INVALID_ORDER | `REJECTED` | Reject, do not retry |
| 10090 | TRADE_RETCODE_NO_CONNECT | `REJECTED` | Reject, log disconnection |
| Other | Unknown | `UNKNOWN` | Log, reject, operator review |

### Retry Policy

| Status | Retry | Max Retries | Backoff |
|--------|-------|-------------|---------|
| `RATE_LIMITED` | Yes | 1 | 1 second |
| `TIMEOUT` | Yes | 1 | 2 seconds |
| All others | No | 0 | N/A |

### Retcode Logging

Every retcode (success or failure) is logged:
```json
{
  "timestamp": "2026-05-04T12:00:00Z",
  "trade_id": "...",
  "broker_retcode": 10014,
  "internal_status": "REJECTED",
  "reason": "invalid_stops",
  "request": {...},
  "response": {...}
}
```

### Kill Switch on Critical Retcodes

The following retcodes trigger an automatic kill switch review:
- `10019` (no money) → check drawdown
- `10090` (no connect) → check MT5 connection
- Any 3 rejections in 10 minutes → kill switch

---

## 7. Ticket Linking

### Current State (Phase 1.5)

```
trade_id == decision_id  (same UUID)
```

### Phase 2 Design

Explicit chain of identifiers:

```
run_id          -> "run_20260504_120000"        (one per session)
  |
  +-> scan_id   -> "run_20260504_120000_EURUSD"  (one per symbol per run)
  |     |
  |     +-> decision_id -> "dec_..."             (one per decision)
  |     |     |
  |     |     +-> trade_id -> "trade_..."        (generated on EXECUTE)
  |     |     |     |
  |     |     |     +-> mt5_ticket -> 123456789  (broker-assigned integer)
```

### ID Definitions

| ID | Type | Generated By | Uniqueness |
|----|------|--------------|------------|
| `run_id` | string | Session | One per run |
| `scan_id` | string | PaperSession | One per symbol per run |
| `decision_id` | UUID | DecisionEngine | One per decision evaluation |
| `trade_id` | UUID | TradeIntent builder | One per approved trade |
| `mt5_ticket` | int | MT5 broker | Broker-assigned |

### Linkage Storage

```python
@dataclass(frozen=True)
class TradeLinkRecord:
    run_id: str
    scan_id: str
    decision_id: str
    trade_id: str
    mt5_ticket: int | None
    symbol: str
    side: str
    entry_price: float
    status: str  # PENDING, FILLED, REJECTED, CANCELLED
    created_at: str
```

Written to `logs/prod/trade_links_*.jsonl` on every execution attempt.

### Backward Compatibility

During Phase 2 transition:
- `decision_id` and `trade_id` may be the same for paper mode
- Live mode always generates distinct `trade_id`
- `mt5_ticket` is `None` for paper fills

---

## 8. Emergency Stop / Force Close Design

### Concept

Panic button that immediately closes all open positions and disarms the system. Available as both manual operator command and automatic trigger.

### Emergency Stop Triggers

| Trigger | Threshold | Action |
|---------|-----------|--------|
| Manual | Operator command | Close all + disarm |
| Drawdown | Equity < 80% of session start | Close all + disarm + log |
| Broker disconnect | MT5 ping fails for 30s | Close all + disarm + log |
| Kill switch | Any kill switch trigger | Close all + disarm |

### Emergency Stop Procedure

```
Emergency Stop Initiated
  |
  v
[1] Set kill_switch_enabled = true (prevent new orders)
  |
  v
[2] Fetch all open positions from MT5
  |
  v
[3] For each open position:
      - Build close request (opposite side, market order)
      - Send via LiveOrderWrapper
      - Log result
      - If close fails: log and continue (do not block on single failure)
  |
  v
[4] Invalidate live arming token
  |
  v
[5] Disarm system
  |
  v
[6] Flush all telemetry
  |
  v
[7] Log emergency stop event with full audit
  |
  v
[8] Notify operator (console/log)
```

### Force Close Implementation

```python
def emergency_close_all_positions(*, reason: str) -> EmergencyStopResult:
    """Close all open positions immediately."""
    positions = mt5.positions_get()
    results = []

    for pos in positions:
        close_request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY,
            "price": mt5.symbol_info_tick(pos.symbol).bid if pos.type == 0 else mt5.symbol_info_tick(pos.symbol).ask,
            "deviation": 10,
            "magic": pos.magic,
            "comment": f"DEVI_EMERGENCY_CLOSE_{reason[:20]}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
            "position": pos.ticket,  # close by position
        }

        result = mt5.order_send(close_request)
        results.append({
            "ticket": pos.ticket,
            "symbol": pos.symbol,
            "retcode": result.retcode if result else None,
        })

    return EmergencyStopResult(
        positions_closed=len(positions),
        close_results=results,
        reason=reason,
        timestamp=datetime.now(tz=UTC).isoformat(),
    )
```

### Emergency Stop Safety

- Never leave the system in an "armed but unknown" state
- If any close fails, log it but continue with others
- After emergency stop, system requires full re-arming to trade again
- Emergency stop is logged to `logs/prod/emergency_*.jsonl`

---

## 9. Disarm / Rollback Process

### Normal Disarm

```
Operator: disarm --reason "session complete"
  |
  v
[1] Invalidate live arming token
[2] Set kill_switch_enabled = true
[3] Wait for any in-flight orders to complete (max 5s)
[4] Flush telemetry
[5] Log disarm event
[6] Confirm: no valid tokens, kill switch ON
```

### Rollback on Order Failure

```
order_send returns rejection retcode
  |
  v
[1] Log rejection with full request/response
[2] Mark decision as REJECTED_EXECUTION
[3] Do NOT retry (except RATE_LIMITED / TIMEOUT per Section 6)
[4] Check if kill switch should trigger (3 failures in 10 min)
[5] Continue to next symbol (do not abort run)
[6] Leave system in known state: armed or disarmed, no dangling
```

### Rollback on Partial Fill

```
order_send returns PARTIAL (retcode 10007)
  |
  v
[1] Log partial fill
[2] Record position with actual filled volume
[3] Do NOT send another order to complete the fill
[4] Mark as "PARTIAL" in trade log
[5] Continue normal operation (position exists, will be managed)
```

### Rollback on System Crash

```
Process crashes while armed
  |
  v
[1] On restart: system boots into paper mode (default)
[2] No token recovery (tokens are in-memory only)
[3] Operator must explicitly re-arm
[4] Check for any open positions from previous session
[5] If positions found: log warning, operator decides to close or manage
```

### State Machine

```
[DISARMED] --arm--> [ARMED]
   ^                    |
   |                    | kill_switch trigger
   |                    v
   +------------ [EMERGENCY_STOP]
   |                    |
   +--disarm------------+
```

- Only valid transitions: DISARMED → ARMED, ARMED → EMERGENCY_STOP, any → DISARMED
- No transition: ARMED → ARMED (must disarm first)
- EMERGENCY_STOP always leads to DISARMED

---

## 10. First Live Test Plan

### Objective

Prove that the live execution path works correctly with the smallest possible risk exposure.

### Constraints (Non-Negotiable)

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Symbols | EURUSD only | Single pair, most liquid |
| Max orders | 1 | Cannot place more than one order |
| Lot size | Minimum valid lot (0.01) | Minimal risk |
| Manual arming | Required | Operator must explicitly arm |
| Token TTL | 15 minutes | Short window |
| Logs | `logs/prod/` only | Separate from eval/paper |
| Kill switch | Must be tested before arming | Verify it works |
| Pre-trade rechecks | All 5 must pass | No shortcuts |

### Test Sequence

#### Phase A: Safety Verification (Before Any Live Order)

| Step | Action | Expected Result | Evidence |
|------|--------|-----------------|----------|
| A1 | Boot system, confirm mode=paper | System starts in paper | log header |
| A2 | Try to arm without config.live_confirmed | Rejection: live_not_confirmed | log |
| A3 | Set config.live_confirmed=true, try to arm without MT5 | Rejection: mt5_not_connected | log |
| A4 | Connect MT5, try to arm with balance=0 | Rejection: account_balance_zero | log |
| A5 | Arm correctly, then trigger kill switch | Kill switch halts decisions | log |
| A6 | Disarm, confirm token invalid | Re-arm required | log |
| A7 | Re-arm, let token expire | Token auto-invalidates | log |

#### Phase B: Live Order Test (One Order Only)

| Step | Action | Expected Result | Evidence |
|------|--------|-----------------|----------|
| B1 | Arm system with 15-min TTL | Token valid, logged | arming log |
| B2 | Run one decision cycle on EURUSD | Decision evaluated | decision log |
| B3 | If EXECUTE: verify all 5 pre-trade rechecks pass | All rechecks logged | recheck log |
| B4 | order_send executed | Broker retcode logged | broker order log |
| B5 | If retcode=10009: record MT5 ticket | Ticket linked to trade_id | trade link log |
| B6 | If retcode != 10009: handle per Section 6 | Appropriate action taken | log |
| B7 | Verify no second order can be placed | Max orders enforced | compliance log |
| B8 | Disarm system | Token invalidated, kill switch ON | disarm log |
| B9 | Verify position exists in MT5 | Position visible | MT5 terminal |
| B10 | Emergency close position via CLI | Position closed | emergency log |

#### Phase C: Evidence Collection

After the test, verify:
- [ ] All logs under `logs/prod/` only
- [ ] No logs leaked to `logs/eval/`
- [ ] Arming event logged
- [ ] Decision event logged
- [ ] Pre-trade rechecks logged
- [ ] Broker order logged
- [ ] Trade link record exists
- [ ] Disarm event logged
- [ ] No duplicate trade IDs
- [ ] No duplicate MT5 tickets
- [ ] Position was force-closed cleanly

#### Phase D: Go/No-Go Criteria

| Check | Pass Criteria |
|-------|---------------|
| Safety | Kill switch worked, disarm worked, no accidental orders |
| Logging | All events traceable from arming to close |
| Broker | Order reached MT5, retcode interpreted correctly |
| Linkage | run_id → scan_id → decision_id → trade_id → ticket chain valid |
| Cleanup | No dangling positions, no valid tokens, system in DISARMED state |

### Rollback Plan

If any step in Phase B fails:
1. Immediately disarm
2. Do not retry the failed order
3. Log failure with full context
4. Switch back to paper mode for diagnosis
5. No further live attempts until root cause identified and fixed

### Sign-Off Required

Before executing the first live test, the following must be signed off:
- [ ] Arming token design reviewed (Section 1)
- [ ] Kill switch tested and verified (Section 2)
- [ ] Max orders enforced (Section 3)
- [ ] All 5 pre-trade rechecks implemented (Section 4)
- [ ] order_send wrapper implemented (Section 5)
- [ ] Broker retcode handling implemented (Section 6)
- [ ] Ticket linkage implemented (Section 7)
- [ ] Emergency stop tested on paper first (Section 8)
- [ ] Disarm/rollback tested (Section 9)
- [ ] This test plan (Section 10) reviewed and approved

**Phase 2 coding begins only after all 10 sign-off items are checked.**
