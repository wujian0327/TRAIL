#!/usr/bin/env python3
"""Report every requested PoS/TRAIL pilot load, including failed or pending runs."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from statistics import mean
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
from audit_r1_1_performance import histogram_quantiles


def extract_metrics(summary):
    window = summary["workload"]["measurement_window"]
    formal = summary["formal_experiment"]
    spec, resources, prometheus = (formal[key] for key in ["spec", "resources", "prometheus"])
    blocks = summary["blocks"]["audit_rows"]
    txs = sum(block["block_transaction_count"] for block in blocks)
    missing = sum(block["missing_evidence_transaction_count"] for block in blocks)
    certificates = txs - missing
    eligible = sum(block["valid_evidence_unique_transaction_count"] for block in blocks)
    baseline = spec["variant"] == "baseline"
    lat = dict(sample_count=0, p95_estimate_ms=None, p99_estimate_ms=None)
    if not baseline:
        lat = histogram_quantiles(prometheus["evidence_verify_bucket_deltas"], prometheus["evidence_verify_count"])
    return dict(
        variant=spec["variant"], seed=spec["seed"], target_tx_per_slot=spec["load_tx_per_slot"],
        formal_window_seconds=window["duration_seconds"], target_tx_per_second=spec["load_tx_per_slot"] / spec["seconds_per_slot"],
        submitted_in_window=window["sent_in_window_count"], included_in_window=window["included_in_window_count"],
        submitted_tx_per_second=window["sent_in_window_count"] / window["duration_seconds"],
        included_tx_per_second=window["included_in_window_count"] / window["duration_seconds"],
        included_tx_per_slot=window["throughput_tx_per_slot"],
        block_ssz_kib=mean(block["block_ssz_bytes"] for block in blocks) / 1024,
        evidence_payload_kib=mean(block["evidence_ssz_bytes"] for block in blocks) / 1024,
        transactions_in_canonical_window_blocks=txs,
        mean_transactions_per_canonical_block=txs / len(blocks),
        evidence_certificates=certificates, evidence_coverage_pct=None if baseline or not txs else 100 * certificates / txs,
        missing_certificates=None if baseline else txs - certificates,
        scoring_eligible_certificates=None if baseline else eligible,
        peak_memory_mib_per_node=resources["per_node_memory_peak_mean_bytes"] / 2**20,
        network_rx_mib_per_node=resources["per_node_network_rx_delta_mean_bytes"] / 2**20,
        network_tx_mib_per_node=resources["per_node_network_tx_delta_mean_bytes"] / 2**20,
        network_mib_per_node=(resources["per_node_network_rx_delta_mean_bytes"] + resources["per_node_network_tx_delta_mean_bytes"]) / 2**20,
        resource_samples=resources["samples"], resource_sample_span_seconds=resources["sample_span_seconds"],
        observed_nodes=resources["node_count"],
        verification_events=lat["sample_count"], p95_ms=lat["p95_estimate_ms"], p99_ms=lat["p99_estimate_ms"],
        p95_bucket_lower_ms=lat.get("p95_bucket_lower_ms"), p95_bucket_upper_ms=lat.get("p95_bucket_upper_ms"),
        p99_bucket_lower_ms=lat.get("p99_bucket_lower_ms"), p99_bucket_upper_ms=lat.get("p99_bucket_upper_ms"),
        histogram_valid=lat.get("histogram_valid"), histogram_unbounded_tail=lat.get("p99_unbounded_tail"),
        p95_unbounded_tail=lat.get("p95_unbounded_tail"), p99_unbounded_tail=lat.get("p99_unbounded_tail"),
        verify_mean_ms=None if baseline else prometheus["evidence_verify_mean_seconds"] * 1000,
        missed_slots=summary["blocks"]["missed_slots"],
        acceptance_passed=formal["acceptance"]["passed"], measurement_quality_passed=formal["measurement_quality"]["passed"],
    )


def overhead(pos, trail, percentage=False):
    if pos is None or trail is None:
        return None
    if percentage:
        return None if pos == 0 else 100 * (trail - pos) / pos
    return trail - pos


def write_csv(path, rows):
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted({key for row in rows for key in row}))
        writer.writeheader()
        writer.writerows(rows)


def display(value, suffix=""):
    return "—" if value is None else f"{value:.3f}{suffix}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.root / "manifest.json").read_text())
    config = manifest["config"]
    output = ROOT / "results/processed" / config["suite"]
    output.mkdir(parents=True, exist_ok=True)
    rows, errors, summaries = [], [], {}
    for entry in manifest["runs"]:
        folder = args.root / entry["run_id"]
        row = dict(run_id=entry["run_id"], target_tx_per_slot=entry["load_tx_per_slot"], variant=entry["variant"], status=entry["status"], error=entry.get("error", ""))
        if (folder / "summary.json").exists():
            summary = json.loads((folder / "summary.json").read_text())
            summaries[(row["target_tx_per_slot"], row["variant"])] = summary
            try:
                row.update(extract_metrics(summary))
                row["certificate_audit"] = entry.get("per_path_certificate_audit", "not_run")
            except Exception as exc:
                errors.append(dict(run_id=entry["run_id"], error=str(exc)))
                row["analysis_error"] = str(exc)
        rows.append(row)
    protected = json.loads((args.root / "protected_files.json").read_text())
    unchanged = all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest for name, digest in protected.items())
    metrics = [
        ("Block size (KiB)", "block_ssz_kib", False),
        ("Peak memory (MiB/node)", "peak_memory_mib_per_node", True),
        ("Network traffic (MiB/node)", "network_mib_per_node", True),
        ("P95 evidence verification (ms)", "p95_ms", None),
        ("P99 evidence verification (ms)", "p99_ms", None),
        ("Missed slots", "missed_slots", False),
    ]
    tables, pairing = [], []
    lines = ["# R1-1：逐路径证书与缓存优化版 Devnet 性能 Pilot", "",
             f"状态：{manifest['status']}；8 个验证者、BA 拓扑、seed 0；3 秒/slot，预热 5 epoch、测量 5 epoch（40 slots / 120 秒）。仅 1 个配对 seed，不计算跨 seed 置信区间。", "",
             "采用相同新客户端二进制，PoS 关闭 TRAIL，TRAIL 启用逐路径独立证书和公钥缓存。各次运行依次执行，交易提交与收据查询各 64 并发。没有将目标负载当成实际吞吐量。", ""]
    for load in config["loads_tx_per_slot"]:
        pos = next(row for row in rows if row["target_tx_per_slot"] == load and row["variant"] == "baseline")
        trail = next(row for row in rows if row["target_tx_per_slot"] == load and row["variant"] == "trail")
        key_pos, key_trail = (load, "baseline"), (load, "trail")
        if key_pos in summaries and key_trail in summaries:
            hashes = lambda key: [tx["tx_hash"] for tx in summaries[key]["workload"]["txs"]]
            pairing.append(dict(target_tx_per_slot=load, same_submitted_transaction_hashes=hashes(key_pos) == hashes(key_trail),
                                pos_window_transactions=pos.get("transactions_in_canonical_window_blocks"),
                                trail_window_transactions=trail.get("transactions_in_canonical_window_blocks")))
        lines += [f"## 目标负载 {load} tx/slot", "", f"PoS：{pos['status']}；TRAIL：{trail['status']}。", "",
                  "| Metric | PoS | TRAIL | Overhead |", "|---|---:|---:|---:|"]
        for label, key, percent in metrics:
            before, after = pos.get(key), trail.get(key)
            difference = None if percent is None else overhead(before, after, percent)
            tables.append(dict(load=load, metric=label, pos=before, trail=after, overhead=difference,
                               overhead_unit="percent" if percent else "absolute"))
            bound = "≥" if key in ["p95_ms", "p99_ms"] and trail.get(key.replace("_ms", "_unbounded_tail")) else ""
            lines.append(f"| {label} | {display(before)} | {bound}{display(after)} | {display(difference, '%' if percent else '')} |")
        lines += ["", "| 检查项 | PoS | TRAIL |", "|---|---:|---:|",
                  f"| 窗口内实际提交 tx/s | {display(pos.get('submitted_tx_per_second'))} | {display(trail.get('submitted_tx_per_second'))} |",
                  f"| 窗口内纳入 tx/slot | {display(pos.get('included_tx_per_slot'))} | {display(trail.get('included_tx_per_slot'))} |",
                  f"| 平均块内交易数 | {display(pos.get('mean_transactions_per_canonical_block'))} | {display(trail.get('mean_transactions_per_canonical_block'))} |",
                  f"| 证据载荷 KiB/block | {display(pos.get('evidence_payload_kib'))} | {display(trail.get('evidence_payload_kib'))} |",
                  f"| 证据覆盖率 % | — | {display(trail.get('evidence_coverage_pct'))} |",
                  f"| 验证事件数 | — | {display(trail.get('verification_events'))} |", ""]
    lines += ["## 统计口径与限制", "",
              "完整 signed SSZ 区块大小按测量窗口内实际块取均值；TRAIL−PoS 是完整区块差值，不能直接解释为证据编码大小。若两组实际纳入交易数不同，需同时参考工作负载和独立证据载荷；所有请求的负载都保留。", "",
              "内存是正式窗口内每节点 EL+CL 的采样峰值，再对 8 个节点取均值，属于观测峰值。流量是相同窗口内 EL+CL 的 TX+RX 计数增量，再对节点取均值；RX/TX 单列保留在 runs.csv。Docker stats 数据有显示舍入，首末采样间隔可能短于 120 秒，实际覆盖时长和样本数均保留。", "",
              "P95/P99 为 per-block TRAIL evidence verification latency 的直方图插值估计；汇总所有 CL 节点的验证事件，重复验证同一块可再次计数。它不是区块传播延迟或完整区块端到端验证时间。分位数所在桶和样本数保留在 runs.csv；末端无界桶会标记。PoS 无该指标，显示为 —。", "",
              "吞吐量仅统计半开正式窗口 [start,end) 内的实际纳入；窗口结束后的排空不计入窗口吞吐量。覆盖率的分母为窗口内全部已纳入交易，未认证、数量上限和工作预算遗漏均保留。每条链上签名另外核对提议者的 Geth RPC，CL 日志保留逐交易遗漏原因。", "",
              "失败或分析错误不会被替换成成功结果；状态和错误保留在 manifest.json、runs.csv 与 validation.json。单 seed Pilot 用于发现问题，不直接更新论文。", ""]
    write_csv(output / "runs.csv", rows)
    write_csv(output / "tables.csv", tables)
    write_csv(output / "pairing.csv", pairing)
    valid = manifest["status"] == "completed" and not errors and unchanged and all(row["status"] == "ok" for row in rows) and all(pair["same_submitted_transaction_hashes"] for pair in pairing)
    validation = dict(passed=valid, stage=manifest["status"], protected_files_unchanged=unchanged,
                      analysis_errors=errors, completed_runs=sum(row["status"] == "ok" for row in rows),
                      total_runs=len(rows), pairing=pairing)
    (output / "validation.json").write_text(json.dumps(validation, indent=2) + "\n")
    (output / "report.md").write_text("\n".join(lines))
    print(json.dumps(dict(output=str(output), **validation), indent=2))


if __name__ == "__main__":
    main()
