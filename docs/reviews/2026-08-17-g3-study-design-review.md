# G3 Study-Design Review Record

- **Recorded:** 2026-08-17
- **Review type:** Pre-results advisory; not a protocol amendment
- **Protocol affected:** `g3-ema-v1`, version 1.0
- **Strategy results produced:** None
- **Final-holdout strategy access:** None
- **Decision:** Pause G3.3; complete a synthetic-only G3.2b design-assurance checkpoint first

## Why this record exists

An independent CTO review challenged the power and cost assumptions in the preregistered G3 EMA
study before the experiment registry was frozen or any registered strategy evaluation was run. A
separate exploratory power calculation was also found invalid: its momentum calibration produced
approximately 15,000% per-trade standard deviation. No table or conclusion from that calculation
is admissible evidence.

This file records the review, the corrections accepted after checking the repository, and the
information disclosure concerning the final holdout. It deliberately does not edit
`G3.1-EMA-PREREGISTRATION.md` or `G3.2-SPLIT-CONTRACT.md`. Any material change remains subject to
their versioning and holdout rules.

## Findings accepted for design assurance

1. **Power is a binding design risk.** The 100-trade floor, +10-bps lower-confidence-bound hurdle,
   doubled-cost requirement, fold-success rule, neighborhood rule, and multiplicity control must
   be tested jointly enough to show that the study can detect an economically relevant EMA edge.
2. **The cost hurdle is large.** Under the preregistered primary scenario, a flat-price round trip
   pays approximately 230 bps across two fills before compounding details. The doubled scenario is
   approximately 460 bps. A useful power analysis must apply the repository's directional
   spread/slippage and per-fill fee semantics rather than subtracting an informal constant.
3. **The original exploratory power table is invalid.** Synthetic paths must have an explicit
   variance decomposition, stationary latent state, calibrated annualized volatility, fixed seed,
   and fail-closed diagnostics for nonfinite or implausibly dispersed paths and trades.
4. **The protocol has unresolved implementation choices.** Before G3.3, the registry contract must
   resolve how fold-specific selections become one final pre-holdout pair, how candidate-screen
   inference interacts with the adaptive walk-forward pipeline, and which exact risk configuration
   is passed to the engine.
5. **G3.3 should remain reusable.** Experiment identity, append-only status, resampling, stress,
   selection, and multiplicity machinery should not assume that all future hypotheses are EMA
   crossovers.

## Cost-route correction

The review suggested that the preregistered 95-bps value necessarily double-counted Robinhood's
market-maker compensation. Repository and official-source review did not support that conclusion.
The two routes are distinct:

- Robinhood Crypto API v1 market-maker routing represents compensation inside the displayed
  spread and has no separately configured transaction fee in this repository.
- Robinhood Crypto API v2 exchange routing can charge an exchange-routing taker fee separately
  from the bid/ask spread. The current public lowest-volume tier is 0.95% per fill.

Accordingly, `costs.py` already separates the two profiles correctly. The remaining issue is not
an arithmetic double count; it is that route, account tier, displayed spread, and realized
slippage have not yet been observed for the intended deployment path. Clip size does not itself
set the v2 fee tier; trailing eligible exchange-routing volume does.

Primary-cost assumptions remain unchanged in protocol version 1.0 while design assurance is in
progress. A read-only quote/estimated-price/fee sampler would reduce uncertainty, but creating a
credential or contacting a broker is outside this checkpoint and requires separate authorization.

## Holdout information disclosure

On 2026-08-17, before G3.3, the project was told that the frozen final year was a large BTC decline.
A market-path-only verification then established these endpoint facts without passing holdout
candles to the strategy or calculating an EMA, signal, fill, trade, parameter score, or strategy
metric:

| Item | Disclosed value |
|---|---:|
| First holdout open | `$117,436.95` |
| Last holdout close | `$63,018.75` |
| Endpoint open-to-close change | `-46.3382%` |
| Admitted holdout bars | `8,750` |

This does not preserve literal blindness to the market path. The holdout remains
**strategy-unseen**, which is the narrower status the project will use from this point forward.
No parameter, cost, threshold, split boundary, signal rule, or decision rule changed in response to
the disclosure. A long-only strategy's behavior in a falling market is relevant to the
unconditional preregistered hypothesis and may not be excluded or rescued by a post-hoc regime
condition.

## G3.2b completion rule

G3.2b may use synthetic data only. It passes design assurance only when all of the following are
true:

- the generator's variance decomposition is explicit and its realized volatility is within a
  predeclared calibration tolerance;
- the run aborts before reporting power if price, return, latent-state, or trade-dispersion checks
  fail;
- synthetic execution arithmetic is reconciled to the production backtest engine on deterministic
  fixtures;
- the frozen fold lengths, validation selection, flat resets, next-bar execution, terminal
  liquidation, primary costs, and doubled costs are represented;
- reported pass rates name every preregistered criterion they include and exclude; and
- the final recommendation is to retain protocol 1.0 unchanged or supersede it prospectively.

G3.2b does not pass G3, unlock the final holdout, or authorize broker access, paper graduation, or
live capital.

## Resolution

The protocol 1.0 files and frozen split artifact remain unchanged. G3.2b subsequently completed a
500-replicate dependency-free, variance-normalized synthetic design screen with engine
reconciliation and machine-readable per-replicate evidence. No scenario passed even the
representable subset of protocol criteria. ADR 0003 therefore retires protocol 1.0 before strategy
execution. G3.3 is paused, not completed, until a prospective protocol 2.0 is approved. See
`docs/evidence/G3.2b-2026-08-17.md`.

## Official route references checked

- [Robinhood Crypto fee schedule](https://cdn.robinhood.com/assets/robinhood/legal/rhc-fee-schedule.pdf)
- [Robinhood smart exchange routing](https://robinhood.com/us/en/support/articles/smart-exchange-routing/)
- [Robinhood crypto fee tiers](https://robinhood.com/us/en/support/articles/crypto-fee-tiers/)
- [Robinhood Crypto API overview](https://robinhood.com/us/en/support/articles/crypto-api/)
- [Robinhood Crypto Trading API documentation](https://docs.robinhood.com/crypto/trading/)
