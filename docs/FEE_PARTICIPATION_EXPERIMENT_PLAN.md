# 中继参与实验：当前四图参考与历史方案

## 当前源码：已还原至昨日10seed实验版本（2026-11-13 18:38）

按用户要求，已按10seed实验对应快照还原seed/时钟/调度、转发行为决策和实验启动器，
使用昨日的多线程运行方式与 `ema_premiums_v2` 决策模型。
今日新增的确定性与逐节点模型代码、入口及配套测试已备份归档；下文今日实验记录为历史结果。
还原前源码和可执行文件、移出文件及核对清单见
[`restore-yesterday-20261113-183832/`](../results/backups/restore-yesterday-20261113-183832/)。
今日试验与诊断的原始日志、图表和冻结文件已整体移入该备份目录的 `experiment_results/`，
原位置已移除；1589个文件逐项SHA256核对通过。昨日10seed四图及其280次运行原始数据保留在原目录。

## 已完成：20seed补跑，seed210–219（2026-11-14 05:29）

用户确认在已有seed200–209基础上补跑210–219，形成20seed完整结果。
新增280次：四种机制 × 每seed七个场景 × 十个seed；每次200epochs，
参与均值仍取100–199，纳入时间仍采用50逻辑秒完整随访。
七个场景为成本5倍下五个fee（0.0001/0.0005/0.001/0.005/0.01），
以及F=0.001下成本1倍和2倍。Full使用η=0.5、θ=0.25；Ersoy的C=0.25；
g=0.0001、初始支付余额10、经济stake不再投资，全部沿用原10seed配置。

计划为 `results/batches/participation-20seeds-v2-plan/config.yaml`，
运行状态为 `results/batches/participation-20seeds-v2/manifest.json`。
使用原归档二进制 `489797bf87a0...`、8组并发。合并时保留原280次运行，
不排除seed209，也不以其后来复跑值替换主数据；合计560次运行。
新输出单独保存到 `results/processed/trail_four_mechanisms_20seeds_v2/`，
其中 `paper_figures/` 采用 `figures/trail_participation` 的PDF/300dpi PNG格式。

新增280/280次成功，无失败；全部560次运行联合审计通过，核对22400个决策窗口、
111440个节点权益份额、56000个成本缩放记录和240个交易纳入随访队列。
保留原280条运行索引及原始数值。20seed均值与逐点95% Student-t区间使用df=19。
常用目录 `figures/trail_participationV2/` 已同步为完整20seed四图；生成脚本默认读取20seed数据，
旧10seed图在更新前另行备份，原10seed结果目录继续保留。

随后按用户要求，常用目录中的b图更新为统一排除seed209、212后的18seed敏感性预览，
四种机制在全部fee点使用相同的18个seed，重算均值及df=17的95%区间，图例与坐标文字保持原样。
a、c、d仍为完整20seed结果，原始560次运行及完整20seed统计继续保留。
复现当前b图：`python3 analysis/plot_trail_relay_participation_v2.py --panels b --exclude-seed 209 --exclude-seed 212`。
省略排除选项可重画完整20seed结果；当前导出范围记录于结果目录的 `publication_export.json`。

Git提交后来源标签发生变化。跨批次合并仍逐个核对实际可执行文件哈希，
并用历史源码归档与新批次源码快照证明26个源码/依赖/启动器文件逐项一致；
两组Git来源均记录到审计文件。合并入口为 `experiments/four_mechanism_20seed_report.py`。

## 已完成：3倍成本敏感性对照（2026-11-13）

按用户要求，只新增Full与Ersoy、seed210、F=0.0001/0.01、成本3倍的4次80epoch运行。
同二进制、同模型、同外生输入的成本5倍数据复用；仅c_i按3/5缩放，其他参数保持一致。
18:31全部完成，4/4成功，6400条个体决策及四组成本配对核对通过。状态为
`results/backups/restore-yesterday-20261113-183832/experiment_results/batches/individual-cost3-v4/manifest.json`，结果位于
`results/backups/restore-yesterday-20261113-183832/experiment_results/processed/individual_cost3_v4/`。40–79epoch均值：3倍成本下，低费/高费的
Full为66.41/63.64%，Ersoy为29.17/56.04%；相对5倍成本均有提升，Full高费略低的现象仍在。
本次仅为单seed、80epochs的短程成本敏感性检查，详细配对表见
[说明](INDIVIDUAL_RELAY_DECISIONS.md)。

## 正在进行：逐节点决策优化（2026-11-13）

按用户要求实现 `individual_projection_v1`，在四种机制的新实验中统一启用。
收入、工作量和有效贡献仅从节点自身历史估计，并按epoch衰减；Full增量出块收益投影包含
实际评分饱和、EMA、权重归一化和激活延迟。96项Rust与29项Python测试、12次短程运行、
320条节点决策独立复算、8组重复/前缀比较及两组旧模型兼容轨迹均通过。
新seed210、四机制×两fee×80epochs的小样本于17:47完成，8/8成功，12800条节点预测/动作记录复算通过。
经济参数不变。F=0.0001/0.01时，40–79epoch均值分别为PoS14.51/14.51%、Fee-only14.51/54.19%、
Ersoy23.81/53.71%、Full64.36/62.18%。Full的活跃节点数比例随fee增加，但权益比例略降；
权益分解由节点99的-4.51pp与其他节点合计+2.32pp构成。该单seed短程结果不作为稳态或单调性证据。
方法、假设和限制见 [说明](INDIVIDUAL_RELAY_DECISIONS.md)，当前状态见
[`manifest.json`](../results/backups/restore-yesterday-20261113-183832/experiment_results/batches/individual-estimator-v4/manifest.json)。

## 正在进行：重复性修复与单节点收益核验（2026-11-13）

已增加 `--deterministic` 逻辑时间模式及单节点 Active/Lazy 配对干预工具。
87 项 Rust 与 25 项 Python 测试通过；200/500 epoch 验收于15:05完成，
21/21次运行成功、14/14组逐项比较一致。指定节点收益对照经并行填充后于16:40全部完成，8个分支核对通过。
本轮不改变经济参数或现有行为估计器，也不覆盖原四图。
实现、验收口径和诊断限制见 [说明](PARTICIPATION_REPRODUCIBILITY.md)，当前进度见
[`manifest.json`](../results/backups/restore-yesterday-20261113-183832/experiment_results/batches/participation-validation-v3/manifest.json)。

独立复算结果表明，公共信号与个体收益方向不一致的情况同时出现在Fee-only和Full。
F=0.01时，Fee-only节点0的公共信号倾向Lazy，但40轮Active相比Lazy的实现净收益高1.6316token；
Full节点95的公共信号倾向Active，但额外2604次转发没有新增奖励，净收益低0.00448token。
两组窗口与基准策略不同，不能跨机制直接排序；结果仅限seed209两个指定节点的有限窗口。
这支持改进公共估计对个体的适用性，尚不视为估计器算术错误或均衡证明。
两个seed修复后的高费对照未复现旧的均值回落，早期低参与谷仍然存在。
轨迹、逐节点净收益及余额复算见
[`participation_validation_v3/`](../results/backups/restore-yesterday-20261113-183832/experiment_results/processed/participation_validation_v3/)。

## 10 个 seed 的后台扩展（2026-11-12）

已于北京时间 **2026-11-13 03:00** 完成，耗时约 8 小时 13 分钟。
新增 224/224 次成功，无失败；合并后 280 次通过联合审计：
11200 个行为更新窗口、55720 个 stake 份额检查、28000 个成本缩放检查和 120 个纳入时间队列。
四联图、四张单图及数据包位于
[`trail_four_mechanisms_10seeds_v2/`](../results/processed/trail_four_mechanisms_10seeds_v2/)。

低 fee 优势仍然明显：F=0.0001 时 Full/Ersoy 均值为 77.18%/14.57%；
F=0.001 时为 83.97%/75.71%。高 fee 差距缩小：F=0.005 为 91.09%/90.20%，
F=0.01 为 89.62%/89.40%；后者 Full−Ersoy 的配对均值差为 +0.21 pp，
95% 区间为 [-3.61,+4.04] pp，不能据此确认稳定领先，也不能证明等效。
Full 最高两费点的样本均值下降 1.47 pp，但只在 2/10 个 seed 中下降，
配对变化区间跨 0；当前结果不支持严格单调或系统性下降的结论。
固定 F=0.001、成本 5 倍时，Full/Ersoy 纳入时间均值为 1.040/1.141 秒。
逐 seed 配对比较、方法与来源指纹保存在该目录的 `paired_comparisons.csv` 和
`paired_comparisons_audit.json`，也已加入下载包。

Fee-only 高费端诊断：从 F=0.005 到 0.01，10 个 seed 为 7 升、1 平、2 降。
seed 209 的窗口均值从 91.74% 降至 34.32%，对总均值贡献 -5.74 pp，
其余 9 个 seed 合计抵消 +0.90 pp，最终变化 -4.84 pp。
日志中一个低 stake、高连接度的枢纽节点在首次策略更新时命中“尝试相反策略”的探索：
0.005 的收益判断为 Lazy，探索后进入 Active；0.01 的收益判断为 Active，探索后保持 Lazy。
该节点在高费运行中直到 epoch 190 才转为 Active；Lazy 仍有 25% 转发概率并可获得路径手续费，
组间单位 stake 收益比较因此混有节点位置与组别构成差异。
高费运行在 epoch 24–124 的 20 个有效决策窗口出现负收益估计，触发退出；
epoch 129 起估计转正并恢复，末尾达到 76.41%，100–199 窗口仍涵盖退出和恢复。
独立复现了 rand 0.8.5 的候选/探索序列，780 次策略决策与下一 epoch 的节点状态一致。
这里的全局网络节点编号不等于交易路径位置 v0；本轮仅诊断，未修改模型或重跑实验。
证据、逐 seed 数据、决策复现与轨迹图保存在
[`fee_only_high_fee_10seeds/`](../results/backups/restore-yesterday-20261113-183832/experiment_results/diagnostics/fee_only_high_fee_10seeds/)。
当前结果提示需要检查群组估计的构成偏差与时长收敛性，不能把有限窗口的均值回落直接解释为高费稳态参与率更低。

2026-11-13 继续检查：对 seed203/209、F=0.005/0.01 做 stake 加权的节点和时段固定效应
观测性对照；seed209/F=0.01 的前125epochs，合并组间收益/工作差比为 -0.81e-6，
固定效应系数比为 +10.25e-6 token/attempt。独立显式加权虚拟变量回归与去均值计算一致。
这支持构成效应解释，但策略内生性、网络溢出和从未切换节点的反事实仍未解决，不能视为因果收益。
数值及方法见诊断目录的 `composition_checks.csv`、`composition_method.json`。

4 次 500epochs 诊断已于北京时间 2026-11-13 10:06 完成，4/4 成功，耗时约28.5分钟；
400个EMA决策窗口复算和16个窗口均值复核通过。经济与行为参数不变，比较100–199到400–499的各窗口。
配置 `experiments/configs/trail_fee_only_duration_500_v2.yaml`，状态
`results/backups/restore-yesterday-20261113-183832/experiment_results/batches/fee-only-duration500-v2/manifest.json`；结果位于
`results/backups/restore-yesterday-20261113-183832/experiment_results/processed/trail_fee_only_duration_500_v2/`，不会替换原10seed主图。

末100epochs（400–499）的参与率均值：

| seed | F=0.005 | F=0.01 |
|---|---|---|
| 203 | 91.96% | 88.52% |
| 209 | 75.82% | 95.13% |

仅此两seed的末段均值为83.89%/91.83%，不外推为10seed总体或正式手续费曲线。
本次seed209/F=0.005从100–199的10.07%恢复到400–499的75.82%，显示部分路径的低参与过程超过200epochs；
其末100epochs仍在61.14%–89.19%之间波动，不能据此保证已收敛。
seed203末段高fee仍低3.45pp，说明延长时长本身不保证单调。
所有4次都未复现旧前段，不能当作旧轨迹的直接续跑，亦不能宣称已经证明原高fee下降会随时长消失。
轨迹图见该结果目录的 `convergence.png/.pdf`，详细分窗统计见 `windows.csv`，限制见 `audit.json`。

前段重复性检查发现4次均在首次策略更新（epoch4）与旧路径分岔。
配置差异仅为最大时长及输出标识，初始图、stake、成本、策略一致。

已用现有编译库做最小API复现：交易身份包含真实系统秒；同一真实秒内不同逻辑slot的
相同交易可生成同一hash，换真实秒则同一固定wallet seed的交易hash改变，且交易验证仍通过。
代码位置为 `src/blockchain/transaction.rs:79`、`src/network/node.rs:821` 和 `src/tools/mod.rs:17`；
逻辑epoch/slot在hash之后附加。此外，内存池从HashMap遍历，仅按fee/真实timestamp排序，
完全相等项没有显式固定顺序。以上是需要修复/复测的重复性来源，不把某一处单独认定为全部漂移的唯一原因。
已有校验确认账本、EMA及统计复算一致，并未保证整段模拟同seed可复现。
长时报告将明确列出前缀是否复现；未复现时只能按同配置长时重跑解释，不能当作原路径续跑。
证据见诊断目录 `reproducibility_checks.json` 与 `reproducibility_probe.rs/.json`。

另一个独立的最小API复现确认：相同wallet/graph seed调用现有BA构造器两次，
有向边集合完全一致，但100个节点中62个的实际邻居插入顺序不同，关键枢纽也不同。
原因是BA构造器通过HashMap/HashSet遍历插入节点和边，网络按边插入顺序构造邻居向量；
Lazy随后逐邻居消耗带seed的随机转发数，顺序变化会改变随机数与邻居的对应关系。
`graph_order_probe.rs/.json` 复现的是编译库返回的实际边插入顺序，
没有用已排序/去重的graph.json来冒充运行时顺序。
初始无向边集、stake、策略、成本及degree已匹配；辅助betweenness指标也观察到变动，
不作为本次不变量或因果解释。该指标的单前驱BFS对遍历顺序敏感，另需修复/验证。
因此，修复重复性至少应覆盖交易唯一身份/时间、图与邻居遍历顺序，并继续检查同优先级交易排序和异步事件顺序。

新长时运行的前200epochs完成后，同一100–199窗口已经出现大幅差异：
seed209/F=0.005 原为91.74%、本次10.07%；F=0.01 原为34.32%、本次92.14%。
这发生在原统计范围内，不能解释成额外300epochs带来的恢复。
新旧前段窗口对照见 `early_rerun_window_comparison.json`；后续500epochs报告仍将单列前缀不复现的限制，
不据此覆盖原主图或宣称原轨迹已收敛。

用户确认沿用下述四张图、四种机制和全部参数，保留 seed 200/201，
新增 seed 202–209；每次仍为 200 epochs / 100 warm-up。
每个 seed 的独立场景为成本 5 倍的五个 fee，加成本 1/2 倍、F=0.001，
共 7 场景 × 4 机制。新增 224 次，复用已有 56 次，最终联合分析 280 次。

配置为 `experiments/configs/trail_four_mechanisms_extra8_v2.yaml`，最多并发 8 次。
后台状态见 `results/batches/four-figures-10seeds-v2/manifest.json`；
其中 `expected_runs=224` 仅统计新增运行，`reused_runs=56`、`total_analysis_runs=280`。
`status=complete` 表示新增模拟、子批次校验、10-seed 联合审计、图表与 ZIP 导出均通过。

完成后由 `experiments/four_mechanism_10seed_report.py` 自动合并，输出到
`results/processed/trail_four_mechanisms_10seeds_v2/`。
曲线为 10 个 seed 等权均值；阴影和误差棒改为跨 seed 的逐点 95% Student-t 置信区间，
自由度 9，不将同一次运行的多个 epoch 当成独立重复。
参与率区间超出 0–100% 时，图中会注明坐标裁切，CSV 保留完整区间。
配色、线型、字体、空心标记、图例和子图版式沿用下述版本。

模拟器二进制、分析脚本、旧数据索引及已有运行的配置/行为日志均记录哈希并冻结；
脚本会在阶段切换与最终汇总前检查，避免合并不匹配的数据。
原两 seed 的四机制图完整保留，不会被这轮覆盖。

## 当前四张图的统一参考（2026-11-12）

用户随后要求加入 PoS 与 Fee-only，补充配置为
`experiments/configs/trail_four_baselines_estimator_v2.yaml`。
沿用下述全部共同参数与修正后的二进制；Fee-only 采用 eta=0、theta=0.25，
PoS 仅按 stake 选主、手续费全部给 proposer。
补充矩阵为成本 5 倍的五个 fee（20 次）加成本 1/2 倍、F=0.001（8 次），
共新增 28 次；同一批 5 倍、F=0.001 日志用于 epoch 图与两张成本图的交点。
28 次补跑均已完成，状态见 `results/batches/fixed-four-baselines-v2/manifest.json`。
使用 `experiments/four_mechanism_reference_report.py`，输出到
`results/processed/trail_four_mechanisms_v2/`。
四机制样式为 PoS 灰色点线/三角、Fee-only 蓝色虚线/方块、Ersoy 绿色点划线/菱形、
Full 橙色实线/圆，均使用空心标记。

本轮比较 PoS、Fee-only、Ersoy、Full TRAIL；复用既有 Full/Ersoy 的 28 次日志，
加新补充的 28 次，合计 56 次不同运行。旧两机制图保留在 `trail_four_figures_v2/`。
共同参考点为 **F=0.001、5 倍转发成本**；Full 使用 eta=0.5、theta=0.25，
Ersoy 使用 C=0.25、eta=0。所有图使用 `ema_premiums_v2` 行为估计器与相同二进制。

| 图 | 横轴 | 纵轴 | 固定条件 |
|---|---|---|---|
| (a) | epoch 0–199 | Active-relay stake (%) | F=0.001，成本 5 倍 |
| (b) | F=0.0001、0.0005、0.001、0.005、0.01（对数轴） | Active-relay stake (%) | 成本 5 倍 |
| (c) | 转发成本 1、2、5 倍（对数轴） | Active-relay stake (%) | F=0.001 |
| (d) | 转发成本 1、2、5 倍（对数轴） | 受限平均纳入时间（逻辑秒） | F=0.001，固定随访 50 秒 |

共同设置：g=0.0001，不随手续费变化；每块补贴 0.1；100 节点、BA 网络、
stake Gini=0.6、20 tx/s、初始支付余额 10、奖励再投资率 0。
1 倍成本的中位数为 1.4055695425812074e-7 token / outbound forwarding attempt，
对数标准差 0.75；同 seed 的节点成本按倍数整体缩放。

每次运行 200 epochs，seed 200/201；(a) 保留完整轨迹，前 100 epochs 不用于 (b/c) 的均值，
(b/c) 平均日志索引 100–199。(d) 取 epochs 100–189 生成的交易，按 tx hash 首次生成
和首次纳入去重，未在 50 秒内纳入的交易计为 50 秒。曲线为等权 seed 均值；按用户提供的
原 Fig. 5 样式，(a) 使用浅色阴影带，(b–d) 使用误差棒，均表示两个 seed 的最小值至最大值，
不代表 95% 置信区间。各机制使用本节列出的颜色、线型与空心标记，
字体、细线、网格、图内图例和下方子图标题与参考图统一。
当前仍是两个 seed 的探索性预览，不据此确认稳态或总体优势。

图、原始来源索引、绘图数据、参数及审计均位于
[`results/processed/trail_four_mechanisms_v2/`](../results/processed/trail_four_mechanisms_v2/)。
完整参数见 [reference_settings.json](../results/processed/trail_four_mechanisms_v2/reference_settings.json)，
四联图见 [four_panels.pdf](../results/processed/trail_four_mechanisms_v2/four_panels.pdf)。
生成命令为 `python3 experiments/four_mechanism_reference_report.py`；
归档中的 `replot.py` 可直接从同目录 `plot_data.csv` 重画，无需重新模拟。

联合审计包含 2240 个行为更新窗口、11144 个 stake 份额检查、5600 个节点成本缩放检查，
以及 24 个纳入时间队列。PoS 的 3 个在 50 秒内纳入的记录携带较晚的重复生成副本时间，
日志中的副本延迟本身有效；统计统一从该 hash 全局首次生成开始计时。
对成本图中两个 PoS seed 点的修正分别为 +0.000447 秒与 +0.000112 秒，参与率不受影响。
修正前的新增基线报告保存在批次目录的 `reporting_before_clock_fix/`，
复算记录在四图目录的 `data_audit.json`。首次生成时间处理的回归检查在内的 17 项 Python 检查通过。

## 历史诊断与正式方案草案（不作为当前四图参数）

下文保留原方案和历次诊断；其中 theta=0.5、成本 1 倍、四机制及正式种子矩阵
属于较早的草案，尚未执行正式批次或时长预检查。当前四图采用上节参考参数。

2026-11-11 补充：现有 pilot 的 Full 在最低 fee 下，模型估计的贡献选主收益已远高于
典型转发成本；共同组间收益估计还存在构成偏差和少数组波动的风险。
在明确行为收益估计口径、权重时点和成本覆盖范围前，不启动下文的两份配置。
单纯延长到 500 epochs 不能解决收益估计口径问题。
现有日志的离线分解见 [平台诊断记录](../results/diagnostics/fee_participation_flatness/diagnosis.md)。

2026-11-11 18:10（北京时间）：已另行启动成本敏感性 pilot，配置为
`experiments/configs/trail_fee_participation_cost_probe.yaml`，由
`experiments/run_fee_cost_probe.py` 驱动。成本 5/10 倍、F=0.0001/0.01、
四机制、seed 200/201，共 32 次，200 epochs / 100 warm-up，最大并发 3。
eta=0.5、theta=0.5、g=0.0001 保持原值；支付余额统一为 10。
本轮沿用现有行为模型，仅检查成本饱和解释，未完成个体收益的因果验证。
总状态记录在 `results/batches/fixed-g-cost-probe-v1/manifest.json`，
各成本分别输出图表和校验报告；下文正式矩阵与时长预检查仍未启动。

该 pilot 已于同日 19:23 完成，32/32 次有效，无失败。Full 在 5 倍成本下的
两端均值为 77.41%→87.73%，10 倍成本下为 55.65%→88.35%。
两个 seed 均出现两端上升，但高 fee 下相对 Ersoy 的优势尚不稳定，
部分轨迹仍有负收益估计与明显窗口漂移。现有结果不确认中间手续费区间的曲线形状。
联合图表、汇总数据与原始节点 stake 复核记录保存在
`results/processed/trail_fee_participation_cost_probe/`。

2026-11-12 09:22（北京时间）：按最新讨论启动 Full 的中间 fee 补点，
成本 5/10 倍，F=0.0005、0.001、0.005，seed 200/201，共新增 12 次。
沿用已验证的原二进制、200/100 统计口径与其他参数，最多并行 3 个。
配置为 `experiments/configs/trail_full_fee_cost_midpoints.yaml`，状态记录于
`results/batches/fixed-g-full-fee-midpoints-v1/manifest.json`。
全部子批次校验后，自动运行 `experiments/full_fee_cost_curve_report.py`，
复用原两个端点的 8 次 Full 运行，合计 20 次，生成两条各 5 个实测点的曲线。
合并输出目录为 `results/processed/trail_full_fee_cost_curve/`；
校验包括同 seed/cost 下完整运行参数仅 fee 与输出标识不同、输入设计匹配、
二进制和源码指纹一致，以及原始策略日志与节点 stake 重算。

Full 补点已于 2026-11-12 09:56 完成，12/12 次有效，并已生成五点曲线。
同日 10:01 启动 Ersoy 的 5 倍成本补点：F=0.0005、0.001、0.005，
seed 200/201，C=0.25、eta=0，共新增 6 次，其他共同设置沿用原配置。
配置为 `experiments/configs/trail_ersoy_fee_cost5_midpoints.yaml`，状态记录于
`results/batches/fixed-g-ersoy-cost5-midpoints-v1/manifest.json`。
完成后自动运行 `experiments/ersoy_full_cost5_curve_report.py`，复用 Ersoy 端点 4 次
与 Full 五点 10 次，合计 20 次，输出至
`results/processed/trail_ersoy_full_cost5_curve/`。
保留全部点、逐 seed 轨迹、配对差值及高费端已有的大幅波动；不按是否领先筛选数据。

Ersoy 补点已于同日 10:17 完成，6/6 次有效，五点对比图已生成。
后续离线诊断重建了 10 次 Ersoy 运行的 400 个决策窗口，收益公式与 EMA 均精确匹配日志。
F=0.001、seed=200 在 epoch 89 的单位 stake 转发量差仅 0.706，
产生 -1.535e-4 的组间收益比值；epoch 99 无有效新观测时仍使用旧负 EMA 做策略更新。
epoch 104 起原始估计转正，EMA 至 154 才转正，造成长期退出和恢复过程。
这提示共同行为估计器的弱差分、观测过期处理与组别构成偏差需先验证/修正，
不能将较低窗口均值直接视为 Ersoy 的稳定表现。该结论不证明 Ersoy 分账公式有错。
诊断数据和轨迹图见 `results/diagnostics/ersoy_variability_cost5/`。

2026-11-12 10:35（北京时间）：已实现共用行为估计器稳健性修正 `ema_premiums_v2`，
先分别平滑收益差和工作差，再取比值；无效观测衰减历史证据并暂停收益驱动切换，
保留随机探索以恢复缺失组别。真实负收益不截断，未新增可调阈值。
共同组间收益的观察方式、手续费规则、Full 贡献选主规则及全部经济参数保持原定义；
该修正不消除组别构成偏差，也不等于已完成个体反事实激励验证。

验证：89 项 Rust 单元测试、20 项既有 Python 检查通过；Ersoy、Full、缺失 Lazy 组
三种短运行均通过独立 EMA/决策复算及奖励预算检查。
配置 `experiments/configs/trail_ersoy_full_cost5_estimator_v2.yaml` 已启动 20 次新运行：
5 倍成本、五个 fee、Ersoy/Full、seed 200/201，200 epochs / 100 warm-up，并发 3。
Ersoy 与 Full 全部使用修正后的同一二进制，未复用旧端点作为新版数据。
进度为 `results/batches/fixed-g-cost5-estimator-v2/manifest.json`。
完成后自动执行 `experiments/ersoy_full_estimator_comparison.py`，验证逐轮 EMA 与无效窗口切换，
并单独生成旧/新比较；输出目录 `results/processed/trail_ersoy_full_cost5_estimator_v2/`。

该轮已于同日 11:29 完成，20/20 有效，800 个决策窗口的矩、决策值与有效性门控
均通过独立复算。新版 Ersoy 五点均值依次为 13.42%、51.58%、68.33%、84.24%、84.25%；
Full 为 78.47%、79.00%、81.92%、88.71%、85.11%。
中高费端 Ersoy 先前异常低的 seed 明显恢复，F=0.01 的平均差距从旧版 19.49 pp 缩为 0.86 pp，
逐 seed 新差值为 +3.81、-2.10 pp。Full 的最后一步在两个 seed 中都下降，均值下降 3.60 pp。
这支持数值放大影响了旧结果，但两 seed 和 200 epoch 仍不足以确认稳定参与率或高费等效。

2026-11-12 11:42（北京时间）：按用户指定启动 Full theta=0.25 敏感性重跑。
配置为 `experiments/configs/trail_full_theta025_cost5_estimator_v2.yaml`，
5 倍成本、原五个 fee、seed 200/201，共 10 次，其他参数、二进制与修正后的估计器均保持一致。
同 seed/fee 下仅 theta 与输出标识改变，完整命令已核对，启动后的实际配置也确认 theta=0.25。
进度为 `results/batches/fixed-g-cost5-theta025-v2/manifest.json`。
完成后自动运行 `experiments/full_theta025_cost5_report.py`，复用上一轮 theta=0.5 的 Full
与 Ersoy 数据作为独立对照，共 30 次，结果保存至
`results/processed/trail_full_theta025_cost5_estimator_v2/`。

该轮于同日 12:12 完成，10/10 有效；合并 30 次运行的 1200 个决策窗口复算通过。
theta=0.25 的五点均值为 78.67%、79.10%、82.39%、88.76%、89.78%，
两个 seed 各自的五点也均单调上升。F=0.01 相对 theta=0.5 的改善为平均 +4.67 pp，
逐 seed +3.83、+5.50 pp；相对 Ersoy 均值领先 5.52 pp。
其他 fee 的 theta 效应均值仅 +0.05 至 +0.46 pp。该结果仍为两 seed、有限观察窗口的敏感性证据，
不能据此确认普遍单调性、稳态或正式采用某个参数；旧结果完整保留。

2026-11-12 16:10（北京时间）：按用户指定启动第三张图的 1/2/5 倍成本 pilot，
仅比较 Full 与 Ersoy，固定 F=0.001；Full eta=0.5、theta=0.25，Ersoy C=0.25、eta=0。
使用同一修正后二进制，seed 200/201，200 epochs / 100 warm-up，最大并发 3。
配置为 `experiments/configs/trail_cost_participation_estimator_v2.yaml`。
1/2 倍各新增 4 次，共 8 次；复用已有 5 倍的 4 次匹配结果，合计 12 次。
原草案的 10/20 倍成本未执行。状态为
`results/batches/fixed-fee-cost-trend-v2/manifest.json`；完成后自动运行
`experiments/cost_participation_curve_report.py`，输出成本—参与率图及校验至
`results/processed/trail_cost_participation_estimator_v2/`。
复用的 Ersoy 配置中 TRAIL 专用 theta 字段为 0.5，新配置该字段为 0.25；
Ersoy 的分账代码不使用该字段，C 与实际分账规则未变，审计会明确记录这个无效参数差异。

该轮于同日 16:36 完成，8/8 新运行有效；合并 12 次后，480 个决策窗口及
1200 个节点成本缩放检查通过。固定 F=0.001 时，1/2/5 倍成本下的均值依次为：
Full 90.78%、90.59%、82.39%；Ersoy 89.90%、84.11%、68.33%。
两个 seed 各自的三个点均随成本增加下降；Full−Ersoy 均值差从 0.88 pp 增至 14.06 pp。
保留原始逐 seed 轨迹及配对区间，当前两 seed、200 epoch 仍仅作为探索性证据。

第四张成本—纳入时间预览直接复用上述 12 次日志，未启动新模拟。
`experiments/cost_inclusion_time_report.py` 按 tx hash 首次生成与首次纳入的逻辑时隙独立复算：
队列取生成于 slots 500–949（epochs 100–189）的交易，固定随访 50 秒，未纳入/晚纳入按 50 秒计。
12 次 RMST 与已报告值完全一致，逐交易延迟也与日志一致；重复生成记录按 tx hash 去重。
Full 在 1/2/5 倍成本下为 1.049、1.042、1.046 秒，Ersoy 为 1.045、1.045、1.305 秒。
PNG/PDF、逐 seed 明细与复算记录位于第三张图同目录，文件前缀 `cost_inclusion_time`
及 `inclusion_time_`。5 倍成本的 Ersoy 差异主要来自 seed 200，当前两 seed 仍不能确认稳定总体差异。

## 研究问题与结论范围

研究在相同网络、成本与区块补贴下，降低可分配手续费 F 后，不同机制能否维持主动转发。
Full 通过贡献评分改变相对 proposer 概率，在手续费较低时可能提供额外参与激励；
手续费较高时，直接分配手续费也可能支撑较高参与率。

不把严格单调、Full 每点领先或两种机制在高费端等效作为数据筛选条件。
主图应报告所有预设点；高费端不显著不同不等于已经证明等效。

## 历史正式方案的固定设置草案（未执行）

| 项目 | 主实验草案 |
|---|---|
| 横轴 | 可分配交易手续费 F，对数坐标 |
| F | 0.0001、0.0002、0.0005、0.001、0.01 |
| 纵轴 | 统计窗口内 Active 节点持有的 stake / 全部经济 stake，按 epoch 平均 |
| 机制 | PoS、Fee-only、Ersoy-fee、Full TRAIL |
| Full eta | 0.5，保留原始配置；0.75 仅作为另行标注的敏感性配置 |
| Fee-only、Ersoy eta | 0，均按 stake 选主 |
| TRAIL proposer_fee_ratio theta | 0.5；Fee-only 与 Full 相同 |
| Ersoy C | 0.25，不随 F 改动 |
| 不可回收成本 g | 所有机制、所有 F 均固定 0.0001 |
| score_cost_reference | 固定 0.00001，因此相同有效路径的 q 恒为 1 |
| 每块基础奖励 | 所有机制均为 0.1 |
| 初始支付余额 | 所有节点、机制、fee 均为 10 token，与经济 stake 分开 |
| 交易扣款 | F + g；费用账本与实际扣款一致 |
| 经济 stake | 原始分布保持不变，奖励再投资率为 0 |
| 节点、网络 | 100 节点，BA，stake Gini = 0.6 |
| 提交负载 | 20 tx/s，相同种子使用同一生成时刻及收发方序列 |
| 转发成本 | 历史参考成本的 1 倍，对数正态 sigma = 0.75 |
| 成本中位数 | 1.4055695425812074e-7 token / outbound forward attempt |
| 初始 Active 节点比例 | 0.5，按节点数选取；不等同于 50% stake |
| 时隙 | 1 逻辑秒 / slot，5 slots / epoch |
| 区块容量 | 80 tx / block |
| 模拟加速 | time_scale = 0.05，不把机器实际运行时间解释为协议纳入时间 |
| 正式种子 | 300–319，共 20 个；未用于本轮参数探索 |
| 总时长 | 建议统一 500 epochs |
| 预热与统计 | 前 300 epochs 预热；日志索引 300–499，共 200 epochs 统计 |

Full 与 Fee-only 还共享 D=4、beta=0.8、saturation_k=1、score_floor_kappa=1、
bonus_zeta=1、bonus_cap=1、score_activation_delay_epochs=2、
reward_settlement_depth=2、max_path_hops=16、evidence_work_limit=4096、
challenge_work_limit=1024。保持已有证据验证和首到路径选择规则。

共同策略模型保持 adaptive_warmup_epochs=5、每 5 epochs 更新、每轮随机选择
10% 节点、收益 EMA alpha=0.25、切换缓冲 10%、探索比例 5%。
它是历史组别收益驱动的行为模型，不是个体完全信息最优响应或均衡证明。

## 为何推荐恢复 eta=0.5

0.5 是原参考配置。此前 eta=0.75 的两点小样本均值略高，但高费端逐种子效果
相反，尚不足以据此替换主配置。保留 0.75 的探索结果，可随后作为 eta 敏感性证据。
如讨论后改用 0.75，须在正式批次启动前统一修改并记录版本，不能运行后再择优选取。

## 运行时长与预检查

原 pilot 的 200/100 口径中，后半段仍出现显著的描述性漂移。
500/300 是改进观察长度的建议，不等于已经证明稳态。

正式 400 次运行前，建议先用种子 202、203，在 F=0.0001 和 0.01 两端，
对四个机制进行相同的 500 epoch 时长预检查，共 16 次。
检查 200–299、300–399、400–499 的轨迹和差异，确认是否仍有持续漂移、
组别收益估计异常或支付预算约束。预检查的判断不得以 Full 是否领先为依据。
如需延长时长，应在正式批次前为所有机制和 fee 统一改动；不只延长某个结果不理想的单元。

即便窗口均值较稳定，也仅报告有限观察期结果，不据此宣称已证明均衡或稳态。

## 统计与图表

每个种子先计算其统计窗口内的平均 Active-relay stake，然后对 20 个种子
计算均值及双侧 95% Student t 置信区间。epoch 不作为独立重复样本。

同时保存同 seed 的 Full − Fee-only 与 Full − Ersoy 差值及配对置信区间。
前者用于分析贡献出块加成，后者用于对比不同激励设计。
所有预设 fee 的结果和区间均保留，不将交叉或非单调点删去。

主图显示跨种子均值和 95% 区间，原始单种子结果保存在 CSV 中。
若需要声称高费端等效，应另行事先规定等效界限和检验；本方案不预设此类结论。

辅助指标保留受限平均纳入时间、超时调整 P95、纳入率、中继净收入等。
500 epoch 设置下，纳入队列取生成于 300–489 的交易，并给予固定 10 epoch
（50 逻辑秒）随访；未纳入或超时计为窗口上限，避免仅保留成功交易。
同时报告 submitted 与 recorded generation 的差异；相同提交负载不代表每组实际
生成记录数一定相等。

## 可复现性与检查

- 正式矩阵为 5 fees × 4 机制 × 20 seeds = 400 次，每次 500 epochs。
- 每 seed 跨机制和 fee 检查 stake、初始策略、成本分布、图拓扑和提交负载。
- 支付余额独立于经济 stake；检查每节点累计支出小于统一初始支付余额，及 F+g 账本。
- 检查奖励预算、逐节点奖励与 epoch 汇总、完整 epoch 覆盖、有限指标及 stake-only 权重。
- 使用同一个归档二进制和源代码快照，保留配置、源码差异、二进制哈希及逐运行状态。
- 正式矩阵不混入此前种子 100–102、200–201 的探索数据。
- 报告明确说明 Ersoy 是共享 PoS 传播环境中的手续费分配适配，没有复现原生智能路由。

配置草案：

- `experiments/configs/trail_fee_participation_fixed_g_main.yaml`：400 次正式候选矩阵。
- `experiments/configs/trail_fee_participation_duration_check.yaml`：16 次时长预检查候选矩阵。

此前 32 次、200 epoch 的 fee pilot 耗时约 81 分钟。按相同并发度简单线性外推，
400 次、500 epoch 约需 40–45 小时；这是资源估计，实际耗时可能随参与率和运行负载变化。
