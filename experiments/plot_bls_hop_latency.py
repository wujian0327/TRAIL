#!/usr/bin/env python3
"""Plot TRAIL hop certificate latency or the standalone same-message benchmark."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from statistics import fmean, stdev

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
from plot_trail_relay_participation import configure_style, decorate, plt
from plot_trail_relay_participation_v2 import STYLES

SAMPLES = ROOT / "results/processed/bls_hop_latency/samples.csv"
OUTPUT = ROOT / "figures/trail_overhead/bls_hop_latency.pdf"
SAME_MESSAGE_SAMPLES = ROOT / "results/processed/bls_same_message_latency/samples.csv"
SAME_MESSAGE_OUTPUT = ROOT / "figures/trail_overhead/bls_same_message_latency.pdf"
HOPS = (1, 2, 4, 6, 8, 16)
OPERATIONS = {
    "signing_total": ("Signing + aggregation", "Full TRAIL"),
    "bls_verification": ("BLS aggregate verification", "Fee-only"),
}
SAME_MESSAGE_OPERATIONS = {
    "signing": ("BLS signing", "Full TRAIL"),
    "aggregate_verification": ("BLS fast aggregate verification", "Fee-only"),
}


def read_samples(source: Path, operations: dict[str, tuple[str, str]]) -> dict[tuple[str, int], list[int]]:
    samples = {}
    with source.open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            key = row["operation"], int(row["hops"])
            if key[0] not in operations:
                continue
            times = samples.setdefault(key, [])
            elapsed = int(row["elapsed_ns"])
            if int(row["sample"]) != len(times) + 1 or elapsed <= 0:
                raise ValueError(f"Invalid sample sequence or elapsed time for {key}")
            times.append(elapsed)
    if samples.keys() != {(operation, h) for operation in operations for h in HOPS}:
        raise ValueError("Expected both operations at every configured hop count")
    for times in samples.values():
        if len(times) < 1000:
            raise ValueError("Expected at least 1000 samples per operation and hop count")
    return samples


def render(source: Path, output: Path, *, same_message: bool = False) -> None:
    if output.suffix.lower() != ".pdf":
        raise ValueError("Expected a .pdf output path")
    png = output.with_suffix(".png")
    for path in (output, png):
        if path.exists():
            raise FileExistsError(path)

    operations = SAME_MESSAGE_OPERATIONS if same_message else OPERATIONS
    samples = read_samples(source, operations)
    configure_style()
    fig, axis = plt.subplots(figsize=(3.45, 2.55))
    fig.subplots_adjust(left=0.15, right=0.97, bottom=0.19, top=0.965)
    axis.set_axisbelow(True)

    for operation, (label, style) in operations.items():
        means = [fmean(samples[operation, hop]) / 1_000_000 for hop in HOPS]
        deviations = [stdev(samples[operation, hop]) / 1_000_000 for hop in HOPS]
        axis.errorbar(HOPS, means, yerr=deviations, **STYLES[style], label=label,
                      linewidth=1.2, markerfacecolor="white", markersize=4.6,
                      capsize=2.6, capthick=0.9, elinewidth=0.9, zorder=3)

    axis.set_xlabel("Signers" if same_message else "Relay Hops")
    axis.set_ylabel("Time (ms)")
    axis.set_xticks(HOPS)
    axis.set_xlim(0.3, 16.8)
    axis.set_ylim(bottom=0)
    decorate(axis)
    axis.get_legend().set_loc("upper left")
    output.parent.mkdir(parents=True, exist_ok=True)
    title = ("BLS Same-message Fast Aggregate Latency" if same_message
             else "TRAIL Hop Certificate BLS Latency")
    with output.open("xb") as file:
        fig.savefig(file, format="pdf", metadata={
            "Title": title,
            "CreationDate": None,
        })
    with png.open("xb") as file:
        fig.savefig(file, format="png", dpi=300)
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--same-message", action="store_true",
                        help="Plot the standalone same-message fast aggregate run")
    parser.add_argument("--samples", type=Path)
    parser.add_argument("--output", type=Path,
                        help="PDF path, with a PNG written alongside it")
    args = parser.parse_args()
    samples = args.samples or (SAME_MESSAGE_SAMPLES if args.same_message else SAMPLES)
    output = args.output or (SAME_MESSAGE_OUTPUT if args.same_message else OUTPUT)
    render(samples, output, same_message=args.same_message)
