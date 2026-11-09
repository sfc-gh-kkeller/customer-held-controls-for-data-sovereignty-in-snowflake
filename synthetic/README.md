# Synthetic data that keeps the insights

A plain-SQL **segment generator** that builds an artificial copy of a sensitive table. It keeps the insights a data
consumer cares about, and no real row is copied. It is meant for sharing data with partners or third countries,
building data products, and giving teams realistic data without giving them real people.

## How it works

1. **Learn statistics per segment** (department × country × level) from the real table:
   - row count;
   - mean and spread of salary;
   - how bonus follows salary;
   - attrition by tenure.
2. **Merge small segments.** A segment with fewer than `MIN_CELL` people (10) is merged into its parent: first
   (department, level), then (department) alone.
3. **Add noise.** Every published count, mean and rate gets Laplace noise. Salaries are clipped first, so one person
   can only move a segment mean by a known, bounded amount.
4. **Sample new rows** using only those noisy statistics. The real table is not read in this step.

```
snow sql -c <connection> -f synthetic/01_source.sql      # 5,000 "real" employees with known structure
snow sql -c <connection> -f synthetic/02_generate.sql    # segment model + synthetic table
snow sql -c <connection> -f synthetic/03_scorecard.sql   # insights and privacy, real vs synthetic
```

Knobs at the top of `02_generate.sql`:

- `MIN_CELL`: the smallest segment that is published on its own.
- `EPS`: the noise strength. Smaller means more privacy and less accuracy.
- `SAL_LO` and `SAL_HI`: the clipping range for salaries.

## Measured results (one run, 5,000 rows, `MIN_CELL = 10`, `EPS = 1`)

The noise is random, so figures move a little between runs (a later run: 6.0% mean segment error, correlation 0.83).

| Insight | Real | Synthetic |
| --- | --- | --- |
| Rows | 5,000 | 4,998 |
| Average salary | 88,881 | 88,738 |
| Correlation level ↔ salary | 0.86 | 0.84 |
| Correlation salary ↔ bonus | 0.82 | 0.81 |
| Attrition overall | 16.6% | 15.9% |
| Attrition, tenure 0–2 / 12+ years | 26.1% / 4.8% | 24.8% / 5.6% |
| Average salary in PL | 70,434 | 67,214 |
| Salary gap, level 5 vs level 1 | 60,583 | 60,627 |
| Three-way effect: Sales × DE × level 5 | 162,121 | 162,161 |
| Rare group: Legal headcount / average salary | 12 / 99,994 | 12 / 116,997 |
| Error over the 30 department × level salary averages | – | 5.2% mean, 47% worst (Legal, level 1) |
| Exact copies of a real row | – | 0 |
| Median distance to the closest real record (1,000 sampled) | 0.657 (real person to nearest other real person) | 0.63 |
| Near copies (distance < 0.05) | 3 (real to real) | 9 |

Reading the results:

- **Large and medium segments keep their structure.** That includes the effects that matter commercially: the level
  gap, the country gap and the three-way premium.
- **Rare groups pay the privacy price.** Legal is merged across levels, so its per-level salaries do not survive. This
  is intended: a group of 12 people cannot be described in detail without describing the people.
- **Synthetic rows sit as close to real people as real people sit to each other.** That is the cost of keeping the
  insights. It is the reason for the noise, the merging and the clipping, and the reason to review privacy before
  anything is shared.

## Pros and cons

| | |
| --- | --- |
| **Pros** | Keeps the segment-level insights you choose, including interactions.<br>No real row is copied, and the sampling step never reads the real table.<br>Plain SQL in Snowflake: no extra service, no data leaves the account to be synthesized.<br>Every published statistic is visible and auditable in `SEGMENT_MODEL`, `COUNTRY_MIX`, `LEVEL_MIX` and `TENURE_MODEL`.<br>The privacy and accuracy trade-off is tunable with `MIN_CELL` and `EPS`. |
| **Cons** | Keeps only what you model. Relationships outside the segment design (here: tenure by segment) come out independent.<br>Not an audited differential-privacy guarantee:<br>– the privacy budget is not added up across statistics;<br>– spreads and regression terms are published without noise.<br>Rare groups lose detail by design.<br>The model is specific to the dataset: a new table needs a new segment design. |

## What it can realistically be used for

| Use | Fit |
| --- | --- |
| Test, development and analytics sandboxes without access to real people | Good |
| Sharing with partners, vendors or offshore teams for building pipelines, dashboards and models | Good |
| Data products and benchmarks sold on agreed insight questions, with this scorecard published alongside | Good, after a privacy review |
| Training first versions of machine learning models | Fair: works for segment-driven signal, not for patterns outside the model |
| Rare events, fraud, individual-level decisions, anything needing exact figures for small groups | Not suitable: use the real data under the controls in Patterns 1–4 |

## Other approaches the platform supports

Snowflake can also run audited open-source generators through **Snowpark Container Services** (for example, a
container-runtime notebook). These include differentially private synthesizers such as MST or AIM (via OpenDP /
`smartnoise-synth`) and model-based generators such as SDV. They are an option when a formal privacy guarantee or
broader coverage of relationships is needed. The scorecard in `03_scorecard.sql` works for their output too.

## Clean up

```sql
DROP SCHEMA SOVEREIGNTY_DEMO.SYNTH;
```
