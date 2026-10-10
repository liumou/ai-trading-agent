# Task Plan: 飞书定时发送 K 线图片（M15+H1 + 均线）

## Goal
对启用的品种，在行情数据存在时，每 15 分钟向飞书群发送当前品种的 **15 分钟（M15）** 和 **1 小时（H1）** 两张 K 线图，图上叠加 **MA5/MA10/MA20 三条均线 + MA55**；按 Asia/Shanghai 时间，**00:00–08:00 不发送**。

## Next Step
✅ 全部实现完成 + 代码审查修复 + 真实端到端验证（真实品种 ID GOLD_）+ 账号隔离回归测试。
剩余（部署）：Railway env 配 FEISHU_APP_ID/SECRET + 飞书开放平台开通 im:resource 权限 + 部署后验证群收到真实 K 线图

## Current Phase
Phase 5（实现完成 + 审查修复 + 真实端到端验证完成，测试全绿）

## Phases

### Phase 1: Requirements & Discovery ✅
- [x] 理解需求（每 15 分钟 / M15+H1 / 3 均线 + MA55 / 夜窗停发）
- [x] 确认调度、K 线、飞书通知基础设施（见 findings.md）
- [x] 确认关键技术依赖（飞书发图需 app_id/app_secret）
- [x] 用户决策确认（飞书应用 / MA5-10-20 / 上海时间 0-8 停发 / 实现方向）
- **Status:** complete

### Phase 2: 飞书图片上传基础设施 ✅
- [x] 新增依赖 `mplfinance` 到 `backend/requirements.txt`（含 matplotlib）
- [x] 新建 `backend/app/notifications/feishu_image.py`：`FeishuImageUploader`（token 换取 + 缓存 2h + 失效重取 + 上传 → image_key；失败不抛出）
- [x] 配置来源：Settings 增加 `feishu_app_id`/`feishu_app_secret`（Vault → env 回落，已对齐 FeishuNotifier 模式）
- [x] `FeishuNotifier` 增加 `send_kline_card`：interactive 卡片内嵌 img 元素 + 标题/注脚
- [x] 运行期刷新：`FeishuImageUploader.reload_from`（清空 token 缓存）
- **Status:** complete

### Phase 3: K 线图生成 ✅
- [x] 新建 `backend/app/notifications/kline_chart.py`：`build_kline_png(symbol, timeframe, df, ma_periods=(5,10,20,55))` → PNG bytes（内存）
- [x] mplfinance `type="candle"` + `mav` 多周期；英文标题（用户决策，规避中文字体依赖）
- [x] 数据用 `get_ohlcv(count=100)` 足够覆盖 MA55（无需 DB 补）
- [x] 画布 12×6 @100dpi 适配飞书；空/不足数据返回 None
- **Status:** complete

### Phase 4: 调度集成（scheduler）✅
- [x] `BotScheduler.set_kline_sender()` 注入（对齐 `set_price_alert_service()`）
- [x] 注册 cron job：`minute="0,15,30,45"`，`id="kline_chart_broadcast"`，`max_instances=1, coalesce=True`
- [x] `_kline_broadcast_job()` → `KlineSender.send_all()`：夜窗判定 → 遍历启用品种 → `is_market_open` → 取 M15/H1 → 画图 → 上传 → 发卡片；逐品种失败隔离
- [x] main.py lifespan 装配：构建 `FeishuImageUploader` + `KlineSender` 注入 scheduler
- **Status:** complete

### Phase 5: 测试与验证 ✅
- [x] 代码审查（code-reviewer 子代理）→ 1 Critical + 5 Important + Minor
- [x] C1：`tick_volume→volume` 列名归一化（生产数据形态，防静默渲染失败）；测试 DF 改真实列名 + 断言
- [x] I1：token 刷新加 asyncio.Lock（并发安全）；I3：并发 Semaphore(3) 限流；I7：一次性告警
- [x] I4 实证：真实 webhook 测出卡片 img.alt 必须对象结构 → 修复 + 测试
- [x] 单元测试合计 57 全绿：`test_kline_chart.py`(15) / `test_feishu_image.py`(11) / `test_kline_sender.py`(14) / `test_symbol_resolver.py`(10) / `test_market_data_alias.py`(7)
  - `build_kline_png` 返回非空 PNG bytes（Agg 无头）+ 均线数值 + tick_volume 归一化
  - 夜窗判定边界（Asia/Shanghai 0/3/7:59/8/12/23:59）
  - `FeishuImageUploader` token 换取/缓存/失效重取/并发锁/上传（httpx MockTransport）
  - `send_kline_card` payload 含 img 元素 + 对象 alt + image_key
  - `KlineSender` 端到端：遍历品种/夜窗跳过/失败隔离/上传失败跳过/透传品种名
  - 账号切换别名跟随（GOLD_ → XAUUSD test）
- [x] 真实端到端：真实凭据 + 真实 webhook + 真实品种 ID `GOLD_` → 群收到 M15+H1 真实 K 线图（price ~4194）
- [x] ruff check 0 error；全量 pytest 不回归（multi_agent 环境敏感失败与本次无关）
- **Status:** complete

### Phase 6: 交付
- [ ] 集成页可配置 app_id/app_secret（若时间允许；否则文档说明 + Vault/env 配置）
- [ ] README/文档更新（飞书发图前提：需开放平台应用）
- [ ] 部署前 checklist（Railway env：FEISHU_APP_ID/SECRET）
- **Status:** pending

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 创建飞书开放平台应用（app_id/app_secret） | webhook 不支持直发图片，image_key 必须经开放平台上传接口获取 |
| MA5/MA10/MA20 + 固定 MA55 | 用户确认的均线组合 |
| 夜窗 Asia/Shanghai 00:00–08:00 停发 | 用户确认「晚上12点以后」语义，与前端时区一致 |
| 每品种独立并发处理 + 失败隔离 | 对齐 `_price_alert_job` 模式：单品种失败不影响其他品种 |
| 用 `mplfinance` | 依赖 matplotlib 成熟 K 线渲染，一行 `mav` 支持多均线；本地 venv 已装 matplotlib |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| WebFetch/WebSearch 底层模型报 400（provider） | 改用 curl + 领域知识确认飞书发图技术路径 |
| 飞书官方文档 JS 渲染无法 curl 解析 | 以开放平台 API 规范（im/v1/images + 卡片 img 元素）为准，写入 findings |

## Open Risks
- 飞书 app 权限：上传图片需授予 `im:resource`（或消息 API 相关）权限；需用户在飞书开放平台开通 + 发布
- 生产镜像需新增绘图依赖 → Railway 构建体积增加（matplotlib 较大）
- 每 15 分钟 × 每品种 × 2 图上传：飞书 API 频率限制需监控（可加图片/上传失败退避）
- **多账号品种 ID**：不同 MT5 账号 broker_alias 不同（如 GOLD→GOLD_ / XAUUSD）。生产靠 `load_profiles_into_memory` + `to_broker_alias` 账号感知；切换账号后需确认新账号 `symbol_configs.broker_alias` 已配置，否则行情请求会退化为 canonical 报 No data（已固化为回归测试）
