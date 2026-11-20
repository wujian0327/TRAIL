# TRAIL 区块级 Validator Index 压缩实验 · 1B 优先

历史回放的 **48,240 个区块全部选择 1B 全局编码**。100 验证者组平均节省 **28.28%**，其中 42,647／48,000 块达到 25%。1,000 验证者的合成结果取决于块内实际索引值和重用程度。同样是 64 个不同身份，使用低编号时选 1B，高编号且高重用时选字典。 [历史汇总](../results/processed/block_validator_compression_v2/archive_savings_summary.csv) · [模式计数](../results/processed/block_validator_compression_v2/adaptive_mode_selection.csv)

## 编码规则与口径

- **Global 1/4/8B** 按区块检查实际出现的全局索引。最大值不超过 255 时使用 `u8`，256 至 `u32::MAX` 使用 `u32`，更大值使用 `u64`。所有索引都按原值序列化。
- **Block Dictionary** 按全局索引升序存储不同索引，每个字典项 8 B。`K ≤ 256` 时局部编号占 1 B，`257 ≤ K ≤ 65,536` 占 2 B，之后占 4 B。全局索引超过 255 的区块仍然可以使用 1 B 局部编号。
- **Adaptive** 对允许的 1B、4B、8B 全局格式和字典计算完整帧大小。等长时依次优先选 1B、4B、8B。块内索引都适合 `u8` 时，只用一个固定大小的表计数 `K` 以核算字典成本，编码无需构建字典。此时两种表示的索引宽度都是 1 B，字典还需额外支付 `8K+4` B。
- 四个实验模式标识分别为 `0=u64`、`1=u32`、`2=Dictionary`、`3=u8`。共同帧包括 1 B 模式字节、4 B 路径记录数量和每条路由 2 B 跳数。每份带签名的路径仍独立保存原 48 B BLS 签名。字典另含 4 B 项数。 [实验编码器](../src/bin/block_validator_compression/codec.rs)

四跳单条路径的核心开销从 80 B 降为 52 B，节省 **35%**。单路径实验帧分别为 87 B 和 59 B。`u8_bytes` 和 `u32_candidate_bytes` 只在相应候选可用时填值。`global_bytes` 和 `global_mode` 记录 1/4/8B 全局回退的实际选择。`adaptive_bytes` 是候选中最小的实际字节数。签名和索引的核心大小与完整帧单列。 [逐块 CSV](../results/processed/block_validator_compression_v2/block_compression.csv)

## 历史区块最优选择

两个批次均来自 BA 路径档案。使用完整名册恢复有序全局索引，并重放每个归档区块。历史归档覆盖 12 与 100 验证者。256、1,000 验证者结果来自合成。 [归档清单](../results/processed/block_validator_compression_v2/archive_provenance.csv) · [原始数据口径](block_validator_compression.md#数据与可比性)

| BA 验证者数 | 历史区块 | Adaptive 选择 | 平均节省 | 最小 | 最大 | 达到 25% |
| ---: | ---: | --- | ---: | ---: | ---: | ---: |
| 12 | 240 | 1B 全局，240 块 | 18.99% | 11.57% | 28.00% | 20 |
| 100 | 48,000 | 1B 全局，48,000 块 | 28.28% | 15.52% | 39.47% | 42,647 |
| 合计 | 48,240 | 1B 全局，48,240 块 | 28.23% | 11.57% | 39.47% | 42,667 |

## 100、256、1,000 验证者的合成对照

各组名册为连续的 `0..V−1`，测试 `N ∈ {100,1000,4000}`、`h ∈ {2,4,6}`。低编号和高编号两组使用相同的重用模式与 `K=64`，只改变全局编号范围。分散组使 `K=min(V−1,Nh)`，包含该名册的最高索引。下表选取 `N=1,000`、`h=4`，统一 8B 基线为 **82,005 B/块**。

| 验证者 | 最高索引 | K | Global 1/4/8B | 字典 | Adaptive | 相对 8B 节省 |
| ---: | ---: | ---: | ---: | ---: | --- | ---: |
| 100 | 99 | 64 | 54,005 B | 54,521 B | 1B 全局 | 34.14% |
| 256 | 255 | 64 | 54,005 B | 54,521 B | 1B 全局 | 34.14% |
| 1,000 | 63 | 64 | 54,005 B | 54,521 B | 1B 全局 | 34.14% |
| 1,000 | 999 | 64 | 66,005 B | 54,521 B | 1B 局部字典 | 33.52% |
| 1,000 | 999 | 999 | 66,005 B | 66,001 B | 2B 局部字典 | 19.52% |

在每种分布的九个参数点上，100 和 256 的三个分布均选 1B 全局，平均节省 **32.73%**。1,000 验证者低编号组全选 1B 全局，平均节省 **32.73%**。高编号 `K=64` 组全选字典，平均节省 **30.31%**。分散组有 4 个点选 4B 全局，5 个点选字典，平均节省 **21.44%**。例如分散组的 `N=100,h=4,K=400` 选 4B 全局，实际为 **6,605 B**，字典为 **9,009 B**。 [全部模式和统计](../results/processed/block_validator_compression_v2/adaptive_mode_selection.csv) · [逐块及合成数据](../results/processed/block_validator_compression_v2/block_compression.csv)

Fig. A 的合成面板展示 1,000 验证者、`h=4`、`K=64` 且索引为 `936..999` 的对照。Fig. B 保留初代字典规模网格，并在历史面板加入 Global 1/4/8B。 [Fig. A](../figures/trail_evidence/block_validator_compression_v2/fig_a_path_evidence_storage.pdf) · [Fig. B](../figures/trail_evidence/block_validator_compression_v2/fig_b_savings_vs_unique_validators.pdf)

## 验证与复现

独立 Rust 实验的 **6 个测试**和既有经济回归通过。覆盖全局索引 255／256、`u32::MAX` 回退、字典项数 256／257 与 65,536／65,537、全帧篡改、原生 BLS 验证，以及恢复前后奖励、传播评分和 Proposer 权重逐项一致。两次生成的逐块 CSV 与五种编码输出的组合摘要一致。计时采用离线 release 微基准。 [Rust 测试](../tests/block_validator_compression.rs) · [计时 CSV](../results/processed/block_validator_compression_v2/codec_timing.csv) · [绘图脚本](../experiments/plot_block_validator_compression.py)

```bash
out=$(mktemp -d /tmp/trail-compression-v2.XXXXXX)
cargo test --offline --test block_validator_compression --test validator_index_economics
cargo run --offline --release --bin block_validator_compression -- --output-dir "$out/data"
MPLCONFIGDIR="$out/cache" python3 experiments/plot_block_validator_compression.py \
  --input "$out/data/block_compression.csv" --output-dir "$out/figures"
```
