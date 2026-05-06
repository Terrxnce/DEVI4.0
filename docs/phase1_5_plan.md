# Phase 1.5 Lean Implementation Plan

Goal: Bridge gaps before Phase 2 without overbuilding. Fix 5 essentials only.

---

## 1. Runtime Order Tracking

**Problem:** `current_orders_this_run` reads from static config, not runtime state.

**Fix:**

1. Create `src/core/runtime_state.py` with a `RuntimeState` dataclass:
   ```python
   @dataclass
   class RuntimeState:
       orders_this_run: int = 0
       decisions_this_run: list[str] = field(default_factory=list)
       trades_this_run: list[str] = field(default_factory=list)
       run_start_time: datetime | None = None
   ```

2. `PaperSession.run()` instantiates `RuntimeState` at start of each run and passes it to `evaluate_decision()`.

3. On `EXECUTE` decision:
   - `RuntimeState.orders_this_run += 1`
   - `RuntimeState.trades_this_run.append(trade_id)`

4. Supervisor gate reads `RuntimeState.orders_this_run` instead of config. Config `max_orders_per_run` remains as the ceiling.

5. Per-bar: if `orders_this_run >= max_orders_per_run`, gate returns `REJECTED_COMPLIANCE` with reason `max_orders_per_run_exceeded`.

6. On new `run_id`, instantiate fresh `RuntimeState` (reset per run, not persisted).

---

## 2. Dynamic Symbol Metadata from MT5

**Problem:** EURUSD hardcoded defaults in config. Other symbols would use wrong point/contract/lot values.

**Fix:**

1. `MT5DataSource.fetch_instrument_profile(symbol)` already returns `InstrumentProfile` with MT5-derived fields.

2. In `PaperSession.run()`:
   - Call `fetch_instrument_profile(symbol)` per symbol before any decision logic.
   - Extract: `point`, `digits`, `contract_size`, `lot_step`, `volume_min`, `volume_max`, `trade_tick_size`.

3. Validation:
   - If any critical field is `None` or `0`: log `missing_instrument_data:{symbol}`, skip that symbol, do not trade.
   - Critical fields: `point`, `contract_size`, `volume_step`.
   - Non-critical fallback: `volume_min` defaults to `volume_step`, `volume_max` defaults to `100.0`.

4. Pass the resolved `InstrumentProfile` into `evaluate_decision()` for lot sizing and SL distance computation.

5. Remove EURUSD hardcoded defaults from paper session. Config `instrument` section remains as a fallback of last resort only.

---

## 3. Multi-Symbol Paper Run

**Problem:** Only EURUSD tested. Need proof of 4 pairs.

**Fix:**

1. Extend `PaperSession` to accept a list of symbols:
   ```python
   symbols: list[str] = ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"]
   ```

2. Per scan cycle, iterate symbols in deterministic order (sorted list).

3. Per symbol:
   - Fetch own M15/H1 bars, tick, profile
   - Run detectors on that symbol's bars
   - Evaluate decision
   - If `EXECUTE`: paper fill using symbol-specific tick
   - Write decision + trade telemetry
   - Write snapshot with `snapshot_id` prefixed by symbol

4. Deduplication:
   - Decision IDs are UUIDs — naturally unique
   - Trade IDs are UUIDs — naturally unique
   - Snapshots use `{run_id}_{symbol}_snapshot` — no overlap
   - Telemetry files are daily JSONL — append-only, no overwrite

5. Result aggregation:
   - Return per-symbol decisions in `result.symbol_results: dict[str, SymbolResult]`
   - Each `SymbolResult` has: `decision`, `failure_code`, `bars_count`, `paper_fill`, `snapshot_id`

6. One decision per symbol per bar. No cross-symbol state (each symbol is independent).

---

## 4. Stability Proof

**Problem:** Single M15 cycle proven. Need proof of continuous operation.

**Fix:**

1. Create `sandbox/run_paper_stability.py` that:
   - Connects to MT5
   - Loops for N M15 cycles (e.g., 5 cycles = ~75 minutes)
   - Each cycle: fetch fresh data for all 4 symbols, run decision, log telemetry
   - Sleep until next M15 bar boundary (e.g., `time.sleep(seconds_until_next_bar)`)

2. Evidence to collect:
   - Cycle count
   - Per-symbol decision count
   - Duplicate decision check: `len(decisions) == len(set(decision_ids))`
   - Duplicate trade check: `len(trades) == len(set(trade_ids))`
   - Telemetry validity: all `record_valid == True`
   - Snapshot count: one per symbol per cycle
   - Replay check: latest snapshot can be loaded and re-hydrated
   - No broker calls: `MT5PaperGuard` still active
   - Live blocked: any live mode attempt rejected

3. Abort conditions:
   - MT5 disconnects → log and exit cleanly
   - Any broker method called → raise and exit
   - Live mode requested → reject
   - Duplicate detected → log and exit

4. Expected outcome: 4 symbols × 5 cycles = 20 decision records, ≤20 snapshots, 0–N trades (only on EXECUTE), all valid, no duplicates, no broker calls.

---

## 5. Minimal Position Tracking

**Problem:** No concept of "open position" exists. Paper trades are created but not tracked.

**Fix:**

1. Create `src/execution/position_tracker.py` with:
   ```python
   @dataclass
   class PaperPosition:
       trade_id: str
       decision_id: str
       ticket: int
       symbol: str
       side: str
       entry_price: float
       sl: float
       tp: float
       lot_size: float
       status: str  # "OPEN" | "CLOSED"
       open_time: str
       close_time: str | None
       close_reason: str | None
   ```

2. `PaperPositionTracker` (in-memory only):
   - `open_position(fill: PaperFillResult)` → creates `PaperPosition(status="OPEN")`
   - `close_position(trade_id, close_price, reason)` → marks `CLOSED`, sets `close_time`
   - `get_open_positions()` → list of open positions
   - `get_all_positions()` → list of all positions

3. In `PaperSession.run()`:
   - Instantiate `PaperPositionTracker`
   - On paper fill: call `tracker.open_position(fill)`
   - After scan: print open position count

4. Telemetry:
   - Write position state to `positions_*.jsonl` each cycle
   - One record per position per cycle (append-only)
   - On close: write final position record with `status="CLOSED"`

5. **Explicitly NOT included:**
   - No trailing stop
   - No breakeven trigger
   - No partial close
   - No margin monitoring
   - No PnL tracking
   - No broker position sync

---

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/core/runtime_state.py` | **Create** | In-memory order/decision counter per run |
| `src/execution/position_tracker.py` | **Create** | Minimal open/close tracking, no advanced management |
| `src/execution/paper_session.py` | **Modify** | Accept symbol list, pass RuntimeState, use dynamic profile, aggregate multi-symbol results |
| `src/supervisor/compliance.py` | **Modify** | Read `orders_this_run` from RuntimeState, not config |
| `src/decision/engine.py` | **Modify** | Accept and pass `RuntimeState`, `InstrumentProfile` |
| `src/data/mt5_source.py` | **Modify** | Ensure all critical fields returned; hard reject on missing critical data |
| `src/risk/evaluator.py` | **Modify** | Use dynamic `InstrumentProfile` instead of config defaults |
| `sandbox/run_paper_stability.py` | **Create** | Continuous multi-cycle, multi-symbol paper session proof |
| `tests/execution/test_position_tracker.py` | **Create** | Open/close lifecycle, no duplicates |
| `tests/execution/test_multi_symbol.py` | **Create** | Deterministic ordering, per-symbol independence |
| `tests/execution/test_runtime_state.py` | **Create** | Counter increments, reset per run, max_orders enforcement |

## Files NOT to Touch

| File | Reason |
|------|--------|
| `src/execution/live_adapter.py` | Live execution is Phase 2 |
| `src/execution/order_wrapper.py` | Broker wrapper is Phase 2 |
| `src/execution/trailing_stop.py` | Trailing management is Phase 2+ |
| `src/execution/breakeven.py` | Breakeven logic is Phase 2+ |
| `src/config/defaults.json` | Instrument defaults remain as fallback only |
| `src/data/mt5_guard.py` | Already correct; no broker methods allowed |

## Acceptance Criteria

| # | Check | How Verified |
|---|-------|------------|
| 1 | `orders_this_run` comes from runtime state, not config | Test: increment on EXECUTE, reject at limit |
| 2 | EURUSD, GBPUSD, AUDUSD, NZDUSD each get own MT5-derived profile | Test: assert profile fields match MT5 for each symbol |
| 3 | Missing critical data → skip symbol, no trade | Test: mock missing `contract_size`, assert skipped |
| 4 | Multi-symbol run produces 4 independent decisions | Test: 4 symbols, 4 decision records, no cross-contamination |
| 5 | No duplicate decisions or trades across symbols/cycles | Stability run: `set(decision_ids)` equals list length |
| 6 | Snapshots are per-symbol, replayable | Test: load snapshot, assert bars/context match |
| 7 | Position tracker records open and closed states | Test: open → close → status changes |
| 8 | No broker methods called during stability run | MT5PaperGuard + log inspection |
| 9 | Live mode rejected | Execution gate test |
| 10 | All tests pass + compileall clean | `pytest -q` + `compileall -q` |

---

## Scope Boundary

**In scope:** Runtime counters, dynamic profiles, 4 symbols, stability loop, minimal position open/close.

**Out of scope:** Live execution, broker orders, trailing, partials, margin monitoring, PnL, multi-timeframe coordination, session persistence across process restarts.
