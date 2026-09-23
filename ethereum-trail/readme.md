# Ethereum TRAIL Clients

This directory contains the Ethereum client forks used by the TRAIL devnet experiments.

## Layout

- `go-ethereum-trail/`: custom geth fork for TRAIL transaction path metadata, block evidence, fee escrow, and settlement execution.
- `lighthouse/`: custom Lighthouse fork for block-inline path records, evidence verification, score/proposer selection, and finalized reward settlement.

## Build

Build geth:

```bash
cd ethereum-trail/go-ethereum-trail
GOCACHE=/tmp/pog-go-build-cache GOTMPDIR=/tmp make geth
```

Build Lighthouse with the minimal spec:

```bash
cd ethereum-trail/lighthouse
cargo build --release --bin lighthouse --features spec-minimal
```

Package local Docker images from the repository root:

```bash
cd /path/to/pog-rs

GETH_BINARY=$PWD/ethereum-trail/go-ethereum-trail/build/bin/geth \
  ./scripts/geth_image.sh package-local

LIGHTHOUSE_BINARY=$PWD/ethereum-trail/lighthouse/target/release/lighthouse \
  ./scripts/lighthouse_image.sh package-local
```

## Notes

- These clients are devnet forks and are not intended to mix with stock clients.
- The current main experiment profile is `1 validator / node`, `minimal`, 3-second slots, and 16 slots per epoch.
- See `../kurtosis.md` for the protocol status and devnet experiment workflow.
