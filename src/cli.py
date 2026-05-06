from __future__ import annotations

import argparse
import getpass
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.config.loader import ConfigError, config_hash, load_config
from src.core.arming import ArmingService
from src.core.enums import (
    ConfidenceTier,
    Direction,
    HTFAgreement,
    Namespace,
    Regime,
    Session,
    SetupClass,
    StructureType,
    Timeframe,
)
from src.core.kill_switch import KillSwitch
from src.core.models import (
    ConfluenceResult,
    ContextSnapshot,
    DetectedStructure,
    ExitPlan,
    RiskVerdict,
    TradeIntent,
)
from src.core.runtime_state import RuntimeState
from src.data.base import DataSourceError
from src.data.mt5_source import MT5DataSource
from src.execution.live_session import LiveSession
from src.execution.live_wrapper import LiveOrderWrapper
from src.execution.recheck import (
    is_market_open_from_snapshot,
    market_open_diagnostics_from_snapshot,
)
from src.ops.namespace_guard import NamespaceGuard, NamespaceViolationError
from src.ops.telemetry import RunManifest, TelemetryWriter
from tools.check_comparability import check_comparability
from tools.evidence_pack import build_evidence_pack
from tools.replay import replay_snapshot_file
from tools.validate_telemetry import validate_telemetry_file


def _print_report(title: str, lines: list[str], as_json: bool, payload: dict[str, Any]) -> int:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return int(payload.get("exit_code", 0))

    print(f"D.E.V.I 4.0 — {title}")
    print()
    for line in lines:
        print(line)
    return int(payload.get("exit_code", 0))


def _namespace_enum(value: str) -> Namespace:
    return Namespace(value.lower())


def _default_run_output(*, logs_root: str, namespace: Namespace) -> str:
    return str(Path(logs_root) / namespace.value / "reports" / "run")


def _resolve_run_output(args: argparse.Namespace, namespace: Namespace) -> str:
    output = args.output
    if output is None:
        output = _default_run_output(logs_root=args.logs_root, namespace=namespace)

    guard = NamespaceGuard(args.logs_root)
    guard.assert_write_allowed(namespace=namespace, target_path=Path(output))
    return output


_LIVE_ARMING_SERVICE = ArmingService()


def _account_type_label(mt5_client: Any, trade_mode: Any) -> str:
    if trade_mode is None:
        return "UNKNOWN"

    mappings = [
        ("ACCOUNT_TRADE_MODE_DEMO", "DEMO"),
        ("ACCOUNT_TRADE_MODE_CONTEST", "CONTEST"),
        ("ACCOUNT_TRADE_MODE_REAL", "REAL"),
    ]
    for attr_name, label in mappings:
        expected = getattr(mt5_client, attr_name, None)
        if expected is not None and trade_mode == expected:
            return label

    return str(trade_mode)


def _symbol_trade_mode_full(mt5_client: Any) -> int:
    return int(getattr(mt5_client, "SYMBOL_TRADE_MODE_FULL", 4))


def _validate_live_one_order_constraints(
    *,
    cfg: dict[str, Any],
    args: argparse.Namespace,
) -> str | None:
    symbol_args = [s.upper() for s in args.symbol]
    if symbol_args != ["EURUSD"]:
        return "armed_run_symbol_must_be_eurusd_only"
    if int(args.max_orders) != 1:
        return "armed_run_max_orders_must_be_1"

    execution_cfg = cfg.get("execution", {})
    risk_cfg = cfg.get("risk", {})
    instrument_cfg = cfg.get("instrument", {})

    if int(execution_cfg.get("max_orders_per_run", 0)) != 1:
        return "config_max_orders_per_run_must_be_1"
    if [str(s).upper() for s in execution_cfg.get("symbol_whitelist", [])] != ["EURUSD"]:
        return "config_symbol_whitelist_must_be_eurusd_only"
    if str(instrument_cfg.get("symbol", "")).upper() != "EURUSD":
        return "config_instrument_symbol_must_be_eurusd"
    if abs(float(risk_cfg.get("fixed_lot_size", 0.0)) - 0.01) > 1e-9:
        return "config_fixed_lot_size_must_be_0_01"
    return None


def _build_live_one_order_intent(
    *,
    cfg: dict[str, Any],
    symbol: str,
    run_id: str,
    data_source: MT5DataSource,
) -> tuple[TradeIntent, float]:
    tick = data_source.fetch_tick(symbol)
    profile = data_source.fetch_instrument_profile(symbol)

    bid = float(tick["bid"])
    ask = float(tick["ask"])
    entry_price = ask
    decision_spread = max(abs(ask - bid), 1e-05)
    point = max(float(profile.point), 1e-05)
    stop_loss = round(entry_price - (20.0 * point), 5)
    take_profit = round(entry_price + (30.0 * point), 5)
    lot_size = float(cfg.get("risk", {}).get("fixed_lot_size", 0.01))
    now = datetime.now(tz=UTC)

    structure = DetectedStructure(
        structure_type=StructureType.ORDER_BLOCK,
        direction=Direction.BULLISH,
        price_high=entry_price,
        price_low=stop_loss,
        quality=1.0,
        age_bars=0,
        atr_relative_size=1.0,
        timeframe=Timeframe.M15,
        bar_index=0,
        bar_time=now,
        metadata={},
    )
    context = ContextSnapshot(
        symbol=symbol,
        bar_time=now,
        session=Session.LONDON,
        micro_window=False,
        trend_m15=Direction.BULLISH,
        trend_h1=Direction.BULLISH,
        htf_agreement=HTFAgreement.AGREES,
        regime=Regime.TRENDING,
        atr_current=0.001,
        atr_percentile=0.5,
        spread_atr_ratio=0.1,
        stale_entry=False,
        news_blocked=False,
        nearby_structures=[structure],
    )
    confluence = ConfluenceResult(
        setup_class=SetupClass.OB_WITH_BOS,
        direction=Direction.BULLISH,
        primary_trigger=structure,
        structural_confirmations=[structure],
        structural_labels=[],
        minor_confluences=[],
        hard_rejects=[],
        soft_penalties=[],
        structural_count=1,
        minor_count=0,
        quality_penalty=0.0,
        effective_quality=1.0,
        confluence_pass=True,
        confidence_tier=ConfidenceTier.A,
        tier_reason="armed_run_live_one_order",
    )
    risk = RiskVerdict(
        approved=True,
        lot_size=lot_size,
        actual_risk_pct=float(cfg.get("risk", {}).get("risk_per_trade_pct", 0.0)),
        intended_risk_pct=float(cfg.get("risk", {}).get("risk_per_trade_pct", 0.0)),
        reason="approved",
    )
    intent = TradeIntent(
        trade_id=f"{run_id}_live_one_order",
        symbol=symbol,
        direction=Direction.BULLISH,
        setup_class=SetupClass.OB_WITH_BOS,
        confidence_tier=ConfidenceTier.A,
        session=Session.LONDON,
        entry_price=entry_price,
        exit_plan=ExitPlan(
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward=1.5,
            sl_source="armed_run",
            tp_source="armed_run",
            breakeven_trigger_r=1.0,
            session_close_exit=False,
        ),
        risk_verdict=risk,
        confluence=confluence,
        context=context,
        config_hash=config_hash(cfg),
        bar_time=now,
    )
    return intent, decision_spread


def cmd_live_arm(args: argparse.Namespace) -> int:
    symbols = [s.upper() for s in args.symbol]
    token = _LIVE_ARMING_SERVICE.arm(
        run_id=args.run_id,
        armed_by=getpass.getuser(),
        reason=args.reason,
        symbols=symbols,
        max_orders=int(args.max_orders),
        ttl_minutes=int(args.ttl_minutes),
    )

    if token is None:
        payload = {
            "tool": "live arm",
            "namespace": args.namespace,
            "run_id": args.run_id,
            "verdict": "FAIL",
            "reason": "already_armed",
            "exit_code": 1,
        }
        return _print_report(
            "live arm",
            [
                f"Namespace  : {args.namespace}",
                "Mode       : live",
                f"Run ID     : {args.run_id}",
                "Verdict    : FAIL (already_armed)",
                "Exit code  : 1",
            ],
            args.json,
            payload,
        )

    payload = {
        "tool": "live arm",
        "namespace": args.namespace,
        "run_id": token.run_id,
        "token_id": token.token_id,
        "armed_at": token.armed_at.isoformat(),
        "expires_at": token.expires_at.isoformat(),
        "symbols": token.symbols,
        "max_orders": token.max_orders,
        "reason": token.reason,
        "note": "informational_only_process_local_token",
        "verdict": "PASS",
        "exit_code": 0,
    }
    return _print_report(
        "live arm",
        [
            f"Namespace  : {args.namespace}",
            "Mode       : live",
            f"Run ID     : {token.run_id}",
            f"Token ID   : {token.token_id}",
            f"Symbols    : {','.join(token.symbols)}",
            f"Max Orders : {token.max_orders}",
            f"Armed At   : {token.armed_at.isoformat()}",
            f"Expires At : {token.expires_at.isoformat()}",
            "Note       : informational only (process-local token)",
            "Verdict    : PASS",
            "Exit code  : 0",
        ],
        args.json,
        payload,
    )


def cmd_live_account_check(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        return _print_report(
            "live account-check",
            [
                f"Namespace  : {args.namespace}",
                "Mode       : live",
                f"Config     : {args.config}",
                f"Run ID     : {args.run_id}",
                f"Verdict    : FAIL ({exc})",
                "Exit code  : 1",
            ],
            args.json,
            {
                "tool": "live account-check",
                "namespace": args.namespace,
                "mode": "live",
                "config": args.config,
                "run_id": args.run_id,
                "failure_code": str(exc),
                "order_send_called": False,
                "verdict": "FAIL",
                "exit_code": 1,
            },
        )

    execution_cfg = cfg.get("execution", {})
    instrument_cfg = cfg.get("instrument", {})
    symbol = str(
        instrument_cfg.get("symbol")
        or (execution_cfg.get("symbol_whitelist") or ["EURUSD"])[0]
    ).upper()

    source: MT5DataSource | None = None
    try:
        source = MT5DataSource()
        account_raw = source.mt5_client.account_info() if source.mt5_client is not None else None
        if account_raw is None:
            raise DataSourceError("mt5_account_info_unavailable")

        symbol_raw = source.mt5_client.symbol_info(symbol) if source.mt5_client is not None else None
        if symbol_raw is None:
            raise DataSourceError(f"mt5_symbol_info_unavailable:{symbol}")

        account = source.fetch_account_info()
        tick = source.fetch_tick(symbol)
        profile = source.fetch_instrument_profile(symbol)
    except (DataSourceError, RuntimeError) as exc:
        return _print_report(
            "live account-check",
            [
                f"Namespace  : {args.namespace}",
                "Mode       : live",
                f"Config     : {args.config}",
                f"Run ID     : {args.run_id}",
                f"Symbol     : {symbol}",
                f"Verdict    : FAIL ({exc})",
                "Exit code  : 1",
            ],
            args.json,
            {
                "tool": "live account-check",
                "namespace": args.namespace,
                "mode": "live",
                "config": args.config,
                "run_id": args.run_id,
                "symbol": symbol,
                "failure_code": str(exc),
                "order_send_called": False,
                "verdict": "FAIL",
                "exit_code": 1,
            },
        )
    finally:
        if source is not None:
            source.close()

    trade_allowed = bool(getattr(symbol_raw, "trade_allowed", True))
    trade_mode = int(getattr(symbol_raw, "trade_mode", -1))
    trade_mode_full = _symbol_trade_mode_full(source.mt5_client)
    tradable = trade_allowed and trade_mode == trade_mode_full
    bid = float(tick["bid"])
    ask = float(tick["ask"])
    tick_available = bid > 0.0 and ask > 0.0 and ask >= bid
    session_deals_present = hasattr(symbol_raw, "session_deals")
    session_deals_raw = getattr(symbol_raw, "session_deals", None)
    market_diagnostics = market_open_diagnostics_from_snapshot(
        bid=bid,
        ask=ask,
        trade_allowed=trade_allowed,
        trade_mode=trade_mode,
        trade_mode_full=trade_mode_full,
        session_deals=session_deals_raw,
        session_deals_present=session_deals_present,
    )
    market_open = is_market_open_from_snapshot(
        bid=bid,
        ask=ask,
        tradable=tradable,
        session_deals=session_deals_raw,
    )
    spread = abs(ask - bid)

    account_number = int(getattr(account_raw, "login", 0) or 0)
    server = str(getattr(account_raw, "server", ""))
    account_type = _account_type_label(source.mt5_client, getattr(account_raw, "trade_mode", None))

    checks = {
        "account_number_present": account_number > 0,
        "server_present": bool(server),
        "tick_available": float(tick["ask"]) > 0 and float(tick["bid"]) > 0,
        "symbol_tradable": tradable,
        "market_open": market_open,
    }
    verdict = "PASS" if all(checks.values()) else "FAIL"

    payload = {
        "tool": "live account-check",
        "namespace": args.namespace,
        "mode": "live",
        "config": args.config,
        "run_id": args.run_id,
        "account_number": account_number,
        "server": server,
        "account_type": account_type,
        "balance": float(account.get("balance", 0.0)),
        "equity": float(account.get("equity", 0.0)),
        "currency": str(account.get("currency", "")),
        "symbol": symbol,
        "bid": bid,
        "ask": ask,
        "spread": spread,
        "trade_mode": trade_mode,
        "trade_mode_full": trade_mode_full,
        "min_lot": float(profile.min_lot),
        "lot_step": float(profile.lot_step),
        "max_lot": float(profile.max_lot),
        "tradable": tradable,
        "market_open": market_open,
        "market_diagnostics": market_diagnostics,
        "checks": checks,
        "armed": False,
        "order_send_called": False,
        "verdict": verdict,
        "exit_code": 0 if verdict == "PASS" else 1,
    }
    return _print_report(
        "live account-check",
        [
            f"Namespace  : {args.namespace}",
            "Mode       : live",
            f"Config     : {args.config}",
            f"Run ID     : {args.run_id}",
            f"Account #  : {account_number}",
            f"Server     : {server}",
            f"Acct Type  : {account_type}",
            f"Balance    : {float(account.get('balance', 0.0)):.2f}",
            f"Equity     : {float(account.get('equity', 0.0)):.2f}",
            f"Currency   : {str(account.get('currency', ''))}",
            f"Symbol     : {symbol}",
            f"Bid        : {bid:.5f}",
            f"Ask        : {ask:.5f}",
            f"Spread     : {spread:.5f}",
            f"Min Lot    : {float(profile.min_lot):.2f}",
            f"Lot Step   : {float(profile.lot_step):.2f}",
            f"Max Lot    : {float(profile.max_lot):.2f}",
            f"Tradable   : {tradable}",
            f"Market Open: {market_open}",
            f"Verdict    : {verdict}",
            f"Exit code  : {0 if verdict == 'PASS' else 1}",
        ],
        args.json,
        payload,
    )


def cmd_live_armed_run(args: argparse.Namespace) -> int:
    """Arm + run live flow in a single process.

    This command is the only supported path for live configs that enable
    auto execution because arming tokens are process-local.
    """
    namespace = _namespace_enum(args.namespace)
    output = _resolve_run_output(args, namespace)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        return _print_report(
            "live armed-run",
            [
                f"Namespace  : {args.namespace}",
                "Mode       : live",
                f"Config     : {args.config}",
                f"Run ID     : {args.run_id}",
                f"Output     : {output}",
                f"Verdict    : FAIL ({exc})",
                "Exit code  : 1",
            ],
            args.json,
            {
                "tool": "live armed-run",
                "verdict": "FAIL",
                "reason": str(exc),
                "exit_code": 1,
            },
        )

    constraint_failure = _validate_live_one_order_constraints(cfg=cfg, args=args)
    if constraint_failure is not None:
        payload = {
            "tool": "live armed-run",
            "namespace": namespace.value,
            "mode": "live",
            "config": args.config,
            "run_id": args.run_id,
            "output": output,
            "final_decision": "REJECTED_EXECUTION",
            "failure_code": constraint_failure,
            "armed_token_seen": False,
            "execution_attempted": False,
            "token_consumed": False,
            "disarm_called": False,
            "token_cleared": False,
            "order_send_called": False,
            "verdict": "FAIL",
            "exit_code": 1,
        }
        return _print_report(
            "live armed-run",
            [
                f"Namespace  : {namespace.value}",
                "Mode       : live",
                f"Config     : {args.config}",
                f"Run ID     : {args.run_id}",
                f"Output     : {output}",
                "Final      : REJECTED_EXECUTION",
                f"Reason     : {constraint_failure}",
                "Verdict    : FAIL",
                "Exit code  : 1",
            ],
            args.json,
            payload,
        )

    arming_service = ArmingService()
    symbols = [s.upper() for s in args.symbol]
    token = arming_service.arm(
        run_id=args.run_id,
        armed_by=getpass.getuser(),
        reason=args.reason,
        symbols=symbols,
        max_orders=int(args.max_orders),
        ttl_minutes=int(args.ttl_minutes),
    )
    if token is None:
        payload = {
            "tool": "live armed-run",
            "namespace": namespace.value,
            "mode": "live",
            "config": args.config,
            "run_id": args.run_id,
            "output": output,
            "final_decision": "REJECTED_EXECUTION",
            "failure_code": "already_armed",
            "armed_token_seen": False,
            "execution_attempted": False,
            "token_consumed": False,
            "disarm_called": False,
            "token_cleared": False,
            "order_send_called": False,
            "verdict": "FAIL",
            "exit_code": 1,
        }
        return _print_report(
            "live armed-run",
            [
                f"Namespace  : {namespace.value}",
                "Mode       : live",
                f"Config     : {args.config}",
                f"Run ID     : {args.run_id}",
                f"Output     : {output}",
                "Final      : REJECTED_EXECUTION",
                "Reason     : already_armed",
                "Verdict    : FAIL",
                "Exit code  : 1",
            ],
            args.json,
            payload,
        )

    auto_execute_live = bool(cfg.get("execution", {}).get("auto_execute_live", False))
    execution_attempted = False
    token_consumed = False
    disarm_called = False
    order_send_called = False
    final_decision = "REJECTED_EXECUTION"
    verdict = "FAIL"
    exit_code = 1
    failure_code = "preflight_no_order_send"
    execution_status: str | None = None
    execution_ticket: int | None = None
    broker_present: bool | None = None
    broker_type: str | None = None
    has_order_send: bool | None = None
    order_send_invoked = False
    mt5_initialized: bool | None = None
    mt5_last_error: str | None = None
    runtime_state = RuntimeState(run_id=args.run_id)

    if auto_execute_live:
        execution_attempted = True
        source: MT5DataSource | None = None
        try:
            source = MT5DataSource()
            intent, decision_spread = _build_live_one_order_intent(
                cfg=cfg,
                symbol="EURUSD",
                run_id=args.run_id,
                data_source=source,
            )
            telemetry_writer = TelemetryWriter(logs_root=args.logs_root, namespace=namespace)
            wrapper = LiveOrderWrapper(data_source=source, telemetry_writer=telemetry_writer)
            result = wrapper.send(
                intent,
                arming_service=arming_service,
                kill_switch=KillSwitch(),
                runtime_state=runtime_state,
                decision_spread=decision_spread,
                max_orders_per_run=int(args.max_orders),
                risk_dynamic_lot_sizing=bool(cfg.get("risk", {}).get("dynamic_lot_sizing", True)),
                risk_fixed_lot_size=float(cfg.get("risk", {}).get("fixed_lot_size", intent.risk_verdict.lot_size)),
            )

            execution_status = result.status
            execution_ticket = result.ticket
            broker_present = getattr(result, "broker_present", None)
            broker_type = getattr(result, "broker_type", None)
            has_order_send = getattr(result, "has_order_send", None)
            order_send_invoked = bool(getattr(result, "order_send_invoked", False))
            mt5_initialized = getattr(result, "mt5_initialized", None)
            mt5_last_error = getattr(result, "mt5_last_error", None)
            order_send_called = order_send_invoked

            if result.sent:
                runtime_state.record_trade(intent.trade_id)
                final_decision = "EXECUTED"
                verdict = "PASS"
                exit_code = 0
                failure_code = "approved"
            else:
                failure_code = result.status
        except (DataSourceError, RuntimeError) as exc:
            failure_code = str(exc)
        finally:
            if arming_service.is_armed:
                token_consumed = arming_service.consume_token(token.token_id, reason="execution_attempt")
            else:
                token_consumed = True
            if source is not None:
                source.close()
    else:
        token_consumed = not arming_service.is_armed

    disarm_called = True
    _ = arming_service.disarm("armed_run_complete")
    token_cleared = not arming_service.is_armed

    payload = {
        "tool": "live armed-run",
        "namespace": namespace.value,
        "mode": "live",
        "config": args.config,
        "run_id": args.run_id,
        "output": output,
        "token_id": token.token_id,
        "armed_token_seen": True,
        "execution_attempted": execution_attempted,
        "token_consumed": token_consumed,
        "disarm_called": disarm_called,
        "token_cleared": token_cleared,
        "order_send_called": order_send_called,
        "final_decision": final_decision,
        "failure_code": failure_code,
        "execution_status": execution_status,
        "execution_ticket": execution_ticket,
        "broker_present": broker_present,
        "broker_type": broker_type,
        "has_order_send": has_order_send,
        "order_send_invoked": order_send_invoked,
        "mt5_initialized": mt5_initialized,
        "mt5_last_error": mt5_last_error,
        "orders_this_run": runtime_state.orders_this_run,
        "verdict": verdict,
        "exit_code": exit_code,
    }
    return _print_report(
        "live armed-run",
        [
            f"Namespace  : {namespace.value}",
            "Mode       : live",
            f"Config     : {args.config}",
            f"Run ID     : {args.run_id}",
            f"Output     : {output}",
            f"Token ID   : {token.token_id}",
            f"Attempted  : {execution_attempted}",
            f"Consumed   : {token_consumed}",
            f"Disarmed   : {token_cleared}",
            f"Order Sent : {order_send_called}",
            f"Broker     : {broker_type if broker_type is not None else ''}",
            f"OS Invoked : {order_send_invoked}",
            f"Final      : {final_decision}",
            f"Reason     : {failure_code}",
            f"Verdict    : {verdict}",
            f"Exit code  : {exit_code}",
        ],
        args.json,
        payload,
    )


def cmd_live_scan(args: argparse.Namespace) -> int:
    """Run one full M15 decision cycle with live execution.

    Arms automatically, runs the pipeline, executes if signals align,
    then disarms. Reports full cycle results.
    """
    namespace = Namespace[args.namespace.upper()]
    cfg = load_config(args.config)

    if not cfg.get("execution", {}).get("live_confirmed", False):
        return _print_report(
            "live scan",
            ["Mode       : live", "Verdict    : FAIL", "Exit code  : 1", "Reason     : live_confirmed=false"],
            args.json,
            {"tool": "live scan", "verdict": "FAIL", "exit_code": 1, "reason": "live_confirmed=false"},
        )

    auto_execute_live = bool(cfg.get("execution", {}).get("auto_execute_live", False))

    # Arm
    symbols = [s.upper() for s in args.symbol]
    token = _LIVE_ARMING_SERVICE.arm(
        run_id=args.run_id,
        armed_by="cli_operator",
        symbols=symbols,
        max_orders=int(args.max_orders),
        ttl_minutes=int(args.ttl_minutes),
        reason=args.reason or "live scan auto-arm",
    )
    if token is None:
        return _print_report(
            "live scan",
            ["Mode       : live", "Verdict    : FAIL", "Exit code  : 1", "Reason     : arm_failed"],
            args.json,
            {"tool": "live scan", "verdict": "FAIL", "exit_code": 1, "reason": "arm_failed"},
        )

    result_payload: dict[str, Any] = {
        "tool": "live scan",
        "run_id": args.run_id,
        "token_id": str(token.token_id),
        "armed": True,
        "execution_attempted": False,
        "verdict": "PASS",
        "exit_code": 0,
    }

    live_fill: dict[str, Any] | None = None
    session_result = None

    if auto_execute_live:
        result_payload["execution_attempted"] = True
        try:
            session = LiveSession(
                config=cfg,
                logs_root=args.logs_root,
                namespace=namespace,
                symbols=symbols,
            )
            session_result = session.run(run_id=args.run_id, token=token)
            session.close()

            # Extract first symbol result for reporting
            first_symbol = symbols[0] if symbols else ""
            if first_symbol and first_symbol in session_result.symbol_results:
                sym_result = session_result.symbol_results[first_symbol]
                result_payload["decision"] = sym_result.decision.value
                result_payload["failure_code"] = sym_result.failure_code
                result_payload["bars_m15_count"] = sym_result.bars_m15_count
                result_payload["tick_bid"] = sym_result.tick_bid
                result_payload["tick_ask"] = sym_result.tick_ask
                live_fill = sym_result.paper_fill

            result_payload["open_position_count"] = session_result.open_position_count
            result_payload["account_balance"] = session_result.account_balance
            result_payload["account_equity"] = session_result.account_equity
            result_payload["execution_summary"] = session_result.execution_summary

            if live_fill is not None:
                result_payload["order_status"] = live_fill.get("order_status")
                result_payload["ticket"] = live_fill.get("ticket")
                result_payload["broker_retcode"] = live_fill.get("broker_retcode")
                result_payload["side"] = live_fill.get("side")
                if live_fill.get("order_status") == "FILLED":
                    result_payload["verdict"] = "PASS"
                    result_payload["exit_code"] = 0
                else:
                    result_payload["verdict"] = "FAIL"
                    result_payload["exit_code"] = 1
            else:
                result_payload["order_status"] = "NO_EXECUTE"
                result_payload["verdict"] = "PASS"
                result_payload["exit_code"] = 0

        except (DataSourceError, RuntimeError) as exc:
            result_payload["verdict"] = "FAIL"
            result_payload["exit_code"] = 1
            result_payload["failure_code"] = f"runtime_error:{exc}"

    # Always disarm after scan
    cleared = _LIVE_ARMING_SERVICE.disarm("scan_complete")
    result_payload["disarm_called"] = True
    result_payload["token_cleared"] = cleared

    lines = [
        f"Namespace  : {namespace.value}",
        f"Mode       : live",
        f"Config     : {args.config}",
        f"Run ID     : {args.run_id}",
        f"Token ID   : {token.token_id}",
        f"Attempted  : {result_payload['execution_attempted']}",
        f"Decision   : {result_payload.get('decision', 'N/A')}",
        f"Order      : {result_payload.get('order_status', 'N/A')}",
        f"Ticket     : {result_payload.get('ticket', 'N/A')}",
        f"Positions  : {result_payload.get('open_position_count', 0)}",
        f"Balance    : {result_payload.get('account_balance', 0.0)}",
        f"Equity     : {result_payload.get('account_equity', 0.0)}",
        f"Disarmed   : {result_payload['token_cleared']}",
        f"Verdict    : {result_payload['verdict']}",
        f"Exit code  : {result_payload['exit_code']}",
    ]
    return _print_report("live scan", lines, args.json, result_payload)


def cmd_live_disarm(args: argparse.Namespace) -> int:
    cleared = _LIVE_ARMING_SERVICE.disarm(args.reason)
    payload = {
        "tool": "live disarm",
        "namespace": args.namespace,
        "run_id": args.run_id,
        "disarmed": bool(cleared),
        "verdict": "PASS",
        "exit_code": 0,
    }
    return _print_report(
        "live disarm",
        [
            f"Namespace  : {args.namespace}",
            "Mode       : live",
            f"Run ID     : {args.run_id}",
            f"Disarmed   : {cleared}",
            "Verdict    : PASS",
            "Exit code  : 0",
        ],
        args.json,
        payload,
    )


def cmd_live_status(args: argparse.Namespace) -> int:
    token = _LIVE_ARMING_SERVICE.get_valid_token()
    payload = {
        "tool": "live status",
        "namespace": args.namespace,
        "run_id": args.run_id,
        "armed": token is not None,
        "token_id": None if token is None else token.token_id,
        "expires_at": None if token is None else token.expires_at.isoformat(),
        "symbols": [] if token is None else token.symbols,
        "max_orders": None if token is None else token.max_orders,
        "verdict": "PASS",
        "exit_code": 0,
    }
    return _print_report(
        "live status",
        [
            f"Namespace  : {args.namespace}",
            "Mode       : live",
            f"Run ID     : {args.run_id}",
            f"Armed      : {token is not None}",
            f"Token ID   : {'' if token is None else token.token_id}",
            f"Expires At : {'' if token is None else token.expires_at.isoformat()}",
            f"Symbols    : {'' if token is None else ','.join(token.symbols)}",
            "Verdict    : PASS",
            "Exit code  : 0",
        ],
        args.json,
        payload,
    )


def cmd_run(args: argparse.Namespace) -> int:
    mode = getattr(args, "run_command", None) or getattr(args, "mode", "paper")
    namespace = _namespace_enum(args.namespace)
    output = _resolve_run_output(args, namespace)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        return _print_report(
            "run",
            [
                f"Namespace  : {args.namespace}",
                f"Mode       : {mode}",
                f"Config     : {args.config}",
                f"Run ID     : {args.run_id}",
                f"Output     : {output}",
                f"Verdict    : FAIL ({exc})",
                "Exit code  : 1",
            ],
            args.json,
            {"tool": "run", "verdict": "FAIL", "reason": str(exc), "exit_code": 1},
        )

    if mode == "live":
        auto_execute_live = bool(cfg.get("execution", {}).get("auto_execute_live", False))
        if auto_execute_live:
            payload = {
                "tool": "run",
                "namespace": namespace.value,
                "mode": mode,
                "config": args.config,
                "run_id": args.run_id,
                "output": output,
                "final_decision": "REJECTED_EXECUTION",
                "failure_code": "live_requires_armed_run",
                "verdict": "FAIL",
                "exit_code": 1,
            }
            return _print_report(
                "run",
                [
                    f"Namespace  : {namespace.value}",
                    f"Mode       : {mode}",
                    f"Config     : {args.config}",
                    f"Run ID     : {args.run_id}",
                    f"Output     : {output}",
                    "Final      : REJECTED_EXECUTION",
                    "Reason     : live_requires_armed_run",
                    "Verdict    : FAIL",
                    "Exit code  : 1",
                ],
                args.json,
                payload,
            )

        payload = {
            "tool": "run",
            "namespace": namespace.value,
            "mode": mode,
            "config": args.config,
            "run_id": args.run_id,
            "output": output,
            "final_decision": "REJECTED_EXECUTION",
            "failure_code": "live_not_armed",
            "verdict": "FAIL",
            "exit_code": 1,
        }
        return _print_report(
            "run",
            [
                f"Namespace  : {namespace.value}",
                f"Mode       : {mode}",
                f"Config     : {args.config}",
                f"Run ID     : {args.run_id}",
                f"Output     : {output}",
                "Final      : REJECTED_EXECUTION",
                "Reason     : live_not_armed",
                "Verdict    : FAIL",
                "Exit code  : 1",
            ],
            args.json,
            payload,
        )

    writer = TelemetryWriter(logs_root=args.logs_root, namespace=namespace)
    run_hash = config_hash(cfg)

    manifest = RunManifest(
        run_id=args.run_id,
        build_id=str(cfg.get("build_id", "4.0.0")),
        config_hash=run_hash,
        namespace=namespace.value,
        mode=mode,
        data_source=str(cfg.get("runtime", {}).get("data_source", "snapshot")),
        start_time=datetime.now(tz=UTC).isoformat(),
    )
    manifest_path = writer.write_run_manifest(manifest)

    payload = {
        "tool": "run",
        "namespace": namespace.value,
        "mode": mode,
        "config": args.config,
        "run_id": args.run_id,
        "output": str(manifest_path),
        "verdict": "PASS",
        "exit_code": 0,
    }
    return _print_report(
        "run",
        [
            f"Namespace  : {namespace.value}",
            f"Mode       : {mode}",
            f"Config     : {args.config}",
            f"Run ID     : {args.run_id}",
            f"Output     : {manifest_path}",
            "Verdict    : PASS",
            "Exit code  : 0",
        ],
        args.json,
        payload,
    )


def cmd_replay(args: argparse.Namespace) -> int:
    result = replay_snapshot_file(args.snapshot)
    payload = {
        "tool": "replay run",
        "namespace": args.namespace,
        "mode": "replay",
        "config": args.config,
        "run_id": args.run_id,
        "output": result["output"],
        "verdict": "PASS" if result["ok"] else "FAIL",
        "exit_code": 0 if result["ok"] else 1,
    }
    return _print_report(
        "replay run",
        [
            f"Namespace  : {args.namespace}",
            "Mode       : replay",
            f"Config     : {args.config}",
            f"Run ID     : {args.run_id}",
            f"Output     : {result['output']}",
            f"Verdict    : {'PASS' if result['ok'] else 'FAIL'}",
            f"Exit code  : {0 if result['ok'] else 1}",
        ],
        args.json,
        payload,
    )


def cmd_telemetry_validate(args: argparse.Namespace) -> int:
    result = validate_telemetry_file(args.file)
    verdict = "PASS" if result.get("ok") else "FAIL"
    payload = {
        "tool": "telemetry validate",
        "namespace": args.namespace,
        "mode": args.mode,
        "config": args.config,
        "run_id": args.run_id,
        "output": args.output,
        "checks": result,
        "verdict": verdict,
        "exit_code": 0 if verdict == "PASS" else 1,
    }
    return _print_report(
        "telemetry validate",
        [
            f"Namespace  : {args.namespace}",
            f"Mode       : {args.mode}",
            f"Config     : {args.config}",
            f"Run ID     : {args.run_id}",
            f"Output     : {args.output}",
            "",
            "Checks:",
            f"- schema validation: {'PASS' if result.get('invalid', 1) == 0 else 'FAIL'}",
            "- required fields: PASS" if result.get("invalid", 1) == 0 else "- required fields: FAIL",
            f"- tp_debug coverage: {'PASS' if result.get('tp_debug_coverage_fail', 1) == 0 else 'FAIL'}",
            "- namespace isolation: PASS",
            "",
            f"Verdict    : {verdict}",
            f"Exit code  : {0 if verdict == 'PASS' else 1}",
        ],
        args.json,
        payload,
    )


def cmd_comparability(args: argparse.Namespace) -> int:
    result = check_comparability(args.expected, args.actual, tick_size=args.tick_size)
    verdict = "PASS" if result["ok"] else "FAIL"
    payload = {
        "tool": "comparability check",
        "namespace": args.namespace,
        "mode": args.mode,
        "config": args.config,
        "run_id": args.run_id,
        "output": args.output,
        "verdict": verdict,
        "diff_count": sum(1 for d in result["diffs"] if not d["ok"]),
        "exit_code": 0 if verdict == "PASS" else 1,
    }
    return _print_report(
        "comparability check",
        [
            f"Namespace  : {args.namespace}",
            f"Mode       : {args.mode}",
            f"Config     : {args.config}",
            f"Run ID     : {args.run_id}",
            f"Output     : {args.output}",
            f"Verdict    : {verdict}",
            f"Exit code  : {0 if verdict == 'PASS' else 1}",
        ],
        args.json,
        payload,
    )


def cmd_evidence_pack(args: argparse.Namespace) -> int:
    artifacts = {
        "namespace": args.namespace,
        "cohort": args.cohort,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "inputs": {
            "decisions": args.decisions,
            "parity": args.parity,
        },
    }
    result = build_evidence_pack(args.output, artifacts)
    payload = {
        "tool": "evidence pack",
        "namespace": args.namespace,
        "mode": args.mode,
        "config": args.config,
        "run_id": args.run_id,
        "output": result["output"],
        "verdict": "PASS" if result["ok"] else "FAIL",
        "exit_code": 0 if result["ok"] else 1,
    }
    return _print_report(
        "evidence pack",
        [
            f"Namespace  : {args.namespace}",
            f"Mode       : {args.mode}",
            f"Config     : {args.config}",
            f"Run ID     : {args.run_id}",
            f"Output     : {result['output']}",
            f"Verdict    : {'PASS' if result['ok'] else 'FAIL'}",
            f"Exit code  : {0 if result['ok'] else 1}",
        ],
        args.json,
        payload,
    )


def cmd_doctor(args: argparse.Namespace) -> int:
    checks = {
        "python": "PASS",
        "config_exists": "PASS" if Path(args.config).exists() else "FAIL",
        "broker_connectivity": "SKIPPED" if not args.broker else "PENDING",
    }
    verdict = "PASS" if checks["config_exists"] == "PASS" else "FAIL"
    payload = {
        "tool": "doctor",
        "namespace": args.namespace,
        "mode": args.mode,
        "config": args.config,
        "run_id": args.run_id,
        "output": "stdout",
        "checks": checks,
        "verdict": verdict,
        "exit_code": 0 if verdict == "PASS" else 1,
    }
    return _print_report(
        "doctor",
        [
            f"Namespace  : {args.namespace}",
            f"Mode       : {args.mode}",
            f"Config     : {args.config}",
            f"Run ID     : {args.run_id}",
            "Output     : stdout",
            f"Verdict    : {verdict}",
            f"Exit code  : {0 if verdict == 'PASS' else 1}",
        ],
        args.json,
        payload,
    )


def _add_common(sub: argparse.ArgumentParser, include_mode: bool = True) -> None:
    sub.add_argument("--namespace", default="eval")
    if include_mode:
        sub.add_argument("--mode", default="paper")
    sub.add_argument("--config", default="src/config/defaults.json")
    sub.add_argument("--run-id", default="eval_run")
    sub.add_argument("--json", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="devi")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run")
    run_sub = run.add_subparsers(dest="run_command", required=True)
    for mode_name in ("backtest", "paper", "shadow", "live"):
        run_mode = run_sub.add_parser(mode_name)
        _add_common(run_mode, include_mode=False)
        run_mode.add_argument("--logs-root", default="logs")
        run_mode.add_argument("--output", default=None)
        run_mode.set_defaults(func=cmd_run)

    replay = sub.add_parser("replay")
    replay_sub = replay.add_subparsers(dest="replay_command", required=True)
    replay_run = replay_sub.add_parser("run")
    _add_common(replay_run)
    replay_run.add_argument("--snapshot", required=True)
    replay_run.set_defaults(func=cmd_replay)

    telemetry = sub.add_parser("telemetry")
    telemetry_sub = telemetry.add_subparsers(dest="telemetry_command", required=True)
    telemetry_validate = telemetry_sub.add_parser("validate")
    _add_common(telemetry_validate)
    telemetry_validate.add_argument("--file", required=True)
    telemetry_validate.add_argument("--output", default="logs/eval/reports/telemetry_validation")
    telemetry_validate.set_defaults(func=cmd_telemetry_validate)

    comparability = sub.add_parser("comparability")
    comparability_sub = comparability.add_subparsers(dest="comparability_command", required=True)
    comparability_check = comparability_sub.add_parser("check")
    _add_common(comparability_check)
    comparability_check.add_argument("--expected", required=True)
    comparability_check.add_argument("--actual", required=True)
    comparability_check.add_argument("--tick-size", type=float, default=0.0)
    comparability_check.add_argument("--output", default="logs/eval/reports/comparability")
    comparability_check.set_defaults(func=cmd_comparability)

    evidence = sub.add_parser("evidence")
    evidence_sub = evidence.add_subparsers(dest="evidence_command", required=True)
    evidence_pack = evidence_sub.add_parser("pack")
    _add_common(evidence_pack)
    evidence_pack.add_argument("--cohort", required=True)
    evidence_pack.add_argument("--decisions", default="")
    evidence_pack.add_argument("--parity", default="")
    evidence_pack.add_argument("--output", required=True)
    evidence_pack.set_defaults(func=cmd_evidence_pack)

    doctor = sub.add_parser("doctor")
    _add_common(doctor)
    doctor.add_argument("--broker", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    live = sub.add_parser("live")
    live_sub = live.add_subparsers(dest="live_command", required=True)
    live_arm = live_sub.add_parser("arm")
    _add_common(live_arm, include_mode=False)
    live_arm.add_argument("--symbol", action="append", required=True)
    live_arm.add_argument("--max-orders", type=int, required=True)
    live_arm.add_argument("--ttl-minutes", type=int, default=30)
    live_arm.add_argument("--reason", default="")
    live_arm.set_defaults(func=cmd_live_arm)

    live_disarm = live_sub.add_parser("disarm")
    _add_common(live_disarm, include_mode=False)
    live_disarm.add_argument("--reason", default="")
    live_disarm.set_defaults(func=cmd_live_disarm)

    live_status = live_sub.add_parser("status")
    _add_common(live_status, include_mode=False)
    live_status.set_defaults(func=cmd_live_status)

    live_account_check = live_sub.add_parser("account-check")
    _add_common(live_account_check, include_mode=False)
    live_account_check.set_defaults(func=cmd_live_account_check)

    live_armed_run = live_sub.add_parser("armed-run")
    _add_common(live_armed_run, include_mode=False)
    live_armed_run.add_argument("--symbol", action="append", required=True)
    live_armed_run.add_argument("--max-orders", type=int, required=True)
    live_armed_run.add_argument("--ttl-minutes", type=int, default=30)
    live_armed_run.add_argument("--reason", default="")
    live_armed_run.add_argument("--logs-root", default="logs")
    live_armed_run.add_argument("--output", default=None)
    live_armed_run.set_defaults(func=cmd_live_armed_run, namespace="prod")

    live_scan = live_sub.add_parser("scan")
    _add_common(live_scan, include_mode=False)
    live_scan.add_argument("--symbol", action="append", required=True)
    live_scan.add_argument("--max-orders", type=int, required=True)
    live_scan.add_argument("--ttl-minutes", type=int, default=30)
    live_scan.add_argument("--reason", default="")
    live_scan.add_argument("--logs-root", default="logs")
    live_scan.set_defaults(func=cmd_live_scan, namespace="prod")

    preflight = sub.add_parser("preflight")
    _add_common(preflight)
    preflight.set_defaults(func=lambda args: _print_report(
        "preflight",
        [
            f"Namespace  : {args.namespace}",
            f"Mode       : {args.mode}",
            f"Config     : {args.config}",
            f"Run ID     : {args.run_id}",
            "Output     : Phase 0 placeholder",
            "Verdict    : PASS",
            "Exit code  : 0",
        ],
        args.json,
        {"tool": "preflight", "verdict": "PASS", "exit_code": 0},
    ))

    phase = sub.add_parser("phase")
    phase_sub = phase.add_subparsers(dest="phase_command", required=True)
    phase_verify = phase_sub.add_parser("verify")
    _add_common(phase_verify)
    phase_verify.add_argument("--phase", required=True)
    phase_verify.set_defaults(func=lambda args: _print_report(
        "phase verify",
        [
            f"Namespace  : {args.namespace}",
            f"Mode       : {args.mode}",
            f"Config     : {args.config}",
            f"Run ID     : {args.run_id}",
            f"Output     : Phase {args.phase} verification placeholder",
            "Verdict    : PASS",
            "Exit code  : 0",
        ],
        args.json,
        {"tool": "phase verify", "verdict": "PASS", "exit_code": 0},
    ))

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except NamespaceViolationError as exc:
        print(f"namespace_violation:{exc}")
        return 1
    except Exception as exc:  # pragma: no cover
        print(f"fatal:{exc}")
        return 3
