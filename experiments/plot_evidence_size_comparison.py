#!/usr/bin/env python3
"""Reproduce the stage-specific evidence size figures from the benchmark CSVs."""

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator

ROOT = Path(__file__).resolve().parents[1]
HOPS = (1, 2, 4, 6, 8, 16)
FINAL = "finalized_path_record"
RELAY = "relay_message"
PARTIAL = "identity_only_sensitivity"
SCHEMES = {
    "TRAIL": (FINAL, "compact_certificate", "#0072b2", "o", "-", "Rust serialization"),
    "NKN": (FINAL, "completed_signature_chain", "#d55e00", "s", "--", "protobuf model"),
    "Ersoy": (RELAY, "combined_attached", "#007d61", "D", "--", "assumed encoding"),
    "TxTail": (RELAY, "offchain_attached_model", "#923b86", "^", "-.", "conditional encoding"),
    "Plutus": (PARTIAL, "hypothetical_identity_subtotal", "#555555", "v", ":", "identity-only scenario"),
}
COLORS = ("#0072b2", "#e69f00", "#009e73", "#cc79a7", "#757575")
PARTS = ("Identity / keys", "Signature", "Proof", "Metadata", "Encoding")


def read_rows(path):
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    key = lambda row: (row["scheme"], row["evidence_stage"], row["profile"], row["hops"])
    output = {key(row): row for row in rows}
    if len(output) != len(rows):
        raise ValueError(f"duplicate comparison rows in {path}")
    return output


def get_row(rows, scheme, hops):
    stage, profile, *_ = SCHEMES[scheme]
    return rows[scheme, stage, profile, str(hops)]


def style_axes(axis):
    axis.spines[["top", "right"]].set_visible(False)
    axis.set_axisbelow(True)
    axis.grid(axis="y", color="#e1e5ea", linewidth=0.7)
    axis.yaxis.set_major_locator(MaxNLocator(5, integer=True))
    axis.set_ylim(bottom=0)
    axis.tick_params(length=3)


def make_size_plot(rows):
    fig, axes = plt.subplots(1, 3, figsize=(11.3, 4.0), layout="constrained")
    panels = [
        ("Completed certificate / proof", ("TRAIL", "NKN")),
        ("In-flight transaction evidence", ("Ersoy", "TxTail")),
        ("Identity subtotal only", ("Plutus",)),
    ]
    for index, (axis, (title, schemes)) in enumerate(zip(axes, panels)):
        for scheme in schemes:
            _, _, color, marker, linestyle, label = SCHEMES[scheme]
            entries = [get_row(rows, scheme, hops) for hops in HOPS]
            sizes = [float(row["encoded_evidence_bytes"]) if row["encoded_evidence_bytes"]
                     else float("nan") for row in entries]
            axis.plot(HOPS, sizes, marker=marker, color=color, linestyle=linestyle,
                      markerfacecolor=color if scheme == "TRAIL" else "white",
                      linewidth=1.6, markersize=5,
                      label=f"{scheme} · {label}")
        style_axes(axis)
        axis.set_title(title, loc="left", fontweight="bold")
        axis.set_xticks(HOPS)
        axis.set_xlabel("Relay hops")
        axis.set_ylabel("Evidence size (bytes)" if index < 2 else "Partial encoded size (bytes)")
        axis.legend(loc="upper left", frameon=False, fontsize=8)
        if "NKN" in schemes:
            axis.text(0.04, 0.70, "NKN requires ≥2 edges", color="#594536",
                      transform=axis.transAxes, fontsize=8)
        if "Plutus" in schemes:
            axis.text(0.04, 0.78, "Native proof size unavailable", color="#555555",
                      transform=axis.transAxes, fontsize=8)
    fig.suptitle("Propagation evidence size vs. relay hops", fontweight="bold")
    return fig


def make_breakdown_plot(rows):
    fig, axes = plt.subplots(1, 2, figsize=(9.0, 4.4))
    fig.subplots_adjust(left=0.095, right=0.98, bottom=0.24, top=0.76, wspace=0.31)
    panels = [
        ("Completed certificate / proof", ("TRAIL", "NKN")),
        ("In-flight transaction evidence", ("Ersoy", "TxTail", "Plutus")),
    ]
    for axis, (title, schemes) in zip(axes, panels):
        style_axes(axis)
        axis.set_title(title, loc="left", fontweight="bold")
        axis.set_ylabel("Evidence size (bytes)")
        peak = 0
        for position, scheme in enumerate(schemes):
            if scheme in ("TxTail", "Plutus"):
                text = "Native total\nunverified" if scheme == "TxTail" else "Native fields\nunverified"
                axis.text(position, 0.08, text, ha="center", va="bottom",
                          transform=axis.get_xaxis_transform(), color="#555555", fontsize=8)
                continue
            row = get_row(rows, scheme, 4)
            values = [
                int(row["identity_bytes"]) + int(row["public_key_bytes"]),
                *[int(row[f"{section}_bytes"]) for section in ("signature", "proof", "metadata", "encoding")],
            ]
            assert sum(values) == int(row["encoded_evidence_bytes"]), scheme
            bottom = 0
            for value, color in zip(values, COLORS):
                if value:
                    axis.bar(position, value, bottom=bottom, width=0.58, color=color,
                             hatch="/" if scheme == "Ersoy" else None,
                             edgecolor="white", linewidth=0.35)
                bottom += value
            peak = max(peak, bottom)
            axis.annotate(f"{bottom} B", (position, bottom), xytext=(0, 4),
                          textcoords="offset points", ha="center", fontsize=9)
        axis.set_xticks(range(len(schemes)), schemes)
        axis.set_xlim(-0.5, len(schemes) - 0.5)
        axis.set_ylim(0, peak * 1.17)
    handles = [Patch(facecolor=color, label=part) for part, color in zip(PARTS, COLORS)]
    fig.legend(handles=handles, ncol=len(handles), loc="lower center", frameon=False,
               bbox_to_anchor=(0.5, 0.055), fontsize=9)
    fig.text(0.5, 0.024, "TRAIL: Rust serialization · NKN: protobuf model · Ersoy: assumed encoding (hatched)",
             ha="center", color="#555555", fontsize=8)
    fig.suptitle("Evidence size breakdown at 4 hops", y=0.96, fontweight="bold")
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=ROOT / "results" / "processed")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "figures" / "evidence_size_comparison")
    args = parser.parse_args()
    rows = read_rows(args.input_dir / "evidence_size_comparison.csv")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    paths = [args.output_dir / "fig_a_evidence_size_vs_hops.pdf",
             args.output_dir / "fig_b_evidence_breakdown_4_hops.pdf"]
    for path in paths:
        if path.exists():
            parser.error(f"output exists: {path}")
    with plt.rc_context({"font.size": 9, "font.family": "DejaVu Sans", "pdf.fonttype": 42}):
        for path, make in zip(paths, [make_size_plot, make_breakdown_plot]):
            fig = make(rows)
            with path.open("xb") as handle:
                fig.savefig(handle, format="pdf", metadata={"CreationDate": None, "ModDate": None})
            plt.close(fig)
            print(path)


if __name__ == "__main__":
    main()
