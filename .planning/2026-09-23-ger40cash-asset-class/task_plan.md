# Task Plan: GER40Cash 激活 asset_class 交叉校验失败修复

## Goal
修复用户激活 GER40Cash 品种时因数据库 `asset_class='forex'` 与券商路径推断类别 `index` 冲突而 400 失败的问题，使激活流程顺畅通过。

## Next Step
实施 toggle 启用时的 asset_class 自动修正（方案 A）+ 补充集成测试

## Current Phase
Phase 2

## Phases

### Phase 1: Requirements & Discovery
- [x] 复现报错消息：`asset_class='forex' does not match broker path 'Derivatives\Cash\Cash Indices\GER40Cash' (inferred 'index')`
- [x] 定位报错来源：`backend/app/api/routes/symbols.py:485 _cross_check_asset_class`，由 `toggle_symbol`（:921）在启用闸门调用
- [x] 搞清两个类别来源：DB 存 `cfg.asset_class='forex'`（存量行创建于旧推断逻辑时代，首段 `Derivatives` 未命中→默认 forex），券商路径推断 `_infer_asset_class` 返回 `index`
- [x] 确认非个例：`_infer_asset_class` 对 `Cash Indices` 词段用 `"ind" in ...` 命中 `index`；常见 CFDs 路径映射记录于 findings
- **Status:** complete

### Phase 2: Planning & Structure
- [x] 确定修复方案 A：toggle 启用时推断类别 ≠ DB 值时自动修正 DB（券商为事实源；库中旧值源于旧推断 bug）
- [x] 方案 B（仅 400 提示用户改）被否：用户体验差，且所有 create/PUT 都以券商推断值回填，toggle 应一致
- [x] 确认需要 audit 记录修正动作、publish reload，避免行为不一致
- **Status:** in_progress

### Phase 3: Implementation
- [x] 在 `toggle_symbol` 启用分支：推断 ≠ DB 值时写回 DB 新类别并记 audit
- [x] 保持 400 语义不变：仍走 `_cross_check_asset_class`，但修正后不再冲突
- **Status:** complete

### Phase 4: Testing & Verification
- [x] 新增集成测试：存量 `asset_class='forex'` 行 + 券商路径推断 `index` → toggle 成功且 DB 更新为 `index`
- [x] 回归：正常类别一致行 toggle 不变
- [x] 运行 `test_api_symbols.py` + `test_infer_asset_class.py` 全绿（46 passed）
- **Status:** complete

### Phase 5: Delivery
- [x] 代码提交（1570deb）+ 交付说明
- **Status:** complete

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 方案 A：toggle 启用时自动修正 DB asset_class | 券商为事实源；create/PUT 均以券商推断值回填；库中旧的 `forex` 源于旧推断 bug，修正方向安全；用户无需要手动改 |
| 保留 400 防护语义 | 修正只针对「库值 vs 推断值」冲突，不削弱「券商品种必须可交易」的启用闸门 |

## Errors Encountered
| Error | Resolution |
|-------|------------|