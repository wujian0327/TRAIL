#!/usr/bin/env python3
"""Render the native disclosure rules and the one-new-SigChain condition."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
from plot_trail_relay_participation import configure_style, plt

from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator


INPUT = ROOT / "results/processed/onchain_path_storage_v2/protocol_native.csv"
OUTPUT = ROOT / "figures/trail_evidence/onchain_path_storage_v2"
HOPS = (2, 4, 6)
BLOCK_SIZES = (100, 1000, 4000)
SCHEMES = ("TRAIL", "Ersoy", "Plutus", "NKN", "TxTail")
STYLES = {
    "TRAIL": ("#D55E00", "o", "-", "Serialized fixture"),
    "Ersoy": ("#009E73", "D", "-.", "Encoding estimate"),
    "NKN": ("#6B6B6B", "v", ":", "One new SigChain"),
    "TxTail": ("#0072B2", "s", "--", "One sampled path"),
}
MEASUREMENT_TYPES = {
    "TRAIL": "actual_serialized",
    "Ersoy": "assumption_based_estimate",
    "Plutus": "not_specified",
    "NKN": "not_specified",
    "TxTail": "protocol_encoding_model",
}


def read_totals(input_file: Path) -> dict[tuple[str, int, int], int | None]:
    totals = {}
    with input_file.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["scenario"] != "protocol_native":
                raise ValueError("Expected only protocol-native rows")
            key = row["scheme"], int(row["hops"]), int(row["transactions_per_block"])
            if key in totals:
                raise ValueError(f"Duplicate block configuration {key}")
            value = row["total_path_storage_bytes"]
            if row["measurement_type"] != MEASUREMENT_TYPES[key[0]]:
                raise ValueError(f"Unexpected measurement status for {key}")
            if (not value) != (row["measurement_type"] == "not_specified"):
                raise ValueError(f"Unexpected storage value for {key}")
            totals[key] = int(value) if value else None
    expected = {(scheme, h, z) for scheme in SCHEMES for h in HOPS for z in BLOCK_SIZES}
    if totals.keys() != expected:
        raise ValueError("Incomplete protocol-native comparison grid")
    for (scheme, _, _), total in totals.items():
        if scheme in ("TRAIL", "Ersoy", "TxTail") and (total is None or total <= 0):
            raise ValueError(f"Missing or nonpositive storage for {scheme}")
    return totals


def read_nkn_conditions(input_file: Path) -> dict[tuple[int, int, int], int]:
    totals = {}
    with input_file.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if (row["scheme"], row["scenario"], row["measurement_type"]) != (
                "NKN", "nkn_disclosure_condition", "protocol_encoding_model"
            ):
                raise ValueError("Expected only NKN disclosure conditions")
            count = int(row["onchain_evidence_count"])
            key = int(row["hops"]), int(row["transactions_per_block"]), count
            total = int(row["total_path_storage_bytes"])
            if key in totals or total != count * int(row["evidence_bytes_per_path"]):
                raise ValueError(f"Inconsistent NKN disclosure condition {key}")
            totals[key] = total
    expected = {(h, z, count) for h in HOPS for z in BLOCK_SIZES for count in (0, 1)}
    if totals.keys() != expected or any(value <= 0 for key, value in totals.items() if key[2] == 1):
        raise ValueError("Incomplete or invalid NKN disclosure conditions")
    return totals


def read_trail_u8(input_file: Path) -> dict[tuple[int, int], tuple[int, int]]:
    totals = {}
    with input_file.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if (row["data_source"], row["cohort"], row["validator_count"]) != (
                "synthetic_paths", "roster_high", "100"
            ):
                continue
            z = int(row["tx_count"])
            if z not in BLOCK_SIZES:
                raise ValueError(f"Unexpected TRAIL fixture transaction count {z}")
            h, extra = divmod(int(row["total_index_occurrences"]), z)
            key = h, z
            if (key in totals or extra or row["global_mode"] != "u8"
                or row["path_length_histogram"] != f"{h}:{z}"
                or int(row["signed_paths"]) != z or not 0 <= int(row["max_global_index"]) < 100):
                raise ValueError(f"Invalid TRAIL 1B fixture for {key}")
            baseline = int(row["baseline_core_bytes"])
            core_u8 = int(row["global_core_bytes"])
            if (baseline, core_u8) != (z * (48 + 8 * h), z * (48 + h)):
                raise ValueError(f"Unexpected TRAIL core sizes for {key}")
            totals[key] = baseline, core_u8
    if totals.keys() != {(h, z) for h in HOPS for z in BLOCK_SIZES}:
        raise ValueError("Incomplete TRAIL 1B comparison grid")
    return totals


def render(input_file: Path, nkn_file: Path, output_dir: Path, compression_file: Path | None = None) -> Path:
    totals = read_totals(input_file)
    nkn_totals = read_nkn_conditions(nkn_file)
    if compression_file is not None:
        for (h, z), (baseline, core_u8) in read_trail_u8(compression_file).items():
            key = "TRAIL", h, z
            if totals[key] != baseline:
                raise ValueError(f"TRAIL source baselines disagree for {key}")
            totals[key] = core_u8
    configure_style()
    fig, axes = plt.subplots(1, 3, figsize=(7.16, 2.8), sharey=True)
    fig.subplots_adjust(left=0.118, right=0.984, top=0.72, bottom=0.28, wspace=0.17)
    legend = []
    for panel, (axis, hops) in enumerate(zip(axes, HOPS)):
        for scheme, (color, marker, linestyle, label) in STYLES.items():
            if scheme == "NKN":
                values = [nkn_totals[hops, z, 1] for z in BLOCK_SIZES]
            else:
                values = [totals[scheme, hops, z] for z in BLOCK_SIZES]
            if scheme == "TRAIL" and compression_file is not None:
                label = "1B global indices"
            line, = axis.plot(BLOCK_SIZES, values, color=color, marker=marker,
                              linestyle=linestyle, markersize=4.5, markerfacecolor="white",
                              markeredgewidth=1.0, linewidth=1.2, label=f"{scheme} · {label}")
            if panel == 0:
                legend.append(line)
        axis.set_title(f"({chr(97 + panel)})  $h = {hops}$", fontsize=8.5, pad=4)
        axis.set_xscale("log")
        axis.set_xticks(BLOCK_SIZES, [f"{z:,}" for z in BLOCK_SIZES])
        axis.xaxis.set_minor_locator(NullLocator())
        axis.set_xlim(75, 5300)
        axis.set_yscale("log")
        axis.set_ylim(70, 1_400_000)
        axis.yaxis.set_major_locator(FixedLocator([100, 1000, 10000, 100000, 1000000]))
        axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
        axis.yaxis.set_minor_locator(NullLocator())
        axis.tick_params(length=3, width=0.8, left=panel == 0)
        axis.grid(axis="y", color="#E6E6E6", linewidth=0.7, zorder=0)
        axis.spines[["left", "bottom"]].set_linewidth(0.9)
        if panel:
            axis.spines["left"].set_visible(False)
    axes[0].set_ylabel("Path Evidence Storage (Bytes)", fontsize=8.5, labelpad=6)
    fig.text(0.55, 0.145, "Transactions per Block", ha="center", fontsize=9)
    fig.text(0.55, 0.053, "Plutus N/A   ·   NKN adds 0 B without a new winning SigChain",
             ha="center", fontsize=7.5, color="#4D4D4D")
    fig.legend(handles=legend, loc="upper center", bbox_to_anchor=(0.55, 0.995),
               ncols=2, frameon=False, handlelength=2.0, columnspacing=2.0, fontsize=8)

    output_dir.mkdir(parents=True, exist_ok=True)
    filename = output_dir / "onchain_path_evidence_storage.pdf"
    with filename.open("xb") as output:
        fig.savefig(output, format="pdf", metadata={"Title": "On-chain Path Evidence Storage Overhead",
                                                     "CreationDate": None})
    plt.close(fig)
    return filename


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument("--nkn-conditions", type=Path)
    parser.add_argument("--compression", type=Path, help="Block compression CSV for TRAIL's 100-validator 1B core sizes")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    nkn_file = args.nkn_conditions or args.input.with_name("nkn_disclosure_conditions.csv")
    print(render(args.input, nkn_file, args.output_dir, args.compression))
