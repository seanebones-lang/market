# G3.2e Connection-Failure Review Record

- **Recorded:** 2026-08-17
- **Review type:** Advisory engineering review; not a protocol amendment
- **Protocol affected:** `G3.2e-BEST-PRICE-COHERENCE-RESOLUTION` (unchanged)
- **Repository changes made by this review:** None
- **Live API requests made by this review:** None
- **Strategy results produced:** None
- **Empirical execution cost admitted:** None
- **Decision:** Repository unchanged; case `021436592` remains the active dependency

## Why this record exists

An independent CTO review examined why the authenticated G3.2e preflight stops on
`best_bid_ask_schema_invalid:results.0:best_price_market_crossed`. Several of its claims were
accepted, several were withdrawn after challenge, and one prescription was rejected as a
statistical error. This file preserves both sides so the reasoning is auditable, and so the
withdrawn claims cannot be recovered later as if they had stood.

It deliberately does not edit the frozen resolution plan, the observation contract, the read
client, or any evidence record.

## Findings accepted

1. **The ordering rule conflates two responsibilities.** `BestBidAskResult._ordered_market`
   (`src/market/execution/robinhood/observations.py`, approx. line 162) enforces `ask >= bid`
   inside payload validation. Structural validity (fields present, decimals finite and positive)
   and observation admissibility (does this relation satisfy the frozen study rule) are separate
   concerns. Reporting the second as a schema error is imprecise.

2. **Crossed rows do not participate in the retry budget.** `_request`
   (`src/market/execution/robinhood/read_client.py`, approx. line 188) retries
   `httpx.TransportError` and HTTP 429/500/503, while a `ValidationError` is converted to
   `RobinhoodReadSchemaError` and raised immediately. This is factually accurate as a description
   of current behavior. It is **not** accepted as a defect requiring correction; see below.

3. **A future refactor is warranted but must be behavior-preserving.** A split resembling
   `BestBidAskPayload` / `BestBidAskObservation` / `ObservationAdmissibilityPolicy` would clarify
   the rejection reason. Under the current protocol a crossed row must still terminate the same
   collection at the same point, with the same public exception and the same sanitized label.

4. **Authentication construction is correct.** The full request path including query string is
   signed, method `GET`, bodyless. No defect identified.

## Claims withdrawn after challenge

| Claim | Disposition |
|---|---|
| Crossed rows should be retried now | **Withdrawn.** Retrying conditional on the observation being crossed, then retaining the replacement, conditions retention on the measured quantity. It truncates the left tail of the signed-spread distribution and changes the missingness mechanism and the estimand. |
| "Retrying a rejected row is not reinterpreting it" | **Withdrawn.** Only true if every attempt remains part of the sampling unit and no preferential selection occurs. The proposed flow did not satisfy that. |
| `/api/v2/crypto/trading/estimated_price/` is a likely latent 404 | **Withdrawn.** The path matches Robinhood's official v2 documentation. The `marketdata` / `trading` asymmetry is intentional. The original flag rested on the v1 surface plus an asymmetry heuristic, which is weak evidence. |
| Crossing is "almost certainly" normal aggregation lag | **Withdrawn as stated.** The volatility-versus-spread arithmetic shows crossing is cheap to produce, not that it occurred for that reason. Three attempts cannot separate lag, undocumented semantics, venue mismatch, field inversion, or a transient backend defect. |
| Partner exchange identified as Bitstamp | **Withdrawn.** Not established by official documentation, which describes one or more partner exchanges. |
| Best bid/ask is immaterial because the fee dominates | **Withdrawn.** Conflates small-in-magnitude with removable-from-the-estimand. The registered contract decomposes cost into midpoint, quoted half-spread, size impact, and all-in one-way and round-trip cost; best bid/ask feeds all of them. |
| The support escalation was aimed at the wrong target | **Withdrawn.** The question put to Robinhood is well-formed and only the vendor can answer it authoritatively. The defensible objection was narrower: that the reply is being treated as the gating dependency. |
| Run approximately 200 signed-spread samples | **Withdrawn.** Requires unissued live-network authorization; records spread magnitude, which the minimization commitment prohibits; oversamples a single market minute with heavy autocorrelation; and conflicts with the frozen two-day, fixed-cadence, one-attempt, sign-only fallback. Describing it as "fully sanitized" was incorrect. |
| Reclassify the frozen fallback as characterization before collection | **Withdrawn.** The resolution plan may be amended only before new Robinhood response information is observed, and that window closed at the first crossed response. The proposal was itself an after-the-fact amendment to a frozen design made after learning its acceptance rule was unlikely to pass. |
| The fallback is a predetermined failure | **Overstated.** The arithmetic conditions on exchangeability between the three pilot attempts and 183 future slots under a stationary independent Bernoulli process. Clustering by deployment, market regime, routing state, or a transient defect is not excluded. |

## Fallback acceptance-rule arithmetic

Both parties independently reproduced the following. The frozen fallback passes only with zero
`ask_lt_bid` rows across at least 183 completed slots, so for a true crossing probability `p`:

```text
P(pass | p) = (1 - p) ** 183
```

| Assumed true crossing rate | P(zero crossings in 183 slots) |
|---:|---:|
| 0.38% | 49.8% |
| 1.6% | 5.2% |
| 9.4% | 1.4e-08 |
| 66.7% | 4.9e-88 |

The rule is an even chance only if the true crossing rate is below approximately **0.38%**, and
retains a 5% chance only below approximately **1.6%**. Two crossings in three attempts give an
approximate 95% interval on `p` of `[9.4%, 99.2%]`. Integrating `(1 - p) ** 183` over a
`Beta(2.5, 1.5)` Jeffreys posterior gives `P(pass) ~= 1.45e-05`.

**Accepted interpretation:** if the observed endpoint behavior persists and the three attempts are
even roughly representative, the zero-crossing fallback is overwhelmingly unlikely to pass. This
is not a known future verdict.

**Also accepted:** a fallback failure would not be scientifically empty. It would establish that
the registered best-price cost decomposition cannot be operated against this interface without an
interpretation the protocol forbids — an interaction between study design and data-generating
interface, not a null result.

## Option value of leaving the fallback frozen

If Robinhood reports and corrects a temporary backend defect, the pilot's two-of-three result stops
being representative and the zero-crossing rule becomes the correct instrument again, runnable
unchanged. Editing it now would destroy that option and would require rebuilding an equivalent
gate under a design already contaminated by knowledge of the crossing rate. The case for leaving
it untouched therefore holds in both branches, not only on governance grounds.

## Recommendations not yet actioned

1. **Timebox the vendor dependency.** Robinhood offered 24-48 business hours "or longer" with no
   guaranteed engineering response, and the resolution plan provides no trigger that reaches its
   own "no useful answer" branch. A stated decision date, after which non-response is classified
   as non-responsive, is a scheduling decision and can be made without touching frozen material.

2. **Propagate the feasibility check to operational protocols.** This is the second frozen
   acceptance rule found to be near-unpassable, after the `g3-ema-v1` joint criteria. The G3.2b
   power appendix installed this discipline for statistical designs only. Before freezing any
   acceptance rule, state the parameter range over which it can pass and confirm that range
   overlaps what is plausibly expected. For the zero-crossing rule this check is one line: it
   passes only if the crossing rate is below roughly 0.4%.

## Standing decision at time of recording

- Case `021436592`: active dependency.
- Ordinary preflight: prohibited.
- Frozen fallback: unchanged and not authorized to run.
- Characterization run: potentially valuable; requires a separate append-only protocol, explicit
  pilot-informed labelling, and separate authorization.
- Validator refactor: deferred until the semantic decision is known.
- Live trading, orders, and capital: locked.
- Repository: unchanged.
