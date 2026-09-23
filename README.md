<div align="center">

<img src="docs/logo/logo.png" alt="AI Trading Agent" width="160" />

# AI Trading Agent

**多品种自主交易平台** · FastAPI + Next.js + MT5 Bridge + Claude AI + LightGBM

交易 **GOLD** · **OILCash** · **BTCUSD** · **USDJPY**（通过 MetaTrader 5）

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Next.js](https://img.shields.io/badge/Next.js-16-000000?logo=nextdotjs&logoColor=white)](https://nextjs.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white)](https://redis.io/)
[![License](https://img.shields.io/badge/License-Private-red)]()

[快速上手](#-快速上手5-分钟跑起来) · [操作流程](#操作流程从配置到实盘) · [页面导览](#-页面导览) · [运维手册](#-运维手册) · [环境变量](#-环境变量)

</div>

---

> **本文档是操作手册**：看完就能知道这个项目怎么启动、怎么配置品种、怎么制定与优化策略、怎么回测、怎么训练模型、怎么上实盘。
> 深入的技术审计见 [`docs/SYMBOL-PARAMETERS-TECH.md`](docs/SYMBOL-PARAMETERS-TECH.md)，品种参数运营者指南见 [`docs/SYMBOL-PARAMETERS.md`](docs/SYMBOL-PARAMETERS.md)。

---

## 📐 系统全景：三个进程 + 两个存储

```
┌────────────────────────────┐      ┌────────────────────────────────────────────┐
│  前端 Frontend (Next.js)    │      │  后端 Backend (FastAPI)  — 本机 Mac / 服务器   │
│  http://localhost:3000      │      │  http://localhost:8002   /docs 接口文档        │
│  仪表盘 · 回测 · ML · 设置   │◄────►│  ├─ API 路由（25 文件 / 114 端点）              │
└────────────────────────────┘ HTTPS │  ├─ BotManager（每品种一个 BotEngine）          │
           ▲                 + WS   │  ├─ Scheduler（定时任务，见 §定时任务）          │
           │                        │  ├─ 策略引擎（11 策略 + 行情状态切换）           │
           │                        │  ├─ ML 训练器（LightGBM，每品种一个模型）         │
           │                        │  ├─ AI 优化器（Claude 出参数建议 + 回测验证）     │
           │                        │  └─ 风控（熔断、手数计算、确认门、AI 情绪过滤）   │
           │                        └──────────┬────────────────────────────┬────────┘
           │                                   │ HTTP (x-bridge-key)         │ SQL / RESP
           │                                   ▼                            ▼
           │                        ┌─────────────────────┐      ┌─────────────────────────┐
           │                        │  MT5 Bridge (:8001)  │      │ PostgreSQL + Redis       │
           │                        │  Windows 主机专用     │      │ 远端 Tailscale / 本机 Docker│
           │                        │  MetaTrader5 仅支持  │      │ DATABASE_URL / REDIS_URL  │
           │                        │  Windows             │      └─────────────────────────┘
           │                        └─────────────────────┘
           │                                   │ MetaTrader5 SDK
           │                                   ▼
           │                        ┌─────────────────────┐
           └───────────────────────►│  MetaTrader 5 终端    │  ← 真实下单在这里发生
                                   │  （券商 XM Global）   │
                                   └─────────────────────┘
```

| 组件 | 地址/端口 | 谁在跑 | 说明 |
|---|---|---|---|
| 前端 | `http://localhost:3000` | 本机/部署机 | Next.js 生产模式或 `npm run dev` |
| 后端 | `http://localhost:8002` | 本机/部署机 | FastAPI；`/docs` 有 Swagger 接口文档 |
| MT5 Bridge | `http://<win-host>:8001` | **Windows 主机** | 唯一接触 MT5 的地方；MetaTrader5 Python 包只支持 Windows |
| PostgreSQL | 远端 `100.72.200.33:15432`（Tailscale）或本机 `5434` | 远端/本机 Docker | 交易、行情、ML 模型、AI 日志全在这 |
| Redis | 远端 `100.72.200.33:16379/2` 或本机 `6380` | 远端/本机 Docker | 缓存、熔断状态、任务队列、pub/sub |

> ⚠️ **本机开发无 Docker 时**：Postgres/Redis 用远端 Tailscale 地址（见 `backend/.env.example`）。
> 有 Docker 时 `docker-compose up -d` 起本机实例（Postgres:5434 / Redis:6380，改 `.env` 指向它们）。

### 局域网 / Tailscale 访问

前端 `api.ts` / `websocket.ts` **运行时自动推导后端地址**（`协议://<访问前端的主机>:8002`），因此无需把 `IP/localhost` 写死进 `.env`：

- **本机**：`http://localhost:3000`（API → `http://localhost:8002`）
- **局域网设备**：`http://<Mac-IP>:3000`（API 自动指向 `http://<Mac-IP>:8002`）
- **Tailscale 设备**：`http://<Tailscale-IP>:3000`（API 自动指向 `http://<Tailscale-IP>:8002`）

> 后端端口默认 8002，可通过 `frontend/.env` 的 `NEXT_PUBLIC_BACKEND_PORT` 覆盖；公网反代场景可用 `NEXT_PUBLIC_API_URL` 显式覆盖（见 `frontend/.env` 注释）。
> 后端已监听 `0.0.0.0` 并允许常见局域网/Tailscale Origin 的 CORS——换 IP 后如遇跨域报错，把新 IP 追加进 `backend/.env` 的 `CORS_ORIGINS`。

---

## 🚀 快速上手（5 分钟跑起来）

### 0. 前置条件

| 需要 | 版本 | 备注 |
|---|---|---|
| Python | 3.12+ | 后端 + MT5 Bridge 各一个 venv |
| Node.js | 22+ | 前端 |
| Docker（可选） | — | 本机 Postgres/Redis；没有就用远端地址 |
| Windows 主机 + MT5 | — | **只做实盘交易时需要**；配置/回测/训练不需要 |

### 1. 一键启动（推荐）

```bash
./start-all.sh                # 启动后端+前端，检测 MT5 Bridge 连通性
./stop-all.sh                 # 停止全部（按 PID + 端口兜底）
```

启动后：
- 后端 `http://localhost:8002`，接口文档 `http://localhost:8002/docs`
- 前端 `http://localhost:3000`，日志在 `logs/backend.log` / `logs/frontend.log`

### 2. 分开启动（开发时常用）

```bash
# 后端（会自动先跑 alembic 迁移）
cd backend
cp .env.example .env          # 填入 DATABASE_URL / DATABASE_URL_SYNC / REDIS_URL 等
./start-backend.sh            # 或 .venv/bin/uvicorn app.main:app --reload --port 8002

# 前端
./start-frontend.sh           # 或 cd frontend && npm run dev

# MT5 Bridge（只在 Windows 主机上执行）
./start-mt5bridge.sh
```

### 3. 环境准备细节

```bash
# 后端 venv + 依赖 + 迁移
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head        # 迁移到最新 schema（25 个版本）
# 注意：alembic 需要 DATABASE_URL_SYNC 且要在 backend/ 目录下执行（PYTHONPATH=backend）

# 前端依赖
cd frontend
npm install
```

### 4. 登录

- 打开 `http://localhost:3000/login`
- 账号：`.env` 里的 `AUTH_USERNAME`（默认 `admin`），密码由 `AUTH_PASSWORD_HASH` 决定
- 前端把 JWT 存 localStorage；所有 `/api/*` 都要带 `Authorization: Bearer <token>`

---

## 🗺 页面导览（18 个页面）

| 路由 | 页面 | 干什么 |
|---|---|---|
| `/dashboard` | 交易仪表盘 | 实时报价、持仓、盈亏、AI 观点、多品种切换 |
| `/symbols` | **品种管理** | 加/删/改品种、SL/TP 参数、ML 参数、券商校验 |
| `/backtest` | **回测工作室** | 回测 / 优化器 / 步进测试 / 蒙特卡洛 / 显著性 / 过拟合 |
| `/ml` | **ML 模型** | 训练模型、查看指标、漂移、校准、预测 |
| `/insights` | AI 洞察 | 新闻情绪 + **AI 策略优化报告（可一键应用）** |
| `/settings` | 设置 | 交易模式、每品种策略、风控参数、**上线模式（rollout）** |
| `/history` | 历史交易 | 已平仓记录、绩效分析、归档 |
| `/quant` | 量化风控 | VaR、波动率、相关性、组合、压力测试 |
| `/macro` | 宏观数据 | FRED 指标、经济日历、相关性 |
| `/activity` | AI 活动流 | 所有 agent 决策时间线 |
| `/ai-usage` | AI 用量 | 各 agent token 消耗与成本 |
| `/agent-prompts` | 交易大厅 | 自定义每个 agent 的系统提示词 |
| `/integration` | 集成状态 | DB/Redis/MT5/Vault/Telegram 连通性诊断 |
| `/notifications` | 通知中心 | 事件历史 |
| `/db-health` | DB 健康 | 数据库状态 |
| `/setup` · `/login` | 认证 | 首次设置 / 登录 |

---

## 🔧 操作流程：从配置到实盘

### 流程 ① 品种配置（先做这个）

> 完整参数说明（含单位陷阱、盈亏比、ATR 原理）见 [`docs/SYMBOL-PARAMETERS.md`](docs/SYMBOL-PARAMETERS.md)，强烈建议先读。

**入口**：`/symbols` 页面 → 新增/编辑品种。

**关键字段分两组，性质完全不同**：

| 字段 | 类型 | 用在哪 | 影响实盘？ |
|---|---|---|---|
| `sl_atr_mult` / `tp_atr_mult` | 倍数（×ATR） | **实盘 + 回测**的止损/止盈 | ✅ 是 |
| `sl_mode` / `sl_floor` / `sl_cap` | 模式 | 止损夹逼（可设下限/上限） | ✅ 是 |
| `tp_mode` / `target_r_multiple` | 模式 | 止盈按固定盈亏比 R | ✅ 是 |
| `ml_tp_pips` | 点数 | 仅**训练打标签** | ❌ 否 |
| `ml_sl_pips` | 点数 | **当前完全不生效**（保留字段） | ❌ 否 |

**一句话**：`ml_*` 是给模型"上课"用的，`*_atr_mult` 才是实盘亏赚用的。

**核心链路**（改完看这里理解效果）：

```
ATR（M15，近14根平均波幅）
   → 止损距离 = ATR × sl_atr_mult（可 clamp 到 [sl_floor, sl_cap]）
   → 止盈距离 = ATR × tp_atr_mult（或 = R × 实际止损距离）
   → 手数 = 风险预算 ÷ (止损距离 + 滑点缓冲) ÷ 合约规模
   → 单笔盈亏 ≈ 价格变动 × 手数 × 合约规模
```

**出厂默认值**：

| 品种 | 周期 | pip_value | sl_mult | tp_mult | 合约规模 | ml_tp_pips | ml 周期 |
|---|---|---|---|---|---|---|---|
| GOLD | M15 | 1.0 | 1.5 | 2.0 | 100 | 10 | M15 |
| OILCash | M15 | 10.0 ⚠️ | 1.5 | 2.0 | 100 | 0.5 | M15 |
| BTCUSD | M15 | 1.0 | 2.0 | 3.0 | 1 | 500 | H1 ⚠️ |
| USDJPY | M15 | 100.0 ⚠️ | 1.5 | 2.0 | 100000 | 0.3 | M15 |

> ⚠️ **单位陷阱**：页面叫"点数"，但乘的是 `pip_value`。GOLD `pip_value=1.0`，填 `ml_tp_pips=1500` 系统理解为 **$1500** 而不是 MT5 口径的 $15——差 100 倍，且会被校验器拒绝（见下）。
> ⚠️ OILCash / USDJPY 的 `pip_value` 出厂值异常（通不过自家校验），见 `docs/SYMBOL-PARAMETERS.md §5`。

**为什么 1500 会被拒绝**：保存/训练时系统校验 `ml_tp_pips × pip_value` 相对单根 K 线波幅的比例：
- 拒绝区间：障碍 < 0.15× 或 > 6× 平均波幅（GOLD 出厂 `10×1.0=$10` ≈ 1.1×，通过）
- 这是"训练屏障 vs 波动"的校验，**与盈亏比无关**。

**操作建议**：
1. 想改实盘盈亏比 → 只改 `sl_atr_mult` / `tp_atr_mult`（两者相除 = 盈亏比），或改用 `tp_mode=rr` 填 `target_r_multiple`
2. 改完 → 点「校验」→ 跑回测验证（流程 ③），回测与实盘同口径
3. 用「券商目录」从 MT5 实时规格创建品种（自动回填 pip_value / 合约规模 / 步进）

---

### 流程 ② 制定策略

**策略清单**（`/api/strategy/available` 实时可查）：

| 策略名 | 说明 | 参数 |
|---|---|---|
| `ema_crossover` | 双均线金叉死叉 | fast_period, slow_period |
| `rsi_filter` | RSI 超买超卖 | rsi_period, rsi_overbought, rsi_oversold |
| `breakout` | 通道突破 | 通道长度、ATR 过滤 |
| `mean_reversion` | 均值回归 | 布林带参数 |
| `ml_signal` | **LightGBM 模型信号**（需先训练） | 模型文件 + 置信度阈值 |
| `dca` | 定投 | 每根 K 线加仓 |
| `grid` | 网格 | 网格间距、层级 |
| `momentum_rank` | 多品种动量排序 | — |
| `pair_spread` | 配对价差 | 需要 cointegration 验证 |
| `risk_parity` | 风险平价 | — |
| `ensemble` | 组合多个策略 | 如 `ema_crossover:0.3,breakout:0.7` |

**在哪里选策略**：`/settings` 页面 → 每个品种一个下拉框：
- 选 `ai_autonomous` = **AI 全权决策**（不跑策略引擎，由 Claude agent 决定买卖）
- 选具体策略名 = 策略引擎跑信号 + AI 情绪过滤（推荐默认）

**全局交易模式**（`backend/.env` → `trading_mode`，页面也改得了）：
- `strategy`（默认）：策略优先，AI 只做确认/过滤
- `ai_autonomous`：AI 自主决策，`process_candle` 跳过策略引擎

**制定新策略的一般路径**：
1. 在 `/backtest` 用现有策略 + 参数跑回测（流程 ③）
2. 用「优化器」网格搜索参数组合
3. 用「步进测试」验证参数稳健性（防过拟合）
4. 用「过拟合」页拿综合过拟合评分
5. 让 AI 出优化建议并一键应用（见流程 ④）
6. 参数满意后，到 `/settings` 给品种换上该策略

---

### 流程 ③ 回测（如何回测）

**入口**：`/backtest` 页面，6 个 Tab：

| Tab | 干什么 | 关键输入 |
|---|---|---|
| **回测** | 单次回测，看该策略在历史数据上的表现 | 品种、周期、策略、参数、数据源、风控参数 |
| **优化器** | 网格搜索，遍历参数组合找最优 | 参数网格、min_trades |
| **步进测试** | Walk-forward：分段训练/测试，验证稳健性 | n_splits、train_pct |
| **蒙特卡洛** | 打乱交易顺序模拟，看结果分布 | n_simulations |
| **显著性检验** | 置换检验 + 协整检验，判断策略是否纯靠运气 | n_permutations |
| **过拟合** | 综合评分（步进 + 置换 + 蒙特卡洛 + 参数稳定性） | — |

**数据源**：
- `mt5`：从 MT5 Bridge 拉实时/最近 K 线（默认，需 bridge 在线）
- `db`：从数据库读历史行情（需要先收集数据；回测页会显示是否有历史数据）

**回测输出指标**：总交易数、胜率、净利润、最大回撤、Sharpe、Profit Factor、资金曲线、AI 过滤掉的信号数、前 100 笔明细。

**重要口径**：
- ✅ 回测**读取品种配置**（SL/TP 倍数、合约规模、点值、clamp/R 模式）——与实盘同口径，改品种参数后回测能看到效果
- ✅ 回测公式有版本号 `BACKTEST_FORMULA_VERSION=v2`；AI 优化报告也会带上生成时的版本，**版本不匹配时不能一键应用**（防止拿旧口径的建议覆盖新口径）
- ⚠️ 跨版本对比历史回测数字时，请用**胜率、盈亏比、期望值**等相对指标，绝对盈亏会随成本口径修正而变

**后端 API**（前端页面调的就是这些）：

```bash
POST /api/backtest/run              # 单次回测
POST /api/backtest/optimize         # 网格优化
POST /api/backtest/walk-forward     # 步进测试
POST /api/backtest/monte-carlo      # 蒙特卡洛
POST /api/backtest/permutation-test # 置换检验
POST /api/backtest/cointegration    # 协整检验（配对策略用）
POST /api/backtest/overfitting-score # 综合过拟合评分
POST /api/backtest/compare          # 多策略对比
```

---

### 流程 ④ 策略优化（AI + 回测验证）

**三种优化方式**：

**A. 网格优化（页面操作）**：`/backtest` → 优化器 Tab → 填参数取值范围 → 跑。适合参数空间小的策略。

**B. AI 优化（推荐）**：`/insights` 页面 → 「运行优化」：
1. 系统把近 7 天真实交易绩效 + 当前参数发给 Claude
2. Claude 给出 `suggested_params` + 置信度 + 理由
3. 系统自动用这些参数**回测对比**：只有新参数回测更优才标记 `applied=true`
4. 页面展示「当前 vs 建议」对照表，点「应用建议」生效（**需先停机器人**）
5. 每次优化存 `AIOptimizationLog`，带 `backtest_formula_version`，版本不一致时拒绝应用

参数范围护栏（Claude 建议超出会被 clamp）：`fast_period∈[5,50]`、`slow_period∈[20,200]`、`rsi_period∈[5,30]`、`rsi_overbought∈[60,85]`、`rsi_oversold∈[15,40]`、`sl_multiplier∈[0.5,3]`、`tp_multiplier∈[1,5]`

**C. 自动周优化**：Scheduler 每周一 06:00 UTC 自动跑一次 AI 优化（生成报告，不自动应用）。

**AI 自主策略切换**（`strategy_switch` 工具）：AI agent 检测到行情状态变化时可建议换策略，有护栏：冷却 1 小时、每天最多 3 次、`enable_auto_strategy_switch` 开关；shadow/paper 模式下只记录不切换。

---

### 流程 ⑤ ML 模型训练

**入口**：`/ml` 页面 → 训练。

**训练链路**：

```
历史 K 线（DB 或 MT5） + 宏观数据(可选)
   → 三重障碍打标签：未来 N 根K线内 上穿 entry+tp 标 BUY(1)、下穿 entry-tp 标 SELL(-1)、都没碰到标 HOLD(0)
   → 40+ 特征（EMA/RSI/ATR/布林/MACD/随机/动量/时间/宏观/情绪...）
   → LightGBM 训练（可选 walk-forward 验证）
   → 模型存文件 models/<symbol>_signal.pkl + 存 DB（model_binary + SHA256 digest 防篡改）
   → 实盘 MLStrategy 每 20 根 K 线检查一次 DB，自动加载新模型
```

**训练参数**：品种、周期、起止日期、`forward_bars`(1-50，默认 10)、`tp_pips`/`sl_pips`（**不传则用品种配置里的 `ml_tp_pips`**）、`test_size`(默认 0.2)、`use_walk_forward`。

**护栏**：数据不足 500 根拒绝；标签样本不足 200 拒绝；障碍相对波幅不合理拒绝（见流程 ①）。

**训练后看什么**：`/ml` 页的指标、特征重要性、漂移检测（drift）、校准分析（calibration）、`/ml` 的预测 tab。

**自动重训**：Scheduler 每周一 04:00 UTC（开盘前）对所有已启用品种重训。

> ⚠️ `ml_sl_pips` 当前不参与打标签（标签屏障刻意对称，只按 tp），是保留字段。BTCUSD `ml_timeframe=H1` 但实盘推理用 M15，是已知待修项。

---

### 流程 ⑥ 实盘交易（怎么交易）

**交易发生在哪里**：Scheduler 按品种周期触发 `process_candle`（每根 K 线收盘时）。全自动，无需人工干预。

**单笔交易决策管线**（`bot/engine.py:process_candle`）：

```
① 熔断检查（单品种 + 全局组合，日亏损/峰值回撤超限则暂停）
② 行情状态检测（regime：趋势/震荡/高波动，会调整 SL/TP 系数）
③ 宏观事件临近？→ 降低敞口
④ 策略引擎生成信号（或 AI 自主模式直接由 agent 决策）
⑤ AI 情绪过滤（Claude 判断 bull/bear/neutral + 置信度）
⑥ 交易许可检查（近期胜率、AI 置信度阈值）
⑦ 确认门 ConfirmationGate：5 个来源投票（量化信号/ML/行情状态/盈亏比/AI），需多数同意
⑧ 计算手数（风控预算 ÷ 止损距离）→ 下单到 MT5 Bridge → 持仓
```

**四个上线档位（rollout mode）**——先在 `/settings` 页面设置：

| 档位 | 行为 | 用途 |
|---|---|---|
| `shadow`（默认） | 模拟下单，不进券商，只记录 | 观察 AI 决策质量 |
| `paper` | 模拟盘（paper_trade 开关） | 更真实的模拟 |
| `micro` | 真实下单但手数封顶 0.01 | 最小真实暴露 |
| `live` | 真实下单 | 正式交易 |

- AI agent 的下单权限还受 `LLM_ALLOW_LIVE` 控制：`false` 时 AI 最多到 shadow/paper
- 实盘机器人状态在 `/dashboard` 看；`/api/bot/start`、`/api/bot/stop`、`/api/bot/emergency-stop` 控制

**风控体系**（不可绕过的护栏）：
- 每笔风险 %（`max_risk_per_trade`）、日亏损上限（`max_daily_loss`）、最大并发、最大手数
- 熔断器：日亏损/回撤超限自动暂停，冷却后可自动恢复
- 手动紧急停止：`/api/bot/emergency-stop`

---

## ⏰ 定时任务总表（Scheduler，UTC）

| 任务 | 频率 | 干什么 |
|---|---|---|
| `bot_tick` | 1 秒 | 拉最新报价推给前端 |
| 各周期 K 线任务 | 按 timeframe cron | 触发 `process_candle` 交易逻辑 |
| `fetch_sentiment` | 每 15 分钟 | 拉新闻做 AI 情绪分析 |
| `sync_positions` | 30 秒 | 与券商持仓对账 |
| `health_check` | 30 秒 | MT5 Bridge 心跳，异常自动暂停 |
| `status_broadcast` | 15 秒 | Redis pub/sub 广播状态给前端 |
| `position_reconciliation` / `pending_trades_recovery` | 5 分钟 | 持仓修复、挂单恢复 |
| `vault_health_check` | 5 分钟 | 密钥/Token 健康 |
| `memory_consolidation` | 每天 02:00 | 记忆沉淀 |
| `db_backup` | 每天 02:30 | pg_dump（需 `ENABLE_DB_BACKUPS=1`） |
| `ai_usage_cleanup` | 每天 03:00 | 清理 90 天前 AI 用量 |
| `ml_retrain` | 每周一 04:00 | 全品种重训 ML 模型 |
| `weekly_optimize` | 每周一 06:00 | AI 策略优化（只出报告） |
| `macro_collect` | 每天 07:00 | 拉宏观数据 |
| `daily_reset` | 按资产类别 | 每日风险预算重置 |
| `daily_summary` | 每天 22:00 | 交易日报（Telegram） |
| `economic_calendar_refresh` | 每小时 | 刷新经济日历 |

---

## 🛠 运维手册

### 日志
```bash
tail -f logs/backend.log logs/frontend.log   # 一键启动时的日志
```
后端是结构化 JSON 日志（`backend/logs/` 也有一份）。

### 数据库迁移
```bash
cd backend
.venv/bin/alembic upgrade head        # 升级到最新（25 个迁移）
.venv/bin/alembic downgrade -1        # 回退一个版本
.venv/bin/alembic current             # 看当前版本
```
⚠️ 新增模型字段时：改 `app/db/models.py` → `alembic revision --autogenerate -m "..."` → `upgrade head`。迁移文件 revision ID 不可复用。

### 测试
```bash
cd backend
.venv/bin/python -m pytest tests/ -v --no-cov        # 全部（665 个用例 / 43 个文件）
.venv/bin/python -m pytest tests/unit/test_risk_manager.py -k "lot_size" -v --no-cov
.venv/bin/python -m ruff check .                      # lint
.venv/bin/python -m ruff format .                     # format

# 前端
cd frontend && npx tsc --noEmit && npm run build
```

### 备份
- `scripts/backup_db.sh`：每日 pg_dump（Scheduler 02:30 UTC 触发，需 `ENABLE_DB_BACKUPS=1`）

### 常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| MT5 Bridge 连不上 / 401 | bridge 未启动、防火墙、API key 不一致 | Windows 上 `./start-mt5bridge.bat`；`backend/.env` 的 `MT5_BRIDGE_URL`/`MT5_BRIDGE_API_KEY` 与 Windows 端一致 |
| `/health` 显示 degraded | MT5 bridge 离线（by design） | 检查 bridge；不影响配置/回测 |
| 前端连不上后端 | 后端端口非 8002 或旧构建仍埋死 IP/localhost | 确认后端在 `:8002`；重新构建前端（`npm run build`）让自动推导生效；仅公网场景设 `NEXT_PUBLIC_API_URL` 覆盖 |
| alembic 迁移失败 | 缺 `DATABASE_URL_SYNC` 或 PYTHONPATH 不对 | 在 `backend/` 目录执行；确认 `.env` 有 `DATABASE_URL_SYNC` |
| 保存品种报 1500 被拒 | `ml_tp_pips × pip_value` 相对波幅超 6× | 按报错提示改小（GOLD 填 10~15 即可） |
| 训练失败 HOLD 占绝大多数 | `ml_tp_pips` 相对波幅过小，屏障几个月碰不到 | 加大 `ml_tp_pips`（GOLD 推荐 4.4~13.3） |
| 下单手数异常偏小 | 品种 `pip_value`/`contract_size` 配置错误（OIL/USDJPY 出厂值异常） | 用券商目录重建该品种，让系统回填规格 |
| 模型不生效 | 训练后实盘没加载 | 模型自动每 20 根 K 线重检一次 DB，无需重启；确认品种匹配 |

---

## 🔑 环境变量（详见 `backend/.env.example`）

| 变量 | 用途 | 必填 |
|---|---|---|
| `DATABASE_URL` / `DATABASE_URL_SYNC` | Postgres（asyncpg + alembic 同步驱动） | ✅ |
| `REDIS_URL` | Redis | ✅ |
| `MT5_BRIDGE_URL` / `MT5_BRIDGE_API_KEY` | MT5 Bridge 地址 + 密钥 | 交易用 |
| `SECRET_KEY` | JWT 签名 | ✅ |
| `AUTH_USERNAME` / `AUTH_PASSWORD_HASH` | 登录 | ✅ |
| `VAULT_MASTER_KEY` | 密钥库 AES 主密钥 | ✅ |
| `ANTHROPIC_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN` | Claude AI | AI 功能用 |
| `LLM_PROVIDER` / `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 多模型 LLM（如 deepseek-v4-flash） | AI 功能用 |
| `LLM_ALLOW_LIVE` | AI 是否允许 live 下单 | — |
| `SYMBOL_STARTUP_VALIDATION` | `warn`/`strict` 品种存在性校验 | — |
| `MAX_RISK_PER_TRADE` / `MAX_DAILY_LOSS` / `MAX_CONCURRENT_TRADES` / `MAX_LOT` | 硬风控上限 | ✅ |
| `USE_AI_FILTER` / `AI_CONFIDENCE_THRESHOLD` | AI 情绪过滤开关与阈值 | — |
| `ROLLOUT_MODE` | `shadow`/`paper`/`micro`/`live` | ✅ |
| `TRADING_MODE` | `strategy`/`ai_autonomous` | — |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Telegram 通知 | 可选 |
| `ENABLE_DB_BACKUPS` | 定时备份开关 | 可选 |
| `TRUSTED_HOSTS` / `SENTRY_DSN` | 安全 / 监控 | 生产用 |

> 完整变量清单见 [`backend/.env.example`](backend/.env.example)；MT5 Bridge 侧的见 [`mt5_bridge/.env.example`](mt5_bridge/.env.example)。

---

## 📁 目录结构

```
ai-trading-agent/
├── backend/
│   ├── app/
│   │   ├── api/routes/        # 25 个路由文件 / 114 个端点
│   │   ├── ai/                # Claude 客户端、策略优化器、确认门、用量
│   │   ├── backtest/          # 回测引擎、优化器、步进测试、蒙特卡洛、过拟合
│   │   ├── bot/               # BotEngine、BotManager、Scheduler、健康监控
│   │   ├── strategy/          # 11 个策略 + 行情状态(regime)
│   │   ├── risk/              # 风控、熔断器、相关性、VaR
│   │   ├── ml/                # LightGBM 训练器、特征、漂移、障碍校验
│   │   ├── services/          # 品种配置服务、校验
│   │   ├── db/                # SQLAlchemy 模型
│   │   └── constants.py       # 所有魔法数字集中地
│   ├── alembic/versions/      # 25 个数据库迁移
│   ├── mcp_server/            # AI agent 系统（工具、护栏、多 agent）
│   └── tests/                 # 665 个测试
├── frontend/
│   ├── app/                   # 18 个页面（App Router）
│   ├── components/            # UI 组件
│   └── lib/                   # API 客户端 + WebSocket
├── mt5_bridge/                # MT5 HTTP Bridge（Windows 专用）
├── docs/
│   ├── SYMBOL-PARAMETERS.md       # 品种参数（运营者版）
│   ├── SYMBOL-PARAMETERS-TECH.md  # 参数/风控链路技术审计
│   └── LONG-TERM-DB-SCALING.md    # DB 扩展方案
├── scripts/backup_db.sh       # 每日备份
├── start-all.sh / start-backend.sh / start-frontend.sh / start-mt5bridge.sh / stop-all.sh
└── docker-compose.yml         # 本机 Postgres(5434) + Redis(6380)
```

---

## 📖 文档索引

| 文档 | 读者 | 内容 |
|---|---|---|
| [`docs/SYMBOL-PARAMETERS.md`](docs/SYMBOL-PARAMETERS.md) | 运营者 | 品种参数怎么填、盈亏比、ATR、单位陷阱（先读这个） |
| [`docs/SYMBOL-PARAMETERS-TECH.md`](docs/SYMBOL-PARAMETERS-TECH.md) | 开发者 | 42 项缺陷登记表、逐字段消费链路 |
| [`docs/LONG-TERM-DB-SCALING.md`](docs/LONG-TERM-DB-SCALING.md) | 开发者 | 数据库长期扩展方案 |
| `CLAUDE.md` | 开发者 | 开发规范、已知问题、命令速查 |

---

## 📜 License

Private — 内部使用。

<div align="center">

Built with **Claude Code** · Trading on **MT5**

</div>
