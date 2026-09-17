# Findings — analyze_recent_trades 工具报错分析

## 目标问题
用户报告：生成报告时出现数据缺口提示 `analyze_recent_trades 工具返回后端异常（'str' object has no attribute 'get'）`。

## 证据链

### 证据 1：接口返回格式
`backend/app/api/routes/history.py:184`：
```python
return {"trades": paginated, "total": len(rows)}
```
`GET /api/history/trades` 返回的是 **`{"trades": [...], "total": n}`**（dict），不是裸数组。

### 证据 2：工具的误读
`backend/mcp_server/tools/learning.py:41`：
```python
trades = trades_resp.json() if trades_resp.status_code == 200 else []
```
把整个 dict 赋给 `trades`。此时 `trades` = `{"trades": [...], "total": n}`。

`learning.py:48-49`：
```python
wins = [t for t in trades if t.get("profit", 0) > 0]
```
`for t in trades` 遍历 **dict 的 key**（字符串 `"trades"`、`"total"`），`t.get("profit", 0)` 在字符串上调用 → `AttributeError: 'str' object has no attribute 'get'`。

`learning.py:92-93` 的 except 兜底把异常转成 `{"error": "Failed to analyze trades: ..."}` 返回给 AI → 报告中出现"数据缺口提示"。

### 证据 3：同类 bug
`backend/mcp_server/tools/history.py:25-27`：
```python
resp = await client.get(f"{_backend_url()}/api/history/trades", ...)
if resp.status_code == 200:
    return {"trades": resp.json()}
```
`resp.json()` 是 `{"trades": [...], "total": n}`，再包一层 `{"trades": ...}` → AI 拿到 `{"trades": {"trades": [...], "total": n}}`。**结构畸形嵌套**（不崩溃，但数据形状错）。

### 证据 4：性能接口（无此问题）
`/api/history/performance` 返回裸 dict（无嵌套），`learning.py:42` 直接使用没问题。

### 证据 5：测试覆盖缺失
`backend/tests/unit/test_phase_e.py` 只有工具名注册断言（`"analyze_recent_trades" in TOOL_NAMES`），**无解析逻辑单测** —— 所以此 bug 未被测试捕获。

## 影响范围
- Reflector agent、Chat Runtime 调用 `analyze_recent_trades` → 报告缺失逐笔统计（报错路径）
- Chat Runtime 调用 `get_trade_history` → 结构畸形但不崩溃（潜伏路径）
- 两个工具都注册在 `chat_runtime.py:22` READONLY_TOOLS 中

## 修复方案（候选）

### 方案 A（最小修复，推荐）
1. `learning.py:41`：`trades = trades_resp.json().get("trades", [])`（对 dict 用 `.get`；对意外裸数组做防御）
2. `history.py:27`：`return {"trades": resp.json().get("trades", []), "total": resp.json().get("total", 0)}`
3. 补充解析逻辑单测（mock httpx）

### 方案 B（更稳的防御）
在方案 A 基础上，把 `trades` 归一逻辑提取成 helper，统一处理"接口返回 dict vs 裸数组"两种形状。适用于接口格式未来可能变化。

### 方案 C（接口契约规范化）
后端改为直接返回裸数组，或工具层强校验 schema。成本高，破坏面大，不推荐现在做。

## 推荐
方案 A。改动小（2 个文件各 1 行 + 测试），直击根因，风险低。

## 验证方法
1. mock httpx 返回 `{"trades": [...], "total": n}`，断言 analyze_recent_trades 正常返回统计
2. mock httpx 返回裸数组（防御），断言仍正常
3. 运行 `pytest tests/unit/test_phase_e.py tests/unit/test_agent_chat.py -v --no-cov`
