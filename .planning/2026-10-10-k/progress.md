# Progress Log

## Session: 2026-10-10

### Current Status
- **Phase:** 5 - Testing & Verification（实现完成，测试全绿）
- **Started:** 2026-10-10

### Actions Taken
1. 探索并确认基础设施：调度器注入模式、K 线获取、飞书通知、依赖缺口
2. 用户确认 4 个决策点（飞书应用 / MA5-10-20 / 上海夜窗 / 实现方向）
3. **新建** `backend/app/notifications/feishu_image.py` — 飞书图片上传器（token 换取 + 缓存 + 上传）
4. **新建** `backend/app/notifications/kline_chart.py` — K 线图渲染（mplfinance + MA5/10/20/55，英文标题规避中文字体）
5. **扩展** `backend/app/notifications/feishu.py` — `send_kline_card`（卡片内嵌 img 元素）
6. **新建** `backend/app/services/kline_sender.py` — 组合服务 + 夜窗判定 + 遍历品种
7. **修改** `backend/app/bot/scheduler.py` — `set_kline_sender` + cron job（每 15 分钟）
8. **修改** `backend/app/main.py` — lifespan 装配 KlineSender
9. **修改** `backend/app/config.py` — Settings 增加 `feishu_app_id`/`feishu_app_secret`
10. **修改** `backend/requirements.txt` — 新增 `mplfinance`
11. 用真实凭据验证：token 获取 ✅ + 图片上传 image_key ✅
12. 本地生成样例 K 线图 PNG 目检 ✅
13. 安装 ruff 并清理 lint

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| test_kline_chart.py (14) | 全绿 | 14 passed | ✅ |
| test_feishu_image.py (9) | 全绿 | 9 passed | ✅ |
| test_kline_sender.py (11) | 全绿 | 11 passed | ✅ |
| ruff check 新增文件 | 0 error | 0 error | ✅ |
| 全量 pytest | 不回归 | 1166 passed, 8 failed (multi_agent 环境敏感) | ⚠️ |

### 全量测试的 8 个失败说明
`test_multi_agent.py` 的 8 个失败**与本次改动无关**：
- 断言硬编码 `model == "claude-haiku-4-5-20251001"` / `"claude-sonnet-4-20250514"`
- 当前会话 `ANTHROPIC_MODEL=deepseek-reasoner` + provider 覆盖 → 解析为 `glm-5.3-flash` / `deepseek-v4-flash`
- 这是环境敏感测试在非标准 provider 下的既有失败

### 端到端验证（真实凭据 + 真实飞书上传）✅
- 用户提供 app_id/app_secret → 真实 `tenant_access_token` 获取成功
- 真实 PNG 上传 → 拿到真实 `image_key`（`img_v3_0216b_...`）
- `KlineSender.send_all()` 真实链路：渲染 M15+H1 → 上传 → 卡片构造 → 成功
- 凭据已写入 `backend/.env`（被 gitignore，安全，不会提交）
- 唯一未验证：真实 webhook 发群（无 webhook URL；走现有已验证 `_post` 路径）

### Errors
| Error | Resolution |
|-------|------------|
| mpf.plot 不支持 dpi kwarg | 移除，savefig 保留 dpi |
| mplfinance 重置 rcParams 字体 | 用 make_mpf_style(rc=...) 覆盖 |
| 中文字体在 Docker 缺失 | 用户决策：图内用英文 |
| 测试时处于夜窗导致 KlineSender 跳过 | KlineSender 增加 night_check 可注入参数 |
| market_open 同步函数被 await | _send_symbol 用 isawaitable 兼容同步/异步 |
| WebSearch/WebFetch 底层 400 | 改用 curl + 领域知识确认飞书 API |
| 卡片 img.alt 纯字符串 → 飞书 200621 | alt 改为对象结构 `{"tag":"plain_text","content":...}`（自定义机器人 webhook 实测） |
| 独立测试脚本用 canonical GOLD 拉不到行情 | 用真实品种 ID `GOLD_`（Bridge `/symbols` 列出）；生产经 `to_broker_alias` 账号感知 |

## Session: 2026-10-10（代码审查 + 真实端到端验证 + 账号隔离）

### Actions Taken
1. 派发 code-reviewer 子代理审查全部改动 → 1 Critical + 5 Important + Minor
2. **C1（Critical）修复**：`build_kline_png` 归一化 `tick_volume→volume`（生产 `get_ohlcv` 用 tick_volume 列，mplfinance volume=True 硬性要求 volume 列，否则生产 100% 静默渲染失败）。测试 DF 改用真实 tick_volume 列名 + 归一化断言
3. **I1 修复**：`FeishuImageUploader` token 刷新加 `asyncio.Lock`（防并发双发 + 失效复用）
4. **I3 修复**：`KlineSender.send_all` 加 `asyncio.Semaphore(3)` 限流（防 N 品种打 2N 桥请求挤压主循环）
5. **I7 修复**：上传器未配置一次性告警（对齐 price_alert_service）
6. **I4 实证**：真实 webhook 端到端测出卡片 `img.alt` 必须对象结构 → 修复 + 更新测试（前面 Errors 表）
7. **真实品种 ID 验证**：Bridge `/symbols` 列出真实符号 `GOLD_`（非 GOLD）→ 用 `GOLD_` 拉真实历史 M15/H1 → 渲染真实 K 线图 → 成功发到飞书群（价格 ~4194）
8. **账号隔离固化为回归测试**：`test_symbol_resolver` 新增账号切换别名跟随；`test_kline_sender` 新增透传品种名验证；确认生产链路（`load_profiles_into_memory` → `apply_db_symbol_profiles` → `to_broker_alias`）账号感知
9. **文档**：docs/feishu-kline-chart.md 增加「品种 ID 与账号隔离」专项说明

### Test Results（更新）
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| test_kline_chart.py (15) | 全绿 | 15 passed | ✅ |
| test_feishu_image.py (11) | 全绿 | 11 passed | ✅ |
| test_kline_sender.py (14) | 全绿 | 14 passed | ✅ |
| test_symbol_resolver.py (10) | 全绿 | 10 passed | ✅ |
| test_market_data_alias.py (7) | 全绿 | 7 passed | ✅ |
| ruff check 改动文件 | 0 error | 0 error | ✅ |
| 别名/K线/飞书 相关合计 | 57 passed | 57 passed | ✅ |

### 端到端真实验证（真实凭据 + 真实 webhook + 真实品种 ID）✅
- webhook 配置后：真实 PNG → 上传 image_key → 卡片（对象 alt）→ 群收到
- `GOLD_` 真实历史：M15 100 bars（最后 2026-10-10 07:45，close 4194.60）+ H1 100 bars → 两张真实图发群
- 周末休市，最后数据停在周五收盘属正常

### 生产就绪状态
- **未上线前仍需**：Railway env 配 `FEISHU_APP_ID/SECRET`、飞书开放平台开通 `im:resource` 权限、部署后验证
- **多账号注意**：切换账号后 KlineSender 自动跟随新账号 broker_alias，无需额外配置（已固化为测试）
