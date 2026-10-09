# 交易纪律风控系统增强 · 优化计划（v3，两轮八路评审修订版）

> 依据：《黄金期货交易计划（纪律风控版）》（2026-10-09）
> 范围：将计划中针对「频繁开仓、多空频繁切换、多次爆仓、总想交易」四类行为的纪律规则，落地为 AI 交易系统的功能增强。
> 版本：v3 = v2.1 + 第二轮四路评审修订（评审 5 时区修复专项 / 评审 6 修订落实复核 / 评审 7 时区×纪律门禁交互 / 评审 8 整体可实施性终审）。两轮共 8 路评审意见见第八节。
> 状态：**待批准** —— 本计划需用户同意后方可实施。

---

## 一、总体思路

**原则：系统替你守住纪律红线，而不是靠自觉。** 交易计划是"写下来"的承诺，系统要做的是把每条铁律变成**不可绕过的硬闸门**（fail-closed），同时提供统计与提示帮助养成纪律习惯。

设计遵循七点（v3 修订）：

1. **复用现有硬闸门**：所有新拦截挂进三通道（manual / strategy / ai_agent）共用的 `preflight_order()` 硬闸门，新增集中式 **"交易纪律门禁"（discipline gate）** 步骤。术语约定：本文"纪律门禁"指 preflight 内的检查步骤，"纪律计数"指其依赖的 Redis 状态，"纪律参数"指可配置阈值。
2. **纪律门禁对三通道一律 fail-closed**：包括引擎通道。评审发现 `engine.py:1058-1083` 对 preflight 异常是 try/except 吞掉后继续下单（fail-open）——纪律是安全线，引擎不得例外；Redis 故障/门禁异常时拒单并记 `kind="discipline_redis_down"`，而非静默放行。
3. **可配置优先且单轨**：所有纪律参数走 **Redis 运行时配置**（对齐 rollout_mode 先例，Redis 为真相、env 为默认值），前端可编辑且立即生效；`discipline_gate_enabled` / `engine_discipline_enabled` 作为总开关（env 默认 + Redis 可改），关闭时记 `kind="discipline_disabled"` 放行（评审 8：风控系统必须有一键回滚手段）。
4. **服务器端强制 + 前端提示**：硬拦截在服务端强制执行；前端展示"被拦原因、解禁时间、今日次数"。
5. **计数可靠、原子、可恢复**：纪律计数用 Redis 原子操作（INCR/Lua）防并发竞态；重启后从 **MT5 history（主源，全通道齐全）/ OrderAudit / Trade 幂等回填**（评审 8：回填主源必须钉死为 bridge 侧 MT5 history，因 AI 通道不写 OrderAudit/Trade）。
6. **纪律时区统一**：纪律计数（次数/熔断/冷却/休息日）的日/周/月边界按 `discipline_timezone`（默认 **Asia/Shanghai**）本地时刻定义，所有纪律时间读取收敛为单一函数 `discipline_now()`；市场开闭市（sessions.py per-asset）与 bridge/落库/展示（UTC 链）保持不变、与纪律计数解耦（评审 7 裁决，详见 3.0 与第九节）。
7. **部署同窗**：时区修复 F1（bridge 输出 UTC）+ F2（后端收敛）+ F3（前端解析）必须同一维护窗口部署，顺序：先后端 F2 → 再前端 F3 → 最后 bridge F1（评审 5/8：新 bridge + 旧后端会因 aware/naive 比较 TypeError 崩溃）。

---

## 二、现状能力盘点

### 已有能力（可直接复用）
| 能力 | 位置 | 说明 |
|------|------|------|
| 手动交易防火墙 | `manual_order_gate.py` | 硬闸门 + JEV/SystemOne 审查 + fail-closed + CAUTION 二次确认 |
| 连亏熔断 | guardrails `consecutive_loss_halt`（.env=3） | 连亏 ≥3 拒单，已有（但仅日粒度） |
| 日亏熔断 3% | CircuitBreaker（单品种+账户级）+ guardrails | 已有（双口径需统一） |
| 日内 equity 回撤 3% | preflight 6b | 已有 |
| 每小时 ≤5 笔 + 120s 间隔 | guardrails（硬编码） | 已有但不可配置 |
| 情绪规则 | systemone（无止损拒/martingale 拦/连亏 warn） | 已有（仅手动通道） |
| SL/TP 防漂移 | constants.py（锚点 5×、每日拉宽 3 次） | 已有 |
| 历史数据 | OrderAudit.review / Trade.profit / BotEvent | 可统计频率、胜率、连续亏损 |
| 权益/持仓/保证金 | MT5 bridge `GET /account`（balance/equity/margin/**free_margin**）`GET /positions` | 可用；**无 leverage 字段需补** |

### 缺失能力（本次要补的，v3 优先级）
| 缺口 | 对治的行为问题 | 优先级 |
|------|--------------|--------|
| **时区统一（bridge 输出 UTC + 后端收敛 + 前端解析 + 存量回填）** | 全系统历史/统计/熔断边界错位 | **M1 前置** |
| **纪律时区口径 + discipline_now() 收敛 + 运维开关** | 全部计数功能的地基 | **M1 前置** |
| 周/月亏损熔断阶梯（含跨日连亏 streak） | 多次爆仓 | P0 |
| 熔断后**当日/本周/当月停手**（rest-of-period，存 until 绝对时刻） | 多次爆仓 | P0 |
| 同品种反手冷静期 + **当日第 2 次反手硬拒** + 确认信号必填 | 多空频繁切换 | P0 |
| 保证金/杠杆硬上限（bridge 补 leverage + free_margin 直用） | 多次爆仓 | P0 |
| 引擎通道纪律 fail-closed + 引擎豁免策略（开关化） | 多次爆仓 | P0 |
| 日/周开仓次数上限（**按通道分池**，用 record_order_opened 计数） | 频繁开仓 | P1 |
| 纪律参数 Redis 运行时配置 + 前端 settings 区块 | 频繁开仓 | P1 |
| 周末不留仓 / 日熔断后收盘前了结持仓 | 多次爆仓 | P1 |
| 逆势 CAUTION（人在环）+ 限次人工豁免通道 | 多空频繁切换 | P2 |
| 强制休息日 / 24h 冲动冷却（阶梯升级，无文字绕过） | 总想交易 | P2 |
| 复盘统计页面（新建只读聚合服务）+ AI 通道 DB 落库 | 总想交易 | P3 |
| 防拆单：日/周累计名义额上限（Σ手数×合约乘数） | 频繁开仓 | P3 |

---

## 三、功能增强方案（v3 修订版）

### 3.0 时区基础修复（M1 前置项 —— 用户报告"历史订单时区不对"）

**问题根因（专项调查结论，见文末附录）**：三层叠加
1. **MT5 bridge 输出 naive 时间**：MT5 Python API 返回的 `.time` 字段是**绝对 Unix epoch 秒**（官方示例 `pd.to_datetime(unit='s')`），但 `mt5_bridge/main.py:854/459` 用 `datetime.fromtimestamp(x).isoformat()` 在 **bridge 宿主机 OS 本地时区**（约 EET）解释，输出无时区标记的 naive 时间；
2. **后端把 EET naive 当 UTC 存**，与 bot 自产 UTC naive 混在同一 `trades.open_time/close_time` 列（`engine.py:1168` UTC vs `:1437/2128/2186` EET）——同一张历史表两种时区；
3. **前端 `new Date(naive)` 当浏览器本地时区解析**再转 Asia/Bangkok（`history/page.tsx:198` 等）——三层各按自己的猜测解释，历史订单时间必然"不对"。

**为什么是纪律计划的前置**：日开仓次数、周/月熔断、强制休息日(周五)的"日/周/月"边界全部依赖时间口径；时区不统一则熔断边界和次数计数错位。**纪律计数的日/周/月边界按 `discipline_timezone`（默认 Asia/Shanghai）定义**（评审 7，见第九节），存储/传输仍走 UTC——两者互不干扰。

**修复方案（F1-F5，必须同窗部署）**：

- **F1【核心】bridge 统一输出 UTC**：`mt5_bridge/main.py` 全部 8 处时间输出点（`:169/170` 挂单 time_setup/time_expiration、`:289` tick、`:367` ohlcv、`:459` positions open_time、`:814` ohlcv history、`:854` deals time）`datetime.fromtimestamp(x).isoformat()` → `datetime.fromtimestamp(x, tz=timezone.utc).isoformat()`；`:831-832` `datetime.now()` → `datetime.now(timezone.utc)`。**同时决策输入侧**（`:714/756/760` 挂单过期时间、`:803-804` OHLCV 窗口——naive datetime 喂给 MT5 会被按宿主机时区解释，需一并改 aware 或明确语义）。
- **F2 后端统一落库 naive UTC（F1 的前置，必须先于 F1 上线）**：全部 9 处消费点收敛——`engine.py:1437/2128/2186`（平仓/孤儿/幻影）+ **`:2051`（挂单恢复 open_time，F1 后 aware 入库会被 asyncpg 拒绝导致挂单恢复失败）** + `history.py:147-169/225/305`（合并/daily-pnl/performance，F1 后 aware vs naive cutoff 比较抛 TypeError 被吞，表现为**手动成交/日盈亏/表现统计静默丢失**）+ `analytics.py:98`（交易分析）。统一规则：收到带 `+00:00` 串 → `astimezone(UTC).replace(tzinfo=None)`；收到旧 naive 串 → 按 `MT5_SERVER_TZ` 配置常量（`zoneinfo.ZoneInfo("Europe/Athens")` 可正确处理 DST）转换后落库。
- **F3 前端统一解析（晚于 F1 部署）**：复用 `chat-utils.ts:13-17` 的 `parseChatDate`（naive 补 Z 视作 UTC，幂等兼容带偏移串）+ `timeZone` 显示（纪律状态端点以 `discipline_timezone` 渲染）。覆盖清单补全：`history/page.tsx:198/275/278`、`manual-reviews/page.tsx:171`（现无 timeZone）、`PriceChart.tsx:174/185/210`、`insights/page.tsx:87/150`、`activity/page.tsx:62-71`、`ai-usage/page.tsx:119-120`、`EventFeed.tsx:21-27`、`format.ts`，**另补 `NewsCard.tsx:26`、`notification-center.tsx:42`、`connection-status.tsx:49`、`ReviewHistoryDialog.tsx:94/121`、`macro/page.tsx:170`**（相对时间计算，评审 5 指出）。
- **F4 风控日边界统一（按评审 7 定案）**：纪律计数（日开仓次数/日亏/连亏 streak 日段/每小时）统一走 `discipline_timezone` 日界（默认上海 00:00 = UTC 前日 16:00），**替代** per-asset 22:00 口径；`sessions.py` per-asset 重置表（22:00/21:00/0:00）**保留**，只承担市场开闭市职责，与纪律计数解耦（否则账户级日亏在 16:00-22:00 UTC 窗口混合两个"日"，熔断先后判定错位）。
- **F5 文档化约定**：CLAUDE.md 补"bridge 时间语义 = 绝对 epoch 秒被宿主机时区解释；后端所有 bridge 时间须转 UTC naive 落库；前端统一补 Z 当 UTC；纪律边界按 discipline_timezone"。
- **F6【v3 新增】存量历史数据一次性回填**：`trades` 表中已混存的 EET-shifted naive 行（`engine.py:1437/2128/2186` 历史写入）回填为正确 naive UTC——按 `MT5_SERVER_TZ` zoneinfo 反推（按每笔时间戳/DST 段 ±2/+3h），或标注"迁移前数据偏移 2-3h"由前端显示补偿。**必须做，否则用户核心投诉"历史订单时区不对"只修了新数据**（评审 5）。

**部署顺序（同一维护窗口）**：先发布后端 F2（双格式解析，兼容旧 bridge）→ 再前端 F3 → 最后 bridge F1（与 1b 的 leverage 一行合并一次部署）。回滚：后端失败只回滚后端（bridge 未动）；bridge 失败只回滚 bridge（后端 F2 兼容旧 naive）。**F3 绝不先于 bridge**（新前端 + 旧 bridge：EET 被当 UTC 显示差 2-3h，变差）。8001 端口不暴露公网/仅 SSH 隧道作为部署检查项。

**验收（v3 可测版）**：bridge 测试断言 8 处时间输出带 `+00:00`；后端落库均 naive UTC（与 bot 自产一致）；**旧 bridge naive 串 + 新后端 F2 的兼容单测**（按 MT5_SERVER_TZ 转换正确）；**Redis down 时纪律门禁拒单记 `discipline_redis_down`**；前端历史页/审查页时间与本地预期一致（差 0 而非 5-8 小时）；存量回填后历史行时间正确。

---

### 3.1 多次爆仓 → 亏损熔断阶梯 + 仓位硬上限（P0，防线）

**交易计划要求**：单笔止损≤1~2%本金、单日≤3~4%、单周≤6~8%、单月≤10~12%、连亏3笔日熔断/5笔周熔断、单笔保证金≤20%可用资金、总持仓保证金≤50%总资金、周末不留仓。

#### 功能 1a：周/月亏损熔断（核心，v3 修订）
- `CircuitBreaker` 增加周/月 PnL 记账（复用 `record_trade_result` 路径，**additive 扩展**，不改现有日 key 结构与 `is_triggered` 签名）。Redis key **带周期号**，**周期号由 `discipline_now().isocalendar()` 生成**（评审 7：iso_year 非 date.year——2025-12-29 属 2026-W01；key 格式 `circuit:weekly:{iso_year}-W{ww:02d}:{account}:daily_pnl:{symbol}`、`circuit:monthly:{YYYY-MM}:{account}:...`，月份用 discipline_timezone 本地月份）。
- **闸门判定账户级聚合**：周/月 key 虽按 symbol 分桶，但判定时仿 `get_global_daily_pnl` 对 `circuit:weekly:{周期}:*` 前缀全部 symbol 求和（评审 7：分散亏损旁路在周/月维度同样存在）。
- `preflight_order()` 新步骤「纪律门禁」检查：周亏 ≥ `discipline_weekly_loss_limit`（6~7%）→ 拒单，**本周剩余禁开**；月亏 ≥ `discipline_monthly_loss_limit`（12%）→ 当月剩余禁开。判定顺序固定：**月 > 周 > 日 > 连亏**，同次触发取最高档。
- **连亏周熔断（v3 修订为序列语义）**：跨日 streak = **按平仓时间排序的连续 N 笔亏损交易**（跨日/跨周末不中断，中间出现盈利单即归零），非"连续 N 天亏损"。现 `guardrails:trade_results:{date}` 日 key + 2 天 TTL 不可用 → 改跨日持久化 `guardrails:loss_streak:{account}`（值 + 最近 close_time，TTL 随写刷新 7 天），平仓时**原子更新**（win→0、loss→+1，Lua 脚本防并发），回填按 close_time 排序重放（ticket 幂等沿用）。streak ≥5 触发周停（停触发时刻所在纪律周剩余）。
- 拒单统一在 **preflight 内写 BotEvent(TRADE_BLOCKED)** + 补 `account_login` 字段（engine.py:1974 / manual_order_gate.py:919 现均不落值）。

#### 功能 1b：保证金/杠杆硬上限
- **优先修 bridge**：`mt5_bridge/main.py` `GET /account` 补 `"leverage": info.leverage`（MT5 API 自带），systemone 的 margin 检查（`systemone.py:430-447`）与 typesafe_jev 自动复活。
- **硬拦直接用 `free_margin`**（bridge 已返回，main.py:390）；本地估算（手数×contract_size×价格÷杠杆）仅作 bridge 旧版本兜底。
- **分母口径对齐（评审 8：参数表与正文须一致）**：统一为 **equity 占比**（与现 systemone 0.25/0.50 阈值同分母），20% 上限 = equity×20% 硬拦。
- **支持期货固定保证金模式**（AU 主力是交易所定保证金/手）：symbol_configs 加 `margin_mode`（leverage / fixed_per_lot）。
- **主闸与兜底分层**：主闸是单笔风险上限 `exposure_cap`（block 收紧到 2%），保证金 20% 只是兜底闸。

#### 功能 1c：熔断后停手语义（rest-of-period，v3 定案）
- 评审 2 R1：60 分钟自动恢复击穿"单日≤3~4%"铁律。v3：日熔断 → **当日剩余停开新仓**；周熔断 → 本周剩余；月熔断 → 当月剩余。60 分钟自动恢复只保留给瞬态闸（点差/频率/防抖）。
- **"当日/本周/当月"= discipline_timezone 自然日/周/月**，存 **until 绝对时刻**（日=本地下一 00:00、周=本地下周一 00:00、月=本地下月 1 日 00:00，zoneinfo 本地算术，禁止手工小时偏移），到期判定 `discipline_now() < until`（评审 7：北京 21:00 触发按 22:00 UTC 口径只停 1 小时，按上海口径停 15 小时——必须绑定纪律日界）。
- 可选增强（P1）：日/周熔断触发后对既有持仓排队**收盘前了结**（复用 `position_close.py`，写 BotEvent）。

#### 功能 1d：周末/隔夜敞口
- 周五收盘前强平（或至少不新开+强制预警）、周末持仓告警；可选 `max_position_duration_hours` 兜底（现为 0 禁用）。"周五"= discipline_timezone 本地周五（评审 7）。

**验收（v3 可测版）**：
- 用 `discipline_now()` 注入固定上海时刻（如 2026-10-09 12:00 = UTC 周五 04:00），断言日 key=`2026-10-09`、周 key=`2026-W41`；写旧周期 key（`2026-W40:`）→ 不触发。
- 向 `circuit:weekly:2026-W41:{acc}:daily_pnl:GOLD=-800` + 另一品种各亏 4% → **账户级聚合周熔断触发**（`ok_connector`/`ok_guardrails` fixture 直调 `preflight_order()`，断言 `ok=False, kind='guardrail'`、reason 含"本周熔断"）。
- ISO 年界用例：2025-12-29 → 断言 key 为 `2026-W01` 而非 `2025-W53`（iso_year 陷阱）。
- streak：`trade_results:2026-10-09` 写 [0,0] + `:2026-10-12` 写 [0] → streak=3（周五→周一连续）；中间盈利单 → 归零。
- 手动 Gate 层 REJECTED + TRADE_BLOCKED；引擎通道 preflight 拒 → TRADE_BLOCKED（**验证引擎 fail-closed**）。
- **Redis down**：mock Redis 抛错 → 拒单记 `kind='discipline_redis_down'`。
- 边界：恰好 6%/7%/12% 的 `>=`/`>` 参数化；并发 `asyncio.gather` 无超限穿透。
- 1c：触发于上海 21:00 → until=本地次日 00:00（断言停手时长，非 60 分钟）。

---

### 3.2 频繁开仓 → 次数上限 + 可配置 + 无信号空仓（P1）

**交易计划要求**：每日≤3 笔、每周≤10 笔；无信号即空仓；禁止报复性开仓/追涨杀跌。

#### 功能 2a：每日/每周开仓次数上限
- **修正（评审 1/2 R2）**：`CircuitBreaker.trade_count` 计的是**已平仓笔数**，不可用于开仓上限。**改用 `record_order_opened`**（三通道唯一共同开仓触点，guardrails.py:376-384）扩展日/周开仓计数器，Redis INCR 原子自增 + 事后超限拒绝。
- **按通道分池（评审 2 R5）**：`discipline_max_trades_per_day` 拆成 manual（日 3）与 engine/ai（各自独立 3~5）；跨通道统一只保留账户级闸门（日亏/连亏/熔断）。
- **双计数口径**：硬闸门按**实际成交开仓**计；**冲动检测按用户提交尝试（含被拒）**计——被拒单是"手痒"最强信号，计入 24h 冷却触发条件。
- 挂单口径：按"placed 计尝试、成交计开仓"（现 `record_order_opened` 对未成交挂单也计数，manual_order_gate.py:542，需明确抵扣）。
- **日/周界按 discipline_timezone**（上海自然日/周一 00:00 起）。

#### 功能 2b：参数去硬编码 + 单轨运行时配置（v3：前移到 M1）
- 每小时 ≤5 → `guardrails_max_trades_per_hour`；最小间隔 120s → `guardrails_min_interval_seconds`；guardrails 日亏 3% 改用 `settings.max_daily_loss`。
- **单轨**：纪律参数统一走 **Redis 运行时配置**（Redis 为真相、env 为默认值），三通道 + 前端 + 多 worker 读同一 Redis；前端 settings 页新增"交易纪律"区块可编辑且**立即生效**（评审 8：该通道前移到 M1 作基础设施，P0 的 1a/3a 就要消费 `discipline_*` 阈值，否则 P0 先 env 后 Redis 返工）。
- 注意：`test_guardrails.py` 头部 **import 了 `MAX_TRADES_PER_HOUR` 模块常量**（第 9-16 行），去硬编码后同步更新。

**验收（v3 可测版）**：参数化"第 3 笔放行/第 4 笔拒"边界；间隔未到拒 + **间隔已过放行**（写过去时间戳）；并发双提交无穿透；`del key` 归零后放行；引擎独立池（引擎开 2 笔不影响 manual 3 笔）；**settings 编辑 → 多 worker 立即一致**（Redis 通道，两个 worker 进程读同一配置）。

---

### 3.3 多空频繁切换 → 方向纪律门禁（P0，直接对治）

**交易计划要求**：大周期定方向（日/周线）；一日一个方向；切换需 ≥2 项确认信号 + 距上次开仓 ≥30 分钟冷静期 + 当日未熔断；禁止逆势重仓。

#### 功能 3a：同品种反手冷静期（核心）
- 纪律门禁检查：同一品种存在持仓或当日已成交 BUY 时，反向 SELL 开仓被拦，除非距上次开仓 ≥ `discipline_flip_cooldown_minutes`（30 分钟）。**冷却用绝对时间戳（epoch 秒），与日界正交**：冷却期内跨日界不解除、日次数照常重置（评审 7 状态机：23:50 触发 24h，次日 00:00 次数归零但冷却仍拦）。
- Redis `discipline:flip:{account}:{symbol}`（方向 + 时间戳），**所有通道开仓写同一 key**（引擎方向不得成为盲区）。
- **防乒乓（评审 2 R4）**：**当日同品种第 2 次反手 → 硬拒 REJECTED**；第 1 次反手 → CAUTION + **强制勾选"≥2 项确认信号"结构化清单**（必填，不填提交不出去）。
- 冷却期满后叠加次数上限（3a 与 2a 联动）。

#### 功能 3b：一日一个方向（边界声明）
- 当日首次反手后进入 CAUTION；第 2 次反手硬拒（见 3a）。**只对手动通道生效**（systemone 是 manual 审查链，manual_order_gate.py:279-285），不覆盖 AI/策略通道。

#### 功能 3c：大周期方向一致性（P2，修正前提 + 豁免通道）
- **修正（评审 1）**：`_signal_alignment`（systemone.py:547-581）在 `strong_against>=2` 时**当前已是 REJECTED**——只需补弱场景（单 TF 强逆势、弱 ADX 冲突）。
- **修订（评审 2 R3）**：D1/W1 明确反向**保持 CAUTION（人在环）**；REJECTED 升级需三重条件：① D1 与 W1 一致反向 ② ADX 强度达标 ③ D1 非成形 K 线（当日未收盘不采信）。
- **限次人工豁免通道**：每周≤2 次、强制勾选确认信号清单、豁免记入纪律评分。

**验收（v3 可测版）**：`discipline:flip:{acc}:GOLD`=now → SELL 拒（reason 含"多空切换/冷静期"）；=now-31min → 放行；恰 30 分钟整点边界；不同品种不互斥；BUY→BUY 不拦；当日第 2 次反手 → REJECTED；方向规则只验手动通道；**冷却跨日正交用例**（触发于上海 23:50，mock now=次日 00:10 → 冷却仍拦且 trades_today=0）；`signal_alignment` 升级前先跑 `test_systemone.py` 确认破坏面。

---

### 3.4 总想交易（手痒）→ 冷静期 + 休息日 + 复盘统计（P2/P3）

**交易计划要求**：冲动识别自查；24 小时冷静期；冲动替代动作；每周 ≥1 日不交易；复盘替代下单。

#### 功能 4a：强制休息日 + 熔断停手（v3 时区定案）
- 月熔断 → 当月剩余禁手动开仓；周熔断 → 本周剩余；连亏 streak ≥5 → 本周剩余（均配合 1a/1c）。
- 强制休息日 `discipline_mandatory_rest_days`（默认 `[4]`=周五）：**按 `discipline_now().weekday()` 判断**（本地周五=北京周五 00:00-24:00，非 UTC 周几——"UTC 周五"会漏拦北京 0-8 点、误拦周六 0-8 点，评审 7）。
- **覆盖改单/撤单重下等变相入口**（周五改 SL 再撤单重下能绕过）。

#### 功能 4b：24h 冲动冷却（无文字绕过，阶梯升级）
- 触发条件：纪律门禁拦截（冲动/频率/方向）后 1 小时内同品种再次尝试开仓 → 24h 冷却（含被拒单计尝试）。
- **无文字理由绕过**（评审 2 5b：与 fail-closed 冲突）；若保留业务妥协通道则**限量 + 计分 + 强制 CAUTION**（每周≤2、消耗豁免额度、拉低纪律评分、豁免单不可 APPROVED）且强制留审计（BotEvent + OrderAudit 行）。
- **阶梯升级**：第 2 次 24h → 第 3 次 72h → 第 4 次本周禁该品种。冷却用绝对时间戳（epoch 秒）**与日界正交**。

#### 功能 4c：复盘统计（P3，新建只读聚合服务）
- **修正（评审 1）**：不是"激活 TradeAccountability 死代码"（内存分类器 max 500、重启即失、无聚合能力）。
- **新建只读聚合服务**：读 OrderAudit(source)/Trade/BotEvent 统计：开仓次数、方向切换次数、违规（TRADE_BLOCKED）、胜率、最大回撤、纪律评分。
- **前置缺口先修**：① BotEvent.account_login 现恒为空；② AI 通道拒单不写 TRADE_BLOCKED、AI 单不入 OrderAudit/Trade（broker.py:142-188）——**broker.py AI 通道 DB 落库前移到 M3 之前**（评审 8：P0 回填完整性长期依赖它）。
- **纪律评分不写 `OrderAudit.review`**（`_verdict_of` 会污染审查链，manual_order_gate.py:60-82）；独立字段/表或纯前端聚合。
- 前端 `/discipline` 页（或并入 `/manual-reviews`），与交易计划"月度统计表"逐项对应。

**验收（v3 可测版）**：休息日=discipline_timezone 本地周几 + clock 注入（上海周五 00:00/23:59 两态）；24h 冷却阶梯（第 2/3/4 次升级）参数化；统计端点字段断言 + 页面目检；豁免通道审计留痕；**休息日 bypass 测试**（改单/撤单重下仍被拦）。

---

## 四、优先级总览、实施里程碑（v3 修订）

| 优先级 | 功能 | 对治问题 | 改动面 | 工作量 |
|--------|------|---------|--------|--------|
| **M1** | 时区 F1-F6 + 纪律时区口径 + 运维开关 + Redis 运行时通道 | 全系统地基 | bridge + engine + history + analytics + 前端 15+ 处 | L |
| **M1** | 存量历史数据一次性回填（F6） | 用户核心投诉 | 回填脚本 | M |
| **P0** | 周/月熔断阶梯 + 跨日 streak（1a，含账户级聚合） | 多次爆仓 | CircuitBreaker(additive) + preflight + BotEvent 出口 | M |
| **P0** | 熔断 rest-of-period 存 until（1c） | 多次爆仓 | preflight + Redis | S |
| **P0** | 反手冷静期 + 第 2 次反手硬拒（3a/3b） | 多空频繁切换 | preflight + Redis + systemone | M |
| **P0** | 保证金/杠杆硬上限（1b） | 多次爆仓 | bridge leverage 一行 + systemone | S |
| **P0** | 引擎通道纪律 fail-closed + 引擎豁免开关（评审 3/8） | 多次爆仓 | engine.py + preflight + 开关 | M |
| **P1** | 日/周开仓次数上限（按通道分池，2a） | 频繁开仓 | guardrails + preflight + Redis | M |
| **P1** | 参数单轨运行时配置 + settings 区块（2b，M1 基础复用） | 频繁开仓 | config + Redis 通道 + settings 页 | M |
| **P1** | 周末不留仓 / 日熔断强平（1d） | 多次爆仓 | preflight + position_close | M |
| **P2** | 逆势 CAUTION + 限次豁免通道（3c） | 方向切换 | systemone | M |
| **P2** | 强制休息日 / 24h 冲动冷却阶梯（4a/4b） | 总想交易 | preflight + Redis | M |
| **P3** | 复盘统计页（4c）+ AI 通道 DB 落库（前移 M3 前） | 总想交易 | 聚合服务 + broker.py + 前端 | L |
| **P3** | 防拆单：日/周累计名义额上限（Σ手数×合约乘数） | 频繁开仓 | guardrails + preflight | M |

### 实施里程碑（评审 8 路线图）

| 里程碑 | 范围 | 依赖 | 验收出口 | 回归风险 | 上线方式 |
|--------|------|------|---------|---------|---------|
| **M1 时区+基础设施** | 3.0 F1-F6（同窗：后端 F2→前端 F3→bridge F1）+ bridge leverage + MT5_SERVER_TZ + discipline_* 参数骨架 + Redis 运行时读路径 + 两个门禁开关 + 存量回填 | 先冻结三项口径决策（见九） | 3.0 验收 + 旧 bridge 兼容单测 + Redis-down 单测 + 开关单测 + 基线 diff | 中（历史落库语义变化） | **内部必须同窗** |
| **M2 P0** | 引擎 fail-closed + 1a/1c + 3a/3b + 1b（含 F4 日界统一） | M1 | 3.1/3.3 验收 + 故障模式补齐 | 高（首次上硬闸门） | 不可独立于 M1 |
| **M3 P1** | 2a/2b + 1d + **broker.py AI 落库（前移）** | M1+M2 | 3.2 验收 + 多 worker 配置一致 | 中 | 可独立 |
| **M4 P2** | 3c 逆势+限次豁免 + 4a/4b 休息日/冷却阶梯 | M2 | 3.3 豁免 + 3.4 验收 + bypass 测试 | 中 | 可独立 |
| **M5 P3** | 4c 聚合服务 + 防拆单 | M1 | 统计端点契约 + 页面目检 | 低 | 可独立 |

---

## 五、改动点清单（v3 补全，实施时逐项核对）

**M1 时区与基础设施**
- `mt5_bridge/main.py`：`GET /account` 补 `leverage` + 8 处时间输出改 UTC 带偏移（F1）+ 输入侧 4 处（:714/756/760/803-804）语义决策
- `backend/app/bot/engine.py`：**修 preflight 异常 fail-open**（:1058-1083）+ `_log_event` 补 account_login（:1974）+ 平仓/孤儿/幻影时间收敛 UTC（F2，:1437/2128/2186）+ **挂单恢复 open_time（:2051）**
- `backend/app/api/routes/history.py`：bridge naive 转 UTC（F2，:147-169 + :225 daily-pnl + :305 performance）
- `backend/app/api/routes/analytics.py`：交易分析时间收敛（F2，:98）
- `backend/app/config.py` + `backend/.env`：`discipline_*` 参数骨架 + `MT5_SERVER_TZ` + `discipline_timezone`（默认 Asia/Shanghai）
- `backend/app/services/discipline.py`（新）：`discipline_now()` 单一时钟 + 周期号生成（isocalendar/YYYY-MM）+ `seconds_until_discipline_day_end()` + until 计算
- `backend/mcp_server/guardrails.py`：日亏口径统一 + 每小时/间隔去硬编码 + Redis 运行时读路径（rollout_mode 先例）
- 运维开关：`discipline_gate_enabled` / `engine_discipline_enabled`（env 默认 + Redis 可改）
- `frontend/lib/format.ts` + 15+ 页面/组件：统一 parseChatDate + 显示时区（F3，含 NewsCard/notification-center/connection-status/ReviewHistoryDialog/macro）
- 存量回填脚本（F6）：trades 表 EET-shifted naive → 正确 naive UTC
- `CLAUDE.md`：时区约定文档化（F5）

**M2-M5 纪律功能**
- `backend/app/risk/circuit_breaker.py`：周/月 PnL 记账（additive）+ 周期号 key + 账户级聚合判定
- `backend/app/services/order_preflight.py`：新增"纪律门禁"步骤（次数/方向/冷却/熔断阶梯）+ preflight 内统一写 BotEvent(TRADE_BLOCKED) + `kind='discipline_redis_down'`
- `backend/app/services/manual_order_gate.py`：`_log_event` 补 account_login（:919）；豁免通道审计
- `backend/app/services/position_close.py`：1c 收盘前了结/1d 周末强平接入（M3）
- `backend/app/db/models.py`：symbol_configs 加 `margin_mode`；纪律评分独立存储（或纯前端聚合）
- `backend/app/services/systemone.py`：`direction_flip` 规则 + 弱逆势升级 + 确认信号清单 + margin 分母统一
- `backend/mcp_server/tools/broker.py`：**AI 通道 DB 落库（OrderAudit/Trade）+ 拒单写 TRADE_BLOCKED + 传 account_login**（M3 前移）
- `backend/app/ai/trade_accountability.py`：归档（思想参考），新建只读聚合服务（M5）
- `backend/app/api/routes/`：纪律状态查询端点（blocked_reason/reason_code/cooldown_remaining_min/trades_today/max_trades，**require_auth**）+ 纪律配置读写端点
- `frontend/app/settings/page.tsx`：交易纪律配置区块
- `frontend/app/trading/page.tsx`：拦截文案 + 次数/冷却展示
- `frontend/app/manual-reviews/page.tsx`（或新 /discipline）：复盘统计
- **测试**：test_discipline_timezone.py + 各功能单测 + 基线重建（见七）

---

## 六、不做什么与边界（v3 补充）

- **不实现**手动通道的绝对回撤 15% 硬拦截（属账户级全局风控，单独评审）。
- **不改变**JEV/SystemOne 决策链主体，只在硬闸门层加纪律检查。
- **不做**自动平仓/强平（除 1c/1d 的"收盘前了结"排队外；期货强平由券商端执行）。
- **不新增**大表/迁移（计数全走 Redis；纪律评分独立存储另议）。
- **边界声明**：MT5 bridge 直连（`BRIDGE_API_KEY` 静态 key）是后端纪律无法覆盖的最终执行者——8001 不暴露公网/仅 SSH 隧道作为部署检查项；长期方案把周/月熔断下沉 bridge。
- **存量数据处置（v3 新增）**：trades 已混存 EET-shifted naive 由 F6 一次性回填；回填前历史统计/展示仍可能偏移 2-3h，文档标注迁移窗口。
- **现网参数挤压（评审 1）**：`MAX_CONCURRENT_TOTAL=1 + MAX_LOT=0.01` 极紧，与"日 3 笔"叠加会互相挤压——实施时按实际资金规模校验并说明，不默认调整。

---

## 七、测试与验收基线（v3 补充）

1. **基线重建**：以 `pytest --collect-only -q` 实测收集数为准（勿引用历史数字）；实施前跑 `pytest --tb=no -q > baseline.txt` 存 FAILED/ERROR 清单（**重点实测 test_multi_agent.py 当前状态**）；改动后 diff，空差集 = 不回归。
2. **零 xfail 缺口**：仓库零 xfail，实施时把既有失败固化为带原因 xfail，或 CI 加"失败集合 diff"门禁。
3. **测试设施**：全走 fakeredis + ASGITransport + 鉴权关闭；时间用 **`discipline_now()` 注入**（mock 返回固定上海时刻，不 mock zoneinfo 解析——Asia/Shanghai 恒定 UTC+8 确定性）+ "写过去时间戳"先例。
4. **故障模式用例（v3 新增）**：Redis down → `discipline_redis_down` 拒单；旧 bridge naive 串兼容单测（MT5_SERVER_TZ 转换）；时钟倒退（周期号回退 → 拒单告警）；休息日 bypass（改单/撤单重下）。
5. **高风险回归点**：test_guardrails.py import 模块常量需同步；test_systemone.py 升级前先跑确认 verdict 破坏面；order_preflight.py 是最高风险公共模块（新步骤插在 guardrails 步骤后）；circuit_breaker.py additive 扩展不改现有签名；mt5_bridge 测试独立 CI job。
6. **前端验收**：纪律状态端点契约断言（结构化字段）+ 页面目检；vitest/e2e 列 P3 不阻塞。

---

## 八、两轮八路评审意见汇总（v3 逐条吸收）

### 第一轮（评审 1-4）
| 评审 | 关键意见 | v3 处理 |
|------|---------|---------|
| 评审 1 实现可行性 | 2a trade_count 口径错误；AI 通道不写表/不写事件；3c 逆势已 REJECTED；配置双轨；bridge leverage 一行 | 2a 用 record_order_opened；broker 落库前移 M3；3c 修正前提；单轨 Redis；1b 排最前 |
| 评审 2 交易风控 | R1 熔断 60min 恢复；R2 口径；R3 逆势无豁免；R4 乒乓；R5 次数池误伤；遗漏周末/强平/防拆单 | 1c rest-of-period+until；按通道分池；3c CAUTION+三重+限次豁免；3a 第 2 次硬拒；1d/1c 强平；防拆单 P3；exposure_cap 收紧 2% |
| 评审 3 架构安全 | 引擎 fail-open；Redis 清零回填；并发竞态；bridge 直连；配置单轨；评分污染 | 三通道 fail-closed；MT5 history 主源回填；INCR 原子；第六节边界；Redis 单轨；评分独立存储 |
| 评审 4 测试验收 | 时间类不可测；前端零测试；边界/并发/归零缺位；基线不可考；零 xfail | 周期号 key+discipline_now 注入；端点契约断言；验收逐条可测；第七节基线；改动点标注风险 |

### 第二轮（评审 5-8）
| 评审 | 关键意见 | v3 处理 |
|------|---------|---------|
| 评审 5 时区修复专项 | F1 修法成立（MT5 time 是绝对 epoch）；F1 单独部署静默破坏后端；F2 收敛点 9 处不全；缺存量回填；F3 覆盖漏 5 处；F4 矛盾破坏 crypto | 3.0 重写：F1-F6 + 同窗部署顺序 + 9 处收敛点 + F6 存量回填 + F3 清单补全 + F4 定案（纪律日界替代 per-asset，sessions 保留市场职责） |
| 评审 6 落实复核 | v2.1 解决第一轮 15/18 项；3.0 保留错误表述；改动清单缺 8 项；遗漏 MAX_CONCURRENT 挤压/spike/浮动盈亏 | 修正 F1 表述；第五节补全 8 项；第六节补现网参数挤压说明 |
| 评审 7 时区×纪律交互 | 纪律计数统一 discipline_timezone（默认上海）；周期号 isocalendar+iso_year 陷阱；streak 序列语义；休息日本地周几；冷却与日界正交；until 绝对时刻；账户级聚合；回填按 close_time 归期；时钟源收敛 | 3.1/3.2/3.3/3.4 逐条落地 + 第九节口径表整体替换 |
| 评审 8 可实施性终审 | 有条件 GO：改动清单缺 8 项、F1 表述错、无应急开关、口径未冻结；部署顺序先后端→前端→bridge；里程碑 M1-M5；存量处置未声明 | 第五节补全 + 开关设计（discipline_gate_enabled）+ 部署顺序写入 3.0 + M1-M5 里程碑表 + 第六节存量处置 |

---

## 九、待用户确认的参数与口径（v3 定案版）

### 时区口径（评审 7 定案，替代 v2.1 的"一律 UTC"）

**核心原则**：纪律计数（次数/熔断/冷却/休息日）按用户已确认口径：**日界 = 22:00 UTC 外汇日**，周/月周期号按外汇日时钟 `(utcnow-22h)` 生成，**休息日与展示 = `discipline_timezone`（默认 Asia/Shanghai）本地时刻**。市场开闭市（sessions.py per-asset）与 bridge/落库/展示（UTC 链，3.0 F1-F3）保持不变。纪律时间读取收敛为 `discipline_now()`（UTC 时刻）；休息日/展示经 ZoneInfo 转换到 Asia/Shanghai。

| 口径 | 定义 | 实现要点 |
|------|------|---------|
| 日界（日开仓次数/日亏/equity 回撤/每小时） | **UTC 22:00 外汇日**（= 上海 06:00）；上海 00:00-06:00 计入前一日 | `discipline_day_key()` = `(utcnow - 22h).strftime("%Y-%m-%d")`；sessions.py 保留市场职责 |
| 周界（周熔断/周次数） | **外汇周**：`(utcnow - 22h).isocalendar()` 的 iso_year/iso_week（iso_year 非 date.year） | key=`circuit:weekly:{iso_year}-W{ww:02d}:...`；读取只读当前周期 key |
| 月界（月熔断） | **外汇月**：`(utcnow - 22h).strftime("%Y-%m")` | 同周；TTL 周 15 天/月 45 天仅回收，读时周期号计算是唯一真相源 |
| 跨日连亏 streak | **交易序列**语义：按平仓时间连续 N 笔亏损，跨日/跨周末不中断，盈利即断 | `guardrails:loss_streak:{account}`（TTL 随写刷新 7 天），原子更新；不用日 key |
| 强制休息日"周五" | **Asia/Shanghai 本地** `weekday()==4`（北京周五全天） | `discipline_mandatory_rest_days=[4]`，比较用 `datetime.now(ZoneInfo("Asia/Shanghai")).weekday()` |
| 24h 冷却 / 反手 30min | 相对时长，绝对时间戳（epoch 秒），**与日界正交**（跨日不解除、日次数照常重置） | key TTL=duration+冗余，绝不用日 TTL；判定顺序：周期级闸门>冷却>次数>方向 |
| 熔断 rest-of-period | 日 until=下一 22:00 UTC；周 until=下周日 22:00 UTC；月 until=下月 1 日 22:00 UTC | until 用 `(utcnow - 22h)` 对齐的边界 + timedelta，DST 安全 |
| 历史订单/展示 | bridge 带偏移 ISO → 后端 naive UTC → 前端 parseChatDate(补 Z) + Asia/Shanghai 显示 | 3.0 F1-F3；纪律状态端点以 discipline_timezone 渲染 |
| 时钟源 | 纪律计数唯一时钟 `discipline_now()`；多 worker 建议 Redis TIME 对齐；时钟倒退（周期号回退）→ 拒单并告警 | 收敛现三时钟源分裂 |

> `discipline_timezone` 默认 Asia/Shanghai（无 DST）。日界 22:00 UTC、周/月按外汇日时钟、休息日按本地——用户已确认此混合口径（市场计数走外汇日、人的行为走本地）。回填（周/月扩展窗口）**必须按每笔 close_time 计算周期号**，不得按"now"归周。

### 参数默认值

| 参数 | v2.1 默认 | v3 建议值 | 依据 |
|------|-----------|----------|------|
| 每日开仓上限 | 3 笔 | 3（manual 池）+ 3~5（engine/ai 池） | R5 按通道分池；按成交计 |
| 每周开仓上限 | 10 笔 | 10（manual 池） | 合理保留 |
| 反手冷静期 | 30 分钟 | 30 分钟 + 当日第 2 次反手硬拒 + 必填 2 项确认信号 | R4 防乒乓 |
| 熔断后冷却 | 60 分钟 | **日=当日停、周=本周停、月=当月停（until 绝对时刻）** | R1 + 评审 7 |
| 周亏熔断 | 8% | **6~7%** | 评审 2 |
| 月亏熔断 | 12% | 12% | 合理保留 |
| 单笔风险 exposure_cap | warn 2%/block 5% | **block 收紧 2%**（warn 1%） | 对应"单笔止损≤1~2%" |
| 单笔保证金占比 | 20% | 20%（**equity 分母**，free_margin 实值硬拦） | 兜底闸；评审 8 对齐分母 |
| 总持仓保证金占比 | 50% | **30~40%** | 评审 2 |
| 强制休息日 | 每周 1 天 | 每周 1 天（**本地周五**，覆盖变相入口） | 评审 7 |
| 冲动 24h 冷却 | 24h | 24h→72h→本周禁（阶梯升级，无文字绕过） | 5b + 评审 7 正交 |
| 连亏周熔断 | 5 笔 | 5 笔（**序列语义**，跨日跨周末连续） | 评审 7 |
| 纪律时区 | UTC | **Asia/Shanghai**（`discipline_timezone`） | 评审 7/8 |
| 门禁总开关 | 无 | `discipline_gate_enabled=true`（env+Redis） | 评审 8 |

### 需用户书面确认的三项二选一（评审 8）—— 已确认（2026-10-10）

1. **纪律时区口径**：✅ **Asia/Shanghai**（用户确认）。用于休息日判断、展示层渲染、前端时区。
2. **日边界**：✅ **22:00 UTC 外汇日**（用户确认，未采纳评审 7 推荐的本地 00:00）。
   - 影响：纪律计数的"日"（日开仓次数、日亏、连亏日段、每小时）在 **UTC 22:00** 切换 = 上海 06:00。上海 00:00-06:00 的操作会计入前一日（用户书面接受此口径，与 MT5 外汇日惯例一致，也天然对齐现有 CircuitBreaker 22:00 UTC per-asset 重置表）。
   - **周/月边界配套**：为保持"一套日历"（评审 7 要求避免混搭），周/月周期号基于**外汇日时钟** `(utcnow - 22h)` 生成：周 = `(utcnow - 22h).isocalendar()`（iso_year/iso_week）、月 = `(utcnow - 22h).strftime("%Y-%m")`。即周日 22:00 UTC 后进入新外汇周/外汇月。
   - 休息日"周五"仍按 **Asia/Shanghai 本地周五**（人的行为纪律，不随外汇日）。
3. **保证金分母**：✅ **统一 equity 占比**（用户确认，与现 systemone 0.25/0.50 同分母；20% 上限 = equity×20% 硬拦）。

> 上述三项已冻结，实施按此执行。参数值（日3笔/周10笔/30min/6-7%/12% 等）仍可在 Redis 运行时配置调整。

---

## 附录：时区专项调查摘要（评审 5 依据）

- MT5 Python API 的 `.time` 字段是**绝对 Unix epoch 秒**（官方示例 `pd.to_datetime(unit='s')`）；`datetime.fromtimestamp(ts)` 用 bridge 宿主机 OS 时区解释才产生 EET naive——根因不是"MT5 给本地时间"，而是"epoch 被宿主机时区解释"。改 `tz=timezone.utc` 只是把标注修正到正确口径，绝对时刻不变。
- 输出点 8 处：`main.py:169/170/289/367/459/814/854` + `:831-832`；输入侧 4 处：`:714/756/760/803-804`（naive 喂 MT5 被按宿主机时区解释）。
- 消费点 9 处：`engine.py:1437/2128/2186/2051`、`history.py:147-169/225/305`、`analytics.py:98`；巧合正确的：`engine.py:1437/2186`（fromisoformat+replace）、`manual_order_gate.py:804`、`market_data.py:39-42`。
- 兼容矩阵：旧 bridge+新后端 F2 安全；**新 bridge+旧后端不安全**（history.py:150-151 aware vs naive TypeError）；新前端+旧 bridge 变差。
- 风控日边界现状：日亏 22:00 UTC（circuit_breaker.py:344 + sessions.py:54-64）、连亏/每小时 00:00 UTC（guardrails.py:79/84/397），错位最多 22h。
- 前端显示：TZ="Asia/Bangkok"（format.ts:2）、CHAT_TIME_ZONE（chat-utils.ts:11）为既有约定；用户在北京（UTC+8）差 1h，纪律状态端点改 discipline_timezone 渲染。

---

*计划文档版本：v3.1（2026-10-10，三项口径已由用户确认冻结：纪律时区 Asia/Shanghai / 日界 22:00 UTC 外汇日 / 保证金分母 equity）。进入实施：M1→M5，实施计划见 .planning/2026-10-10-discipline-gate-implementation/*
