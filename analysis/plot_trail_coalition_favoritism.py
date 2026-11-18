#!/usr/bin/env python3
"""Plot validated paired Fig. 4(b) results; never fills missing scenarios."""
import argparse
import csv
from collections import defaultdict
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import statistics

os.environ.setdefault("MPLCONFIGDIR", "/tmp/trail-matplotlib")
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
from scipy.stats import t


ROOT = Path(__file__).resolve().parents[1]


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def percentage_points(directory, points):
    """Normalize each validated pair by its own positive honest net income."""
    arms = defaultdict(dict)
    for row in read_csv(directory / "runs.csv"):
        key = float(row["adversary_stake_fraction"]), row["adversary_placement"], int(row["seed_value"])
        mode = row["coalition_path_selection"]
        if mode not in {"honest", "favoritism"} or mode in arms[key]:
            raise ValueError(f"unexpected or duplicate selection arm: {key}, {mode}")
        arms[key][mode] = float(row["total_modeled_net_income"])
    normalized = []
    seen = set()
    groups = defaultdict(list)
    for pair in read_csv(directory / "paired.csv"):
        key = float(pair["target_stake"]), pair["placement"], int(pair["seed_value"])
        if key in seen or set(arms[key]) != {"honest", "favoritism"}:
            raise ValueError(f"duplicate or incomplete validated pair: {key}")
        seen.add(key)
        honest, favoritism = arms[key]["honest"], arms[key]["favoritism"]
        if not math.isfinite(honest) or not math.isfinite(favoritism) or honest <= 0:
            raise ValueError(f"percentage requires finite income and a positive honest baseline: {key}")
        delta = favoritism - honest
        if not math.isclose(delta, float(pair["total_delta_modeled_net_income"]), rel_tol=0, abs_tol=1e-10):
            raise ValueError(f"net income does not reconcile with validated paired data: {key}")
        percent = 100 * delta / honest
        groups[key[:2]].append(percent)
        normalized.append(dict(target_stake=key[0], placement=key[1], seed_value=key[2],
                               honest_net_income_token=honest, favoritism_net_income_token=favoritism,
                               delta_net_income_token=delta, relative_net_income_change_pct=percent))
    expected = {(float(p["target_stake"]), p["placement"]) for p in points}
    if set(groups) - expected:
        raise ValueError("paired data contain an unexpected scenario")
    exported = []
    plotted = []
    for point in points:
        values = groups[float(point["target_stake"]), point["placement"]]
        n = int(point["n"])
        if len(values) != n:
            raise ValueError("percentage data must use exactly the validated paired seeds")
        mean = statistics.fmean(values) if n else None
        half = float(t.ppf(0.975, n - 1)) * statistics.stdev(values) / math.sqrt(n) if n >= 2 else None
        plotted.append(dict(point, mean=mean, ci95=half))
        exported.append(dict(target_stake=point["target_stake"], placement=point["placement"],
                             paired_seeds=n, mean_pct=mean, ci95_halfwidth_pct=half,
                             ci95_low_pct=mean-half if half is not None else None,
                             ci95_high_pct=mean+half if half is not None else None))
    write_csv(directory / "paired_percent.csv", normalized)
    write_csv(directory / "points_percent.csv", exported)
    return plotted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "results/processed/trail_coalition_favoritism_main_v2/points.csv")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "figures/trail_coalition_favoritism")
    parser.add_argument("--metric", choices=("percent", "token"), default="percent",
                        help="Percent is the mean of per-pair changes relative to honest net income")
    parser.add_argument("--allow-partial", action="store_true", help="Explicitly label an interim snapshot")
    parser.add_argument("--output-name", default="trail_coalition_favoritism")
    args = parser.parse_args()
    if Path(args.output_name).name != args.output_name:
        raise SystemExit("output-name must be a filename stem")
    rows = read_csv(args.input)
    expected = {(placement, stake) for placement in ("random", "high-betweenness") for stake in (0.1, 0.2, 0.3)}
    if len(rows) != 6 or {(r["placement"], float(r["target_stake"])) for r in rows} != expected:
        raise SystemExit("Fig. 4(b) requires all six validated main scenarios")
    rows = [dict(r, n=int(r.get("n", r.get("paired_seeds", 0))),
                 effective=r.get("effective", r.get("changed", "")),
                 mean=r.get("mean", r.get("net_income_difference_mean", "")),
                 ci95=r.get("ci95", r.get("ci95_halfwidth", ""))) for r in rows]
    for row in rows:
        n = row["n"]
        if not 0 <= n <= 20 or (not args.allow_partial and n != 20):
            raise SystemExit("Fig. 4(b) requires 20 paired seeds; partial plots need --allow-partial")
        if n and (int(row["eligible"]) <= 0 or int(row["effective"]) <= 0):
            raise SystemExit("genuine effective path-selection opportunities are required")
    if args.metric == "percent":
        rows = percentage_points(args.input.parent, rows)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9.5,
                         "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
                         "legend.fontsize": 7, "pdf.fonttype": 42, "ps.fonttype": 42})
    fig, ax = plt.subplots(figsize=(4.2, 3.4) if args.allow_partial else (3.6, 2.7))
    for placement, label, color, marker in [("random", "Random", "#0072B2", "o"),
                                            ("high-betweenness", "High-betweenness", "#D55E00", "s")]:
        group = sorted((r for r in rows if r["placement"] == placement), key=lambda r: float(r["target_stake"]))
        ax.errorbar([100 * float(r["target_stake"]) for r in group],
                    [float(r["mean"]) if r["n"] else math.nan for r in group],
                    yerr=[float(r["ci95"]) if r["n"] >= 2 else math.nan for r in group], color=color, marker=marker,
                    markerfacecolor="white", markersize=4, linewidth=1, capsize=2.5, label=label)
    ax.axhline(0, color="#666666", linewidth=0.7, linestyle="--")
    ax.set_xlabel("Coalition Staking Share (%)")
    if args.metric == "percent":
        ax.set_ylabel("Relative Net Income Change (%)")
        ax.yaxis.set_major_formatter(PercentFormatter(xmax=100, decimals=0))
    else:
        ax.set_ylabel("Incremental coalition net income (token)")
    ax.set_xticks([10, 20, 30])
    ax.set_xlim(8, 32)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#e5e5e5", linewidth=0.6)
    ax.set_axisbelow(True)
    ax.legend(frameon=True, edgecolor="#bbbbbb")
    if args.allow_partial:
        metadata = json.loads((args.input.parent / "snapshot_metadata.json").read_text())
        time = datetime.fromisoformat(metadata["snapshot_at"])
        footer = ["Interim results | " + time.strftime("%d %b %Y, %H:%M") + " CST",
                  "Paired seeds at 10%, 20%, 30%; error bars: 95% t CI"]
        for placement, label in [("random", "Random"), ("high-betweenness", "High-betweenness")]:
            group = sorted((r for r in rows if r["placement"] == placement), key=lambda r: float(r["target_stake"]))
            footer.append(label + ": " + ", ".join(f"{r['n']}/20" if r["n"] else "pending" for r in group))
        fig.text(0.16, 0.025, "\n".join(footer), fontsize=7, va="bottom")
        fig.tight_layout(rect=(0, 0.20, 1, 1))
    else:
        fig.tight_layout()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for suffix in ["pdf", "png"]:
        fig.savefig(args.output_dir / f"{args.output_name}.{suffix}", dpi=300, bbox_inches="tight")
    plt.close(fig)
    provenance = dict(metric=args.metric, source=str(args.input.resolve()),
        x_label=ax.get_xlabel(), y_label=ax.get_ylabel(),
        reference_line=dict(y=0, linestyle="--"),
        source_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(), interim=args.allow_partial,
        percentage_input_sha256={name: hashlib.sha256((args.input.parent / name).read_bytes()).hexdigest()
                                 for name in ("runs.csv", "paired.csv")} if args.metric == "percent" else None,
        definition="mean over paired seeds of 100 * (favoritism net income - honest net income) / honest net income"
                   if args.metric == "percent" else "mean paired net income difference in token",
        period="epochs 10-79", ci_method="two-sided 95% Student t over paired-seed values",
        points=rows)
    (args.input.parent / f"{args.output_name}_metadata.json").write_text(json.dumps(provenance, indent=2) + "\n")


if __name__ == "__main__":
    main()
