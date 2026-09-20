# Task Plan — 修复 Gold 选择时报 asset_class 与券商路径不匹配错误

## Goal

修复品种页面选择 Gold 时报错：
`asset_class='metal' does not match broker path 'Derivatives\SpotMetals_\GOLD_' (inferred 'forex')`
使 Gold（及同类 XM Derivatives 路径品种）可正常通过准入校验。

## Next Step

全部完成 —— 等待用户重启后端做人工验收（品种页选择 Gold 不再报错）。

## Current Phase

Complete（代码侧）— 人工验收待用户重启后端

## 根因分析

- 券商（XM）返回品种路径为 `Derivatives\SpotMetals_\GOLD_`，"metal" 字样在**第二段** `SpotMetals_`，第一段是 `Derivatives`。
- `backend/app/api/routes/symbols.py:342 _infer_asset_class()` **只取路径第一段**（`path.split("\\")[0]`）匹配 `_PATH_TO_CLASS` 表；`derivatives` 不在表内 → 兜底返回 `"forex"`。
- 该推断函数同时服务：
  1. broker-catalog（`symbols.py:596`）——目录自动填充的 asset_class；
  2. `_cross_check_asset_class()`（`symbols.py:470`，在 645 / 792 / 906 三处调用：创建、更新、启用校验）。
- 数据库中 Gold 的 `asset_class='metal'`（与 `config.py` / `SYMBOL_PROFILES` 约定一致），推断结果 `forex != metal` → fail-closed 抛 400。
- 本质是**推断函数太窄**，而不是用户配置错。配置 `metal` 是正确语义。

## 备选方案

- **A（推荐）：增强 `_infer_asset_class`** —— 对完整路径（全部段拼接，含去掉 `_` 等噪声后）按词匹配 `_PATH_TO_CLASS`，保持首段优先；`Derivatives\SpotMetals_\GOLD_` → 命中 `metals` → `metal`。单点修复，catalog 与 cross-check 同时受益，向后兼容（现有 forex 路径仍命中首段）。
- B：放宽 cross-check（路径无法判定时不拒绝）—— 削弱 fail-closed 设计，放弃。
- C：数据库改成 forex —— 语义错误（金不是 forex，影响 pip 建议、交易时段、策略映射），放弃。

## Phases

### Phase 0: 规划与分析 (complete)
- [x] 定位报错来源 `symbols.py:480` 与推断逻辑 `symbols.py:342`
- [x] 确认 XM 路径首段 `Derivatives` 导致兜底 `forex`
- [x] 确认 `_infer_asset_class` 的 3 类消费方
- **Status:** complete

### Phase 1: 后端修复 (complete)
- [x] 重写 `_infer_asset_class`：全路径小写 + 归一化（去 `_`/空格），优先首段精确类别词，未命中再扫全路径词表；保持默认 `forex`
- [x] catalog 缓存 key bump（`xm:catalog:v1` → `v2`），避免 1h Redis 旧缓存
- [x] 单测 `tests/unit/test_infer_asset_class.py`：8 个用例（XM SpotMetals→metal、forex/crypto/energy/index/stock、空路径、未知路径）
- **Status:** complete（24 unit + 36 integration 全绿；端到端模拟：`_cross_check_asset_class({'path': 'Derivatives\\SpotMetals_\\GOLD_'}, 'metal')` 不再抛 400）

### Phase 2: 验证 (complete)
- [x] 运行相关单测与集成测试（全部通过）
- [ ] 人工验收（需用户重启后端）：品种页选择 Gold 不再报错；Add Symbol 目录中 SpotMetals 显示 metal
- **Status:** complete（代码侧；人工验收待用户）

## Risks

- 全路径匹配可能误伤（如路径含 "index" 字样的其他品种）——用归一化后的整词匹配缓解，并在单测覆盖。
- 已缓存目录 1 小时内仍是旧推断 —— 通过 bump 缓存 key 解决。

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 方案 A：增强 `_infer_asset_class` 全路径匹配 | 单点修复，保持 fail-closed 设计，向后兼容 |

## Errors Encountered
| Error | Resolution |
|-------|------------|
