# 第二轮多路代码评审：提交 1f51b41（laya 影子观测）

日期：2026-09-22
方式：OCR 委托模式（`open-code-review v1.12.8`）选定范围（13 个可评审文件）+ 三路并行评审（架构/安全风控/测试验证）+ 主持人复核证据。
范围：Phase 3.0–3.3 + Phase 4 同批提交的实现与测试。

## 评审结论
并发骨架（事件驱动生命周期、finally 必调 finish、异常吞掉、迁移单头链、纯函数边界）整体成立；
**但存在 2 个必须修的高危缺陷（H1 数据污染、H2 H-3 非干扰性泄漏路径）**，建议在观测期数据产生前修复，否则污染 4 周分歧数据集。

---

## High（必须修）

### H1 持仓快照恒为空 —— 观测数据被系统性写空（架构路，主持人已复核）
- 位置：`backend/app/ai/laya_engine_observation.py:113`（`_compact_position`，调用点 161）
- 证据：`order_executor.get_open_positions()` 返回 `list[dict]`（`order_executor.py:91`，内部即 `p.get("symbol")`），而 `_compact_position` 用 `getattr(p, "symbol", "")` 取值——对 dict 永远命中默认值。
- 后果：laya 6 问的 `positions` 输入与落库 `state_snapshot` 持仓明细恒为空（`positions_count` 单独计算所以准确）；risk_check 问在无持仓规模/敞口证据下判定；Phase 4 分歧数据集被污染，离线复核无可用持仓数据。
- 掩盖：单测 `test_laya_engine_observation.py:69` 用带属性的类实例 `P`，生产是 dict。
- 修复：`_compact_position` 同时支持 dict（`p.get`）与对象（`getattr`）；补 dict 用例。

### H2 engine wrapper 边界未做异常隔离 —— H-3 存在可证明泄漏路径（安全路）
- 位置：`backend/app/bot/engine.py:820-846`
- 证据：`from app.ai.laya_engine_observation import start_engine_observation`、`start_engine_observation(...)` 都在 `try` 之外；`finally` 里 `obs.finish(allowed)` 也无保护。laya 模块 import 失败 / `asyncio.create_task` 在 loop 关闭竞态抛 `RuntimeError` / `__init__` 意外异常，都会穿透 `_check_trade_permission`；调用方 `process_candle` 顶层 `except`（engine.py:589-594）会变成「本周期不开仓 + BotState.ERROR 整机停摆」。
- 后果：影子故障改变实盘行为，违反 H-3「laya 故障/超时/崩溃绝不改变返回结果」。
- 修复：观测创建/收尾全包 try/except（观测失败记日志、返回 None），`obs.finish` 也包保护；加故障注入测试（start_engine_observation 抛异常 → 返回值不变）。

---

## Medium（建议观测期开始前修）

### M1 手动下单路径同步等待 laya —— 最坏推迟真钱订单约 35s（安全路）
- 位置：`backend/app/services/manual_order_gate.py:208`
- 证据：`laya_review = await laya_task` 在执行分支之前同步等待，`_laya_shadow_review` 上限 `laya_gate_predict_timeout_s+5`=35s；`.env` 已开 `LAYA_GATE_SHADOW=true`。
- 后果：进程冷启动后首批手工单，laya 冷加载/下载卡住时订单执行推迟最多 ~35s，H-1「执行前重验硬状态」漂移窗口被拉大。verdict 不变，但时序上非零干扰。
- 修复：关键路径只给小预算 await（如 3s），超时即跳过影子摘要；落库改为后台 fire-and-forget（完成后补写专表与 review["laya"]）。

### M2 推理超时不停止底层线程 + 共享 _agent 无串行化（架构路，已实测）
- 位置：`backend/app/ai/laya_runtime.py:97`（`predict`）、167-169（`warmup`）
- 证据：`asyncio.wait_for(asyncio.to_thread(...))` 超时只取消 await，线程继续跑完；`_load` 有锁但推理本身无串行化，而 docstring 自认 Agent 存在可变共享状态。
- 后果：模型卡死/慢于超时时每次检查都遗留一条 CPU 线程，并并发启动新的 `system_one`；反复超时线程无限累积，最终占满进程级 ThreadPoolExecutor。
- 修复：推理加锁串行化；超时后把 runtime 置不可用（fail-stop），避免后续堆积；补超时测试。

### M3 首次触发在事件循环上同步 `import laya`（安全路）
- 位置：`backend/app/ai/laya_runtime.py:26`
- 证据：`laya_gate.py` 在协程内 `from app.ai.laya_runtime import get_laya_runtime` → 首次触发模块顶层 `import laya`（torch/transformers 栈）在事件循环上同步执行，阻塞全部品种协程数秒到数十秒。
- 修复：try-import 改为懒加载/线程内预热（warmup 已有，但 import 阶段要离环），或应用启动期在线程中预导入。

### M4 空测试类虚标：`TestPredictTimeoutWarmup` 无任何 test 方法（测试路，已复核）
- 位置：`backend/tests/unit/test_laya_runtime.py:556`
- 后果：docstring 宣称覆盖「predict 超时 + 启动预热（H3/H4）」，实际 pytest 收集 0 例；`predict` 超时分支与 `warmup()` 完全无测试。
- 修复：补真实超时/预热测试（与 M2 一起）。

### M5 classify_divergence 矩阵不完整 + 未知 verdict 落入 ABSENT（测试路）
- 位置：`backend/tests/unit/test_laya_engine_observation.py:100`
- 缺口：chain=None 与 REJECTED/ESCALATE/CAUTION/UNAVAILABLE 组合、`(None,None)`、`(False,None)`、`(False,UNAVAILABLE)`、未知 laya verdict fallthrough（未知值被错标为「影子未启用」）。
- 修复：补全 21 组合 + 决策未知值语义（建议未知 → UNAVAILABLE 而非 ABSENT）并钉测试。

### M6 engine 侧 `_persist` ORM 映射从未执行（测试路）
- 位置：`backend/tests/unit/test_laya_engine_observation.py:158-252`
- 后果：生命周期测试全部 `patch.object(obs, "_persist")`，真实 `LayaEngineObservation(...)` 列映射从未构造；与 models/迁移列不一致测试全绿（Phase 3 的 `TestShadowPersist` 是正确做法）。
- 修复：仿 Phase 3 用 fake session 实跑 `_persist`。

### M7 `laya_gate.py:244` 硬编码 `min_confidence=0.6`（主持人）
- 证据：config 有 `laya_gate_confidence_threshold=0.6` 但收敛器调用点硬编码，配置旋钮未接线。
- 修复：改用 `settings.laya_gate_confidence_threshold`；补 config wiring 测试。

### M8 观测未判 `laya_enabled` —— 全 UNAVAILABLE 噪音行 + 敏感快照留存（安全路）
- 位置：`backend/app/ai/laya_engine_observation.py:311`
- 后果：laya 未启用/加载失败时每次开仓检查仍新建后台任务并落 UNAVAILABLE 行，且 `state_snapshot` 明文留存 balance/daily_pnl/持仓，稀释报表分母。
- 修复：`start_engine_observation` 或 `_run_laya_review` 增加 `laya_enabled` 门控（未启用连任务都不建）；评估快照脱敏。

---

## Low（可并入同批）

- **L1** `laya_engine_observation.py:184-185,293-296`：inner 异常时 `allowed=None`，`divergence_final` 静默回退到 TradeGate 口径，无最终判定却计入 final 分歧 → 仅当 `allowed is not None` 才写 final 口径（否则 NULL/独立类别）。
- **L2** 迁移索引与模型不一致：`c2d3e4f5a6b7` 建了 `ix_laya_engine_observations_signal_label` 但模型 `signal_label` 无 `index=True`；`b0c1d2e3f4a5` 的 created_at 索引同理 → 统一（模型补 index=True）。
- **L3** 文档不一致：`laya_engine_observation.py:8` docstring 称默认 False，`config.py:339` 实际 True，phase4-observation.md 写「默认关」→ 统一文档 + 加 config 默认值回归测试。
- **L4** `laya_engine_report.py:64`：空输入分支缺 `laya_absent` 键，非空分支有，schema 不一致 → 补键。
- **L5** `laya_engine_report.py:45`：`percentiles` 用 nearest-rank（ceil），`laya_gate_report.latency_percentiles` 用线性插值，docstring 却称「等价物」→ 统一或改文档。
- **L6** 未使用 import：`laya_engine_observation.py`（math/List）、`laya_engine_report.py`（Optional/DIV_LAYA_ESCALATE/DIV_LAYA_UNAVAILABLE/List）→ 清理。
- **L7** 权重供应链（安全路，识别不修）：HF repo 无 revision 锁定/无校验和，`LAYA_HF_ENDPOINT=hf-mirror.com` 镜像可被篡改 → 已列入 Phase 5 权重 root-of-trust，本轮不动。
- **L8** 测试边界补强：Kappa/Wilson 边界（n=1、全单类、agree=0）、converger 跨规则优先级（insufficient/低置信先于 block）、阈值恰好 0.6 与越界、manual/engine 超时分支故障注入、engine H-3 端到端组合（wrapper 真实观测器 + laya 崩溃 → 返回值不变）。

---

## 验收口径（修复后）
- 全部相关单测通过（预计新增 ~25-35 例）；laya 相关套件 ≥160 passed
- H1：生产 dict 持仓落库非空（新测试钉住）
- H2：start/finish 故障注入下 `_check_trade_permission` 返回值不变
- M1：manual gate 关键路径等待预算 ≤3s（测试断言等待时长）
- M2/M3：超时后 runtime 置不可用、无线程堆积；import 不在事件循环执行
- 无新行为变更（仍是观测-only，不 enforce）
