# TRAIL 传播证据原论文核对

2026-11-19 · 论文图修订

四跳 TRAIL 的原始 8 B 索引基线为 **80 B 核心、105 B 完整紧凑证书**。全文核对后，Plutus 的传播证据按 **384 B** 计，TxTail 图 8 从上一版的 177 B 估算改为 **128 B**。这里比较单件证据的字节数。

[Fig. A 跳数曲线](../figures/trail_overhead/fig_a_evidence_size_vs_hops.pdf) · [Fig. B 四跳分解](../figures/trail_evidence/fig_b_evidence_breakdown_4_hops.pdf) · [原始主 CSV](../results/processed/evidence_size_paper_verified/evidence_size_comparison.csv) · [原始字段 CSV](../results/processed/evidence_size_paper_verified/evidence_size_breakdown.csv)

## Fig. A 的 1 B 索引模型

Fig. A 中的 TRAIL 曲线采用固定宽度 `u8`，适用于全局 Validator Index 均在 `0–255` 的路径。每条路径包含独立的 48 B BLS 聚合签名和 \(h\) 个非 Proposer 索引。核心字段为 `48 + h` B。完整证书模型沿用原紧凑布局的 25 B 元数据与编码开销，因此绘制 `73 + h` B。在 1、2、4、6、8、16 跳的点分别为 74、75、77、79、81、89 B。该固定格式假设解码方已知索引宽度。Fig. B 与原始 CSV 继续使用实测的 8 B 证书数据。 [1 B 编码实验](block_validator_compression_v2.md) · [Fig. A PNG](../figures/trail_overhead/fig_a_evidence_size_vs_hops.png)

## 核对结果

- **TRAIL，区块路径证书。** 真实校验后的 Rust 输出包含 48 B BLS 签名、4 个 8 B 非 Proposer 索引、16 B Chain ID 和 Epoch、9 B 签名长度和索引计数。完整公式为 `73 + 8h`。四跳的 105 B 是仿真器单证书的紧凑表示，540 B 归一化为 **19.44%**。 [Rust 编码器](../src/blockchain/path.rs) · [测量模型](../src/bin/evidence_size_compare/model.rs)
- **Ersoy，交易传播记录。** arXiv v2 PDF 第 8–10 页 §VI、尤其第 9 页 Algorithm 2，确认先识别 Leader 并建立梯度路径，再传送客户端的交易和路径标识签名以及沿途公钥。四跳的 **196 B** 是本实验假设的 3 × 33 B 压缩 SEC1 公钥、64 B 紧凑 ECDSA、32 B 路径哈希和 1 B 计数。论文给出字段语义，曲线依赖这些编码选择。保留原生授权时增量为 **36.30%**。若等长替换原生授权，增量为 132 B（24.44%）。Leader 识别凭证及原生区块路径封装单列为未量化。 [Ersoy 等，§VI](https://arxiv.org/pdf/1712.07564#page=9) · [SEC 1 点编码](https://www.secg.org/sec1-v2.pdf)
- **Plutus，交易传播记录。** Hemo §3.2、论文第 22–25 页描述发送者签署带目的公钥的消息。第 38 页 §5.5 和 Fig. 5-8 明确采用每跳 32 B 公钥和 64 B 签名，以 200 B 原始消息为起点。四跳证据字段合计 **384 B（71.11%）**。同页正文另有约 300 B 的概数，本实验采用与图 5-8 一致的 96 B/跳公式。图 5-8 的迹线到第 9 跳，16 跳按同一公式外推。§3.3 第 25–28 页列出抽奖回看、候选键、胜者键和提议者 VRF 证明。第 29 页说明实现沿用 Algorand 的 SHA-256 和所引 VRF。抽奖结果的线格式和证明字节数未给出，因此另行留空。 [Hemo，MIT 论文](https://hdl.handle.net/1721.1/119766)
- **NKN，回溯组装的完成证明。** 白皮书 v1.0 §5.3 第 11–12 页给出 PoR 的目的，字节以 Go v2 提交 `6ff0fd160d2da341c5c329bea3e971f1c36c1ac5` 的官方 protobuf 为准。四条边包含源、三个中继及目的回执。ID 与公钥共 320 B，5 个 Ed25519 签名 320 B，3 份 32 B VRF 输出与 96 B 证明共 384 B，元数据 42 B，Protobuf 标签和长度 62 B，共 **1,128 B（208.89%）**。采用高度 ≥1,200,000 的 SIGNATURE 模式，三个中继均置 mining，末中继下一公钥从目的公钥恢复。固定 nonce 为 `0x12345678`，`data_size=540` 是独立于两个百分比分母的线夹具字段。历史 HASH 分支保留端点签名和中继 VRF，四跳模型为 1,038 B。普通中继包推进哈希及长度，完整证明回溯后才组装，公开的 SigChainTxn 另有交易封装。 [本地白皮书](../NKN_Whitepaper.pdf#page=11) · [固定版本 schema](https://github.com/nknorg/nkn/blob/6ff0fd160d2da341c5c329bea3e971f1c36c1ac5/pb/sigchain.proto) · [签名模式](https://github.com/nknorg/nkn/blob/6ff0fd160d2da341c5c329bea3e971f1c36c1ac5/config/config.go#L163) · [VRF](https://github.com/nknorg/nkn/blob/6ff0fd160d2da341c5c329bea3e971f1c36c1ac5/crypto/ed25519/vrf/vrf.go#L32) · [普通转发与回溯](https://github.com/nknorg/nkn/blob/6ff0fd160d2da341c5c329bea3e971f1c36c1ac5/por/porserver.go#L200) · [封装发布](https://github.com/nknorg/nkn/blob/6ff0fd160d2da341c5c329bea3e971f1c36c1ac5/lnode/relay.go#L263)
- **TxTail，图 8 的传播证据。** §V-A/C（印刷页 3202–3203）把发起者 BLS 公钥放入授权交易，初始签名随交易发送，随后每个中继用注册描述符和自报份额扩展证据。§VIII-A.1（3208 页）给出每跳 7 B 描述符和 1 B 份额。按图 8 矢量坐标用 0 B 和 250 B 刻度标定，初值为 96.00 B，四跳为 128.00 B。因此主模型是 **96 B 聚合签名 + 28 B 描述符 + 4 B 份额 = 128 B（23.70%）**。该比例以已携带发起者公钥的授权交易为基线。上一版把 96 B 公钥、48 B 签名和自设的 1 B 计数同时纳入传播记录，得到 177 B。Phore 的 G1 公钥分支与图 8 对应的 96 B 签名相容，48 B 发起者公钥仅作为单列的推断交易扩展。§VI-A 的形式证明采用另一组方向，不能据此改写图 8 的实验起点。8、16 跳是图 8 范围外外推。 [Dai 等，§V、§VI 和 §VIII-A](https://ieeexplore.ieee.org/document/11442688/) · [Phore 序列化](https://github.com/phoreproject/bls/blob/master/g1pubs/bls.go#L15)
- **TxTail，图 9 的链上公开。** §V-D 和 §VIII-A.2（3204、3208–3209 页）只公开每块一笔被抽样交易的证据，另计 Merkle 包含证明、relaybase 以及每笔交易的发起者 BLS 公钥。作者以 300 B 交易报告的 16.2% 属于图 9 的区块口径。本文两张图中的 540 B 和 300 B 百分比仅归一化单件证据或其增量。 [Dai 等，Fig. 9](https://ieeexplore.ieee.org/document/11442688/)

## 验证与复现

10 个定向 Rust 测试覆盖六个跳数、论文字段拆分、基线抵扣、外推标签、各阶段隔离、Protobuf 字段边界和结果保护。NKN 的 2、4、6、8、16 跳保持与固定版本生成的 Go protobuf 序列化逐字节一致。图 A 使用对数纵轴，各方案沿用同一线型与标记绘制。图 B 展示四跳字节成分，为 NKN 单设横轴。两图按 `trail_participationV2` 的 3.45 × 2.55 英寸单栏规格和 DejaVu Sans 字体导出矢量 PDF 及 300 dpi PNG。 [参与度参考脚本](../analysis/plot_trail_relay_participation_v2.py)

```bash
out=$(mktemp -d /tmp/trail-paper-figures.XXXXXX)
cargo test --offline --bin evidence_size_compare --test evidence_size_compare
cargo run --offline --bin evidence_size_compare -- --output-dir "$out/data"
MPLCONFIGDIR="$out/cache" python3 experiments/plot_evidence_size_paper_verified.py \
  --input-dir "$out/data" --output-dir "$out/figures"
```

只重绘 Fig. A 可传入 `--figure a`。用 `--trail-index-bytes 8` 可复现原始 TRAIL 曲线。 [绘图脚本](../experiments/plot_evidence_size_paper_verified.py) · [集成测试](../tests/evidence_size_compare.rs)

## English text for the Overhead section using the original 8 B TRAIL baseline

The compact TRAIL certificate contains one 48-byte BLS aggregate signature and one 8-byte validator index per relay edge. The block header supplies the proposer identity. The Rust encoder adds 25 bytes for the chain identifier, evidence epoch, signature length, and index count, yielding 105 bytes at four hops. This certificate occupies 19.44% of a 540-byte reference transaction. For relay-message evidence, Hemo's Plutus thesis specifies a 32-byte public key and a 64-byte signature per edge, yielding 384 bytes at four hops. Ersoy et al.'s Combined Propagation Algorithm yields 196 bytes under the stated compressed-key, signature, digest, and framing assumptions. These formats grow by 8, 96, and 33 bytes per additional hop, respectively. [Plutus](https://hdl.handle.net/1721.1/119766) · [Ersoy et al.](https://arxiv.org/pdf/1712.07564#page=9)

Reconstructing TxTail's Fig. 8 gives a 96-byte initial BLS signature and an 8-byte increment per appended relay contribution. Aggregation preserves the signature width. The four-hop relay-evidence record is 128 bytes, with the initiator's BLS public key carried by the authorized transaction. Points beyond six hops extrapolate the published increment. The NKN result of 1,128 bytes at four hops describes an Ed25519-mode completed SigChain, assembled during backtracking, with endpoint signatures and per-relay VRF material. TxTail's on-chain result in Fig. 9 counts one sampled proof and its Merkle witness per block, a relaybase transaction, and the per-transaction initiator keys. The plotted byte counts characterize their stated evidence objects and stages. Ratios to a reference transaction express relative object size. [TxTail](https://ieeexplore.ieee.org/document/11442688/) · [NKN backtracking implementation](https://github.com/nknorg/nkn/blob/6ff0fd160d2da341c5c329bea3e971f1c36c1ac5/por/porserver.go#L610)
