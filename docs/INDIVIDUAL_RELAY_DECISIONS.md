# 逐节点中继收益投影：individual_projection_v1

2026-11-13 18:38：按用户要求已还原至昨日10seed源码；本页记录今日模型的历史实现与结果。
本页所列新增源码、入口和测试已移入还原备份，当前可用模型见
[当前源码记录](FEE_PARTICIPATION_EXPERIMENT_PLAN.md)。

## 使用范围和当前状态

该版本改进模拟器的参与决策层。PoS、Fee-only、Ersoy、Full 在新实验中统一使用同一算法，
手续费分配、贡献记分、实际 proposer 选择、成本分布和经济 stake 的规则保持原设置。

代码入口是 `src/network/individual_benefit.rs`，实际策略更新位于 `world_state.rs`。
CLI 使用 `--adaptive-benefit-model individual-projection-v1`。
旧模型通过 `--adaptive-benefit-model pooled-premiums-v2` 保留，仍是历史配置的默认解析结果，
用于复现与对照；新实验入口明确传入逐节点模型，避免静默混用两种模型。

小样本入口：`python3 experiments/run_individual_estimator_pilot.py --phase pilot --max-parallel 3`。
状态见 [`manifest.json`](../results/backups/restore-yesterday-20261113-183832/experiment_results/batches/individual-estimator-v4/manifest.json)，
计划与实际参数分别见同目录 `run_plan.json` 和每次运行的 `run_config.json`。
所有运行使用冻结的二进制和源码快照，完成后自动独立复算并输出趋势图。

## 新种子小样本结果（2026-11-13）

17:47完成，8/8次运行成功，耗时约35分20秒；12800条逐节点预测/动作记录复算通过。
另行核对了相同的初始stake/策略/成本、物理拓扑和提交负载；支付余额充足。
均值、曲线及复核记录位于
[`individual_estimator_v4/`](../results/backups/restore-yesterday-20261113-183832/experiment_results/processed/individual_estimator_v4/)。

以下为seed210、成本5倍、epochs40–79的平均Active-relay stake，单位为总stake百分比。

| 机制 | F=0.0001 | F=0.01 |
|---|---:|---:|
| PoS | 14.51% | 14.51% |
| Fee-only | 14.51% | 54.19% |
| Ersoy | 23.81% | 53.71% |
| Full | 64.36% | 62.18% |

本次Full在两个点均领先，但高费点比低费点低2.19pp。逐节点权益分解显示，节点99持有
4.5058%总stake，在低费统计窗口40/40轮Active、高费0/40轮Active，单独贡献-4.5058pp；
其他节点合计抵消约+2.3190pp。与此同时Full的活跃节点数比例从48.275%升到58.5%，
所以节点数参与和权益参与在这两个点并不同向。详见 `full_stake_decomposition.csv`。

节点99在两个费点的成本相同，epoch39均未触发探索；低费的个体收益预测高于成本而选择Active，
高费的预测低于成本而保持Lazy。在此前epochs30–39中，该节点低费获得原始贡献0.302986，
高费原始贡献和中继收入均为0，工作量分别为205/211次。日志与有效路径奖励机会变化的解释一致，
但这不是独立控制其他节点策略的因果机制验证，也不证明手续费升高普遍导致参与权益降低。

在epochs39–79中的非探索Active决策记录里，低费Full的36次决定都需要投影的proposer增益，
高费49次中只有7次需要该项，其他42次仅靠手续费分量即可维持Active。这是模型内部收益分解，
不是实际移除select机制后重跑网络的因果消融。该结果支持本轮模型中低费依赖select、高费更多
依赖手续费的解释。单seed短程结果仍不提供总体排序、置信区间、稳态或单调性结论。

低费Fee-only与PoS的策略轨迹逐项重合：被选中更新的节点均没有出现个体收益高于成本的情形；
两组保留相同初始状态、候选选择和探索。其14.51%仍包含暂未退出及探索的Active节点，不能当作稳态。

## 追加3倍成本对照（已完成）

按用户要求，在相同seed210、100节点、80epochs下新增Full/Ersoy各两个手续费点
（0.0001、0.01）的3倍成本运行，共4次。相应5倍成本的4次已有日志直接复用。
使用同一冻结二进制 `3f758e4abe94...`，只将转发成本中位数乘数由5改3，
每个节点的c_i均应为原来的0.6倍；其他配置和外生输入保持一致。

入口 `experiments/run_individual_cost3_probe.py`，状态见
[`manifest.json`](../results/backups/restore-yesterday-20261113-183832/experiment_results/batches/individual-cost3-v4/manifest.json)。
每次结束后核对成本缩放、初始stake/策略/图/负载、首次更新前5epochs的逐节点历史，
并独立复算个体决策。完成后自动保存40–79epoch的3x/5x均值对照及成本图至
`results/backups/restore-yesterday-20261113-183832/experiment_results/processed/individual_cost3_v4/`。本次仍为单seed短程敏感性测试，不预设参与率随成本单调。

2026-11-13 18:31完成，4/4次新运行成功，耗时25分18秒；6400条个体决策独立复算通过。
四组配对均仅改变成本及输出标识，32000条节点成本缩放和2000条首次决策前历史核对通过；
余额均充足。以下为epochs40–79的平均Active-relay stake：

| 机制 | 手续费 | 5倍成本 | 3倍成本 | 变化（百分点） |
|---|---:|---:|---:|---:|
| Ersoy | 0.0001 | 23.81% | 29.17% | +5.37 |
| Full | 0.0001 | 64.36% | 66.41% | +2.05 |
| Ersoy | 0.01 | 53.71% | 56.04% | +2.33 |
| Full | 0.01 | 62.18% | 63.64% | +1.47 |

本轮降低成本提升了四组均值，但未消除Full高费端点略低的现象：3倍成本下，
Full高费比低费低2.77个百分点，活跃节点数比例则由52.50%升至61.625%。
这两个端点不足以确定完整手续费曲线。图及原始均值见
[`individual_cost3_v4/`](../results/backups/restore-yesterday-20261113-183832/experiment_results/processed/individual_cost3_v4/)。

## 估计每个节点自己的收益

对节点 i，从已经结束的 epoch 读取：

- 实际中继手续费收入 R_i；
- 实际付出成本的 outbound 转发次数 W_i；
- 已纳入有效路径的原始贡献 C_i。

当前转发概率记作 π，Active为1，Lazy为0.25。先将上述观测分别除以 π，
得到全概率转发下的局部收入、工作量、有效贡献率近似，然后仅在该节点自己的历史上平滑。
不使用其他节点的手续费收入来初始化、补全或替代它的收入。

这是一项明确的模型假设：在当前观测环境下，局部收入、工作和贡献近似随转发概率缩放。
真实路径竞争和额外转发带来的网络反馈可能产生非线性，后续对照仍须检查这些偏差。

## 观测过时与缺失

个体率的默认 epoch EMA 系数为0.25（`--adaptive-individual-rate-ema-alpha`），与
旧公共诊断量每个更新窗口的 EMA 分开。窗口内观测按 epoch 指数加权；长度 n 的窗口结束后，
旧个体历史乘以 `0.75^n`。第一个窗口用归一化的指数加权观测初始化。
策略仍按原设置每5epochs更新、每次选择10%节点、保留5%的相反策略探索和10%的切换缓冲。

有工作量、没有奖励和有效贡献时，零收益是有效观测；缺少工作量时，收益估计不可用。
估计不可用时保留当前策略并允许既有探索，不回退到公共收益信号。
实际学习和决策只依赖已经完成的观测，不读取后续 epoch 或诊断对照的未来收益。

## Full 的延迟出块增益

对两个候选动作 π_A=1、π_L=0.25，分别推算节点未来的原始贡献，再使用实际协议公式：

1. 依 normalized stake 和 K 做对数贡献饱和。
2. 按 `S_next = beta*saturated + (1-beta)*S_previous` 更新评分。
   frozen-v1的 beta 权重作用于新观测。
3. 计入原配置的评分激活延迟；本轮为2epochs，之前的共同 pending roots 不产生动作差值。
4. 依 kappa、zeta、bonus cap、eta 重新计算并归一化所有 proposer 权重。
   自身评分改变时也更新共同的评分阻尼分母，不只调整自身权重分子。

预测时，其他节点的最新公开评分暂时固定，未来 proposer 奖励池用历史观测的平滑值近似。
该奖励池包括基础补贴和已实现的 proposer 手续费；路径相关手续费不是精确的反事实期望。
这是局部条件投影，不是复制整个网络的反事实运行。

默认预测20epochs、逐epoch折扣0.95，参数在新种子运行前固定并对四组一致。
将折扣后的额外中继收入和额外预期 proposer 收入相加，再除以折扣后的额外转发次数，
得到节点自己的每次额外转发收益，与它自己的 c_i 作原有切换缓冲比较。
eta=0时贡献出块增益严格为0；已有 stake 收益和旧评分的共同收益不记成新转发的增量收益。

## 日志与独立核对

新文件 `individual_relay_decisions.csv` 每次更新为每个节点记录：
窗口观测、有效 EMA 系数、平滑后的局部率、normalized stake、最新评分、预测 proposer 池、
折扣后的增量收入与工作量、成本、预测净增益，以及实际是否被选择、探索及切换。

`adaptive_relay_metrics.csv` 中原来的组别列继续提供公共诊断量。
逐节点模型下其公共 `decision_benefit_per_forward` 留空，`decision_mode` 明确为
`individual_projection_v1`；实际决策以逐节点日志为准。
`node_epoch_metrics.csv` 的收益估计字段记录此前一次更新所得的该节点估计，缺失标志见逐节点日志。

`experiments/individual_estimator_audit.py` 用独立 Python 实现复算局部历史、评分预测、
权重归一化和实际动作。另与原逐epoch节点/奖励日志做绑定核对；原日志有小数舍入，绑定核对保留
相应精度界限，完整精度预测输入和输出也记录在逐节点日志中。

已通过96项Rust、29项Python测试。四种机制共12次小规模运行，320条节点决策独立核对，
8组同seed重复/延长前缀逐项一致；旧版Fee-only和Full的两组15epoch轨迹也逐项复现。

## 回看与新种子小样本

历史回看使用此前已检查的seed209，只将截至决策时刻的旧日志输入新公式。
开发用的四个案例中，新信号方向均与后续40epochs的实现净收益符号一致，包括
Fee-only节点0/95建议Active、Full节点95建议Lazy；详见
[`retrospective.json`](../results/backups/restore-yesterday-20261113-183832/experiment_results/diagnostics/individual_estimator_v4/retrospective.json)。
这些案例已参与模型开发，不能作为未见过的验证集或总体准确率证明。

当前新种子验证使用seed210、100节点、成本5倍、F=0.0001和0.01，四种机制各80epochs，
共8次；初始支付余额10、g=0.0001、基础补贴0.1、reinvestment=0、Full eta=0.5/theta=0.25、
Ersoy C=0.25及既有其他经济参数保持不变。仅报告40–79epochs的描述性均值和完整轨迹。
该单seed短程实验用于功能与趋势检查，不提供置信区间、稳态/均衡或普遍机制排序的结论。
完成后结果保存于 `results/backups/restore-yesterday-20261113-183832/experiment_results/processed/individual_estimator_v4/`，不覆盖原四张主图。
