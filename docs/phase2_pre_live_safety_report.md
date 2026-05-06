# Phase 2 Pre-Live Safety Report

**Date:** 2026-05-04
**Status:** ALL GATES IMPLEMENTED AND TESTED. REAL `order_send` BLOCKED.
**Approval Required Before:** Live trading can commence.

---

## 1. Exact Live Gate Order

Live execution passes through **two sequential layers** before any broker call.

### Layer A: Decision Pipeline (`evaluate_supervisor`)

Called from `src/decision/engine.py:evaluate_decision()` for every symbol in every cycle.

| Order | Check | Enforced On | Failure Code |
|-------|-------|-------------|--------------|
| 1 | Kill switch | Live mode only | `kill_switch_active:{reason}` |
| 2 | max_orders_per_run > 0 | All modes | `max_orders_per_run_invalid` |
| 3 | Runtime orders < max | All modes | `max_orders_per_run_exceeded` |
| 4 | `live_confirmed` in config | Live mode only | `live_not_confirmed_in_config` |
| 5 | Arming service armed | Live mode only | `live_not_armed` |
| 6 | Token valid (not expired) | Live mode only | `arming_token_invalid` |
| 7 | Symbol in token authorized list | Live mode only | `symbol_not_authorized_for_live` |

If Layer A rejects, the decision returns `FinalDecision.REJECTED_COMPLIANCE`.
Paper/eval/shadow modes skip checks 4–7 entirely; kill switch is also skipped.

### Layer B: LiveOrderWrapper (`src/execution/live_wrapper.py`)

Called only when Layer A passes AND the decision is `EXECUTE` in live mode.

| Order | Check | Failure Status | Source |
|-------|-------|---------------|--------|
| 1 | Arming token valid | `blocked_not_armed` | `ArmingService.get_valid_token()` |
| 2 | Symbol in token list | `blocked_symbol_not_authorized` | Token.symbols |
| 3 | Kill switch clear | `blocked_kill_switch:{reason}` | `KillSwitch.evaluate()` |
| 4 | Runtime orders < max | `blocked_max_orders_exceeded` | `RuntimeState.orders_this_run` |
| 5 | Pre-trade rechecks pass | `blocked_recheck:{detail}` | `PreTradeRecheck.run_all()` |
| 6 | **(FUTURE)** `mt5.order_send` | `sent=True` | **NOT IMPLEMENTED YET** |

If gates 1–5 all pass, the wrapper returns:

```python
LiveOrderResult(
    sent=False,
    status="ready_to_send",
    reason="All gates passed. Broker call intentionally mocked for Phase 2 safety.",
    ...
)
```

**No broker call is made.**

---

## 2. All Failure Codes

### Supervisor Gate (`SupervisorVerdict.reason`)

| Code | Trigger | Mode |
|------|---------|------|
| `kill_switch_active:{reason}` | Kill switch latched or config flag | Live only |
| `max_orders_per_run_invalid` | Config has max_orders < 1 | All |
| `max_orders_per_run_exceeded` | orders_this_run >= max_orders | All |
| `live_not_confirmed_in_config` | `execution.live_confirmed=False` | Live only |
| `live_not_armed` | No arming service or not armed | Live only |
| `arming_token_invalid` | Token expired or missing | Live only |
| `symbol_not_authorized_for_live` | Context symbol not in token.symbols | Live only |

### LiveOrderWrapper (`LiveOrderResult.status`)

| Status | Trigger |
|--------|---------|
| `blocked_not_armed` | `arming_service.get_valid_token()` returns None |
| `blocked_symbol_not_authorized` | `intent.symbol not in token.symbols` |
| `blocked_kill_switch:{reason}` | `kill_switch.evaluate().triggered == True` |
| `blocked_max_orders_exceeded` | `runtime_state.orders_this_run >= max_orders_per_run` |
| `blocked_recheck:{detail}` | Any pre-trade recheck fails |
| `ready_to_send` | All gates passed (mocked — no broker call) |

### Pre-Trade Recheck (`RecheckVerdict.reason`, prefixed by check name)

| Full Reason Pattern | Check | Condition |
|---------------------|-------|-----------|
| `spread:spread_widened:{decision}->{current}` | Spread | Current > decision * 2.0 |
| `spread:spread_zero` | Spread | Bid == ask |
| `account:account_balance_zero` | Account | Balance <= 0 |
| `account:equity_below_threshold:{equity}<{threshold}` | Account | Equity < balance * 0.50 |
| `account:insufficient_margin:{free}<{required}` | Account | Free margin < rough margin |
| `risk:lot_size_deviation:{pct}%>{max}%` | Risk | Recalculated lot deviates > 20% |
| `symbol:symbol_trade_disabled` | Symbol | `trade_allowed=False` |
| `symbol:symbol_trade_mode_invalid:{mode}` | Symbol | `trade_mode != 0` |
| `market:market_closed` | Market | `session_deals=False` |
| `{check}:recheck_no_data_source` | Any | Data source is None |

---

## 3. Proof: Paper / Eval / Shadow Cannot Execute Live

### By Design

The live execution path is **only reachable** when **all** of the following are true simultaneously:

1. `config["runtime"]["mode"] == "live"` (case-insensitive)
2. `config["execution"]["live_confirmed"] == True`
3. `ArmingService.is_armed == True` with a valid, non-expired token
4. `KillSwitch.is_triggered == False`
5. `RuntimeState.orders_this_run < max_orders_per_run`
6. `PreTradeRecheck.run_all()` returns `passed=True`
7. `LiveOrderWrapper.send()` is called with all required arguments

### Evidence from Code

**Paper mode ignores kill switch and arming:**

```python
# src/supervisor/gate.py:33-39
if mode == "live" and kill_switch is not None:
    ks_verdict = kill_switch.evaluate(...)
    if ks_verdict.triggered:
        return SupervisorVerdict(approved=False, ...)
```

Kill switch is only checked when `mode == "live"`.

**Live mode gates only enforced when `mode == "live"`:**

```python
# src/supervisor/gate.py:56-68
if mode == "live":
    if not live_confirmed: reject
    if not armed: reject
    if token invalid: reject
    if symbol not authorized: reject
```

**Paper fills use synthetic adapter, not LiveOrderWrapper:**

```python
# src/execution/paper_session.py
# PaperSession uses PaperExecutionAdapter, not LiveOrderWrapper.
# PaperExecutionAdapter.calculate_fill_price() returns synthetic prices.
# No broker methods are called.
```

**MT5PaperGuard blocks broker execution methods:**

```python
# src/data/mt5_guard.py
FORBIDDEN_METHODS = {"order_send", "order_check", ...}
# Any call raises MT5BrokerMethodForbidden
```

### Test Evidence

| Test File | Assertion |
|-----------|-----------|
| `tests/supervisor/test_live_gates.py::test_paper_ignores_arming_and_kill_switch` | Paper passes with kill switch triggered + no arming |
| `tests/execution/test_paper_fill_controlled.py` | Paper adapter has no `order_send` attribute |
| `tests/data/test_mt5_guard.py` | `order_send` raises `MT5BrokerMethodForbidden` |

---

## 4. Proof: `order_send` Is Not Called Yet

### Static Analysis

Running `rg "order_send" src/ --include "*.py"` across the production codebase (`src/`) yields only:

1. **Comments and docstrings** in `src/execution/live_wrapper.py` — describing the future step.
2. **Comment in `src/execution/recheck.py`** — "before order_send is called" (safety note).
3. **Comment in `src/execution/paper_session.py`** — "No order_send" (safety rule).
4. **Guard definition in `src/data/mt5_guard.py`** — listing `order_send` as a forbidden method.

**Zero function calls to `order_send` exist in production code.**

### Runtime Proof

The `LiveOrderWrapper.send()` method returns at line 128 with:

```python
return LiveOrderResult(
    sent=False,
    status="ready_to_send",
    reason="All gates passed. Broker call intentionally mocked for Phase 2 safety.",
    ...
)
```

There is no code path after this return. The function exits here. No MT5 client is ever invoked.

### Test Evidence

| Test | Assertion |
|------|-----------|
| `test_ready_to_send_when_all_gates_pass` | `result.sent is False`, `status == "ready_to_send"` |
| `test_wrapper_has_no_order_send_attribute` | `not hasattr(wrapper, "order_send")` |

---

## 5. What Will Change Once Real `order_send` Is Approved

The following changes will be made **only after explicit operator approval**:

### 5.1 `LiveOrderWrapper.send()` — Add Real Broker Call

Replace the mocked return block with:

```python
# AFTER APPROVAL ONLY:
ticket = self._data.mt5_client.order_send(
    {
        "action": mt5_client.TRADE_ACTION_DEAL,
        "symbol": intent.symbol,
        "volume": intent.risk_verdict.lot_size,
        "type": mt5_client.ORDER_TYPE_BUY if intent.direction == Direction.BULLISH else mt5_client.ORDER_TYPE_SELL,
        "price": intent.entry_price,
        "sl": intent.exit_plan.stop_loss,
        "tp": intent.exit_plan.take_profit,
        "deviation": 10,
        "magic": int(token.token_id[:8], 16),
        "comment": f"devi:{token.run_id}:{intent.trade_id}",
    }
)
return LiveOrderResult(
    sent=ticket.retcode == 10009,
    status="sent" if ticket.retcode == 10009 else f"broker_reject:{ticket.retcode}",
    reason=ticket.comment,
    ticket=ticket.ticket,
    broker_retcode=ticket.retcode,
    ...
)
```

### 5.2 New Fields in `LiveOrderResult`

- `ticket: int | None`
- `broker_retcode: int | None`
- `slippage: float | None`
- `execution_time: str | None`

### 5.3 Telemetry Logging

Add `logs/prod/live_orders_*.jsonl` writer inside `LiveOrderWrapper`.

### 5.4 No Changes to Gate Logic

The arming, kill switch, max orders, and pre-trade rechecks **do not change**. The only change is replacing the mocked return with a real broker call.

### 5.5 Rollback Plan

If real `order_send` causes issues, revert `live_wrapper.py` to the current mocked version. All gates remain intact.

---

## 6. First Live Test Plan

### Constraints (Hard Limits)

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Symbol | EURUSD only | Most liquid, lowest spread, least slippage |
| Lot size | 0.01 only | Minimum position size = ~$1,100 at 1.1000 |
| Max orders | 1 per run | Cannot accumulate unintended positions |
| Max orders per day | 1 | Single test per day until validated |
| Arming | Manual operator required | No automated arming |
| Kill switch | Must be wired and active | Any anomaly = immediate halt |
| Pre-trade rechecks | All 5 required | Spread, account, risk, symbol, market |
| Telemetry | Full snapshot + decision + order | Every event logged to `logs/prod/` |

### Pre-Flight Checklist

- [ ] MT5 connected to demo account with >$1,000 balance
- [ ] `execution.live_confirmed = true` in config
- [ ] `execution.kill_switch_enabled = true` in config (armed for safety)
- [ ] `runtime.mode = "live"` in config
- [ ] `execution.max_orders_per_run = 1` in config
- [ ] Operator manually arms: `arming_service.arm(..., symbols=["EURUSD"], max_orders=1, ...)`
- [ ] Verify `kill_switch.is_triggered == False`
- [ ] Verify `runtime_state.orders_this_run == 0`
- [ ] Pre-trade rechecks: all 5 pass on EURUSD
- [ ] Paper mode session completed successfully in same environment first

### Execution Flow

```
Cycle Start
  ├── MT5 connected?
  ├── Mode == "live"
  ├── live_confirmed == true
  ├── ArmingService armed for EURUSD, 0.01 lot, max 1 order
  ├── KillSwitch clear
  ├── RuntimeState.orders_this_run == 0
  ├── Decision pipeline: full confluence + exit + risk
  ├── Supervisor gate: all live gates pass
  ├── Pre-trade rechecks: spread, account, risk, symbol, market ALL pass
  ├── LiveOrderWrapper.send() → READY_TO_SEND
  └── [APPROVAL REQUIRED] Real order_send executed
```

### Abort Conditions (Any One Triggers Halt)

| Condition | Action |
|-----------|--------|
| Spread > 2x decision spread | Skip cycle, log recheck failure |
| Equity < 50% of balance | Skip cycle, log account failure |
| Kill switch triggered | Skip all symbols, remain in HOLD |
| Token expired | Skip cycle, require re-arming |
| Broker error rate > 3 in 10 min | Trigger kill switch, halt |
| Any unexpected exception | Trigger kill switch, log, halt |

### Post-Trade Verification

- [ ] `LiveOrderResult.ticket` captured and logged
- [ ] MT5 terminal shows open position at 0.01 lot EURUSD
- [ ] Position SL/TP match intent exit plan
- [ ] Telemetry file contains complete order record
- [ ] Position manually closed within 24 hours (test only)

---

## 7. Test Summary

| Module | Tests | Status |
|--------|-------|--------|
| `src/core/arming.py` | 8 | All pass |
| `src/core/kill_switch.py` | 11 | All pass |
| `src/supervisor/gate.py` (live gates) | 11 | All pass |
| `src/execution/recheck.py` | 18 | All pass |
| `src/execution/live_wrapper.py` | 11 | All pass |
| **Full suite** | **218** | **All pass** |

### Compile Check

```
python -m compileall src tests sandbox -q
# exit 0
```

---

## 8. Sign-Off

This report certifies that:

1. All safety gates are implemented and tested.
2. No `order_send` call exists in production code.
3. Paper/eval/shadow modes are completely isolated from live execution.
4. The first live test plan is ready with hard constraints.

**Operator approval is required before implementing real `order_send`.**
