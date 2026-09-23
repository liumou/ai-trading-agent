# Phase 3 模型面决策：B（切换托管 JEV）vs C（放弃 6 问判定）

## 0. 决策背景（证据速览）
- 未微调 laya：全口径 100% ESCALATE，方向命中≈基率（findings §6.3）。
- head-only 微调（A，已闭环）：val_acc 0.34→0.66 学会合成规则，但持出集无方向技能
  （entry_pass 三周期 p≥0.51、命中集合与 base 逐根一致 p=1.0、阈值 0.6 仍 100% ESCALATE；§6.4）。
- Phase 0/1 已把证据面补齐（Decision Context V2 子集、512 token 内不截断）→ 输入面不再阻塞。
- **剩余解锁点只剩"模型本身"**：B=换已校准托管模型；C=不做机读 6 问判定（维持 LLM 现状）。

## 1. 选项 B：切换托管 JEV（TypeSafe System One）
### 机制事实（QuantDinger 同源实现：backend_api_python/app/services/ai_decision_filter.py）
- 调用：`POST {JEV_BASE_URL}/systemone`（默认 `https://api.typesafe.ai/v1`，Header `Bearer JEV_API_KEY`），
  payload `{"model", "state", "questions"}`，timeout 默认 8s（上限 30s）。
- 契约：`JEV_QUESTIONS` 与本项目 `LAYA_GATE_OPTIONS` **6 问同名同选项** → state/问题定义可原样复用。
- 返回：每问 typed Choice + 全概率 + 置信度（无需文本解析）。
- 收敛（确定性）：entry/risk/execution 三问置信度须 ≥ min_confidence（默认 0.55，JEV 自有概率刻度）
  ——低于则抛错 → 降级 LLM → 再失败则 **fail-open 放行并审计**；最终
  `allowed = entry==pass ∧ risk≠block ∧ exec≠block ∧ ¬(sa=conflict ∧ regime=adverse 双高置信)`；
  entry/risk/exec 为强制问、dq 仅审计——与本项目收敛器结构一致（本项目多 ESCALATE fail-closed 分支）。
- 退出操作永远绕过 AI；每决策按服务端 feature cost 计费（quantdinger 走 credits，具体价格须查
  TypeSafe 定价 / 机构合同）。

### 本项目工作量（估 2-4 人日）
1. JEV 客户端模块（~120-180 行 + 单测）：httpx `POST /v1/systemone`；settings 新增
   `jev_api_key / jev_base_url / jev_model / jev_timeout_s / jev_min_confidence`。
2. 接入现有 gate 接口：state 优先用 `build_laya_engine_snapshot` 的 JSON 形态（贴近 QuantDinger 协议）；
   可选回放对比 JSON vs prose（c5）两形态。
3. 降级策略二选一：(a) 保本项目 fail-closed（低置信 → ESCALATE 交 LLM）；(b) 学 QuantDinger fail-open
   ——当前影子阶段选 (a)，enforce 阶段再评估。
4. 影子观察 + 240 样本回放验证（c5 口径、seed 42 配对）：JEV vs laya(base/FT) 的
   entry_pass 命中率、判定分布、ECE、延迟中位数。
5. 配置项 + 文档 + 回归测试（现有 168 laya 用例）。

### 依赖与风险
- 凭据：需 `JEV_API_KEY`（TypeSafe 账号）——**未知项，须用户确认**。
- 网络：Railway CPU 环境外网可达性未保证（本项目曾为 HF 设镜像端点）→ 须先 1-2h 连通性冒烟。
- 延迟：+8s/次判定预算，高于本地 laya ~2s；影子/非关键路径可接受，enforce 建议异步。
- 成本：按决策计费，须核实（信号频率 × 单价）。
- 数据出域：state 含持仓/敞口/策略参数（无明文密钥）→ 须做供应商数据合规确认。

### B 验收标准（同 3A.3 口径）
连通冒烟 OK + 240 样本 entry_pass 显著 > 基率（p<0.05）**或**出现可用判定（非 100% ESCALATE）
+ ECE ≤ 0.07；延迟中位数 ≤ 8s。

## 2. 选项 C：放弃 6 问判定（保留情绪/策略通道）
### 事实
- 生产现状即 de-facto C：gate 仅 shadow（`laya_gate_shadow/enforce` 默认关），实测全口径 100%
  ESCALATE = 交回 LLM → **gate 当前对交易决定零影响**。
- sentiment/strategy 两轻任务通道独立于 gate（news_sentiment.py 预筛 + 策略情绪），有真实使用价值。
- 确定性链（预算/风控/信号方向）已存在并生效。

### 工作量（估 0.5-1 人日）
1. 默认停用 gate 影子观察（`laya_gate_engine_shadow` 置关 + engine/manual 两处调用点配置短路，
   不删代码、保留回滚）。
2. 停止写 gate 观察行（保留表结构，报表按版本分流）。
3. sentiment/strategy 接口与新闻预筛保持不变。
4. 文档标记"6 问判定中止原因 = 实测无技能 + 微调未达标"；回归测试（168 laya 用例）。

### 风险 / 收益
- 收益：省 gate 路径每样本 ~2-3s CPU；可不加载 395M 模型（省 ~1.6GB RAM）；观察表停止堆积噪音。
- 代价：放弃机读结构化 6 问判定（契约保留，未来 B 条件具备可重启）。
- 风险：低；对交易行为零变化（shadow 停用），符合 H-3 非干扰不变量。

## 3. 决策矩阵与建议
| 维度 | B（托管 JEV） | C（放弃 6 问） |
|---|---|---|
| 解锁方向技能可能 | 唯一路径（已校准托管模型） | 无（维持 LLM 现状） |
| 工作量 | 中（2-4 人日） | 低（0.5-1 人日） |
| 外部依赖 | 凭据 + 外网 + 计费 | 无 |
| 风险 | 中（网络/成本/数据出域） | 低 |
| H-3 兼容 | shadow 先行 ✅ | ✅ |

**建议**：先做 5-10 分钟决策检查（是否已有 `JEV_API_KEY`、能否连通 `api.typesafe.ai`）；
凭据 + 网络可用 → 走 B 验证（连通冒烟 → 240 样本回放，评估后再定 shadow/enforce）；
不可用 → 落地 C 止损，B 列为"条件具备后的重启项"。

## 4. 需用户拍板
1. 是否有 TypeSafe JEV 凭据/账号（决定 B 可行性）；
2. 若批准 B：确认外网调用实验范围（少量 systemone 请求）；
3. 若批准 C：确认停用 shadow 观察与文档标记范围。
