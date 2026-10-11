# Task Plan: ML 数据采集异常修复

## Goal

排查并修复「ML 模型页面数据采集异常」。**两个独立根因**：
1. **MT5 Bridge 宕机**（10-10 14:45 ~ 10-11 凌晨 502）→ 采集期间 bridge 不可用，全部 chunk 失败（现 bridge `/health` 已恢复 200）。
2. **时区遗留 Bug（代码缺陷）**：新 bridge 输出 `+00:00` aware 时间 → `collector.py:80` `to_pydatetime()` 未 naive 归一 → asyncpg 拒绝写 `ohlcv_data`（无时区列）→ **即使 bridge 恢复，采集入库也必失败**。

计划：**修复 collector 时区 bug → 校验 bridge 链路 → 重跑采集回填 → 验证 ML 数据覆盖 → 按需重训 → 验证 ML 页面恢复**。

## Next Step

全部 Phase 完成。待用户操作：ML 页面点击 OILCash Train（或等周度 cron 周一 04:00 UTC），把 OILCash `pending` 推进到 `ready`。

## Current Phase

**All Phases complete**（Phase 1-6 全部完成，2026-10-11）

### 修复方案（已确认范式）
- `app/data/collector.py:80`：`time_idx.to_pydatetime()` 后做 aware→naive UTC 归一：
  ```python
  dt = time_idx.to_pydatetime()
  if dt.tzinfo is not None:
      dt = dt.astimezone(UTC).replace(tzinfo=None)
  ```
  与 `trade_reviewer.py:548-549` `_naive()`、`backtest/engine.py:115` 同一范式；兼容旧 bridge naive 输入与新版 aware 输入。

## Phases

### Phase 1: 修复时区入库 Bug（代码改动）
- [x] 修改 `app/data/collector.py:76-82`：`to_pydatetime()` 后 aware→naive UTC 归一（`astimezone(UTC).replace(tzinfo=None)`，兼容 naive 输入）；import 加 `UTC`
- [x] 新增 `tests/unit/test_collector.py` 3 用例：aware 归一 / naive 保留 / ON CONFLICT 幂等（autouse fixture 为 SQLite 补建 `uq_ohlcv_symbol_timeframe_time` 与生产一致）
- [x] 回归：test_collector 3/3 过；ruff 通过；test_market_data_alias / test_trade_reviews / test_ml_strategy 55/55 过；全量 1223 passed（10 failed 均为 env 配置类，与本改动无关）
- **Status:** complete

### Phase 2: 校验链路与状态（只读）
- [x] 确认 bridge `/health` 稳定恢复（连续 3 次 200）
- [x] 用后端密钥 `MT5_BRIDGE_API_KEY` 测 `/ohlcv/{symbol}/history`：`GOLD_` 正确别名；返回 `"time":"2025-04-01T01:00:00+00:00"` —— **确凿验证 aware `+00:00` 输入存在**（正是修复前触发 DataError 的格式）
- [x] 确认符号别名：`GOLD_` = broker 别名（`to_broker_alias` 处理），`GOLD`/`XAUUSD`/`Gold` 无数据
- [x] 查 DB `ohlcv_data` 现状（BTCUSD H1 到 09-14、M15 到 09-14、OILCash M15 到 10-09、GOLD M15 到 10-09）
- [x] 查 `symbol_configs.ml_status`（OILCash=pending、BTCUSD/GOLD/US30Cash=ready）
- [x] 查引擎当前状态（bot_state=STOPPED；bridge/redis/ai 均健康）
- **Status:** complete

### Phase 3: 重跑数据采集（回填缺失历史数据）
- [x] BTCUSD H1+M15 回填（13393 / 53565 bars，缺口 09-14→10-11 补齐）
- [x] OILCash M15 回填（35765 → 39789 bars，到 10-09 23:00）
- [x] GOLD M15+H1 回填（首轮 0 bars → 定位临时脚本漏 `load_profiles_from_db()` → 修正后 36116 / 9034 bars）
- [x] 确认每 chunk 入库成功（**零 DataError**，exit 0）
- [x] 验证 `-19` = asyncpg `executemany` `rowcount=-1` 报告惯例（DB 实际增长证实，非负插入）
- **Status:** complete

### Phase 4: 验证数据覆盖 & ML 状态
- [x] 近 90 天窗口 bars 全部 ≥500：OILCash M15=6530、GOLD M15=5939、BTCUSD H1=2161、GOLD H1=1486、OILCash H1=1187
- [x] 确认 `ml_status`：BTCUSD/GOLD/US30Cash=ready；OILCash（两账号）=pending
- [x] OILCash 训练推进属用户操作（`/retrain` 需 engine，bot STOPPED 时 503）—— **未擅自触发**（会改生产模型）
- [x] ML 页面数据异常根因已消除（数据覆盖图表将正常显示）
- **Status:** complete

### Phase 5: 根因归档与防复发（评估完成，无新增代码）
- [x] 时区处理全链路排查：`collector.py` 是唯一遗漏点（`trade_reviewer._naive`、`backtest/engine.py:115` 已正确）
- [x] 别名退化机制已文档化：`load_profiles_into_memory` docstring 明确警告未加载则别名原样返回（本次临时脚本教训与该警告一致）
- [x] `ml_status` 失败落库已正确（3 条失败路径均写 "failed"）
- [x] Bridge 宕机前置检查 / 采集告警 = 优化项，非正确性缺陷，不做大改
- **Status:** complete

### Phase 6: 交付验证
- [x] 数据覆盖恢复：BTCUSD 09-14→10-11、OILCash/GOLD →10-09，90 天窗口全部达标
- [x] BTCUSD/GOLD ML=ready；OILCash=pending 待用户训练（数据已就绪，非阻塞）
- [x] 回归：test_collector 3/3、相关 55/55、全量 1223 passed（10 failed 均 env 配置类）
- [x] 临时脚本清理
- [x] git commit（英文信息）
- **Status:** complete

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| **根因 1 = MT5 Bridge 宕机** | 日志证据完整：10-10 21:24 health_monitor 判定 unreachable 并 PAUSED，10-11 04:44-04:48 采集全 502 |
| **根因 2 = 时区遗留 Bug（collector.py:80 未 naive 归一）** | 用户补充的错误日志确认：bridge 恢复后采集返回 1932 bars，但 asyncpg `DataError: can't subtract offset-naive and offset-aware datetimes`。逐环实测：bridge `+00:00` → pandas aware → `to_pydatetime()` 保留 aware → 无时区列拒绝。**这是导致「入库失败」的直接原因** |
| **先修 bug 再重采集** | 不修 collector 时区 bug，bridge 恢复后重采集也必失败（root cause 不变，白跑） |
| **修复范式复用 `_naive()` · `backtest:115`** | `astimezone(UTC).replace(tzinfo=None)` 已是项目内多次验证的正确 naive UTC 范式；collector 遗漏，补上即一致 |
| **采集后必须验证数据覆盖 + ml_status** | 只重跑采集不验证 = 无法确认 ML 页面真的恢复；`_ml_retrain_symbol` 对 <500 bars 会标 failed（scheduler.py:1040-1046） |
| **Phase 5 加固项按需评估，不做大改** | 核心是修复 bug + 恢复数据；防复发加固（bridge 检查前置跳过、ml_status 失败落库）需用户确认范围，避免过度工程 |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| MT5 Bridge `/health` 与 `/ohlcv/*` 502（10-10 14:45 ~ 10-11 凌晨） | 1（日志分析） | 根因 = bridge 服务宕机；已实测恢复 200 |
| **asyncpg DataError: can't subtract offset-naive and offset-aware（入库失败）** | 1（代码审计 + 实测复现） | 根因 = `collector.py:80` `to_pydatetime()` 保留 aware；修复 = naive UTC 归一（与 trade_reviewer._naive 同范式）。用户提供该错误日志 |
| bridge `/ohlcv/{symbol}/history` 无 `x-bridge-key` 返回 422 | 1 | 密钥在 `.env:MT5_BRIDGE_API_KEY`，connector 注入 `X-Bridge-Key`；实时带密钥探测被权限分类器超时挡住，留待 Phase 2 执行 |
| Redis `Connection reset by peer` / 超时（04:02-04:35，Tailscale 100.72.200.33） | 1 | 伴随网络抖动，需在 Phase 2 确认是否仍存在（可能独立于 bridge 问题） |

## 备注

- 本次排查**未改动任何代码**（仅读取日志 + 探测 bridge 健康）。
- bridge 恢复探测：`curl http://192.168.3.47:8001/health` 返回 200（10-11 排查时实测）。
- 采集失败日志关键位置：`errors.log:04:47:31-51`（historical OHLCV 502 ×6）、`bot.log:04:46-04:49`（Collecting 无 Chunk 成功）。
- 需用户确认：bridge 侧（Windows VPS）宕机根因与恢复动作，是否由用户在 VPS 侧处理（重启 mt5_bridge + watchdog）。
