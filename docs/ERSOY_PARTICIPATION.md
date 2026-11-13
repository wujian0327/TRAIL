# Ersoy fee sharing under the shared PoS propagation model

The main comparison contains PoS, Fee-only TRAIL (`eta=0`), Ersoy-fee,
and Full TRAIL. Separate suites cover score-smoothing ablation (`beta=1`)
and Ersoy parameter sensitivity. The main Ersoy parameter is fixed at
`C=0.25` before observing results; sensitivity values are `0.1` and `0.4`.
This is a declared fee-allocation parameter, not a value derived from the
current BA graph's degree or a claim that the paper's incentive theorem
holds for this stake distribution.

## Path and reward semantics

Reference: Ersoy et al., CVCBT 2018, Theorem 1 and Fig. 1,
<https://arxiv.org/html/1712.07564>.

`v0` is the transaction originator/client. For a certified path
`v0 -> v1 -> ... -> vk`, `vk` is the proposer. The fee-sharing path is
`v1,...,vk`, excluding the client, and contains `k` nodes:

- Non-proposer position `i`: `F C (1-C)^(i-1)`.
- Proposer position `k`: `F (1-C)^(k-1)`.
- Direct client-to-proposer and proposer-originated transactions give the
  full fee to the proposer.
- The common block subsidy is added separately, exactly once per block.
- Invalid or missing evidence receives no path fee; the fee is recorded
  as burned. Valid paths conserve the complete distributable fee.

Ersoy uses `eta=0`; a nonzero proposer bonus is rejected. Score diagnostics
may still be computed, but do not affect its election probability.

All arms share the topology, cost distribution, workload model, evidence,
first-arrival path selection and adaptive Active/Lazy behavioral model.
The native Ersoy smart routing and shortest-path selection are not reproduced.
Consequently, these results compare fee incentives, not native network or
cryptographic overhead or the original protocol's full strategic guarantees.

The shared behavioral model estimates expected proposer income using the
observed epoch's proposer reward pool times proposer probability. This is
a history-only mean-field approximation; in Ersoy it averages fees that
depend on the realized path length. Realized proposer rewards are retained
separately. The model provides behavioral evidence, not an equilibrium proof.

### Behavioral estimator repair (2026-11-12)

New binaries log `benefit_estimator=ema_premiums_v2`. They keep the historical
group observations, alpha, costs, hysteresis, exploration and reward rules,
but smooth the reward premium and work premium separately:

    R_ema <- (1-alpha) R_ema + alpha (R_active/S_active - R_lazy/S_lazy)
    W_ema <- (1-alpha) W_ema + alpha (W_active/S_active - W_lazy/S_lazy)
    decision benefit = R_ema / W_ema

The first valid observation initializes both premiums directly. A valid
observation requires both groups, finite premiums, positive extra work and a
finite ratio. Thus small-work windows receive proportionally little weight;
negative reward evidence remains eligible and is not clipped.
On an invalid observation both historical premiums decay by `(1-alpha)` but
no income-based switch is allowed. Random exploration remains enabled, so
a missing group can reappear. The update round advances on every scheduled
decision, keeping the candidate schedule independent of observation validity.

The adaptive CSV retains its original fields and adds the estimator version,
observation validity, actual decision benefit, and both smoothed premiums.
`smoothed_benefit_per_forward` is a historical ratio; a blank
`decision_benefit_per_forward` explicitly means it was not used that round.
`experiments/adaptive_estimator_audit.py` independently reconstructs these
moments and checks that invalid-window switches come only from exploration.

This repairs ratio amplification and stale-decision handling. It does not
turn a comparison of different Active/Lazy populations into an individual
counterfactual estimator. Legacy binaries and results remain archived.

The paired rerun uses `trail_ersoy_full_cost5_estimator_v2.yaml`: 5x costs,
fees `0.0001,0.0005,0.001,0.005,0.01`, Ersoy and Full, seeds 200/201,
200 epochs with 100 warm-up. All 20 runs use the repaired binary; old Full
points are not reused in the repaired mechanism comparison. The postprocessor
`ersoy_full_estimator_comparison.py` retains a separate old/new comparison.

## Configurations

| Config suffix | Seeds | Epochs / warm-up | Costs | Runs |
|---|---:|---:|---|---:|
| `pilot` | 100, 101 | 40 / 20 | 2x | 14 |
| `main` | 100--119 | 200 / 100 | 1x, 2x, 3x | 240 |
| `no_ema` | 100--119 | 200 / 100 | 1x, 2x, 3x | 60 |
| `sensitivity` | 100--119 | 200 / 100 | 2x | 40 |

All configs are named `experiments/configs/trail_ersoy_participation_*.yaml`.
The pilot includes five mechanisms at `C=0.25` plus Ersoy at `C=0.1,0.4`.
It is a smoke test, not evidence of steady-state convergence or superiority.
The formal sensitivity suite supplies the two extra C values; its `C=0.25`
reference is in the main suite. All costs retain the historical shared
reference `1.4055695425812074e-7` token per forward.

## Running and checking

```bash
cargo test --offline --lib
python3 -m unittest discover -s experiments/tests -p 'test_ersoy_participation.py'
cargo build --release --offline
python3 experiments/run_experiments.py --config experiments/configs/trail_ersoy_participation_pilot.yaml --no-build
python3 experiments/ersoy_participation_report.py --config experiments/configs/trail_ersoy_participation_pilot.yaml
```

Replace `pilot` with `main`, `no_ema`, or `sensitivity` for each formal suite.
The sequential formal batch archives the binary, source, source patch and
configurations, then runs main, sensitivity and no-EMA suites with automatic
validation. The final combined report compares all arms, including paired
Full-TRAIL versus no-EMA differences, in `trail_ersoy_participation_complete`.
Its `manifest.json` tracks stage status:

```bash
python3 -u experiments/run_ersoy_comparison.py --batch-dir results/batches/ersoy-fee-v1 --max-parallel 3
```

For an early readout, a subset can retain the original suite name, run revision,
archived binary and parameter dimension names, while selecting seeds
`100,101,102` and only the `1x` cost. This produces 12 paired runs across the
four mechanisms, retaining 200 epochs and 100 warm-up epochs. Completed
matching runs are reused. `run_ersoy_subset.py` runs only the supplied subset
config and generates a separate validated output, without scheduling any
remaining main, sensitivity or ablation runs:

```bash
python3 -u experiments/run_ersoy_subset.py \
  --config results/batches/ersoy-fee-v1/subset_3seeds_cost1.yaml \
  --manifest results/batches/ersoy-fee-v1/subset_manifest.json \
  --output-name trail_ersoy_participation_subset_3seeds_cost1
```

The subset manifest is independent of the full batch's manifest. A full batch
stopped at the user's request stays `stopped` while the subset runs and completes.

### Fixed-g fee trend pilot

`experiments/configs/trail_fee_participation_fixed_g_pilot.yaml` scans fees
`0.0001, 0.0002, 0.0005, 0.001` with PoS, Fee-only, Ersoy and Full TRAIL.
It fixes irrecoverable cost at `0.0001`, subsidy at `0.1`, forwarding cost at
`1x`, and uses fresh paired seeds `200,201`: 32 runs of 200 epochs, excluding
the first 100 epochs. Each node starts with a separate spendable balance of
`1.0`; economic stake retains its original distribution and is not reinvested.
Both actual transaction charging and the fee ledger use `F+g`.

```bash
python3 experiments/run_fee_participation.py \
  --config experiments/configs/trail_fee_participation_fixed_g_pilot.yaml \
  --batch-dir results/batches/fixed-g-fee-trend-v1 --max-parallel 3
```

The pilot plot uses a logarithmic fee axis, mean participation and thin lines
for individual seeds. It does not present two seeds as the planned 20-seed
paper result. Across-seed 95% t intervals remain available in the CSV output.
The report also checks common input workloads, unchanged economic stake,
nonbinding transaction funding, and `F+g` accounting.

Run all formal arms with the same binary and source diff. Preserve an exact
source patch and binary hash with the batch log. Do not reuse old three-arm
results or change the simulator while a batch is running.

The report requires complete configured coverage, matching simulator source
fingerprints, matching initial stake/profile/cost/degree designs per seed,
finite metrics, stake-only weights for the relevant arms, and reconciled
node/epoch rewards. Node rewards are stored with six decimal places; the
reconciliation explicitly allows that rounding error.

It writes `runs.csv`, `groups.csv`, `epochs.csv`, `paired_contrasts.csv`,
`audit.json` and PNG/PDF figures under `results/processed/<suite>/`.

## Metric interpretation

- Active relay stake: average over the analysis epochs within each run,
  followed by the across-seed mean and two-sided 95% Student t interval.
- Inclusion: generated transactions with a complete 10-epoch (50 logical
  seconds) observation window. Missing or late inclusions receive the
  horizon value. The restricted mean and timeout-adjusted P95 include them.
- Economic statistics: token per validator per analysis epoch. Forward cost
  is attempts times that validator's cost. Relay net income subtracts this
  cost from relay rewards. Total net income also includes proposer rewards
  and subtracts the change in the cumulative originator fee ledger.
  Rewards accrue to the included blocks; account balances can lag because
  the common TRAIL/Ersoy settlement queue waits for the configured depth.
- The existing simulator charges an originator both the distributable fee
  and an equal irrecoverable cost (`2F` in the fee ledger), identically in
  every arm. Forwarding costs are behavioral/off-chain costs and are not
  debited from the on-chain balance field.
- Contrasts use paired seed differences, not epochs as independent samples.
  Small pilot intervals can be extremely wide; formal conclusions need the
  full predeclared seed matrix. Nonblocking enqueueing and runtime scheduling
  can still make realized workloads differ despite matched workload seeds.
- `beta=1` removes score-history smoothing, while preserving score activation
  delay and the independent behavioral-benefit EMA.
