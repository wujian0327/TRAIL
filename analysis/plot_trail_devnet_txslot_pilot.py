#!/usr/bin/env python3
"""Plot the seed-0 PoS/TRAIL high-load pilot in tx/slot units."""

from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/trail-matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "figures" / "trail_devnet"
TABLE_PATH = ROOT / "results" / "processed" / "trail_devnet_txslot_seed0.csv"
SECONDS_PER_SLOT = 3.0

RUNS = {
    ("Ethereum PoS", 64): ROOT / "results/raw/frozen_v1_devnet_main/baseline_ba_n8_load64_seed0",
    ("Ethereum PoS", 128): ROOT / "results/raw/trail_devnet_high_load_pilot/baseline_ba_n8_load128_seed0",
    ("Ethereum PoS", 256): ROOT / "results/raw/trail_devnet_high_load_pilot/baseline_ba_n8_load256_seed0",
    ("Ethereum PoS", 512): ROOT / "results/raw/trail_devnet_high_load_pilot/baseline_ba_n8_load512_seed0",
    ("Full TRAIL", 64): ROOT / "results/raw/trail_devnet_load64_128_trail_pilot/topostake_ba_n8_load64_seed0",
    ("Full TRAIL", 128): ROOT / "results/raw/trail_devnet_load64_128_trail_pilot/topostake_ba_n8_load128_seed0",
    ("Full TRAIL", 256): ROOT / "results/raw/trail_devnet_load256_trail_pilot/topostake_ba_n8_load256_seed0",
    ("Full TRAIL", 512): ROOT / "results/raw/trail_devnet_load512_trail_pilot/topostake_ba_n8_load512_seed0",
}


def load_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for (mechanism, offered), run_dir in RUNS.items():
        with (run_dir / "runner_status.json").open(encoding="utf-8") as handle:
            status = json.load(handle)
        with (run_dir / "acceptance.json").open(encoding="utf-8") as handle:
            acceptance = json.load(handle)
        with (run_dir / "summary.json").open(encoding="utf-8") as handle:
            summary = json.load(handle)
        if status.get("status") != "ok" or not acceptance.get("passed"):
            raise ValueError(f"invalid run: {run_dir}")
        formal = summary.get("formal_experiment", {})
        if not formal.get("measurement_quality", {}).get("passed"):
            raise ValueError(f"measurement-quality failure: {run_dir}")
        workload = summary["workload"]
        success = int(workload["success_count"])
        tx_count = int(workload["tx_count"])
        actual_send_tps = float(workload["actual_send_tps"])
        throughput_tps = float(workload["inclusion_throughput_tps"])
        p95_seconds = float(workload["inclusion_delay_seconds"]["p95"])
        values = (actual_send_tps, throughput_tps, p95_seconds)
        if success != tx_count or not all(math.isfinite(value) for value in values):
            raise ValueError(f"incomplete or non-finite workload: {run_dir}")
        rows.append(
            {
                "mechanism": mechanism,
                "seed": 0,
                "offered_tx_per_slot": offered,
                "target_tx_per_second": offered / SECONDS_PER_SLOT,
                "actual_sent_tx_per_slot": actual_send_tps * SECONDS_PER_SLOT,
                "included_tx_per_slot": throughput_tps * SECONDS_PER_SLOT,
                "p95_inclusion_seconds": p95_seconds,
                "success_count": success,
                "run_dir": str(run_dir),
            }
        )
    return sorted(rows, key=lambda row: (str(row["mechanism"]), int(row["offered_tx_per_slot"])))


def write_table(rows: list[dict[str, object]]) -> None:
    TABLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TABLE_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def plot(rows: list[dict[str, object]]) -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8.5,
            "axes.labelsize": 10.5,
            "legend.fontsize": 8.5,
            "xtick.labelsize": 8.0,
            "ytick.labelsize": 8.0,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    styles = {
        "Ethereum PoS": dict(color="#666666", linestyle="--", marker="^", markerfacecolor="white"),
        "Full TRAIL": dict(color="#D55E00", linestyle="-", marker="o", markerfacecolor="#D55E00"),
    }
    fig, axis = plt.subplots(figsize=(3.45, 2.55))
    for mechanism in ("Ethereum PoS", "Full TRAIL"):
        series = [row for row in rows if row["mechanism"] == mechanism]
        style = styles[mechanism]
        axis.plot(
            [float(row["offered_tx_per_slot"]) for row in series],
            [float(row["included_tx_per_slot"]) for row in series],
            label=mechanism,
            color=style["color"],
            linestyle=style["linestyle"],
            marker=style["marker"],
            markerfacecolor=style["markerfacecolor"],
            markeredgecolor=style["color"],
            markeredgewidth=1.1,
            linewidth=1.3,
            markersize=5.0,
            zorder=3,
        )
    ticks = [64, 128, 256, 512]
    axis.set_xticks(ticks)
    axis.set_yticks([0, 64, 128, 192, 256, 320])
    axis.set_xlim(35, 535)
    axis.set_ylim(0, 345)
    axis.set_xlabel("Offered load (tx/slot)")
    axis.set_ylabel("Throughput (tx/slot)")
    axis.grid(axis="y", color="#E6E6E6", linewidth=0.7, zorder=0)
    axis.spines["left"].set_linewidth(0.9)
    axis.spines["bottom"].set_linewidth(0.9)
    axis.tick_params(width=0.8, length=3)
    axis.legend(frameon=True, framealpha=0.92, facecolor="white", edgecolor="#D0D0D0",
                borderpad=0.35, handletextpad=0.5, loc="upper left")
    fig.subplots_adjust(left=0.19, right=0.97, bottom=0.20, top=0.97)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT_DIR / "trail_devnet_txslot_seed0.pdf", facecolor="white")
    fig.savefig(OUTPUT_DIR / "trail_devnet_txslot_seed0.png", dpi=300, facecolor="white")
    plt.close(fig)


def main() -> None:
    rows = load_rows()
    write_table(rows)
    plot(rows)
    print(f"wrote {TABLE_PATH}")
    print(f"wrote {OUTPUT_DIR / 'trail_devnet_txslot_seed0.pdf'}")
    print(f"wrote {OUTPUT_DIR / 'trail_devnet_txslot_seed0.png'}")


if __name__ == "__main__":
    main()
