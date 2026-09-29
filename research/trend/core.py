"""Pure research trend harness. Standard library only; no Trip's engine imports."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import statistics
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence


TRADING_DAYS = 252
REQUIRED_COLUMNS = ("date", "open", "high", "low", "close", "adj_close", "volume")


class DataValidationError(ValueError):
    pass


@dataclass(frozen=True)
class DailyBar:
    date: date
    open: float
    high: float
    low: float
    close: float
    adj_close: float
    volume: float

    def to_dict(self) -> dict:
        out = asdict(self)
        out["date"] = self.date.isoformat()
        return out


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _finite(value: str, field: str) -> float:
    try:
        number = float(value)
    except Exception as exc:
        raise DataValidationError(f"{field} is not numeric") from exc
    if not math.isfinite(number):
        raise DataValidationError(f"{field} must be finite")
    return number


def parse_csv(path: Path) -> list[DailyBar]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != REQUIRED_COLUMNS:
            raise DataValidationError(
                f"{path.name}: columns must be exactly {','.join(REQUIRED_COLUMNS)}"
            )
        rows: list[DailyBar] = []
        previous: date | None = None
        for line_no, row in enumerate(reader, 2):
            try:
                d = date.fromisoformat(str(row["date"]))
            except Exception as exc:
                raise DataValidationError(f"{path.name}:{line_no}: invalid ISO date") from exc
            if previous is not None and d <= previous:
                raise DataValidationError(
                    f"{path.name}:{line_no}: dates must be strictly increasing and unique"
                )
            previous = d
            o = _finite(row["open"], "open")
            h = _finite(row["high"], "high")
            l = _finite(row["low"], "low")
            c = _finite(row["close"], "close")
            a = _finite(row["adj_close"], "adj_close")
            v = _finite(row["volume"], "volume")
            if min(o, h, l, c, a) <= 0:
                raise DataValidationError(f"{path.name}:{line_no}: prices must be positive")
            if v < 0:
                raise DataValidationError(f"{path.name}:{line_no}: volume must be non-negative")
            if h < max(o, c, l) or l > min(o, c, h):
                raise DataValidationError(f"{path.name}:{line_no}: OHLC invariant failed")
            rows.append(DailyBar(d, o, h, l, c, a, v))
    if not rows:
        raise DataValidationError(f"{path.name}: empty CSV")
    return rows


def read_manifest(path: Path) -> dict[str, str]:
    if not path.exists():
        raise DataValidationError("manifest.sha256 is missing")
    out: dict[str, str] = {}
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2:
            raise DataValidationError(f"manifest:{line_no}: expected '<sha256>  <file>'")
        digest, filename = parts
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise DataValidationError(f"manifest:{line_no}: invalid lowercase SHA-256")
        if Path(filename).name != filename or not filename.endswith(".csv"):
            raise DataValidationError(f"manifest:{line_no}: invalid CSV filename")
        if filename in out:
            raise DataValidationError(f"manifest:{line_no}: duplicate filename")
        out[filename] = digest
    if not out:
        raise DataValidationError("manifest.sha256 contains no CSV entries")
    return out


def asset_class_map(universe_spec: Mapping[str, object]) -> dict[str, str]:
    classes = universe_spec.get("classes")
    if not isinstance(classes, Mapping):
        raise DataValidationError("universe classes missing")
    out: dict[str, str] = {}
    for cls, symbols in classes.items():
        if not isinstance(cls, str) or not isinstance(symbols, list):
            raise DataValidationError("invalid universe class mapping")
        for symbol in symbols:
            if not isinstance(symbol, str) or not symbol:
                raise DataValidationError("invalid universe symbol")
            if symbol in out:
                raise DataValidationError(f"duplicate universe symbol: {symbol}")
            out[symbol] = cls
    return out


def dataset_sufficiency(
    data: Mapping[str, Sequence[DailyBar]],
    *,
    minimum_valid_assets: int,
    minimum_history_years: float,
    required_periods: Sequence[str],
) -> dict:
    reasons: list[str] = []
    valid_assets = sorted(k for k, rows in data.items() if rows)
    if len(valid_assets) < minimum_valid_assets:
        reasons.append(
            f"valid assets {len(valid_assets)} < required {minimum_valid_assets}"
        )
    if not valid_assets:
        return {
            "sufficient": False,
            "reasons": reasons or ["no valid assets"],
            "valid_assets": [],
            "common_start": None,
            "common_end": None,
            "history_years": 0.0,
            "required_periods_present": False,
        }
    common_start = max(data[s][0].date for s in valid_assets)
    common_end = min(data[s][-1].date for s in valid_assets)
    history_years = max(0.0, (common_end - common_start).days / 365.2425)
    if common_end <= common_start or history_years < minimum_history_years:
        reasons.append(
            f"common history {history_years:.3f} years < required {minimum_history_years}"
        )
    periods_ok = True
    for spec in required_periods:
        try:
            start_s, end_s = spec.split("/", 1)
            start, end = date.fromisoformat(start_s), date.fromisoformat(end_s)
        except Exception:
            reasons.append(f"invalid required period specification: {spec}")
            periods_ok = False
            continue
        if common_start > start or common_end < end:
            periods_ok = False
            reasons.append(f"common history does not cover required period {spec}")
    return {
        "sufficient": not reasons,
        "reasons": reasons,
        "valid_assets": valid_assets,
        "common_start": common_start.isoformat(),
        "common_end": common_end.isoformat(),
        "history_years": history_years,
        "required_periods_present": periods_ok,
    }


def load_owner_data(data_dir: Path, universe_spec: Mapping[str, object]) -> dict:
    mapping = asset_class_map(universe_spec)
    manifest_path = data_dir / "manifest.sha256"
    try:
        manifest = read_manifest(manifest_path)
    except DataValidationError as exc:
        suff = dataset_sufficiency(
            {},
            minimum_valid_assets=int(universe_spec["minimum_valid_assets"]),
            minimum_history_years=float(universe_spec["minimum_history_years"]),
            required_periods=list(universe_spec["required_periods"]),
        )
        suff["reasons"] = [str(exc)] + list(suff["reasons"])
        return {
            "data": {},
            "invalid_assets": {s: "manifest unavailable" for s in sorted(mapping)},
            "manifest_hash": None,
            **suff,
        }

    data: dict[str, list[DailyBar]] = {}
    invalid: dict[str, str] = {}
    for symbol in sorted(mapping):
        filename = f"{symbol}.csv"
        path = data_dir / filename
        if filename not in manifest:
            invalid[symbol] = "not present in manifest"
            continue
        if not path.exists():
            invalid[symbol] = "CSV missing"
            continue
        actual = sha256_file(path)
        if actual != manifest[filename]:
            invalid[symbol] = f"SHA-256 mismatch expected={manifest[filename]} actual={actual}"
            continue
        try:
            data[symbol] = parse_csv(path)
        except DataValidationError as exc:
            invalid[symbol] = str(exc)

    extra = sorted(set(manifest) - {f"{s}.csv" for s in mapping})
    if extra:
        invalid["__manifest__"] = f"universe-unlisted CSV entries: {extra}"

    suff = dataset_sufficiency(
        data,
        minimum_valid_assets=int(universe_spec["minimum_valid_assets"]),
        minimum_history_years=float(universe_spec["minimum_history_years"]),
        required_periods=list(universe_spec["required_periods"]),
    )
    if extra:
        suff["sufficient"] = False
        suff["reasons"] = list(suff["reasons"]) + [invalid["__manifest__"]]
    return {
        "data": data,
        "invalid_assets": invalid,
        "manifest_hash": sha256_file(manifest_path),
        **suff,
    }


def _simple_returns(rows: Sequence[DailyBar], window: int) -> list[float]:
    if len(rows) < window + 1:
        return []
    prices = [b.adj_close for b in rows[-(window + 1):]]
    return [prices[i] / prices[i - 1] - 1.0 for i in range(1, len(prices))]


def target_weights(
    data: Mapping[str, Sequence[DailyBar]],
    asset_classes: Mapping[str, str],
    *,
    as_of: date | None = None,
    lookback: int = 252,
    vol_window: int = 60,
    target_vol: float = 0.10,
    per_asset_cap: float = 0.20,
    per_class_cap: float = 0.40,
    gross_cap: float = 1.0,
) -> dict[str, float]:
    """Pure deterministic target-weight function. It sees only rows dated <= as_of."""
    if lookback < 2 or vol_window < 2:
        raise ValueError("lookback and vol_window must be >= 2")
    if not (0 < target_vol <= 1 and 0 < per_asset_cap <= 1 and 0 < per_class_cap <= 1):
        raise ValueError("invalid positive sizing limits")
    if not (0 < gross_cap <= 1):
        raise ValueError("gross_cap must be in (0,1] for this no-leverage harness")

    vol: dict[str, float] = {}
    active: list[str] = []
    for symbol in sorted(data):
        rows = [r for r in data[symbol] if as_of is None or r.date <= as_of]
        if len(rows) < max(lookback, vol_window + 1):
            continue
        sma = sum(r.adj_close for r in rows[-lookback:]) / lookback
        if not rows[-1].adj_close > sma:
            continue
        rets = _simple_returns(rows, vol_window)
        if len(rets) < vol_window:
            continue
        daily_sd = statistics.stdev(rets)
        ann = daily_sd * math.sqrt(TRADING_DAYS)
        if not math.isfinite(ann) or ann <= 0:
            continue
        active.append(symbol)
        vol[symbol] = ann

    weights = {s: 0.0 for s in sorted(data)}
    if not active:
        return weights
    inv = {s: 1.0 / vol[s] for s in active}
    inv_sum = sum(inv.values())
    normalized = {s: inv[s] / inv_sum for s in active}
    estimated_vol = math.sqrt(sum((normalized[s] * vol[s]) ** 2 for s in active))
    scale = 1.0 if estimated_vol <= 0 else min(1.0, target_vol / estimated_vol)
    for s in active:
        weights[s] = min(per_asset_cap, normalized[s] * scale)

    class_totals: dict[str, float] = {}
    for s, w in weights.items():
        cls = asset_classes[s]
        class_totals[cls] = class_totals.get(cls, 0.0) + w
    for cls, total in sorted(class_totals.items()):
        if total > per_class_cap:
            factor = per_class_cap / total
            for s in sorted(weights):
                if asset_classes[s] == cls:
                    weights[s] *= factor

    gross = sum(weights.values())
    if gross > gross_cap:
        factor = gross_cap / gross
        weights = {s: w * factor for s, w in weights.items()}

    for s, w in weights.items():
        if w < -1e-15 or w > per_asset_cap + 1e-12:
            raise AssertionError(f"weight constraint failed for {s}: {w}")
    if sum(weights.values()) > gross_cap + 1e-12:
        raise AssertionError("gross cap failed")
    return weights


def _common_dates(data: Mapping[str, Sequence[DailyBar]]) -> list[date]:
    sets = [{r.date for r in rows} for rows in data.values() if rows]
    if not sets:
        return []
    return sorted(set.intersection(*sets))


def _price_maps(data: Mapping[str, Sequence[DailyBar]]) -> dict[str, dict[date, float]]:
    return {s: {r.date: r.adj_close for r in rows} for s, rows in data.items()}


def _is_month_end(dates: Sequence[date], index: int) -> bool:
    return index == len(dates) - 1 or (
        dates[index + 1].year,
        dates[index + 1].month,
    ) != (dates[index].year, dates[index].month)


def simulate_allocations(
    data: Mapping[str, Sequence[DailyBar]],
    asset_classes: Mapping[str, str],
    cost_model: Mapping[str, object],
    *,
    cost_multiplier: float,
    target_fn: Callable[[date], Mapping[str, float]],
) -> dict:
    dates = _common_dates(data)
    if len(dates) < 2:
        raise ValueError("need at least two common dates")
    prices = _price_maps(data)
    weights = {s: 0.0 for s in sorted(data)}
    cash_weight = 1.0
    equity = 1.0
    equity_curve = [equity]
    daily_returns: list[float] = []
    pending: tuple[date, dict[str, float]] | None = None
    turnover = 0.0
    cost_paid = 0.0
    trade_count = 0
    executions: list[dict] = []
    bps_map = dict(cost_model["base_cost_bps"])

    for i in range(1, len(dates)):
        prev_d, d = dates[i - 1], dates[i]
        before = equity
        gross_growth = cash_weight
        pretrade: dict[str, float] = {}
        for s in weights:
            ratio = prices[s][d] / prices[s][prev_d]
            gross_growth += weights[s] * ratio
        if gross_growth <= 0 or not math.isfinite(gross_growth):
            raise ValueError("non-positive portfolio growth")
        equity *= gross_growth
        for s in weights:
            ratio = prices[s][d] / prices[s][prev_d]
            pretrade[s] = (weights[s] * ratio) / gross_growth
        pre_cash = cash_weight / gross_growth

        if pending is not None:
            decision_date, target = pending
            delta = {s: float(target.get(s, 0.0)) - pretrade.get(s, 0.0) for s in weights}
            one_way = sum(abs(x) for x in delta.values())
            cost_fraction = 0.0
            for s, change in delta.items():
                cls = asset_classes[s]
                cost_fraction += abs(change) * float(bps_map[cls]) * cost_multiplier / 10000.0
                if abs(change) > 1e-12:
                    trade_count += 1
            if cost_fraction >= 1:
                raise ValueError("transaction cost exhausted portfolio")
            cost_value = equity * cost_fraction
            equity -= cost_value
            cost_paid += cost_value
            turnover += one_way
            weights = {s: float(target.get(s, 0.0)) for s in weights}
            cash_weight = 1.0 - sum(weights.values())
            if cash_weight < -1e-10:
                raise ValueError("target weights imply leverage")
            executions.append(
                {
                    "decision_date": decision_date.isoformat(),
                    "execution_date": d.isoformat(),
                    "turnover": one_way,
                    "cost_fraction": cost_fraction,
                }
            )
            pending = None
        else:
            weights = pretrade
            cash_weight = pre_cash

        daily_returns.append(equity / before - 1.0)
        equity_curve.append(equity)

        if _is_month_end(dates, i) and i < len(dates) - 1:
            target = {s: float(w) for s, w in target_fn(d).items()}
            if any(w < -1e-12 for w in target.values()) or sum(target.values()) > 1 + 1e-12:
                raise ValueError("target function violated no-short/no-leverage constraints")
            pending = (d, target)

    years = max((dates[-1] - dates[0]).days / 365.2425, 1 / 365.2425)
    return {
        "dates": [d.isoformat() for d in dates],
        "equity": equity_curve,
        "daily_returns": daily_returns,
        "ending_equity": equity,
        "turnover_total": turnover,
        "turnover_annualized": turnover / years,
        "trade_count": trade_count,
        "cost_paid_normalized": cost_paid,
        "execution_log": executions,
    }


def simulate_strategy(
    data: Mapping[str, Sequence[DailyBar]],
    asset_classes: Mapping[str, str],
    strategy_spec: Mapping[str, object],
    cost_model: Mapping[str, object],
    *,
    cost_multiplier: float,
    lookback: int | None = None,
    vol_window: int | None = None,
) -> dict:
    signal = dict(strategy_spec["signal"])
    volatility = dict(strategy_spec["volatility"])
    sizing = dict(strategy_spec["sizing"])
    lb = int(lookback if lookback is not None else signal["lookback_days"])
    vw = int(vol_window if vol_window is not None else volatility["window_days"])

    def targets(d: date) -> Mapping[str, float]:
        return target_weights(
            data,
            asset_classes,
            as_of=d,
            lookback=lb,
            vol_window=vw,
            target_vol=float(volatility["target_portfolio_volatility"]),
            per_asset_cap=float(sizing["per_asset_cap"]),
            per_class_cap=float(sizing["per_class_cap"]),
            gross_cap=float(sizing["gross_cap"]),
        )

    return simulate_allocations(
        data,
        asset_classes,
        cost_model,
        cost_multiplier=cost_multiplier,
        target_fn=targets,
    )


def _max_drawdown(equity: Sequence[float]) -> float:
    peak = equity[0]
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        worst = max(worst, 1.0 - value / peak)
    return worst


def metrics_from_equity(
    dates: Sequence[str | date],
    equity: Sequence[float],
    *,
    turnover_annualized: float = 0.0,
    cost_drag_cagr: float = 0.0,
) -> dict:
    if len(dates) != len(equity) or len(equity) < 2:
        raise ValueError("dates/equity length mismatch or insufficient observations")
    ds = [d if isinstance(d, date) else date.fromisoformat(d) for d in dates]
    returns = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity))]
    years = max((ds[-1] - ds[0]).days / 365.2425, 1 / 365.2425)
    cagr = (equity[-1] / equity[0]) ** (1 / years) - 1 if equity[0] > 0 else float("nan")
    vol = statistics.stdev(returns) * math.sqrt(TRADING_DAYS) if len(returns) >= 2 else 0.0
    mean = statistics.mean(returns) if returns else 0.0
    sharpe = mean / statistics.stdev(returns) * math.sqrt(TRADING_DAYS) if len(returns) >= 2 and statistics.stdev(returns) > 0 else 0.0
    downside = [min(r, 0.0) for r in returns]
    downside_dev = math.sqrt(sum(r * r for r in downside) / len(downside)) if downside else 0.0
    sortino = mean * TRADING_DAYS / (downside_dev * math.sqrt(TRADING_DAYS)) if downside_dev > 0 else (float("inf") if mean > 0 else 0.0)
    max_dd = _max_drawdown(equity)
    calmar = cagr / max_dd if max_dd > 0 else (float("inf") if cagr > 0 else 0.0)
    worst_12m = None
    if len(equity) > TRADING_DAYS:
        worst_12m = min(equity[i] / equity[i - TRADING_DAYS] - 1 for i in range(TRADING_DAYS, len(equity)))
    rolling = []
    window = TRADING_DAYS * 3
    if len(equity) > window:
        rolling = [equity[i] / equity[i - window] - 1 for i in range(window, len(equity))]
    rolling_positive = (sum(r > 0 for r in rolling) / len(rolling)) if rolling else None
    return {
        "cagr": cagr,
        "annualized_volatility": vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "turnover_annualized": turnover_annualized,
        "cost_drag_cagr": cost_drag_cagr,
        "worst_12m": worst_12m,
        "rolling_3y_positive_pct": rolling_positive,
        "observations": len(equity),
    }


def _finite_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    if math.isinf(value):
        return 1e12 if value > 0 else -1e12
    if not math.isfinite(value):
        raise ValueError("metric is not finite")
    return value


def _metric_bundle(sim: Mapping[str, object], no_cost: Mapping[str, object]) -> dict:
    m = metrics_from_equity(
        sim["dates"],
        sim["equity"],
        turnover_annualized=float(sim["turnover_annualized"]),
    )
    no = metrics_from_equity(no_cost["dates"], no_cost["equity"])
    m["cost_drag_cagr"] = no["cagr"] - m["cagr"]
    m["trade_count"] = int(sim["trade_count"])
    m["ending_equity"] = float(sim["ending_equity"])
    return {k: _finite_or_none(v) if isinstance(v, float) or v is None else v for k, v in m.items()}


def _buy_and_hold(
    data: Mapping[str, Sequence[DailyBar]],
    symbol: str,
    cost_model: Mapping[str, object],
    asset_classes: Mapping[str, str],
    *,
    cost_multiplier: float,
) -> dict:
    if symbol not in data:
        raise ValueError(f"benchmark symbol missing: {symbol}")
    dates = _common_dates(data)
    prices = _price_maps(data)[symbol]
    bps = float(cost_model["base_cost_bps"][asset_classes[symbol]]) * cost_multiplier
    initial = 1.0 - bps / 10000.0
    eq = [initial]
    for i in range(1, len(dates)):
        eq.append(eq[-1] * prices[dates[i]] / prices[dates[i - 1]])
    return {
        "dates": [d.isoformat() for d in dates],
        "equity": eq,
        "daily_returns": [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq))],
        "ending_equity": eq[-1],
        "turnover_annualized": 1.0 / max((dates[-1] - dates[0]).days / 365.2425, 1 / 365.2425),
        "trade_count": 1,
    }


def _fixed_monthly(
    data: Mapping[str, Sequence[DailyBar]],
    asset_classes: Mapping[str, str],
    cost_model: Mapping[str, object],
    fixed: Mapping[str, float],
    *,
    cost_multiplier: float,
) -> dict:
    def fn(_: date) -> Mapping[str, float]:
        return {s: float(fixed.get(s, 0.0)) for s in data}

    return simulate_allocations(data, asset_classes, cost_model, cost_multiplier=cost_multiplier, target_fn=fn)


def _stability_windows(dates: Sequence[str], equity: Sequence[float]) -> list[dict]:
    width = TRADING_DAYS * 5
    step = TRADING_DAYS
    out = []
    for start in range(0, max(0, len(equity) - width), step):
        end = start + width
        if end >= len(equity):
            break
        m = metrics_from_equity(dates[start : end + 1], equity[start : end + 1])
        out.append(
            {
                "start": dates[start],
                "end": dates[end],
                "cagr": _finite_or_none(m["cagr"]),
                "sharpe": _finite_or_none(m["sharpe"]),
                "max_drawdown": _finite_or_none(m["max_drawdown"]),
            }
        )
    return out


def bootstrap_sharpes(
    returns: Sequence[float],
    *,
    seed: int,
    block_length: int,
    replications: int,
) -> list[float]:
    if not returns or block_length < 1 or replications < 1:
        return []
    rng = random.Random(seed)
    n = len(returns)
    out: list[float] = []
    for _ in range(replications):
        sample: list[float] = []
        while len(sample) < n:
            start = rng.randrange(n)
            for j in range(block_length):
                sample.append(returns[(start + j) % n])
                if len(sample) == n:
                    break
        if len(sample) < 2 or statistics.stdev(sample) == 0:
            out.append(0.0)
        else:
            out.append(statistics.mean(sample) / statistics.stdev(sample) * math.sqrt(TRADING_DAYS))
    return out


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        raise ValueError("empty percentile input")
    if not 0 <= q <= 1:
        raise ValueError("q outside [0,1]")
    ordered = sorted(values)
    pos = q * (len(ordered) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    frac = pos - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def build_backtest_report(
    data: Mapping[str, Sequence[DailyBar]],
    universe_spec: Mapping[str, object],
    strategy_spec: Mapping[str, object],
    cost_model: Mapping[str, object],
    *,
    data_metadata: Mapping[str, object],
) -> dict:
    classes = asset_class_map(universe_spec)
    classes = {s: classes[s] for s in data}
    no_cost = simulate_strategy(data, classes, strategy_spec, cost_model, cost_multiplier=0.0)
    strategy: dict[str, dict] = {}
    simulations: dict[str, dict] = {}
    for multiplier in (1, 2, 4):
        sim = simulate_strategy(data, classes, strategy_spec, cost_model, cost_multiplier=float(multiplier))
        simulations[f"{multiplier}x"] = sim
        strategy[f"{multiplier}x"] = _metric_bundle(sim, no_cost)

    valid = sorted(data)
    eq_fixed = {s: 1.0 / len(valid) for s in valid}
    spy = _buy_and_hold(data, "SPY", cost_model, classes, cost_multiplier=2.0)
    spy_no = _buy_and_hold(data, "SPY", cost_model, classes, cost_multiplier=0.0)
    ew = _fixed_monthly(data, classes, cost_model, eq_fixed, cost_multiplier=2.0)
    ew_no = _fixed_monthly(data, classes, cost_model, eq_fixed, cost_multiplier=0.0)
    sixty_forty = _fixed_monthly(
        data,
        classes,
        cost_model,
        {"SPY": 0.60, "IEF": 0.40},
        cost_multiplier=2.0,
    )
    sixty_forty_no = _fixed_monthly(
        data,
        classes,
        cost_model,
        {"SPY": 0.60, "IEF": 0.40},
        cost_multiplier=0.0,
    )
    benchmarks = {
        "SPY_BUY_AND_HOLD": _metric_bundle(spy, spy_no),
        "EQUAL_WEIGHT_UNIVERSE": _metric_bundle(ew, ew_no),
        "60_40_SPY_IEF": _metric_bundle(sixty_forty, sixty_forty_no),
    }

    grid = []
    for lb in strategy_spec["robustness_grid"]["signal_lookback_days"]:
        for vw in strategy_spec["robustness_grid"]["volatility_window_days"]:
            sim = simulate_strategy(
                data,
                classes,
                strategy_spec,
                cost_model,
                cost_multiplier=2.0,
                lookback=int(lb),
                vol_window=int(vw),
            )
            metric = _metric_bundle(sim, no_cost)
            grid.append(
                {
                    "lookback": int(lb),
                    "vol_window": int(vw),
                    "net_cagr_2x": metric["cagr"],
                    "positive": metric["cagr"] > 0,
                }
            )
    positive_fraction = sum(x["positive"] for x in grid) / len(grid)

    boot_cfg = strategy_spec["bootstrap"]
    boot = bootstrap_sharpes(
        simulations["2x"]["daily_returns"],
        seed=int(boot_cfg["seed"]),
        block_length=int(boot_cfg["block_length_trading_days"]),
        replications=int(boot_cfg["replications"]),
    )
    p5 = percentile(boot, 0.05) if boot else None

    return {
        "schema_version": 1,
        "strategy_id": strategy_spec["strategy_id"],
        "data": dict(data_metadata),
        "strategy": strategy,
        "benchmarks": benchmarks,
        "robustness_grid": {
            "variants": grid,
            "positive_fraction_2x": positive_fraction,
        },
        "bootstrap": {
            "seed": int(boot_cfg["seed"]),
            "block_length_trading_days": int(boot_cfg["block_length_trading_days"]),
            "replications": int(boot_cfg["replications"]),
            "sharpe_5th_percentile_2x": p5,
        },
        "stability_windows_2x": _stability_windows(
            simulations["2x"]["dates"], simulations["2x"]["equity"]
        ),
        "execution_audit": {
            "one_day_causal_lag": all(
                date.fromisoformat(x["execution_date"]) > date.fromisoformat(x["decision_date"])
                for x in simulations["2x"]["execution_log"]
            ),
            "executions": simulations["2x"]["execution_log"],
        },
    }
