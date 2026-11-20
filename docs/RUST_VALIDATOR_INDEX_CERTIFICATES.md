# TRAIL Rust 路径证书 Validator Index 编码报告

区块中每条路径的证书以 `Vec<u64>` 保存非 Proposer 身份序列。相对旧版地址证书，16 跳路径的核心开销从 720 B 降到 176 B，完整二进制从 873 B 降到 201 B，实际 JSON 从 873 B 降到 191 B。四种路径长度的聚合签名、Relay 奖励、Propagation Score 和生效后的 Proposer 权重均与旧版逐字节一致。

## 注册表与代码改动

- 启动时收集完整钱包名单，包含普通节点、不稳定节点、Sybil 主节点及其每个子身份。按地址字典序分配从零开始的 `u64` 索引，保存双向映射及可信 BLS 公钥。节点编号和异步 stake 注册顺序独立于这套索引。各节点、WorldState 和 TRAIL 共识共享同一个不可变的 `Arc<ValidatorRegistry>`。每次运行导出 `validator_indices.json`，随机钱包模式也可用该名单重建映射。重复地址、重复 stake 注册和未知身份会被拒绝。 [注册表](../src/consensus/validator_registry.rs) · [网络初始化](../src/network/mod.rs) · [WorldState](../src/network/world_state.rs)
- 路径打包先校验双向 Hop 证明，再按原有地址消息聚合该路径的 BLS 签名，最后编码非 Proposer 节点。验证时从区块头取 Proposer，解码索引并重建原始 Hop Statement。缓存键绑定索引序列和包含地址、公钥的注册表指纹。共识继续用配置的最大跳数筛选可计奖、可计分的证据。 [路径证书](../src/blockchain/path.rs) · [出块](../src/network/node.rs) · [奖励与评分](../src/consensus/trail.rs)
- 区块、链和审计读取逻辑在需要地址时解码。独立 withholding runner 接入同一注册表，两个 Python 审计读取器均支持读取历史地址证书和新索引证书。 [区块](../src/blockchain/block.rs) · [区块链](../src/blockchain/mod.rs) · [独立 runner](../src/network/withholding.rs) · [审计解码](../experiments/validator_indices.py)

## 证书大小

四组样本使用钱包种子 `0x54505300 + 跳数`、固定交易时间戳、Epoch 7 和相同的双向传播记录。每个有签名的样本保存的索引数 `L` 等于跳数。下表给出旧版 → 新版大小和节省量，百分比以该列旧版大小为分母。 [测量程序](../src/bin/frozen_evidence_bench.rs)

| 跳数 | 核心 B | 核心节省 | 实际 JSON B | JSON 节省 | 完整二进制 B | 二进制节省 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 132 → 64 | 68 B，51.52% | 243 → 157 | 86 B，35.39% | 173 → 89 | 84 B，48.55% |
| 4 | 216 → 80 | 136 B，62.96% | 333 → 161 | 172 B，51.65% | 273 → 105 | 168 B，61.54% |
| 8 | 384 → 112 | 272 B，70.83% | 513 → 169 | 344 B，67.06% | 473 → 137 | 336 B，71.04% |
| 16 | 720 → 176 | 544 B，75.56% | 873 → 191 | 682 B，78.12% | 873 → 201 | 672 B，76.98% |

- 新版签名证书核心开销为 `48 + 8L`，旧版用同一份 48 B 原始签名和每个地址 42 B 的 UTF-8 字符串，核心开销为 `48 + 42L`。旧版 `bytes()` 返回的是地址字节数加带 `0x` 的 98 字符 Hex 签名长度，本报告的核心列按原始签名字节计量。
- 完整二进制由 8 B Chain ID、8 B Epoch、1 B 签名长度、原始签名、8 B 序列长度及有序身份组成，整数均为小端。旧版仿真器使用 JSON，表中的旧版二进制用相同的头部实际编码，并为每个 42 B UTF-8 地址加 8 B 长度前缀。
- JSON 列直接测量 `serde_json` 输出，包括十进制索引文本、字段和完整 Hex 签名。JSON 大小随索引位数和 Epoch 位数变化。零跳证书使用空序列和空签名，核心为 0 B，完整二进制为 25 B。

## 正确性与经济结果

- 106 个 Rust 单元测试和 1 个 Rust 集成回归通过。覆盖 2、4、8、16 跳、二进制往返和异常长度、索引替换/交换/删除、未知索引、重复身份及 Proposer、交易篡改与真实签名交易替换、Epoch 和 Chain ID 错误、错误公钥与缓存隔离、无效签名，以及 Hop 内和跨证书的签名抵消。亦覆盖 Sybil 证书、零跳、Genesis、缺失证书和区块 Path Root 篡改。12 个 Python 审计单元测试通过。
- 对照旧版 `8caf1df`，四组固定样本的签名字节、每个地址的奖励、燃烧费用、最新及已生效的 Propagation Score、归一化评分及 Proposer 权重均通过精确快照比较。快照使用相同的确定性浮点求和顺序。另以配置上限 2 跳检查更长证书的 Relay 奖励和评分贡献均为零。 [对照回归](../tests/validator_index_economics.rs)
- 小规模证书 Smoke Test 通过全部 49 个检查样本。常规 Rust Smoke Test 在 2 个 Epoch 内产出 7 个区块，13 笔入块交易的证书均有效，注册表包含 11 个身份。独立 runner 在 1 个 Epoch 内产出 3 个区块、10 条证书，其中 6 条为多跳。用相同种子和零 withholding 概率与旧版配对，解码后的证书、签名、交易、Proposer、epoch 经济记录及评分权重记录一致。

复现证书大小和验收检查时，可从仓库根目录运行以下命令。输出写入新建的临时目录。

```bash
out=$(mktemp -d /tmp/trail-index-evidence.XXXXXX)
cargo run --offline --bin frozen_evidence_bench -- \
  --hops 2,4,8,16 --warmup 1 --repetitions 2 --block-repetitions 1 \
  --evidence-work-limit 32 --samples "$out/samples.csv" \
  --summary "$out/summary.csv" --report "$out/report.json"
```

## 语义与历史数据兼容性

传播阶段仍签署原始地址消息，每条路径独立验证自己的聚合签名。奖励、评分、选举公式和实验配置沿用原值。证书校验要求仿真器的 Chain ID `7032030` 且拒绝把区块头 Proposer 再写入索引序列。区块基础校验与路径证据资格保持分层，正确提交了无效或缺失证据的交易仍按原有规则处理奖励与评分。

Path Root 仍对证书 JSON 计算，非空路径的 JSON 字段由字符串变为整数，因此相应 Path Root 和区块哈希会变化。带非空字符串地址序列的历史区块需要旧版 Rust 读取，两个 Python 审计脚本可继续读取该格式。解释新版记录时需要保留同一运行的注册表。完整二进制大小来自新增证书编解码器，现有区块 JSON 导出继续使用 JSON。

首次测试触发了既有拓扑测试写入工作区根目录的 `graph.json`。该文件现与 `TRAIL-bac` 备份的 SHA-256 一致，缺少测试前的独立校验值。后续单元测试和 Smoke Test 的文件输出均置于独立临时目录。
