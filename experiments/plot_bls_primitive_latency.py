#!/usr/bin/env python3
"""Plot standalone BLS signing and verification times with percentile caps."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
from plot_trail_relay_participation import configure_style, decorate, plt
from plot_trail_relay_participation_v2 import STYLES

SUMMARY = ROOT / "results/processed/bls_primitive_latency/summary.csv"
OUTPUT = ROOT / "figures/trail_overhead/bls_primitive_latency.pdf"
OPERATIONS = ("signing", "verification")


def read_summary(path: Path) -> dict[str, tuple[float, float, float]]:
    rows = {}
    with path.open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            operation = row["operation"]
            if operation in rows or int(row["warmups"]) != 50 or int(row["samples"]) < 1000:
                raise ValueError(f"Invalid benchmark row for {operation}")
            mean, p95, p99 = (float(row[key]) / 1000 for key in ("mean_us", "p95_us", "p99_us"))
            if mean <= 0 or not 0 < p95 <= p99:
                raise ValueError(f"Invalid latencies for {operation}")
            rows[operation] = mean, p95, p99
    if rows.keys() != set(OPERATIONS):
        raise ValueError("Expected one signing and one verification row")
    return rows


def render(summary: Path, output: Path) -> None:
    rows = read_summary(summary)
    means, p95, p99 = zip(*(rows[op] for op in OPERATIONS), strict=True)
    configure_style()
    fig, axis = plt.subplots(figsize=(3.45, 2.55))
    fig.subplots_adjust(left=0.16, right=0.97, bottom=0.17, top=0.96)
    x = range(len(OPERATIONS))
    axis.set_axisbelow(True)
    axis.bar(x, means, width=0.44, color=STYLES["Full TRAIL"]["color"],
             label="Mean", zorder=1)
    for quantiles, label, style, capsize, zorder in [
        (p95, "P95", "Ersoy", 3, 3),
        (p99, "P99", "PoS", 4, 2),
    ]:
        delta = [q - mean for q, mean in zip(quantiles, means, strict=True)]
        axis.errorbar(x, means, yerr=[[max(-d, 0) for d in delta], [max(d, 0) for d in delta]],
                      fmt="none", ecolor=STYLES[style]["color"], capsize=capsize, capthick=1,
                      elinewidth=0.9, label=label, zorder=zorder)
    axis.set_xticks(x, ["BLS Signing", "BLS Verification"])
    axis.set_xlim(-0.55, 1.55)
    axis.set_ylim(bottom=0)
    axis.set_ylabel("Time (ms)")
    decorate(axis)
    axis.get_legend().set_loc("upper left")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as file:
        fig.savefig(file, format="pdf", metadata={"Title": "BLS Signing and Verification Latency",
                                                  "CreationDate": None})
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, default=SUMMARY)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    render(args.summary, args.output)
