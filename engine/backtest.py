from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

from providers import DemoProvider
from strategies import consensus, evaluate, features

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((Path(__file__).parent / "config.json").read_text())


def max_drawdown(curve):
    peak = curve[0] if curve else 1.0
    mdd = 0.0
    for x in curve:
        peak = max(peak, x)
        if peak > 0:
            mdd = max(mdd, 1.0 - x / peak)
    return mdd


def _update_protective_stop(position, bar):
    """Mirror forge_agent management: close-derived stops apply only to later bars."""
    initial_r = position["entry"] - position["initial_stop"]
    if initial_r <= 0:
        return position
    favorable_r = (bar.close - position["entry"]) / initial_r
    if favorable_r >= CFG["trade"]["break_even_after_r"]:
        position["stop"] = max(position["stop"], position["entry"])
        position["protected"] = True
    if favorable_r >= CFG["trade"]["trail_after_r"]:
        position["stop"] = max(
            position["stop"],
            bar.close - CFG["trade"]["trail_atr_multiple"] * position["atr_at_entry"])
        position["trailing"] = True
    return position


def simulate(symbol, bars, cost_bps=5.0, signal_threshold=0.72):
    """Causal bar-close backtest: signal at close i, earliest fill at open i+1."""
    equity = 100000.0
    curve = [equity]
    position = None
    pending = None
    trades = []
    fills = []

    for i in range(60, len(bars)):
        bar = bars[i]

        # A prior-close signal may fill only at this bar's open.
        if pending is not None and position is None:
            entry = bar.open * (1 + cost_bps / 10000)
            stop = entry - CFG["trade"]["atr_stop_multiple"] * pending["atr"]
            risk = entry - stop
            if risk > 0:
                qty = int((equity * CFG["risk"]["max_risk_per_trade_pct"]) / risk)
                qty = min(qty, int((equity * CFG["risk"]["max_total_exposure_pct"]) / entry))
                if qty > 0:
                    position = {
                        "entry": entry, "initial_stop": stop, "stop": stop,
                        "target": entry + CFG["trade"]["target_r_multiple"] * risk, "qty": qty,
                        "atr_at_entry": pending["atr"], "protected": False, "trailing": False,
                    }
                    fills.append({"signal_i": pending["signal_i"], "fill_i": i})
            pending = None

        # If a position was open at this bar's open, this entire bar can affect it.
        if position:
            raw_exit = None
            if bar.open <= position["stop"]:
                raw_exit = bar.open
            elif bar.open >= position["target"]:
                raw_exit = position["target"]
            else:
                stop_hit = bar.low <= position["stop"]
                target_hit = bar.high >= position["target"]
                if stop_hit and target_hit:
                    raw_exit = position["stop"]  # OHLC order is unknowable; resolve against strategy.
                elif stop_hit:
                    raw_exit = position["stop"]
                elif target_hit:
                    raw_exit = position["target"]
            if raw_exit is not None:
                exit_px = raw_exit * (1 - cost_bps / 10000)
                pnl = (exit_px - position["entry"]) * position["qty"]
                equity += pnl
                trades.append(pnl)
                position = None
            else:
                # Identical causal ordering to forge_agent: this bar first tests the OLD stop and
                # target; a break-even/trailing adjustment derived from this close affects only
                # subsequent bars.
                _update_protective_stop(position, bar)

        # Create a signal only after this bar has closed. It cannot fill on this bar.
        if position is None and pending is None:
            hist = bars[:i + 1]
            f = features(hist)
            c = consensus(evaluate(f))
            if c["direction"] == "LONG" and c["signal_score"] >= signal_threshold and not c["conflict"]:
                pending = {"signal_i": i, "atr": f.atr14}

        mark = equity
        if position:
            mark += (bar.close - position["entry"]) * position["qty"]
        curve.append(mark)

    wins = [x for x in trades if x > 0]
    losses = [x for x in trades if x < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "symbol": symbol, "cost_bps": cost_bps, "signal_threshold": signal_threshold,
        "trades": len(trades), "win_rate": (len(wins) / len(trades) if trades else 0),
        "expectancy": (mean(trades) if trades else 0), "profit_factor": (gross_win / gross_loss if gross_loss else None),
        "net_pnl": sum(trades), "max_drawdown_pct": max_drawdown(curve),
        "small_sample_warning": len(trades) < 30,
        "causality_check": all(x["fill_i"] == x["signal_i"] + 1 for x in fills),
    }


def run():
    provider = DemoProvider()
    reports = []
    for symbol in CFG["symbols"]:
        bars = provider.bars(symbol, 600)
        for cost in (0, 5, 10, 20, 40):
            for threshold in (0.67, 0.72, 0.77):
                reports.append(simulate(symbol, bars, cost, threshold))
    out = {
        "notice": "Synthetic robustness test only; never evidence of live-market profitability.",
        "tests": reports,
        "all_causal": all(x["causality_check"] for x in reports),
        "all_small_sample": all(x["small_sample_warning"] for x in reports),
        "worst_net_pnl": min(x["net_pnl"] for x in reports),
        "best_net_pnl": max(x["net_pnl"] for x in reports),
        "worst_drawdown_pct": max(x["max_drawdown_pct"] for x in reports),
    }
    out_path = ROOT / "runtime_data" / "backtest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))
    print(json.dumps({k: v for k, v in out.items() if k != "tests"}, indent=2))


if __name__ == "__main__":
    run()
