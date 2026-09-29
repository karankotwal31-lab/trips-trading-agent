"""Read-only status for the additive execution layer.

Run with:  PYTHONPATH=engine python engine/execution/status.py

Reports the deterministic reason this layer cannot release live capital, plus what the frozen
Truth Engine actually says about the currently approved data configuration. It performs no
mutation, opens no broker connection and requires no credentials.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "engine") not in sys.path:
    sys.path.insert(0, str(ROOT / "engine"))

from config_guard import fingerprint_config, validate_config  # noqa: E402
from market_time import closed_bars_only  # noqa: E402
from providers import DemoProvider  # noqa: E402
from truth_guard import validate_bars  # noqa: E402

from execution import (  # noqa: E402
    AMENDABLE_ADD_RULES,
    AMENDABLE_REMOVE_RULES,
    AMENDMENT_ARTIFACTS,
    CORE_CAPABILITIES,
    CANONICAL_STATE_MODEL_STATUS,
    EVIDENCE_ALLOWLIST,
    FORBIDDEN_AUTOMATION_TECHNIQUES,
    FROZEN_CEILING_MAP,
    PRECONDITIONS,
    AdapterRegistry,
    FrozenLiveBoundary,
    Lifecycle,
    OrderCapabilities,
    SanitizedEvidencePacket,
    SessionCalendar,
    SupervisorPolicy,
    SupervisorRunner,
    approved_symbol_scope,
    check_representable,
    describe_frozen_runtime_decisions,
    frozen_config_guard_permits,
    frozen_config_mode,
    frozen_constitution_rule_ids,
    frozen_hard_limits,
    frozen_permitted_modes,
    live_release_requirements,
    owner_authority_status,
    verify_frozen_core_digest,
)


def _approved_config() -> dict:
    return validate_config(json.loads((ROOT / "engine" / "config.json").read_text()))


def _truth_report(cfg: dict) -> dict:
    """Run the frozen Truth Engine over the configured provider. Demonstration, not a cycle."""
    try:
        provider = DemoProvider() if cfg.get("provider") == "demo" else None
        if provider is None:
            return {"evaluated": False, "reason": "configured provider is not available offline"}
        raw = provider.bars(cfg["symbols"][0], 240)
        bars = closed_bars_only(raw, cfg["bar_interval"],
                                close_lag_seconds=cfg["truth"]["bar_close_lag_seconds"])
        verdict = validate_bars(
            source=provider.identity.name, source_family=provider.identity.source_family,
            source_kind=cfg["provider_source_kind"], symbol=cfg["symbols"][0],
            interval=cfg["bar_interval"], bars=bars,
            max_age_minutes=cfg["risk"]["max_data_age_minutes"],
            min_bars=cfg["forge"]["require_history_bars"],
            allow_synthetic_analysis=cfg["truth"]["allow_synthetic_analysis"],
            fixed_source_kind=provider.identity.fixed_source_kind,
            realtime_request_attested=False)
        return {"evaluated": True, "symbol": cfg["symbols"][0], "closed_bars": len(bars),
                "trusted_for_analysis": verdict.trusted_for_analysis,
                "trusted_for_trade": verdict.trusted_for_trade,
                "reasons": verdict.reasons[:6]}
    except Exception as exc:
        return {"evaluated": False, "reason": f"{type(exc).__name__}: {exc}"[:200]}


def _suite_integrity() -> dict:
    sys.path.insert(0, str(ROOT / "tests"))
    try:
        from suite_integrity import SuiteIntegrityError, verify

        return {"ok": True, "manifest_hash": verify()["manifest_hash"]}
    except SuiteIntegrityError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}


def _cycle_bridge_summary() -> dict:
    """Show that frozen-cycle decisions can be extracted and routed, without a real cycle."""
    import datetime as _dt

    from execution import CycleDecision

    sample = CycleDecision(symbol="SPY", side="BUY", strategy="sample",
                           signal_bar_ts=_dt.datetime.now(_dt.timezone.utc).isoformat(),
                           atr_at_signal=1.5, data_integrity_hash="sample", source="sample",
                           decision_price=100.0)
    runtime = {"portfolio": {"pending_entries": {"SPY": {
        "signal_bar_ts": sample.signal_bar_ts, "strategy": "sample", "atr_at_signal": 1.5,
        "data_integrity_hash": "sample", "source": "sample"}}}, "ledger": []}
    extracted = describe_frozen_runtime_decisions(runtime, prices={"SPY": 100.0})
    return {
        "wired": True,
        "reads": "the frozen cycle's authoritative pending-entry decision record",
        "creates_a_second_strategy_record": False,
        "sizes_from": "the frozen risk.position_size result reported by preflight",
        "default_intent_policy": {"order_type": "MARKET", "time_in_force": "DAY",
                                  "note": "mirrors the frozen 'fill at next bar open' paper policy"},
        "extraction_smoke_test_decision_id": extracted["decisions"][0]["decision_id"] if extracted["decisions"] else None,
        "journal_file": "runtime_data/execution/cycle_route_journal.json",
    }


def _supervisor_summary() -> dict:
    """Exercise the supervision pipeline end to end with the deterministic provider."""
    from execution.supervisor import RuleBasedSupervisorProvider

    packet = SanitizedEvidencePacket(fields={}, generated_at="1970-01-01T00:00:00+00:00")
    runner = SupervisorRunner(provider=RuleBasedSupervisorProvider())
    outcome = runner.run(packet)
    return {
        "provider": runner.provider_id,
        "deterministic_network_free_provider": True,
        "ai_is_a_required_hop_for_an_order": False,
        "evidence_allowlist": list(EVIDENCE_ALLOWLIST),
        "policy": SupervisorPolicy().describe(),
        "empty_evidence_finding": outcome.finding.value,
        "execution_authority": outcome.execution_authority,
        "may_only_tighten": outcome.may_only_tighten,
        "note": ("Findings are limited to NORMAL/CAUTION/INVESTIGATE/SUPERVISOR_HALT_REQUEST. "
                 "A SUPERVISOR_HALT_REQUEST can only block new exposure. Absence of supervision "
                 "is recorded as SUPERVISOR_UNAVAILABLE and never read as approval."),
    }


def build_status() -> dict:
    cfg = _approved_config()
    boundary = FrozenLiveBoundary()
    lifecycle = Lifecycle()
    verdict = boundary.evaluate(mode=frozen_config_mode())
    rule_ids = frozen_constitution_rule_ids()
    registry = AdapterRegistry()
    return {
        "schema_version": 2,
        "layer": "Trip's additive execution layer",
        "modified_frozen_core": False,
        "frozen_core_digest_verified": verify_frozen_core_digest()["verified"],
        "approved_config_fingerprint": fingerprint_config(cfg),
        "constitution_rule_count": len(rule_ids),
        "constitution_contains_paper_first": "PAPER_FIRST" in rule_ids,
        "constitution_contains_live_gate": "LIVE_GATE" in rule_ids,
        "frozen_config_mode": frozen_config_mode(),
        "lifecycle_stage": lifecycle.stage.value,
        "live_transmission": lifecycle.may_transmit_live(),
        "boundary_verdict": verdict.to_dict(),
        "core_state_source": lifecycle.core_state_basis().source,
        "frozen_config_guard_permitted_modes": list(frozen_permitted_modes()),
        "frozen_config_guard_live_probe": frozen_config_guard_permits("live"),
        "live_release_requirements": live_release_requirements(boundary=boundary),
        "frozen_cycle_bridge": _cycle_bridge_summary(),
        "approved_symbol_scope": sorted(approved_symbol_scope()),
        "frozen_hard_ceilings": {key: frozen_hard_limits()[key]
                                 for key in sorted(frozen_hard_limits())
                                 if key in set(FROZEN_CEILING_MAP.values())},
        "governor_ceiling_map": dict(FROZEN_CEILING_MAP),
        "capital_governor_profile": {
            "configured": False,
            "detail": ("No owner-approved Capital Governor profile exists in this repository. "
                       "Financial limits are never defaulted, so preflight cannot be constructed "
                       "until the owner supplies one."),
        },
        "preflight": {
            "evaluable": False,
            "reason": "a Capital Governor profile is required, and it is an owner input",
            "precondition_count": len(PRECONDITIONS),
            "preconditions": list(PRECONDITIONS),
        },
        "frozen_truth_chain": _truth_report(cfg),
        "session_truth": SessionCalendar().evaluate(__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc)).to_dict(),
        "broker_registry": registry.summarize(),
        "broker_adapters_shipped": len(registry.discover()),
        "core_capabilities_required_of_an_adapter": list(CORE_CAPABILITIES),
        "order_representability_contract": {
            "undeclared_adapter_verdict": check_representable(
                __import__("types").SimpleNamespace(side="BUY", order_type="LIMIT",
                                                   time_in_force="DAY"),
                OrderCapabilities())["code"],
            "note": ("An adapter must declare which order types, times-in-force and sides it can "
                     "express. Unknown is not supported: a non-SUPPORTED verdict yields "
                     "ORDER_INCOMPATIBLE or CAPABILITY_UNSUPPORTED and therefore no trade."),
        },
        "forbidden_automation_techniques": list(FORBIDDEN_AUTOMATION_TECHNIQUES),
        "supervisor": _supervisor_summary(),
        "canonical_state_model": CANONICAL_STATE_MODEL_STATUS,
        "amendment_mechanism": {
            "implemented": True,
            "adds_rules": list(AMENDABLE_ADD_RULES),
            "removes_rules": list(AMENDABLE_REMOVE_RULES),
            "owner_signature_required": True,
            "owner_signature_scheme": owner_authority_status()["algorithm"],
            "owner_authority": owner_authority_status(),
            "self_applies": False,
            "artifacts_requiring_owner_review_and_reapproval": list(AMENDMENT_ARTIFACTS),
            "note": ("A verified, owner-signed amendment opens the gate using the PRODUCTION "
                     "FrozenLiveBoundary, and a verified release basis is the only way to release "
                     "capital without the frozen files changing. Authority is an HMAC tag made with "
                     "the owner key, never a boolean: a release basis without a valid signed live "
                     "authorization is refused, so owner decision A cannot imply decision B. "
                     "Applying the amendment is refused here because editing hash-pinned frozen "
                     "files and re-freezing the core manifest is an owner act."),
        },
        "safety_suite_integrity": _suite_integrity(),
        "note": (
            "The authority pipeline (frozen Truth/Risk/Constitution gates driven through preflight, "
            "capability contract, adapter registry, session truth, identity-drift detection, "
            "capital governor, two-permission gate, gateway, durable idempotency, reconciliation, "
            "one-way supervisor halt) is implemented and tested, and the frozen paper cycle's "
            "decisions are routed through it. Live capital release is refused by TWO independent "
            "frozen facts: the Constitution's PAPER_FIRST rule and the frozen config_guard's "
            "paper-only mode restriction. The owner-gated amendment mechanism that would lift both "
            "is implemented and demonstrated. No real broker adapter ships: where no authorized "
            "programmable interface exists the answer is BROKER_AUTOMATION_UNSUPPORTED."
        ),
    }


if __name__ == "__main__":
    print(json.dumps(build_status(), indent=2))
