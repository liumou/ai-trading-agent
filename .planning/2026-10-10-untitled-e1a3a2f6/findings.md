# Findings — 历史订单复盘功能

## 需求原文
> 历史记录里增加订单的复盘功能，对历史订单进行复盘：为什么亏损了？是什么原因导致的？是做对了，还是做错了，应该吸取什么经验等。盈利了，是什么原因？要对历史订单进行复盘，防止以后再犯同样的错误。

## 现状盘点（2026-10-10）

### 已有「复盘雏形」基础设施（可直接复用）

1. **Trade 模型字段丰富**（`backend/app/db/models.py:114`）
   - `trade_reason` — 开仓原因（AI/手动通道写入）
   - `pre_trade_snapshot`(JSON) — 开仓前快照（含 regime.composite）
   - `post_trade_analysis`(JSON) — 平仓后分析
   - `comment`、`strategy_name`、`ai_sentiment_score/label`、`account_login`

2. **`BotEngine._build_post_trade_analysis`**（`engine.py:1572`）——**规则式**复盘雏形：
   - 退出原因判定：`stop_loss` / `take_profit` / `manual_close`（按价格距 SL/TP 距离阈值 20%）
   - 持仓时长（duration_hours）、结果（win/breakeven/loss）、入场/出场 regime
   - `_handle_closed_trades`（engine.py:1450）平仓时自动写入 `trade.post_trade_analysis`
   - **局限**：纯规则计算，**无 AI 判断"做对做错"、无根因分析、无经验总结**

3. **`TradeAccountabilityTracker`**（`backend/app/ai/trade_accountability.py`）——四分类问责逻辑**已存在但未接线**（`_records` 仅内存列表，无生产调用点、无持久化）：
   - `skilled_win`（做对+赚）→ 强化
   - `correct_process`（做对+亏）→ 方差，不调整
   - `lucky_win`（做错+赚）→ 噪音，**不强化**
   - `real_mistake`（做错+亏）→ 学习调整
   - 这是"做对还是做错"的权威分类框架，应接入复盘

4. **AI 调用栈完备**（`backend/app/ai/`）
   - `AIClient.complete_json_async(system, user, max_tokens, agent_id)` — 结构化 JSON 输出（client.py:59），失败返回 None（AI 可选层）
   - `prompts.py` — 提示词模板模式 + `language.append_language_instruction`（多语言）
   - `news_sentiment.py:_clean` — 防注入清洗范例（剥离指令形字符 + 截断 + 标为 DATA）
   - `manual_order_gate.py` — `asyncio.wait_for` 只约束 LLM 调用的超时范式
   - `provider.py` — 熔断 + 重试 + JSON 解析降级（response_format → prompt 约束）

5. **行情上下文能力**（`context_builder.py`）
   - `build_price_action_stats` — 30d 趋势/ATR/RSI（当前行情，非当时）
   - `build_trade_history_patterns` — 胜率按小时/天
   - `build_historical_patterns` — NFP/时段规律
   - `build_macro_context` — 宏观快照/相关性/事件
   - **缺口**：无「单笔交易开仓→平仓窗口」的当时行情特征提取（复盘核心输入）

6. **历史 API 已透传复盘字段**（`history.py:_trade_dict`）
   - `/api/history/trades` 每行已含 `trade_reason`、`pre_trade_snapshot`、`post_trade_analysis`
   - 前端 `history/page.tsx` 的 `Trade` 类型已声明这些字段，表格已展示 `post_trade_analysis.summary_th/outcome`

7. **异步任务底座**：Agent Chat V2（`agent_chat.py` + `services/chat_runs.py`）有成熟队列（202 + 轮询、租约、事件流），但绑定会话模型、偏重

### 缺口清单（用户诉求 vs 现状）
| 缺口 | 说明 |
|------|------|
| 无 AI 深度逐单复盘 | `post_trade_analysis` 是规则摘要，没有"为什么亏/做对做错/经验教训" |
| TradeAccountabilityTracker 未接线 | 分类逻辑存在，无调用点、无持久化 |
| 无复盘 API | `/api/history/trades` 仅列表；无单笔复盘触发/查询端点 |
| 无复盘存储 | 复盘结果无独立落点（`post_trade_analysis` 会被引擎持续覆写） |
| 无复盘前端 UI | 历史页只展示规则摘要，无深度复盘视图 |
| 无"当时行情"上下文 | 复盘需开仓→平仓窗口的行情特征，现无提取函数 |

## 方案草案

### 核心：逐单 AI 复盘（按需触发）
- 输入：Trade 全字段 + `pre_trade_snapshot` + 开仓→平仓窗口行情特征（趋势/ATR/最大逆向幅度/是否扫 SL）+ `post_trade_analysis` 退出原因
- 引擎：`AIClient.complete_json_async` → 结构化复盘
- 输出：问责分类（复用四分类）+ 亏损/盈利根因标签 + 做对做错判断 + 经验教训 + 改进建议 + 泰语/中文摘要
- 存储：**新表 `trade_reviews`**（trade_id FK、status pending/running/completed/failed、review JSON、created_at）——独立生命周期、可审计、避免被引擎覆写
- 触发：手动逐单（点击"复盘"按钮）+ 可选批量（周期窗口内全部未复盘订单）

### 复盘输出结构（草案 JSON）
```json
{
  "classification": "skilled_win|correct_process|lucky_win|real_mistake",
  "reasoning_correct": true,
  "loss_causes": ["逆势开仓", "止损过近", "进场过早", "波动率异常" ...],
  "win_causes": ["顺势交易", "止损保护", "耐心持仓" ...],
  "what_was_wrong": "...",
  "what_was_right": "...",
  "lessons": ["..."],
  "improvement_actions": ["..."],
  "summary_th": "...",
  "confidence": 0.0-1.0
}
```

### 安全约束
- Trade/行情数据在 prompt 中标为 DATA（防注入，复用 `_clean` 清洗）
- verdict/classification 白名单校验（LLM 输出必须落在枚举内）
- LLM 失败 fail-closed → 返回 503/"复盘暂不可用"，绝不编造

### 影响面
- 后端：新 `app/ai/trade_reviewer.py`、新表 `trade_reviews` + alembic 迁移、新路由（挂 history 或独立 `trade_reviews.py`）、prompts 新增复盘模板
- 前端：history/page.tsx 加"复盘"按钮 + 复盘弹窗/抽屉、api.ts 封装、翻译 zh/en
- 测试：复盘服务单测（mock AI）、路由 CRUD、校验白名单、前端 tsc/build
