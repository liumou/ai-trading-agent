# Task Plan: analyze_recent_trades 工具报错分析与修复计划

## Goal
定位 `analyze_recent_trades` 工具返回 `'str' object has no attribute 'get'` 异常的根本原因，产出修复方案，计划经用户同意后执行。

## Next Step
已修复并验证（66/66 测试通过）。向用户提交完成报告，等待部署验证。

## Current Phase
Phase 1（分析完成，待交付）

## Phases

### Phase 1: 根因分析 — ✅ complete
- [x] 定位 analyze_recent_trades 实现（backend/mcp_server/tools/learning.py:14-93）
- [x] 确认 /api/history/trades 响应格式（backend/app/api/routes/history.py:184 → `{"trades": [...], "total": n}`）
- [x] 确认调用方如何消费（chat_runtime 注册为工具，返回 dict 序列化给 LLM）
- [x] 排查同类工具（get_trade_history 同样误读响应 → 结构畸形嵌套）
- [x] 排查测试覆盖（无解析逻辑单测，仅工具名注册断言）

### Phase 2: 方案设计 — ✅ complete
- [x] 修复 learning.py 解析：`trades = resp.json().get("trades", [])`
- [x] 修复 history.py 解析：`return {"trades": resp.json().get("trades", []), "total": ...}`
- [x] 防御性加固：把 `trades` 若为 dict 时归一为 `get("trades", [])`
- [x] 补充单元测试（mock httpx 响应）

### Phase 3: 实施 — ✅ complete
- [x] 应用代码修复（learning.py + history.py）
- [x] 更新测试（test_mcp_backend_callback.py 新增 4 用例）

### Phase 4: 验证 — ✅ complete
- [x] 运行相关测试（66/66 通过，无回归）
- [x] 手动验证工具返回（mock 验证通过）
- [x] py_compile 语法验证（venv 无 ruff，CI 覆盖 lint）

### Phase 5: 交付 — ✅ complete
- [x] 向用户提交修复报告
- **Status:** complete

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 分析范围限定于"为什么报错 + 修复方案"，不做代码修改 | 用户明确要求：计划经过我同意才能执行 |

## 根因（摘要）

`analyze_recent_trades`（learning.py:14）在第 41 行把 `/api/history/trades` 的完整 JSON body 直接赋给 `trades`：

```python
trades = trades_resp.json() if trades_resp.status_code == 200 else []
```

但该接口返回的是 `{"trades": [...], "total": n}`（history.py:184）—— 是一个 **dict 而非 list**。于是第 48 行 `for t in trades` 遍历 dict 时拿到的是字符串 key `"trades"` 和 `"total"`，第 49 行 `t.get("profit", 0)` 就抛 `AttributeError: 'str' object has no attribute 'get'`，被第 92 行 except 捕获后返回 `{"error": "Failed to analyze trades: ..."}`。报错信息完全吻合。

**同类 bug**：`get_trade_history`（history.py:27）同样把整个 body 塞进 `{"trades": resp.json()}`，产生 `{"trades": {"trades": [...], "total": n}}` 嵌套畸形结构，虽不崩溃但 AI 读到的数据形状错误。

## Errors Encountered
| Error | Resolution |
|-------|------------|
| 无 | 纯分析任务，未修改代码 |
