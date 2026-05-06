# First Live Test — Dry-Run Checklist (No Real Trade)

This checklist is for a **controlled dry-run only**. It validates that all gates, limits, linkage, and telemetry are correct **without placing a real live trade**.

---

## 1) Exact Command Sequence (PowerShell)

Run from repo root: `c:\Users\Index\Desktop\DEVI.4.0`

### Step A — Baseline safety validation

```powershell
python -m pytest tests/execution/test_live_wrapper.py tests/execution/test_retcode.py tests/supervisor/test_live_gates.py tests/supervisor/test_gate.py tests/execution/test_safety.py -q
python -m compileall src tests sandbox -q
```

Expected: all tests pass, compile exits 0.

### Step B — Create a dry-run config copy

```powershell
Copy-Item src\config\defaults.json src\config\live_dry_run.json -Force
```

Edit `src/config/live_dry_run.json` with required values from Section 2 below.

### Step C — CLI preflight sanity (still non-trading)

```powershell
python run.py doctor --namespace eval --mode paper --config src/config/live_dry_run.json --run-id dryrun_precheck
python run.py preflight --namespace eval --mode paper --config src/config/live_dry_run.json --run-id dryrun_preflight
```

Expected: PASS outputs.

### Step D — Live gate proof (no arming = fail)

```powershell
python run.py run live --namespace eval --config src/config/live_dry_run.json --run-id dryrun_live_gate
```

Expected: `REJECTED_EXECUTION`, reason `live_not_armed`.

### Step E — Mocked execution proof only (no real broker)

```powershell
python -m pytest tests/execution/test_live_wrapper.py::test_order_send_success_retcode_10009 -q
python -m pytest tests/execution/test_live_wrapper.py::test_order_send_failure_retcode_10027 -q
python -m pytest tests/execution/test_live_wrapper.py::test_telemetry_logged_on_success -q
```

Expected: all pass. This proves wrapper wiring/retcode/telemetry/ticket linkage using mocked MT5 only.

---

## 2) Required Config Values

Use `src/config/live_dry_run.json`:

```json
{
  "runtime": {
    "mode": "live",
    "namespace": "eval",
    "logs_root": "logs"
  },
  "execution": {
    "live_confirmed": true,
    "auto_execute_live": false,
    "max_orders_per_run": 1,
    "kill_switch_enabled": true
  },
  "instrument": {
    "symbol": "EURUSD",
    "min_lot": 0.01,
    "lot_step": 0.01
  }
}
```

Notes:
- Keep `max_orders_per_run = 1`.
- Keep symbol pinned to `EURUSD`.
- For dry-run, `auto_execute_live` remains `false`.

---

## 3) Arming Steps (Current State)

`python run.py live arm` currently exists as a **placeholder CLI** and does not persist runtime token state.

For deterministic dry-run validation, use in-process arming (tests already do this):

```powershell
python -c "from src.core.arming import ArmingService; s=ArmingService(); t=s.arm(run_id='dryrun_001', armed_by='operator', reason='dryrun', symbols=['EURUSD'], max_orders=1, ttl_minutes=30); print('armed=', s.is_armed, 'token=', t.token_id if t else None)"
```

Dry-run acceptance condition:
- arming service returns a valid token,
- token symbols include only `EURUSD`,
- token max_orders is `1`.

---

## 4) Preflight Checks (Must All Pass)

Before any real execution approval, verify:

- MT5 wiring tests pass with mocked client only (`test_live_wrapper.py`).
- Supervisor live-gate tests pass (`test_live_gates.py`, `test_gate.py`).
- Paper safety tests pass (`test_safety.py`).
- Config enforces:
  - `runtime.mode = live`
  - `execution.live_confirmed = true`
  - `execution.max_orders_per_run = 1`
  - `execution.kill_switch_enabled = true`
  - symbol = `EURUSD`
- Arming token rules:
  - manual arming required,
  - valid (not expired),
  - authorized symbols only (`EURUSD`).
- Pre-trade rechecks all pass in dry-run scenarios.

---

## 5) Abort Conditions

Abort immediately if any condition below occurs:

- `live_not_armed` or `arming_token_invalid`
- `kill_switch_active:*` / `blocked_kill_switch:*`
- `max_orders_per_run_exceeded` / `blocked_max_orders_exceeded`
- any `blocked_recheck:*` status:
  - spread widened/zero,
  - account balance/equity/margin failure,
  - lot size deviation,
  - symbol disabled/trade mode invalid,
  - market closed
- any broker wiring exception status:
  - `blocked_no_mt5_client`
  - `blocked_broker_exception`
  - `blocked_broker_none`

---

## 6) Verify Only 1 EURUSD 0.01 Lot Trade Can Be Sent

Dry-run verification points:

1. **Config lock**
   - `execution.max_orders_per_run = 1`
   - symbol = `EURUSD`
   - lot floor/step = `0.01`

2. **Arming lock**
   - token `symbols=['EURUSD']`
   - token `max_orders=1`

3. **Wrapper request lock (mocked MT5 assertion)**
   - In `test_order_send_success_retcode_10009`, assert request fields:
     - `symbol == 'EURUSD'`
     - `volume == intent.risk_verdict.lot_size` (set this to `0.01` in the first-live scenario)
     - `sl` and `tp` match intent

4. **Runtime counter lock**
   - `test_blocked_when_max_orders_reached` proves second send is blocked once count is reached.

---

## 7) Confirm Ticket Linkage + Telemetry After Trade (Mocked)

Use wrapper telemetry tests:

```powershell
python -m pytest tests/execution/test_live_wrapper.py::test_telemetry_logged_on_success -q
python -m pytest tests/execution/test_live_wrapper.py::test_telemetry_logged_on_failure -q
```

Expected telemetry record includes:
- `run_id`
- `token_id`
- `decision_id`
- `symbol`
- `ticket`
- `broker_retcode`
- `status`
- full `request` payload

Ticket linkage chain to verify:
- internal `decision_id` (trade intent id),
- MT5 `ticket` from `order_send` result,
- request `comment` format: `devi:{run_id}:{decision_id}`.

---

## 8) Rollback / Disarm Steps

If anything is abnormal during dry-run or pre-live verification:

1. Stop execution attempts.
2. Disarm token (in-process service):

```powershell
python -c "from src.core.arming import ArmingService; s=ArmingService(); print('disarmed=', s.disarm('rollback'))"
```

3. Latch kill switch manually in runtime flow if needed:

```powershell
python -c "from src.core.kill_switch import KillSwitch; k=KillSwitch(); k.trigger('manual_rollback'); print('triggered=', k.is_triggered, 'reason=', k.reason)"
```

4. Reset config back to non-live defaults:
   - `runtime.mode = paper`
   - `execution.live_confirmed = false`

5. Re-run safety suite:

```powershell
python -m pytest tests/execution/test_safety.py tests/supervisor/test_live_gates.py -q
```

---

## 9) Exit Criteria for Dry-Run Completion

Dry-run is complete when:

- all command checks in Section 1 pass,
- live-no-arming rejection is proven,
- mocked order_send success/failure paths are proven,
- ticket + telemetry linkage is proven,
- max-orders and EURUSD-only constraints are proven,
- no real live trade was executed.
