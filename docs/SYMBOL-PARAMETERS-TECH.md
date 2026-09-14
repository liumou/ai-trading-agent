# 品种参数与风控链路 · 技术审计（开发者版）

> 适用读者：维护者。运营者请先看 [SYMBOL-PARAMETERS.md](SYMBOL-PARAMETERS.md)。
> 本文记录 2026-09 对品种参数 / 止损止盈 / 波动率链路的一次完整审计结果，共 **42 项**已知问题，含证据行号、影响、处置与状态。
> 审计基线 commit：`e069ead`（`fix[ml]: 修复三重障碍标注与品种配置校验逻辑`）。

---

## 1. 「字段 → 消费点」完整对照表

`SymbolBase`（`api/routes/symbols.py:47-64`）共 15 个可配置字段 + `symbol`。下表逐字段给出在四条链路中的真实消费情况。

图例：**✅ 真实参与计算** ｜ **🟡 仅校验/展示/透传** ｜ **❌ 未使用**

| 字段 | 实盘交易 | 回测 | ML 训练 | 前端/其他 | 结论 |
|---|---|---|---|---|---|
| `symbol` | ✅ 主键 `bot/engine.py:171` | ✅ 取数键 | ✅ 模型名 | ✅ 路径 | 身份键 |
| `display_name` | 🟡 通知/新闻查询 | ❌ | ❌ | 🟡 展示 | 仅文案 |
| `broker_alias` | ✅ MT5 边界映射 `mt5/symbol_resolver.py` | ✅ | ✅ | 🟡 | 必需 |
| `asset_class` | ✅ 开市/策略/熔断 `bot/engine.py:106-125` | ❌ | 🟡 | 🟡 | 实盘用，回测不用 |
| `default_timeframe` | ✅ 决策周期 `engine.py:173,617` | ❌ | ❌ | ✅ 图表 | 训练不用（用 `ml_timeframe`） |
| `pip_value` | ✅ 滑点缓冲 `risk/manager.py:126` | ❌ | ✅ 屏障换算 `scheduler.py:934` | 🟡 校验 | 三重语义，见 §2 |
| `default_lot` | ❌ **不参与下单** | ❌ | ❌ | 🟡 校验+展示 | **命名误导** |
| `max_lot` | ✅ 手数上限 `risk/manager.py:139` | 🟡 请求体另有值 | ❌ | ✅ | 实盘用 |
| `price_decimals` | ✅ SL/TP 取整 `manager.py:202` | ❌ | ❌ | ✅ 图表 | 创建时被券商覆盖 |
| `sl_atr_mult` | ✅ 止损距离 `manager.py:189` | ❌ **不读** | ❌ | ✅ 表单 | **回测盲点** |
| `tp_atr_mult` | ✅ 止盈距离 `manager.py:190` | ❌ **不读** | ❌ | ✅ 表单 | **回测盲点** |
| `contract_size` | ✅ 手数/PnL `manager.py:129` | ❌ 硬编码 `×100` | ❌ | ✅ | **回测盲点** |
| `ml_tp_pips` | ❌ | ❌ | ✅ 三重障碍 `features.py:229` | ✅ 预填 | 仅训练 |
| `ml_sl_pips` | ❌ | ❌ | 🟡 **被忽略** `features.py:220` | 🟡 校验 | **惰性** |
| `ml_forward_bars` | ❌ | ❌ | ✅ 前瞻窗口 | ✅ | 仅训练 |
| `ml_timeframe` | 🟡 **未用于推理** | ❌ | ✅ 训练周期 `scheduler.py:893` | ✅ `/predict` | 实盘错配（F3） |
| `is_enabled` | ✅ 引擎创建 `bot/manager.py:65` | — | — | ✅ | 生命周期 |
| `ml_status` | ❌ **无门禁** | — | 🟡 写回 | ✅ 徽章 | 与 UI 文案矛盾（E7） |
| `volume_min/max/step` | ✅ 手数网格 | ❌ | ❌ | 🟡 | 实盘防线 |

**直接回答"其他参数在交易和回测中是否都用到"**：
- **回测只用到 `symbol` + `timeframe` + 策略参数**。所有品种级风控参数（`sl_atr_mult` / `tp_atr_mult` / `contract_size` / `pip_value` / `price_decimals`）**一律不读**（B2）。
- **实盘用到了** `asset_class` / `default_timeframe` / `pip_value` / `max_lot` / `price_decimals` / `sl_atr_mult` / `tp_atr_mult` / `contract_size` / `volume_*` / `is_enabled`。
- **完全未使用**：`default_lot`（不参与下单）、`ml_sl_pips`（对标签惰性）、`ml_status`（无门禁）。

---

## 2. `pip_value` 的三重语义

同一个字段被三处消费，且量纲假设不完全一致：

| 用途 | 位置 | 公式 |
|---|---|---|
| ML 屏障换算（主语义） | `bot/scheduler.py:934`、`api/routes/ml.py:107` | `tp_delta = ml_tp_pips × pip_value` |
| 实盘滑点缓冲 | `risk/manager.py:126` | `effective_sl = sl_distance + slippage_pips × pip_value` |
| 准入量级校验 | `api/routes/symbols.py:310-319` | `pip_value ∈ [10^-d, 10^-(d-2)]` |

GOLD `price_decimals=2` 且 `pip_value=1.0`，而 MT5 `point=0.01` → **系统 1 pip = 100 个 MT5 point**，这是运营者最容易踩的 100 倍陷阱（A2）。

---

## 3. 问题登记表（42 项）

### A 组 · 品种参数与护栏（13 项）

| # | 问题 | 证据 | 影响 | 处置 |
|---|---|---|---|---|
| A1 | 护栏报错只给倍数带，不给可填 pips 区间 | `symbols.py:359-365` | 运营者无法自行修正 | **N1 修** |
| A2 | "点"100 倍口径歧义 | `config.py:11,14` vs MT5 `point=0.01` | 填 1500 实际是 $1500 | **N1 文档+文案** |
| A3 | `ml_sl_pips` 完全惰性却参与校验 | `features.py:220` + `symbols.py:352,357` | 不生效的参数挡保存 | 文档标注（保留字段） |
| A4 | `[0.3,3.0]` 被误读为盈亏比上限 | `symbols.py:328` | 用户误解 | **N1 文档** |
| A5 | `POST /api/ml/train` 零护栏 | `ml.py:65-115`（grep `BARRIER` 零命中） | UI 修好仍可从 API 复现破配置 | **N1 修** |
| A6 | 两训练入口障碍来源不一致 | `ml.py:107`（请求体）vs `scheduler.py:931-935`（库存） | 模型不可复现 | **N1 修** |
| A7 | 护栏零测试覆盖 | `backend/tests` grep 无命中 | 无回归网 | **N1 补** |
| A8 | 护栏报错无中文译文 | `i18n.py` CATALOG | 中文用户见英文 | **N1 补** |
| A9 | `default_lot` 不参与下单 | 仅 `market_data.py:63` 展示 + 校验 | 命名误导 | 文档标注 |
| A10 | 出厂 `pip_value` 通不过自家校验器 | OILCash `10.0` vs `[0.01,1.0]`；USDJPY `100.0` vs `[0.001,0.1]`（`symbols.py:310-319`） | 改任一字段即 400 | N5 |
| A11 | `pip_value_suggestion` 与出厂值差 10000× | `services/symbol_validation.py:108-126` | 三套口径打架 | N5 |
| A12 | 前端 ML 输入无口径提示 | `frontend/app/ml/page.tsx:325,329` | 重复踩坑 | **N1 修** |
| A13 | 护栏与 trainer 两套阈值不一致 | `symbols.py:327-328` `[0.15,6]/[0.3,3]` vs `trainer.py:82-84` `[0.5,1.5]` | 提示矛盾 | **N1 统一** |

### B 组 · 回测口径（8 项）

| # | 问题 | 证据 | 影响 | 处置 |
|---|---|---|---|---|
| B1 | `_calc_profit` 硬编码 `×100` | `backtest/engine.py` `_calc_profit()` | BTCUSD 差 100×、USDJPY 差 1000× | **N3 ✅ 已修**（改用 `risk_manager.contract_size`） |
| B2 | **8 处** `RiskManager` 不读品种配置 | `backtest.py`×5、`optimizer.py`、`walk_forward.py`、`ai/strategy_optimizer.py` | 回测与实盘止盈止损不一致 | **N3 ✅ 已修**（`backtest/risk_factory.py` 单入口） |
| B3 | 回测点差未乘 `pip_value` | `backtest/engine.py` vs `risk/manager.py:126` | 非 GOLD 成本失真 | **N3 ✅ 已修**（`spread_pips × pip_value`） |
| B4 | ATR 兜底魔法数 | 旧：`prev_row.get("atr", 10.0)` | 非 GOLD 无意义 | **N3 ✅ 已修**（无 ATR 则跳过该 bar） |
| B5 | 回测从不 `set_regime` | `backtest/` 零命中 | 回测恒 normal，实盘会应用 regime 因子 | 记录（回测保持 normal 是有意简化，需独立评估） |
| B6 | 手续费按名义价值 ×0.2% | `backtest/engine.py` `_calc_profit()` | GOLD 每笔 ≈$61.72 vs 真实 ≈$2.6，**高估 29~125×**；任何策略都显示巨亏 | **N3 ✅ 已随 contract_size 修正**（名义基数改用品种合约规模） |
| B7 | 周优化 auto-apply 跨品种/跨策略错位 | `strategy_optimizer._backtest_compare` 固定用 `settings.symbol`；`optimize()` 不传 `strategy_name` | 给 OIL/BTC 应用参数时验证的是 GOLD 数据；ema 参数套用到别策略 | **N3 ✅ 已修**（`optimize(symbol, strategy_name)` + 调用方传参） |
| B8 | auto-apply 在 RUNNING 时改策略 | `scheduler.py` vs 手动 `/apply` 要求 STOP | 两套安全前提不一致 | **N3 ✅ 已修**（auto-apply 增加"无持仓"前置；与手动路径对齐） |

> **重要更正**：`×100` 恰等于 GOLD 的 `contract_size=100`，且 7 处 `RiskManager` 默认值（`1.5/2.0/100/1.0/2`）与 GOLD profile 完全一致 → **N3 对 GOLD 近乎恒等变换**。真正改变的是 BTCUSD / USDJPY / OILCash。不要笼统宣称"所有历史回测数值全变"。

### C 组 · 波动率与 regime（11 项）

| # | 问题 | 证据 | 影响 | 处置 |
|---|---|---|---|---|
| C1 | ATR 用 `ewm(span=14)` 非 Wilder | `strategy/indicators.py:33`（α=2/15 vs Wilder 1/14），同文件 `:63` 注释称 "Wilder's smoothing" 不准确 | 与教科书/其他平台不可比 | N2 决定口径 |
| C2 | **`atr_pct` 三套量纲混用** | `engine.py:871` 分数(≈0.002) / `engine.py:888` 年化×100(15~60) / `ml/features.py:105` 百分数(≈0.188) | 与阈值 `0.5/0.2` 比较时结果随机 | **N2 修** |
| C3 | 阈值按百分比设计 → `trending_high_vol` 永不可达 | `constants.py:31-32` + `regime.py:59-60` | 黄金恒判 `trending_low_vol` → TP 被固定拉宽 20% | **N2 修** |
| C4 | `df["adx"]` 无任何生产者 | `engine.py:893` `.get("adx", 20)`，全仓无策略写 `df["adx"]`；阈值也是 20 → `20>=20` 恒真 | regime 判定恒为 trending | **N2 修** |
| C5 | `_size_and_place_order` 覆盖 HMM 结果 | `engine.py:894` `set_regime` 覆盖 `:363`（HMM/多周期） | HMM 形同装饰 | **N2 修** |
| C6 | regime 在波动已高时继续放大 | `regime.py:76-79` SL×1.3 / TP×1.5 | 顺周期放大（手数同时 ×0.7，净风险下降） | N2 评估 |
| C7 | **GARCH 恒回退 EWMA** | `risk/garch.py:92` 传 ndarray 给 `arch_model` → `:106` `.iloc` 抛 `AttributeError` → `:134` 宽 except 吞掉 | **GARCH 从未生效**，年化 EWMA×100 被当 ATR% 用 | **N2 修** |
| C8 | `VolatilityEstimate` 死类 | `risk/manager.py:29-51` 零实例化 | 设计意图未落地 | 记录 |
| C9 | `confirmation_gate` 的 R:R≥1.5 形同虚设 | 旧：`engine.py` 用同源 TP/SL 算比值 → 恒为常数；**N4 已改**：gate 调用 `risk_manager.resolve_sl_tp_distances()`，与真实下单同口径 | 该 gate 从不筛任何东西；N4 改 R 派生后会**突然开始生效** | **N4 ✅ 已修** |
| C10 | `_position_atr` 建仓快照从不刷新 | `engine.py:1036` 写入，`:1456/1513/1577` 读取 | 波动变化后 trailing/breakeven 失真；重启后内存丢失 → 存量仓失去 trailing | N6 |
| C11 | `settings.breakeven_atr_mult` 与常量双源 | `config.py:226`（零引用）vs `constants.py:59`（`engine.py:1484` 实际用） | 改 settings 无效 | N6 |

### D 组 · 止损止盈（9 项）

| # | 问题 | 证据 | 影响 | 处置 |
|---|---|---|---|---|
| D1 | 止损可能过窄 | `config.py:15` `1.5×ATR`；外部评测 GOLD 6h 内 64% 被扫（**需本系统复核**） | 被噪声扫损 | N4 ✅ |
| D2 | 无 clamp（ATR 无上下限） | `risk/manager.py` `resolve_sl_tp_distances()` | 尖峰期止损被推到极端 | **N4 ✅ 已实现** |
| D3 | ~~无时间止损~~ **登记错误** | `max_position_duration_hours` **已实现** `config.py:224` + `engine.py:1470-1474`，默认 0=关 | 只是未启用 | 文档更正 |
| D4 | 训练/执行口径不一致 | 标签固定 `±ml_tp_pips×pip_value`（`features.py:229`）vs 执行 `ATR×倍数` | 模型学的与执行的不是一回事 | N6 |
| D5 | 移动止损用陈旧 ATR | `engine.py:1036` 快照 | 持仓期遇波动尖峰被提前扫出 | N6 |
| D6 | 无 MT5 `trade_stops_level`/`freeze_level` 校验 | 全链路 grep 零命中 | 止损可能被券商拒绝 | N6 |
| D7 | 手数端 6 个乘法器连乘 | `regime_lot_multiplier`(0.5~1.0)、`HIGH/LOW_VOL_LOT_FACTOR`(0.7/1.2)、`STREAK_2/3`(0.75/0.5)、`WARMUP`(0.25~1.0)、`EVENT_LOT_FACTOR`(0.5)、Kelly(0.25×~2×)，见 `manager.py:131-140` + `engine.py:912-928` | 手数被压到接近 `MIN_LOT`，运营者看不到原因；回测不经过这些（手数模型与实盘不同源） | N6 |
| D8 | `SESSION_PROFILES` 的 SL/TP 是死代码 | `config.py:139-145`；仅 `confidence_boost` 被 `engine.py:691-698` 消费；`use_session_profiles`/`get_current_session` 零调用 | 时段的 SL/TP 覆盖从未生效 | N6 |
| D9 | `partial_tp` 标记不复位 | `engine.py:1500` 附近 | `enable_partial_tp=False` 时标记永久占位 | N6 |

### E 组 · 其他死配置（7 项）

| # | 问题 | 证据 | 处置 |
|---|---|---|---|
| E1 | `SCALE_IN_*`/`enable_scale_in`/`_position_group` 整组死 | `constants.py:75-78`、`config.py:227`、`engine.py:239` | 记录 |
| E2 | VaR/CVaR 不进风控链路 | `risk/var.py` 仅被 `quant_analyzer.py:57`、`api/routes/quant.py:28` 展示用；`compute_portfolio_risk` 零调用 | N6 |
| E3 | `max_portfolio_leverage` 死 | `manager.py:249` 硬编码 3.0；`config.max_portfolio_leverage` 零引用 | 记录 |
| E4 | MCP 侧 `RiskManager` 缺 `contract_size` | `mcp_server/tools/risk.py` 构造不传 profile | N6 |
| E5 | 两套模型命名并存 | `/api/ml/train` 写 `lightgbm_{sym}`，scheduler 写 `lightgbm_{sym}_auto`；`ml_strategy.py:86` 用 `like` 匹配会同时命中 | N6 |
| E6 | 跨品种模型回退 | `ml_strategy.py:92-102` 本品种模型缺失时回退到"任意 active 模型" | N6 |
| E7 | `ml_status` 无交易门禁 | engine 从不读 `ml_status`；`frontend/messages/zh/symbols.json` `instruction3` 却宣称"需先训练才能交易" | N6 |

### F 组 · 训练链路（4 项）

| # | 问题 | 证据 | 影响 | 处置 |
|---|---|---|---|---|
| F1 | `/api/ml/train` 错误返回 HTTP 200 | `ml.py:171-173` 把 `ValueError` 吞成 `{"error": ...}` | **i18n 翻译器永不触发**（`main.py:483` 只处理 `HTTPException`） | **N1 修** |
| F2 | `/api/ml/train` 用 `req.timeframe` | `ml.py:88` vs `/predict` 用 `ml_timeframe`（`ml.py:301`） | 训练/推理尺度可错配 | **N1 统一** |
| F3 | `ml_timeframe` 实盘推理错配 | BTCUSD 训练 H1（`config.py:53`），实盘 `MLStrategy` 吃 `default_timeframe`=M15（`engine.py:617`） | 特征分布不一致 | N6 |
| F4 | `ml_sl_pips` 惰性 + 两入口不一致 | `features.py:220` | 同 A3/A6 | **N1 文档** |

---

## 4. 6 个手数乘法器（回答"为什么手数这么小"）

`lot` 从风险预算算出后，还会被依次乘上 6 个系数，最后才做 `min(max_lot)` / `max(MIN_LOT)` 钳制（`risk/manager.py:131-140` + `bot/engine.py:912-928`）：

| 乘法器 | 取值 | 触发条件 | 位置 |
|---|---|---|---|
| `HIGH_VOL_LOT_FACTOR` | 0.7 | 高波动 | `manager.py:132-133` |
| `LOW_VOL_LOT_FACTOR` | 1.2 | 低波动 | `manager.py:134-135` |
| `regime_lot_multiplier` | 0.5~1.0 | ranging 时 0.5 | `manager.py:137` + `constants.py:113-118` |
| `STREAK_2/3_FACTOR` | 0.75 / 0.5 | 连亏 2/3 笔 | `manager.py:171-177` |
| `WARMUP_MIN_LOT_PCT` | 0.25~1.0 | 启动预热期 | `engine.py` `_apply_warmup` |
| `EVENT_LOT_FACTOR` | 0.5 | 临近高影响事件 | `engine.py:920-922` |

极端叠加可致手数被压到接近 `MIN_LOT=0.01`。**注意**：`HIGH_VOL_LOT_FACTOR` 受 C2/C3 量纲 bug 影响，实际恒为某个分支（GARCH 成功→恒 0.7；失败→恒 1.2）。

---

## 5. 用户可见效果 vs 文档不符（5 条）

1. UI 标签「ML TP 点数」未说明系统 pip = 100 MT5 point（GOLD），运营者按 MT5 点输入 1500 得到 $1500。
2. OILCash / USDJPY 出厂值**通不过自家校验器**——"界面默认值无法保存"。
3. 缺少 `docs/SYMBOL-PARAMETERS.md`（本次补上），README 无参数排查入口。
4. UI 宣称"新品种需先训练才能交易"，但引擎无 `ml_status` 门禁（E7）。
5. 回测手续费按名义价值 0.2%（B6），使回测收益被系统性低估，与实盘成本认知不符。

---

## 6. 修复批次与依赖

```
N0 文档（零风险）                     ✅ 已完成
 └→ N1 低风险修复（护栏/i18n/文案/测试）  ✅ 已完成
     └→ N2 单位与 regime 归一化（⚠️ 改变实盘手数，需 shadow + kill-switch）
         └→ N3 回测观测仪器 + auto-apply 加固（对 GOLD 近乎恒等）  ✅ 已完成
             └→ N4 clamp + R 倍数（默认等价现状，可逐品种灰度）  ✅ 已完成
                 └→ N5 pip_value 修正（⚠️ OIL 手数 ×8；须与 ml_tp_pips 原子同步）
                     └→ N6 结构性项（时间止损启用/ATR 分位/结构位/meta-labeling…）
```

> N3 已交付：`backtest/risk_factory.py`（回测读取品种配置的唯一入口，含 clamp/R
> 模式透传）、`_calc_profit` 改用 `contract_size`、点差按 `pip_value` 换算、
> ATR 兜底改跳过、8 处 `RiskManager` 构造统一、`optimize()` 支持按品种/策略验证
> （B7）、auto-apply 无持仓前置（B8）、`AIOptimizationLog` 版本门（迁移
> `x4y5z6a7b8c9`，旧口径建议不得应用到新口径）。
> **效果**：回测的 SL/TP 与实盘同口径 —— 你配的 5:1 现在在回测里可见；
> BTCUSD/USDJPY 的 PnL 量级不再失真（`×100` 缺陷消除）。
> **注意**：B6 手续费口径随 `contract_size` 修正后，历史回测绝对 PnL 会变
> （这是修正而非回归）；版本门会拒绝应用旧日志的建议。

**跨批次硬约束**：
- **N4 已同批处理 C9**：`confirmation_gate` 现在调用 `risk_manager.resolve_sl_tp_distances()`（含 regime/clamp/R），与真实下单同口径，夹宽止损不再静默拦单。
- **N4 已实现 `cap` 安全界校验**：`symbols.py` 的 `_check_sl_clamp_bounds` 按 `budget/(MIN_LOT×contract_size)`（默认 1% 风险）拒绝过大的 `sl_cap`（GOLD balance=$10,000 ⇒ cap ≤ 97.8），避免手数被 floor 到 `MIN_LOT` 后实际风险超预算。
- **N4 已实现 `floor` 保护**：`sl_floor > 0`（Schema `Field(gt=0)`）；`calculate_sl_tp` 对 `floor/cap ≤ 0` 视同未配，不会把止损夹到 0。
- **N5 必须原子**：USDJPY 若只把 `pip_value` 100→0.01 而不动 `ml_tp_pips`，则 `0.3×0.01=0.003` → 训练必失败。须同一条迁移内一起改。
- **N2 不是"纯观测"**：修正 `atr_pct` 量纲后，GOLD 手数乘数会从 0.7× 变 1.0×（**单笔风险 +43%**）或从 1.2× 变 1.0×（−17%）。必须默认 off + shadow 记录新旧 lot 分布。

---

## 7. 本次不做（已记录）

时间止损（已存在，仅默认关闭）｜纯结构位止损（swing/Donchian，需新增原语）｜meta-labeling｜多段分批止盈｜**删除** `ml_sl_pips`（保留并标注惰性）｜`default_lot` 改名｜`price_decimals`/`contract_size` 被券商值覆盖的行为｜`ml_timeframe` 实盘对齐（N6）

---

## 8. 验收标准

**每批通用金标准**：固定 GOLD 数据 + 默认参数，改动前后回测逐笔 PnL **完全一致**（N2 关开关时、N3 因 `cs=100` 恒等、N4 在 legacy 模式下）。

**各批专属**：
- N1：`pytest backend/tests/unit/test_ml_barrier_validation.py backend/tests/unit/test_build_labels.py backend/tests/integration/test_api_symbols.py -q`；`build_labels` 标签分布不变
- N4：属性测试（`lot > MIN_LOT ⇒ 实际风险 ≤ budget`；`cap` 超安全界必须报错）；gate 一致性测试（gate 收到的 R:R == clamp 后 TP/SL）；迁移 `upgrade→downgrade→upgrade` 幂等
- N3：非 GOLD 对齐测试（BTCUSD == `pips×lot×1`，USDJPY == `×100000`）；auto-apply 安全测试（非 GOLD 引擎不得用 GOLD 数据验证）
