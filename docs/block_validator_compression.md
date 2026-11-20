# TRAIL 区块级 Validator Index 压缩实验

4B 索引在历史 BA 区块上平均节省 **16.16%（100 验证者）**和 **10.85%（12 验证者）**。Adaptive 分别为 **16.18%** 和 **10.87%**，达到约 25% 的只有 48,240 个区块中的 1 个。当前样本的收益不足以支持正式采用字典或自适应格式。若后续需要降低存储，优先评估带 `u64` 回退的 Fixed 4B。 [逐块 CSV](../results/processed/block_validator_compression/block_compression.csv) · [历史汇总](../results/processed/block_validator_compression/archive_savings_summary.csv)

## 数据与可比性

| 历史来源 | 拓扑与名册 | 运行数 | 区块数 | 每块交易数范围 | 每块 K 范围 | 记录的跳数范围 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Selective withholding 主批次 | BA，100 个验证者 | 120 | 48,000 | 2–46 | 5–50 | 1–9 |
| Withholding 开发检查 | BA，12 个验证者 | 12 | 240 | 2–14 | 1–8 | 1–5 |

档案提供每笔交易的有序地址路径及独立签名。实验使用钱包种子重建 132 份完整注册表，与持久化的 `initial_stakes.json` 名册逐一核对，再按当前 `ValidatorRegistry` 的地址排序规则恢复全局 `u64` 索引。全部 48,240 个区块的原始路径 Root 和头部哈希均通过核对，共 922,290 份带签名路径。每个运行取一份恢复的证书做无缓存 BLS 验证。四种格式都对每个区块实际序列化并逐项解码比对。CSV 的 `index_source` 标为 `reconstructed_from_full_roster`。 [来源清单](../results/processed/block_validator_compression/archive_provenance.csv) · [索引注册表](../src/consensus/validator_registry.rs#L36) · [档案读取器](../src/bin/block_validator_compression/archive.rs)

纳入统计的是两个完整归档批次。区块等权，部分运行共享种子，这些结果按归档区块做描述性统计。主批次和开发检查的每块索引重用率均值分别为 **51.39%** 和 **34.73%**。`blockchain.json` 也含逐条地址路径，但缺少可直接归因的运行元数据和完整名册。Coalition 日志提供路径选择记录，其他 Rust 指标的长度汇总无法还原完整区块的路径集合。可用的 Ring 和 Star 记录属于 Devnet。纳入分析的历史区块至多保存 46 笔交易，100、1,000、4,000 笔仅由合成结果覆盖。 [主批次](../results/raw/trail_withholding_main_v2) · [开发检查](../results/raw/withholding-devcheck-20261117-170456)

## 序列化计量

`N` 为带 48 B 签名的路径数量，`H` 为保存的索引出现次数，`K` 为区块内不同索引数量。路径保持原交易顺序。零跳和未认证占位记录在原位置使用长度 0，签名字段为空。历史平均跳数以 `N` 为分母，直方图包含长度 0，重复出现次数为 `H−K`。

| 编码 | Core Evidence Bytes | 实际编码附加字段 |
| --- | --- | --- |
| Baseline 8B | `48N + 8H` | 1 B 模式，4 B 路径记录计数，每条记录 2 B 跳数 |
| Fixed 4B | `48N + 4H`，存在超过 `u32::MAX` 的索引时为 `48N + 8H` | 与 Baseline 相同 |
| Block Dictionary | `48N + wH + 8K` | 公共附加字段及 4 B 字典项数。全局索引升序写入，每个字典项 8 B |
| Adaptive | 所选模式的核心字段 | 三种格式中选择实际字节数最小的一种，复用相同的模式标记。等长时优先固定宽度 |

局部索引宽度 `w` 在 `K ≤ 256` 时为 1 B，`257 ≤ K ≤ 65,536` 时为 2 B，之后为 4 B。CSV 的 `dictionary_entries_bytes` 为 `8K`，`dictionary_overhead_bytes` 为 `8K+4`。`baseline_bytes` 等列由实际向量长度得到，四种格式的 `*_core_bytes` 单列。`compression_ratio` 是 Adaptive 总字节数除以 Baseline 总字节数，`saving_pct` 是对应节省率。计量范围为路径负载和压缩需用的帧，原交易、Chain ID、Epoch 和头部承诺属于外部区块上下文。四跳单路径的核心为 80 B 和 64 B，在统一实验帧下实际为 87 B 和 71 B。 [编码器](../src/bin/block_validator_compression/codec.rs)

## 历史结果

所有节省率采用实际编码字节数。负值代表体积增加。

| 历史组 | 编码 | 比较基线 | 平均节省 | 最小节省 | 最大节省 | 按字节汇总节省 |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| BA 100 | Fixed 4B | 8B | 16.16% | 8.87% | 22.55% | 16.20% |
| BA 100 | Dictionary | 8B | 12.43% | −6.37% | 26.43% | 12.74% |
| BA 100 | Adaptive | 8B | 16.18% | 8.87% | 26.43% | 16.23% |
| BA 100 | Dictionary | Fixed 4B | −4.43% | −25.56% | 5.27% | −4.13% |
| BA 12 | Fixed 4B | 8B | 10.85% | 6.61% | 16.00% | 10.73% |
| BA 12 | Dictionary | 8B | 3.58% | −3.87% | 12.42% | 4.79% |
| BA 12 | Adaptive | 8B | 10.87% | 6.61% | 16.00% | 10.77% |
| BA 12 | Dictionary | Fixed 4B | −8.19% | −19.25% | 0.68% | −6.66% |

合计 48,240 块，Fixed 4B 平均节省 **16.13%**，范围为 6.61% 至 22.55%。字典平均节省 **12.38%**，范围为 −6.37% 至 26.43%。Adaptive 平均节省 **16.15%**，范围为 6.61% 至 26.43%。字典比 4B 更省的有 1,506／48,000 个 BA 100 区块，以及 6／240 个 BA 12 区块。启用 Adaptive 后，BA 100 每块较 4B 平均只少 **0.40 B**。Adaptive 在全部历史与合成结果中等于最小的实际编码字节数，保证以相同实验帧的 8B 格式为上界。 [历史汇总](../results/processed/block_validator_compression/archive_savings_summary.csv)

## 合成敏感性与失去收益的条件

`N ∈ {100, 1000, 4000}`，`h ∈ {2, 4, 6}`，`K ∈ {16, 64, 128, 256, 512, 1024}` 的网格有 48 个可行点。另加入每种 `N,h` 的 `K=H` 无重用端点，以及一个触发 4B 回退的宽索引点。6 个 `K>H` 的组合不可行。路径从确定性循环序列得到，每个块精确覆盖目标 `K`，每条路径内身份唯一。签名使用 48 B 占位内容，BLS 和经济回归使用独立的真实签名夹具。 [不可行组合](../results/processed/block_validator_compression/synthetic_unavailable.csv)

以四跳为例，Fixed 4B 核心节省 **20%**，在 `N=1000` 的实际帧中节省 **19.51%**。`K=64` 时四种实际编码依次为 **82,005、66,005、54,521、54,521 B**，字典比 8B 节省 **33.52%**，比 4B 多省 **17.40%**。`N=4000, K=H=16,000` 时，字典变为 **360,009 B**，比 **328,005 B** 的 8B Baseline 大 **9.76%**，Adaptive 选择 **264,005 B** 的 4B 格式。 [逐块及合成 CSV](../results/processed/block_validator_compression/block_compression.csv)

当固定格式每个索引占 `a` B 时，字典严格更省的条件是 `8K+4 < (a−w)H`，等价于 `K/N < (a−w)(H/N)/8 − 1/(2N)`。相对 4B 的门槛随 `w` 变化。1 B 局部索引约需每个不同身份出现超过 **2.67 次**，2 B 局部索引约需超过 **4 次**。4 B 局部索引在可用全局 4B 格式的前提下总会增加字典开销。以 100 条四跳路径为例，1 B 局部编号时 `K ≤ 149` 才能胜过 4B，`K=256` 到 `257` 还会增加每次出现的宽度。固定 4B 回退时比较基线改为 `a=8`。

Fig. A 的历史点是按交易数分组的平均值，合成面板用四跳、`K=64`。Fig. B 的历史点是按 `K` 分组的平均值，绿色带覆盖组内字典最小至最大节省，合成面板展示四跳字典在三个负载下的变化。 [Fig. A · 实际编码字节数](../figures/trail_evidence/block_validator_compression/fig_a_path_evidence_storage.pdf) · [Fig. B · 随不同索引数量变化的节省](../figures/trail_evidence/block_validator_compression/fig_b_savings_vs_unique_validators.pdf)

## 计算成本

在 AMD Ryzen 9 9955HX、Rust 1.94.0 release 构建中，以单线程预热后 9 批 × 100 次测量中位数。每次计时包含编解码和对应的分配，解码包含索引范围检查。读档、承诺哈希、BLS 验证和共识处理在计时区间之外。 [计时 CSV](../results/processed/block_validator_compression/codec_timing.csv)

| 输入 | 8B 编码／解码 | 4B 编码／解码 | 字典编码／解码 | Adaptive 编码／解码 |
| --- | ---: | ---: | ---: | ---: |
| BA 100 历史夹具，14 笔 | 0.05／0.27 µs | 0.06／0.34 µs | 0.52／0.49 µs | 0.41／0.34 µs |
| 合成 1,000 笔，四跳，K=64 | 3.91／31.72 µs | 5.18／41.62 µs | 68.23／55.46 µs | 68.00／55.49 µs |
| 合成 4,000 笔，四跳，K=512 | 17.47／123.96 µs | 22.59／172.96 µs | 285.23／216.86 µs | 268.74／204.47 µs |

字典收集和局部编号查找使编码成本在高负载下可见。Adaptive 即便最终选择固定宽度，也先计算这一帧的字典成本。上述微基准用于比较编码器本身的成本和规模趋势。

## 正确性与复现

5 个新增 Rust 测试验证有序路径和逐路径签名、零跳占位、`u32` 界限和回退、四个局部宽度边界、确定性、非法局部编号、未知全局索引及损坏帧。签名夹具恢复后通过生产版无缓存 BLS 验证，Relay 奖励、Propagation Score、原始及归一化 Proposer 权重逐项相等。现有 Validator Index 经济回归也通过。两次独立进程生成的逐块结果一致，包含四种编码的长度前缀及完整字节的组合摘要 `wire_set_sha256`。 [新增测试](../tests/block_validator_compression.rs) · [现有回归](../tests/validator_index_economics.rs)

实验承诺使用独立域、区块哈希和完整编码字节。解码端从认证上下文取得预期摘要，覆盖模式、字典、记录顺序、签名和局部索引。测试将字典项替换为另一个已注册身份，原承诺拒绝变更，强制按变更内容解码也无法通过原路径 Root 和受影响路径的 BLS 验证。正式集成时应在经过认证的头部或路径承诺中绑定这一编码。 [实验承诺](../src/bin/block_validator_compression/codec.rs) · [篡改回归](../tests/block_validator_compression.rs)

```bash
out=$(mktemp -d /tmp/trail-block-compression.XXXXXX)
cargo test --offline --test block_validator_compression --test validator_index_economics
cargo run --offline --release --bin block_validator_compression -- --output-dir "$out/data"
MPLCONFIGDIR="$out/cache" python3 experiments/plot_block_validator_compression.py \
  --input "$out/data/block_compression.csv" --output-dir "$out/figures"
```
