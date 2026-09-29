"""Trip's additive execution layer.

READ-ONLY WITH RESPECT TO THE FROZEN CORE
-----------------------------------------
Every file pinned by ``infra/core_v06.sha256`` is used here strictly as an input:

* the approved symbol scope comes from ``config_guard.VALIDATED_SYMBOLS``,
* the hard financial ceilings come from ``config_guard.HARD_LIMITS``,
* the Constitution rules come from ``constitution.NON_NEGOTIABLES``,
* the frozen core digest comes from ``infra/core_v06.sha256``.

None of them is edited, wrapped, monkeypatched or duplicated into a second authority. This
package is therefore additive: it does not move a fingerprint, does not change
``approved_build.json`` and does not alter ``config.json``.

WHAT IT PROVES
--------------
The layer implements the full authority pipeline — capability contract, capital governor,
two-permission gate, broker gateway, durable idempotency, reconciliation, one-way supervisor
halt — and then refuses to release capital because the frozen Constitution still forbids
live-money order submission. It terminates at ``LIVE_LOCKED`` by construction, not by omission.
"""

from __future__ import annotations

import sys
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from . import durable_store  # noqa: E402
from .capital_governor import (  # noqa: E402
    AUTONOMOUS,
    FROZEN_CEILING_MAP,
    GOVERNOR_KEYS,
    OWNER,
    CapitalGovernor,
    GovernorDecision,
    PortfolioSnapshot,
    fingerprint_profile,
    frozen_hard_limits,
)
from .contracts import (  # noqa: E402
    APPROVED_BAR_INTERVAL,
    APPROVED_INSTRUMENT_SCOPE,
    CANONICAL_STATE_MODEL_STATUS,
    CAPABILITY_UNSUPPORTED,
    CORE_CAPABILITIES,
    MAX_EVIDENCE_BYTES,
    ORDER_INCOMPATIBLE,
    OPTIONAL_CAPABILITIES,
    SUPPORTED,
    OrderCapabilities,
    AutonomousAuthorityIncrease,
    BrokerAccount,
    BrokerAdapter,
    BrokerAutomationUnsupported,
    BrokerHealth,
    BrokerPosition,
    BrokerTransport,
    CapabilityError,
    CapabilityMatrix,
    CapabilityStatus,
    ExecutionLayerError,
    ExecutionState,
    GovernorError,
    IllegalStateTransition,
    IntentError,
    IntentExpired,
    LedgerError,
    TransportKind,
    approved_symbol_scope,
    assert_preserves_economic_meaning,
    assert_transition_allowed,
    aware_timestamp,
    canonical_json,
    check_representable,
    decision_bar_close_time,
    decision_bar_is_closed,
)
from .adapters import (  # noqa: E402
    AlpacaAdapter,
    BrokerChannel,
    BrokerContractError,
    UpstoxAdapter,
)
from .amendment import (  # noqa: E402
    AMENDABLE_ADD_RULES,
    AMENDABLE_REMOVE_RULES,
    AMENDMENT_APPLICABLE,
    AMENDMENT_ARTIFACTS,
    APPLICATION_REFUSED,
    AmendmentApplicationRefused,
    AmendmentError,
    AmendmentProposal,
    CoreStateBasis,
    amended_rule_ids,
    apply_amendment,
    blockers_to_live_release,
    frozen_core_basis,
    live_release_requirements,
    release_basis_from_verdict,
    verify_amendment,
)
from .cycle_bridge import (  # noqa: E402
    JOURNAL_FILE,
    CycleDecision,
    CycleRouter,
    IntentPolicy,
    RouteResult,
    decisions_from_frozen_runtime,
    describe_frozen_runtime_decisions,
    route_frozen_cycle,
)
from .gate import (  # noqa: E402
    AUTHORIZED,
    BLOCKED,
    CAPITAL_RELEASE_FIELDS,
    FROZEN_PROHIBITION_RULE_IDS,
    LIVE_AUTHORIZATION_RULE_IDS,
    TRADE_VALID_FIELDS,
    AuthorityGate,
    FrozenLiveBoundary,
    GateDecision,
    LiveBoundaryVerdict,
    Permission,
    capital_release,
    frozen_config_guard_permits,
    frozen_config_guard_permitted_modes,
    frozen_config_mode,
    frozen_constitution_rule_ids,
    frozen_modes,
    frozen_permitted_modes,
    frozen_risk_permits,
    frozen_risk_permitted_modes,
    trade_valid,
    verify_frozen_core_digest,
)
from .gateway import (  # noqa: E402
    LEDGER_FILE,
    IdempotencyLedger,
    SubmissionOutcome,
    SubmissionResult,
    UniversalBrokerGateway,
    client_order_id_for,
)
from .identity import (  # noqa: E402
    approved_build_hash,
    approved_config_hash,
    authorization_drift,
    current_identity,
)
from .intent import ORDER_TYPES, SIDES, TIME_IN_FORCE, ExecutionIntent  # noqa: E402
from .lifecycle import (  # noqa: E402
    ALLOWED_TRANSITIONS,
    CORE_STATE_CALLER_ASSERTED_REFUSED,
    CORE_STATE_SOURCES,
    DEFAULT_STAGE,
    LIVE_LOCKED_REFUSAL,
    OWNER_BLOCKING_ITEMS,
    Actor,
    Lifecycle,
    LiveAuthorization,
    Stage,
)
from .exchange_calendar import (  # noqa: E402
    calendar_for_years,
    default_us_equity_calendar,
    early_closes,
    market_holidays,
)
from .owner_authority import (  # noqa: E402
    ALGORITHM as OWNER_SIGNATURE_ALGORITHM,
    PURPOSE_AMENDMENT,
    PURPOSE_LIVE_AUTHORIZATION,
    SIGNATURE_VERSION as OWNER_SIGNATURE_VERSION,
    OwnerAuthorityError,
    key_id_for,
    load_public_key,
    owner_authority_status,
    signed_message,
    verify_owner_signature,
)
from .preflight import (  # noqa: E402
    ANOMALY_CHECKS,
    PRECONDITIONS,
    HealthGateEvidence,
    LiveEnvironmentAttestation,
    PreflightEvaluator,
    PreflightReport,
)
from .provenance import (  # noqa: E402
    DataPurpose,
    DataSourceGuard,
    DataSourceRecord,
    ProvenanceViolation,
)
from .reconciliation import Discrepancy, ReconciliationEngine, ReconciliationResult  # noqa: E402
from .registry import (  # noqa: E402
    AUTHORIZED_TRANSPORTS,
    FORBIDDEN_AUTOMATION_TECHNIQUES,
    NON_EXECUTING_CLASSIFICATIONS,
    AdapterRegistry,
    BrokerRegistration,
    PluginClassification,
    UnauthorizedInterface,
    assert_not_natural_language_evidence,
)
from .session import (  # noqa: E402
    SessionCalendar,
    SessionCalendarError,
    SessionStatus,
    SessionVerdict,
)
from .supervisor import (  # noqa: E402
    EVIDENCE_ALLOWLIST,
    FORBIDDEN_OUTPUT_FIELDS,
    SUPERVISOR_UNAVAILABLE,
    RuleBasedSupervisorProvider,
    SafetyController,
    SanitizedEvidencePacket,
    SupervisorAuthorityViolation,
    SupervisorFinding,
    SupervisorOutcome,
    SupervisorPolicy,
    SupervisorProvider,
    SupervisorRunner,
    UnavailableSupervisorProvider,
    availability_outcome,
    interpret_supervisor_output,
)

__all__ = [
    "AUTHORIZED", "ALLOWED_TRANSITIONS", "APPROVED_BAR_INTERVAL", "APPROVED_INSTRUMENT_SCOPE",
    "AUTONOMOUS", "Actor", "AuthorityGate", "AutonomousAuthorityIncrease", "BLOCKED",
    "BrokerAccount", "BrokerAdapter", "BrokerAutomationUnsupported", "BrokerHealth",
    "BrokerPosition", "BrokerTransport", "CANONICAL_STATE_MODEL_STATUS", "CAPABILITY_UNSUPPORTED",
    "CAPITAL_RELEASE_FIELDS", "CORE_CAPABILITIES", "CapabilityError", "CapabilityMatrix",
    "CapabilityStatus", "OrderCapabilities", "ORDER_INCOMPATIBLE", "SUPPORTED",
    "RuleBasedSupervisorProvider", "SupervisorPolicy", "SupervisorProvider", "SupervisorRunner",
    "UnavailableSupervisorProvider", "aware_timestamp", "check_representable",
    "decision_bar_close_time", "decision_bar_is_closed",
    "AMENDABLE_ADD_RULES", "AMENDABLE_REMOVE_RULES", "AMENDMENT_APPLICABLE",
    "AMENDMENT_ARTIFACTS", "APPLICATION_REFUSED", "AmendmentApplicationRefused",
    "AmendmentError", "AmendmentProposal", "CoreStateBasis", "amended_rule_ids",
    "apply_amendment", "blockers_to_live_release", "frozen_core_basis",
    "live_release_requirements", "release_basis_from_verdict", "verify_amendment",
    "frozen_config_guard_permits", "frozen_permitted_modes", "frozen_config_guard_permitted_modes",
    "frozen_modes", "frozen_risk_permits", "frozen_risk_permitted_modes",
    "JOURNAL_FILE", "CycleDecision", "CycleRouter", "IntentPolicy", "RouteResult",
    "decisions_from_frozen_runtime", "describe_frozen_runtime_decisions", "route_frozen_cycle",
    "CORE_STATE_CALLER_ASSERTED_REFUSED", "CORE_STATE_SOURCES", "OWNER_BLOCKING_ITEMS",
    "CapitalGovernor", "DEFAULT_STAGE", "Discrepancy", "EVIDENCE_ALLOWLIST", "ENGINE_DIR",
    "ExecutionIntent", "ExecutionLayerError", "ExecutionState", "FORBIDDEN_OUTPUT_FIELDS",
    "FROZEN_CEILING_MAP", "FROZEN_PROHIBITION_RULE_IDS", "FrozenLiveBoundary", "GOVERNOR_KEYS",
    "GateDecision", "GovernorDecision", "GovernorError", "IdempotencyLedger",
    "IllegalStateTransition", "IntentError", "IntentExpired", "LEDGER_FILE",
    "ANOMALY_CHECKS", "AUTHORIZED_TRANSPORTS", "AdapterRegistry", "BrokerRegistration",
    "DataPurpose", "DataSourceGuard", "DataSourceRecord", "FORBIDDEN_AUTOMATION_TECHNIQUES",
    "AlpacaAdapter", "BrokerChannel", "BrokerContractError", "UpstoxAdapter",
    "calendar_for_years", "default_us_equity_calendar", "early_closes", "market_holidays",
    "HealthGateEvidence", "LiveEnvironmentAttestation", "NON_EXECUTING_CLASSIFICATIONS",
    "PRECONDITIONS", "PluginClassification", "PreflightEvaluator", "PreflightReport",
    "ProvenanceViolation", "SessionCalendar", "SessionCalendarError", "SessionStatus",
    "SessionVerdict", "UnauthorizedInterface", "approved_build_hash", "approved_config_hash",
    "assert_not_natural_language_evidence", "authorization_drift", "current_identity",
    "LIVE_AUTHORIZATION_RULE_IDS", "LIVE_LOCKED_REFUSAL", "LedgerError", "Lifecycle",
    "LiveAuthorization", "LiveBoundaryVerdict", "MAX_EVIDENCE_BYTES", "OPTIONAL_CAPABILITIES",
    "ORDER_TYPES", "OWNER", "Permission", "PortfolioSnapshot", "ReconciliationEngine",
    "ReconciliationResult", "SIDES", "SUPERVISOR_UNAVAILABLE", "SafetyController",
    "SanitizedEvidencePacket", "Stage", "SubmissionOutcome", "SubmissionResult",
    "SupervisorAuthorityViolation", "SupervisorFinding", "SupervisorOutcome", "TIME_IN_FORCE",
    "TRADE_VALID_FIELDS", "TransportKind", "UniversalBrokerGateway", "approved_symbol_scope",
    "assert_preserves_economic_meaning", "assert_transition_allowed", "availability_outcome",
    "canonical_json", "capital_release", "client_order_id_for", "durable_store",
    "fingerprint_profile", "frozen_config_mode", "frozen_constitution_rule_ids",
    "frozen_hard_limits", "interpret_supervisor_output", "trade_valid",
    "verify_frozen_core_digest",
    "OWNER_SIGNATURE_ALGORITHM", "OWNER_SIGNATURE_VERSION", "PURPOSE_AMENDMENT",
    "PURPOSE_LIVE_AUTHORIZATION", "OwnerAuthorityError", "key_id_for", "load_public_key",
    "owner_authority_status", "signed_message", "verify_owner_signature", "ed25519",
]
