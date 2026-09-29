"""The canonical live execution path, as ordered data - and the rules that keep it in order.

There is exactly one route by which capital becomes a real position:

    LIVE market data -> Truth -> Strategy -> Risk -> Capital Governor -> Constitution
    -> immutable ExecutionIntent -> Execution Authority Gate -> UniversalBrokerGateway
    -> verified LIVE BrokerAdapter -> LIVE broker account -> real broker execution
    -> reconciliation

It is written down here as a tuple rather than left implicit in call order, so that "the order was
respected" is a question with an answer instead of a claim in a comment. :func:`assert_canonical_live_route`
checks the structural properties that actually matter, and the test suite asserts this list matches
the mandated one exactly.

Three properties this module exists to make checkable:

1. **LIVE, throughout.** Market data, broker and account must all be live. There is no paper or
   sandbox stage in the path, and :func:`assert_no_non_live_environment` refuses any environment
   that is not the live one.
2. **The first mutation happens after the gate, and only after LIVE_ENABLED.** The gateway is the
   single point at which an order may be transmitted, and it may only do so holding a
   :class:`~execution.contracts.LiveMutationPermit`. Nothing upstream of it can mutate anything.
3. **Autonomy is bounded by signed owner limits.** After activation, normal valid decisions may
   execute without per-order human approval - that is the point of the system - but strictly
   inside the owner-signed Capital Governor profile. An autonomous component may *reduce* those
   limits and may never raise them. :func:`assert_no_autonomous_authority_increase` is the
   machine-checkable statement of that.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

from .contracts import MUTATION_REQUIRES_STAGE, ExecutionLayerError

#: The mandated canonical live path, in order.
CANONICAL_LIVE_PATH: Tuple[str, ...] = (
    "live_market_data",
    "truth",
    "strategy",
    "risk",
    "capital_governor",
    "constitution",
    "immutable_execution_intent",
    "execution_authority_gate",
    "universal_broker_gateway",
    "verified_live_broker_adapter",
    "live_broker_account",
    "real_broker_execution",
    "reconciliation",
)

#: The single point at which an order may leave this process. Everything before it is read-only.
TRANSMISSION_POINT = "universal_broker_gateway"

#: The only environment any stage of the live route may run in.
LIVE_ROUTE_ENVIRONMENT = "live"

#: Environments that must never appear on the live route. Paper and sandbox are not degraded
#: versions of live trading; they are different systems, and Trip's is live-money-only. A recorded
#: fixture is not a trading environment at all and has no endpoint.
FORBIDDEN_TRADING_ENVIRONMENTS: Tuple[str, ...] = ("paper", "sandbox", "sim", "simulated",
                                                    "backtest", "shadow")

#: Stages that carry portfolio state. A recorded fixture is not one of them.
STATEFUL_STAGES: Tuple[str, ...] = ("real_broker_execution", "reconciliation")


class LivePathViolation(ExecutionLayerError):
    """The live route was composed in a way this system must never execute."""


def path_index(name: str) -> int:
    try:
        return CANONICAL_LIVE_PATH.index(name)
    except ValueError:
        raise LivePathViolation(f"{name!r} is not a stage of the canonical live path") from None


def assert_canonical_live_route(route: Sequence[str] = CANONICAL_LIVE_PATH) -> Dict[str, Any]:
    """Every mandated stage present, in the mandated order, and nothing else inserted."""
    given = tuple(route)
    if given != CANONICAL_LIVE_PATH:
        missing = [stage for stage in CANONICAL_LIVE_PATH if stage not in given]
        extra = [stage for stage in given if stage not in CANONICAL_LIVE_PATH]
        out_of_order = [stage for stage in given if stage in CANONICAL_LIVE_PATH
                        and path_index(stage) != min(
                            (path_index(other) for other in given
                             if other in CANONICAL_LIVE_PATH), default=-1)]
        raise LivePathViolation(
            f"the live route is not canonical: missing={missing} extra={extra} "
            f"out_of_order={out_of_order}")
    return {"canonical": True, "stages": list(given),
            "transmission_point_index": path_index(TRANSMISSION_POINT),
            "first_stateful_stage_index": min(path_index(s) for s in STATEFUL_STAGES)}


def assert_no_non_live_environment(*, market_data_source_kind: str = "",
                                    broker_environment: str = "",
                                    broker_account_environment: str = "") -> Dict[str, Any]:
    """Refuse any live route whose data or broker is not genuinely live.

    A demo feed, a delayed feed, a paper endpoint or a sandbox account is not a degraded version of
    live; it is a different thing, and executing against it would be trading a fiction.
    """
    offenders = []
    for label, value in (("market data source kind", market_data_source_kind),
                         ("broker environment", broker_environment),
                         ("broker account environment", broker_account_environment)):
        if not value:
            continue
        lowered = str(value).strip().lower()
        if lowered in FORBIDDEN_TRADING_ENVIRONMENTS:
            offenders.append(f"{label} {value!r} is not a live trading environment; Trip's is "
                             f"live-money-only and permits no runtime fallback between them")
        elif lowered != LIVE_ROUTE_ENVIRONMENT:
            offenders.append(f"{label} {value!r} is not {LIVE_ROUTE_ENVIRONMENT!r}")
    if offenders:
        raise LivePathViolation("; ".join(offenders))
    return {"canonical": True, "market_data_source_kind": market_data_source_kind or None,
            "broker_environment": broker_environment or None}


def assert_no_forbidden_trading_environment(*environments: Any) -> Dict[str, Any]:
    """Refuse any environment name that names a non-live trading environment.

    Narrower than :func:`assert_no_non_live_environment` on purpose: a broker account document may
    legitimately report a venue segment (``SEC``) or an account status (``ACTIVE``) rather than the
    word ``live``, and refusing those would refuse reality. What must never appear is a name that
    means *not this account, not real money*.
    """
    offenders = [str(value) for value in environments
                 if value and str(value).strip().lower() in FORBIDDEN_TRADING_ENVIRONMENTS]
    if offenders:
        raise LivePathViolation(
            f"{offenders} name a non-live trading environment; Trip's is live-money-only and "
            f"permits no runtime fallback between environments")
    return {"canonical": True, "forbidden_present": offenders}


def mutation_stage() -> str:
    """The only stage at which a brokerage account may be mutated."""
    return MUTATION_REQUIRES_STAGE


#: Capabilities no component may take for itself. These bound autonomy rather than granting it.
AUTONOMOUS_CAPABILITIES: Tuple[str, ...] = (
    "execute a validated decision within the owner-signed governor profile",
    "reduce its own authority at any time",
)


def assert_no_autonomous_authority_increase(profile: Mapping[str, Any],
                                            reduced: Mapping[str, Any],
                                            ceilings: Mapping[str, Any]) -> Dict[str, Any]:
    """Autonomy may only ever tighten. Any ceiling the reduced profile exceeds is a violation.

    Called with the frozen hard ceilings, not the owner's numbers, so an over-generous governor
    profile cannot become the yardstick that measures its own permissiveness.
    """
    violations = []
    for key, ceiling in ceilings.items():
        if key not in profile:
            violations.append(f"{key} is absent from the governor profile")
            continue
        try:
            signed, autonomous = float(profile[key]), float(reduced[key])
        except (TypeError, ValueError):
            violations.append(f"{key} is not numeric")
            continue
        if autonomous > signed:
            violations.append(f"{key} was raised autonomously from {signed} to {autonomous}")
        if float(ceiling) != float(ceiling):  # NaN ceiling is a configuration fault, not a limit
            violations.append(f"{key} ceiling is not a number")
    if violations:
        raise LivePathViolation(
            "no component may autonomously expand owner-approved authority: " + "; ".join(violations))
    return {"canonical": True, "may_only_tighten": True,
            "permitted_autonomy": list(AUTONOMOUS_CAPABILITIES),
            "note": ("After LIVE_ENABLED, normal valid decisions execute autonomously without "
                     "per-order human approval, strictly inside the owner-signed Capital "
                     "Governor profile and the frozen deterministic guardrails.")}


__all__ = [
    "AUTONOMOUS_CAPABILITIES",
    "CANONICAL_LIVE_PATH",
    "FORBIDDEN_TRADING_ENVIRONMENTS",
    "LIVE_ROUTE_ENVIRONMENT",
    "STATEFUL_STAGES",
    "TRANSMISSION_POINT",
    "LivePathViolation",
    "assert_canonical_live_route",
    "assert_no_autonomous_authority_increase",
    "assert_no_forbidden_trading_environment",
    "assert_no_non_live_environment",
    "mutation_stage",
    "path_index",
]
