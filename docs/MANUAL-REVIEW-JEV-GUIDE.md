# 手动单 JEV 审查 — 使用与配置指南

> 状态：**已配置完毕、运行中后端已生效**（JEV 主审 + 本地规则秒级担保 + LLM 兜底）。
> 你只需要**验证**（下第一笔单前后各看一处），不需要改任何东西，除非想调阈值。
>
> **技术参考**（请求/响应协议、手动单全流程、详细时序图）见 [JEV-INTEGRATION.md](./JEV-INTEGRATION.md)。

## 1. 现状（已完成，无待办）

`.env`（backend/.env，gitignore 已覆盖、密钥不进版本库）：

```dotenv
MANUAL_REVIEW_PROVIDER=typesafe_jev            # JEV 主审；local 为第二链，LLM 兜底
MANUAL_REVIEW_TYPESAFE_API_KEY=oc_sk_***       # 你的 key（已写入）
MANUAL_REVIEW_TYPESAFE_BASE_URL=https://opencode.ai/zen/v1/systemone
MANUAL_REVIEW_TYPESAFE_MODEL=jev-1.13-free
MANUAL_REVIEW_TYPESAFE_CONF_FLOOR=0.15         # 真实模型实测后调低（0.30 会吞掉风险否决）
```

运行中进程的启动日志（`backend/logs/bot.log`，重启后可见）会打印一条：

```
Manual review provider=typesafe_jev ... | JEV(configured): base=... model=jev-1.13-free
timeout=8.0s min_confidence=0.55 conf_floor=0.15
```

`JEV(configured)` 表示 key 已生效；若显示 `NOT configured` 就是没读到 key（多为启动目录不对，`.env` 相对 CWD 读取，必须从 `backend/` 目录启动）。

## 2. 你唯一要做的事：验证

### 第一次验证（下第一笔手动单时）

1. 从前端手动交易页提交一笔订单。
2. 观察审查耗时：**约 1~1.5 秒返回判决**（JEV 实测延迟；原 LLM 路径最长 90 秒）。
3. 审计确认（翻库，只读）：

```sql
SELECT id, symbol, status, error_message,
       review->'systemone'->>'provider' AS provider,
       review->'systemone'->>'converge' AS converge,
       review->'llm'->>'verdict' AS verdict,
       created_at
FROM order_audits ORDER BY id DESC LIMIT 3;
```

正常时应看到 `provider = typesafe_jev`、`converge` 含五检查与 verdict、`llm.verdict` 为兼容映射（两字段同存）。

### 如果 `provider` 为空（只有 llm.verdict）

说明当次走了 LLM 兜底。查日志确认降级原因（正常会有 `systemone degraded` 的 WARNING 行，写明哪个 provider 为什么失败）：

```bash
grep degraded backend/logs/bot.log | tail -5
```

如果**没有降级日志**却走了 LLM 路径，这是异常（部署首日曾出现过一次，未定位）——把审计行 id 和这段日志贴给我，或重启后端（见 §5）后盯着日志再下一单。

## 3. 判决语义（知道这个才不会意外）

| JEV 返回 | 落到的档位 | 你看到什么 |
|----------|-----------|-----------|
| 全检查 clear 且置信 ≥0.55 | **APPROVED** — 直接执行 | 无确认页 |
| 任何 caution 档 / 置信 0.15~0.55 | **CAUTION** — 等你在 120s 内二次确认 | 「请确认」弹窗 |
| risk/execution = block，或 signal=conflict ∧ regime=adverse | **REJECTED** — 拒单（不可覆写） | 拦截原因 |
| 置信 <0.15 或请求失败 | 降级 local 规则（秒级）→ 再失败 → LLM（慢） | — |

免费模型 `jev-1.13-free` 实测置信带宽 0.17~0.36，**常规单大概率落 CAUTION 让你一键确认**——这是设计（AI 倾向通过但把握不足时不自动放行），不是故障。想少确认一点，见 §4 第 3 条。

## 4. 旋钮速查（全部 env，改后重启生效）

| 变量 | 当前默认 | 作用 / 何时调 |
|------|---------|--------------|
| `MANUAL_REVIEW_PROVIDER` | `typesafe_jev` | `local_jev`＝本地规则主审（毫秒级，JEV 降级中继）；`llm`＝完全回滚旧行为（且不写 systemone 审计） |
| `MANUAL_REVIEW_MIN_CONFIDENCE` | `0.55` | JEV「直接放行」的置信线。**想减少 CAUTION 确认就调低它（如 0.45）**，不要动 floor |
| `MANUAL_REVIEW_TYPESAFE_CONF_FLOOR` | `0.15` | 低于此视为噪声 → 降级。**不要调高**——0.30 会把风险否决一起吞掉（实测 S3 危险单 block 仅 0.33 置信） |
| `MANUAL_REVIEW_TYPESAFE_TIMEOUT_S` | `8` | JEV HTTP 超时（只包 JEV 段） |
| `MANUAL_REVIEW_TYPESAFE_PROXY_URL` | 空 | JEV 出网代理（对齐 telegram_proxy_url 用法） |
| `MANUAL_REVIEW_TYPESAFE_CIRCUIT_THRESHOLD` / `_COOLDOWN_S` | `3` / `300` | 连续失败 N 次冷却 N 秒跳过 JEV（防每单白等） |
| `MANUAL_REVIEW_TYPESAFE_API_KEY` / `_BASE_URL` / `_MODEL` | 已配 | 换模型/换端点时改 |
| `MANUAL_REVIEW_DEGRADED_ALERT_THRESHOLD` | `3` | SystemOne 连续失败 N 次发 CIRCUIT_BREAKER 事件 + Telegram 聚合告警 |
| `MANUAL_REVIEW_*`（规则阈值组） | 见启动日志 | 本地规则引擎 19 个参数（spike/risk_pct/size/rr…），启动日志会 dump 全部生效值 |

## 5. 重启 / 回滚 / 切换

重启（从 backend 目录，conf_floor 等新变量才会进启动日志）：

```bash
cd backend && ./../start-backend.sh        # 或 .venv/bin/uvicorn app.main:app --port 8002
```

回滚（一行，重启即还原旧行为，且审计不再写 systemone）：

```dotenv
MANUAL_REVIEW_PROVIDER=llm
```

## 6. 运维注意

- **MT5 桥必须在线**：JEV/local 都要实时拉行情，桥不稳时链会顺延；桥失败但 LLM 可用时订单仍可审（就是慢）。
- **AutoTrading 开关**：执行段若报 `AutoTrading disabled by client (code: 10027)`，是 MT5 终端设置问题（EA 交易未开），与 JEV 无关。
- **熔断只影响 JEV 这一环**：JEV 熔断后自动落到 local（毫秒级），不影响 fail-closed 语义。
- **数据一致性**：state 里行情证据与下单参考价来自同一实时源；周末休市 K 线陈旧会判 `data_quality 不足 → CAUTION`，属正常。
- **密钥纪律**：JEV key 只存 .env/vault，禁止进代码/审计/日志/前端（有单元测试锁定此行为）。