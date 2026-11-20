#!/usr/bin/env python3
"""Plot measured block path encodings and savings from the offline codec CSV."""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
from plot_trail_relay_participation import configure_style, plt
from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator


DATA = ROOT / "results/processed/block_validator_compression_v2/block_compression.csv"
OUTPUT = ROOT / "figures/trail_evidence/block_validator_compression_v2"
SCHEMES = {
    "baseline": ("Baseline 8B", "#6B6B6B", "^", ":"),
    "u32": ("Fixed 4B", "#0072B2", "s", "--"),
    "global": ("Global 1/4/8B", "#A95185", "P", "-"),
    "dictionary": ("Block Dictionary", "#009E73", "D", "-."),
    "adaptive": ("Adaptive", "#D55E00", "o", "None"),
}
COHORTS = (12, 100)
SYNTHETIC_N = (100, 1000, 4000)
HISTORICAL = "historical_rust_paths"
SYNTHETIC = "synthetic_paths"


def groups(rows, key):
    grouped = defaultdict(list)
    for row in rows:
        grouped[int(row[key])].append(row)
    if not grouped:
        raise ValueError(f"No data for {key}")
    return sorted(grouped.items())


def line(axis, x, y, scheme, *, every=1):
    name, color, marker, linestyle = SCHEMES[scheme]
    adaptive = scheme == "adaptive"
    axis.plot(x, y, label=name, color=color, marker=marker, linestyle=linestyle,
              linewidth=1.2, markersize=6.1 if adaptive else 3.8, markeredgewidth=0.85,
              markerfacecolor="none" if adaptive else "white", markevery=every)


def setup(ylabel, xlabel):
    configure_style()
    fig, axes = plt.subplots(1, 3, figsize=(7.16, 2.65))
    fig.subplots_adjust(left=0.10, right=0.98, bottom=0.255, top=0.76, wspace=0.38)
    for axis in axes:
        axis.set_axisbelow(True)
        axis.grid(axis="y", color="#E6E6E6", linewidth=0.7)
        axis.spines[["left", "bottom"]].set_linewidth(0.9)
        axis.tick_params(length=3, width=0.8)
    axes[0].set_ylabel(ylabel, fontsize=8.7)
    fig.text(0.54, 0.10, xlabel, ha="center", fontsize=9)
    return fig, axes


def historical_groups(rows, count, key):
    return groups([row for row in rows if row["data_source"] == HISTORICAL
                   and row["topology"].upper() == "BA" and int(row["validator_count"]) == count], key)


def synthetic_rows(rows, *, n=None, k=None):
    return [row for row in rows if row["data_source"] == SYNTHETIC and row["cohort"] in ("grid", "all_distinct")
            and float(row["mean_hops"]) == 4 and (n is None or int(row["tx_count"]) == n)
            and (k is None or int(row["unique_validator_count"]) == k)]


def titles(axis, letter, text):
    axis.set_title(f"({letter})  {text}", loc="left", fontsize=8.3, pad=6)


def render_storage(rows):
    fig, axes = setup("Path Evidence Storage (Bytes)", "Transactions per Block")
    for axis, count, letter in zip(axes[:2], COHORTS, "ab"):
        grouped = historical_groups(rows, count, "tx_count")
        xs = [point for point, _ in grouped]
        for scheme in SCHEMES:
            ys = [statistics.fmean(int(row[f"{scheme}_bytes"]) for row in bucket) for _, bucket in grouped]
            line(axis, xs, ys, scheme, every=2 if count == 12 else 6)
        titles(axis, letter, f"Historical BA, {count} Validators")
        axis.set_xticks((2, 5, 8, 11, 14) if count == 12 else (5, 15, 25, 35, 45))
        axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))

    axis = axes[2]
    points = sorted((row for row in rows if row["cohort"] == "roster_high"
                     and row["validator_count"] == "1000" and float(row["mean_hops"]) == 4),
                    key=lambda row: int(row["tx_count"]))
    xs = [int(point["tx_count"]) for point in points]
    if tuple(xs) != SYNTHETIC_N:
        raise ValueError("Missing synthetic scale points")
    for scheme in SCHEMES:
        line(axis, xs, [int(row[f"{scheme}_bytes"]) for row in points], scheme)
    titles(axis, "c", "Synthetic, 1,000 Validators")
    axis.text(0.98, 0.06, "4 hops, 64 high indices", transform=axis.transAxes,
              ha="right", va="bottom", fontsize=6.9)
    axis.set_xscale("log")
    axis.set_yscale("log")
    axis.set_xticks(SYNTHETIC_N, [f"{n:,}" for n in SYNTHETIC_N])
    axis.xaxis.set_minor_locator(NullLocator())
    axis.yaxis.set_major_locator(FixedLocator([5000, 20000, 100000, 400000]))
    axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
    axis.yaxis.set_minor_locator(NullLocator())
    axis.set_ylim(4100, 480000)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.54, 0.98),
               frameon=False, ncols=5, handlelength=1.7, columnspacing=1.3, fontsize=8)
    return fig


def render_savings(rows):
    fig, axes = setup("Savings vs. 8B Baseline (%)", "Unique Validator Indices per Block")
    columns = {"u32": "u32_saving_pct", "global": "global_saving_pct",
               "dictionary": "dictionary_saving_pct", "adaptive": "saving_pct"}
    for axis, count, letter in zip(axes[:2], COHORTS, "ab"):
        grouped = historical_groups(rows, count, "unique_validator_count")
        xs = [point for point, _ in grouped]
        savings = [[float(row["dictionary_saving_pct"]) for row in bucket] for _, bucket in grouped]
        axis.fill_between(xs, list(map(min, savings)), list(map(max, savings)),
                          color=SCHEMES["dictionary"][1], alpha=0.13, linewidth=0)
        for scheme, column in columns.items():
            ys = [statistics.fmean(float(row[column]) for row in bucket) for _, bucket in grouped]
            line(axis, xs, ys, scheme, every=1 if count == 12 else 6)
        titles(axis, letter, f"Historical BA, {count} Validators")
        axis.set_ylim(-9, 43)
        axis.set_yticks((0, 10, 20, 30, 40))
        axis.set_xticks((1, 3, 5, 7) if count == 12 else (10, 20, 30, 40, 50))

    axis = axes[2]
    for n, color, marker, linestyle in [(100, "#6B6B6B", "^", ":"), (1000, "#0072B2", "s", "--"),
                                        (4000, "#009E73", "D", "-")]:
        points = sorted(synthetic_rows(rows, n=n), key=lambda r: int(r["unique_validator_count"]))
        xs = [int(point["unique_validator_count"]) for point in points]
        if not points or xs[-1] != 4 * n:
            raise ValueError(f"Missing all-distinct endpoint for N={n}")
        axis.plot(xs, [float(row["dictionary_saving_pct"]) for row in points], color=color,
                  marker=marker, linestyle=linestyle, markerfacecolor="white", markersize=3.6,
                  linewidth=1.0, label=f"{n:,}")
    axis.set_xscale("log")
    axis.set_xticks((16, 64, 256, 1024, 4096, 16384), ("16", "64", "256", "1k", "4k", "16k"))
    axis.xaxis.set_minor_locator(NullLocator())
    axis.set_ylim(-15, 36)
    axis.set_yticks((-10, 0, 10, 20, 30))
    titles(axis, "c", "Synthetic Dictionary")
    synthetic_handles, synthetic_labels = axis.get_legend_handles_labels()
    fig.legend(synthetic_handles, synthetic_labels, loc="upper center", bbox_to_anchor=(0.854, 0.995),
               title="Paths per block, 4 hops", title_fontsize=7.1, ncols=3, frameon=False,
               handlelength=1.6, columnspacing=0.9, fontsize=7)
    for axis in axes:
        axis.axhline(0, color="#AAA", linewidth=0.8, zorder=1)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.375, 0.962),
               frameon=False, ncols=4, handlelength=1.5, columnspacing=1.1, fontsize=7.6)
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DATA)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    with args.input.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        candidates = ("baseline_bytes", "u8_bytes", "u32_candidate_bytes", "dictionary_bytes")
        if int(row["adaptive_bytes"]) != min(int(row[column]) for column in candidates if row[column]):
            raise ValueError("Adaptive result differs from the smallest serialized size")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for render, filename, title in [
        (render_storage, "fig_a_path_evidence_storage.pdf", "Path Evidence Storage under Different Encodings"),
        (render_savings, "fig_b_savings_vs_unique_validators.pdf", "Compression Savings vs. Unique Validators"),
    ]:
        fig = render(rows)
        path = args.output_dir / filename
        with path.open("xb") as output:
            fig.savefig(output, format="pdf", metadata={"Title": title, "CreationDate": None})
        plt.close(fig)
        print(path)


if __name__ == "__main__":
    main()
