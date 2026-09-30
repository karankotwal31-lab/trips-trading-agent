from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from zoneinfo import ZoneInfo


class ConfigError(RuntimeError):
    pass


# Hard ceilings live in code, below user-editable config. They are intentionally conservative.
VALIDATED_SYMBOLS = {"SPY", "QQQ", "AAPL"}

HARD_LIMITS = {
    "max_risk_per_trade_pct": 0.01,
    "max_daily_loss_pct": 0.03,
    "max_total_exposure_pct": 0.50,
    "max_correlated_exposure_pct": 0.30,
    "max_open_positions": 5,
    "min_signal_score_floor": 0.65,
    "max_data_age_minutes": 240,
    "max_assumed_spread_bps": 50,
    "max_slippage_bps": 100,
    "halt_on_drawdown_pct": 0.10,
}


def fingerprint_config(cfg: dict) -> str:
    raw = json.dumps(cfg, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _need(condition: bool, message: str):
    if not condition:
        raise ConfigError(message)


def validate_config(raw: dict) -> dict:
    cfg = deepcopy(raw)
    _need(cfg.get("project_name") == "Trip's", "project_name must be exactly Trip's")
    _need(cfg.get("mode") == "paper", "Trip's hard constitution permits paper mode only")
    _need(cfg.get("instrument_scope") == "US_EQUITY_CASH_LONG_ONLY", "this release supports US cash equities, long-only, only")
    _need(cfg.get("bar_interval") == "60min", "this release has only been validated for 60min bars")
    _need(isinstance(cfg.get("symbols"), list) and cfg["symbols"], "symbols must be a non-empty list")
    _need(all(isinstance(s, str) and s.strip() and len(s) <= 32 for s in cfg["symbols"]), "invalid symbol")
    _need(all(s == s.strip().upper() for s in cfg["symbols"]), "symbols must use canonical uppercase form")
    normalized_symbols = {s.upper() for s in cfg["symbols"]}
    _need(len(normalized_symbols) == len(cfg["symbols"]), "symbols must be unique after canonicalization")
    _need(normalized_symbols <= VALIDATED_SYMBOLS,
          f"this release symbol scope is locked to {sorted(VALIDATED_SYMBOLS)}; additional symbols require separate validation")
    _need(float(cfg.get("initial_equity", 0)) > 0, "initial_equity must be positive")

    kinds = {"real", "delayed", "demo", "replay"}
    _need(cfg.get("provider_source_kind") in kinds, "invalid provider_source_kind")
    if cfg.get("secondary_provider") is not None:
        _need(cfg.get("secondary_source_kind") in kinds, "secondary_source_kind required")
    if cfg.get("provider") == "demo":
        _need(cfg.get("provider_source_kind") == "demo", "demo provider cannot be relabeled as real/delayed")
    if cfg.get("secondary_provider") == "demo":
        _need(cfg.get("secondary_source_kind") == "demo", "demo secondary provider cannot be relabeled")

    r = cfg["risk"]
    _need(0 < r["max_risk_per_trade_pct"] <= HARD_LIMITS["max_risk_per_trade_pct"], "max_risk_per_trade_pct exceeds hard ceiling")
    _need(0 < r["max_daily_loss_pct"] <= HARD_LIMITS["max_daily_loss_pct"], "max_daily_loss_pct exceeds hard ceiling")
    _need(0 < r["max_total_exposure_pct"] <= HARD_LIMITS["max_total_exposure_pct"], "max_total_exposure_pct exceeds hard ceiling")
    _need(
        0 < r["max_correlated_exposure_pct"] <= HARD_LIMITS["max_correlated_exposure_pct"],
        "max_correlated_exposure_pct exceeds hard ceiling")
    _need(
        r["max_correlated_exposure_pct"] < r["max_total_exposure_pct"],
        "max_correlated_exposure_pct must be strictly tighter than total exposure")
    groups = r.get("correlated_symbol_groups")
    _need(isinstance(groups, list) and groups, "correlated_symbol_groups must be a non-empty list")
    seen_correlated = set()
    for group in groups:
        _need(isinstance(group, list) and len(group) >= 2,
              "every correlated symbol group must contain at least two symbols")
        normalized_group = [str(symbol).strip().upper() for symbol in group]
        _need(len(set(normalized_group)) == len(normalized_group),
              "correlated symbol group contains duplicates")
        _need(set(normalized_group) <= VALIDATED_SYMBOLS,
              "correlated symbol group contains an unvalidated symbol")
        _need(not (set(normalized_group) & seen_correlated),
              "a symbol may belong to only one correlated exposure group")
        seen_correlated.update(normalized_group)
    _need(1 <= int(r["max_open_positions"]) <= HARD_LIMITS["max_open_positions"], "max_open_positions exceeds hard ceiling")
    _need(HARD_LIMITS["min_signal_score_floor"] <= r["min_signal_score"] <= 0.99, "min_signal_score outside allowed range")
    _need(1 <= r["max_data_age_minutes"] <= HARD_LIMITS["max_data_age_minutes"], "max_data_age_minutes outside hard bounds")
    _need(1 <= int(r["cooldown_after_losses"]) <= 10, "cooldown_after_losses outside hard bounds")
    _need(1 <= int(r["cooldown_cycles"]) <= 100, "cooldown_cycles outside hard bounds")
    _need(0 <= r["assumed_spread_bps"] <= r["max_assumed_spread_bps"] <= HARD_LIMITS["max_assumed_spread_bps"], "spread assumptions outside hard bounds")
    _need(0 <= r["assumed_slippage_bps"] <= HARD_LIMITS["max_slippage_bps"], "slippage assumption outside hard bounds")
    try:
        ZoneInfo(r["risk_day_timezone"])
    except Exception as e:
        raise ConfigError("invalid risk_day_timezone") from e

    t = cfg["trade"]
    _need(0.5 <= t["atr_stop_multiple"] <= 5.0, "atr_stop_multiple outside hard bounds")
    _need(0.5 <= t["target_r_multiple"] <= 10.0, "target_r_multiple outside hard bounds")
    _need(0.5 <= t["break_even_after_r"] <= 5.0, "break_even_after_r outside hard bounds")
    _need(t["break_even_after_r"] <= t["trail_after_r"] <= 10.0, "trail_after_r must be >= break-even threshold")
    _need(0.25 <= t["trail_atr_multiple"] <= 5.0, "trail_atr_multiple outside hard bounds")
    _need(1 <= int(t["pending_entry_max_bars"]) <= 3, "pending_entry_max_bars outside hard bounds")

    f = cfg["forge"]
    _need(int(f["require_history_bars"]) >= 60, "require_history_bars must be >= 60")
    _need(1 <= int(f["require_strategy_agreement"]) <= 3, "require_strategy_agreement outside hard bounds")
    _need(0 < f["escalate_on_drawdown_pct"] < f["halt_on_drawdown_pct"] <= HARD_LIMITS["halt_on_drawdown_pct"], "drawdown thresholds invalid")

    truth = cfg["truth"]
    _need(0 < truth["max_ohlc_deviation_pct"] <= 0.02, "max_ohlc_deviation_pct outside hard bounds")
    _need(0 <= truth["max_timestamp_skew_minutes"] <= 15, "max_timestamp_skew_minutes outside hard bounds")
    _need(1 <= int(truth["min_cross_source_bars"]) <= 10, "min_cross_source_bars outside hard bounds")
    _need(0 <= int(truth["bar_close_lag_seconds"]) <= 300, "bar_close_lag_seconds outside hard bounds")

    h = cfg.get("health") or {}
    _need(1 <= int(h.get("heartbeat_minutes", 0)) <= 60, "health heartbeat_minutes outside hard bounds")
    _need(5 <= int(h.get("stale_temp_minutes", 0)) <= 1440, "health stale_temp_minutes outside hard bounds")
    _need(64 <= int(h.get("disk_free_min_mb", 0)) <= 102400, "health disk_free_min_mb outside hard bounds")
    _need(h.get("safe_auto_repair") is True, "this release requires bounded safe auto repair")
    _need(h.get("daily_deep_diagnostics") is True and h.get("hourly_light_diagnostics") is True, "required diagnostics cadence disabled")

    e = cfg.get("evolution") or {}
    _need(e.get("enabled") is True and e.get("shadow_only") is True, "evolution must be enabled in shadow-only mode")
    _need(e.get("auto_apply_code_changes") is False, "evolution may not auto-apply code changes")
    _need(e.get("auto_apply_risk_changes") is False, "evolution may not auto-apply risk changes")
    _need(e.get("auto_apply_constitution_changes") is False, "evolution may not auto-apply Constitution changes")
    _need(e.get("require_out_of_sample") is True and e.get("require_human_approval") is True, "evolution promotion controls are mandatory")
    _need(30 <= int(e.get("min_candidate_trades", 0)) <= 10000, "evolution min_candidate_trades outside hard bounds")
    _need(3 <= int(e.get("min_walk_forward_windows", 0)) <= 100, "evolution min_walk_forward_windows outside hard bounds")

    sup = cfg.get("supervisor") or {}
    _need(sup.get("daily_packet") is True, "daily supervisor packet is mandatory")
    _need(sup.get("critical_escalation_immediate") is True, "critical escalation must be immediate")
    _need(sup.get("relay_every_decision") is True, "every substantive decision must be mirrored for supervisor review")
    _need(sup.get("transport_mode") == "local_outbox_until_hosted_bridge", "this release may not claim a direct supervisor transport that is not actually connected")
    _need(10 <= int(sup.get("max_undelivered_events", 0)) <= 10000, "supervisor max_undelivered_events outside hard bounds")
    _need(sup.get("halt_new_entries_on_relay_backlog") is True, "supervisor relay backlog must block new entries")
    _need(sup.get("supervisor_advice_is_advisory_only") is True, "supervisor advice must remain advisory only")
    _need(sup.get("require_external_verification_for_current_market_claims") is True, "current market supervisor claims require external verification")
    _need(sup.get("require_signed_ack") is True, "supervisor delivery acknowledgements must be cryptographically authenticated")
    try:
        ZoneInfo(sup.get("report_timezone", ""))
    except Exception as exc:
        raise ConfigError("invalid supervisor report_timezone") from exc
    time_value = str(sup.get("report_local_time", ""))
    _need(len(time_value) == 5 and time_value[2] == ":" and time_value[:2].isdigit() and time_value[3:].isdigit(), "invalid supervisor report_local_time")
    hh, mm = int(time_value[:2]), int(time_value[3:])
    _need(0 <= hh <= 23 and 0 <= mm <= 59, "invalid supervisor report_local_time")

    return cfg
