# SETS 调研发现 — findings.md

日期：2026-10-12
调研对象：https://gitclone.com/github.com/Shelpid/SETS.git
评估目标：判断 SETS 能否应用到 ai-trading-agent 项目

---

## 一、SETS 是什么

**SETS MACHINE (Self Evolving Trading System)** — 一个**纯前端、纯浏览器运行**的自进化交易模拟器。
- 用遗传算法「繁殖」网格-DCA（grid-DCA）交易策略
- 在真实 BTCUSDT 小时数据（2399 根）上回测
- 每 4.8 秒进化一代：交叉 / 变异 → 回测 → 样本外（OOS）门控 → 淘汰弱者
- **没有后端、没有数据库、没有 API key、没有实盘接入** — 一切在浏览器 ES modules 中运行

### 技术栈
| 层 | 实现 |
|----|------|
| 语言 | 纯 JavaScript (ES modules)，无构建、无 npm 依赖 |
| 数据 | `dist/data/candles.js` 内嵌 2399 根 BTCUSDT 1h K 线（Binance 导出） |
| 引擎 | `dist/engine/bot.js` (网格回测) + `evolution.js` (GA) + `rng.js` (种子随机) |
| 工具 | `tools/fetch_data.py` (Python 标准库从 Binance 拉数据) |
| 测试 | `node --test` (8 个测试，含无前视、确定性、可复现验证) |
| License | MIT |

### 代码规模（全部约 3700 行）
- `engine`：bot.js 126 / evolution.js 147 / rng.js 16 / series.js 57
- `app.js` 400（进化循环调度 + 状态机）
- `ui/draw.js` 350（canvas 绘制）
- `data/candles.js` 2403（内嵌 K 线数据，占大头）

---

## 二、核心算法详解

### 1. 基因编码（GENES，一维数组，8 个基因）
网格-DCA 策略被参数化为 8 个最优基因：

| Gene | 范围 | 含义 |
|------|------|------|
| `family` | 0-3 (int) | 入场逻辑：0=动量(收盘>前收盘且z>Z) / 1=均值回归(z<-Z) / 2=波动突破(收破N根前高) / 3=区间网格(安静带内) |
| `lookback` | 10-200 (int) | 滚动窗口(h) |
| `entryZ` | 0.2-2.5σ | 入场阈值 |
| `levels` | 2-8 (int) | 网格买入挂单数 |
| `spacing` | 0.3-3% | 网格间距 |
| `mult` | 1-2× | 每层仓位倍增系数 |
| `tp` | 0.3-4% | 止盈（相对均价） |
| `stop` | 1-12% | 止损（相对最深网格层） |

### 2. 遗传循环（evolution.js，每代 step()）
```
1. 精英保留：总排名前 (E-4) + 每种 family 最优 1 只 → Elites
2. 注入移民：随机 I=8 只全新基因组
3. 物种配额：按 family 排名给权重 [0.4,0.26,0.2,0.14] 分配后代名额
   → 防止单一幸运家族垄断种群
4. 亲本选择：每个物种内部 3 轮锦标赛 (tournament)
5. 交叉：uniform crossover（每基因 50% 概率取父/母）
6. 变异：Gaussian 变异（p=0.18, scale=0.12 × 基因范围），钳位到边界
7. 回测：每个后代在 train 窗口 + 独立 out-of-sample 窗口都跑 backtest
8. 排名：按 train fitness 排序
```

### 3. Fitness 与 OOS 门控（防过拟合的关键）
```js
fitness(m)   = m.ret - 0.6 * m.maxDD - (m.trades < 4 ? 0.15 : 0)
passesGate(m) = m.ret > 0.01 && m.maxDD < 0.1 && m.trades >= 3 && m.winRate >= 0.5
```
- **train**, **val 两个窗口（split=0.7）**：train 决定 fitness / 排名；**val（未见过 30%）决定谁能「活过门禁」成为 leader**
- leader 只在 passesGate 的配置里选，且「克隆不算晋升」（基因组相同不重复推广）
- Kelly 仓位：`base = cash / Σ(mult^k)`

### 4. 回测引擎（bot.js）严谨性亮点
1. **信号用 j 收盘，执行用 j+1 开盘** → 无未来函数（VALIDATION 有专门测试：改 candle 501 不影响 500 信号）
2. **同一类 GridBot 跑回测和纸面交易** → 展示/回测口径一致（我方有相似的 backtest_consistency 测试）
3. **每根 bar 内 adverse-first 顺序**：先网格成交（low≤挂单价）→ 再止损 / 止盈 → 真实模拟 K 线内的不利优先
4. 手续费 FEE=0.0004/fill 计入
5. Qty 计算含费：`qty += usd*(1-FEE)/price`
6. 测试窗口结束 flatten（标记到市平仓，未实现亏损计入）
7. 指标：ret / maxDD / trades / winRate / payoff / exposure
8. `mulberry32` 种子随机 + `gauss` — 确定性（同 seed 同结果）

### 5. 品种与数据
- 单品种：BTCUSDT 小时线（2399 根）。`fetch_data.py` 可拉 Binance 更新。

---

## 三、我方项目现状对照

| 能力 | SETS | 我方 ai-trading-agent | 差距 |
|------|------|----------------------|------|
| 策略数量 | 单一网格-DCA（4 变体 family） | 11 策略（EMA/RSI/Breakout/MeanReversion/ML/DCA/Grid/MomentumRank/PairSpread/RiskParity/Ensemble）+ MTF + regime | SETS 远不如 |
| K 线数据 | 内嵌单品种 BTCUSDT (2399 bars) | 实时 MT5 接入 + 多品种（GOLD/OIL/BTC/USDJPY）+ Redis 缓存 | SETS 不可比 |
| 回测引擎 | 极简 bar 循环，含费、无前视 | 完整 BacktestEngine + SL/TP + 合约规模 + AI 过滤 + 盈亏比 | 我方更完善 |
| 参数优化 | **遗传算法**（147 行） | **笛卡尔积穷举** grid_search（MAX_COMBINATIONS=500）+ 注释截断 | **SETS 在「高维大空间搜索」上更优** |
| 样本外验证 | 硬门控 passesGate（ret>1% 且 DD<10% 且 ≥3 笔 且 胜率≥50%）| walk_forward_test（5 折 OOS）+ monte_carlo + overfitting 评分 | 我方更全 |
| 过拟合防线 | OOS 门控 + 克隆不晋升 + 物种配额 | walk-forward + OOS 组合分 | 各有特色 |
| 多目标 | ret - 0.6·DD | sharpe×PF×win_rate | 可借鉴 DD 权重 |
| 实盘/纸面 | 仅纸面（历史 K 线回放）| **真 MT5 实盘 + shadow/paper/micro/live 渐进** | 我方碾压 |
| 前端 | canvas 直观进化可视化 | Next.js 富面板（无 GA 可视化） | **SETS 的可视化是亮点** |

### 我方关键实现（已确认）
- `backend/app/backtest/optimizer.py:56 grid_search` — 笛卡尔积穷举，`MAX_COMBINATIONS=500` 截断
- `backend/app/backtest/walk_forward.py:92 walk_forward_test` — 锚定/滑动窗口 OOS 验证（我方已有 OOS！）
- `backend/app/ai/strategy_optimizer.py` — AI(Claude) 建议参数 + `_backtest_compare` 回测验证 + `PARAM_RANGES` 钳位
- `backend/app/strategy/grid.py + dca.py` — 已有独立的 Grid/DCA 策略实现
- `mcp_server/tools/strategy_gen.py` — `STRATEGY_PROFILES` 每策略参数范围

---

## 四、评估结论

### ✅ 可以借鉴/移植的（核心价值）
**1. 遗传算法搜索（最有价值）**
- 我方 `grid_search` 用笛卡尔积穷举，参数维度一旦超过 4-5 个，组合数组合爆炸被 `MAX_COMBINATIONS=500` 硬截断。
- SETS 的「锦标赛选择 + 均匀交叉 + 高斯变异 + 物种配额」是**成熟可移植的 GA 骨架**，同样依赖「对每个配置跑一次 BacktestEngine」，只是选择下一步机体的方式不同。
- 移植方案：在我方 `backend/app/backtest/` 新增 `genetic_search.py`，复用现有 `BacktestEngine` / `score_result` / `walk_forward_test`，用 SETS 的思路做参数搜索；API 前端加「遗传优化」按钮。预计 ~200-300 行 Python，完全站得住。

**2. OOS 硬门控写法（参考）**
- 我方 walk_forward 已覆盖 OOS，但 SETS 的 `passesGate`（ret>1% 且 DD<10% 且 ≥3 笔 且 胜率≥50%）+「克隆不晋升」是简练可抄的「继续追踪候选 leader」的原语。可在我方策略切换 guard（`strategy_switch_guard.py`）或优化结果采纳逻辑里参考。

**3. K 线内 adverse-first 成交顺序（若我方未实现）**
- 我方 BacktestEngine 的 `_check_sl_tp` 每 bar 先查 SL 再 TP，但**没有网格逐层成交**这一步（我方 grid 策略是逐根 K 线产生单信号，不是 K 线内多个网格挂单成交）。若未来要真正复刻 SETS 网格，可以抄这个顺序。

**4. 进化过程可视化（前端亮点）**
- SETS 的 gene pool / fitness 历史 / 进化动画是直观的前端交互。我方 `/backtest` `/ml` 页可借鉴「每代最优 vs OOS 门控」可视化，强化 AI/参数调优的可解释性。

### ❌ 不可直接采用
1. **技术栈不匹配** — SETS 是纯浏览器 JS + 无构建 ES modules；我方是 Python FastAPI + pandas + 确定性回测。JS 代码不能直接拿来用，只能「翻译成 Python」。
2. **单一策略** — SETS 只有 long-only 网格 DCA；我方早已有多策略 + 多资产 + MT5 实盘。引入 SETS 本体是开倒车。
3. **无实盘/无风控** — SETS 无账户、无杠杆、无实盘执行、无持仓管理；我方有 ManualOrderGate 防火墙、账户切换、券商别名解析。无法替代我方任何一层。
4. **数据内嵌** — 2399 根 BTC 1h 对我方 MT5 实时多品种无意义。

### 🎯 一句话结论
> SETS 不能「应用」进本项目，但它贡献了一个**成熟、极简、可移植的遗传算法参数搜索引擎**（147 行 JS → 翻译成 Python ~200-300 行），可补上我方 `grid_search` 在高维参数空间上的组合爆炸短板。其 OOS 硬门控、K 线内 adverse-first 网格成交、进化可视化也有参考价值。

## 若采纳的落地建议（低风险）
1. **新增 `backend/app/backtest/genetic_search.py`**：GA 骨架（种子随机 mulberry32/gauss → 锦标赛 → 交叉 → 高斯变异 → fitness），复用 BacktestEngine 评估每组参数。参数空间用 STRATEGY_PROFILES 的 param_ranges 定义。
2. **API**：`/api/backtest/genetic-optimize` POST（strategy_name, param_ranges, pop_size, generations），返回 per-gen 最优 + best params + OOS 指标，与现有 `/optimize` 并存（用户可选择穷举或遗传）。
3. **前端**：backtest 页增加「遗传优化」tab  + 每代 fitness 曲线图。
4. **沿用防线**：遗传得到的 best params 仍走 walk_forward OOS + overfitting 评分（我方更强的防线不用丢）。
5. **测试**：determinism（同 seed 同结果）、基因边界钳位、no-look-ahead、克隆不晋升 — 均有前例可抄（tests/engine.test.mjs → pytest）。

## 已核查：SETS 的诚实声明（README/VALIDATION 原文）
- 结果表仅来自一个 30 天 OOS 窗口，不是持久优势证据，同期 buy&hold 收益更高 → 作者自己承认无稳定 alpha
- 无任何实盘连接，纯历史纸面
- 复选框未验证物理设备多点触控
## 2026-10-12 新增：修改止损报错调查

### 报错
```
SL 4175.0 must be above entry 4178.08 for SELL
```

### 来源
`backend/app/services/manual_order_gate.py:631-634`（`modify_position_sltp` 方向合法性校验），
不是 broker 返回。该错误由我方后端拦截产生。

### 根因
方向校验用 **entry（开仓价）** 作基准，而非 **现价（current_price）**：
- SELL: `new_sl <= entry` → 拒绝（要求 SL > 开仓价）
- 但用户持仓 = 4178.08 做空 SELL，现价已跌至 ~4175（大幅盈利）
- 用户想设 SL=4175 锁盈。对 SELL，SL 本就该在现价上方、且可能低于开仓价（盈利锁定时）
- 用开仓价做基准，导致盈利仓永远无法把 SL 收进盈利区（SL < 开仓价）→ 误伤

### 正确基准（MT5 语义）
SL/TP 合法性判定应对**现价**而非开仓价：
- SELL（做空，price_current = bid）：SL 必须 > 现价，TP 必须 < 现价
- BUY（做多，price_current = ask）：SL 必须 < 现价，TP 必须 > 现价
- 开仓价只影响「距离」计算（漂移锚点/风控），不影响方向合法性

### 影响范围
- 所有盈利仓（价格越过开仓价后）收紧/移动 SL 都会被误拒，除非 SL 仍停在开仓价同侧
- 现有测试 `test_sltp_direction_validated`（BUY SL≥entry 拒绝）用例输入恰好仍符合旧语义，
  因为 BUY 用例 entry=2000、sl=2010（现价未给，测试只 mock 了 entry）；SELL 侧无覆盖

### 待确认
- `get_positions` 返回含 `current_price` 字段（mt5_bridge/main.py:489），改持仓时可用现价做方向基准
- 前端持仓表用 `o.price_open?.toFixed(2)` 显示（frontend/app/trading/page.tsx:756），但这是显示
