# Fig. 4(b): opt-in coalition path favoritism

The feature is disabled by default. Existing configurations, seed derivation,
RNG streams, transaction hashes/timestamps, score/reward formulas, and legacy
single-path reception remain unchanged. Existing figure scripts are not modified.
New configurations build into `target/coalition-favoritism-v2/release/trail`, leaving
the existing `target/release/trail` experiment binary in place.

## Switch and scope

`--coalition-path-selection off|honest|favoritism` selects the mode. Both enabled
arms use exactly the same candidate collection mechanism. `favoritism` changes
the selector only for coalition proposers in epochs `[10, 50)` by default.
Every other proposer/phase chooses the shortest candidate, with lexical node
sequence as a deterministic tie-break. Favored paths maximize the sum of the
existing TRAIL gamma over coalition intermediate relays; ties use the honest rule.

This is an explicit **terminal-receiver collection extension** for this experiment.
It does not claim that the old first-arrival implementation already offered
these choices, or that withholding attacks have been evaluated.

After the unchanged proposer election, only that proposer starts retaining
pending prefixes. The existing logical slot counter advances once, and a direct
request wakes the workload generator without waiting for its polling interval.
The generator uses its existing Poisson/count/routing streams once per slot.
Each queued originator acknowledges actual processing: success after signing and
scheduling broadcast, failure on rejected generation, or cancellation if a queued
message is dropped. All acknowledgements (including failed ones) complete before
the fixed collection timer starts. A 30-second real preparation timeout fails the
run explicitly instead of silently shortening the window.

Already verified prefixes from preparation remain eligible. The collection
window (`--coalition-candidate-window-ms 1000`, logical milliseconds) is then
armed for its FULL configured duration. V2 uses `time_scale=1`, so this is a real
1-second window; it cannot be consumed by waiting for workload polling. Selection,
signing and block construction follow the fixed cutoff. The preparation and
collection stages add wall-clock processing time but no extra logical workload
batches. Both arms use the same timing and neither waits for a desired number of
candidates. The original polling implementation remains when the switch is off.

Ordinary nodes continue receiving/signing/forwarding normally. The proposer
stores genuine incoming prefixes without creating its receiving proof. Complete
upstream signatures, the final sender proof, registered validator membership,
epoch/chain domain, identity uniqueness and path length are checked before admission.
Paths arriving after the cutoff are excluded. Duplicate sequences are deduplicated;
limits are 32 candidates per transaction and 4096 transactions per collection
window, with rejected/capped counts recorded. Default max path length is 16.

Transactions the proposer already signed are fixed to that original path and
cannot be reconsidered. At cutoff the proposer chooses the transaction set by
fee/timestamp/hash, then chooses ONE pending prefix per included transaction,
calls the original receiving-proof guard, and verifies the complete aggregate
certificate uncached. Candidate selection does not alter transaction selection.
Unpacked prefixes resume ordinary propagation using the honest choice in both
arms. No signature cache is cleared or bypassed.

High-betweenness placement uses all-shortest-path Brandes dependencies over the
actual undirected neighbor graph, with deterministic traversal by validator
address. The old approximation stays in use when this feature is off. Coalition
selection otherwise uses the unchanged selector and attack seed. Its realized
stake can exceed the target because validators are indivisible; both values are
reported, and bounds use realized stake.

## Evidence and economics

Enabled runs add these files in their own output directory:

- `coalition_path_selections.jsonl`: actual candidate prefixes including upstream
  signatures, final aggregate certificate, honest/favored/selected paths, eligible
  and changed-selection indicators, fixed-candidate relay/contribution effects.
  Records are published only after the canonical block is accepted.
- `coalition_epoch_economics.jsonl`: coalition relay/proposer rewards accrued at
  inclusion, actual payment snapshots, raw contribution, future proposer weights,
  honest relay rewards, bounds, and candidate rejection/cap counts.
- `coalition_actual_payments.json`: final per-epoch costs recorded only after
  successful balance deduction. This is authoritative for income reporting,
  including costs processed after an intermediate epoch snapshot.
- `initial_stakes.json`: exact initial economic stake keyed by validator address.
- `coalition_windows.jsonl`: per-slot generation acknowledgements, preparation
  duration, collection start/cutoff, admission/rejection/duplicate counts and
  messages fixed by an earlier receipt.
- `coalition_candidate_arrivals.jsonl`: proposer-handler arrival offsets,
  verification duration and disposition, including late messages from the same
  logical workload batch after closure. Late messages are unverified observations,
  and do not enter the closed proposal. Already admitted prefixes replayed for
  ordinary forwarding are excluded from the after-close late count.

Modeled income = accrued relay rewards + accrued proposer rewards − actual
transaction payments (`F + g`). Pending reward settlement is not added again.
Extra operating and validation costs are unmodeled. The legacy `node_epoch`
net-income field and submitted-request fee counter are NOT used for this figure.
The legacy `conflicting_receipt_count` records requests rejected before receiving
signatures are created. These attempts are reported separately, not treated as
accepted conflicting signatures. The report checks consistency of explicit
receiving proofs in retained prefixes; final aggregate certificates are verified
by the Rust builder. Retained prefixes do not cover every receiving proof in
the entire simulation.
Next-epoch expected base proposer rewards are a separate diagnostic and are not
added to actual income. Main income covers attack + post epochs 10–79; warmup,
attack and post effects are also exported separately.

The figure's default Y axis is relative net-income change, in percent. For each
validated seed pair it computes `100 * (favoritism_income - honest_income) /
honest_income`, then plots the mean and a two-sided 95% Student-t interval across
those paired percentages. Every honest baseline must be finite and strictly
positive; an invalid denominator fails explicitly. This is the mean of per-pair
changes, rather than the ratio of the two scenario means. The denominator is
honest net income over epochs 10–79, not initial stake or gross rewards.
`points_percent.csv` and `paired_percent.csv` preserve the plotted statistics and
per-seed denominators. The underlying token-denominated report remains available;
`--metric token` regenerates that view. An explicitly labeled interim figure can
use `--allow-partial --input <snapshot>/summary.csv` without relaxing the default
requirement for all 20 pairs in a complete figure.

The report matches replication labels, placement and target stake, checking the
undirected topology and every validator's initial economic stake, along with
coalition identity, source and protocol parameters. Seeds are retained as metadata
and are not an extra validity requirement. Different initial proposers or background
realizations are recorded as diagnostics, not rejected. Stake comparisons tolerate
only rounding noise (absolute tolerance 1e-12), with exact validator identities.
Seed logic remains unchanged. Asynchronous delivery, wall-clock transaction
identities and election outcomes remain sources of across-run variation.

The report refuses a pair with no genuine attack-phase multi-path opportunity.
The main figure additionally requires effective favoritism in every scenario
and all 20 pairs per point. Zero opportunities are never presented as a zero-profit
security result. Per-epoch bounds apply to proposer probabilities, not finite
realized win frequencies.

## Prepared commands (not run automatically)

V2 suites are independent of the preserved V1 raw data and binary. Pilot has 12
runs, main has 240 runs; each has 80 epochs. Both use `time_scale=1`. The collection
window and all frozen parameters match across arms:
100 validators, BA, stake Gini 0.6, 20 tx/s, capacity 80, 1-second logical slots,
5 slots/epoch, D=4, beta=0.8, eta=0.5, theta=0.5, F=g=0.00001, subsidy=0.1,
no reward reinvestment, score activation after 2 epochs, settlement after 2 blocks.

```bash
python scripts/task.py run-config --config experiments/configs/trail_coalition_favoritism_pilot.yaml --dry-run
python scripts/task.py run-config --config experiments/configs/trail_coalition_favoritism_pilot.yaml
python experiments/trail_coalition_favoritism_report.py --config experiments/configs/trail_coalition_favoritism_pilot.yaml
```

Inspect opportunities, effective changes, matching external inputs, receipts,
and accounting before deciding whether the pilot supports the formal matrix.
If opportunities are absent, diagnose collection/propagation timing or extend
the observation period; do not alter seed logic or fabricate paths.

```bash
python scripts/task.py run-config --config experiments/configs/trail_coalition_favoritism_main.yaml --dry-run
python scripts/task.py run-config --config experiments/configs/trail_coalition_favoritism_main.yaml
python experiments/trail_coalition_favoritism_report.py --config experiments/configs/trail_coalition_favoritism_main.yaml
python analysis/plot_trail_coalition_favoritism.py
```

New audit files require a fresh run directory to avoid overwriting evidence.
Use a new run revision for a genuinely new execution; preserve old outputs.
Processed outputs go to `results/processed/trail_coalition_favoritism_{pilot,main}_v2`;
the new figure goes to `figures/trail_coalition_favoritism`.

The final security figure uses `Coalition Staking Share (%)` and
`Relative Net Income Change (%)` as its axis labels, with a dashed horizontal
reference at zero. Generate the PNG and PDF in the selected V2 directory with:

```bash
python analysis/plot_trail_coalition_favoritism.py --output-dir figures/trail_securityV2
```

The integration test uses ordinary node messages through two real relays to
deliver two independently signed branches of the SAME transaction to a proposer.
It verifies both policies' final certificates, single-receipt behavior and actual
reward plans. It is a small correctness test, not a pilot or formal experiment.
