#!/usr/bin/env python3
"""Plot the five-seed PoS/TRAIL devnet load sweep in tx/slot units."""

from __future__ import annotations

import csv
import json
import math
import os
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/trail-matplotlib")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.stats import sem, t

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "figures" / "trail_devnet"
RUN_TABLE = ROOT / "results" / "processed" / "trail_devnet_txslot_runs.csv"
GROUP_TABLE = ROOT / "results" / "processed" / "trail_devnet_txslot_groups.csv"
SECONDS_PER_SLOT = 3.0
LOADS = (64, 128, 256, 512)
SEEDS = range(5)


def run_dir(variant: str, offered: int, seed: int) -> Path:
    suite = "trail_devnet_512_main" if offered == 512 else "trail_devnet_txslot_main"
    return ROOT / "results" / "raw" / suite / f"{variant}_ba_n8_load{offered}_seed{seed}"


def load_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for variant, mechanism in (("baseline", "Ethereum PoS"), ("trail", "Full TRAIL")):
        for offered in LOADS:
            for seed in SEEDS:
                directory = run_dir(variant, offered, seed)
                status = json.loads((directory / "runner_status.json").read_text())
                acceptance = json.loads((directory / "acceptance.json").read_text())
                summary = json.loads((directory / "summary.json").read_text())
                quality = summary.get("formal_experiment", {}).get("measurement_quality", {})
                # The formal 512 report retained this run after confirming that its sole
                # failed check was an after-run Beacon/Geth epoch sampling race.
                adjudicated = offered == 512 and variant == "trail" and seed == 1
                if status.get("status") != "ok" or not acceptance.get("passed"):
                    failed = [c.get("name") for c in acceptance.get("checks", []) if not c.get("passed")]
                    if not (adjudicated and failed == ["geth-relay-epoch-current"]):
                        raise ValueError(f"invalid run: {directory} ({status.get('status')}, {failed})")
                if not quality.get("passed"):
                    raise ValueError(f"measurement-quality failure: {directory}")
                workload = summary["workload"]
                success = int(workload["success_count"])
                tx_count = int(workload["tx_count"])
                actual_send_tps = float(workload["actual_send_tps"])
                throughput_tps = float(workload["inclusion_throughput_tps"])
                p95_seconds = float(workload["inclusion_delay_seconds"]["p95"])
                values = (actual_send_tps, throughput_tps, p95_seconds)
                if success != tx_count or not all(math.isfinite(value) for value in values):
                    raise ValueError(f"incomplete or non-finite workload: {directory}")
                rows.append({
                    "mechanism": mechanism, "variant": variant, "seed": seed,
                    "offered_tx_per_slot": offered,
                    "target_tx_per_second": offered / SECONDS_PER_SLOT,
                    "actual_sent_tx_per_slot": actual_send_tps * SECONDS_PER_SLOT,
                    "included_tx_per_slot": throughput_tps * SECONDS_PER_SLOT,
                    "p95_inclusion_seconds": p95_seconds,
                    "success_count": success, "target_transaction_count": tx_count,
                    "acceptance_adjudicated": adjudicated, "run_dir": str(directory),
                })
    return sorted(rows, key=lambda r: (str(r["mechanism"]), int(r["offered_tx_per_slot"]), int(r["seed"])))


def ci95(values: list[float]) -> tuple[float, float]:
    mean = sum(values) / len(values)
    return mean, float(t.ppf(0.975, len(values) - 1) * sem(values))


def group_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, int], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["mechanism"]), int(row["offered_tx_per_slot"]))].append(row)
    output: list[dict[str, object]] = []
    for (mechanism, offered), sample in sorted(grouped.items()):
        included_mean, included_ci = ci95([float(r["included_tx_per_slot"]) for r in sample])
        sent_mean, sent_ci = ci95([float(r["actual_sent_tx_per_slot"]) for r in sample])
        p95_mean, p95_ci = ci95([float(r["p95_inclusion_seconds"]) for r in sample])
        output.append({
            "mechanism": mechanism, "offered_tx_per_slot": offered, "n": len(sample),
            "actual_sent_tx_per_slot_mean": sent_mean, "actual_sent_tx_per_slot_ci95": sent_ci,
            "included_tx_per_slot_mean": included_mean, "included_tx_per_slot_ci95": included_ci,
            "p95_inclusion_seconds_mean": p95_mean, "p95_inclusion_seconds_ci95": p95_ci,
        })
    return output


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def plot(groups: list[dict[str, object]]) -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 8.5, "axes.labelsize": 10.5,
        "legend.fontsize": 8.5, "xtick.labelsize": 8.0, "ytick.labelsize": 8.0,
        "axes.spines.top": False, "axes.spines.right": False,
        "pdf.fonttype": 42, "ps.fonttype": 42,
    })
    styles = {
        "Ethereum PoS": dict(color="#666666", linestyle="--", marker="^", markerfacecolor="white"),
        "Full TRAIL": dict(color="#D55E00", linestyle="-", marker="o", markerfacecolor="#D55E00"),
    }
    fig, axis = plt.subplots(figsize=(3.45, 2.55))
    for mechanism in ("Ethereum PoS", "Full TRAIL"):
        series = [row for row in groups if row["mechanism"] == mechanism]
        style = styles[mechanism]
        axis.errorbar(
            [float(row["offered_tx_per_slot"]) for row in series],
            [float(row["included_tx_per_slot_mean"]) for row in series],
            yerr=[float(row["included_tx_per_slot_ci95"]) for row in series],
            label=mechanism, color=style["color"], linestyle=style["linestyle"],
            marker=style["marker"], markerfacecolor=style["markerfacecolor"],
            markeredgecolor=style["color"], markeredgewidth=1.1, linewidth=1.3,
            markersize=5.0, capsize=2.2, elinewidth=1.0, zorder=3,
        )
    axis.set_xticks(LOADS)
    axis.set_yticks([0, 64, 128, 192, 256, 320, 384])
    axis.set_xlim(35, 535)
    axis.set_ylim(0, 430)
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
    fig.savefig(OUTPUT_DIR / "trail_devnet_txslot.pdf", facecolor="white")
    fig.savefig(OUTPUT_DIR / "trail_devnet_txslot.png", dpi=300, facecolor="white")
    plt.close(fig)


def main() -> None:
    rows = load_rows()
    groups = group_rows(rows)
    if len(rows) != 40 or any(int(row["n"]) != 5 for row in groups):
        raise ValueError(f"expected 40 runs and n=5 per point, got {len(rows)} runs")
    write_csv(RUN_TABLE, rows)
    write_csv(GROUP_TABLE, groups)
    plot(groups)
    print(f"wrote {RUN_TABLE}")
    print(f"wrote {GROUP_TABLE}")
    print(f"wrote {OUTPUT_DIR / 'trail_devnet_txslot.pdf'}")
    print(f"wrote {OUTPUT_DIR / 'trail_devnet_txslot.png'}")


if __name__ == "__main__":
    main()
