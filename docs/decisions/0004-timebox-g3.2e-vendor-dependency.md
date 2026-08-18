# ADR 0004: Timebox the G3.2e Robinhood Vendor Dependency

- **Status:** Accepted as a scheduling decision only
- **Date:** 2026-08-18
- **Decision owner:** CTO research gate
- **Support case:** `021436592`
- **Decision deadline:** `2026-08-31T17:00:00-05:00` (`America/Chicago`)

## Context

Robinhood Crypto frontline support could not resolve the v2 best-price field semantics and
escalated the question to its back-end team on 2026-08-17. The stated expectation was 24–48
business hours or longer, without a guaranteed engineering response or service level.

G3.2e remains blocked by repeated live rows in which the documented buy `ask` was below the sell
`bid`. The frozen resolution plan preserves the published field meanings, prohibits another
ordinary preflight, and defines an unimplemented sign-only fallback that has no network authority.
That plan's amendment window has closed and this ADR does not modify it.

An unbounded vendor dependency would leave the gate without an explicit point for choosing its
already-defined no-useful-answer branch. Ten business days after submission provides a clear
operational review point without turning elapsed time into scientific evidence.

## Decision

1. Wait through `2026-08-31T17:00:00-05:00` (close of business Monday in `America/Chicago`) for an
   authoritative, technically useful Robinhood response.
2. If a useful response arrives by that deadline, sanitize and preserve its conclusions under the
   existing evidence rules before selecting or executing any next step.
3. If no response arrives, or the response does not resolve the documented field directions,
   crossing semantics, timestamp status, or recommended client treatment, classify the vendor
   dependency as **non-responsive for planning purposes** at the deadline.
4. Non-responsive classification leaves G3.2e blocked. It does not count as evidence that crossing
   is expected, does not pass or fail the frozen fallback, and does not authorize another
   preflight, diagnostic collection, scheduler, credential expansion, order, or capital use.
5. After a non-responsive classification, the next admissible action is an offline design review
   of a separate, append-only, pilot-informed characterization protocol. Any implementation or
   collection requires its own prospective protocol and explicit identifier-bearing authorization.
6. Preserve `docs/research/G3.2e-BEST-PRICE-COHERENCE-RESOLUTION.md` unchanged and unexecuted unless
   an action expressly allowed by that frozen record is later authorized.

## Consequences

- The vendor no longer has an unlimited scheduling veto over the next design decision.
- Elapsed time is not treated as endpoint evidence and cannot relax the coherence invariant.
- A later Robinhood response remains admissible evidence even after the deadline; it will be
  recorded prospectively and may change the then-current design decision without rewriting prior
  records.
- The frozen fallback retains option value if Robinhood identifies and corrects a transient
  backend defect.
- G3.3 remains paused, G4–G10 remain locked, and live order submission remains unauthorized.

## Related records

- `docs/research/G3.2e-BEST-PRICE-COHERENCE-RESOLUTION.md`
- `docs/evidence/G3.2e-support-2026-08-17.md`
- `docs/research/PROTOCOL-FREEZE-CHECKLIST.md`
