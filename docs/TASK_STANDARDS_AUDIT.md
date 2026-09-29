# TASK Standards & Controls Audit

**Review date:** 2026-09-30  
**Scope:** Trip's Autonomous Safety Kernel (TASK) and the existing execution boundary.  
**Result:** **CONDITIONAL PASS FOR ENGINEERING / NOT CLEARED FOR LIVE MONEY**

This is an engineering alignment review, not legal advice, an exchange approval, or ISO certification.
Full ISO normative texts are licensed standards; this review uses their public scope descriptions and
does not claim clause-level certification.

## Design conclusion

The design survives the standards review because TASK is outside the learning/strategy loop and has
one-way authority only:

```
strategy / learning -> frozen Risk sizing -> TASK -> immutable ExecutionIntent -> gateway -> broker
```

TASK may preserve quantity, reduce quantity, or block. It cannot increase quantity, change side,
change symbol, alter strategy logic, grant capital authority, change owner limits, or bypass the
existing Capital Governor. A LIVE_ENABLED route without TASK fails closed.

## Baselines reviewed

| Baseline | Relevant expectation | Trip's/TASK disposition |
|---|---|---|
| FIA, *Best Practices for Automated Trading Risk Controls and System Safeguards* (Jul 2024) | max order/position controls, price tolerance, cancel-on-disconnect, kill switches, market-data reasonability, repeated-execution limits, message throttles, self-match prevention, reconciliation, conformance testing | Existing Capital Governor/reconciliation/conformance plus TASK cover the missing execution controls. Venue-provided COD still requires broker/exchange evidence. |
| NFA Interpretive Notice 9046 | written supervision of automated order-routing security/capacity/credit/risk controls; pre-execution commodity/quantity/order limits; automatic blocking | Existing Risk + Capital Governor remain the financial authority; TASK adds execution-condition blocking without duplicating that authority. |
| SEBI circular 13/2025 and later implementation timeline | controlled retail algo/API access, broker/exchange identification and risk/surveillance controls | TASK supports evidence-gated venue requirements. Actual applicability depends on the broker/exchange/account and must be verified before live activation. |
| Upstox Developer API implementation (effective Apr 1 2026) | registered static IP for order APIs; X-Algo-Name for Algo App order tracking; formal registration above the stated order-rate threshold | Represented as required compliance evidence, never hard-coded as fake/assumed facts. No order is eligible if a configured required item is unverified. |
| NIST AI RMF 1.0 | Govern, Map, Measure, Manage continuously across the AI lifecycle; test before deployment and monitor in operation | Safety policy is separated from learning, hash-bound, testable and non-bypassable at live execution. Online learning cannot grant itself execution authority. |
| ISO/IEC 42001:2023 public scope | AI management system, risk/opportunity governance, responsible use, traceability and continual improvement | Architecture aligns at control-design level; no certification claim. |
| ISO/IEC 27001:2022 public scope | information-security risk management; confidentiality, integrity, availability | Existing build/config fingerprints, least-privilege boundaries, owner signatures and fail-closed evidence are aligned controls; no certification claim. |
| NIST SP 800-218 SSDF 1.1 | secure-development practices integrated into the SDLC | TASK code and its adversarial suite are integrity-pinned and go through the repository's single safety gate. |
| ISO 22301:2019 public scope | continuity and recovery from disruption | Broker disconnect, unknown state and recovery must fail closed; venue/broker COD evidence is explicit. No certification claim. |
| OWASP API Security Top 10 (2023) | authentication/authorization, resource controls, secure API consumption | Broker API evidence is explicit; secrets are not placed in strategy logic or TASK policy. |

## Controls that were already strong

- Immutable broker-neutral `ExecutionIntent`.
- Adapter economic-meaning preservation.
- Frozen Truth / Risk / Constitution preflight.
- Capital Governor with no invented financial defaults.
- Autonomous mechanisms may tighten but cannot raise owner authority.
- Durable idempotency and no blind resubmission after unknown broker outcome.
- Broker/local reconciliation.
- Stale/invalid data fail closed.
- Owner Ed25519 trust root and signed owner acts.
- Build/config/test integrity manifests.
- Live-money path remains locked.

## Gaps found before TASK

The review found missing or incomplete execution controls rather than a need to constrain strategy
intelligence:

1. explicit price-tolerance check;
2. local new-order message throttle;
3. repeated automated-execution breaker;
4. self-match prevention at the participant boundary;
5. deterministic ADV/liquidity participation cap;
6. explicit broker disconnect/COD evidence;
7. venue-specific compliance evidence such as static-IP/auth/algo tagging where applicable;
8. derivative First Notice / Last Trade lifecycle protection;
9. a read-only risk-budget interface the autonomous strategy can query before proposing;
10. an explicit rule that live execution may not bypass this safety envelope.

TASK addresses these at the proposal/execution boundary.

## Non-dulling behavior

TASK does **not** reject a sound signal merely because its requested quantity is too large for
current liquidity. It reduces the quantity to the currently executable ADV budget and preserves
the strategy's direction and thesis. Absolute unsafe states (bad data, prohibited venue state,
unverified compliance, excessive spread, self-match risk, lifecycle cutoff, broker disconnect)
block execution.

This is intentionally different from putting hundreds of restrictive rules inside the model.

## Commodity status

Commodity/futures lifecycle controls now exist as generic safety capability, including physical
settlement, First Notice Date and Last Trade Date evidence. **Commodity execution is not enabled.**
The current validated production scope remains unchanged. Widening symbol/instrument scope requires
separate exchange metadata, broker support, conformance evidence, owner-approved TASK parameters,
and the existing reviewed scope-change process.

## Owner inputs still required before activation

TASK deliberately contains no production numeric defaults. Before it can protect a live route, an
owner/venue-approved policy must explicitly define:

- maximum price deviation;
- maximum spread;
- maximum ADV participation;
- new-order message rate;
- repeated-execution count/window;
- First Notice and Last Trade buffers;
- permitted order types and venue states;
- whether cancel-on-disconnect and self-match prevention are mandatory;
- which venue/account compliance evidence items are required.

These values must be fingerprinted and approved. Missing values do not silently default.

## Sources

- SEBI, *Safer participation of retail investors in Algorithmic trading*, Circular
  SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013:
  https://www.sebi.gov.in/legal/circulars/feb-2025/safer-participation-of-retail-investors-in-algorithmic-trading_91614.html
- SEBI, Sep 30 2025 implementation-timeline circular:
  https://www.sebi.gov.in/legal/circulars/sep-2025/extension-of-timeline-for-implementation-of-sebi-circular-dated-february-04-2025-on-safer-participation-of-retail-investors-in-algorithmic-trading-_96979.html
- FIA, *Best Practices for Automated Trading Risk Controls and System Safeguards*, Jul 2024:
  https://www.fia.org/sites/default/files/2024-07/FIA_WP_AUTOMATED%20TRADING%20RISK%20CONTROLS_FINAL_0.pdf
- NFA Interpretive Notice 9046:
  https://www.nfa.futures.org/rulebooksql/rules.aspx?RuleID=9046&Section=9
- Upstox Developer API, Algo Registration & Static IP Requirement:
  https://upstox.com/developer/api-documentation/announcements/algo-trading-circular/
- NIST AI RMF:
  https://airc.nist.gov/airmf-resources/airmf/5-sec-core/
- NIST SP 800-218 SSDF 1.1:
  https://csrc.nist.gov/pubs/sp/800/218/final
- ISO/IEC 42001:2023:
  https://www.iso.org/standard/42001
- ISO/IEC 27001:2022:
  https://www.iso.org/standard/27001
- ISO 22301:2019:
  https://www.iso.org/standard/75106.html
- OWASP API Security Top 10 2023:
  https://api-security.owasp.org/editions/2023/en/0x11-t10/
