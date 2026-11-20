#!/usr/bin/env python3
"""540B pilot: distinguish core certificate, serialized evidence and full blocks."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path
from statistics import mean

from eth_account import Account

from report_r1_1_path_performance import ROOT, display, extract_metrics, write_csv
from trail_devnet_runner import DEFAULT_PREFUNDED_PRIVATE_KEYS, DEFAULT_RECIPIENT, normalized_hash


def distribution(values):
    values = sorted(values)
    if not values:
        return dict(count=0, min=None, mean=None, p50=None, p95=None, max=None, histogram={})
    def quantile(fraction):
        position = (len(values) - 1) * fraction
        lo = int(position)
        hi = min(lo + 1, len(values) - 1)
        return values[lo] + (values[hi] - values[lo]) * (position - lo)
    return dict(count=len(values), min=values[0], mean=mean(values), p50=quantile(.5), p95=quantile(.95),
                max=values[-1], histogram=dict(sorted(Counter(map(str, values)).items())))


def reconstruct_short(task):
    tx, chain = task
    account = Account.from_key(DEFAULT_PREFUNDED_PRIVATE_KEYS[tx["sender_index"]])
    signed = account.sign_transaction(dict(chainId=chain, to=DEFAULT_RECIPIENT, value=1, gas=21000,
        nonce=tx["nonce"], maxFeePerGas=50_000_000_000, maxPriorityFeePerGas=2_000_000_000))
    assert normalized_hash(signed.hash.hex()) == normalized_hash(tx["tx_hash"]), "short-reference reconstruction does not match archived hash"
    return normalized_hash(tx["tx_hash"]), len(signed.raw_transaction)


def summarize(summary, raw_sizes):
    row = extract_metrics(summary)
    blocks = summary["blocks"]["audit_rows"]
    all_hashes = [normalized_hash(tx) for block in blocks for tx in block["transaction_hashes"]]
    assert all(tx in raw_sizes for tx in all_hashes), "unknown transaction in canonical block"
    denominator = sum(raw_sizes[tx] for tx in all_hashes)
    if all("execution_transaction_bytes" in block for block in blocks):
        assert denominator == sum(block["execution_transaction_bytes"] for block in blocks)
    signature = sum(block["record_signature_bytes"] for block in blocks)
    indices = sum(block["record_identity_bytes"] for block in blocks)
    core = signature + indices
    serialized = sum(block["evidence_ssz_bytes"] for block in blocks)
    count = sum(block["evidence_record_count"] for block in blocks)
    assert serialized == core + 64 * count, "SSZ evidence byte decomposition mismatch"
    txs = summary["workload"]["txs"]
    succeeded = [tx for tx in txs if tx.get("status") == 1]
    gas_limits = [tx.get("gas_limit", 21000) for tx in txs]
    gas_blocks = [block for block in blocks if block.get("block_gas_limit")]
    utilization = [100 * block["block_gas_used"] / block["block_gas_limit"] for block in gas_blocks]
    exhausted = [block for block in gas_blocks if block["block_gas_limit"] - block["block_gas_used"] < min(gas_limits)]
    near_full_with_backlog = []
    genesis = summary["workload"]["genesis_time"]
    seconds_per_slot = summary["workload"]["seconds_per_slot"]
    for block in exhausted:
        block_time = genesis + block["slot"] * seconds_per_slot
        pending = [tx for tx in txs if tx.get("send_unix", float("inf")) < block_time
                   and (tx.get("included_slot", float("inf")) > block["slot"])]
        if pending:
            near_full_with_backlog.append(block["slot"])
    window = summary["workload"]["measurement_window"]
    last_included = max((tx["included_block_timestamp"] for tx in succeeded), default=window["end_unix"])
    row.update(transaction_bytes=denominator, bls_signature_bytes=signature, validator_index_bytes=indices,
        core_path_evidence_bytes=core, serialized_evidence_bytes=serialized,
        fixed_trail_body_fields_bytes=36*len(blocks),
        evidence_list_plus_fixed_body_fields_bytes=serialized+36*len(blocks),
        mean_core_evidence_bytes_per_certified_tx=core / count if count else None,
        mean_serialized_evidence_bytes_per_certified_tx=serialized / count if count else None,
        core_over_transaction_pct=100 * core / denominator if denominator else None,
        serialized_over_transaction_pct=100 * serialized / denominator if denominator else None,
        evidence_record_count=count, tx_success_rate_pct=100 * len(succeeded) / len(txs),
        measurement_tx_count=len(txs), successful_measurement_tx_count=len(succeeded),
        submitted_pending_at_window_end=max(0, window["sent_in_window_count"] - window["included_in_window_count"]),
        post_window_drain_seconds=max(0, last_included - window["end_unix"]),
        block_gas_limit_min=min((b["block_gas_limit"] for b in gas_blocks), default=None),
        block_gas_limit_max=max((b["block_gas_limit"] for b in gas_blocks), default=None),
        block_gas_used_max=max((b["block_gas_used"] for b in gas_blocks), default=None),
        gas_utilization_mean_pct=mean(utilization) if utilization else None,
        gas_utilization_max_pct=max(utilization) if utilization else None,
        gas_exhausted_blocks=len(exhausted) if gas_blocks else None,
        gas_exhausted_with_pending_slots=near_full_with_backlog,
        raw_tx_size_distribution=distribution([raw_sizes[tx] for tx in all_hashes]),
        all_submitted_raw_size_distribution=distribution([tx["raw_tx_bytes"] for tx in txs if "raw_tx_bytes" in tx]),
        gas_used_distribution=distribution([tx["gas_used"] for tx in succeeded]),
        gas_limit_distribution=distribution(gas_limits),
        calldata_size_distribution=distribution([tx["calldata_bytes"] for tx in txs if "calldata_bytes" in tx]))
    return row


def short_reference(root, suite):
    cache = root / "short_reference_verified.json"
    if cache.exists():
        return json.loads(cache.read_text())
    reference_root = ROOT / "results/raw" / suite
    manifest = json.loads((reference_root / "manifest.json").read_text())
    results = []
    for entry in manifest["runs"]:
        summary_path = reference_root / entry["run_id"] / "summary.json"
        summary = json.loads(summary_path.read_text())
        rows = [tx for phase in [summary.get("warmup"), summary["workload"]] if phase for tx in phase["txs"]]
        needed = {normalized_hash(tx) for b in summary["blocks"]["audit_rows"] for tx in b["transaction_hashes"]}
        tasks = [(tx, summary["workload"]["chain_id"]) for tx in rows if normalized_hash(tx.get("tx_hash")) in needed]
        with ProcessPoolExecutor(max_workers=8) as executor:
            sizes = dict(executor.map(reconstruct_short, tasks, chunksize=64))
        record = summarize(summary, sizes)
        record.update(run_id=entry["run_id"], source_sha256=hashlib.sha256(summary_path.read_bytes()).hexdigest(),
                      signed_raw_bytes_verified_against_archived_hashes=True)
        results.append(record)
    cache.write_text(json.dumps(results, indent=2) + "\n")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.root / "manifest.json").read_text())
    config = manifest["config"]
    output = ROOT / "results/processed" / config["suite"]
    output.mkdir(parents=True, exist_ok=True)
    old = short_reference(args.root, config["short_reference_suite"])
    rows, errors, summaries, distributions, per_block = [], [], {}, [], []
    for entry in manifest["runs"]:
        path = args.root / entry["run_id"] / "summary.json"
        if not path.exists():
            continue
        try:
            summary = json.loads(path.read_text())
            txs = [tx for phase in [summary.get("warmup"), summary["workload"]] if phase for tx in phase["txs"]]
            sizes = {normalized_hash(tx["tx_hash"]): tx["raw_tx_bytes"] for tx in txs if tx.get("tx_hash")}
            row = summarize(summary, sizes)
            row.update(run_id=entry["run_id"], status=entry["status"], raw_error=entry.get('error',''),
                       transaction_audit=entry.get("transaction_audit"), certificate_audit=entry.get("per_path_certificate_audit"))
            post_path=path.parent/'post_measurement_validation.json'
            if post_path.exists():
                post=json.loads(post_path.read_text())
                row['post_measurement_accepted']=post['measurement_accepted']
                row['observation_adjudication']=post['observation_adjudication']
                if post['transaction_audit_passed']:row['transaction_audit']='passed'
            summaries[(row["target_tx_per_slot"],row["variant"])] = summary
            for name in ["raw_tx_size_distribution", "all_submitted_raw_size_distribution", "gas_used_distribution", "gas_limit_distribution", "calldata_size_distribution"]:
                value = row.pop(name)
                distributions.append(dict(run_id=entry["run_id"], metric=name, **value))
            rows.append(row)
            for b in summary["blocks"]["audit_rows"]:
                per_block.append(dict(run_id=entry["run_id"], **{k:v for k,v in b.items() if k not in ["transaction_hashes","measurement_transaction_hashes","legal_empty_transaction_hashes"]}))
        except Exception as exc:
            errors.append(dict(run_id=entry["run_id"], error=repr(exc)))
    pairs, comparisons = [], []
    lines = ["# 540B Type-2 交易 Devnet 性能 Pilot", "", f"运行状态：{manifest['status']}。仅 seed 0，8 节点 BA；每次预热 5 epoch、测量 40 slots / 120 秒。", "",
        "所有交易在测量窗口前完成精确签名长度校准和签名恢复检查；Calldata 是绑定 seed、phase、序号的确定性非零字节。Gas Limit 使用实际节点对最大可能非零 Calldata 的 eth_estimateGas 结果加 10% 余量，限于 EOA 接收者；收据与签名数据在测量后独立核验。协议、BLS、客户端镜像、Rust 仿真器和论文保持不变。", "",
        "## 三个不同的开销口径", "",
        "Core Path Evidence = 48B 聚合签名 + 8B × 路径中的 Validator Index 数（包括 origin）。它不包含交易哈希、Epoch、费用字段和 SSZ 偏移。", "",
        "Actual Serialized Evidence 是真实 SSZ 证据列表的编码字节数；本结构每条在 Core 之外还有 32B 交易哈希、8B Epoch、16B 费用和 8B 偏移，共 64B。逐块重编码结果与保存的完整 SSZ 校验，不能用 Core 替代实际编码大小。", "",
        "块级另有固定的 32B trail_evidence_root 和 4B 证据列表字段偏移；当前 PoS/TRAIL 使用相同客户端 SSZ 结构，PoS 也保留这 36B 固定字段。它们单列在 runs.csv，不混入证据列表的编码字节；完整 SSZ 区块大小已包括它们。", "",
        "两个百分比的分母均为同一测量窗口内链上全部交易的真实 signed raw transaction bytes 总和，不包含 SSZ 交易列表偏移。Full SSZ Block-Size Difference 是配对运行中完整签名区块的均值差，不等同证据载荷；实际纳入量不同时会受交易量差异影响。", ""]
    if len(rows)==6 and all(r.get('post_measurement_accepted') for r in rows):
        lines[3:3]=["**六组数据均已通过独立交易/证书复核。原始运行有一项 Epoch 边界采样告警，其 failed 标记保留，处理依据在报告末尾。**", ""]
    if manifest.get('smoke',{}).get('status')=='passed':
        smoke_folder=Path(manifest['smoke']['summary_path']).parent
        audit=json.loads((smoke_folder/'transaction_audit.json').read_text())
        lines += ["## Smoke 正确性", "",
            f"4 节点、16 tx/slot：独立核验 {audit['checked_transactions']} 笔交易（640 warm-up、256 测量），全部精确 540B、签名恢复正确、收据成功且 Gas 足够。逐路径签名与实际提议者 Geth 输出逐条匹配；完整原始审计见 Smoke 目录。", ""]
    for load in config["loads_tx_per_slot"]:
        pos = next((r for r in rows if r["target_tx_per_slot"]==load and r["variant"]=="baseline"),None)
        trail = next((r for r in rows if r["target_tx_per_slot"]==load and r["variant"]=="trail"),None)
        lines += [f"## {load} tx/slot", ""]
        if not pos or not trail:
            lines += ["该负载的配对结果尚未齐全；已完成数据见 runs.csv。", ""]
            continue
        key = lambda variant: (load,variant)
        hashes = lambda variant: [tx["tx_hash"] for tx in summaries[key(variant)]["workload"]["txs"]]
        warmup_hashes = lambda variant: [tx["tx_hash"] for tx in summaries[key(variant)]["warmup"]["txs"]]
        pair = dict(load=load, same_measurement_hashes=hashes("baseline")==hashes("trail"),
                    same_warmup_hashes=warmup_hashes("baseline")==warmup_hashes("trail"),
                    full_ssz_block_difference_kib=trail["block_ssz_kib"]-pos["block_ssz_kib"],
                    pos_mean_transactions=pos["mean_transactions_per_canonical_block"], trail_mean_transactions=trail["mean_transactions_per_canonical_block"])
        pairs.append(pair)
        lines += [f"PoS / TRAIL 状态：{pos['status']} / {trail['status']}。", ""]
        lines += ["| 路径证据指标 | TRAIL |", "|---|---:|"]
        for label,key_name in [("BLS 签名总字节数","bls_signature_bytes"),("Validator Index 总字节数","validator_index_bytes"),
            ("Core Path Evidence 总字节数","core_path_evidence_bytes"),("实际 SSZ Evidence Payload 总字节数","serialized_evidence_bytes"),
            ("每条获认证交易平均 Core 字节数","mean_core_evidence_bytes_per_certified_tx"),
            ("每条获认证交易平均实际证据字节数","mean_serialized_evidence_bytes_per_certified_tx"),
            ("Core / Transaction Bytes (%)","core_over_transaction_pct"),("Actual Serialized / Transaction Bytes (%)","serialized_over_transaction_pct")]:
            lines.append(f"| {label} | {display(trail[key_name])} |")
        lines += ["", "| 系统与运行质量 | PoS | TRAIL |", "|---|---:|---:|"]
        for label,k in [("完整 SSZ KiB/block","block_ssz_kib"),("内存峰值均值 MiB/node","peak_memory_mib_per_node"),
            ("RX MiB/node/window","network_rx_mib_per_node"),("TX MiB/node/window","network_tx_mib_per_node"),
            ("P95 证据验证估计 ms","p95_ms"),("P99 证据验证估计 ms","p99_ms"),("验证事件样本数","verification_events"),
            ("漏槽","missed_slots"),("实际提交 tx/s","submitted_tx_per_second"),("窗口纳入 tx/slot","included_tx_per_slot"),
            ("交易成功率 %","tx_success_rate_pct"),("证据覆盖率 %","evidence_coverage_pct"),
            ("区块 Gas 使用率最大值 %","gas_utilization_max_pct"),("Gas 接近上限的块数","gas_exhausted_blocks"),
            ("窗口末已提交未纳入交易数","submitted_pending_at_window_end"),("窗口后排空秒数","post_window_drain_seconds")]:
            lines.append(f"| {label} | {display(pos.get(k))} | {display(trail.get(k))} |")
        lines += ["", f"**Full SSZ Block-Size Difference：{pair['full_ssz_block_difference_kib']:.3f} KiB/block。** 配对测量和 warm-up 交易哈希一致：{pair['same_measurement_hashes']} / {pair['same_warmup_hashes']}。", "",
            f"P95 所在桶 [{trail['p95_bucket_lower_ms']}, {trail['p95_bucket_upper_ms']}] ms；P99 所在桶 [{trail['p99_bucket_lower_ms']}, {trail['p99_bucket_upper_ms']}] ms。均为汇总所有 CL 节点事件的粗粒度直方图线性插值，重复验证同一块可计入多次，不是精确原始样本分位数或区块传播延迟。", ""]
        lines += ["| 测量交易分布（count / min / mean / P95 / max） | PoS | TRAIL |", "|---|---|---|"]
        for label,metric in [("实际签名长度 B","all_submitted_raw_size_distribution"),("Calldata 长度 B","calldata_size_distribution"),("Gas Limit","gas_limit_distribution"),("实际 Gas Used","gas_used_distribution")]:
            values=[]
            for current in [pos,trail]:
                d=next(d for d in distributions if d['run_id']==current['run_id'] and d['metric']==metric)
                values.append(" / ".join(display(d[k]) for k in ['count','min','mean','p95','max']))
            lines.append(f"| {label} | {values[0]} | {values[1]} |")
        lines.append("")
        for current in [pos,trail]:
            reference = next(r for r in old if r["target_tx_per_slot"]==load and r["variant"]==current["variant"])
            comparisons.append(dict(load=load,variant=current["variant"],
                short_raw_bytes_mean=reference["raw_tx_size_distribution"]["mean"],
                short_raw_bytes_min=reference["raw_tx_size_distribution"]["min"],short_raw_bytes_max=reference["raw_tx_size_distribution"]["max"],
                core_pct_if_only_denominator_changed=100*reference['core_path_evidence_bytes']/(reference['transactions_in_canonical_window_blocks']*540),
                serialized_pct_if_only_denominator_changed=100*reference['serialized_evidence_bytes']/(reference['transactions_in_canonical_window_blocks']*540),
                **{f"short_{name}":reference[name] for name in ["mean_core_evidence_bytes_per_certified_tx","mean_serialized_evidence_bytes_per_certified_tx","core_over_transaction_pct","serialized_over_transaction_pct","block_ssz_kib","included_tx_per_slot","verify_mean_ms","peak_memory_mib_per_node","network_rx_mib_per_node","network_tx_mib_per_node","p95_ms","p99_ms"]},
                **{f"sized_{name}":current[name] for name in ["mean_core_evidence_bytes_per_certified_tx","mean_serialized_evidence_bytes_per_certified_tx","core_over_transaction_pct","serialized_over_transaction_pct","block_ssz_kib","included_tx_per_slot","verify_mean_ms","peak_memory_mib_per_node","network_rx_mib_per_node","network_tx_mib_per_node","p95_ms","p99_ms"]}))
    lines += ["## 与此前短交易的比较", "",
        "此前常称为 120B 的工作负载不直接假定为 120B：对原运行中的每笔窗口交易，按其 chainId、sender、nonce 和原费用/空 data 规则重新签名，并与保存的交易哈希逐笔匹配，验证真实长度。结果见 short_comparison.csv 和原始目录 short_reference_verified.json。旧文件只读。", "",
        "540B 不改变每条签名的 48B 和每个身份的 8B。单位证据字节数的绝对变化只能来自实际路径长度分布；百分比则还受交易字节分母增大影响，不能把这一变化称为证书压缩。完整块、流量、内存、Gas 和实际吞吐量均为新的实测绝对值；完整块差值还受两组实际纳入数量及区块内容差异影响。", "",
        "Gas 积压检查以各块实际 Gas Used/Limit，以及剩余 Gas 是否足以容纳下一笔交易的 Gas Limit 为依据。若没有接近 Gas 上限的块，窗口末的短暂未纳入不能直接归因为 Gas 上限；相关槽、窗口后排空和实际提交量保留在数据中。内存与网络沿用 EL+CL 每节点口径，内存为观测峰值后对节点平均，RX/TX 为窗口首末采样计数差，采样覆盖时长亦保留。", ""]
    lines += ["| 负载 | 旧 Core % | 仅把分母改为 540B 的 Core % | 实测 Core % | 旧实际编码 % | 仅改分母的编码 % | 实测编码 % |", "|---:|---:|---:|---:|---:|---:|---:|"]
    for c in comparisons:
        if c['variant']=='trail':
            lines.append(f"| {c['load']} | {c['short_core_over_transaction_pct']:.3f} | {c['core_pct_if_only_denominator_changed']:.3f} | {c['sized_core_over_transaction_pct']:.3f} | {c['short_serialized_over_transaction_pct']:.3f} | {c['serialized_pct_if_only_denominator_changed']:.3f} | {c['sized_serialized_over_transaction_pct']:.3f} |")
    lines += ["", "上表的中间列固定旧证据和交易数量，只替换分母；中间列与本次实测之间的剩余差异来自路径/交易数量和覆盖差异。费用的 Gas 单价配置不变，但 Calldata 增加会提高实际 Gas Used 与实际支付费用。", ""]
    adjudications=[r for r in rows if r.get('observation_adjudication')]
    if adjudications:
        lines += ["## 原始验收时序告警", "",
            "以下运行的原始 failed 状态和验收文件均保留，没有重跑选择较好结果。只在 Beacon 快照位于 Epoch 最后一槽、所有 Geth 同时前进恰好一个 Epoch、其余验收与测量质量全通过时，才按观测边界竞态处理。交易和链上每条 BLS 证书另做离线重放验证。该复核没有修改协议或 BLS 验证算法，也不冒充已执行的活体 RPC 对应核对。", ""]
        for r in adjudications:
            lines.append(f"- {r['run_id']}: {json.dumps(r['observation_adjudication'],ensure_ascii=False)}")
        lines.append("")
    protected=json.loads((args.root/"protected_files.json").read_text())
    unchanged=all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest for name,digest in protected.items())
    complete=manifest["status"] in ['completed','completed_with_failures'] and len(rows)==6
    valid=complete and not errors and unchanged and all((r["status"]=="ok" or r.get('post_measurement_accepted')) and r["transaction_audit"]=="passed" for r in rows) and all(p["same_measurement_hashes"] and p["same_warmup_hashes"] for p in pairs)
    for name,data in [("runs.csv",rows),("distributions.csv",distributions),("blocks.csv",per_block),("paired.csv",pairs),("short_comparison.csv",comparisons)]:
        write_csv(output/name,[{key:json.dumps(value,sort_keys=True) if isinstance(value,(dict,list)) else value for key,value in row.items()} for row in data])
    (output/"report.md").write_text("\n".join(lines))
    validation=dict(passed=valid,status=manifest["status"],completed_runs=sum(e["status"]=="ok" for e in manifest["runs"]),
                    protected_files_unchanged=unchanged,errors=errors,pairing=pairs,raw_tx_target_bytes=config["raw_tx_target_bytes"],
                    validated_measurements=sum(bool(r['status']=='ok' or r.get('post_measurement_accepted')) for r in rows),
                    observation_adjudications=[r['run_id'] for r in adjudications],
                    analysis_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (output/"validation.json").write_text(json.dumps(validation,indent=2)+"\n")
    print(json.dumps(validation,indent=2))


if __name__=="__main__":
    main()
