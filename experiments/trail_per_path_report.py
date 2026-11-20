#!/usr/bin/env python3
"""Report one before/after per-path certificate smoke, preserving prior results."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT/'results/raw/devnet_per_path_r1c1_20261118')
    parser.add_argument('--output',type=Path,default=ROOT/'results/processed/devnet_per_path_r1c1_20261118')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    runs={phase:json.loads((args.root/(phase+'_validation.json')).read_text()) for phase in ['before','after']}
    assert all(run['passed'] for run in runs.values())
    protected=json.loads((args.root/'protected_files.json').read_text())
    checks={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest for name,digest in protected.items()}
    assert all(checks.values())
    original=[]
    for phase,run in runs.items():
        summary=json.loads(Path(run['summary_path']).read_text())
        original.append([tx['tx_hash'] for tx in summary['workload']['txs']])
    rows=[]
    for phase,run in runs.items():
        window=run['formal_window'];lat=run['verification']
        rows.append(dict(phase=phase,window_seconds=window['duration_seconds'],window_slots=window['end_slot']-window['start_slot'],
            submitted_in_window=window['sent_in_window_count'],included_in_window=window['included_in_window_count'],
            ssz_mean_kib=run['ssz_mean_bytes']/1024,evidence_payload_mean_kib=run['evidence_payload_mean_bytes']/1024,
            evidence_coverage_pct=100*run['evidence_coverage'],verification_events=lat['sample_count'],
            verify_mean_ms=run['verify_mean_ms'],p95_histogram_estimate_ms=lat['p95_estimate_ms'],p99_histogram_estimate_ms=lat['p99_estimate_ms'],
            missed_slots=run['missed_slots'],max_distinct_signatures=run['max_distinct_signatures'],
            geth_uncertified=run['geth_uncertified_count'],cl_omissions=run['cl_omissions']))
    with (args.output/'comparison.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    checks_file=args.root/'test_validation.json'
    tests=json.loads(checks_file.read_text())
    assert tests['passed'], 'Correctness checks must pass before reporting smoke results'
    report=dict(passed=True,scope='correctness + one small 4-node smoke per scheme; not a performance matrix',
        same_measurement_transaction_hashes=original[0]==original[1],protected_files_unchanged=all(checks.values()),
        tests=tests,measurements=rows,raw_root=str(args.root),
        per_path_rpc_to_ssz_signature_matches=True,ssz_schema_changed=False,
        prior_attempts=[dict(directory='before_attempt_array_adapter',reason='Network/measurement accepted; post-processing signature array decoder corrected'),
                        dict(directory='before_attempt_local_proxy',reason='Local RPC was routed to a desktop proxy; no valid measurement window')])
    (args.output/'validation.json').write_text(json.dumps(report,indent=2)+'\n')
    lines=['# Devnet per-path BLS certificate correctness report','',
        'Geth 按交易路径独立聚合，Lighthouse 每条记录使用并校验自己的签名；保留原有 origin 证明与逐跳收发签名的域、交易和 Epoch 绑定。SSZ 记录及 48 字节签名字段未变。',
        '', '## 正确性测试','',
        f"Geth：{tests['geth_top_level_tests']} 项测试通过（另有 {tests['geth_subtests']} 个子测试）；Lighthouse：{tests['lighthouse_targeted_tests']} 项定向测试通过；Python：{tests['python_tests']} 项测试通过。`cargo check`、客户端 release 构建及格式检查均通过。原始日志和命令保留在原始目录。",'',
        '覆盖不同路径的不同有效签名、旧区块级签名拒绝、交易/路径/Epoch 篡改、重复身份、未知公钥、非法签名、空路径/空证据列表、路径长度和工作预算，以及 1,025 条记录时保留 1,024 条并记录被省略交易的哈希及原因。构造时超过数量/工作预算的记录被明确记录；收到超过上限的证据列表直接拒绝计入信用。无效证据不会进入 Relay 奖励与评分累计。',
        '', '## 小规模 Devnet Smoke','',
        '每种机制仅 1 个 seed；4 节点 ring、16 tx/slot、5 个 warm-up epoch、16 个正式槽（48 秒），共 256 笔测量交易。两次依次运行，不与编译并行。签名逐条与实际提议者 Geth RPC 输出进行比较。修改后每笔被认证交易的签名均不同。',
        '', '| 机制 | 窗口纳入 | SSZ KiB/block | 证据 KiB/block | 验证均值 ms | P95 / P99 估计 ms | 事件数 | 证据覆盖率 | 漏槽 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"| {r['phase']} | {r['included_in_window']} | {r['ssz_mean_kib']:.3f} | {r['evidence_payload_mean_kib']:.3f} | {r['verify_mean_ms']:.3f} | {r['p95_histogram_estimate_ms']:.2f} / {r['p99_histogram_estimate_ms']:.2f} | {r['verification_events']} | {r['evidence_coverage_pct']:.1f}% | {r['missed_slots']} |")
    lines += ['',f"两次测量交易哈希序列完全相同：{report['same_measurement_transaction_hashes']}。",'',
        '延迟指标是 per-block TRAIL evidence verification latency；P95/P99 为同窗口直方图插值估计，样本少、桶粗，不能视为精确分位数或完整区块端到端验证时间。只有一个 Smoke 对照，不能据此作统计显著性或持续吞吐能力结论。相同路径/记录数的 SSZ 原始长度不随签名内容变化；实测块均值还取决于实际纳入和路径长度。',
        '每组 75 个验证事件来自四个 CL 节点的直方图增量之和，不等于 75 个不同区块。修改前 P95/P99 均落在 0–5 ms 桶，修改后均落在 10–25 ms 桶。两次均提交 256 笔测量交易，48 秒正式窗口内纳入 232 笔（4.833 tx/s、14.5 tx/slot）；窗口结束后纳入的交易不计入该吞吐量。证据覆盖率以窗口内所有已纳入交易为分母，包含被截断交易；认证覆盖率也不代表过期证据可以获得评分。',
        '原镜像版本为 Lighthouse v8.2.0-cfb411b，修改版 v8.2.0-8caf1df，均为 release、blst、mainnet+minimal preset；镜像与源文件指纹见原始目录。该对照不宣称隔离所有工具链差异。',
        '', '## 数据保留与清理','',
        '首轮修改前网络测量和原验收已通过，签名读取按字符串假设遇到字节数组，原数据保留；第二轮受本机代理影响未进入有效测量，亦保留。主表只使用完成端到端签名审计的运行，选择依据是验证完整性而非性能。',
        '原 :dev 镜像未覆盖，所有新输出另存；Rust 仿真器源码和五份 R1-1 原报告已逐文件哈希核对保持一致。Smoke enclave 在各次运行结束时清理。没有启动完整性能矩阵。',
        '', '复现：依次运行 `run_trail_per_path_smoke.py --phase before` 与 `--phase after`（指定独立镜像标签及新目录），再运行 `trail_per_path_report.py`。']
    (args.output/'report.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
