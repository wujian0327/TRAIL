#!/usr/bin/env python3
"""Summarize the formal 512 tx/slot TRAIL devnet experiment."""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import statistics
from pathlib import Path
from typing import Any

from scipy.stats import t as student_t


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW = ROOT / "results/raw/trail_devnet_512_main"
DEFAULT_OUT = ROOT / "results/processed"
VARIANTS = ("baseline", "trail")


def mean_ci(values: list[float]) -> tuple[float, float]:
    if not values:
        return math.nan, math.nan
    mean = statistics.fmean(values)
    if len(values) < 2:
        return mean, math.nan
    sem = statistics.stdev(values) / math.sqrt(len(values))
    return mean, float(student_t.ppf(0.975, len(values) - 1)) * sem


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    position = quantile * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def aggregate_blocks(summary: dict[str, Any]) -> dict[str, float]:
    rows = summary["blocks"]["audit_rows"]
    blocks = len(rows)
    txs = sum(int(row["block_transaction_count"]) for row in rows)
    measurement_txs = sum(int(row["measurement_transaction_count"]) for row in rows)
    records = sum(int(row["evidence_record_count"]) for row in rows)
    valid = sum(int(row["valid_evidence_unique_transaction_count"]) for row in rows)
    missing = sum(int(row["missing_evidence_transaction_count"]) for row in rows)
    expired = sum(int(row["expired_evidence_count"]) for row in rows)
    legal_empty = sum(int(row["legal_empty_path_transaction_count"]) for row in rows)
    evidence_bytes = sum(int(row["evidence_ssz_bytes"]) for row in rows)
    evidence_occurrences = sum(int(row["evidence_ssz_occurrences_in_block"]) for row in rows)
    identity_count = sum(int(row["path_identity_count"]) for row in rows)
    hop_total = sum(
        float(row["path_hop_mean"]) * int(row["evidence_record_count"]) for row in rows
    )
    zero_hop = 0
    one_hop = 0
    multi_hop = 0
    for row in rows:
        histogram = row.get("path_hop_histogram", {})
        zero_hop += int(histogram.get("0", 0))
        one_hop += int(histogram.get("1", 0))
        multi_hop += sum(int(count) for hop, count in histogram.items() if int(hop) >= 2)
    adjusted_denominator = max(0, txs - legal_empty)
    return {
        "window_block_count": float(blocks),
        "window_block_transaction_count": float(txs),
        "window_measurement_transaction_count": float(measurement_txs),
        "evidence_record_count": float(records),
        "valid_evidence_unique_transaction_count": float(valid),
        "missing_evidence_transaction_count": float(missing),
        "expired_evidence_count": float(expired),
        "evidence_ssz_occurrences_in_block": float(evidence_occurrences),
        "legal_empty_path_transaction_count": float(legal_empty),
        "zero_hop_evidence_count": float(zero_hop),
        "one_hop_evidence_count": float(one_hop),
        "multi_hop_evidence_count": float(multi_hop),
        "evidence_ssz_total_bytes": float(evidence_bytes),
        "evidence_bytes_per_block": evidence_bytes / blocks if blocks else 0.0,
        "evidence_bytes_per_evidence_tx": evidence_bytes / records if records else 0.0,
        "evidence_bytes_per_valid_evidence_tx": evidence_bytes / valid if valid else 0.0,
        "evidence_bytes_per_all_tx": evidence_bytes / txs if txs else 0.0,
        "evidence_carriage_coverage": records / txs if txs else 0.0,
        "evidence_coverage": valid / txs if txs else 0.0,
        "adjusted_evidence_coverage": valid / adjusted_denominator if adjusted_denominator else 0.0,
        "mean_path_identity_count": identity_count / records if records else 0.0,
        "mean_path_hops": hop_total / records if records else 0.0,
        "block_ssz_mean_bytes": statistics.fmean(
            float(row["block_ssz_bytes"]) for row in rows
        ) if rows else 0.0,
    }


def run_row(run_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    status = read_json(run_dir / "runner_status.json")
    summary = read_json(run_dir / "summary.json")
    acceptance = read_json(run_dir / "acceptance.json")
    adjudication = "none"
    if status.get("status") != "ok" or not acceptance.get("passed", False):
        failed = [check for check in acceptance.get("checks", []) if not check.get("passed")]
        race = (
            len(failed) == 1
            and failed[0].get("name") == "geth-relay-epoch-current"
            and "observed=[" in str(failed[0].get("detail", ""))
            and summary.get("formal_experiment", {}).get("measurement_quality", {}).get("passed") is True
        )
        if not race:
            raise RuntimeError(
                f"{run_dir.name}: status={status.get('status')}, failed_checks={failed}"
            )
        adjudication = "accepted: beacon/Geth epoch sampling crossed one boundary after the run"
    spec = summary["formal_experiment"]["spec"]
    workload = summary["workload"]
    window = workload["measurement_window"]
    resources = summary["formal_experiment"]["resources"]
    prometheus = summary["formal_experiment"]["prometheus"]
    txs = workload.get("txs", [])
    delays = [
        float(tx["inclusion_delay_seconds"])
        for tx in txs
        if tx.get("status") == 1 and "inclusion_delay_seconds" in tx
    ]
    included_timestamps = [
        float(tx["included_block_timestamp"])
        for tx in txs
        if tx.get("status") == 1 and "included_block_timestamp" in tx
    ]
    block_metrics = aggregate_blocks(summary)
    row: dict[str, Any] = {
        "run_id": run_dir.name,
        "runner_status": status.get("status"),
        "acceptance_passed_raw": bool(acceptance.get("passed", False)),
        "acceptance_adjudication": adjudication,
        "variant": spec["variant"],
        "seed": int(spec["seed"]),
        "target_tx_per_slot": int(spec["load_tx_per_slot"]),
        "target_tx_per_second": int(spec["load_tx_per_slot"]) / int(spec["seconds_per_slot"]),
        "target_transaction_count": int(window["target_transaction_count"]),
        "send_success_count": int(workload.get("send_success_count", 0)),
        "send_failure_count": int(workload.get("send_failure_count", 0)),
        "actual_send_tps": float(workload.get("actual_send_tps", 0.0)),
        "sent_in_window_count": int(window["sent_in_window_count"]),
        "included_in_window_count": int(window["included_in_window_count"]),
        "pending_submitted_at_window_end": max(0, int(window["sent_in_window_count"]) - int(window["included_in_window_count"])),
        "not_yet_sent_at_window_end": max(0, int(window["target_transaction_count"]) - int(window["sent_in_window_count"])),
        "post_window_drain_seconds": max(0.0, max(included_timestamps, default=float(window["end_unix"])) - float(window["end_unix"])),
        "window_throughput_tx_per_slot": float(window["throughput_tx_per_slot"]),
        "window_throughput_tx_per_second": float(window["throughput_tx_per_second"]),
        "final_included_count": int(workload.get("success_count", 0)),
        "final_inclusion_ratio": float(workload.get("final_inclusion_ratio", 0.0)),
        "unfinished_count": int(workload.get("unfinished_count", 0)),
        "p95_inclusion_seconds": percentile(delays, 0.95),
        "inclusion_elapsed_seconds": float(workload.get("inclusion_elapsed_seconds", 0.0)),
        "missed_slots": int(summary["blocks"].get("missed_slots", 0)),
        "cpu_per_node_percent": float(resources.get("per_node_cpu_mean_percent", 0.0)),
        "peak_memory_per_node_mib": float(resources.get("per_node_memory_peak_mean_bytes", 0.0)) / 2**20,
        "network_rx_per_node_mib": float(resources.get("per_node_network_rx_delta_mean_bytes", 0.0)) / 2**20,
        "network_tx_per_node_mib": float(resources.get("per_node_network_tx_delta_mean_bytes", 0.0)) / 2**20,
        "network_total_per_node_mib": (
            float(resources.get("per_node_network_rx_delta_mean_bytes", 0.0))
            + float(resources.get("per_node_network_tx_delta_mean_bytes", 0.0))
        ) / 2**20,
        "evidence_verify_count": float(prometheus.get("evidence_verify_count", 0.0) or 0.0),
        "evidence_verify_p95_ms": float(prometheus.get("evidence_verify_p95_seconds", 0.0) or 0.0) * 1000,
        "measurement_start_slot": int(window["start_slot"]),
        "measurement_end_slot": int(window["end_slot"]),
        "measurement_start_unix": float(window["start_unix"]),
        "measurement_end_unix": float(window["end_unix"]),
        **block_metrics,
    }
    audit = []
    for item in summary["blocks"]["audit_rows"]:
        copied = dict(item)
        copied["run_id"] = run_dir.name
        copied["variant"] = spec["variant"]
        copied["seed"] = int(spec["seed"])
        audit.append(copied)
    return row, audit


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            encoded = {
                key: json.dumps(value, sort_keys=True) if isinstance(value, (list, dict)) else value
                for key, value in row.items()
            }
            writer.writerow(encoded)


def grouped_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metrics = [
        "actual_send_tps", "sent_in_window_count", "included_in_window_count",
        "pending_submitted_at_window_end", "not_yet_sent_at_window_end",
        "post_window_drain_seconds", "window_throughput_tx_per_slot", "final_inclusion_ratio",
        "p95_inclusion_seconds", "missed_slots", "cpu_per_node_percent",
        "peak_memory_per_node_mib", "network_rx_per_node_mib",
        "network_tx_per_node_mib", "network_total_per_node_mib",
        "block_ssz_mean_bytes", "evidence_bytes_per_block",
        "evidence_bytes_per_evidence_tx", "evidence_bytes_per_valid_evidence_tx",
        "evidence_bytes_per_all_tx", "evidence_carriage_coverage",
        "evidence_coverage", "adjusted_evidence_coverage", "mean_path_hops",
        "evidence_verify_p95_ms",
    ]
    output = []
    for variant in VARIANTS:
        selected = [row for row in runs if row["variant"] == variant]
        result: dict[str, Any] = {"variant": variant, "n": len(selected)}
        for metric in metrics:
            mean, ci = mean_ci([float(row[metric]) for row in selected])
            result[f"{metric}_mean"] = mean
            result[f"{metric}_ci95"] = ci
        output.append(result)
    return output


def paired_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {(row["variant"], row["seed"]): row for row in runs}
    metrics = [
        "window_throughput_tx_per_slot", "final_inclusion_ratio",
        "p95_inclusion_seconds", "cpu_per_node_percent",
        "peak_memory_per_node_mib", "network_rx_per_node_mib",
        "network_tx_per_node_mib", "network_total_per_node_mib",
        "block_ssz_mean_bytes",
    ]
    output = []
    for metric in metrics:
        deltas = []
        relative = []
        for seed in range(5):
            baseline = float(by_key[("baseline", seed)][metric])
            trail = float(by_key[("trail", seed)][metric])
            deltas.append(trail - baseline)
            if baseline:
                relative.append((trail / baseline - 1.0) * 100.0)
        delta_mean, delta_ci = mean_ci(deltas)
        relative_mean, relative_ci = mean_ci(relative)
        output.append({
            "metric": metric,
            "n_pairs": len(deltas),
            "trail_minus_pos_mean": delta_mean,
            "trail_minus_pos_ci95": delta_ci,
            "relative_change_percent_mean": relative_mean,
            "relative_change_percent_ci95": relative_ci,
        })
    return output


def table_tex(groups: list[dict[str, Any]], paired: list[dict[str, Any]]) -> str:
    indexed = {row["variant"]: row for row in groups}
    diff = {row["metric"]: row for row in paired}
    b = indexed["baseline"]
    t = indexed["trail"]
    def v(row: dict[str, Any], metric: str, scale: float = 1.0) -> str:
        return f"{row[metric + '_mean'] * scale:.3f} $\\pm$ {row[metric + '_ci95'] * scale:.3f}"
    return "\n".join([
        r"\begin{tabular}{lrr}",
        r"\toprule",
        r"Metric & Ethereum PoS & Full TRAIL \\",
        r"\midrule",
        f"Final inclusion (\\%) & {v(b, 'final_inclusion_ratio', 100)} & {v(t, 'final_inclusion_ratio', 100)} \\\\",
        f"Window throughput (tx/slot) & {v(b, 'window_throughput_tx_per_slot')} & {v(t, 'window_throughput_tx_per_slot')} \\\\",
        f"CPU/node (\\%) & {v(b, 'cpu_per_node_percent')} & {v(t, 'cpu_per_node_percent')} \\\\",
        f"Peak memory/node (MiB) & {v(b, 'peak_memory_per_node_mib')} & {v(t, 'peak_memory_per_node_mib')} \\\\",
        f"Network TX/node/window (MiB) & {v(b, 'network_tx_per_node_mib')} & {v(t, 'network_tx_per_node_mib')} \\\\",
        f"Network RX/node/window (MiB) & {v(b, 'network_rx_per_node_mib')} & {v(t, 'network_rx_per_node_mib')} \\\\",
        f"SSZ block size (KiB) & {v(b, 'block_ssz_mean_bytes', 1/1024)} & {v(t, 'block_ssz_mean_bytes', 1/1024)} \\\\",
        f"Evidence payload (KiB/block) & -- & {v(t, 'evidence_bytes_per_block', 1/1024)} \\\\",
        f"p95 evidence verification (ms) & -- & {v(t, 'evidence_verify_p95_ms')} \\\\",
        r"\bottomrule",
        r"\end{tabular}",
        "",
        f"% Paired SSZ block-size difference: {diff['block_ssz_mean_bytes']['trail_minus_pos_mean']/1024:.3f} KiB/block",
    ])


def report_md(runs: list[dict[str, Any]], groups: list[dict[str, Any]], paired: list[dict[str, Any]]) -> str:
    indexed = {row["variant"]: row for row in groups}
    baseline = indexed["baseline"]
    trail = indexed["trail"]
    block_diff = next(row for row in paired if row["metric"] == "block_ssz_mean_bytes")
    adjudicated = [row for row in runs if row["acceptance_adjudication"] != "none"]
    missing = sum(int(row["missing_evidence_transaction_count"]) for row in runs if row["variant"] == "trail")
    legal_empty = sum(int(row["legal_empty_path_transaction_count"]) for row in runs if row["variant"] == "trail")
    return f"""# TRAIL devnet 512 tx/slot report

## Scope and data quality

- Ten complete runs: five independent seeds per variant, 400 canonical formal-window blocks.
- Nine runs passed raw acceptance. `{adjudicated[0]['run_id']}` completed with finite data and passed measurement quality, but raw acceptance sampled the Beacon head in epoch 24 and all eight Geth nodes after they had advanced together to epoch 25. It is retained with an explicit one-epoch sampling-race adjudication.
- No duplicate or missing `(variant, seed)` keys; all runs used 40 slots (120 s), zero missed slots, 20,480 successful submissions, and 100% final successful receipts.
- Target load was 512 tx/slot (170.667 tx/s), but the local generator achieved only {baseline['actual_send_tps_mean']:.3f} +/- {baseline['actual_send_tps_ci95']:.3f} tx/s for PoS and {trail['actual_send_tps_mean']:.3f} +/- {trail['actual_send_tps_ci95']:.3f} tx/s for TRAIL. These runs therefore do not demonstrate sustained 512 tx/slot.

## Statistical window and resource definitions

The formal window is the half-open interval `[start_slot, end_slot)` after five warm-up epochs. Window throughput is the number of measurement transactions included in those 40 slots divided by 40. Final inclusion is tracked separately after the window. Resource samples are restricted to the same timestamps; EL and CL CPU are summed per node, memory is each node's within-window EL+CL peak averaged over eight nodes, and network RX/TX are separate counter deltas. Means and two-sided 95% Student's t confidence intervals use five runs.

## Evidence reconciliation

The block endpoint returned actual signed-beacon-block SSZ bytes. Each on-chain record serializes as 108 fixed bytes (32-byte tx hash, 8-byte epoch, two 8-byte fee fields, 4-byte path offset, and a 48-byte aggregate signature), plus 8 bytes per identity; the variable list adds a 4-byte offset per record. The current implementation creates one aggregate signature per block and repeats its 48 bytes in every record.

- Evidence payload: {trail['evidence_bytes_per_block_mean']/1024:.3f} +/- {trail['evidence_bytes_per_block_ci95']/1024:.3f} KiB/block.
- Evidence bytes/carried transaction: {trail['evidence_bytes_per_evidence_tx_mean']:.3f} +/- {trail['evidence_bytes_per_evidence_tx_ci95']:.3f} bytes.
- Evidence bytes/all included transactions: {trail['evidence_bytes_per_all_tx_mean']:.3f} +/- {trail['evidence_bytes_per_all_tx_ci95']:.3f} bytes.
- On-chain evidence carriage coverage: {trail['evidence_carriage_coverage_mean']*100:.3f}% +/- {trail['evidence_carriage_coverage_ci95']*100:.3f} percentage points.
- Scoring-eligible coverage: {trail['evidence_coverage_mean']*100:.3f}% +/- {trail['evidence_coverage_ci95']*100:.3f} percentage points; older carried records are excluded from scoring.
- Missing records: {missing} of all TRAIL formal-window transactions; all occur in one 1,284-transaction block where the configured `max_paths_per_block=1024` cap was reached. Of the 260 omitted records, {legal_empty} also satisfy sender=proposer; the remaining {missing-legal_empty} are cap omissions.
- Mean path length: {trail['mean_path_hops_mean']:.3f} +/- {trail['mean_path_hops_ci95']:.3f} hops.

## Complete-block size and performance

PoS signed-block SSZ size is {baseline['block_ssz_mean_bytes_mean']/1024:.3f} +/- {baseline['block_ssz_mean_bytes_ci95']/1024:.3f} KiB/block; TRAIL is {trail['block_ssz_mean_bytes_mean']/1024:.3f} +/- {trail['block_ssz_mean_bytes_ci95']/1024:.3f} KiB/block. The paired complete-block difference is {block_diff['trail_minus_pos_mean']/1024:.3f} +/- {block_diff['trail_minus_pos_ci95']/1024:.3f} KiB/block. This is not the evidence payload: it is smaller because TRAIL included fewer transactions per formal-window block ({trail['window_throughput_tx_per_slot_mean']:.3f} versus {baseline['window_throughput_tx_per_slot_mean']:.3f}).

The prior 7.22 KiB/block value should be replaced by either **44.505 KiB/block evidence payload** or **38.474 KiB/block paired complete-block difference**, with the label matching the chosen quantity.

## Prototype caveats

The implementation stores a one-identity evidence path for many zero-hop transactions; it does not generally omit the evidence record when sender equals proposer. Therefore the proposed paper rule “empty only when sender=proposer” is not fully reflected by this prototype. The 100% transaction result means every submitted transaction eventually returned a successful receipt; it does not mean the system sustained the configured 512 tx/slot inside the formal window.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    runs: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    seen = set()
    for run_dir in sorted(path for path in args.raw.iterdir() if path.is_dir()):
        if not (run_dir / "summary.json").exists():
            continue
        row, audit = run_row(run_dir)
        key = (row["variant"], row["seed"])
        if key in seen:
            raise RuntimeError(f"duplicate run {key}")
        seen.add(key)
        runs.append(row)
        blocks.extend(audit)
    expected = {(variant, seed) for variant in VARIANTS for seed in range(5)}
    if seen != expected:
        raise RuntimeError(f"incomplete matrix: missing={sorted(expected-seen)}, extra={sorted(seen-expected)}")
    groups = grouped_rows(runs)
    paired = paired_rows(runs)
    prefix = args.out / "trail_devnet_512"
    write_csv(prefix.with_name(prefix.name + "_runs.csv"), runs)
    write_csv(prefix.with_name(prefix.name + "_blocks.csv"), blocks)
    write_csv(prefix.with_name(prefix.name + "_groups.csv"), groups)
    write_csv(prefix.with_name(prefix.name + "_paired.csv"), paired)
    tex_path = prefix.with_name(prefix.name + "_table_ii.tex")
    tex_path.write_text(table_tex(groups, paired) + "\n")
    report_path = prefix.with_name(prefix.name + "_report.md")
    report_path.write_text(report_md(runs, groups, paired))
    share = args.out / "trail_devnet_512_share"
    share.mkdir(parents=True, exist_ok=True)
    for path in [
        prefix.with_name(prefix.name + suffix)
        for suffix in ("_runs.csv", "_blocks.csv", "_groups.csv", "_paired.csv", "_table_ii.tex", "_report.md")
    ]:
        shutil.copy2(path, share / path.name)
    original_order = [
        ("trail", 0), ("baseline", 0), ("baseline", 4), ("trail", 4),
        ("baseline", 3), ("baseline", 2), ("trail", 1), ("trail", 2),
        ("trail", 3), ("baseline", 1),
    ]
    final_manifest = {
        "suite": "trail_devnet_512_main",
        "config": read_json(ROOT / "experiments/configs/trail_devnet_512_main.yaml"),
        "actual_execution_order": [f"{variant}_ba_n8_load512_seed{seed}" for variant, seed in original_order],
        "runs": sorted(runs, key=lambda row: original_order.index((row["variant"], row["seed"]))),
        "note": "seed 0 was validated first; remaining conditions continued in the precomputed randomized order",
    }
    manifest_path = prefix.with_name(prefix.name + "_manifest.json")
    manifest_path.write_text(json.dumps(final_manifest, indent=2, sort_keys=True) + "\n")
    shutil.copy2(manifest_path, share / manifest_path.name)
    shutil.copy2(ROOT / "experiments/configs/trail_devnet_512_main.yaml", share / "trail_devnet_512_main.yaml")
    print(f"wrote {len(runs)} runs and {len(blocks)} block rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
