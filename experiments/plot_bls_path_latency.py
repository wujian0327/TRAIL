#!/usr/bin/env python3
"""Plot single-path BLS signing and verification latencies as one figure."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
from plot_trail_relay_participation import configure_style, decorate, plt
from plot_trail_relay_participation_v2 import STYLES

SUMMARY = ROOT / "results/processed/bls_path_latency/summary.csv"
OUTPUT = ROOT / "figures/trail_overhead/bls_signing_verification_latency.pdf"
HOPS = (1, 2, 4, 6, 8, 16)


def read_summary(source: Path) -> dict:
    rows = {}
    with source.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            h = int(row["hops"])
            key = row["operation"], h
            if (key in rows or int(row["hop_signatures"]) != 2 * h
                    or int(row["samples"]) < 1000 or int(row["warmups"]) != 50):
                raise ValueError(f"Invalid summary row for {key}")
            rows[key] = row
    expected = {(op, h) for op in ("signing_total", "bls_signing", "bls_aggregation", "bls_verification")
                for h in HOPS}
    if rows.keys() != expected:
        raise ValueError("Incomplete latency summary")
    return rows


def latency_ms(rows, operation, field):
    return [float(rows[operation, h][field]) / 1000 for h in HOPS]


def render(summary: Path, output: Path) -> None:
    rows = read_summary(summary)
    if any(float(rows["bls_verification", h]["p95_us"]) > float(rows["bls_verification", h]["p99_us"])
           for h in HOPS):
        raise ValueError("Inconsistent verification percentiles")
    configure_style()
    fig, axis = plt.subplots(figsize=(3.45, 2.55))
    fig.subplots_adjust(left=0.15, right=0.97, bottom=0.19, top=0.965)
    signing_mean = latency_ms(rows, "signing_total", "mean_us")
    verification_mean = latency_ms(rows, "bls_verification", "mean_us")
    for values, label, reference in [
        (signing_mean, "Signing + aggregation", "Full TRAIL"),
        (verification_mean, "Aggregate verification", "Fee-only"),
    ]:
        axis.plot(HOPS, values, **STYLES[reference], label=label, linewidth=1.2,
                  markerfacecolor="white", markersize=4.6, zorder=4)
    for label, field, reference, capsize, zorder in [
        ("P95", "p95_us", "Ersoy", 2.4, 3),
        ("P99", "p99_us", "PoS", 3.0, 2),
    ]:
        quantiles = latency_ms(rows, "bls_verification", field)
        delta = [q - mean for q, mean in zip(quantiles, verification_mean, strict=True)]
        yerr = [[max(-d, 0) for d in delta], [max(d, 0) for d in delta]]
        axis.errorbar(HOPS, verification_mean, yerr=yerr, fmt="none", label=label,
                      ecolor=STYLES[reference]["color"], capsize=capsize, capthick=0.9,
                      elinewidth=0.9, zorder=zorder)
    axis.set_xlabel("Relay Hops")
    axis.set_ylabel("Time (ms)")
    axis.set_xticks(HOPS)
    axis.set_xlim(0.3, 16.8)
    axis.set_ylim(bottom=0)
    decorate(axis)
    axis.get_legend().set_loc("upper left")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as handle:
        fig.savefig(handle, format="pdf", metadata={
            "Title": "BLS Signing and Verification Latency vs. Relay Hops", "CreationDate": None,
        })
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, default=SUMMARY)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    render(args.summary, args.output)
