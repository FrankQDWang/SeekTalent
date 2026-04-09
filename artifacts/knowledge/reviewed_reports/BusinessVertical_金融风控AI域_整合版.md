---
report_id: report.business_vertical.finance_risk_control_ai.codex_synthesis_2026_04_07
report_type: business_vertical
domain_id: finance_risk_control_ai
title: BusinessVertical_金融风控AI域_整合版
source_model: Codex synthesis
generated_on: 2026-04-07
language: zh-CN
confidence_summary: high
source_reports:
  - BusinessVertical_金融风控AI域_知识库.md
  - 金融风控AI招聘知识库构建.md
---

# BusinessVertical_金融风控AI域_整合版

## Summary

本整合版把 `finance_risk_control_ai` 的默认核心范围收敛为三条主线：`信用评分/授信`、`申请反欺诈`、`支付交易风控`。这样做是为了提高首轮召回 precision，不把 `市场风险/量化交易/纯平台/MLOps` 这类相邻但边界更宽的方向混进默认主域。  
在招聘搜索里，本域最可靠的判断原则是：同一段文本里最好同时出现 `业务对象`、`风险标签`、`决策动作或组件`。例如：`授信/额度/信审` + `逾期/违约/坏账/DPD` + `评分卡/规则引擎/审批策略`。  
通用 AI/ML 词汇如 `XGBoost`、`LightGBM`、`Spark`、`特征工程` 只能算二级加分，不能单独证明候选人属于金融风控垂类。

## Canonical Terms

| Canonical Term | 定义 |
| --- | --- |
| 授信 / 信审 / 额度 / 定价 | 对是否放贷、批核额度与定价进行决策的核心业务动作。 |
| 信用评分卡 | 用于准入、行为管理或催收决策的标准化评分体系。 |
| A卡 / B卡 / C卡 | 申请评分、行为评分、催收评分等评分卡分工。 |
| 贷前 / 贷中 / 贷后 | 信贷生命周期里的三个主要风控阶段。 |
| 逾期 / DPD / 违约 / 坏账 / NPL / 核销 | 信用风险结果类锚点词。 |
| WOE / IV | 评分卡变量编码与筛选的强专有词。 |
| KS | 评分卡与风控模型区分度常见指标。 |
| PSI | 监控样本分布稳定性与漂移的常见指标。 |
| OOT | 按时间切分的独立验证方式。 |
| Reject Inference | 处理拒贷样本偏差的常见方法或流程。 |
| 表现期 / 观察窗口 | 用于定义违约/逾期标签的时间窗口。 |
| 申请欺诈 / ATO | 申请阶段与账户接管场景下的反欺诈对象。 |
| 交易欺诈 / Chargeback | 支付或交易场景下的拒付、盗刷和资金损失风险。 |
| 规则引擎 / 决策引擎 | 把模型分数与业务规则结合，输出放行、拦截或转人工。 |
| 实时拦截 / 实时评分 | 在申请或交易发生时给出即时风险决策。 |
| 设备指纹 | 用终端和浏览器环境形成设备级风险标识。 |
| 团伙识别 / 关系图谱 | 用账户、设备、IP、交易关系识别协同欺诈。 |
| 多头借贷 / 过度负债 | 授信风险中的高危行为和财务压力信号。 |
| 银行扩展词 | `PD/LGD/EAD`、`IRB/模型验证`、`AML/KYC/CDD`，作为银行条线扩展，不纳入默认核心召回。 |

## Alias Map

| 词桶 | 常见写法 | Must-bind 规则 | 关键消歧 |
| --- | --- | --- | --- |
| 评分卡桶 | `评分卡/信用评分/A卡/B卡/C卡/scorecard/credit scoring` | 必须绑定 `授信/逾期/违约/WOE/KS` | 排除平衡计分卡、绩效考核 |
| 生命周期桶 | `贷前/申请/准入`、`贷中/行为评分/账户管理`、`贷后/催收/回收/collections` | 最好绑定具体动作或指标 | 排除空泛“全流程” |
| 风险结果桶 | `逾期/DPD/M1/M2/M3/违约/坏账/NPL/核销/拒付` | 最好绑定贷款、授信或支付对象 | 排除泛经营指标 |
| 授信决策桶 | `授信/信审/额度/定价/批核/审批` | 最好绑定 `评分卡/逾期/规则引擎` | 排除 B2B 赊销语境 |
| 欺诈桶 | `申请欺诈/ATO/盗号/盗刷/chargeback/fraud ring/device fingerprint` | 最好绑定 `申请/开户/交易/支付` | 排除电诈治理、纯安全 |
| 决策动作桶 | `拦截/放行/转人工/冻结/审批/额度调整` | 必须绑定 `申请/交易/支付/授信` | 排除运维监控动作 |
| 银行扩展桶 | `PD/LGD/EAD/IRB/模型验证/AML/KYC/CDD` | 仅在银行/持牌机构语境强化 | 不默认拉高全域召回 |

## Positive Signals

| signal_id | 描述 | 常见文本证据 | 置信度 |
| --- | --- | --- | --- |
| FINRISK_PS_001 | 金融对象 + 风险结果 + 决策组件共现 | `授信/额度` + `逾期/违约` + `评分卡/规则引擎` | high |
| FINRISK_PS_002 | 评分卡硬信号 | `A/B/C卡`、`WOE/IV/KS/PSI/OOT`、`Reject Inference` | high |
| FINRISK_PS_003 | 授信强信号 | `授信/准入/定价` + `逾期/坏账/NPL/DPD` | high |
| FINRISK_PS_004 | 申请反欺诈强信号 | `申请欺诈/ATO/设备指纹/团伙/规则引擎` | high |
| FINRISK_PS_005 | 支付交易风控强信号 | `交易风控/实时拦截/拒付/盗刷/chargeback` | high |
| FINRISK_PS_006 | 生命周期明确 | `贷前/贷中/贷后` 后跟具体职责和动作 | high |
| FINRISK_PS_007 | 业务结果闭环 | `通过率/坏账率/NPL/入催率/拒付损失/拦截金额` | medium |
| FINRISK_PS_008 | 工程化能力被金融锚点拉住 | `XGBoost/LightGBM/Spark/Hive/Flink` 且同段有金融强词 | medium |
| FINRISK_PS_009 | 银行扩展强信号 | `PD/LGD/EAD/IRB/模型验证/AML交易监控` | medium |

## Negative/Confusion Signals

| signal_id | 描述 | 为什么容易误召 | 处理建议 |
| --- | --- | --- | --- |
| FINRISK_NEG_001 | 纯 CV / NLP / LLM / RAG | 也是 AI 热词，但缺金融对象和风险口径 | 强降权 |
| FINRISK_NEG_002 | 推荐 / 广告 / CTR / CVR / 增长 | 也有模型、策略，但目标函数完全不同 | 强降权 |
| FINRISK_NEG_003 | 优惠券 / 拉新 / 薅羊毛 / 秒杀防刷 | 属于营销反作弊，不是默认金融主域 | 强降权 |
| FINRISK_NEG_004 | 内容审核 / 社区治理 | 经常写风控或安全，但不是授信/欺诈 | 强降权 |
| FINRISK_NEG_005 | WAF / DDoS / 渗透 / 漏洞 | 是传统安全攻防，不是金融业务风控 | 强降权 |
| FINRISK_NEG_006 | VaR / 衍生品 / HFT / Alpha | 更像市场风险或量化交易，默认非本主域 | 中到强降权 |
| FINRISK_NEG_007 | BI / SQL / 支持分析 only | 有分析，但无模型、规则或指标闭环 | 中降权 |
| FINRISK_NEG_008 | 通用平台 / MLOps only | 有工程能力，但缺金融业务口径 | 中降权 |

## Seed Branch Suggestions

### Branch 1: 信用评分卡与授信建模

- 适用场景：互金、消金、银行卡中心、信贷模型岗位
- Query Terms：`评分卡`、`WOE`、`IV`、`授信`
- Must-have 绑定：至少命中 `逾期/DPD/KS/坏账`
- 主要误召风险：平衡计分卡、通用二分类项目
- Do NOT Union：`推荐`、`广告`、`LLM`

### Branch 2: 贷前反欺诈与申请欺诈

- 适用场景：线上贷款、开户、授信申请、信审反欺诈
- Query Terms：`反欺诈`、`申请欺诈`、`设备指纹`、`团伙`
- Must-have 绑定：至少命中 `申请/开户/信审/规则引擎`
- 主要误召风险：营销反作弊、内容风控
- Do NOT Union：`优惠券`、`活动`、`拉新`

### Branch 3: 支付交易风控与拒付/盗刷

- 适用场景：支付机构、钱包、卡组织、交易风控
- Query Terms：`交易风控`、`实时拦截`、`拒付`、`规则引擎`
- Must-have 绑定：至少命中 `支付/转账/卡/wallet`
- 主要误召风险：电商订单风控、运维实时监控
- Do NOT Union：`促销`、`秒杀`、`增长`

### Branch 4: 风控策略与决策引擎

- 适用场景：策略专家、规则平台、决策流岗位
- Query Terms：`风控策略`、`决策引擎`、`阈值`、`策略迭代`
- Must-have 绑定：至少命中 `授信/逾期/欺诈/交易拦截`
- 主要误召风险：增长策略、内容安全策略
- Do NOT Union：`投放`、`ROI`、`内容审核`

### Branch 5: 模型监控与验证

- 适用场景：模型治理、银行验证、风控监控岗位
- Query Terms：`PSI`、`OOT`、`模型监控`、`稳定性`
- Must-have 绑定：至少命中 `评分卡/KS/违约/逾期`
- 主要误召风险：通用 MLOps、AIOps、运维监控
- Do NOT Union：`Prometheus`、`SRE`、`AIOps`

## Rerank Cues

### Must-have

- 同句或同段出现 `业务对象 + 风险标签 + 决策组件`
- `WOE/IV/KS/PSI/OOT/A/B/C卡/Reject Inference`
- `申请欺诈/ATO/设备指纹/团伙/chargeback`
- `授信/额度/信审/定价` 与 `逾期/坏账/NPL/DPD`

### Preferred

- `规则引擎` 与 `模型并行/规则+模型融合`
- `贷前/贷中/贷后` 各阶段职责明确
- `通过率/坏账率/NPL/拦截金额/拒付损失`
- `XGBoost/LightGBM/Spark/Hive/Flink` 已被金融锚点拉住
- 银行条线的 `PD/LGD/EAD/IRB/AML/KYC`

### Risk

- 只有通用 ML / 特征工程，没有金融对象与风险口径
- Title 很大但经历文本里没有动作、指标和业务对象
- 只写“全生命周期/MLOps/平台建设/持续优化”这类空话

### Confusion

- `增长/投放/活动反作弊`
- `内容安全/审核`
- `推荐/广告/CTR/CVR`
- `网络安全/攻防`
- `VaR/衍生品/量化交易`

## Open Questions

- `A/B/C卡` 在不同机构里写法差异很大，alias map 是否还需要更多行业俗称
- 银行条线的 `PD/LGD/EAD/IRB` 是否应拆成单独扩展 vertical，而不是放在本主稿里做加分项
- `市场风险/量化工程` 是否另建一份独立 business vertical，而不是继续借用 `finance_risk_control_ai`
- `AutoML/Feature Store/MLOps` 对某些大中台岗位可能是核心，但在默认金融风控主域里应保持次级权重
- 对“小公司 title 虚高”的候选，后续是否要在 rerank 中显式降低 `title` 权重、提高“动作 + 指标 + 对象”权重
