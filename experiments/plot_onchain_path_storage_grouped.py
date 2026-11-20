#!/usr/bin/env python3
"""Render four-hop core path storage for TRAIL Original, Compressed, and Ersoy."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from plot_onchain_path_storage import BLOCK_SIZES, INPUT, ROOT, configure_style, plt, read_trail_u8
from plot_trail_relay_participation import decorate
from plot_trail_relay_participation_v2 import STYLES as PARTICIPATION_STYLES


COMPRESSION = ROOT / "results/processed/block_validator_compression_v2/block_compression.csv"
OUTPUT_PDF = ROOT / "figures/trail_evidence/onchain_path_evidence_storage.pdf"
HOPS = 4
VALIDATORS = 100
SIGNATURE_BYTES = 48
ERSOY_FIELDS = (0, 3 * 33, 64, 0)
COLORS = {
    "TRAIL Compressed": PARTICIPATION_STYLES["Full TRAIL"]["color"],
    "TRAIL Original": PARTICIPATION_STYLES["Fee-only"]["color"],
    "Ersoy": PARTICIPATION_STYLES["Ersoy"]["color"],
}


def read_ersoy(input_file):
    rows = {}
    with input_file.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if (row["scheme"], row["hops"]) != ("Ersoy", str(HOPS)):
                continue
            count = int(row["transactions_per_block"])
            if count in rows:
                raise ValueError(f"Duplicate Ersoy result for {count} transactions")
            _, keys, signature, identifier = ERSOY_FIELDS
            expected = {
                "scenario": "protocol_native",
                "measurement_type": "assumption_based_estimate",
                "onchain_evidence_count": str(count),
                "path_identity_bytes": str(keys),
                "path_signature_bytes": str(signature),
                "path_identifier_bytes": str(identifier),
                "path_proof_bytes": str(identifier),
                "path_reward_share_bytes": "0",
                "total_path_storage_bytes": str(count * sum(ERSOY_FIELDS)),
            }
            if any(row[key] != value for key, value in expected.items()):
                raise ValueError(f"Unexpected Ersoy model for {count} transactions")
            rows[count] = row
    if rows.keys() != set(BLOCK_SIZES):
        raise ValueError("Incomplete Ersoy comparison grid")
    return rows


def result_row(scheme, count, fields, measurement, source, assumptions):
    index, relay_key, signature, identifier = fields
    per_path = sum(fields)
    return {
        "scheme": scheme,
        "measurement_type": measurement,
        "transactions_per_block": count,
        "validator_count": VALIDATORS,
        "hops": HOPS,
        "onchain_evidence_count": count,
        "index_bytes_per_path": index,
        "relay_public_key_bytes_per_path": relay_key,
        "signature_bytes_per_path": signature,
        "identifier_bytes_per_path": identifier,
        "core_evidence_bytes_per_path": per_path,
        "core_evidence_bytes_per_block": count * per_path,
        "core_evidence_kib_per_block": f"{count * per_path / 1024:.8f}",
        "source_reference": source,
        "assumptions": assumptions,
    }


def build_comparison(compression_file, ersoy_file):
    trail = read_trail_u8(compression_file)
    ersoy = read_ersoy(ersoy_file)
    source = compression_file.resolve()
    if source.is_relative_to(ROOT):
        source = source.relative_to(ROOT)
    rows = []
    for count in BLOCK_SIZES:
        original, compressed = trail[HOPS, count]
        for scheme, width, measured in [
            ("TRAIL Original", 8, original), ("TRAIL Compressed", 1, compressed),
        ]:
            row = result_row(
                scheme, count, (HOPS * width, 0, SIGNATURE_BYTES, 0), "actual_serialized",
                f"{source.as_posix()} (roster_high, 100 validators)",
                "Synthetic Rust serialization fixture. One path per transaction and one 48 B BLS signature. "
                f"{width} B per non-proposer index. Core fields only.",
            )
            if row["core_evidence_bytes_per_block"] != measured:
                raise ValueError(f"{scheme} fields disagree with encoded bytes for {count} transactions")
            rows.append(row)
        rows.append(result_row(
            "Ersoy", count, ERSOY_FIELDS, "assumption_based_estimate",
            ersoy[count]["source_reference"],
            "Plain-key Combined model. Three 33 B compressed SEC1 secp256k1 relay keys and one 64 B compact ECDSA "
            "client signature on the transaction and path identifier. Reconstruct the identifier from the "
            "leader credential, leader key, and ordered relay keys. Original transaction authorization excluded.",
        ))
    return rows


def render(rows):
    configure_style()
    fig, axis = plt.subplots(figsize=(3.45, 2.55))
    fig.subplots_adjust(left=0.15, right=0.975, top=0.97, bottom=0.19)
    axis.set_axisbelow(True)
    offsets = (-0.26, 0, 0.26)
    for (scheme, color), offset in zip(COLORS.items(), offsets, strict=True):
        values = [row["core_evidence_bytes_per_block"] / 1000 for row in rows if row["scheme"] == scheme]
        bars = axis.bar([group + offset for group in range(len(BLOCK_SIZES))], values, width=0.235,
                        color=color, edgecolor=color, linewidth=0.35, label=scheme)
        axis.bar_label(bars, fmt="%.1f", padding=2, fontsize=6.3)
    axis.set_ylim(0, 710)
    axis.set_xlim(-0.52, 2.52)
    axis.set_yticks(range(0, 701, 100))
    axis.set_xticks(range(len(BLOCK_SIZES)), [f"{count:,}" for count in BLOCK_SIZES])
    axis.set_xlabel("Transactions per Block")
    axis.set_ylabel("Block Size Expansion (KB)", fontsize=8.6)
    decorate(axis)
    axis.get_legend().set_loc("upper left")
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compression", type=Path, default=COMPRESSION)
    parser.add_argument("--ersoy-data", type=Path, default=INPUT)
    parser.add_argument("--csv", type=Path, help="Also export the underlying data to a new CSV")
    parser.add_argument("--pdf", type=Path, default=OUTPUT_PDF)
    args = parser.parse_args()
    rows = build_comparison(args.compression, args.ersoy_data)
    png_path = args.pdf.with_suffix(".png")
    for path in (args.csv, args.pdf, png_path):
        if path is not None and path.exists():
            raise FileExistsError(path)
    args.pdf.parent.mkdir(parents=True, exist_ok=True)
    if args.csv is not None:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("x", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    fig = render(rows)
    with args.pdf.open("xb") as handle:
        fig.savefig(handle, format="pdf", metadata={"Title": "Four-hop Core Path Evidence Storage",
                                                    "CreationDate": None})
    with png_path.open("xb") as handle:
        fig.savefig(handle, format="png", dpi=300)
    plt.close(fig)


if __name__ == "__main__":
    main()
