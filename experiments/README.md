# TRAIL experiment tooling

This directory contains the experiment configurations, runners, validation
code, and processors used by the maintained TRAIL figures. The repository root
[`README.md`](../README.md) is the canonical reproduction guide.

## Maintained experiment groups

- Security: proposer influence/scaling, transaction flooding, and path checks.
- Participation: adaptive relay participation and inclusion time.
- Fairness: organic reward concentration and stake reinvestment.
- Outage resilience: sustained validator outages and score persistence.
- Devnet: Ethereum PoS and Full TRAIL real-client measurements.

The final configurations and development pilots remain in `configs/` so that
existing raw artifacts retain their provenance. `configs/protocol_frozen_v1.yaml`
contains shared frozen-v1 protocol parameters.

## Entry points

Run a simulator configuration without adding a dedicated task:

```bash
python3 scripts/task.py run-config \
  --config experiments/configs/frozen_v1_security_main.yaml \
  --dry-run
```

Generate all maintained paper figures:

```bash
python3 scripts/task.py trail-figures
```

Individual figure groups are available as:

```bash
python3 scripts/task.py trail-security-figures
python3 scripts/task.py trail-participation-figures
python3 scripts/task.py trail-fairness-figures
python3 scripts/task.py trail-outage-figures
python3 scripts/task.py trail-devnet-figures
```

Use `python3 scripts/task.py --help` for experiment, reporting, smoke-test, and
devnet task options.

## Layout

- `configs/`: final matrices plus retained pilot/probe provenance.
- `run_experiments.py`: generic Rust-simulator matrix runner.
- `trail_devnet_runner.py` and `run_frozen_devnet_*`: devnet execution tools.
- `trail_*_report.py`: processors still required by maintained figures.
- `tests/`: tests for active runners, processors, and figure conventions.
- `results/raw/`: immutable per-run artifacts.
- `results/processed/`: regenerated tables and reports.

Raw runs are not modified by the reporting and plotting tasks. Use `--force`
only when intentionally replacing simulator output, and `--resume` for an
interrupted devnet matrix.
