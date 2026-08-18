# Prospective Protocol Freeze Checklist

- **Effective date:** `2026-08-18`
- **Scope:** Research, measurement, compatibility, operational, and safety protocols
- **Application:** Prospective protocols and new protocol versions only

## Purpose

Before a protocol is frozen or allowed to contact its target system, verify that its decision rule
can answer the intended question over a plausible range of outcomes. This applies even when the
protocol is a compatibility or operational gate rather than a statistical estimator.

This checklist is prospective governance. It does not authorize an amendment to a frozen record,
reinterpret an observed result, grant network access, or relax any existing gate.

## Required freeze record

Every new protocol or protocol version must record:

1. **Decision and scope.** State the exact question, unit of observation, target system or
   population, allowed inputs, outputs, and actions.
2. **Outcome semantics.** Define every retained, rejected, missing, invalid, and terminal outcome,
   including whether an outcome contributes to an estimand, an operational verdict, or both.
3. **Acceptance rule.** Express each pass, fail, block, escalation, and inconclusive condition in
   executable or mathematically unambiguous terms.
4. **Feasibility envelope.** Identify the parameter or system-behavior range in which the rule can
   pass. For a stochastic rule, report the pass-probability function or a justified simulation;
   for a deterministic rule, report the reachable passing set.
5. **Plausibility overlap.** State, using only information admissible before the freeze, why the
   feasible passing range overlaps behavior that is plausible for the target. Name assumptions
   such as independence, stationarity, exchangeability, rate stability, or fault persistence.
6. **Joint-rule feasibility.** Evaluate the complete conjunction of binding criteria, not only
   each criterion in isolation. Include coverage, missingness, multiplicity, cost, risk, latency,
   reliability, and concentration rules when applicable.
7. **Failure meaning.** State what a failure establishes. Distinguish evidence about the target
   from evidence that the protocol and target interface are incompatible.
8. **Sensitivity.** Show how the pass probability or reachable set changes near the decision
   boundary and under credible adverse conditions.
9. **External-dependency timebox.** For any vendor, data-provider, reviewer, or operator
   dependency without a binding service level, set a decision date and a no-useful-response branch.
   The branch must not create automatic authority for network contact or another material action.
10. **Change control.** Set the last moment at which amendment is allowed, define what information
    closes that window, require preservation of superseded records, and state when a new protocol
    version is mandatory.
11. **Authority boundary.** Cite the identifier-bearing authorization for every external read,
    write, schedule, credential, order, capital use, or other state-changing action. Absence of an
    authorization is a prohibition.
12. **Evidence identity.** Bind protocol version, source identity, timestamps, code revision,
    configuration, hashes, redaction rules, and the treatment of incomplete or failed runs.

## Feasibility decision

Before freezing, choose exactly one disposition:

- **Feasible:** The passing range has a documented, nontrivial overlap with plausible target
  behavior, and the protocol can distinguish the decisions it claims to make.
- **Compatibility stress:** The rule is intentionally severe; a failure will establish a defined
  incompatibility rather than be interpreted as a neutral or strategy-level result.
- **Descriptive only:** The protocol characterizes behavior and has no pass/fail or promotion
  authority.
- **Redesign:** The passing range has negligible overlap with plausible behavior, the joint rule
  lacks useful power, or failure cannot be interpreted. Do not freeze or execute it.

A protocol must not be labeled feasible solely because each component rule can pass separately.
If feasibility depends on a model, report the model and its limitations next to the conclusion.

## Minimal examples

For a zero-event rule over `n` independent observations with event rate `p`, record:

```text
P(pass | p) = (1 - p)^n
```

For a multi-criterion strategy study, estimate the probability of satisfying the full joint rule
over prospectively defined effect sizes and nuisance conditions. A high marginal pass rate for one
criterion does not compensate for a near-zero joint pass rate.

For a deterministic safety protocol, enumerate the states in which the gate can pass and prove
that the test fixture or drill can reach those states without bypassing the control under test.

## Review sign-off

The freeze record must identify who prepared the feasibility analysis and who accepted the rule.
Acceptance confirms the rule is fit for its declared purpose; it does not authorize later gates,
broker actions, capital, or post-result reinterpretation.
