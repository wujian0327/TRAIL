#!/usr/bin/env python3
"""Summarize the opt-in Lighthouse path verifier microbenchmark."""

import argparse
import csv
import hashlib
import json
from pathlib import Path
from statistics import mean, median


ROOT = Path(__file__).resolve().parents[1]
STAGES = [
    "signature_decode_ns", "key_lookup_ns", "key_decode_ns",
    "message_build_ns", "bls_verify_ns",
]


def read_samples(path):
    with path.open() as handle:
        rows = list(csv.DictReader(handle))
    seen = set()
    for row in rows:
        for key in row.keys() - {"algorithm"}:
            row[key] = int(row[key])
        identity = tuple(row[key] for key in ["algorithm", "paths", "hops", "iteration"])
        assert identity not in seen, f"duplicate measurement: {identity}"
        seen.add(identity)
        assert row["wall_ns"] > 0
        if row["algorithm"] != "optimized_cold":
            assert row["profiled_total_ns"] >= sum(row[key] for key in STAGES)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=ROOT / "results/raw/devnet_path_verify_profile_20261118")
    parser.add_argument("--output", type=Path, default=ROOT / "results/processed/devnet_path_verify_profile_20261118")
    args = parser.parse_args()
    rows = read_samples(args.raw / "paired_profile.csv")
    initial = read_samples(args.raw / "initial_profile.csv")
    protected = json.loads((args.raw / "protected_files.json").read_text())
    assert all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest for name, digest in protected.items())
    tests = json.loads((args.raw / "validation_checks.json").read_text())
    assert tests["passed"]
    summaries = []
    stages = []
    for paths, hops in sorted({(row["paths"], row["hops"]) for row in rows}):
        group = [row for row in rows if (row["paths"], row["hops"]) == (paths, hops)]
        baseline = [row for row in group if row["algorithm"] == "reference"]
        optimized = [row for row in group if row["algorithm"] == "optimized"]
        cold = [row for row in group if row["algorithm"] == "optimized_cold"]
        assert len(baseline) == len(optimized) >= 20 and len(cold) == 1
        assert {r["iteration"] for r in baseline} == {r["iteration"] for r in optimized}
        before = mean(r["wall_ns"] for r in baseline) / 1e6
        after = mean(r["wall_ns"] for r in optimized) / 1e6
        summaries.append(dict(
            paths=paths, hops=hops, paired_repetitions=len(baseline),
            reference_mean_ms=before, optimized_mean_ms=after,
            reference_median_ms=median(r["wall_ns"] for r in baseline) / 1e6,
            optimized_median_ms=median(r["wall_ns"] for r in optimized) / 1e6,
            mean_reduction_pct=100 * (before - after) / before,
            optimized_first_call_including_cache_init_ms=cold[0]["wall_ns"] / 1e6,
        ))
        for algorithm, samples in [("reference", baseline), ("optimized", optimized)]:
            total = mean(row["profiled_total_ns"] for row in samples)
            stages.append(dict(
                paths=paths, hops=hops, algorithm=algorithm,
                profiled_total_ms=total / 1e6,
                **{key.replace("_ns", "_ms"): mean(row[key] for row in samples) / 1e6 for key in STAGES},
                bls_share_pct=100 * mean(row["bls_verify_ns"] for row in samples) / total,
            ))
    args.output.mkdir(parents=True, exist_ok=True)
    for filename, data in [("comparison.csv", summaries), ("stages.csv", stages)]:
        with (args.output / filename).open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(data[0]))
            writer.writeheader()
            writer.writerows(data)
    initial_total = mean(r["profiled_total_ns"] for r in initial)
    initial_summary = {key: mean(r[key] for r in initial) / 1e6 for key in STAGES}
    initial_crypto_pct = 100 * mean(r["bls_verify_ns"] for r in initial) / initial_total
    validation = dict(
        passed=True, tests=tests, protected_files_unchanged=True,
        raw_samples=len(rows), initial_samples=len(initial),
        scope="synthetic local release-mode microbenchmark; no new Devnet runs",
        cross_path_batch_verification_in_production=False,
        reference_signature_cancellation_negative_control_passed=True,
        production_independent_signature_checks_reject_cancellation=True,
        measurements=summaries,
    )
    (args.output / "validation.json").write_text(json.dumps(validation, indent=2) + "\n")
    lines = [
        "# Lighthouse 逐路径证书验证 Profiling 与优化", "",
        "仅修改 Lighthouse 的验证实现与本地测试。每条路径的独立 48 字节证书、Geth 输出、SSZ 结构、交易/Epoch 绑定及证据限额均未改变。Rust 仿真器、原 Smoke 和 R1-1 报告通过哈希核对保持不变；论文及 SSZ 类型未修改，Git 差异检查通过。", "",
        "## 优化前瓶颈", "",
        f"真实 BLS 签名、本地合成 16 条证书、每条 3 跳，初始 30 次 release 测量：密码学验证占 {initial_crypto_pct:.1f}%，公钥重复解码平均 {initial_summary['key_decode_ns']:.3f} ms。分解计时的总耗时与无分解计时的完整验证耗时另存，避免把计时开销当成优化效果。", "",
        "## 已采用的优化", "",
        "1. 每个不可变注册表单独缓存解析后的公钥，缓存前进行编码、非无穷点和子群检查；不缓存签名验证结论，不跨注册表复用。",
        "2. 缓存静态身份哈希，逐跳更新路径前缀，避免反复从路径起点重建前缀。",
        "3. 复用消息缓冲区并直接引用已解析公钥，减少字符串、原始密钥字节和临时签名记录的分配。",
        "4. 每份证书继续独立执行 BLS aggregate_verify，保留签名子群、身份唯一性、未知密钥、路径长度、记录数量、工作预算和整批无效证据不计信用的检查。", "",
        "## 本地对照", "",
        "相同 release 测试进程内，用相同证书分别运行冻结的旧逐路径实现和优化实现；每组预热 4 次、配对重复 30 次并交替先后顺序。表中条数是一次验证调用中的证书数，不是实测 tx/slot。输入为合成有效路径，不等同 Devnet 的真实路径分布。冷调用包括该注册表首次建立缓存的成本；每组合成注册表仅有 hops+1 个密钥，冷成本不能外推到更大的注册表。", "",
        "| 证书数 | 每条跳数 | 优化前均值 ms | 优化后均值 ms | 降低 | 首次调用 ms |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(f"| {row['paths']} | {row['hops']} | {row['reference_mean_ms']:.3f} | {row['optimized_mean_ms']:.3f} | {row['mean_reduction_pct']:.1f}% | {row['optimized_first_call_including_cache_init_ms']:.3f} |")
    lines += [
        "", "分解结果见 stages.csv。旧版的消息重建阶段也包含路径地址解析和注册存在性检查；优化版将这些检查归入密钥查找阶段。各阶段之间的单独变化不应全部解释为算法提速。优化版稳态密钥解码为零，其一次性解析与子群检查成本包含在冷调用中。未对这些重复调用计算跨 seed 置信区间。", "",
        "## 批量验证评估", "",
        "新增真实 BLS 抵消攻击测试：将两份原签名分别加上 Δ 与 −Δ。它们逐条验证都无效，但直接跨路径相加后的一次验证会通过；当前生产实现会拒绝。安全批量验证需要验证者在收到固定输入后选择独立、不可预测、非零的随机权重，并在加权前完成公钥/签名子群检查。", "",
        "本地依赖 blst 0.3.13 的 verify_multiple_aggregate_signatures 接口每份签名只对应一个消息与公钥；路径证书覆盖多条不同消息，无法直接套用。接入需要额外的多消息加权实现、随机数失败处理、负面测试和审查。本轮只完成评估和反例测试，没有将自写批量密码学算法接入共识。算法依据：[Fast verification of multiple BLS signatures](https://ethresear.ch/t/fast-verification-of-multiple-bls-signatures/5407)。", "",
        "## 正确性与下一阶段", "",
        f"Lighthouse 相关正确性测试 {tests['lighthouse_correctness_tests']} 项通过；两次显式 Profiling 运行通过；cargo check、rustfmt 和差异检查通过。明细见 validation.json 及原始日志。新增测试比较 0–15 跳的消息及公钥序列与原版逐字节一致，覆盖缓存隔离、地址别名、无效/无穷点/非子群公钥、篡改和抵消攻击；保留此前的 1024 条记录和工作预算测试。", "",
        "本轮没有启动 Devnet、修改既有性能结果或更新论文，也没有重建或覆盖现有客户端镜像。先前的 12.60 ms Smoke 仍代表缓存优化前的逐路径版本；本表不可直接替代它，更不可作为 R1-1 的最终性能结果。下一步应从优化后的源代码构建独立镜像，先做优化版 Smoke，再以 64 和 256 tx/slot 检查实际提交/窗口纳入速率、证据覆盖率、尾延迟和漏槽。", "",
    ]
    (args.output / "report.md").write_text("\n".join(lines))
    print(json.dumps(dict(passed=True, output=str(args.output), comparisons=summaries), indent=2))


if __name__ == "__main__":
    main()
