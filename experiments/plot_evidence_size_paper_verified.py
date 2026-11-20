#!/usr/bin/env python3
"""Render the two evidence-size paper figures from the verified CSV."""

import argparse
from math import nan
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
from plot_trail_relay_participation import configure_style

from plot_evidence_size_comparison import read_rows

FIGSIZE = (3.45, 2.55)
HOPS = (1, 2, 4, 6, 8, 16)
SCHEMES = {
    "TRAIL": ("finalized_path_record", "compact_certificate", "#D55E00", "o", "-"),
    "Ersoy": ("relay_message", "combined_attached", "#009E73", "D", "-."),
    "Plutus": ("relay_message", "thesis_message_evidence", "#A95185", "^",
               (0, (4, 2, 1, 2, 1, 2))),
    "NKN": ("finalized_path_record", "completed_signature_chain", "#6B6B6B", "v", ":"),
    "TxTail": ("relay_message", "fig8_relay_evidence", "#0072B2", "s", "--"),
}
PARTS = (
    ("Identity / Public Keys", "#0072B2"),
    ("Signatures", "#D55E00"),
    ("Cryptographic Proofs", "#009E73"),
    ("Metadata", "#A95185"),
    ("Encoding Overhead", "#6B6B6B"),
)
LEGEND = dict(frameon=True, framealpha=0.9, facecolor="white",
              edgecolor="#D0D0D0", borderpad=0.35, handletextpad=0.5)


def get_row(rows, scheme, hops):
    stage, profile, *_ = SCHEMES[scheme]
    return rows.get((scheme, stage, profile, str(hops)))


def complete(row):
    return row is not None and row["evidence_scope"] == "complete_record" and row["encoded_evidence_bytes"]


def style(axis, major_axis):
    axis.spines[["top", "right"]].set_visible(False)
    axis.spines[["bottom", "left"]].set_linewidth(0.9)
    axis.set_axisbelow(True)
    axis.grid(axis=major_axis, color="#E6E6E6", linewidth=0.7)
    axis.tick_params(length=3, width=0.8)


def make_size_plot(rows, *, trail_index_bytes=1):
    fig, axis = plt.subplots(figsize=FIGSIZE)
    fig.subplots_adjust(left=0.18, right=0.97, bottom=0.18, top=0.97)
    for scheme, (_, _, color, marker, linestyle) in SCHEMES.items():
        entries = [get_row(rows, scheme, hops) for hops in HOPS]
        sizes = [int(row["encoded_evidence_bytes"]) if complete(row) else nan
                 for row in entries]
        if scheme == "TRAIL" and trail_index_bytes == 1:
            for i, (hops, row) in enumerate(zip(HOPS, entries, strict=True)):
                if not complete(row) or (int(row["identity_bytes"]), int(row["signature_bytes"])) != (8 * hops, 48):
                    raise ValueError(f"expected an 8-byte-index TRAIL certificate at {hops} hops")
                # Model a fixed u8 layout, retaining the measured certificate's framing.
                sizes[i] -= 7 * hops
        axis.plot(HOPS, sizes, color=color, linestyle=linestyle, marker=marker,
                  markerfacecolor="white", markeredgewidth=1.0, markersize=4.5,
                  linewidth=1.2, label=scheme)
        if not any(complete(row) for row in entries):
            axis.text(0.99, 0.04, f"{scheme}: N/A", transform=axis.transAxes, ha="right")
    style(axis, "y")
    axis.set_yscale("log")
    axis.set_ylim(64 if trail_index_bytes == 1 else 69, 6400)
    axis.yaxis.set_major_locator(FixedLocator([80, 160, 320, 640, 1280, 2560, 5120]))
    axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
    axis.yaxis.set_minor_locator(NullLocator())
    axis.set_xlim(0.6, 16.45)
    axis.set_xticks(HOPS)
    axis.set_xlabel("Relay Hops")
    axis.set_ylabel("Evidence Size (Bytes)")

    axis.legend(loc="upper left", ncols=3, columnspacing=0.75,
                handlelength=1.4, **LEGEND)
    return fig


def components(row):
    return [
        int(row["identity_bytes"]) + int(row["public_key_bytes"]),
        int(row["signature_bytes"]), int(row["proof_bytes"]),
        int(row["metadata_bytes"]), int(row["encoding_bytes"]),
    ]


def draw_breakdown(axis, rows, schemes):
    style(axis, "x")
    axis.set_yticks(range(len(schemes)), schemes)
    axis.set_ylim(len(schemes) - 0.44, -0.56)
    axis.tick_params(axis="y", length=0, pad=4)
    for position, scheme in enumerate(schemes):
        row = get_row(rows, scheme, 4)
        if not complete(row):
            axis.text(0.015, position, "N/A", transform=axis.get_yaxis_transform(),
                      va="center", color="#484D54")
            continue
        total = 0
        for value, (_, color) in zip(components(row), PARTS):
            if value:
                axis.barh(position, value, left=total, height=0.53, facecolor=color,
                          edgecolor="white", linewidth=0.45,
                          hatch="///" if scheme == "Ersoy" else None)
                total += value
        if total != int(row["encoded_evidence_bytes"]):
            raise ValueError(f"invalid four-hop component sum for {scheme}")
        axis.annotate(f"{total:,} B", (total, position), xytext=(3, 0),
                      textcoords="offset points", va="center", fontsize=7)


def make_breakdown_plot(rows):
    fig, (small, full) = plt.subplots(
        2, 1, figsize=FIGSIZE,
        gridspec_kw={"height_ratios": [3.4, 0.85]},
    )
    fig.subplots_adjust(left=0.18, right=0.97, bottom=0.39, top=0.89, hspace=0.52)
    draw_breakdown(small, rows, ("TRAIL", "TxTail", "Ersoy", "Plutus"))
    small.set_xlim(0, 450)
    small.set_xticks([0, 100, 200, 300, 400])

    draw_breakdown(full, rows, ("NKN",))
    full.set_xlim(0, 1330)
    full.set_xticks([0, 300, 600, 900, 1200])
    full.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
    full.set_xlabel("Evidence Size (Bytes)", labelpad=2)
    fig.suptitle("Evidence Size Breakdown at 4 Relay Hops", y=0.97, fontsize=8.5)

    handles = [Patch(facecolor=PARTS[i][1], label=PARTS[i][0]) for i in (0, 2, 1, 3, 4)]
    fig.legend(handles=handles, ncols=3, loc="lower center",
               handlelength=0.9, columnspacing=0.8, bbox_to_anchor=(0.52, 0.069), **LEGEND)
    fig.text(0.53, 0.048, "TRAIL: block certificate. NKN: completed proof (separate scale).",
             ha="center", fontsize=6.0)
    fig.text(0.53, 0.014, "Others: relay messages. Hatching denotes Ersoy's encoding assumption.",
             ha="center", fontsize=6.0)
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path,
                        default=ROOT / "results/processed/evidence_size_paper_verified")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "figures/trail_evidence")
    parser.add_argument("--figure", choices=("a", "b", "both"), default="both")
    parser.add_argument("--trail-index-bytes", type=int, choices=(1, 8), default=1,
                        help="Fig. A TRAIL index width. The 1B series is a fixed-width encoding model.")
    args = parser.parse_args()
    rows = read_rows(args.input_dir / "evidence_size_comparison.csv")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    figures = []
    if args.figure in ("a", "both"):
        figures.append(("fig_a_evidence_size_vs_hops",
                        lambda data: make_size_plot(data, trail_index_bytes=args.trail_index_bytes)))
    if args.figure in ("b", "both"):
        figures.append(("fig_b_evidence_breakdown_4_hops", make_breakdown_plot))
    for path in (args.output_dir / f"{stem}.{fmt}"
                 for stem, _ in figures for fmt in ("pdf", "png")):
        if path.exists():
            parser.error(f"output exists: {path}")
    with plt.rc_context():
        configure_style()
        for stem, make in figures:
            fig = make(rows)
            pdf = args.output_dir / f"{stem}.pdf"
            png = args.output_dir / f"{stem}.png"
            with pdf.open("xb") as handle:
                fig.savefig(handle, format="pdf", metadata={"CreationDate": None, "ModDate": None})
            with png.open("xb") as handle:
                fig.savefig(handle, format="png", dpi=300)
            plt.close(fig)
            print(pdf)
            print(png)


if __name__ == "__main__":
    main()
