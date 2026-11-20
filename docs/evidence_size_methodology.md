# TRAIL 传播证据大小对比实验

2026-11-19 · 本地 Rust 证书序列化与字段级编码对照

TRAIL 四跳证书实测为 **80 B 核心、105 B 完整编码**，相对 540 B 原生交易的增量为 **19.44%**。NKN 的 1,128 B 是完整回溯证明的 Protobuf 模型。Ersoy 的 196 B 和 TxTail 的 177 B 分别来自两种传播消息编码假设。Plutus 的原生总量保留为空。阶段、基线及测量类型是这些结果的一部分。

[主 CSV](../results/processed/evidence_size_comparison.csv) · [字段分解 CSV](../results/processed/evidence_size_breakdown.csv) · [Fig. A 跳数曲线](../figures/evidence_size_comparison/fig_a_evidence_size_vs_hops.pdf) · [Fig. B 四跳分解](../figures/evidence_size_comparison/fig_b_evidence_breakdown_4_hops.pdf)

## 比较口径

一跳是一条有向边。包含 `h` 条边的简单路径有 `h + 1` 个参与节点。各方案记录的身份数取决于其端点、已知上下文及线格式，详见下表。样本取 `h ∈ {1, 2, 4, 6, 8, 16}`。540 B 和 300 B 是统一的原生交易或应用载荷大小基线。两种百分比共用同一份证据和同一个分子。NKN 的 Protobuf 测量固定使用 `data_size=540`。

- **核心字节**是证据中的签名、身份、全公钥和证明数据。TxTail 假设模型将签名所覆盖的逐项奖励份额计入证明数据。
- **编码字节**包括核心字段、元数据、类型标签、计数与长度前缀。其中 `total_evidence_bytes` 仅针对完整记录。`encoded_evidence_bytes` 可记录标有 `partial_subtotal` 的子集大小。
- **增量字节**是完整编码大小扣除在该行基线条件下可以复用的部分。`overhead_pct_540B` 和 `overhead_pct_300B` 分别计算 `100 × 增量字节 / 540` 和 `100 × 增量字节 / 300`。原生交易的签名、公钥和外层消息字节留在原生基线中。只有明确假设能复用同一字段或等长替换授权时，才给予基线抵扣。

`actual_serialized` 表示当前 Rust 证书编码器的输出。 `protocol_encoding_model` 表示按已核实的官方结构编码合成字段。 `assumption_based_estimate` 表示密码学宽度、封装或复用规则含建模假设。 `not_specified` 表示可访问来源不足以确定原生总量，或该阶段对所选跳数无适用的完整记录。CSV 用空字段保留这些缺失值及无意义的百分比。

## 字段结构与来源

| 方案与证据阶段 | 身份记录和密码学字段 | 元数据、编码及基线 |
| --- | --- | --- |
| TRAIL · 区块路径证书 | `h` 个非 Proposer 索引，每个 8 B。一个 48 B 路径级 BLS 聚合签名。Proposer 从区块头恢复。 | Chain ID 与 Evidence Epoch 各 8 B。签名长度 1 B，索引计数 8 B。整数小端。原生交易及区块封装在计量范围外。 |
| Ersoy · 交易到达 Leader 前 | `h − 1` 个中继公钥。Combined Algorithm 从空公钥列表开始，附加途经中继的键。客户端对交易和收到的路径标识签署一份签名。 | 模型采用压缩 SEC1 secp256k1 公钥 33 B、紧凑 ECDSA 64 B、SHA-256 路径标识 32 B 和 u8 公钥数。客户端原有身份信息从基线或已知上下文取得，Leader 已由 recognition 确定。 |
| Plutus · 传播消息与抽奖 | MIT 摘要确认记录消息传播路径并以抽奖奖励传播节点。论文全文下载未成功，原生身份字段数量、类型、签名和编码均留空。 | 传播与抽奖分别列行。另提供独立的纯身份敏感性方案，假设源身份已知，保存 `h` 个 32 B 接收者标识和 1 B 计数。该子集的大小无法约束原生总量。 |
| NKN · 回溯组装的完整 SigChain | 两端各有 32 B ID 和 32 B Ed25519 公钥。源、`h − 1` 个中继和目的共 `h + 1` 个元素与身份，各含 64 B 签名。中继额外贡献 32 B ID、32 B VRF 输出及 96 B VRF 证明。下一跳公钥 32 B 出现在源元素和除最后中继外的各中继元素。 | 两个 varint 为 nonce 和 data_size，另有 32 B 区块哈希。中继的 mining 字段设为 true。使用官方 proto3 标签与长度前缀，零值及空字段按 schema 省略。最后一个中继的下一公钥从目的公钥恢复。 |
| TxTail · 单笔交易传播证据 | SMU 摘要确认聚合签名、公钥映射和与顺序无关的证据。按任务给定的 7 B 描述符加 1 B 份额建模。有 `h` 个接收边条目和一个发起者身份。 | 基本编码假设为 48 B BLS 聚合签名、96 B 独立发起者 BLS 公钥、u8 计数。完整图 8 固定项及线格式未获原文确认，原生总量留空。Bitcoin 交易已有授权维持在基线，独立 BLS 发起者公钥的复用情景单列。 |

**TRAIL。** 使用当前工作树和 `h + 1` 个确定性钱包构造真实签名路径，钱包种子为 `0x54505300 + h`，证据 Epoch 为 7。实验通过现有验证接口核对聚合签名，再测量 `to_compact_binary()` 输出。核心恒等式为 `48 + 8h`，完整编码为 `73 + 8h`。证书独立按路径聚合，地址顺序及交易绑定仍由双向 Hop Statement 验证。公钥从仿真器可信注册表获取。 [现有 Rust 编码器](../src/blockchain/path.rs) · [实验模型](../src/bin/evidence_size_compare/model.rs)

**Ersoy。** §IV-D / Algorithm 2 先由时隙 Leader 广播凭证，各节点在 recognition 阶段通过父梯度节点的标识和自己的公钥计算向外的路径标识。交易随后沿预先建立的梯度路径向 Leader 传播，验证方用客户端签名、更新过的公钥列表和自己的路径标识核对该路径。论文未为这些字段固定曲线、签名算法、哈希或线编码。上表选择固定宽度的密码学假设以构造可重现的示例。论文另述区块存储以传播公钥和客户端签名为主，并提出可用承诺隐藏中继公钥的变体。本实验测量交易阶段的明文公钥模型。完整 recognition 消息含 Leader 凭证和向外路径标识。该凭证的编码字节数及该阶段分摊到多少笔交易没有确定取值。CSV 将这部分单列，并为同一交易编码给出两种授权基线。 [Ersoy 等，§IV-D / Algorithm 2](https://arxiv.org/pdf/1712.07564#page=9) · [SEC 1 压缩点表示](https://www.secg.org/sec1-v2.pdf)

**Plutus。** 可访问的 MIT 记录支持路径追踪和抽奖激励这两个机制层结论。官方 PDF 下载返回访问错误，摘要不足以核实路径证据字段或抽奖证明的类型和宽度。因此，证据审计保留 `not_specified`。假设子集只展示存身份列表的数量级，Fig. A 把它放在独立面板，Fig. B 保留原生字段未核实的空位。Algorand 原生共识证书的大小没有计入。 [Hemo，MIT 硕士论文记录](https://dspace.mit.edu/entities/publication/17dde16d-5355-4413-a1e1-e84ae0039f1f)

**NKN。** 完整线对象用官方 Protobuf 字段号构造 `SigChain`。源和目的元素的 ID 来自外层元数据，默认省略。两端无 VRF，也不设置 mining。目的回执的下一公钥为空。重放当前 Ed25519 分支时，SIGNATURE 枚举值 0 在 proto3 中省略。中继启用 mining 时，其一字节布尔值及标签均需计入。本例 nonce 为 `0x12345678`，两个 varint 值分别占 5 B 和 2 B，二者另有各 1 B 标签。完整链至少需要三个元素，直连一跳的完整证据为不适用。普通中继消息推进上一哈希和长度，完整链按需回溯组装，再由实现提交或传播 PoR 证据。签名链、按区块绑定的 VRF 和代码中的 ID／路径检查是完整见证的不同部分。 [官方 schema](https://github.com/nknorg/nkn/blob/master/pb/sigchain.proto) · [NKN 结构说明](https://docs.nkn.org/docs/signature-chain) · [验证和完成条件](https://github.com/nknorg/nkn/blob/master/por/sigchain.go#L32) · [回溯实现](https://github.com/nknorg/nkn/blob/master/por/porserver.go#L610) · [目的回执](https://github.com/nknorg/nkn-sdk-go/blob/master/client.go) · [VRF 常量](https://github.com/nknorg/nkn/blob/master/crypto/ed25519/vrf/vrf.go#L32)

本例完整 SigChain 是独立序列化对象。另一行假设外层共享上下文已经提供相同的来源公钥和目标 ID，可抵扣两项各 34 B，对完整对象的大小没有影响。这一行是条件模型。历史 HASH 分支与当前选用的 Ed25519 分支有不同的算法标签与字段大小，边界测试验证两种编码的差值。 [高度分支](https://github.com/nknorg/nkn/blob/master/config/config.go#L163) · [Relay 消息实现](https://github.com/nknorg/nkn/blob/master/lnode/relay.go#L71)

**TxTail。** 可访问的作者机构摘要支持与路径顺序无关的聚合证据、公钥映射，以及基于 Merkle 承诺的可验证抽样。7+1 B 采用任务给定口径。IEEE 正文及图 8／图 9 未取得可读版本，发起者 BLS 公钥的组与宽度、初始记录边界、字段编码及注册消息无法独立核实。当前模型计一个独立发起者公钥，并给出复用同一公钥的条件变体。公钥登记是一项单独的前置成本，完整传播记录和图 9 的区块抽样成本分别对应不同阶段。此实验仅量化图 8 所针对的单笔证据量级。 [IEEE 论文记录](https://ieeexplore.ieee.org/document/11442688/) · [SMU 作者机构摘要](https://ink.library.smu.edu.sg/sis_research/11083/)

## 各跳数完整编码大小

数值单位为 B。Plutus 所列为单独的身份子集设定，完整记录和增量百分比均为空。Ersoy 采用保留原生签名的附加证明情景，TxTail 采用独立携带发起者 BLS 公钥的条件编码。

| `h` | TRAIL 实测 | Ersoy 假设 | Plutus 原生 | Plutus 假设子集 | NKN Protobuf 模型 | TxTail 条件编码 |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 81 | 97 | — | 33 | 不适用 | 153 |
| 2 | 89 | 130 | — | 65 | 586 | 161 |
| 4 | 105 | 196 | — | 129 | 1,128 | 177 |
| 6 | 121 | 262 | — | 193 | 1,670 | 193 |
| 8 | 137 | 328 | — | 257 | 2,212 | 209 |
| 16 | 201 | 592 | — | 513 | 4,380 | 273 |

Fig. A 按阶段展示已完成的路径记录、传播消息和部分字段。各面板使用独立纵轴。Fig. B 在四跳时仅在同阶段内画分解柱。身份标识和公钥合并为一个图例项，CSV 分字段保留。Plutus 和 TxTail 的原生总量缺少足够证据，图 B 留空位。 [Fig. A](../figures/evidence_size_comparison/fig_a_evidence_size_vs_hops.pdf) · [Fig. B](../figures/evidence_size_comparison/fig_b_evidence_breakdown_4_hops.pdf)

## 四跳字段和相对增量

| 方案与情景 | 签名 | 身份 | 公钥 | 证明 | 元数据 | 编码 | 核心 | 完整编码 | 增量 | 540 B | 300 B |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRAIL · Rust 实测 | 48 | 32 | 0 | 0 | 16 | 9 | 80 | 105 | 105 | 19.44% | 35.00% |
| Ersoy · 附加授权假设 | 64 | 0 | 99 | 32 | 0 | 1 | 195 | 196 | 196 | 36.30% | 65.33% |
| Ersoy · 等长替换原授权 | 64 | 0 | 99 | 32 | 0 | 1 | 195 | 196 | 132 | 24.44% | 44.00% |
| Plutus · 原生记录 | — | — | — | — | — | — | — | — | — | — | — |
| Plutus · 仅身份子集假设 | — | 128 | — | — | — | 1 | — | — | — | — | — |
| NKN · 独立 SigChain | 320 | 160 | 160 | 384 | 42 | 62 | 1,024 | 1,128 | 1,128 | 208.89% | 376.00% |
| NKN · 条件复用路由上下文 | 320 | 160 | 160 | 384 | 42 | 62 | 1,024 | 1,128 | 1,060 | 196.30% | 353.33% |
| TxTail · 携带发起者公钥的假设 | 48 | 28 | 96 | 4 | 0 | 1 | 176 | 177 | 177 | 32.78% | 59.00% |
| TxTail · 条件复用发起者公钥 | 48 | 28 | 96 | 4 | 0 | 1 | 176 | 177 | 81 | 15.00% | 27.00% |

字节数为单条完整对象或指定字段子集的大小。阶段、路径顺序绑定、端点证明、注册表和抽样前提不同，结果无法直接推导累计流量、链上成本或总体安全性排名。 [主 CSV 与情景基线](../results/processed/evidence_size_comparison.csv) · [字段计数](../results/processed/evidence_size_breakdown.csv)

## 验证和复现

4 个新增 Rust 单元测试、4 个新增集成测试及 20 个既有路径证书测试通过。检查包括真实 TRAIL 编码和确定性、五方案六个跳数、四跳 80 B、字段合计、Protobuf 零值及 varint 边界、签名和公钥复用、百分比分母，以及已有输出保护。NKN 的五个可构造跳数逐字节匹配官方生成的 Go 类型（blob `1adf236`）的确定性 `proto.Marshal` 输出，Rust 测试固定这些样本的 SHA-256。Go 字段值是合成的，仅用于核对编码。 [官方生成 Go 类型](https://github.com/nknorg/nkn/blob/master/pb/sigchain.pb.go) · [集成测试](../tests/evidence_size_compare.rs)

实验执行范围包括 Rust 字节计数器、定向测试和离线绘图。Rust 二进制和绘图脚本均要求新输出文件。以下命令使用临时目录复现。

```bash
out=$(mktemp -d /tmp/trail-evidence-compare.XXXXXX)
cargo test --offline --bin evidence_size_compare --test evidence_size_compare
cargo run --offline --bin evidence_size_compare -- --output-dir "$out/data"
MPLCONFIGDIR="$out/mpl-cache" python3 experiments/plot_evidence_size_comparison.py \
  --input-dir "$out/data" --output-dir "$out/figures"
```

[二进制入口](../src/bin/evidence_size_compare.rs) · [绘图脚本](../experiments/plot_evidence_size_comparison.py)
