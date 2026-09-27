from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

from build_guard import BuildIntegrityError, verify_build_integrity
from config_guard import ConfigError, fingerprint_config, validate_config
from redaction import sanitize
from store import DATA, StateStoreError, read_json, read_runtime, validate_runtime
from supervisor_relay import relay_health

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
DASHBOARD_DATA = DOCS / "data" / "dashboard.json"
DASHBOARD_HASH = DOCS / "data" / "dashboard.json.sha256"
CONFIG_PATH = HERE / "config.json"
APPROVED_CONFIG_PATH = HERE / "approved_config.sha256"

MAX_ACTIVITY = 40
MAX_CHART_BARS = 80
MAX_EVOLUTION_PROPOSALS = 8
MAX_ESCALATIONS = 20


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json(name: str) -> dict:
    """Read a non-authoritative observability mirror with integrity checking when present."""
    path = DATA / name
    if not path.exists():
        return {}
    try:
        value = read_json(name, {}, strict=True)
        return value if isinstance(value, dict) else {}
    except StateStoreError:
        return {"_read_error": True, "_read_error_type": "INTEGRITY_OR_PARSE_FAILURE"}


def _approved_config() -> tuple[dict | None, dict]:
    status = {"verified": False, "fingerprint": None, "error": None}
    try:
        raw = json.loads(CONFIG_PATH.read_text())
        cfg = validate_config(raw)
        current = fingerprint_config(cfg)
        approved = APPROVED_CONFIG_PATH.read_text().strip()
        if current != approved:
            raise ConfigError("approved configuration fingerprint mismatch")
        status.update({"verified": True, "fingerprint": current})
        return cfg, status
    except Exception as exc:
        status["error"] = type(exc).__name__
        return None, status


def _build_status() -> dict:
    try:
        manifest = verify_build_integrity()
        return {"verified": True, "manifest_hash": manifest["manifest_hash"], "error": None}
    except BuildIntegrityError as exc:
        return {"verified": False, "manifest_hash": None, "error": type(exc).__name__}
    except Exception as exc:
        return {"verified": False, "manifest_hash": None, "error": type(exc).__name__}


def _runtime(cfg: dict | None) -> tuple[dict | None, dict]:
    status = {"present": (DATA / "runtime_snapshot.json").exists(), "verified": False, "error": None}
    if cfg is None or not status["present"]:
        return None, status
    try:
        runtime = read_runtime(float(cfg["initial_equity"]))
        validate_runtime(runtime)
        status["verified"] = True
        return runtime, status
    except Exception as exc:
        status["error"] = type(exc).__name__
        return None, status


def _test_count(deep: dict) -> tuple[int | None, int | None]:
    if not isinstance(deep, dict):
        return None, None
    for cmd in deep.get("commands", []):
        if cmd.get("name") != "safety_tests":
            continue
        tail = str(cmd.get("tail", ""))
        match = re.search(r"ALL PASS \((\d+) tests\)", tail)
        if match:
            total = int(match.group(1))
            return total, total if cmd.get("passed") else None
    return None, None


def _public_decision(event: dict) -> dict:
    """Deliberately whitelist fields. Raw evidence packets never enter the public dashboard."""
    return sanitize({
        "ts": event.get("ts"),
        "seq": event.get("seq"),
        "event_id": event.get("event_id"),
        "event_kind": event.get("event_kind"),
        "subsystem": event.get("subsystem"),
        "symbol": event.get("symbol"),
        "severity": event.get("severity"),
        "action": event.get("action"),
        "requires_supervisor_review": bool(event.get("requires_supervisor_review")),
        "event_hash": event.get("event_hash"),
    })


def _public_escalation(item: dict) -> dict:
    return sanitize({
        "id": item.get("id"),
        "status": item.get("status"),
        "reason": item.get("reason"),
        "symbol": item.get("symbol"),
        "created_at": item.get("created_at"),
        "last_seen_at": item.get("last_seen_at"),
        "occurrences": item.get("occurrences", 1),
        "question": item.get("question"),
    })


def _chart_bar(bar: dict) -> dict | None:
    if not isinstance(bar, dict):
        return None
    keys = ("ts", "open", "high", "low", "close", "volume")
    if any(k not in bar for k in keys):
        return None
    return {k: bar.get(k) for k in keys}


def _public_market(state: dict, cfg: dict | None) -> dict:
    result: dict[str, dict] = {}
    market = state.get("market", {}) if isinstance(state, dict) else {}
    symbols: Iterable[str] = (cfg or {}).get("symbols", []) if cfg else market.keys()
    for symbol in symbols:
        item = market.get(symbol, {}) if isinstance(market, dict) else {}
        truth = item.get("truth", {}) if isinstance(item, dict) else {}
        consensus = item.get("consensus", {}) if isinstance(item, dict) else {}
        gate = item.get("forge_gate", {}) if isinstance(item, dict) else {}
        constitution = item.get("constitution_gate", {}) if isinstance(item, dict) else {}
        candle = item.get("candle_interpretation", {}) if isinstance(item, dict) else {}
        chart = []
        if truth.get("trusted_for_analysis"):
            for bar in (item.get("chart_series") or [])[-MAX_CHART_BARS:]:
                clean = _chart_bar(bar)
                if clean is not None:
                    chart.append(clean)
        result[symbol] = sanitize({
            "status": item.get("status", "UNAVAILABLE"),
            "source": truth.get("source"),
            "source_kind": truth.get("source_kind"),
            "source_family": truth.get("source_family"),
            "trusted_for_analysis": bool(truth.get("trusted_for_analysis", False)),
            "trusted_for_trade": bool(truth.get("trusted_for_trade", False)),
            "latest_bar_ts": truth.get("latest_bar_ts"),
            "age_minutes": truth.get("age_minutes"),
            "integrity_hash": truth.get("integrity_hash"),
            "last_closed_bar": item.get("last_closed_bar"),
            "direction": consensus.get("direction"),
            "signal_score": consensus.get("signal_score"),
            "strategy": consensus.get("strategy"),
            "strategy_conflict": bool(consensus.get("conflict", False)),
            "candle_context": candle.get("label"),
            "candle_evidence_strength": candle.get("evidence_strength"),
            "forge_gate_passed": bool(gate.get("passed", False)),
            "constitution_passed": bool(constitution.get("passed", False)),
            "chart_series": chart,
        })
    return result


def build_dashboard_payload() -> dict:
    generated = now_iso()
    cfg, config_status = _approved_config()
    build_status = _build_status()
    runtime, runtime_status = _runtime(cfg)
    state = _safe_json("state.json")
    health = _safe_json("health_latest.json")
    deep = _safe_json("deep_diagnostics_latest.json")
    evolution = _safe_json("evolution_latest.json")
    backtest = _safe_json("backtest.json")

    test_total, test_passed = _test_count(deep)
    portfolio = runtime.get("portfolio", {}) if runtime else {}
    decisions = runtime.get("decision_journal", []) if runtime else []
    escalations = runtime.get("escalation_queue", []) if runtime else []
    open_escalations = [e for e in escalations if isinstance(e, dict) and e.get("status") == "OPEN"]

    relay = None
    if runtime is not None and cfg is not None:
        try:
            relay = relay_health(runtime, cfg)
        except Exception:
            relay = {"passed": False, "action": "UNVERIFIED", "undelivered_events": None}
    if relay is None:
        relay = {"passed": False, "action": "NO_RUNTIME", "undelivered_events": None,
                 "last_ack_seq": None, "last_decision_seq": None}

    positions = []
    for symbol, pos in (portfolio.get("positions", {}) if isinstance(portfolio.get("positions", {}), dict) else {}).items():
        positions.append(sanitize({
            "symbol": symbol, "qty": pos.get("qty"), "entry": pos.get("entry"), "stop": pos.get("stop"),
            "target": pos.get("target"), "protected": bool(pos.get("protected")), "trailing": bool(pos.get("trailing")),
            "fill_bar_ts": pos.get("fill_bar_ts"), "last_processed_bar_ts": pos.get("last_processed_bar_ts"),
        }))

    pending = []
    for symbol, order in (portfolio.get("pending_entries", {}) if isinstance(portfolio.get("pending_entries", {}), dict) else {}).items():
        pending.append(sanitize({
            "symbol": symbol, "strategy": order.get("strategy"), "signal_score": order.get("signal_score"),
            "signal_bar_ts": order.get("signal_bar_ts"), "created_at": order.get("created_at"),
            "candle_context": order.get("candle_context"), "source": order.get("source"),
        }))

    # Synthetic diagnostics may reject an edge, but this release never upgrades them into proof.
    strategy_unproven = True

    market = _public_market(state, cfg)
    all_trade_untrusted = bool(market) and all(not x.get("trusted_for_trade") for x in market.values())
    truth_active = bool(market) and all(x.get("status") == "OK" for x in market.values())

    deep_checks = []
    for cmd in deep.get("commands", []) if isinstance(deep, dict) else []:
        deep_checks.append({"name": cmd.get("name"), "passed": bool(cmd.get("passed"))})
    if isinstance(deep, dict):
        deep_checks.extend([
            {"name": "chaos", "passed": bool((deep.get("chaos") or {}).get("passed"))},
            {"name": "security", "passed": bool((deep.get("security") or {}).get("passed"))},
        ])

    payload: Dict[str, Any] = {
        "schema_version": 1,
        "project": "Trip's",
        "release": (cfg or {}).get("version", "UNVERIFIED"),
        "generated_at": generated,
        "snapshot_policy": {
            "read_only": True,
            "max_display_age_seconds": 900,
            "integrity_required": True,
            "missing_or_stale_state_behavior": "SHOW_UNVERIFIED_NEVER_INVENT",
        },
        "identity": {
            "instrument_scope": (cfg or {}).get("instrument_scope", "UNVERIFIED"),
            "bar_interval": (cfg or {}).get("bar_interval", "UNVERIFIED"),
            "symbols": (cfg or {}).get("symbols", []),
        },
        "system": {
            "paper_only": bool(cfg and cfg.get("mode") == "paper"),
            "live_execution_supported": False,
            "trade_authority": "DATA_TRUTH + FORGE_GATE + CONSTITUTION",
            "health_is_trade_authority": False,
            "build_integrity": build_status,
            "config_integrity": config_status,
            "runtime_integrity": runtime_status,
            "truth_layer_active": truth_active,
            "all_markets_trade_untrusted": all_trade_untrusted,
        },
        "portfolio": {
            "equity": portfolio.get("equity"),
            "cash": portfolio.get("cash"),
            "peak_equity": portfolio.get("peak_equity"),
            "daily_pnl": portfolio.get("daily_pnl"),
            "cycle_count": portfolio.get("cycle_count"),
            "halted": bool(portfolio.get("halted", False)) if runtime else None,
            "halt_reason": portfolio.get("halt_reason") if runtime else None,
            "positions": positions,
            "pending_entries": pending,
            "metrics": state.get("metrics", {}) if isinstance(state, dict) else {},
        },
        "guardian": {
            "status": health.get("status", "UNVERIFIED") if isinstance(health, dict) else "UNVERIFIED",
            "generated_at": health.get("generated_at") if isinstance(health, dict) else None,
            "health_gate_passed": health.get("health_gate_passed") if isinstance(health, dict) else None,
            "checks": sanitize((health.get("checks") or [])[:30]) if isinstance(health, dict) else [],
            "repairs": sanitize((health.get("repairs") or [])[-20:]) if isinstance(health, dict) else [],
        },
        "diagnostics": {
            "generated_at": deep.get("generated_at") if isinstance(deep, dict) else None,
            "passed": deep.get("passed") if isinstance(deep, dict) else None,
            "action": deep.get("action") if isinstance(deep, dict) else "UNVERIFIED",
            "test_total": test_total,
            "test_passed": test_passed,
            "checks": deep_checks,
            "chaos": sanitize({
                "passed": (deep.get("chaos") or {}).get("passed") if isinstance(deep, dict) else None,
                "invalid_position_sizes": (deep.get("chaos") or {}).get("invalid_position_sizes") if isinstance(deep, dict) else None,
                "malformed_data_false_accepts": (deep.get("chaos") or {}).get("malformed_data_false_accepts") if isinstance(deep, dict) else None,
                "validator_crashes": (deep.get("chaos") or {}).get("validator_crashes") if isinstance(deep, dict) else None,
                "unsafe_supervisor_counsel_accepts": (deep.get("chaos") or {}).get("unsafe_supervisor_counsel_accepts") if isinstance(deep, dict) else None,
            }),
            "security_passed": (deep.get("security") or {}).get("passed") if isinstance(deep, dict) else None,
        },
        "market": market,
        "strategy": {
            "verdict": "UNPROVEN" if strategy_unproven else "UNDER_REVIEW",
            "live_trading_authorized": False,
            "synthetic_evidence_only": True,
            "all_small_sample": backtest.get("all_small_sample") if isinstance(backtest, dict) else None,
            "worst_net_pnl": backtest.get("worst_net_pnl") if isinstance(backtest, dict) else None,
            "best_net_pnl": backtest.get("best_net_pnl") if isinstance(backtest, dict) else None,
            "worst_drawdown_pct": backtest.get("worst_drawdown_pct") if isinstance(backtest, dict) else None,
        },
        "evolution": {
            "mode": evolution.get("mode", "UNVERIFIED") if isinstance(evolution, dict) else "UNVERIFIED",
            "champion_mutated": evolution.get("champion_mutated") if isinstance(evolution, dict) else None,
            "auto_promotion_allowed": evolution.get("auto_promotion_allowed") if isinstance(evolution, dict) else None,
            "proposals": sanitize((evolution.get("proposals") or [])[:MAX_EVOLUTION_PROPOSALS]) if isinstance(evolution, dict) else [],
        },
        "supervisor": {
            "transport_mode": (cfg or {}).get("supervisor", {}).get("transport_mode", "UNVERIFIED"),
            "delivery_state": (
                "UNVERIFIED" if relay.get("undelivered_events") is None
                else "OUTBOX_READY_NOT_DELIVERED" if relay.get("undelivered_events") > 0
                else "NO_UNDELIVERED_EVENTS" if relay.get("passed") else "UNVERIFIED"
            ),
            "relay_passed": relay.get("passed"),
            "relay_action": relay.get("action"),
            "undelivered_events": relay.get("undelivered_events"),
            "last_ack_seq": relay.get("last_ack_seq"),
            "last_decision_seq": relay.get("last_decision_seq"),
            "advice_execution_authority": "ZERO",
        },
        "activity": [_public_decision(e) for e in decisions[-MAX_ACTIVITY:]][::-1],
        "escalations": [_public_escalation(e) for e in open_escalations[-MAX_ESCALATIONS:]][::-1],
        "risk": {
            "max_risk_per_trade_pct": (cfg or {}).get("risk", {}).get("max_risk_per_trade_pct"),
            "max_daily_loss_pct": (cfg or {}).get("risk", {}).get("max_daily_loss_pct"),
            "max_total_exposure_pct": (cfg or {}).get("risk", {}).get("max_total_exposure_pct"),
            "max_open_positions": (cfg or {}).get("risk", {}).get("max_open_positions"),
            "halt_on_drawdown_pct": (cfg or {}).get("forge", {}).get("halt_on_drawdown_pct"),
            "no_martingale": True,
            "stale_data_rejection": True,
            "constitution_lock": True,
        },
        "capabilities": _safe_capabilities(),
        "operator_notices": [
            "Dashboard is read-only and has zero execution authority.",
            "Health PASS is not trade authorization.",
            "A queued supervisor event is not delivered until an authenticated acknowledgement advances the cursor.",
            "Synthetic/demo data may support analysis only; it cannot authorize a current-market trade.",
            "Strategy evidence remains unproven; no profitability or loss-prevention claim is made.",
        ],
    }
    return sanitize(payload)


def _safe_capabilities() -> dict:
    path = HERE / "capability_registry.json"
    try:
        data = json.loads(path.read_text())
        return sanitize({
            "release": data.get("release"),
            "supported": data.get("supported", {}),
            "not_supported": data.get("not_supported", []),
        })
    except Exception:
        return {"release": "UNVERIFIED", "supported": {}, "not_supported": ["CAPABILITY_REGISTRY_UNAVAILABLE"]}


def export_dashboard_snapshot() -> dict:
    payload = build_dashboard_payload()
    DOCS.joinpath("data").mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, indent=2, sort_keys=False, allow_nan=False).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    tmp = DASHBOARD_DATA.with_suffix(".json.tmp")
    tmp_hash = DASHBOARD_HASH.with_suffix(".sha256.tmp")
    tmp.write_bytes(raw)
    tmp_hash.write_text(digest + "\n")
    tmp.replace(DASHBOARD_DATA)
    tmp_hash.replace(DASHBOARD_HASH)
    return {"path": str(DASHBOARD_DATA), "sha256": digest, "generated_at": payload["generated_at"]}


if __name__ == "__main__":
    print(json.dumps(export_dashboard_snapshot(), indent=2))
