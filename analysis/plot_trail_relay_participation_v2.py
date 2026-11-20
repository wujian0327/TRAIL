#!/usr/bin/env python3
"""Export the 10/20-seed four-panel study in trail_participation's style.

Read reviewed stake means and pointwise 95% Student-t intervals from plot_data.csv.
For active relay percentages, recompute both statistics from the same runs.
An explicit Fee-only exclusion option provides a labeled panel-b sensitivity
preview, recomputing that series from the remaining seed rows at every fee.
The shared exclusion option uses the same seed subset for all mechanisms while
preserving figure text for layout previews; record exclusions alongside exports.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import defaultdict
from functools import cache
from pathlib import Path

import plot_trail_relay_participation as original
from scipy.stats import t as student_t

plt = original.plt
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "results/processed/trail_four_mechanisms_20seeds_v2/plot_data.csv"
OUTPUT = ROOT / "figures/trail_participationV2"
PROTOCOLS = {
    "pos": "PoS",
    "trail_eta0": "Fee-only",
    "ersoy_fee": "Ersoy",
    "trail": "Full TRAIL",
}
STYLES = {
    **original.STYLES,
    "Ersoy": {"color": "#009E73", "marker": "D", "linestyle": "-."},
}


def seed_values(
    data: list[dict[str, str]], panels: str, *, active_relays: bool,
) -> dict[tuple[str, str, float], dict[int, float]]:
    @cache
    def relay_percentages(source):
        with (ROOT / source / "adaptive_relay_metrics.csv").open(newline="", encoding="utf-8") as handle:
            epochs = list(csv.DictReader(handle))
        if [int(row["epoch"]) for row in epochs] != list(range(200)):
            raise ValueError(f"{source}: expected epochs 0–199")
        active = [100 * float(row["active_fraction"]) for row in epochs]
        stakes = [100 * float(row["active_stake_share"]) for row in epochs]
        if not all(0 <= value <= 100 for value in active + stakes):
            raise ValueError(f"{source}: invalid relay percentages")
        steady = statistics.mean(active[100:]), statistics.mean(stakes[100:])
        return active, stakes, steady

    values = defaultdict(dict)
    for row in data:
        panel = row["panel"]
        if row["kind"] != "seed" or panel not in panels:
            continue
        x = float(row["x"])
        value = float(row["y"])
        if active_relays and panel != "d":
            source = row["raw_source"]
            active, stakes, steady = relay_percentages(source)
            active_pct, stake = (active[int(x)], stakes[int(x)]) if panel == "a" else steady
            if not math.isclose(stake, value, rel_tol=0, abs_tol=1e-8):
                raise ValueError(f"{source}: relay stake does not match the reviewed point at {x}")
            value = active_pct
        samples = values[panel, row["protocol"], x]
        seed = int(row["seed"])
        if seed in samples:
            raise ValueError(f"{panel}/{row['protocol']} at {x}: duplicate seed {seed}")
        samples[seed] = value
    return values


def render(data_file: Path, output: Path, *, panels: str = "abcd",
           fee_only_excluded_seeds: tuple[int, ...] = (),
           excluded_seeds: tuple[int, ...] = (), active_relays: bool = False) -> list[Path]:
    with data_file.open(newline="", encoding="utf-8") as handle:
        data = list(csv.DictReader(handle))
    seeds = sorted({int(row["seed"]) for row in data if row["kind"] == "seed"})
    if len(seeds) not in (10, 20) or seeds != list(range(200, 200 + len(seeds))):
        raise ValueError(f"expected study seeds 200-209 or 200-219, got {seeds}")
    excluded = set(fee_only_excluded_seeds)
    shared_excluded = set(excluded_seeds)
    for selection in [shared_excluded, excluded | shared_excluded]:
        if not selection.issubset(seeds) or len(seeds) - len(selection) < 2:
            raise ValueError("exclusions must be study seeds and leave at least two seeds")
    if not panels or set(panels) - set("abcd"):
        raise ValueError("panels must select a, b, c, or d")
    if excluded and "b" not in panels:
        raise ValueError("Fee-only exclusions apply only to panel b")
    by_seed = seed_values(data, panels, active_relays=active_relays)
    participation_label = "Active relays (%)" if active_relays else "Active relay stake (%)"
    original.configure_style()
    outputs = []
    for panel, expected_points in [("a", 200), ("b", 5), ("c", 3), ("d", 3)]:
        if panel not in panels:
            continue
        fig, ax = plt.subplots(figsize=(3.45, 2.55))
        upper_limits = []
        for protocol, label in PROTOCOLS.items():
            points = sorted(
                (row for row in data if row["panel"] == panel
                 and row["kind"] == "mean" and row["protocol"] == protocol),
                key=lambda row: float(row["x"]),
            )
            if len(points) != expected_points:
                raise ValueError(f"{panel}/{protocol}: expected {expected_points} points")
            if any(int(row["n_seeds"]) != len(seeds) for row in points):
                raise ValueError(f"{panel}/{protocol}: inconsistent seed count")
            x = [float(row["x"]) for row in points]
            means = [float(row["y"]) for row in points]
            cis = [float(row["ci95"]) for row in points]
            series_label = label
            curve_excluded = shared_excluded | (excluded if panel == "b" and protocol == "trail_eta0" else set())
            if curve_excluded or (active_relays and panel != "d"):
                means, cis = [], []
                for value in x:
                    samples = {
                        seed: y for seed, y in by_seed[panel, protocol, value].items()
                        if seed not in curve_excluded
                    }
                    if set(samples) != set(seeds) - curve_excluded:
                        raise ValueError(f"{panel}/{protocol} at {value}: incomplete paired seed set")
                    n = len(samples)
                    means.append(statistics.mean(samples.values()))
                    cis.append(float(student_t.ppf(0.975, n-1))
                               * statistics.stdev(samples.values()) / math.sqrt(n))
                if panel == "b" and protocol == "trail_eta0" and excluded:
                    series_label = f"Fee-only ({len(seeds)-len(curve_excluded)} seeds)"
            if not all(math.isfinite(value) for value in x + means + cis) or min(cis) < 0:
                raise ValueError(f"{panel}/{protocol}: invalid plot values")
            style = STYLES[label]
            upper_limits.extend(mean + ci for mean, ci in zip(means, cis))
            common = dict(**style, markerfacecolor="white", linewidth=1.2,
                          label=series_label, zorder=2)
            if panel == "a":
                ax.fill_between(x, [mean-ci for mean, ci in zip(means, cis)],
                                [mean+ci for mean, ci in zip(means, cis)],
                                color=style["color"], alpha=0.12, linewidth=0, zorder=1)
                markers = [*range(0, len(x) - 1, 20), len(x) - 1]
                ax.plot(x, means, markevery=markers, markeredgewidth=0.9,
                        markersize=3.8, **common)
            else:
                ax.errorbar(x, means, yerr=cis, markeredgewidth=1.0,
                            markersize=4.5, capsize=2.5, **common)
        ax.set_ylabel("Inclusion time (s)" if panel == "d" else participation_label)
        if panel == "a":
            ax.set(xlabel="Epoch", xlim=(0, 203), ylim=(0, 100))
            ax.set_xticks([0, 50, 100, 150, 200])
        elif panel == "b":
            fees = [0.0001, 0.0005, 0.001, 0.005, 0.01]
            ax.set_xscale("log")
            ax.set_xticks(fees, [f"{fee:g}" for fee in fees])
            ax.set(xlabel=r"Transaction fee $F$", xlim=(0.000075, 0.0133), ylim=(0, 100))
            ax.minorticks_off()
        else:
            ax.set_xscale("log")
            ax.set_xticks([1, 2, 5], [r"$1\times$", r"$2\times$", r"$5\times$"])
            cost_label = "Forwarding Cost" if panel == "c" else "Median relay cost"
            ax.set(xlabel=cost_label + r" / $C_{\mathrm{ref}}$", xlim=(0.88, 5.7),
                   ylim=(0, math.ceil(max(upper_limits)*1.08) if panel == "d" else 100))
            ax.minorticks_off()
        if panel != "d":
            ax.set_yticks([0, 20, 40, 60, 80, 100])
        original.decorate(ax, reverse_legend=active_relays and panel != "d")
        if panel == "b" and excluded:
            legend = ax.get_legend()
            legend.set_loc("center right")
            legend.set_bbox_to_anchor((0.995, 0.4))
        if panel == "a":
            # Four legend entries fit between the PoS and Fee-only bands here.
            legend = ax.get_legend()
            legend.set_loc("lower right")
            legend.set_bbox_to_anchor((0.995, 0.12))
        fig.subplots_adjust(bottom=0.18, left=original.LEFT_MARGIN, right=0.97, top=0.97)
        outputs.extend(original.save(fig, f"trail_relay_participation_{panel}", output))
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--panels", default="abcd", help="Panels to export, e.g. b or abcd")
    parser.add_argument("--active-relays", action="store_true",
                        help="Panels a–c use the percentage of validators with the active relay profile")
    parser.add_argument("--fee-only-exclude-seed", type=int, action="append", default=[],
                        help="Panel-b sensitivity preview; omit this seed at every Fee-only fee")
    parser.add_argument("--exclude-seed", type=int, action="append", default=[],
                        help="Sensitivity preview; omit this seed from all mechanisms and preserve figure text")
    args = parser.parse_args()
    for path in render(args.data, args.output_dir, panels=args.panels,
                       fee_only_excluded_seeds=tuple(args.fee_only_exclude_seed),
                       excluded_seeds=tuple(args.exclude_seed), active_relays=args.active_relays):
        print(path)


if __name__ == "__main__":
    main()
