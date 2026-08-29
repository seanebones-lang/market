# Detectability Frontier — Sample-Size Input for a Future Protocol 2.0

- **Recorded:** 2026-08-17
- **Review type:** Advisory design input; not a protocol, not an amendment, not evidence
- **Method:** Closed-form only. No simulation, no project data, no strategy execution.
- **Project data read:** None
- **Strategy results produced:** None
- **Final-holdout access:** None
- **Status:** Informational. Confers no gate authority and does not authorize G3.3.

## Why this record exists

ADR 0003 requires a future protocol 2.0 to "derive its sample-size requirement from prospective
power instead of combining a fixed 100-trade floor with slow trend candidates." This file supplies
the closed-form frontier that turns that requirement into a number, so the design conversation can
start from a constraint rather than a preference.

It is deliberately separate from the G3.2b power study. G3.2b simulated the specific `g3-ema-v1`
pipeline and criteria. This record asks a more basic question that is independent of any strategy
family: **given the calendar history available, what size of edge is detectable at all?**

## Method

For returns sampled `P` times per year over `T` years, the standard error of the annualized Sharpe
estimate is approximately `sqrt((1 + SR^2 / (2P)) / T)`. At hourly sampling the correction term is
negligible, so `SE(SR_annual) ~= 1 / sqrt(T)`.

A one-sided test at level `alpha` with power `1 - beta` therefore requires:

```text
SR * sqrt(T) >= z_alpha + z_beta
```

Family-wise control across `m` candidates enters through `alpha / m` (Bonferroni / Holm first
step). All figures below use 80% power and a 5% family-wise level.

## A. Minimum detectable annualized Sharpe

| Available history | m=1 | m=4 | m=9 | m=36 |
|---|---:|---:|---:|---:|
| Stitched walk-forward OOS (27 months) | 1.66 | 2.06 | 2.25 | 2.56 |
| Full G1 dataset (5 years) | 1.11 | 1.38 | 1.51 | 1.71 |
| BTC hourly since approx. 2015 (~11 years) | 0.75 | 0.93 | 1.02 | 1.16 |
| Hypothetical 20 years | 0.56 | 0.69 | 0.76 | 0.86 |

## B. Years of history required to detect a given true Sharpe

| True annualized Sharpe | m=1 | m=4 | m=9 | m=36 |
|---:|---:|---:|---:|---:|
| 0.50 | 24.7y | 38.0y | 45.7y | 58.8y |
| 0.75 | 11.0y | 16.9y | 20.3y | 26.1y |
| 1.00 | 6.2y | 9.5y | 11.4y | 14.7y |
| 1.50 | 2.7y | 4.2y | 5.1y | 6.5y |
| 2.00 | 1.5y | 2.4y | 2.9y | 3.7y |
| 3.00 | 0.7y | 1.1y | 1.3y | 1.6y |

## Consequences for protocol 2.0

1. **The binding constraint is calendar time, not criteria design.** Against the 36-candidate
   family on the stitched out-of-sample window, nothing below an annualized Sharpe of roughly 2.6
   is detectable at 80% power. No rewriting of thresholds, hurdles, or floors changes this.

2. **Extending the dataset is the highest-leverage available change.** Moving from 5 years to
   roughly 11 years of hourly history takes the 36-candidate minimum detectable Sharpe from 1.71
   to 1.16. That is a G1 extension, not new science, and it dominates any achievable gain from
   multiplicity method or criteria tuning.

3. **Shrinking the candidate family helps, but far less than data.** Going from 36 candidates to 4
   improves the 5-year minimum detectable Sharpe from 1.71 to 1.38. Worth doing; not sufficient
   alone.

4. **State the target explicitly.** Protocol 2.0 should name the "worth owning" alternative before
   freezing, then confirm the available history can detect it. If the target is a Sharpe of 1.0
   and the family is 36, panel B says roughly 15 years are required, and the design is infeasible
   as posed regardless of how carefully the criteria are written.

## On changing the estimand

Moving the primary estimand from per-trade returns to an aligned hourly or daily portfolio return
series would resolve the criterion-conflict pathology that G3.2b exposed: the trade-count floor
would stop fighting the validation ranking rule, because the number of observations would no longer
depend on which candidate is selected. It would also make the block bootstrap well-posed on aligned
time blocks, and make a Romano-Wolf or SPA family valid across candidates that share one time index.

**It would not create statistical power.** The Sharpe standard error still collapses as
`1 / sqrt(T)` in calendar time, so panels A and B continue to bind unchanged. The benefit is
structural, not informational.

## Limitations

- Assumes independent, identically distributed returns. Real strategy returns exhibit serial
  dependence and fat tails, both of which inflate the requirement.
- A long-or-flat strategy is exposed only part of the time, further reducing effective sample size
  relative to elapsed calendar time.
- Bonferroni / Holm is conservative under positive dependence; a resampling-based family-wise
  method would recover part of the `m` penalty but cannot alter the `1 / sqrt(T)` scaling.
- The figures are therefore **optimistic lower bounds** on the history required.
