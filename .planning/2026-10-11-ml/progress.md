# Progress — ML 数据采集异常排查

## 2026-10-11 排查会话

### 已定位根因（日志证据闭环）
- **症状**：ML 模型页面数据采集异常（OILCash / BTCUSD 历史数据回填失败）
- **根因**：MT5 Bridge（`192.168.3.47:8001`）自 2026-10-10 14:45 起宕机（502 Bad Gateway），持续到 10-11 凌晨未恢复 → Bootstrap seed 采集（`symbols.py` 启用 OILCash 时触发）全部 chunk 失败
- **当前状态**：bridge `/health` 已恢复（实测 200，10-11 排查时）

### 关键时间线
| 时间 | 事件 |
|------|------|
| 10-10 14:45 | Bridge 首个 502（/ohlcv/BTCUSD） |
| 10-10 21:24:49 | health_monitor 判定 unreachable ×3 → 暂停全部引擎（PAUSED, pause_reason=bridge） |
| 10-10 21:35 | Bridge 仍 502 |
| 10-11 03:26 / 04:48 | 后端两次重启，bridge 未恢复 |
| 10-11 04:44 | 用户启用 OILCash → Bootstrap seed 采集触发 |
| 10-11 04:46-04:48 | OILCash/BTCUSD/GOLD 采集全 502，无任一 chunk 成功 |
| 10-11 排查时 | bridge /health 返回 200（已恢复） |

### 已做
- [x] 初始化计划 `2026-10-11-ml`
- [x] 分析 `backend/logs/errors.log`（2220 行）+ `bot.log`（50509 行）
- [x] 定位采集入口：`symbols.py:290-332`（bootstrap seed）、`collector.py:25-70`（chunk 循环）、`scheduler.py:996-1046`（ML 重训）
- [x] 确认健康监视器暂停逻辑：`health_monitor.py:64-97`
- [x] 实测 bridge `/health` 恢复（200）
- [x] 确认 `x-bridge-key` 密钥位置（`.env:MT5_BRIDGE_API_KEY`，connector 注入）
- [x] **定位时区入库 Bug**：用户提供 asyncpg DataError 日志 → `collector.py:80` `to_pydatetime()` 保留 aware → 无时区列拒绝。逐环实测 pandas 2.2.3 aware 解析行为 + 修复范式等价性
- [x] 写 `findings.md`（含第二根因）+ `task_plan.md`（Phase 1 = 修复 bug）

### Phase 1 执行（修复完成）
- [x] `collector.py:76-82` 加 naive UTC 归一（`astimezone(UTC).replace(tzinfo=None)`），import 补 `UTC`
- [x] 新增 `tests/unit/test_collector.py`：aware 归一 / naive 保留 / ON CONFLICT 幂等，3/3 通过
- [x] ruff 通过；相关回归（market_data_alias + trade_reviews + ml_strategy）55/55 通过
- [x] 全量回归 1223 passed / 10 failed（全部 env 配置类，与本改动无关）

### Phase 2 执行（链路验证，完成）
- [x] bridge `/health` 连续 3 次 200（已恢复）
- [x] bridge `/ohlcv/{symbol}/history` 用后端密钥成功返回，`GOLD_` 为正确别名，**time 确凿为 `"2025-04-01T01:00:00+00:00"`（aware +00:00）** —— 修复必要性获直接证据
- [x] codegraph 确认 ML 训练链路消费 `load_from_db`，修复写入侧后无下游副作用
- [x] **DB 现状查询（分类器恢复后完成）**：
  - ohlcv_data 覆盖：OILCash M15=35765 bars（到 10-09）、BTCUSD M15=50894（到 09-14）、GOLD M15=36024（到 10-09）
  - **last_created 时间线**：OILCash 最近入库 10-09 11:41（bridge 宕机前）、GOLD 10-09 13:30、**BTCUSD 09-14 12:33（缺口最大）**
  - ml_status：OILCash(336773771)=pending（bootstrap 卡住）、BTCUSD=ready、GOLD(336773771)=ready
  - **ml_timeframe**：OILCash=M15、BTCUSD=H1、GOLD=M15（config.py）

### Phase 3 执行（重采集，完成）
- [x] 确认重采集目标：BTCUSD H1+M15（09-14 缺口）、OILCash M15、GOLD M15（增量，ON CONFLICT 保证幂等）
- [x] 写 `tmp_recollect.py`：走后端同链路（MT5BridgeConnector→MarketDataService→HistoricalDataCollector.collect），2025-04-01→今
- [x] 后台采集完成（task b5ww02uy9，exit 0，**无任何 DataError** —— 时区修复获生产级验证）
- [x] 验证 `-19` 语义：asyncpg `executemany` 返回 `rowcount=-1` × 19 chunk 的报告惯例，非真实负插入（DB 实际增长证实）
- [x] **定位 GOLD 首轮 0 bars 根因 = 临时脚本缺陷（非生产 bug）**：脚本漏调 `load_profiles_from_db()` → `SYMBOL_PROFILES` 保持静态默认（`broker_alias=None`）→ `to_broker_alias("GOLD")` 返回 `"GOLD"` → bridge 端实际是 `"GOLD_"` 无数据。BTCUSD/OILCash 的 alias 恰好等于规范名，"碰巧"成功掩盖了缺陷
- [x] 修正脚本补 `load_profiles_into_memory()`，GOLD 补齐（M15 +92 → 36116、H1 +23 → 9034，到 10-09 23:45）

### Phase 4 执行（数据覆盖验证，完成）
- [x] ohlcv_data 最终覆盖：BTCUSD H1=13393（到 10-11）、M15=53565（到 10-11）、OILCash M15=39789（到 10-09）、GOLD M15=36116（到 10-09 23:45）
- [x] **近 90 天窗口 bars 全部远超 ML 门槛（≥500）**：OILCash M15=6530、GOLD M15=5939、BTCUSD H1=2161、GOLD H1=1486、OILCash H1=1187
- [x] ml_status 现状：BTCUSD/GOLD/US30Cash(336773771)=ready；OILCash（两账号）=pending
- [x] 后端健康（8002）：`mt5_connected:true, redis_connected:true, ai_available:true`，`bot_state:STOPPED`
- [x] OILCash `pending` → 训练属用户操作：`/api/symbols/{symbol}/retrain` 需要 engine（bot STOPPED 时 503）；ML 页面有 Train 按钮；周度 cron（周一 04:00 UTC）会自动重训全部 enabled 引擎。**未擅自触发**（会修改生产模型 + 影响实盘）
- [x] ML 页面「数据采集异常」根因已消除：数据覆盖图表将显示补齐后的曲线

### Phase 5 执行（防复发加固评估）
- [x] 时区处理排查：`collector.py` 是唯一的 `pd.to_datetime` + 写无时区列遗漏点（`trade_reviewer._naive`、`backtest/engine.py:115` 已正确）
- [x] 别名退化已知：`load_profiles_into_memory` docstring 已明确记载「进程没加载过，别名就退化成原样返回」→ 本次临时脚本教训与该警告一致，非代码缺陷
- [x] ml_status 失败落库已正确：`_ml_retrain_symbol` 三条失败路径（<500 bars / <200 labeled / 异常）均 `_set_symbol_ml_status("failed")`
- [x] 采集失败可观测性：`collector.collect` 对失败 chunk 仅 `logger.warning`（不抛），`get_data_status` 可读；GOLD 首轮 9 chunk 全 0 bars 未产生告警 —— 属可改进项但需引入告警机制，超出本次范围
- [x] Bridge 宕机前置检查（跳过白跑）：属优化项，非正确性缺陷，不做大改
- **结论：无新增代码加固，本次仅修复 bug + 补数据**

### 阻塞（已解除）
- Bash 分类器模型（deepseek-v4-flash）间歇性超时问题已恢复，Phase 3-5 全部完成
