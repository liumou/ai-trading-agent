# Findings & Decisions

## Requirements
- 用户提供《黄金期货交易计划（纪律风控版）.docx》，聚焦四类交易行为问题：频繁开仓、多空频繁切换、多次爆仓、总想交易。
- 要求：基于现有系统能力，设计功能增强方案，生成优化计划文档；计划需用户批准后才执行。

## Research Findings

### 用户交易计划文档核心内容（四大问题 → 对策）
| 行为问题 | 计划中的对策 | 对应章节 |
|---------|------------|---------|
| 频繁开仓 | 开仓前强制检查清单、每日/每周次数上限（建议每日≤3、每周≤10）、无信号即空仓 | 三 |
| 多空频繁切换 | 大周期定方向、一日一个方向、切换需≥2项确认信号+≥30分钟冷静期+未熔断 | 四 |
| 多次爆仓 | 单笔1-2%/单日3-4%/单周6-8%/单月10-12%熔断阶梯、连亏3笔日熔断/5笔周熔断、仓位上限（单笔保证金≤20%、总持仓≤50%）、隔夜/周末持仓限制、手数用风险倒推 | 二 |
| 总想交易（手痒） | 24小时冷静期、强制休息日、冲动替代动作、复盘替代下单、情绪识别 | 五 |

### 系统现状（已有能力）
- **手动交易防火墙**（manual_order_gate.py）：硬闸门（preflight_order）+ SystemOne/JEV 审查（systemone.py + typesafe_jev.py）+ fail-closed + CAUTION 二次确认（120s TTL）
- **已有熔断**：日亏3%（单品种+账户级）、日内equity回撤3%、连亏熔断（guardrails_consecutive_loss_halt，.env=3）、绝对回撤15%（仅引擎）、冷却60分钟（仅引擎自动恢复）
- **已有频率限制（硬编码）**：每小时≤5笔、最小间隔120s（mcp_server/guardrails.py L43-44，不可配置）
- **已有情绪规则**：无止损拒、亏损后15分钟内翻倍手数 block（martingale）、连亏≥3 warn、频率近限 warn
- **已有 SL/TP 防漂移**：改 SL 锚点倍数≤5×、每日拉宽≤3次
- **JEV 决策**：provider 链 local_jev→typesafe_jev→LLM，conf_floor=0.30，min_confidence=0.55（CAUTION）
- **数据**：OrderAudit（review JSON 全历史）、Trade（盈亏）、BotEvent（TRADE_BLOCKED/CIRCUIT_BREAKER）、Redis TTL key（连亏/日开仓计数）
- **MT5 bridge**：GET /account（balance/equity/margin/free_margin/profit，无 leverage 字段）、GET /positions

### 系统缺口（与交易计划对照）
1. **每日/每周开仓次数上限：无**（BiasGuard 死代码、CircuitBreaker.trade_count 未用于闸门）
2. **可配置性差**：每小时次数/最小间隔硬编码；guardrails 日亏3%硬编码与 settings.max_daily_loss 脱节；max_portfolio_leverage 声明未用
3. **方向切换/反手限制：完全没有**（同品种 BUY→SELL 快速反手无拦截）
4. **手动通道冷却/冷静期/休息日：无**（熔断后无"禁手动开仓N分钟"；无24h冷静期、无强制休息日概念）
5. **账户级风控配置：无**（mt5_accounts 只有凭据，风控参数全全局）
6. **杠杆/保证金硬上限：不生效**（systemone margin_pct 软检查依赖 bridge leverage，而 bridge /account 不返回 leverage）
7. **周/月熔断阶梯：无**（只有日级）
8. **隔夜/周末持仓限制：无**
9. **复盘问责未接入**：BiasGuard / TradeAccountability 死代码

### 可插入硬拦截的关卡（实现要点）
1. 路由层 manual_trading.py POST /api/trading/orders（输入校验）
2. submit_order() switching gate + preflight（manual_order_gate.py L161-185）
3. preflight_order() 第1-9步（order_preflight.py L120-352）
4. mcp_server/guardrails.py _validate_order_core()（L234-352）
5. _apply_verdict() REJECTED / CAUTION 确认
6. _execute_approved() 执行前重验 + rollout 门禁

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| 复用现有 preflight 硬闸门 + guardrails 扩展，而非新建独立拦截链 | 三通道共用同一套硬闸门，最小侵入、行为一致 |
| 新增"交易纪律门禁"（discipline gate）作为 preflight 的一个新步骤 | 集中承载频率上限/方向切换/冷静期/休息日/周月熔断，内聚且易测 |
| 周/月熔断、开仓次数、反手冷却用 Redis 计数（可配置 TTL） | 与现有 circuit_breaker / guardrails 计数方式一致，无新表 |
| 账户级风控参数优先复用 symbol_configs + settings（env 可覆盖），不新建大表 | mt5_accounts 无风控字段；先做全局+每品种，账户级按需 |
| 前端：新增"交易纪律"设置区块 + 冷却/限制文案提示 | 复用 settings 页 + ReviewResultCard 拒绝文案通道 |
| 不实现绝对回撤15%手动通道硬拦截（本期） | 属于账户级全局风控，风险大，需单独评审 |

## 评审 1（实现可行性）结论 — 2026-10-09
见 docs/optimization/trading-discipline-enhancement.md 之外保存于 progress.md。要点：
1. **【口径错误】2a 复用 CircuitBreaker.trade_count 计开仓**：该计数在平仓路径自增（circuit_breaker.py:78-80），数的是当日已平仓笔数非开仓。改用 record_order_opened（三通道唯一共同开仓触点，guardrails.py:376-384）
2. **【通道缺口】AI 通道（broker.py place_order）不写 OrderAudit/Trade、拒单不写 TRADE_BLOCKED**（broker.py:142-188）；BotEvent.account_login 恒为空（engine.py:1974、manual_order_gate.py:919）——按账户统计事件前需修
3. **【前提错误】3c 逆势升级**：双 TF 强逆势当前已 REJECTED（systemone.py:191-193 + :620-621），只需补弱场景（单 TF/弱 ADX）；3b 方向规则只对手动通道生效（systemone 是 manual 审查链）
4. **【机制不符】前端 settings 可编辑**：guardrails 类参数 env-only 重启生效（guardrails.py:63-71），无 UI 运行时通道；唯一先例 rollout_mode 走 Redis（guardrails.py:115-136）
5. **【边界】周/月重置**：现有 reset 按品种 asset_class 每日 TTL（sessions.py:49-64），无周一/月初概念，需新 helper
6. **【高性价比】修 bridge /account 补 leverage 一行**（mt5_bridge/main.py:392），systemone margin 检查自动复活；free_margin 已返回可直接硬拦
7. **【设计冲突】4b"填理由绕过一次"与 fail-closed 总原则矛盾**，需显式豁免+审计
8. **【环境】现网 MAX_CONCURRENT_TOTAL=1 + MAX_LOT=0.01 极紧**，与日3笔上限叠加会互相挤压
9. 966 用例低估（实为 81 文件/1118 函数）；计划引用的行号全部准确

## 评审 2（交易风控逻辑）结论 — 2026-10-09
R1【P0】熔断 60 分钟自动恢复击穿"单日≤3~4%"铁律 → 熔断档一律 rest-of-period（日=当日停、周=本周停、月=当月停）；60min 只留瞬态闸
R2【P0】CircuitBreaker.trade_count 计的是**平仓笔数**非开仓数 → 新建开仓计数（复用 record_order_opened），硬闸门按成交计、冲动冷却按尝试（含被拒）计
R3【P0】大周期逆势升级 REJECTED + 无豁免 = 方向误判时永久锁死 → D1/W1 冲突保持 CAUTION；REJECTED 升级需"双大周期同向反向+ADX 强度+D1 非成形 K 线"三重条件；限次人工豁免（每周≤2，记入纪律分）
R4【P0】"一日一个方向"可被 30 分钟+CAUTION 确认乒乓绕过 → 当日同品种第 2 次反手硬拒；第 1 次 CAUTION+强制勾选 2 项确认信号；所有通道开仓写同一 flip key
R5【P1】跨通道共享次数池使引擎单挤占手动额度 → 次数按通道分池（manual 独立池），跨通道只保留账户级闸门
遗漏（必须补）：① 周末不留仓/隔夜敞口（用户计划第二章明文，现 max_position_duration_hours=0 禁用）；② 日熔断后收盘前了结全部持仓（复用 position_close.py）；③ 防拆单（Σ手数×合约乘数上限）
遗漏（建议补）：spike_chase 覆盖 AI 通道；反手"≥2 项确认信号"机器化结构化清单；周/月熔断含浮动盈亏口径
参数评审：单笔风险 exposure_cap block 5% 过松应收紧 2%；周亏建议 6~7%；总持仓保证金 50% 过松建议 30~40%；保证金 20% 是兜底闸（主闸是 exposure_cap），需支持期货固定保证金模式（优先用 bridge margin/free_margin 实值）
新增发现：连亏计数是日 key（guardrails:trade_results:{date}），"周 5 笔连亏"无跨日 streak 实现，需补；MAX_WEEKLY_LOSS_PCT=0.07 常量存在但是死代码

## 评审 3（架构与安全）结论 — 2026-10-09
A1【高危】引擎通道纪律穿透：engine.py:1058-1083 对 preflight 异常 try/except 吞掉后照常下单（fail-open），Redis 挂掉或纪律 gate 抛错时自动交易完全绕过纪律 → 纪律门禁必须对引擎通道也 fail-closed，且区分 kind="discipline_redis_down"
A2【高危】Redis 重启=纪律清零：backfill_today（circuit_breaker.py:288-337）只覆盖引擎日 PnL；AI 通道订单 OrderAudit/Trade 两表都不写（broker.py:142-188）→ "DB 核对重启恢复"对 AI 通道不成立；需扩展 7/30 天回填 + flip key 用当日成交 history+持仓重建 + MT5 history 幂等回填
A3【中】三通道并发竞态：AI/引擎通道无锁；手动通道锁在提交阶段释放、计数在执行后才 +1 → 需 Redis 原子操作（INCR 后判读）或 Lua 脚本；提交+执行前重验两处都检查
A4【中】引擎被纪律误伤：flip 冷静期/次数上限无条件套引擎自动单 → 按通道分档（手动全硬拦、引擎/AI warn 或豁免）；AI 通道不传 account_login（broker.py:80-84）
A5【边界】bridge 直连绕过：bridge 所有交易端点只有 BRIDGE_API_KEY 静态 key（mt5_bridge/main.py:52-56）无纪律风控 → 计划需声明边界；长期把周/月熔断下沉 bridge
配置体系：纪律参数应统一走 Redis 运行时通道（对齐 rollout_mode 先例 guardrails.py:122-136），否则"前端编辑不生效"（settings 是 pydantic 进程单例 config.py:518）
审计：纪律评分不得写入 OrderAudit.review（_verdict_of 会污染审查链，manual_order_gate.py:60-82）；豁免通道必须留痕（BotEvent+OrderAudit）
时钟：现有全 UTC 无 DST 问题；周/月 TTL 惰性重置（_seconds_until_week_reset/_month_reset），固定周 vs 滚动 7 天窗口需明确（建议固定周）
测试数更正：1115（非 966）；baseline 需更正
安全：新增纪律状态端点必须 require_auth（对齐 manual_trading.py:22 router 级依赖）；生产强制 setup 鉴权写入验收

## 评审 4（测试与验收）结论 — 2026-10-09
- 测试基础设施：全用 fakeredis（conftest.py，每测试新建天然隔离）；无 freezegun；时间用"写过去时间戳到 Redis"（test_circuit_breaker.py:66）或 patch time.time（test_ai_circuit_breaker.py）；autouse _pin_guardrail_defaults 钉配置默认值；前端零测试设施（package.json 无 test script、无 vitest/jest/playwright、CI 无 frontend job）
- **测试数更正**：实测 1095 函数/79 文件（参数化展开接近 1115/81）；"966"无出处，必须用 pytest 快照重建基线；仓库**零 xfail**（既有失败无代码内标记，验收无法自动区分）
- 时间类验收关键设计决策：周/月熔断建议 **key 带周期号**（circuit:weekly:2026-W41:）→ 测试写旧周期 key 断言不读，免时钟 mock；TTL 滚动窗口备选（需 mock 时钟/del key）
- 回归高风险改动：test_guardrails.py **import 了 MAX_TRADES_PER_HOUR 模块常量**（第 9-16 行）；test_systemone.py 若把 CAUTION 升 REJECTED 会改现有 verdict；order_preflight.py 是最高风险公共模块（被 broker/manual/engine 三处引用）
- 前端验收建议：纪律状态端点返回结构化字段（blocked_reason/reason_code/cooldown_remaining_min/trades_today/max_trades）+ 后端契约断言 + 页面目检；vitest/e2e 列 P3 不阻塞 P0
- e2e：用 ASGITransport + 鉴权关闭（conftest）+ fakeredis，无需浏览器 e2e（门禁是服务端强制）
- CI：backend 跑 pytest --cov=app --cov-fail-under=30，**不卡失败数**；建议加失败集合 diff 门禁或固化 xfail
- 验收标准修正方向：每条验收给可落地测试路径（fixture 复用：ok_connector/ok_guardrails/_manager/_profiles/engine/redis_client）

## 时区链路调查结论 — 2026-10-09（用户报告"历史订单时区不对"）
根因链（三层叠加，主因第①层）：
1. **MT5 bridge 输出 naive 服务器本地时间（EET）**：main.py:831-832 from_date=datetime.now()；:854 "time": datetime.fromtimestamp(deal.time).isoformat()；:459 open_time 同；全文件零时区转换（无 astimezone/timezone.utc/mktime）
2. **后端混语义存储**：models.py:133-134 Trade.open_time/close_time 同一列混两种 naive——bot 自产=UTC（engine.py:48-50 _naive_utc()）、bridge 平仓/孤儿回填/手动= EET（engine.py:1437/2128/2186 原样落库）；history.py:147-150 把 EET naive 当 UTC 比较 cutoff
3. **前端解析错误**：new Date(naive) 当浏览器本地时区解析再转 Asia/Bangkok（history/page.tsx:198/278、manual-reviews/page.tsx:171、PriceChart.tsx:174/185/210、format.ts:2 TZ="Asia/Bangkok"）；唯一做对的是 chat-utils.ts:13-17 parseChatDate（naive 补 Z 视作 UTC）
风控边界不一致：日亏按 22:00 UTC 滚动（circuit_breaker.py:344 + sessions.py:54-64），连亏/每小时按 00:00 UTC（guardrails.py:79/84/397）——错位最多 22 小时
现有约定：CLAUDE.md:227 后端必须 datetime.utcnow() naive（asyncpg 拒绝 offset-aware）；前端固定 Asia/Bangkok；bridge 侧无任何时区策略声明
修复方向（F1 收益最大）：F1 bridge 全部 fromtimestamp 改 tz=timezone.utc 输出带 +00:00（机械替换，需同步部署 + 后端兼容旧 naive）；F2 后端收到带偏移串 astimezone(UTC).replace(tzinfo=None)、旧 naive 按 MT5_SERVER_TZ 常量转换；F3 前端统一 parseChatDate（补 Z 当 UTC + Bangkok）；F4 风控日边界统一（日亏/连亏同一边界）；F5 manual-reviews 页补 timeZone；F6 文档化约定
对纪律计划的影响：日开仓次数/周月熔断/强制休息日(周五)的边界定义必须先定时区口径——建议所有纪律计数统一用 UTC 自然日 + 周期号 key（评审 4 已定），休息日"周五"按 UTC 定义并在配置注明

## 评审 5（时区修复方案专项）结论 — 2026-10-09
F1 判定【正确】：MT5 Python API 的 .time 字段是**绝对 Unix epoch 秒**（官方示例 pd.to_datetime(unit='s')；bridge 测试 mock 也用裸 epoch 整型），不是服务器本地墙钟。fromtimestamp(ts) 用宿主机 OS 时区解释才产生 EET naive——改 tz=timezone.utc 只是把标注修正到正确口径，绝对时刻不变，无新错位
要改的输出点共 8 处：main.py:169/170/289/367/459/814/854 + :831-832 from_date/to_date
F1 漏了"输入侧"：main.py:714/756（挂单过期 datetime.fromisoformat naive 喂 MT5）、:760、:803-804（OHLCV 窗口）——MT5 把 naive 当宿主机本地解释，需一并决策
F2 收敛点清单不全（计划只列 4 处，实际 9 处消费点）：
- 漏 engine.py:2051（挂单恢复 open_time fromisoformat 无 replace → F1 后 aware 入库 asyncpg 拒 → 挂单恢复反复失败，:2044 吞异常）
- 漏 history.py:225（daily-pnl）、:305（performance）、analytics.py:98（交易分析）——F1 后 aware vs naive cutoff 比较抛 TypeError 被 except 吞 → **手动成交/日盈亏/表现统计静默丢失**
- 巧合正确的点：engine.py:1437/2186（fromisoformat+replace 恰好剥成正确 UTC）、manual_order_gate.py:804、market_data.py:39-42
**高危：F1 单独部署会静默破坏后端**——计划"F1 后端自动正确前端自愈"不成立，F1+F2 必须同一部署窗口
**高危：缺既有历史数据回填**——trades 里已存 EET-shifted naive 永远错 2-3h，用户核心投诉（历史订单）只修新数据；需一次性回填（按 MT5_SERVER_TZ zoneinfo 反推或按 DST 段 +2/+3h）
F3：parseChatDate 正则能匹配 +00:00 幂等；漏 NewsCard.tsx:26、notification-center.tsx:42、connection-status.tsx:49、ReviewHistoryDialog.tsx:94/121、macro/page.tsx:170（相对时间计算）；Bangkok vs 北京（用户 UTC+8）是既有 UX 约定，建议展示层跟随浏览器或配置化
F4 矛盾：3.0 节说"统一 22:00 UTC"、第九节说"UTC 自然日（或 22:00）"；字面统一会破坏 crypto 24/7（sessions.py:37）+ test_circuit_breaker.py:362-363（crypto get_reset_hour==0）→ 保留 per-asset 重置表（crypto=0），"统一"限定账户级 guardrail 计数器日界
周/月 key 建议：2026-W41 按 ISO 周（周一）计算；放弃"日界 22:00、周界周一 00:00、外汇周周日 22:00"混搭，统一同一套日历；周日晚 22:00 后成交归属钉死
**纪律边界时区建议：做成配置项 discipline_timezone 默认 Asia/Shanghai**（约束的是北京的人，"UTC 周五"=北京周五 08:00 起反直觉）；存储/传输仍 UTC
MT5_SERVER_TZ 用 zoneinfo.ZoneInfo("Europe/Athens") 转旧 naive 可靠（tzdata 处理 DST）；上线前在 VPS 跑 date 确认宿主机时区
桥输入侧（main.py:714/756/760/803-804）naive 窗口的时区解释需一并决策

## 评审 6（v2 修订落实复核）结论 — 2026-10-09
v2.1 已解决第一轮 18 项核对中 15 项（2a 口径/逆势前提/AI 缺口/R1-R5/周末强平/引擎 fail-closed/INCR/配置单轨/周期号 key/基线/契约断言/验收可测化均落实）
但：
- **3.0 节保留"F1 只需同步部署 bridge 下游自动正确"错误表述**（评审 5 高危：F1 单独部署静默破坏后端；F1+F2 必须同窗）；F2 收敛点只列 4 处，评审 5 指出的 5 处高危消费点（engine.py:2051、history.py:225/305、analytics.py:98）未进清单；F1 输出点只列 4 处漏 :289/367/814；F1 输入侧（main.py:714/756/760/803-804）未决策
- 第九节"统一按 UTC"与评审 5 建议"discipline_timezone 默认 Asia/Shanghai"相反；"UTC 自然日（或 22:00 外汇日）"二义未定案；未纳入"保留 per-asset 重置表（crypto=0）"
- 既有历史数据（trades 已存 EET-shifted naive 错 2-3h）回填方案完全缺失
- 3.0 节"见第六节后附录"悬空引用（无附录）；第八节无评审 5 汇总行
- F3 覆盖漏 NewsCard/notification-center/connection-status/ReviewHistoryDialog/macro 五处
- 改动点清单 9 项"承诺未列"：broker.py DB 落库（AI 通道）/broker account_login/CLAUDE.md F5/position_close(1d)/margin_mode/跨日 streak/回填扩展/强制休息日/防拆单
- 遗漏：MAX_CONCURRENT_TOTAL=1 与日3笔挤压未讨论；spike_chase 覆盖 AI 通道未提；周/月熔断含浮动盈亏口径未说明；引擎豁免策略细节未展开；生产强制鉴权验收未入第七节

## 评审 7（时区×纪律门禁交互）结论 — 2026-10-09
核心裁决：纪律计数统一走 **discipline_timezone 本地自然日（默认 Asia/Shanghai）**，市场开闭市（sessions.py per-asset 22:00/21:00/0:00）与 bridge/落库/展示（UTC 链）保持不变、解耦
- 日界：本地 00:00（上海=UTC 前日 16:00），**替代 per-asset 22:00**（否则账户级日亏混合两个"日"，16:00-22:00 UTC 窗口错位）；F4 落"统一 TTL"而非换算表
- 周/月 key：周期号由 discipline_now().isocalendar() 生成（**iso_year 陷阱**：2025-12-29→2026-W01）；周=本地周一 00:00（非外汇周）；月=本地 1 日 00:00；读取只读当前周期 key（TTL 仅回收：周 15 天/月 45 天）；**回填必须按每笔 close_time 归期**（重启于周一回填周五亏损必须写 2026-W40，否则周熔断错判）
- 跨日 streak = **交易序列语义**（按平仓时间连续 N 笔亏损，跨日/跨周末不断，中间盈利即断），非"连续 N 天"；现 guardrails:trade_results 日 key + 2 天 TTL 不可用 → 改跨日持久化 guardrails:loss_streak:{account}（原子更新 Lua/WATCH）
- 强制休息日"周五"= 本地 weekday()==4（"UTC 周五"=北京周五 08:00 起，漏拦北京 0-8 点+误拦周六 0-8 点，反直觉失效）
- 24h 冷却/反手 30min 与日界正交：绝对时间戳，跨日不解除、日次数照常重置（23:50 触发次日 00:00 不绕过）；key TTL=duration+冗余绝不用日 TTL；判定顺序：周期级闸门>冷却>次数>方向
- rest-of-period 存 **until 绝对时刻**（本地下一 00:00/下周一/下月 1 日），"当日"=纪律时区自然日；北京 21:00 触发按 22:00 UTC 只停 1 小时（R1 变相回归）vs 上海口径停 15 小时
- 时钟源统一：discipline_now()（datetime.now(ZoneInfo(settings.discipline_timezone))）；现三时钟源分裂（time.time()/datetime.now(UTC)/isoformat()）必须收敛；多 worker 建议 Redis TIME；时钟倒退检测（周期号回退拒单告警）
- DST：Asia/Shanghai 无 DST 默认安全；允许配置 DST 时区但一切边界用 zoneinfo 本地算术（禁止手工小时偏移）
- 纪律计数账户级（日亏/周亏/月亏/连亏/次数），反手冷静期唯一按品种；1a 周/月 key 虽 :daily_pnl:{symbol} 但闸门判定加账户级聚合（仿 get_global_daily_pnl）
- 测试：discipline_now() 可注入（mock 返回固定上海时刻）；fakeredis+上海+周期号稳定；边界用例用上海 23:59/00:00、UTC 周日 15:59/16:00、2025-12-29 iso_year；冷却跨日正交用例；独立 test_discipline_timezone.py
- 前端 CHAT_TIME_ZONE="Asia/Bangkok"（UTC+7）用户在北京（UTC+8）差 1 小时——纪律状态端点以 discipline_timezone 渲染，前端展示顺带改 Asia/Shanghai

## 评审 8（整体可实施性终审）结论 — 2026-10-09
**有条件 GO**：核心设计全部正确（周期号 key/按通道分池/rest-of-period/代码引用 100% 核实），4 个阻止项可在 v3 闭合：改动清单缺 8 项、F1 部署表述错误、无应急开关、时区口径未冻结
部署顺序（同一维护窗口内）：先发布后端 F2（双格式解析）→ 再前端 F3 → 最后 bridge F1（与 leverage 一行合并）；**新 bridge+旧后端不安全**（history.py:150-151 aware vs naive cutoff 比较 TypeError → /api/history 崩溃）；F3 绝不先于 bridge；8001 不暴露公网落成部署检查项
功能依赖：参数 Redis 运行时通道（2b P1）应前移到 M1 作基础设施（P0 的 1a/3a 就要消费 discipline_* 阈值）；回填以 MT5 history（bridge 侧全通道齐全）为主源需钉死
改动面规模：bridge 1 + backend ~10 + frontend ~8，跨三部署面，必须里程碑切割 M1-M5
文档硬伤 12 条（B.4）："见第六节后附录"悬空（无附录）；F1"只需同步部署 bridge"+"每项独立可上线"矛盾；manual-reviews 行误标 (F5) 应为 F3；3.3 节标题标 P0 但 3c 是 P2；3.4 节标题标 P2 但 4c 是 P3；防拆单 P3 在缺失能力表但优先级表/改动清单全无；1b 保证金分母 equity vs free_margin 两处未对齐；F4 与口径表 22:00/00:00 二选一未定；"周五"按 UTC 待用户决策阻塞 M1；术语"纪律门禁/discipline gate/纪律计数"混用；"评审 2"混用 R1-R5/洞 3/5b 编号；"966 无出处"不严谨（test_circuit_breaker.py:129 有 ticket=966668039 非测试数）
改动清单仍缺 8 项：broker 落库、CLAUDE.md F5、position_close 1d、margin_mode/symbol_configs、连亏跨日 streak、回填扩展、强制休息日、防拆单；新增第 10 项：引擎豁免策略无落地条目
验收缺：Redis down 用例（原则 2 承诺但验收无）；旧 bridge 兼容单测；时钟倒退；1c/1d 独立验收；2b"编辑即生效多 worker 一致"；休息日 bypass/确认信号必填
**运维开关缺失（风控最重要属性）**：无 discipline_gate_enabled（env 默认+Redis 可改，关闭记 kind='discipline_disabled'）、无 engine_discipline_enabled、MT5_SERVER_TZ 启动自检（bridge 时间 vs UTC 对拍）、key 错位恢复路径
存量数据处置未声明：trades 已混存 EET 当 UTC 是重算还是标注偏移，v3 必补
里程碑 M1-M5：M1 时区+基础设施（同窗：后端→前端→bridge）/M2 P0（引擎 fail-closed+1a/1c+3a/3b+1b）/M3 P1（2a/2b+1d，可独立）/M4 P2（3c+4a/4b，可独立）/M5 P3（4c+broker 落库，可独立）；建议 broker AI 落库前移到 M3 前（P0 回填完整性依赖它）
实施前必须 3 件事：v3 补清单缺口+F1 表述+同窗顺序+存量处置；补运维开关与回滚+Redis down/旧 bridge/时钟/休息日 bypass 验收；冻结三项二选一（时区口径/日边界 22:00 vs 00:00/保证金分母）+ 用户书面确认引擎豁免清单

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| 本任务无代码变更（纯规划交付），模板 Phase 3/4 标记为规划内决策 | 生成优化计划文档即 Phase 3 交付物；用户批准后进入实施 |

## Resources
- 交易计划文档：/Users/liumou/Downloads/黄金期货交易计划.docx
- 关键文件（探索结论见上方）
- 记忆：手动交易防火墙、用户偏好自主执行+中文
