# Progress: GER40Cash 激活 asset_class 交叉校验失败修复

## 2026-09-23 — 根因定位完成

- 定位报错来源：`symbols.py:485 _cross_check_asset_class`，toggle 启用闸门 `:921` 调用。
- 根因：存量行 DB `asset_class='forex'` 遇升级后的全路径推断 `index` → 冲突 400。
- 选定方案 A（toggle 启用时自动修正 DB 类别）。待实施。

## 2026-09-23 — 实施 + 测试完成

- 在 `toggle_symbol` 启用分支（`symbols.py:921-939`）加自动修正：路径存在时推断类别 ≠ 库值 → 写回新值 + `symbol_asset_class_fixed` audit + info 日志。
- 修正后仍走 `_cross_check_asset_class`（400 语义不变）；旧 bridge 无 path → `inferred=None` 跳过修正，行为同旧。
- 新增 2 个集成测试：
  - `test_toggle_on_auto_fixes_stale_asset_class`：GER40Cash 存量行 forex → 启用成功、DB 变 index、二次 toggle 正常。
  - `test_toggle_on_keeps_matching_asset_class`：US30 已为 index → 启用不被改动。
- 测试结果：`test_api_symbols.py` + `test_market_sessions.py` + `test_infer_asset_class.py` 共 69 passed。
- ruff 未安装在当前环境，跳过；改动风格与现有代码一致。