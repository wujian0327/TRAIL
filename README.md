# TRAIL

TRAIL is a research artifact for studying propagation incentives whose effect
on Proof-of-Stake proposer selection remains bounded by real economic stake.
The repository contains:

- a Rust event-driven simulator and frozen-v1 protocol implementation;
- reproducible experiment configurations and raw-run validation tooling;
- modified Geth and Lighthouse clients for a real-client Ethereum devnet;
- the plotting scripts used for the current TRAIL paper figures.

## Environment

```bash
git clone -b codex/frozen-v1-security-eval \
  https://github.com/wujian0327/pog-rs.git
cd pog-rs

rustup update stable
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install matplotlib numpy pyyaml requests scipy web3
cargo build --release
python scripts/task.py test
```

## Simulator experiments

Inspect or run any retained experiment configuration through the common entry
point. Existing complete runs are reused unless `--force` is supplied.

```bash
python scripts/task.py run-config \
  --config experiments/configs/frozen_v1_security_main.yaml \
  --dry-run

python scripts/task.py run-config \
  --config experiments/configs/frozen_v1_security_main.yaml
```

The frozen smoke and security workflows have short aliases:

```bash
python scripts/task.py frozen-smoke
python scripts/task.py frozen-security-pilot --dry-run
python scripts/task.py frozen-security-main --dry-run
python scripts/task.py frozen-padding-check
```

## Current paper figures

The maintained figures are grouped under five output directories:

- `figures/trail_security/`
- `figures/trail_fairness/`
- `figures/trail_participation/`
- `figures/trail_outage/`
- `figures/trail_devnet/`

Regenerate an individual group or all five groups:

```bash
python scripts/task.py trail-security-figures
python scripts/task.py trail-fairness-figures
python scripts/task.py trail-participation-figures
python scripts/task.py trail-outage-figures
python scripts/task.py trail-devnet-figures

python scripts/task.py trail-figures
```

Relay-participation and transaction-flooding processed data can be rebuilt
without rerunning simulations:

```bash
python scripts/task.py trail-participation-report
python scripts/task.py trail-transaction-flooding-report
```

## Real-client Ethereum devnet

The devnet requires Docker, the Kurtosis CLI, and a local checkout of
`ethereum-package`. Build and package the modified clients with:

```bash
cd ethereum-trail/go-ethereum-trail
make geth
cd ../..
GETH_BINARY="$PWD/ethereum-trail/go-ethereum-trail/build/bin/geth" \
  ./scripts/geth_image.sh package-local

cd ethereum-trail/lighthouse
cargo build --release -p lighthouse --features spec-minimal
cd ../..
LIGHTHOUSE_BINARY="$PWD/ethereum-trail/lighthouse/target/release/lighthouse" \
  ./scripts/lighthouse_image.sh package-local
```

Validate and inspect the frozen devnet matrix before launching it:

```bash
python scripts/task.py frozen-devnet-check
python scripts/task.py frozen-devnet-pilot \
  --package /path/to/ethereum-package --dry-run
python scripts/task.py frozen-devnet-main \
  --package /path/to/ethereum-package --dry-run
```

Use `--resume` for completed partial matrices and `--stop-on-failure` for a
fail-fast run. Detailed client and acceptance semantics are documented in
`docs/FROZEN_V1_DEVNET.md`.

## Artifact layout

- `results/raw/<suite>/`: immutable per-run configs, logs, metrics and summaries;
- `results/processed/`: grouped statistics and consistency reports;
- `figures/trail_*/`: current PDF and PNG paper figures;
- `analysis/plot_trail_*.py`: maintained plotting implementations;
- `experiments/configs/`: retained experiment provenance and rerun matrices.

Historical raw and processed artifacts keep their original schema tokens. The
read-only compatibility layer in `experiments/trail_compat.py` maps them to the
current TRAIL names when they are loaded.
