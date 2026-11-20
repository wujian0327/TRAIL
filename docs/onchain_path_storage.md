# On-chain Path Evidence Storage

本实验按 TxTail 图 9 的维度构造九组区块，交易数 `z ∈ {100, 1000, 4000}`，跳数 `h ∈ {2, 4, 6}`。TRAIL 和 Ersoy 都按逐交易披露计，Ersoy 的字节数采用显式编码假设。TxTail 每块公开一次抽中证据。NKN 官方出块器新增 0 或 1 份获胜 SigChain，主图显示新增一份的条件结果。Plutus 的完整链上披露格式与数量保留为 N/A。 [8B 前版 PDF](../figures/trail_evidence/onchain_path_storage_v2/onchain_path_evidence_storage.pdf) · [原生规则结果](../results/processed/onchain_path_storage_v2/protocol_native.csv) · [NKN 条件结果](../results/processed/onchain_path_storage_v2/nkn_disclosure_conditions.csv)

## 四跳三方案分组柱状图

三个方案均设定 100 个验证者、每笔交易保存一条有效四跳路径。每块 100、1,000 或 4,000 笔交易。TRAIL Original 和 TRAIL Compressed 均保留一份 48 B BLS 签名，另计 4 个 8 B 或 1 B 全局索引。核心大小分别为 **80 B 和 52 B/路径**，压缩节省 **35%**。纵轴 Block Size Expansion 表示每块核心路径证据的绝对增量，以 `bytes / 1000` 得到 KB。原始交易、Epoch、Chain ID 和通用编码字段在统计范围外。 [分组柱状图 PDF](../figures/trail_evidence/onchain_path_evidence_storage.pdf) · [PNG](../figures/trail_evidence/onchain_path_evidence_storage.png) · [字段与原始字节数 CSV](../results/processed/onchain_path_storage_grouped/core_path_evidence_storage.csv)

Ersoy §VI Algorithm 2 由客户端签署 `Signed(T, IN_cT)`，并由中间节点依次附加各自的公钥。Storage efficiency 段明确将存储成本归于传播节点公钥和一份客户端签名。四跳估算取三个 33 B 压缩 SEC1 secp256k1 公钥和一份 64 B 紧凑 ECDSA 路径签名，共 **163 B**。签名长度和公钥编码属于模型假设。普通交易授权若仅覆盖 `T`，就无法验证包含 `IN_cT` 的消息，所以计一份路径绑定签名，原有交易授权不计入。宿主接受单份路径签名兼作原授权时，才有抵扣原授权字节的条件。 [Ersoy §VI，Algorithm 2 和 Storage efficiency](https://arxiv.org/pdf/1712.07564#page=9)

`IN_cT` 由 Leader 凭证、Leader 公钥和有序中间节点公钥按识别路径重建。论文描述区块携带路径标识，具体落盘格式留给实现。重建布局下独立存储的标识为 **0 B**。显式保存一份 32 B 摘要的备选布局为 195 B/路径。分组图采用重建布局，并将 Ersoy 标记为 `assumption_based_estimate`。 [Ersoy §VI](https://arxiv.org/pdf/1712.07564#page=9) · [模型回归测试](../src/bin/onchain_path_storage.rs#L493)

```bash
out=$(mktemp -d /tmp/trail-grouped-storage.XXXXXX)
MPLCONFIGDIR="$out/cache" python3 experiments/plot_onchain_path_storage_grouped.py \
  --csv "$out/core_path_evidence_storage.csv" --pdf "$out/onchain_path_evidence_storage.pdf"
```

## 1B 索引主图修订

修订图沿用 2、4、6 跳的三个面板。TRAIL 使用 100 验证者合成区块已验证的 `u8` 实验编码，只计 `z(48+h)` B 核心字段。2、4、6 跳分别为 **50、52、54 B/路径**，1,000 笔时分别为 **50,000、52,000、54,000 B/块**。其余曲线仍从现有 CSV 读取。下文的 8B 数据对应前版主图。 [修订主图 PDF](../figures/trail_evidence/onchain_path_storage_v3/onchain_path_evidence_storage.pdf) · [1B 编码数据](../results/processed/block_validator_compression_v2/block_compression.csv)

TxTail §VIII-A.2（3208–3209 页）描述通过逐笔 VRF 抽奖选择披露的路径。图 9 的 NKN 柱给出区块级总量，但论文未公布抽样概率或阈值、命中到实际上链的换算规则，也未给出核心字段的分解。官方 PoR 文档给出数据包的阈值资格规则，仍无法确定图 9 使用的模型参数。§VIII-A.1 和图 8 的 **239 B/跳**属于链下传播增量。修订图保留可核实的 **NKN 新增一份获胜 SigChain** 条件，三条横线分别为 512、1,024、1,536 B。无新获胜 SigChain 时新增 0 B。 [TxTail §VIII-A 与图 8–9](https://doi.org/10.1109/TIFS.2026.3675466) · [NKN PoR 阈值](https://docs.nkn.org/docs/proof-of-relay#efficient-proof-of-relay) · [NKN 出块器](https://github.com/nknorg/nkn/blob/6ff0fd160d2da341c5c329bea3e971f1c36c1ac5/chain/mining.go#L72-L84)

```bash
out=$(mktemp -d /tmp/trail-onchain-u8.XXXXXX)
MPLCONFIGDIR="$out/cache" python3 experiments/plot_onchain_path_storage.py \
  --compression results/processed/block_validator_compression_v2/block_compression.csv \
  --output-dir "$out/figures"
```

## 等式与计量范围

`S_onchain(z, h) = K(z, h) × S_path(h)`。`K` 是每块新增记录数，`S_path` 包含每份记录的身份、公钥或索引、路径签名、必需密码学证明和逐跳奖励份额。TRAIL、Ersoy、Plutus 和 NKN 的边从发送方计到接收方。TxTail 沿用图 8 与图 9 的 `h`，即从初始证据开始追加贡献的 relayer 数量。TRAIL 的最后一条边终止于区块 Proposer，TxTail 的 `h` 对应实际追加的描述符和份额。NKN 的 `h` 条边连接两个端点和 `h−1` 个中间 Relay。

主指标排除原始交易、Chain ID、Evidence Epoch、通用序列化字段、区块头、Merkle Root 和包含证明、relaybase、注册交易，以及其他非路径元数据。字段列按**单份证据**计，`total_path_storage_bytes` 按**合成区块**计。`path_identifier_bytes` 是 `path_proof_bytes` 的子项。`storage_kib = S_onchain / 1024`，`storage_bytes_per_transaction = S_onchain / z`，`overhead_pct_TB = 100 S_onchain / (T z)`。540 B 和 300 B 是参考交易载荷。`z` 是合成区块参数，以上比例描述相对交易载荷的证据开销。

## 原生上链规则核查

| 方案 | 区块中公开的对象与数量 | 本实验计入的字段与依据 |
| --- | --- | --- |
| TRAIL | 每笔有认证路径的交易独立保存一份证书。Proposer 即交易发起者时是零跳记录。本实验的交易均有非零且有效的路径。 | 每份 48 B BLS 聚合签名和 `h` 个 8 B Validator Index。Proposer 从区块头恢复。Rust 用已双向签名并通过验证的夹具序列化，读取原始签名和索引负载。2、4、6 跳分别为 64、80、96 B。 [TRAIL §III-B.2，pp. 4–5，图 3](<../TRAIL__A_Persistent_and_Robust_Propagation_Incentive_Mechanism_for_Proof_of_Stake_Blockchains%20(7).pdf#page=4>) · [Rust 证书编码](../src/blockchain/path.rs#L518) · [区块验证](../src/blockchain/block.rs#L141) |
| Ersoy | Combined Propagation 的 Leader 随每笔有效交易保存路径。路由依赖先确定的 Leader，Algorithm 2 在识别阶段创建梯度标识符，在交易阶段收集沿途公钥。 | 论文描述区块保存路径标识，并将额外存储归为传播者公钥和一份客户端签名。按 `K = z` 计算。主图用 `33(h−1) + 64` B/路径，采用可重建标识的布局，标为 `assumption_based_estimate`。单独保存路径标识的布局另加 32 B。 [Ersoy §III，p. 3，§VI，pp. 8–10，Algorithm 2 和 Storage efficiency](https://arxiv.org/pdf/1712.07564#page=9) |
| Plutus | 提议者逐条验证收到的传播路径。抽奖从回看区块提取传播公钥，新提议附获胜公钥、验证使用提议者 VRF 证明。抽中对象是公钥。 | 原文为传播消息给出每跳 32 B 收件公钥和 64 B 发送者签名。区块如何保留路径公钥、是否逐笔保留完整签名链，以及抽奖结果与 VRF 的完整线格式缺乏足够规范，完整路径披露数量和字节均为 N/A。获胜键和提议者 VRF 服务于奖励抽奖，本指标仅纳入认证传播路径的证明。 [Plutus §3.2.2，pp. 24–25](../Plutus.pdf#page=24) · [§3.3，pp. 25–28](../Plutus.pdf#page=25) · [§5.5，p. 38，图 5-8](../Plutus.pdf#page=38) |
| NKN | 回溯完成的 SigChain 进入 `SigChainTxn`。官方出块器仅在选择了 `TXN_SIGNER` 且该获胜交易尚未入账时加入一份。正常交易池收集流程排除 `SigChainTxn`。无候选或可从账本获取既有获胜记录时新增 0 份。 | 固定版本和条件下，每份保存 `2(h+1)` 个 32 B 身份或公钥字段、`h+1` 个 64 B Ed25519 签名，以及每个中间 Relay 的 32 B VRF 输出和 96 B 证明。两个状态分别为 `0` 与 `256h` B/块。主图展示 `K = 1`，实际状态频率留空。 [白皮书 §5.3，pp. 11–12](../NKN_Whitepaper.pdf#page=11) · [回溯序列化与 PoR 提交](https://github.com/nknorg/nkn/blob/6ff0fd160d2da341c5c329bea3e971f1c36c1ac5/lnode/relay.go#L263-L294) · [获胜候选](https://github.com/nknorg/nkn/blob/6ff0fd160d2da341c5c329bea3e971f1c36c1ac5/por/porserver.go#L263-L321) · [出块器](https://github.com/nknorg/nkn/blob/6ff0fd160d2da341c5c329bea3e971f1c36c1ac5/chain/mining.go#L72-L84) · [交易池](https://github.com/nknorg/nkn/blob/6ff0fd160d2da341c5c329bea3e971f1c36c1ac5/chain/pool/txpool.go#L312-L317) |
| TxTail | 提议者承诺该区块的传播证据集合，以区块哈希对交易数取模，向区块体写入抽中的一份传播记录及对应的 Merkle 包含证明。 | 一份 96 B 聚合签名，以及每位 relayer 的 7 B 注册公钥描述符和 1 B 奖励份额。96 B 采用论文图 8 在 `h = 0` 的矢量坐标重建，签名宽度随聚合保持固定。发起者公钥由已授权交易提供。2、4、6 跳分别为 112、128、144 B。 [TxTail §V-A/C，pp. 3202–3203，§V-D，p. 3204，§VIII-A，pp. 3208–3209，图 8–9](https://doi.org/10.1109/TIFS.2026.3675466) |

### 字段边界

- **TRAIL。** 真实紧凑编码额外有 16 B Chain ID 与 Evidence Epoch，以及 9 B 的签名长度和索引计数。四跳编码总长 105 B，抽取出的路径负载为 80 B。实验每个跳数验证并编码一个确定性夹具，再按记录数计量。 [编码器](../src/blockchain/path.rs#L526) · [夹具](../src/bin/onchain_path_storage.rs#L58)
- **Ersoy 的签名。** Algorithm 2 的客户端签名覆盖 `Signed(T, IN_cT)`。普通交易授权已签署 `T` 时，该签名无法验证新增的路径标识。主模型保留基线中原有的交易授权，并只计一份额外的 64 B 路径签名。只有宿主账本接受路径绑定签名兼作原有交易授权，且该签名实际取代等宽授权字段，才可以抵扣原始签名。在这一替换假设下，两种四跳布局的增量分别为 99 B 和 131 B。论文没有规定宿主账本的授权替换规则。 [Ersoy §VI，Algorithm 2](https://arxiv.org/pdf/1712.07564#page=9)
- **Ersoy 的路径标识。** Algorithm 2 给出 Leader 的 `OUT = H(Lr, PK_leader)`，各中继向外更新为 `H(IN, PK_relay)`。当 Leader 凭证和公钥可从区块上下文获得，并保留中介公钥顺序时，可按 Leader 到客户端的反向传播顺序重建标识。原文也描述区块携带路径标识，但未给出存储布局。主图采用重建布局，四跳为 `99 + 64 = 163 B`。显式保存一个 32 B 标识的布局为 `99 + 64 + 32 = 195 B`。两者都排除通用计数编码。 [Ersoy §VI，Algorithm 2 和 Storage efficiency](https://arxiv.org/pdf/1712.07564#page=9) · [Ersoy 编码敏感性 CSV](../results/processed/onchain_path_storage_v2/ersoy_encoding_sensitivity.csv)
- **NKN。** 固定 Go 提交 `6ff0fd160d2da341c5c329bea3e971f1c36c1ac5`，以参考高度 ≥1,200,000 的 `SIGNATURE` 模式计。端点 ID 与公钥计一次，末中继的下一公钥采用实现允许的省略形式，由 `dest_pubkey` 恢复。四跳为 320 B 身份和公钥、320 B 签名、384 B VRF 材料。中继签名模式仍要求 VRF 输出及证明，验证器检查是否存在，并用参考区块哈希验证二者，因此它们属于路径证明。第一阶段的 1,128 B Protobuf 夹具还含 42 B 元数据和 62 B 通用编码。本阶段只保留 1,024 B 核心。外层交易授权、提交者地址和交易封装也在指标范围外。 [第一阶段字段核查](evidence_size_paper_verification.md) · [Protobuf](https://github.com/nknorg/nkn/blob/6ff0fd160d2da341c5c329bea3e971f1c36c1ac5/pb/sigchain.proto) · [签名模式](https://github.com/nknorg/nkn/blob/6ff0fd160d2da341c5c329bea3e971f1c36c1ac5/config/config.go#L163-L166) · [路径和 VRF 验证](https://github.com/nknorg/nkn/blob/6ff0fd160d2da341c5c329bea3e971f1c36c1ac5/por/sigchain.go#L32-L145) · [VRF 宽度](https://github.com/nknorg/nkn/blob/6ff0fd160d2da341c5c329bea3e971f1c36c1ac5/crypto/ed25519/vrf/vrf.go#L32-L37)
- **TxTail。** 主指标保留抽中证据的聚合签名及描述符、份额。签名取图 8 的实验编码宽度，§VI-A 的形式证明使用的群方向单独处理。Merkle 包含证明验证所披露证据与区块承诺一致，按本实验口径排除。论文图 9 同时统计该证明、relaybase 与逐交易发起者公钥，这些成本都在上表路径负载之外。TxTail 的描述符、份额可配对重排，验证的是参与者与份额。TRAIL 的签名绑定有序路径。 [TxTail §V-B/D，pp. 3203–3204，§VI-A，p. 3204，§VIII-A.2，pp. 3208–3209](https://doi.org/10.1109/TIFS.2026.3675466) · [TRAIL 路径验证](../src/blockchain/path.rs#L411)

## 主结果

每个单元格为 **B/合成区块**。Ersoy 列采用重建路径标识的估算。NKN 列依次列出新增零份和新增一份的条件总量。

| h | z | TRAIL | Ersoy 估算 | Plutus | NKN 0 / 1 份 | TxTail |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 100 | 6,400 | 9,700 | N/A | 0 / 512 | 112 |
| 2 | 1,000 | 64,000 | 97,000 | N/A | 0 / 512 | 112 |
| 2 | 4,000 | 256,000 | 388,000 | N/A | 0 / 512 | 112 |
| 4 | 100 | 8,000 | 16,300 | N/A | 0 / 1,024 | 128 |
| 4 | 1,000 | 80,000 | 163,000 | N/A | 0 / 1,024 | 128 |
| 4 | 4,000 | 320,000 | 652,000 | N/A | 0 / 1,024 | 128 |
| 6 | 100 | 9,600 | 22,900 | N/A | 0 / 1,536 | 144 |
| 6 | 1,000 | 96,000 | 229,000 | N/A | 0 / 1,536 | 144 |
| 6 | 4,000 | 384,000 | 916,000 | N/A | 0 / 1,536 | 144 |

在 `z = 1,000, h = 4`，以 540 B 与 300 B 为分母，TRAIL 分别为 **14.8148%** 与 **26.6667%**，Ersoy 为 **30.1852%** 与 **54.3333%**，TxTail 为 **0.0237%** 与 **0.0427%**。NKN 新增一份时分别为 **0.1896%** 与 **0.3413%**，新增零份时两者均为 0%。主图三个面板使用相同的对数坐标，图例标明 Ersoy 是编码估算，NKN 曲线只对应新增一份的条件。NKN 零值见图下注记和条件 CSV。 [原生规则数据](../results/processed/onchain_path_storage_v2/protocol_native.csv) · [NKN 条件数据](../results/processed/onchain_path_storage_v2/nkn_disclosure_conditions.csv) · [矢量主图](../figures/trail_evidence/onchain_path_storage_v2/onchain_path_evidence_storage.pdf)

## 补充情景

- **完整留存。** 假设每笔交易保留一份完整证据。按主图的 Ersoy 布局，四跳每份分别为 TRAIL 80 B、Ersoy 163 B、Plutus 384 B、NKN 1,024 B、TxTail 128 B。其中 `384z` B 来自为 Plutus 假设留存每跳公钥和签名。`measurement_type` 标记全保留假设，`path_measurement_type` 记录单份字节数的依据。 [完整留存 CSV](../results/processed/onchain_path_storage_v2/full_evidence_retention.csv)
- **NKN 状态敏感性。** `p` 为区块新增获胜 SigChain 的假设概率。在 `p = {0, 0.25, 0.5, 0.75, 1}` 下，期望值为 `p × 256h`，写入 `expected_path_storage_bytes`。例如四跳且 `p = 0.5` 时为 512 B/区块。实际状态频率留空。 [NKN 敏感性 CSV](../results/processed/onchain_path_storage_v2/nkn_state_sensitivity.csv)

## 验证与复现

5 个定向 Rust 测试覆盖 TRAIL 实际编码边界、Ersoy 路径标识重建与两种签名消息的差异、TxTail 单次选择、NKN 新增 0／1 份的条件，以及五份 CSV 在九组参数上的字段和、缺失值及两个分母。修订结果写入 `onchain_path_storage_v2`。 [Rust 实验](../src/bin/onchain_path_storage.rs) · [集成测试](../tests/onchain_path_storage.rs) · [绘图脚本](../experiments/plot_onchain_path_storage.py)

```bash
out=$(mktemp -d /tmp/trail-onchain-storage.XXXXXX)
cargo test --offline --bin onchain_path_storage --test onchain_path_storage
cargo run --offline --bin onchain_path_storage -- --output-dir "$out/data"
MPLCONFIGDIR="$out/cache" python3 experiments/plot_onchain_path_storage.py \
  --input "$out/data/protocol_native.csv" --output-dir "$out/figures"
```

## English text for the Overhead section

Figure X compares the core path evidence stored per synthetic block at 100, 1,000, and 4,000 transactions and at two, four, and six relay hops. At four hops and 1,000 transactions, TRAIL retains 80,000 bytes, or 14.81% of a 540-byte-per-transaction payload. Ersoy et al. also disclose paths per transaction. Assuming three 33-byte intermediary keys, a separate 64-byte client signature, and reconstruction of the path identifier, the estimate is 163,000 bytes. Explicitly storing a 32-byte identifier increases this estimate to 195,000 bytes. Both layouts exclude generic framing. [TRAIL Fig. 3](<../TRAIL__A_Persistent_and_Robust_Propagation_Incentive_Mechanism_for_Proof_of_Stake_Blockchains%20(7).pdf#page=4>) · [Ersoy §VI](https://arxiv.org/pdf/1712.07564#page=9)

TxTail discloses the relayer set and reward shares of one sampled transaction. Its four-hop record occupies 128 bytes across the tested block sizes, excluding the Merkle inclusion witness and other block overhead. NKN adds 1,024 bytes of four-hop core evidence when the block publishes a new winning SigChain and zero bytes when it publishes none. The plotted NKN curve is conditional on the first state. Plutus remains unquantified for native storage because the thesis establishes the propagation message sizes and the relay-key lottery, while the complete on-chain disclosure rule remains unspecified. [TxTail Figs. 8–9](https://doi.org/10.1109/TIFS.2026.3675466) · [NKN block builder](https://github.com/nknorg/nkn/blob/6ff0fd160d2da341c5c329bea3e971f1c36c1ac5/chain/mining.go#L72-L84) · [Plutus §3.3](../Plutus.pdf#page=25)
