# 交易纪律风控系统 · 配置操作说明

> 依据：docs/optimization/trading-discipline-enhancement.md（v3.1，两轮八路评审）
> 实施：M1-M5 已完成（2026-10-10），后端 1133 用例通过
> 本文面向：系统使用者（交易者）与运维人员 —— 教你怎么配置来限制「频繁开仓、多空频繁切换、多次爆仓、总想交易」四类交易行为

---

## 一、配置入口总览

纪律系统有三层配置，**生效方式与优先级不同**，先记住这张表：

| 配置层 | 存放位置 | 生效方式 | 谁可改 | 典型用途 |
|--------|---------|---------|--------|---------|
| ① 启动配置 | `backend/.env`（env 变量） | **重启后端生效** | 运维 | 熔断档位、总开关、时区等基线 |
| ② 运行时配置 | Redis `discipline:cfg:*` | **立即生效，无需重启** | 前端/API | 日常调节（次数、冷静期、熔断阈值） |
| ③ 按品种配置 | `symbol_configs` 表（前端 /symbols 页） | 保存后生效 | 交易者 | 单品种手数、SL/TP 模式、volume 防线 |

**优先级**：② 覆盖 ①（Redis 有值则以 Redis 为准），③ 是独立维度（每品种），互不冲突。

**运维逃生门（最重要）**：任何误伤/误配，把 `discipline_gate_enabled` 设为 `false` 即可**一键关闭整个纪律门禁**（记审计事件，不影响交易）。

---

## 二、参数速查表（全部参数 + 默认值 + 作用）

### A. 总开关与时区（第 ① 层，env）

| env 变量 | 默认 | 作用 |
|---------|------|------|
| `DISCIPLINE_GATE_ENABLED` | `true` | 纪律门禁总开关。`false` = 完全豁免所有纪律检查（逃生门） |
| `ENGINE_DISCIPLINE_ENABLED` | `true` | 引擎自动交易通道是否套纪律。`false` = 引擎豁免（仅手动通道强制） |
| `DISCIPLINE_TIMEZONE` | `Asia/Shanghai` | 纪律时区（休息日/展示用；已冻结，勿改） |
| `MT5_SERVER_TZ` | `Europe/Athens` | 旧 bridge naive 时间换算时区（已冻结，勿改） |

### B. 熔断与冷却（第 ① 层 env / 第 ② 层 Redis 均可）

| 参数 | 默认 | 作用 |
|------|------|------|
| `discipline_weekly_loss_limit` | `0.07`（7%） | 周累计亏损 ≥7% → **本周剩余禁开**（rest-of-period） |
| `discipline_monthly_loss_limit` | `0.12`（12%） | 月累计亏损 ≥12% → **当月剩余禁开** |
| `discipline_cooldown_minutes` | `60` | 瞬态闸冷却（点差/频率类，非熔断档） |
| `discipline_impulse_cooldown_hours` | `24` | 冲动冷却阶梯：第2次 24h → 第3次 72h → 第4次本周禁 |
| `discipline_consecutive_loss_week_halt` | `5` | 连亏 ≥5 笔（跨日序列）→ 周停 |

> 熔断判定顺序：**月 > 周 > 日 > 连亏**，同次触发取最高档。日亏 3% 由现有 guardrails 承担。

### C. 开仓次数与频率（第 ①/② 层）

| 参数 | 默认 | 作用 |
|------|------|------|
| `discipline_max_trades_per_day_manual` | `3` | **手动通道**每日开仓上限（你交易计划的红线） |
| `discipline_max_trades_per_day_engine` | `5` | 引擎通道每日开仓上限（独立池，不挤占手动额度） |
| `discipline_max_trades_per_week_manual` | `10` | 手动通道每周开仓上限 |
| `guardrails_max_trades_per_hour` | `5` | 每小时内 ≤5 笔（三通道共用，原硬编码） |
| `guardrails_min_interval_seconds` | `120` | 两次开仓最小间隔 120s（三通道共用） |

### D. 方向切换（第 ①/② 层）

| 参数 | 默认 | 作用 |
|------|------|------|
| `discipline_flip_cooldown_minutes` | `30` | 同品种反手（BUY↔SELL）冷静期；**当日第 2 次反手直接硬拒** |

> 反手确认：第 1 次反手进入 CAUTION（需人工二次确认 + 勾选 ≥2 项确认信号），第 2 次硬拒。

### E. 仓位与保证金（第 ①/② 层 + 按品种）

| 参数 | 默认 | 作用 |
|------|------|------|
| `discipline_max_single_margin_pct` | `0.20` | 单笔保证金 ≤ **equity×20%**（兜底闸；主闸是单笔风险 exposure_cap 2%） |
| `discipline_max_total_margin_pct` | `0.40` | 总持仓保证金 ≤ equity×40% |
| `discipline_max_lots_per_day` | `1.0` | 防拆单：日累计手数 ≤1.0（堵"拆小单绕过单笔上限"） |
| `guardrails_max_lot_per_trade` | `1.0` | 单笔手数上限（env，重启生效；现网 .env=0.01） |
| `guardrails_max_concurrent_total` | `5` | 总并发持仓上限（现网 .env=1） |

### F. 休息日与周末（第 ① 层 env）

| 参数 | 默认 | 作用 |
|------|------|------|
| `discipline_mandatory_rest_days` | `[4]` | 强制休息日（Asia/Shanghai 周几：0=周一…4=周五，默认周五） |
| 周末不留仓 | 内置 | 周六/周日禁止新开仓（针对周末休市品种，crypto 24/7 除外） |

---

## 三、如何配置：三种方式的实操

### 方式 ① 改 env（重启生效）—— 熔断基线

编辑 `backend/.env`，例如把周亏熔断从 7% 收紧到 6%：

```bash
# backend/.env
DISCIPLINE_WEEKLY_LOSS_LIMIT=0.06      # 周亏 ≥6% 停
DISCIPLINE_MONTHLY_LOSS_LIMIT=0.10     # 月亏 ≥10% 停
DISCIPLINE_MAX_TRADES_PER_DAY_MANUAL=2 # 手动每日最多 2 笔
```

> 改完重启后端：`cd backend && ./start-backend.sh`（或按你的部署方式）。

### 方式 ② Redis 运行时配置（立即生效）—— 日常调节

无需重启，写 Redis 即生效（多 worker 一致）。用 API 或 redis-cli 都行：

```bash
# 用 API（需登录 token）：
curl -X PUT http://localhost:8002/api/trading/discipline/config/max_trades_day_manual \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"value": 2}'

# 或用 redis-cli 直接写：
redis-cli SET "discipline:cfg:max_trades_day_manual" "2"
redis-cli SET "discipline:cfg:weekly_loss_limit" "0.06"
redis-cli SET "discipline:cfg:gate_enabled" "false"   # 应急关闭整个门禁
```

**可运行时配置的字段**（写 `discipline:cfg:<field>`）：

| field | 对应参数 | 类型 |
|-------|---------|------|
| `gate_enabled` | 总开关 | bool |
| `engine_enabled` | 引擎开关 | bool |
| `max_trades_day_manual` | 手动日上限 | int |
| `max_trades_day_engine` | 引擎日上限 | int |
| `max_trades_week_manual` | 手动周上限 | int |
| `flip_cooldown_minutes` | 反手冷静期 | int |
| `impulse_cooldown_hours` | 冲动冷却 | int |
| `consecutive_loss_week_halt` | 连亏周停 | int |
| `cooldown_minutes` | 瞬态冷却 | int |
| `weekly_loss_limit` | 周熔断 | float |
| `monthly_loss_limit` | 月熔断 | float |
| `max_single_margin_pct` | 单笔保证金 | float |
| `max_total_margin_pct` | 总保证金 | float |

**查询当前生效值**：`GET /api/trading/discipline/config`

### 方式 ③ 按品种配置 —— 单品种防线

前端 `/symbols` 页编辑该品种（也可 API 改 `symbol_configs`）：
- `max_lot`：该品种单笔手数上限
- `volume_min/max/step`：券商手数网格（低于 min 直接拒）
- `sl_mode/sl_floor/sl_cap`：止损标准化
- `margin_mode`：保证金模式（leverage / fixed_per_lot，期货每手固定保证金用）

---

## 四、针对四大行为问题的推荐配置组合

### 问题 1：频繁开仓
```bash
# 收紧次数 + 间隔
DISCIPLINE_MAX_TRADES_PER_DAY_MANUAL=3   # 每日最多 3 笔（你的计划红线）
DISCIPLINE_MAX_TRADES_PER_WEEK_MANUAL=10 # 每周最多 10 笔
GUARDRAILS_MAX_TRADES_PER_HOUR=3         # 每小时最多 3 笔
GUARDRAILS_MIN_INTERVAL_SECONDS=300      # 两次开仓至少隔 5 分钟
```
效果：次数用完即止；每小时/间隔双限；被拒单计入手痒信号。

### 问题 2：多空频繁切换
```bash
DISCIPLINE_FLIP_COOLDOWN_MINUTES=30      # 反手冷静期 30 分钟（你的计划）
```
效果：BUY 后 30 分钟内 SELL 被拒；当日第 2 次反手直接硬拒；第 1 次反手必须人工确认并勾选 ≥2 项确认信号；同品种反向持仓在审查层标 CAUTION。

### 问题 3：多次爆仓
```bash
# 熔断阶梯（你的计划：单日3-4% / 单周6-8% / 单月10-12%）
GUARDRAILS_MAX_DAILY_LOSS=0.03           # 单日亏 3% 停
DISCIPLINE_WEEKLY_LOSS_LIMIT=0.07        # 单周亏 7% → 本周停
DISCIPLINE_MONTHLY_LOSS_LIMIT=0.12       # 单月亏 12% → 当月停
DISCIPLINE_CONSECUTIVE_LOSS_WEEK_HALT=5  # 连亏 5 笔 → 周停
# 仓位防线
GUARDRAILS_MAX_LOT_PER_TRADE=0.05        # 单笔 ≤0.05 手
GUARDRAILS_MAX_CONCURRENT_TOTAL=1        # 最多同时 1 仓
DISCIPLINE_MAX_SINGLE_MARGIN_PCT=0.20    # 单笔保证金 ≤ equity 20%
DISCIPLINE_MAX_LOTS_PER_DAY=0.3          # 日累计 ≤0.3 手（防拆单）
```
效果：触发任一档 → 对应周期剩余时间禁开新仓；手数倒推防重仓；拆小单绕过被封。

### 问题 4：总想交易（手痒）
```bash
DISCIPLINE_MANDATORY_REST_DAYS=[4]       # 每周五强制休息（本地周五）
DISCIPLINE_IMPULSE_COOLDOWN_HOURS=24     # 冲动冷却 24h 起步，阶梯升级
# 周末
（内置）                                  # 周六/周日禁新开仓
```
效果：周五禁开；被纪律拦截达第 2 次 → 24h 冷却、第 3 次 72h、第 4 次本周禁；周末不开新仓；复盘页看纪律评分。

---

## 五、纪律状态与复盘统计（怎么看效果）

| 端点 | 内容 |
|------|------|
| `GET /api/trading/discipline/status` | 今日/本周开仓次数（分通道）、冷却剩余分钟、熔断 halt 与解禁时间、是否休息日、时区 |
| `GET /api/trading/discipline/stats?days=30` | 复盘统计：开仓次数、违规次数、胜率、最大回撤、**纪律评分**（100 起，违规/超频/低胜率扣分） |
| `GET /api/trading/discipline/config` | 全部运行时配置当前生效值 |

前端 `/trading` 页会展示"被拦原因 + 解禁时间 + 今日次数 N/3"；拒绝原因分类徽章见审查历史页。

---

## 六、部署与启用检查清单

纪律代码已就位，**三步启用**：

1. **重启 MT5 bridge（Windows 侧）**：时间输出改 UTC + `/account` 补 leverage 字段
2. **存量数据回填**（一次性，修正历史订单时区）：
   ```bash
   cd backend
   .venv/bin/python scripts/backfill_trade_timezone.py --dry-run   # 先看影响行数
   .venv/bin/python scripts/backfill_trade_timezone.py             # 确认后执行
   ```
3. **确认后端已加载配置**：`GET /api/trading/discipline/config` 能返回默认值；交易页下单测试一条被拦截路径（如周五/次数超限）看拒绝文案。

> 注意：环境变量名是 `DISCIPLINE_*` 大写（pydantic 自动映射），Redis cfg key 是小写字段名。改 env 需重启，改 Redis 立即生效。

---

## 七、常见问题排查

| 现象 | 可能原因 | 处理 |
|------|---------|------|
| 单下不了但没提示纪律 | `discipline_gate_enabled=false` | 检查 env / `discipline:cfg:gate_enabled` |
| 引擎不开仓了 | 引擎被纪律拦（如周熔断/次数满） | 临时 `engine_enabled=false`（Redis）或调大 engine 池配额 |
| 时间显示还是差几小时 | bridge 未重启 / 回填未执行 | 按第六节步骤 1+2 |
| 想临时放开某个限制 | 改 Redis cfg | 写 `discipline:cfg:<field>` 立即生效 |
| 误伤太多想整体关 | 逃生门 | `redis-cli SET "discipline:cfg:gate_enabled" "false"` |

---

*配置说明 v1（2026-10-10）。参数名以 backend/app/config.py 与 app/services/discipline_gate.py 为准；运行时字段见 `_RUNTIME_*_FIELDS` 映射。*
