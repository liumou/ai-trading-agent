# Findings — ML 数据采集异常排查

> 排查会话：2026-10-11。任务：ML 模型页面数据采集异常，查日志定位根因并生成执行计划。

## 根因结论（已用日志证据闭环）

**MT5 Bridge（Windows VPS `192.168.3.47:8001`）自 2026-10-10 14:45 起持续返回 502 Bad Gateway（宕机未恢复）。**
2026-10-11 04:44 用户在 ML 页面/品种页启用 OILCash，触发 Bootstrap seed 历史数据采集；同一时刻 BTCUSD 也在采集。因 bridge 宕机，**全部 chunk 拉取失败，历史数据回填落空 → ML 训练前置数据缺失 → ML 模型页面数据采集异常。**

## 证据链（时间线）

### 1. Bridge 宕机起点：2026-10-10 14:45
- `backend/logs/errors.log:14:45:10` — `MT5 Bridge GET /ohlcv/BTCUSD HTTP error: 502`
- `bot.log:14:45:05` — `Failed to get OHLCV for BTCUSD: Server error '502 Bad Gateway' for url 'http://192.168.3.47:8001/ohlcv/BTCUSD?...'`

### 2. 健康监视器判定宕机并暂停：2026-10-10 21:24:49
- `errors.log:21:24:49` — `app.bot.health_monitor:_on_failure:71 - MT5 Bridge unreachable for 3 checks — pausing all trading`
- `health_monitor.py:69-81`：连续 ≥3 次失败 → `_is_degraded=True` → 所有 RUNNING 引擎置为 `PAUSED`，`pause_reason="bridge"`，记 `BotEventType.ERROR`
- 之后 21:25:56、21:35:54 仍持续 `/health` 502 → 引擎保持 PAUSED

### 3. 10-11 凌晨重启未恢复（bridge 依然宕）
- `bot.log:03:26:02` — 后端进程重启（`Starting Trading Bot`）
- `03:20~` 持续 `Stale tick for GOLD_: 69722s old`（tick 从 bridge 拿不到）
- `04:18:28` — Redis `Connection reset by peer` + `Operation timed out`（Tailscale 网络也不稳）

### 4. 用户启用 OILCash → 触发 Bootstrap seed 采集：2026-10-11 04:44
- `bot.log:04:44:04` — `Symbol OILCash -> enabled=True` + `BotManager reload: {'added': ['OILCash']...}`
- `symbols.py:290-332` `_bootstrap_symbol_data`：ml_status pending→training → `collector.collect(symbol, timeframe, from_date, to_date)` 回填历史数据 → `scheduler._ml_retrain_symbol` 重训
- `bot.log:04:46:03` — `Collecting OILCash H1 2025-04-01 -> 2025-05-01`（开始采集）

### 5. 采集全部失败：bridge 502 / tick 超时
- `bot.log:04:46:47` — `Failed to get tick for BTCUSD/GOLD_/OILCash: tick timeout`（每 3s 一次）
- `errors.log:04:47:31` — `MT5 Bridge historical OHLCV failed`（第一次 history 拉取失败）
- `04:47:33` — `MT5 Bridge GET /health failed after 3 attempts`
- `04:47:39~04:47:51` — `historical OHLCV HTTP error: 502` × 5（BTCUSD 各 30 天 chunk 全失败）
- `bot.log:04:47:31~51` — 每 chunk 都 `No data for chunk X -> Y`（返回空 df）
- **OILCash / BTCUSD / GOLD 无任何一个 chunk 成功**（`Chunk ... bars` 成功日志为 0）

### 6. 后端二次重启仍未恢复：04:48
- `bot.log:04:48:28` — 再次 `Starting Trading Bot`（3 品种 GOLD/BTCUSD/OILCash）
- `04:48:34~04:49:21` — 又触发 3 次 `Collecting ... 2025-04-01 -> 2025-05-01`
- **04:48 之后无任何成功 Chunk、无 bridge 健康恢复事件** → 采集持续失败，ML 训练数据持续缺失

### 7. 证据缺口确认
- errors.log / bot.log 截至分析时（10-11 05:00 前）无任何 bridge `recovered` / `health OK` 事件
- ML 页面「数据采集异常」与上述 Bootstrap seed 失败直接对应（前端 `/ml` 读 ml_status/数据覆盖，数据未回填 → 显示异常）

## 代码位置

| 环节 | 文件:行 | 说明 |
|------|---------|------|
| 采集入口 | `app/api/routes/symbols.py:290-332` | `_bootstrap_symbol_data`：ml_status pending→training → collect → retrain |
| 采集实现 | `app/data/collector.py:25-70` | 30 天 chunk 循环，`market_data.get_ohlcv_range`，空 df → warning 继续 |
| 采集数据源 | `app/mt5/market_data.py:85` | `connector.get_ohlcv_range(symbol, timeframe, from_date, to_date)` |
| Bridge HTTP | `app/mt5/connector.py:262-265` | `get_ohlcv_range`：失败/502 抛错 |
| ML 重训 | `app/bot/scheduler.py:996-1046` | `_ml_retrain_symbol`：`collector.load_from_db` 90 天窗口，`<500 bars → skipped/failed` |
| 健康监视 | `app/bot/health_monitor.py:64-97` | ≥3 失败 → 暂停引擎 |
| 引擎暂停 | `app/bot/health_monitor.py:69-81` | RUNNING→PAUSED, pause_reason="bridge" |

## 关键观察

1. **根因不在后端代码**，而在 **MT5 Bridge 服务本身宕机**（Windows VPS 侧进程挂掉/网络 502）。
2. 采集异常是**下游症状**：bridge 不可用 → `get_ohlcv_range` 全 502 → 历史数据回填失败 → ML 训练数据不足。
3. 2026-10-10 21:24 引擎已因 bridge 宕机被 PAUSED，但 **10-11 凌晨的重启把引擎带回 RUNNING/auto_started**（bridge 仍未恢复，trading 实际不可用但引擎状态非 PAUSED —— 需确认重启后是否恢复 degraded 判断）。
4. 附带问题：Redis（100.72.200.33:16379 Tailscale 地址）在 04:02-04:35 也出现 Connection reset / 超时 —— 网络层（Tailscale/内网）不稳定，可能是 bridge 502 的伴随现象，也可能独立。
5. **Bridge 恢复前一切采集/训练都会失败**，需先恢复 bridge。

## 🔴 第二根因（用户补充日志确认）：时区遗留 Bug — 采集入库失败

**症状**：bridge 恢复后采集返回了 1932 根 bar，但**入库抛异常**：
```
sqlalchemy.exc.DBAPIError ... asyncpg.exceptions.DataError: invalid input for query argument $3
in element #0 of executemany() sequence: datetime.datetime(2025, 4, 1, 1, 0, tzin...
(can't subtract offset-naive and offset-aware datetimes)
[SQL: INSERT INTO ohlcv_data (symbol, timeframe, time, open, high, low, close, volume) ...]
```

**根因链条（已逐环实测验证）**：
1. **bridge 新版**（时区 M1 变更，见 CLAUDE.md）返回的 OHLCV `time` 字段带 `+00:00` 后缀（旧版 naive EET）
2. `market_data.py:91` `pd.to_datetime(df["time"])` → pandas 2.2.3 解析成 **offset-aware**（`tz='UTC'`，实测确认）
3. `collector.py:80` `time_idx.to_pydatetime()` → 保留 `tzinfo=datetime.timezone.utc`（BUG 点，未做 naive 归一）
4. `ohlcv_data.time` 列是 `TIMESTAMP WITHOUT TIME ZONE`（`models.py:105`）
5. asyncpg 严格拒绝 aware datetime 写无时区列 → **整批 executemany 失败** → 采集「入库失败」

**为什么是遗留 bug**：手动单/复盘链路（`trade_reviewer.py:548-553` `_naive()`）早已有 `astimezone(UTC).replace(tzinfo=None)` 正确范式；`backtest/engine.py:115` 也是 `.replace(tzinfo=None)`；**唯独 `collector.py:80` 遗漏**。此前采集正常是因为旧 bridge 输出 naive time，pandas 解析为 naive，不触发；桥端 M1 时区升级后 explode。

**影响面**：
- `collector.py:80` 是**唯一**把 pd.Timestamp `to_pydatetime()` 直接写 `ohlcv_data` 的位置 → 所有经 DataCollector 的历史回填（bootstrap seed / POST /api/data/collect / ML 训练数据收集）在新桥端下全部入库失败
- `ml.py:184-187` / `scheduler.py:1136-1139` 写 MLModelLog（不同表，naive utcnow 生成，不受影响）

**修复方案（已验证等价性）**：
- `collector.py:80` 改为：`time_idx.to_pydatetime()` → 若 `tzinfo` 非 None，先 `astimezone(UTC).replace(tzinfo=None)`（与 `trade_reviewer._naive` / `backtest/engine.py:115` 同一范式，实测产出 `2025-04-01 01:00:00` naive，数值不变）
- 涵盖 aware 与 naive 两种输入（向后兼容旧桥端 naive 时间）

## 待验证（执行阶段）

- [ ] bridge 当前是否已恢复？（`http://192.168.3.47:8001/health`）
- [ ] 引擎当前状态（RUNNING/PAUSED/degraded）
- [ ] ohlcv_data 表 OILCash/BTCUSD 数据覆盖现状（`GET /api/data/status` 或 DB 查询）
- [ ] bridge 宕机根因：VPS 侧进程？网络？（需 Windows VPS 侧查 watchdog 日志 / 手动重启）
- [ ] 是否已发生「OILCash ML 重训失败」→ ml_status=failed？（查 symbol_configs.ml_status）
