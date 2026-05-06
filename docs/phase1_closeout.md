# D.E.V.I Phase 1 Closeout Report

## Fully Accepted

| Component | Status | Evidence |
|-----------|--------|----------|
| **Data ingestion** | Accepted | CSV source (`src/data/csv_source.py`), MT5 source (`src/data/mt5_source.py`) with bars, tick, account info, symbol profile fetching |
| **Detectors** | Accepted | Order block, BOS, FVG, liquidity sweep, rejection, engulfing — all with quality scoring and age filtering |
| **Context/trend/regime** | Accepted | EMA trend classification, HTF agreement, regime classification (trending/neutral/ranging/expanding), ATR percentile |
| **Setup/confluence** | Accepted | Tier A/B/C confluence with confirmation counting, quality penalties, structural validation |
| **Exits** | Accepted | SL from structure/ATR, TP from structure, RR validation (min 1.3), tp_debug logging |
| **Risk sizing** | Accepted | Real lot calculation from balance, entry, SL, point, contract size, lot step, min/max lot. Risk deviation check (<=20%). Canonical failure codes |
| **Supervisor gate** | Accepted | Max orders per run enforcement, live arming requirements, auto-execute checks |
| **Execution gate** | Accepted | Mode enforcement, live execution blocked in Phase 1, paper/eval allowed |
| **Paper execution** | Accepted | BUY fills at ask, SELL fills at bid, synthetic tickets, no broker methods, MT5-derived pricing |
| **Telemetry** | Accepted | Decision records, trade logs, scan logs, snapshots, validation (`record_valid`, `record_invalid_reasons`) |
| **Snapshots/replay** | Accepted | `SnapshotRecord` with full bar data, context, structures; replay infrastructure in place |
| **Live safety block** | Accepted | `live_execution_not_allowed_phase1` in execution gate; `MT5PaperGuard` blocks all broker methods |

## Still Deferred / Not Accepted

| Item | Reason | Phase |
|------|--------|-------|
| Real broker execution | Safety: no live orders until full design review | Phase 2 |
| Real live order placement | Requires arming, kill switch, rollback design | Phase 2 |
| Real position lifecycle | Position tracking, broker sync, external closure handling | Phase 2+ |
| Trailing/breakeven/partials | Config exists but not wired to execution | Phase 2+ |
| Distinct `trade_id` from `decision_id` | Currently same UUID; needs link field | Phase 2 cleanup |
| Per-symbol instrument profiles | Only EURUSD defaults in config | Phase 2 |
| Runtime-state order count | `current_orders_this_run` reads from static config, not runtime | Phase 2 wiring |
| Long paper session stability | Single-run proven; multi-hour sessions not tested | Phase 2 validation |
| Multiple symbol support | EURUSD only tested | Phase 2 |

## Test Status

```
python -m pytest -q
# 160 passed

python -m compileall src tests -q
# Exit code 0 (no syntax errors)
```

### Test Coverage Summary

| Module | Tests | Key Coverage |
|--------|-------|-------------|
| `tests/data/` | 6 | MT5 guard blocks forbidden methods, data-only source |
| `tests/context/` | 6 | Trend, regime, session classification |
| `tests/detectors/` | 21 | All 6 detector types with quality/age filtering |
| `tests/exits/` | 8 | SL/TP planning, RR validation, tp_debug |
| `tests/risk/` | 9 | Real lot sizing, canonical codes, deviation check |
| `tests/supervisor/` | 5 | Max orders, live arming, compliance |
| `tests/execution/` | 14 | Paper fill logic, safety, gate blocking |
| `tests/ops/` | 6 | Decision records, telemetry, schema validation |
| `tests/decision/` | 16 | Full pipeline, paper end-to-end, MT5 session |
| `tests/position/` | 2 | TradeIntent builder only |
| **Total** | **160** | |

## Safety Proof

### Live Execution Blocked

```python
# src/execution/gate.py:28
if runtime_mode == "live":
    return ExecutionVerdict(approved=False, reason="live_execution_not_allowed_phase1")
```

### Paper Mode Data-Only MT5

**Allowed MT5 calls:**
- `mt5.initialize()` / `mt5.shutdown()`
- `copy_rates_from_pos()` / `copy_rates_range()`
- `symbol_info()` / `symbol_info_tick()`
- `account_info()`

**Blocked MT5 calls (20+ methods):**
- `order_send`, `order_check`, `order_modify`, `order_cancel`
- `position_close`, `position_modify`, `positions_total`
- `trade_order_send`, `trade_position_close`
- All margin/profit calculation methods

**Guard implementation:**
```python
# src/data/mt5_guard.py
class MT5PaperGuard:
    def __getattr__(self, name: str):
        if name in FORBIDDEN_METHODS:
            raise MT5BrokerMethodForbidden(f"'{name}' forbidden in paper mode")
        return getattr(self._client, name)
```

### No Broker Execution Path

- `PaperExecutionAdapter` has no `_broker`, `_mt5`, or `_connection` attributes
- `paper_retcode` is a hardcoded synthetic placeholder (10009)
- All fill prices calculated mathematically from MT5-derived bid/ask

---

## Recommended Phase 2 Design

**Do not implement until approved.**

### 1. Live Arming Token

```
Concept:
- Live mode requires an explicit "arm" action with a time-limited token
- Token expires after configurable duration (default: 30 minutes)
- Token is session-specific and bound to run_id

Safety:
- No live execution without valid token
- Token cannot be persisted; must be re-armed each session
- Disarm immediately invalidates token
```

### 2. Max Orders Per Run

```
Current: reads from config (static)
Phase 2: reads from runtime state (in-memory counter)

Design:
- RuntimeState tracks orders_this_run, orders_today, orders_this_symbol
- Incremented on each EXECUTE decision
- Reset on new run or session boundary
- Configurable limits per run, per day, per symbol
```

### 3. Kill Switch

```
Concept:
- Immediate halt of all new decisions
- Already in config (execution.kill_switch_enabled: true)

Design:
- Check at top of evaluate_decision()
- If enabled and triggered: return REJECTED_EXECUTION, failure_code="kill_switch_active"
- Trigger sources: config flag, runtime signal, drawdown breach
- Cannot be bypassed by any profile
```

### 4. Pre-Trade Spread/Risk Recheck

```
Concept:
- Re-validate conditions between decision and execution
- MT5 data may have changed during pipeline runtime

Design:
- Fetch fresh tick before calling PaperExecutionAdapter
- Compare fresh spread to decision-time spread
- If spread increased beyond threshold (e.g., 2x): reject or re-evaluate
- Re-check account balance before sizing (may have changed)
- If risk deviation exceeds 20% after recheck: reject
```

### 5. Order Send Wrapper

```
Concept:
- Single controlled entry point for broker order_send
- All real orders go through this wrapper

Design:
class LiveOrderWrapper:
    def send_order(self, intent: TradeIntent) -> LiveOrderResult:
        # 1. Verify live token is valid
        # 2. Verify kill switch is OFF
        # 3. Re-check spread/risk
        # 4. Call mt5.order_send()
        # 5. Log broker retcode
        # 6. If failed: log reason, do not retry blindly
        # 7. If success: link ticket to decision_id
        pass

Safety:
    - Only instantiated in live mode
    - Paper mode never creates this wrapper
    - All broker methods logged with full audit trail
```

### 6. Broker Retcode Handling

```
Design:
- Map MT5 retcodes to canonical internal status
- 10009 = TRADE_RETCODE_DONE (success)
- 10014 = TRADE_RETCODE_INVALID_STOPS (SL/TP invalid)
- 10019 = TRADE_RETCODE_NO_MONEY (margin check failed)
- 10027 = TRADE_RETCODE_TRADE_DISABLED (instrument disabled)

Response:
- Success: proceed to position tracking
- Rejectable: log, do not retry, mark decision as REJECTED_EXECUTION
- Retryable (rate limit): wait, retry once, then reject
```

### 7. Ticket Linking

```
Current: trade_id == decision_id (same UUID)
Phase 2: Separate IDs with explicit link

Design:
- decision_id: UUID from evaluate_decision
- trade_id: UUID generated on fill creation
- decision_id stored on TradeIntent and in trade log
- ticket: broker-assigned integer (MT5 OrderTicket())
- Link chain: decision_id -> trade_id -> ticket
```

### 8. Emergency Stop

```
Concept:
- Panic button to close all open positions immediately
- Available in both paper and live modes

Design:
- CLI command: devi emergency-stop --reason "manual"
- Calls mt5.position_close() for all open positions
- Logs emergency action with full audit
- Disarms live mode automatically
- Triggers kill switch for remainder of session
```

### 9. Rollback / Disarm Process

```
Concept:
- Clean shutdown that leaves no dangling state
- Reversible: can disarm without losing data

Design:
- Disarm action:
  1. Set live_confirmed = false
  2. Invalidate live token
  3. Enable kill switch
  4. Flush all pending telemetry
  5. Log disarm event with reason

- Rollback on failure:
  1. If order_send fails: mark decision as REJECTED_EXECUTION
  2. If partial fill: handle according to broker status
  3. Do not retry failed orders automatically
  4. Always leave system in known state (armed or disarmed)
```

---

## Acceptance Gate for Phase 2

Before any live execution code is written, the following must be approved:

1. [ ] Live arming token design reviewed
2. [ ] Kill switch behavior defined and tested
3. [ ] Order send wrapper design reviewed
4. [ ] Emergency stop procedure documented
5. [ ] Rollback/disarm process defined
6. [ ] Per-symbol instrument profile design approved
7. [ ] Runtime state management (orders, positions) designed
8. [ ] Broker retcode handling matrix approved
9. [ ] Ticket linking schema approved
10. [ ] All above items have passing tests in paper mode first

**Phase 2 implementation begins only when all 10 items are checked.**
