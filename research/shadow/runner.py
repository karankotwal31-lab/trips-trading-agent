"""No-order daily shadow runner for the preregistered Trend Harness.

This module reads only owner-supplied CSV market data and research specifications. It computes
hypothetical portfolio weights, mark-to-market and transaction-cost effects. It has no broker,
order, credential, engine, or network path.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.shadow.journal import JournalError, append_event, load_events, verify_file  # noqa: E402
from research.trend.core import (  # noqa: E402
    DailyBar,
    asset_class_map,
    load_owner_data,
    sha256_file,
    target_weights,
)


DEFAULT_STALE_TRADING_DAYS = 3
DEFAULT_HEARTBEAT_MISSED_BUSINESS_DAYS = 2


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def business_days_after(start: date, end: date) -> int:
    """Weekdays in (start, end]. Exchange holidays are intentionally not guessed."""
    if end <= start:
        return 0
    count = 0
    d = start
    from datetime import timedelta
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return count


def common_dates(data: Mapping[str, Sequence[DailyBar]]) -> list[date]:
    sets = [{row.date for row in rows} for rows in data.values() if rows]
    if not sets:
        return []
    return sorted(set.intersection(*sets))


def price_on(data: Mapping[str, Sequence[DailyBar]], symbol: str, d: date) -> float:
    for row in reversed(data[symbol]):
        if row.date == d:
            return float(row.adj_close)
        if row.date < d:
            break
    raise ValueError(f"{symbol} has no adjusted close for {d.isoformat()}")


def research_hashes(root: Path, data_dir: Path) -> dict[str, str | None]:
    trend = root / "research" / "trend"
    manifest = data_dir / "manifest.sha256"
    return {
        "strategy_spec": sha256_file(trend / "strategy_spec.json"),
        "universe": sha256_file(trend / "universe.json"),
        "cost_model": sha256_file(trend / "cost_model.json"),
        "pass_criteria": sha256_file(trend / "pass_criteria.json"),
        "data_manifest": sha256_file(manifest) if manifest.exists() else None,
    }


def write_heartbeat(
    path: Path,
    *,
    attempted_at: datetime,
    status: str,
    market_date: date | None,
    journal_hash: str | None,
    detail: object,
    success: bool,
) -> dict:
    previous = {}
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            previous = {}
    document = {
        "schema_version": 1,
        "last_attempt_at": attempted_at.astimezone(timezone.utc).isoformat(),
        "last_success_at": (
            attempted_at.astimezone(timezone.utc).isoformat()
            if success
            else previous.get("last_success_at")
        ),
        "status": status,
        "market_date": market_date.isoformat() if market_date else None,
        "journal_hash": journal_hash,
        "detail": detail,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return document


def heartbeat_health(
    path: Path,
    *,
    now: datetime | None = None,
    max_business_days: int = DEFAULT_HEARTBEAT_MISSED_BUSINESS_DAYS,
) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not path.exists():
        return {"healthy": False, "reason": "HEARTBEAT_MISSING", "missed_business_days": None}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        stamp = datetime.fromisoformat(str(doc["last_attempt_at"]).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("timezone missing")
        stamp = stamp.astimezone(timezone.utc)
    except Exception as exc:
        return {"healthy": False, "reason": f"HEARTBEAT_INVALID:{type(exc).__name__}", "missed_business_days": None}
    if stamp > now:
        return {"healthy": False, "reason": "HEARTBEAT_FROM_FUTURE", "missed_business_days": 0}
    missed = business_days_after(stamp.date(), now.date())
    return {
        "healthy": missed <= max_business_days,
        "reason": None if missed <= max_business_days else "MISSED_SHADOW_RUN",
        "missed_business_days": missed,
        "last_attempt_at": stamp.isoformat(),
    }


def _last_state(events: Sequence[Mapping[str, object]]) -> dict | None:
    for event in reversed(events):
        if event.get("kind") == "STATE":
            return dict(event)
    return None


def _cost_fraction(
    prior_weights: Mapping[str, float],
    target_weights_map: Mapping[str, float],
    classes: Mapping[str, str],
    cost_model: Mapping[str, object],
) -> tuple[float, list[dict]]:
    bps = dict(cost_model["base_cost_bps"])
    total = 0.0
    hypothetical: list[dict] = []
    for symbol in sorted(target_weights_map):
        before = float(prior_weights.get(symbol, 0.0))
        after = float(target_weights_map[symbol])
        delta = after - before
        if abs(delta) <= 1e-15:
            continue
        per_side = float(bps[classes[symbol]])
        contribution = abs(delta) * per_side / 10000.0
        total += contribution
        hypothetical.append({
            "symbol": symbol,
            "weight_before": before,
            "weight_after": after,
            "delta_weight": delta,
            "cost_bps_per_side": per_side,
            "modeled_cost_fraction": contribution,
        })
    return total, hypothetical


def _drift_weights(
    weights: Mapping[str, float],
    cash_weight: float,
    data: Mapping[str, Sequence[DailyBar]],
    previous_date: date,
    current_date: date,
) -> tuple[dict[str, float], float, float]:
    gross_growth = float(cash_weight)
    ratios: dict[str, float] = {}
    for symbol, weight in weights.items():
        ratio = price_on(data, symbol, current_date) / price_on(data, symbol, previous_date)
        ratios[symbol] = ratio
        gross_growth += float(weight) * ratio
    if not math.isfinite(gross_growth) or gross_growth <= 0:
        raise ValueError("non-positive or non-finite shadow portfolio growth")
    drifted = {
        symbol: float(weights[symbol]) * ratios[symbol] / gross_growth
        for symbol in sorted(weights)
    }
    drifted_cash = float(cash_weight) / gross_growth
    return drifted, drifted_cash, gross_growth


def _initial_state(symbols: Sequence[str], market_date: date) -> dict:
    return {
        "market_date": market_date.isoformat(),
        "equity": 1.0,
        "weights": {s: 0.0 for s in sorted(symbols)},
        "cash_weight": 1.0,
        "observed_days": 1,
        "rebalance_count": 0,
        "cumulative_cost_fraction": 0.0,
        "modeled_cost_fraction": 0.0,
        "max_missing_trading_days": 0,
    }


def run_shadow_once(
    *,
    data_dir: Path,
    journal_path: Path,
    heartbeat_path: Path,
    root: Path = ROOT,
    attempted_at: datetime | None = None,
    stale_trading_days: int = DEFAULT_STALE_TRADING_DAYS,
) -> dict:
    """Process at most one new common market date and never transmit an order."""
    attempted_at = (attempted_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    chain = verify_file(journal_path)
    if not chain["valid"]:
        write_heartbeat(
            heartbeat_path,
            attempted_at=attempted_at,
            status="JOURNAL_INVALID",
            market_date=None,
            journal_hash=None,
            detail=chain["reason"],
            success=False,
        )
        raise JournalError(str(chain["reason"]))

    trend = root / "research" / "trend"
    spec = _json(trend / "strategy_spec.json")
    universe = _json(trend / "universe.json")
    costs = _json(trend / "cost_model.json")
    classes_all = asset_class_map(universe)
    loaded = load_owner_data(data_dir, universe)
    hashes = research_hashes(root, data_dir)

    if not loaded["sufficient"]:
        event = append_event(journal_path, {
            "kind": "NO_ENTRY",
            "attempted_at": attempted_at.isoformat(),
            "market_date": None,
            "reason": "INVALID_OR_INSUFFICIENT_DATA",
            "detail": list(loaded["reasons"]),
            "hashes": hashes,
            "execution_authority": "NONE",
        })
        write_heartbeat(
            heartbeat_path,
            attempted_at=attempted_at,
            status="NO_ENTRY_INVALID_DATA",
            market_date=None,
            journal_hash=event["event_hash"],
            detail=list(loaded["reasons"]),
            success=False,
        )
        return {"status": "NO_ENTRY_INVALID_DATA", "event": event}

    data = loaded["data"]
    dates = common_dates(data)
    if not dates:
        raise ValueError("validated data has no common dates")
    latest = dates[-1]
    stale_age = business_days_after(latest, attempted_at.date())
    if stale_age > stale_trading_days:
        event = append_event(journal_path, {
            "kind": "NO_ENTRY",
            "attempted_at": attempted_at.isoformat(),
            "market_date": latest.isoformat(),
            "reason": "STALE_DATA",
            "stale_business_days": stale_age,
            "hashes": hashes,
            "execution_authority": "NONE",
        })
        write_heartbeat(
            heartbeat_path,
            attempted_at=attempted_at,
            status="NO_ENTRY_STALE_DATA",
            market_date=latest,
            journal_hash=event["event_hash"],
            detail={"stale_business_days": stale_age},
            success=False,
        )
        return {"status": "NO_ENTRY_STALE_DATA", "event": event}

    events = load_events(journal_path)
    previous = _last_state(events)
    if previous is None:
        state = _initial_state(sorted(data), latest)
        event = append_event(journal_path, {
            "kind": "STATE",
            "attempted_at": attempted_at.isoformat(),
            "hashes": hashes,
            "execution_authority": "NONE",
            **state,
        })
        write_heartbeat(
            heartbeat_path,
            attempted_at=attempted_at,
            status="INITIALIZED",
            market_date=latest,
            journal_hash=event["event_hash"],
            detail={"observed_days": 1},
            success=True,
        )
        return {"status": "INITIALIZED", "state": event}

    previous_date = date.fromisoformat(str(previous["market_date"]))
    if latest <= previous_date:
        hb = write_heartbeat(
            heartbeat_path,
            attempted_at=attempted_at,
            status="NO_NEW_MARKET_DATE",
            market_date=previous_date,
            journal_hash=str(previous["event_hash"]),
            detail={"latest_available": latest.isoformat()},
            success=True,
        )
        return {"status": "NO_NEW_MARKET_DATE", "heartbeat": hb}

    missing = max(0, business_days_after(previous_date, latest) - 1)
    prior_weights = {str(k): float(v) for k, v in dict(previous["weights"]).items()}
    drifted, drifted_cash, growth = _drift_weights(
        prior_weights,
        float(previous["cash_weight"]),
        data,
        previous_date,
        latest,
    )
    equity_before_cost = float(previous["equity"]) * growth
    equity = equity_before_cost
    cumulative_cost = float(previous["cumulative_cost_fraction"])
    modeled_cost = float(previous["modeled_cost_fraction"])
    rebalances = int(previous["rebalance_count"])
    rebalance_event = None

    # The prior observed date is only known to be month-end when the next market date arrives in
    # a new month. This preserves the exact close-t decision / close-t+1 execution lag without
    # guessing holidays or using future data.
    if (previous_date.year, previous_date.month) != (latest.year, latest.month):
        sizing = dict(spec["sizing"])
        signal = dict(spec["signal"])
        volatility = dict(spec["volatility"])
        target = target_weights(
            data,
            {s: classes_all[s] for s in data},
            as_of=previous_date,
            lookback=int(signal["lookback_days"]),
            vol_window=int(volatility["window_days"]),
            target_vol=float(volatility["target_portfolio_volatility"]),
            per_asset_cap=float(sizing["per_asset_cap"]),
            per_class_cap=float(sizing["per_class_cap"]),
            gross_cap=float(sizing["gross_cap"]),
        )
        decision = append_event(journal_path, {
            "kind": "DECISION",
            "attempted_at": attempted_at.isoformat(),
            "decision_market_date": previous_date.isoformat(),
            "execution_market_date": latest.isoformat(),
            "target_weights": target,
            "hashes": hashes,
            "execution_authority": "NONE",
            "causal_rule": "close_t_to_close_t_plus_1",
        })
        cost_fraction, hypothetical = _cost_fraction(
            drifted,
            target,
            {s: classes_all[s] for s in data},
            costs,
        )
        if cost_fraction >= 1:
            raise ValueError("modeled shadow cost exhausted portfolio")
        equity *= 1.0 - cost_fraction
        cumulative_cost += cost_fraction
        modeled_cost += cost_fraction
        drifted = {s: float(target.get(s, 0.0)) for s in sorted(data)}
        drifted_cash = 1.0 - sum(drifted.values())
        rebalances += 1
        rebalance_event = append_event(journal_path, {
            "kind": "HYPOTHETICAL_REBALANCE",
            "attempted_at": attempted_at.isoformat(),
            "decision_event_hash": decision["event_hash"],
            "decision_market_date": previous_date.isoformat(),
            "execution_market_date": latest.isoformat(),
            "hypothetical_trades": hypothetical,
            "cost_fraction": cost_fraction,
            "equity_before_cost": equity_before_cost,
            "equity_after_cost": equity,
            "execution_authority": "NONE",
            "broker_contacted": False,
            "order_submitted": False,
        })

    state_payload = {
        "kind": "STATE",
        "attempted_at": attempted_at.isoformat(),
        "market_date": latest.isoformat(),
        "equity": equity,
        "weights": drifted,
        "cash_weight": drifted_cash,
        "observed_days": int(previous["observed_days"]) + 1,
        "rebalance_count": rebalances,
        "cumulative_cost_fraction": cumulative_cost,
        "modeled_cost_fraction": modeled_cost,
        "max_missing_trading_days": max(int(previous["max_missing_trading_days"]), missing),
        "missing_trading_days_since_prior_state": missing,
        "hashes": hashes,
        "execution_authority": "NONE",
    }
    state_event = append_event(journal_path, state_payload)
    write_heartbeat(
        heartbeat_path,
        attempted_at=attempted_at,
        status="PROCESSED",
        market_date=latest,
        journal_hash=state_event["event_hash"],
        detail={
            "observed_days": state_payload["observed_days"],
            "rebalance_count": rebalances,
            "missing_trading_days": missing,
        },
        success=True,
    )
    return {
        "status": "PROCESSED",
        "state": state_event,
        "rebalance": rebalance_event,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, default=ROOT / "research" / "data" / "daily")
    ap.add_argument("--journal", type=Path, default=ROOT / "research" / "shadow" / "journal.jsonl")
    ap.add_argument("--heartbeat", type=Path, default=ROOT / "research" / "shadow" / "heartbeat.json")
    ap.add_argument("--attempted-at", default=None, help="timezone-aware ISO-8601; tests/replay only")
    ap.add_argument("--stale-trading-days", type=int, default=DEFAULT_STALE_TRADING_DAYS)
    args = ap.parse_args()
    attempted = (
        datetime.fromisoformat(args.attempted_at.replace("Z", "+00:00"))
        if args.attempted_at
        else None
    )
    result = run_shadow_once(
        data_dir=args.data_dir,
        journal_path=args.journal,
        heartbeat_path=args.heartbeat,
        attempted_at=attempted,
        stale_trading_days=args.stale_trading_days,
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
