# Selective withholding experiment

This mode is independent of Coalition Path Favoritism. It never starts candidate
collection, adaptive relay updates, flooding or sybil nodes. Select it with
`--attack-mode selective-withholding`. All existing configurations and the old
seed bundle remain unchanged.

## Controlled transport and pairing

The old wall-clock asynchronous simulator cannot ensure identical warm-up
receipts. This opt-in mode processes independent node inboxes concurrently,
using eight node workers per run. A coordinator dispatches positive-delay time
windows and joins results in node order. Within a window no outgoing message can
arrive at another node before that window ends, so signature verification and
forwarding run concurrently without violating causal order. Same-node inbox
order and equal-time envelope identities are deterministic; worker completion
order does not affect delivery or numerical reductions. All three probabilities
share this parallel transport. It reuses real transaction signatures,
one-prefix receipt guard, aggregate path verification, block validation,
frozen-v1 reward plan, score saturation/EMA and delayed score/reward activation.
It does not use precomputed routes or synthetic propagation credit.

The graph, wallets and stakes use the original generators and seed derivation.
Poisson counts and source/destination draws use the original background streams.
New transaction identities are signed with a unique logical timestamp; legacy
transaction constructors retain their wall-clock behavior. Stable floating-point
reduction order is explicitly enabled only for this mode's TRAIL instance.
Each scenario elects proposers from its own actual scores using common per-slot
randomness. Scores are not frozen after attack onset.

Messages incur the original active relay's 50 ms hop delay in logical time, times
the configured delay multiplier. Creation events are evenly placed within each
1 s slot. The canonical block is proposed/synchronized at slot end. Signed path
domains expire at epoch boundaries; the experiment does not artificially retry
or resign expired paths. These controlled transport choices are shared by every
arm and must be disclosed when interpreting network inclusion results. They do
not change transport in any other experiment.

Every validator starts with 100 token liquid balance, separately from immutable
economic stake, to keep insufficient funds from changing paired workloads.
F=g=0.001 token; g follows the frozen-v1 fee default because the task does not
specify a separate value. Stake reinvestment is off.

## Attack and accounting

An eligible event is a coalition node's first valid receipt of an honest-origin
transaction directly from an honest upstream node. Its deterministic coin is
SHA3-256 of attack seed, node identity and signed transaction identity. During
epochs 10–49 it withholds with probability p in {0, 0.5, 1}; outside this interval
it always forwards. The decision is made before outbound signing. A withholding
node retains its valid receipt and still forwards to eligible coalition
neighbors. It suppresses only outbound edges to honest neighbors. Honest-to-
honest edges and consensus participation are unaffected.

Node/transaction decisions, unique transaction counts, suppressed edges and
actual signed forwarding attempts are separate metrics. Costs use only signed
relay forwarding attempts, matching the participation counter. Originator
broadcasts are logged separately and are not charged as relay attempts.

Modeled cost-adjusted net income is accrued relay and proposer rewards minus
actual F+g deductions minus forwarding attempts times
1.4055695425812074e-7 token. Delayed settlement is not added a second time.
This is not an estimate of every real deployment operating cost.

Expected proposer rewards sum, at each slot, the election weight of each
coalition validator times its base reward plus theta times the fees of that
validator's actual eligible block transaction set. This includes both subsidy
and fees, is conditional on the realized history, and is reported separately
from actual rewards.

## Run and validation

```bash
cargo fmt --check
python scripts/task.py test
python experiments/check_withholding_parallel.py
python scripts/task.py run-config --config experiments/configs/trail_withholding_pilot.yaml --dry-run
python scripts/task.py run-config --config experiments/configs/trail_withholding_pilot.yaml
python experiments/trail_withholding_report.py --config experiments/configs/trail_withholding_pilot.yaml
```

The pilot has 18 runs. The main configuration has 120 runs. Both use separate
suite/revision names and build into `target/withholding-v2`; old experiment
binaries are preserved. No `--force` is used. Every run requires fresh audit
files. Before main execution, the pilot audit must pass exact (not rounded)
warm-up economics, complete proposer/block sequences and per-validator score,
weight and balance comparisons. Background transaction files, topology, initial
stakes, coalition membership, source identity, protocol settings and seeds must
match. Background comparison uses every signed creation record in logical
`(at_ms, id)` order. Physical log append order may change after attack onset as
parallel delivery windows evolve; its raw hash is retained as a diagnostic,
while all transaction fields, identities and generation times remain exact.
Warm-up logs must also match in their original append order, and state checks
include the attack-start boundary. Missing/failed runs, unsigned or invalid path records, inconsistent costs
and nonexistent withholding opportunities fail explicitly.

The parallel preflight executes 12 matched small-network scenarios with two
workers, then repeats them with eight workers. It requires actual concurrency
greater than one and byte-identical epoch, state, slot, block, generation,
decision, topology, coalition and stake files across the two worker counts.
Single-worker configurations are rejected. Official runs use eight node
workers each, with four runs at a time. The earlier global serial transport and
its partial data were explicitly rejected and are excluded from analysis.

```bash
python scripts/task.py run-config --config experiments/configs/trail_withholding_main.yaml --dry-run
python scripts/task.py run-config --config experiments/configs/trail_withholding_main.yaml
python experiments/trail_withholding_report.py --config experiments/configs/trail_withholding_main.yaml
```

Raw per-run files contain epoch CSV/JSONL, signed block evidence, generation and
decision logs, per-validator states, inputs, summary, command and exit status.
Processed results contain `epochs.csv`, `paired.csv`, `summary.csv`,
`consistency.csv`, `validation.json`, `appendix_table.tex` and `report.md`.
Paired Student-t intervals cover attack, recovery, attack+recovery observation
and full-run windows. Theorem 6 uses each run's actual immutable economic stake;
all bound violations and a>=1/3 premise failures are reported separately.

No result is selected for having a desired sign. Confidence intervals containing
zero do not establish unprofitability. The experiment does not change the paper,
Response to Reviewers, existing figures or the path-favoritism experiment.

## Complete-study figures

```bash
python analysis/plot_trail_withholding.py
```

The plotter requires all 120 runs and 20 matched seeds per scenario, checks the
published statistics against `paired.csv`, and exports four individual PNG/PDF
figures plus a combined overview into `figures/trail_securityV2`. It uses the
attack+recovery observation window, epochs 10–79. Income in the overview and
appendix table is the paired modeled cost-adjusted difference in token.

An additional `trail_selective_withholding_income_percent` figure computes each
seed's percentage change relative to its own positive baseline before averaging.
This estimates a different quantity from the token mean, so its confidence
interval can have different significance. Compact figure data are exported as
`figure_points.csv`, `income_percent_pairs.csv` and `最终结果汇总.csv` in the
processed main-results directory. No existing security figure is overwritten.
Plot metadata JSON is also written beside the processed results; the figure
directory contains only PNG and PDF outputs.
