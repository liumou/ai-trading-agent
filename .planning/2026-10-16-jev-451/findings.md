# Findings & Decisions — jev-451 挂单静默失败 + spike_chase 方向误判

## 事件（两起，同日关联）
1. **静默失败**：2026-10-16 08:54 UTC，GOLD 挂单 review_id=15，confirm 后前端报"已执行"但 MT5 无单。
   真因 = rollout shadow 拦截（用户补充 audit error_message 确认）——业务层正确，代码层缺陷是 confirm 谎报 EXECUTED。
2. **spike_chase 误判**（用户二次反馈，本次新增）：GOLD BUY_LIMIT 4160（止损 4155 / 止盈 4200 / 现价 4190）
   被拦 "blocked (emotion): spike_chase"。挂单价在现价**下方** 30 点是回调低吸，非追高。

## 根因 2：spike_chase 方向丢失（deviation_guard.py:85-87）— 已修复
旧实现：
```python
z_now = abs(float(entry) - float(ema20.iloc[-1])) / float(atr14.iloc[-1])
z_hist = ((close - ema20).abs() / atr14).dropna()
```
- `abs()` 抹掉 BUY/SELL 方向 → 无法区分"追涨（BUY 在 EMA 上方）"与"低吸（BUY 在 EMA 下方）"
- 用户场景数值：`entry − EMA = -23.7`（回调区），z=1.54 落 p100 → 误判 block
- 方向相反侧的偏离**根本不是追涨杀跌**，不应触发

### 修复：加方向校验（deviation_guard.py）
| 方向 | entry 相对 EMA | 含义 | 修复后 |
|------|--------------|------|--------|
| BUY | entry > EMA | 追涨（买在均线上方） | 判定 |
| BUY | entry < EMA | 低吸/回调（买在均线下方） | **返回 None** |
| SELL | entry < EMA | 杀跌（卖在均线下方） | 判定 |
| SELL | entry > EMA | 反弹/反抽（卖在均线上方） | **返回 None** |

验证：`entry=4160`（低吸）→ None ✅；`entry=4220`（追涨）→ 仍 block ✅
一处修复，三通道（手动 gate / systemone / preflight）共同受益。

## 根因 1：confirm 谎报成功（已修复）
- `confirm_and_execute` 无条件返回 EXECUTED；`_execute_approved` 失败路径零日志
- 修复：返回失败 dict 透传 + 补 logger + 前端补 FAILED 分支

## 验证证据
| 项 | 结果 |
|----|------|
| spike_chase 方向语义 4 新测试 | RED（2 failed）→ GREEN（12 passed） |
| spike_chase 相关 4 文件 | 80 passed |
| 根因1 2 新测试 | PASSED |
| 全量后端 | 1346 passed / 10 既有失败（feishu/ml_barrier/multi_agent，stash 反向验证） |
| tsc / ruff | 通过 |

## 资源
- `backend/app/services/deviation_guard.py`（spike_chase 纯函数 + 本次方向修复）
- 调用点：`manual_order_gate.py:937`、`systemone.py:386`、`order_preflight.py:158`
- 测试：`backend/tests/unit/test_deviation_guard.py`
