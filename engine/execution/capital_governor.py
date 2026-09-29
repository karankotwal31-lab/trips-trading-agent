"""Capital Governor — hard account/portfolio financial boundaries.

Separate from per-trade Risk (``engine/risk.py``, frozen). The Governor owns portfolio-level
boundaries; Risk owns per-trade risk. The Governor is layered ABOVE Risk and never replaces it,
so it cannot become a second competing risk authority.

Two rules are structural, not advisory:

1. There are NO built-in financial limits. Every value comes from an explicit owner-approved
   profile. A missing key is an error, never a default.
2. Autonomous mechanisms may only REDUCE. Raising owner-approved authority requires the
   approved configuration-change process and explicit owner authorization.

Profiles are versioned, auditable and integrity-checked against an approved fingerprint, and
are additionally bounded by the frozen ``config_guard.HARD_LIMITS`` ceilings (read, never edited).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Tuple

from .contracts import AutonomousAuthorityIncrease, GovernorError, canonical_json

GOVERNOR_KEYS: Tuple[str, ...] = (
    "max_deployable_capital",
    "max_order_value",
    "max_position_exposure_pct",
    "max_portfolio_exposure_pct",
    "max_concentration_pct",
    "max_daily_loss_pct",
    "max_drawdown_pct",
    "max_positions",
    "max_new_exposure_per_period",
)

# Governor key -> frozen hard-ceiling key. The frozen ceilings are a floor of safety that the
# Governor may tighten but never exceed.
FROZEN_CEILING_MAP: Dict[str, str] = {
    "max_daily_loss_pct": "max_daily_loss_pct",
    "max_portfolio_exposure_pct": "max_total_exposure_pct",
    "max_positions": "max_open_positions",
    "max_drawdown_pct": "halt_on_drawdown_pct",
}

_WHOLE_NUMBER_KEYS = ("max_positions",)

AUTONOMOUS = "AUTONOMOUS"
OWNER = "OWNER"


def fingerprint_profile(profile: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(profile))).hexdigest()


def frozen_hard_limits() -> Dict[str, Any]:
    """Read the frozen hard ceilings. Read-only: this layer must never edit config_guard."""
    from config_guard import HARD_LIMITS

    return dict(HARD_LIMITS)


@dataclass(frozen=True)
class PortfolioSnapshot:
    equity: float
    cash: float
    peak_equity: float
    daily_pnl: float
    deployable_capital: float
    new_exposure_this_period: float
    positions: Mapping[str, float] = field(default_factory=dict)
    quantities: Mapping[str, int] = field(default_factory=dict)
    exposure: float = 0.0
    cooldown_remaining: int = 0
    pending_entries: Mapping[str, Any] = field(default_factory=dict)
    halted: bool = False
    halt_reason: Any = None

    @property
    def drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, 1.0 - float(self.equity) / float(self.peak_equity))

    @classmethod
    def from_runtime_state(cls, state: Mapping[str, Any], *, deployable_capital: float,
                           new_exposure_this_period: float = 0.0) -> "PortfolioSnapshot":
        """Build the snapshot from Trip's authoritative portfolio state.

        ``deployable_capital`` is deliberately required: it is an owner-approved Governor
        boundary, not something derivable from the frozen portfolio state, and defaulting it
        would be inventing a financial limit.
        """
        latest = dict(state.get("last_prices") or {})
        positions_value: Dict[str, float] = {}
        quantities: Dict[str, int] = {}
        exposure = 0.0
        for symbol, position in (state.get("positions") or {}).items():
            price = float(latest.get(symbol, position.get("entry", 0.0)))
            quantity = int(position.get("qty", 0))
            positions_value[symbol] = price * quantity
            quantities[symbol] = quantity
            exposure += price * quantity
        equity = float(state.get("equity", 0.0))
        return cls(
            equity=equity, cash=float(state.get("cash", 0.0)),
            peak_equity=float(state.get("peak_equity", equity)),
            daily_pnl=float(state.get("daily_pnl", 0.0)),
            deployable_capital=float(deployable_capital),
            new_exposure_this_period=float(new_exposure_this_period),
            positions=positions_value, quantities=quantities, exposure=exposure,
            cooldown_remaining=int(state.get("cooldown_remaining", 0)),
            pending_entries=dict(state.get("pending_entries") or {}),
            halted=bool(state.get("halted", False)), halt_reason=state.get("halt_reason"))


@dataclass(frozen=True)
class GovernorDecision:
    allowed: bool
    code: str
    reasons: Tuple[str, ...]
    checks: Tuple[Dict[str, Any], ...]
    profile_version: str
    profile_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "code": self.code,
            "reasons": list(self.reasons),
            "checks": list(self.checks),
            "profile_version": self.profile_version,
            "profile_hash": self.profile_hash,
        }


class CapitalGovernor:
    """Deterministic owner-configured portfolio boundary enforcement."""

    def __init__(self, profile: Mapping[str, Any], *, profile_version: str, approved_profile_hash: str) -> None:
        self._profile = self._validate_profile(profile)
        if not isinstance(profile_version, str) or not profile_version.strip():
            raise GovernorError("profile_version is required")
        if not isinstance(approved_profile_hash, str) or len(approved_profile_hash) != 64:
            raise GovernorError("approved_profile_hash must be a SHA-256 hex digest")
        actual = fingerprint_profile(self._profile)
        if actual != approved_profile_hash:
            raise GovernorError("Capital Governor profile does not match the owner-approved fingerprint")
        self._profile_version = profile_version
        self._profile_hash = actual

    # -- construction / integrity ----------------------------------------

    @staticmethod
    def _validate_profile(profile: Mapping[str, Any]) -> Dict[str, Any]:
        if not isinstance(profile, Mapping):
            raise GovernorError("profile must be a mapping of explicit owner-approved limits")
        missing = [key for key in GOVERNOR_KEYS if key not in profile]
        if missing:
            raise GovernorError(
                "Capital Governor profile is incomplete; financial limits are never defaulted. "
                f"Missing: {sorted(missing)}"
            )
        clean: Dict[str, Any] = {}
        for key in GOVERNOR_KEYS:
            value = profile[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise GovernorError(f"{key} must be numeric")
            value = float(value)
            if value <= 0:
                raise GovernorError(f"{key} must be positive")
            if key in _WHOLE_NUMBER_KEYS and value != int(value):
                raise GovernorError(f"{key} must be a whole number")
            clean[key] = int(value) if key in _WHOLE_NUMBER_KEYS else value

        ceilings = frozen_hard_limits()
        for key, ceiling_key in FROZEN_CEILING_MAP.items():
            ceiling = ceilings.get(ceiling_key)
            if ceiling is None:
                raise GovernorError(f"frozen hard ceiling {ceiling_key!r} is unavailable; refusing to run")
            if clean[key] > ceiling:
                raise GovernorError(
                    f"{key}={clean[key]} exceeds the frozen hard ceiling {ceiling_key}={ceiling}"
                )
        return clean

    @property
    def profile(self) -> Dict[str, Any]:
        return dict(self._profile)

    @property
    def profile_version(self) -> str:
        return self._profile_version

    @property
    def profile_hash(self) -> str:
        return self._profile_hash

    def describe(self) -> Dict[str, Any]:
        return {
            "profile": self.profile,
            "profile_version": self._profile_version,
            "profile_hash": self._profile_hash,
            "frozen_ceilings_applied": dict(FROZEN_CEILING_MAP),
            "note": "Owner-approved boundaries. Autonomous mechanisms may only reduce these.",
        }

    # -- one-way authority ------------------------------------------------

    def apply_reduction(self, new_profile: Mapping[str, Any], *, actor: str) -> Dict[str, Any]:
        """Tighten the profile. An autonomous caller may never loosen it."""
        candidate = self._validate_profile(new_profile)
        if actor == AUTONOMOUS:
            raised = [key for key in GOVERNOR_KEYS if candidate[key] > self._profile[key]]
            if raised:
                raise AutonomousAuthorityIncrease(
                    f"autonomous risk reduction may not increase owner-approved authority: {sorted(raised)}"
                )
        elif actor != OWNER:
            raise GovernorError(f"unknown authority actor {actor!r}")
        previous = self._profile
        self._profile = candidate
        self._profile_version = f"{self._profile_version}+{'autonomous' if actor == AUTONOMOUS else 'owner'}"
        self._profile_hash = fingerprint_profile(candidate)
        return {"actor": actor, "previous": previous, "current": dict(candidate),
                "profile_version": self._profile_version, "profile_hash": self._profile_hash}

    # -- enforcement ------------------------------------------------------

    def evaluate(self, *, intent: Any, portfolio: PortfolioSnapshot, price: float) -> GovernorDecision:
        """Decide whether the Capital Governor permits THIS exposure."""
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not float(price) > 0:
            raise GovernorError("a positive reference price is required to size exposure")
        price = float(price)
        equity = float(portfolio.equity)
        if equity <= 0:
            return self._decision(False, "GOVERNOR_BLOCKED", ["non-positive equity"],
                                  [("equity_positive", False, f"equity={equity}")])

        p = self._profile
        order_value = float(intent.quantity) * price
        held_value = float(portfolio.positions.get(intent.symbol, 0.0))
        resulting_symbol_value = held_value + order_value
        existing_exposure = float(sum(portfolio.positions.values()))
        resulting_exposure = existing_exposure + order_value
        resulting_positions = len(portfolio.positions) + (0 if intent.symbol in portfolio.positions else 1)

        drawdown = 0.0
        if portfolio.peak_equity > 0:
            drawdown = max(0.0, (float(portfolio.peak_equity) - equity) / float(portfolio.peak_equity))
        concentration = (resulting_symbol_value / resulting_exposure) if resulting_exposure > 0 else 0.0
        # Concentration is the share of DEPLOYED capital in one name. In a single-name book that
        # ratio is 1.0 by definition, so the check only constrains a genuinely multi-name book
        # rather than making the first position impossible.
        concentration_constraining = resulting_positions >= 2

        checks: List[Tuple[str, bool, str]] = [
            ("max_order_value", order_value <= p["max_order_value"],
             f"order_value={order_value:.2f} limit={p['max_order_value']:.2f}"),
            ("max_deployable_capital", resulting_exposure <= p["max_deployable_capital"],
             f"deployed_after={resulting_exposure:.2f} limit={p['max_deployable_capital']:.2f}"),
            ("max_position_exposure_pct", resulting_symbol_value <= equity * p["max_position_exposure_pct"],
             f"{intent.symbol}_after={resulting_symbol_value:.2f} cap={equity * p['max_position_exposure_pct']:.2f}"),
            ("max_portfolio_exposure_pct", resulting_exposure <= equity * p["max_portfolio_exposure_pct"],
             f"portfolio_after={resulting_exposure:.2f} cap={equity * p['max_portfolio_exposure_pct']:.2f}"),
            ("max_concentration_pct",
             (not concentration_constraining) or concentration <= p["max_concentration_pct"] + 1e-12,
             f"concentration={concentration:.4f} limit={p['max_concentration_pct']:.4f}"
             + ("" if concentration_constraining else " (single-name book; ratio not constraining)")),
            ("max_positions", resulting_positions <= p["max_positions"],
             f"positions_after={resulting_positions} limit={p['max_positions']}"),
            ("max_daily_loss_pct", float(portfolio.daily_pnl) > -(equity * p["max_daily_loss_pct"]),
             f"daily_pnl={float(portfolio.daily_pnl):.2f}"),
            ("max_drawdown_pct", drawdown < p["max_drawdown_pct"],
             f"drawdown={drawdown:.4f} limit={p['max_drawdown_pct']:.4f}"),
            ("max_new_exposure_per_period",
             float(portfolio.new_exposure_this_period) + order_value <= p["max_new_exposure_per_period"],
             f"new_exposure_after={float(portfolio.new_exposure_this_period) + order_value:.2f} "
             f"limit={p['max_new_exposure_per_period']:.2f}"),
        ]

        failing = [name for name, passed, _ in checks if not passed]
        return self._decision(not failing, "GOVERNOR_PERMITS" if not failing else "GOVERNOR_BLOCKED",
                              failing, checks)

    def _decision(self, allowed: bool, code: str, reasons: List[str],
                  checks: List[Tuple[str, bool, str]]) -> GovernorDecision:
        return GovernorDecision(
            allowed=allowed,
            code=code,
            reasons=tuple(reasons),
            checks=tuple({"name": n, "passed": bool(v), "detail": d} for n, v, d in checks),
            profile_version=self._profile_version,
            profile_hash=self._profile_hash,
        )
