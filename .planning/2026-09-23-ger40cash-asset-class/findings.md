# Findings: GER40Cash 激活 asset_class 交叉校验失败

## 报错

```
asset_class='forex' does not match broker path 'Derivatives\Cash\Cash Indices\GER40Cash' (inferred 'index'). Use 'index' or pick a different symbol.
```

## 来源链

- `backend/app/api/routes/symbols.py:485` `_cross_check_asset_class(spec, declared)` — 推断类别 ≠ 声明类别 → 400
- 调用方（启用闸门）：`toggle_symbol` `:921` — `_cross_check_asset_class(spec, cfg.asset_class)`
- 推断：`_infer_asset_class(path)` `:342`
- 用户路径：前端 `/symbols` 页激活开关 → `POST /api/symbols/{symbol}/toggle` → 启用闸门

## 根因

1. GER40Cash 是**存量行**，创建时数据库 `asset_class` 存为 `forex`。
2. 创建时期的 `_infer_asset_class` 只查路径**首段**；`Derivatives` 未命中任何 needle → 走默认 `return "forex"`。
3. commit `4795aa8` 升级为**全路径整词匹配**：`"ind" in "indices"` 命中 → GER40Cash 正确推断为 `index`。
4. 升级后激活时重启校验，库值 `forex` ≠ 推断 `index` → 400。

## `_PATH_TO_CLASS` 映射（:329）

```
("cryptocurrenc","crypto"), ("crypto","crypto"), ("metal","metal"),
("energ","energy"), ("ind","index"), ("share","stock"),
("stock","stock"), ("equit","stock"), ("forex","forex")
```

`_infer_asset_class` 两段式匹配：
- 首段子串匹配（旧行为）
- 全路径归一化（小写、去 `_`/空格）后整词子串匹配

## 相关 CFDs 路径 → 类别（XM 常见）

| 券商路径 | 推断 |
|----------|------|
| `Forex\Majors\EURUSD` | forex |
| `Derivatives\SpotMetals_\GOLD_` | metal |
| `Energies\OILCash` | energy |
| `Derivatives\Cash\Cash Indices\GER40Cash` | **index** |
| `Crypto\BTCUSD` | crypto |
| `Stocks\AAPL` | stock |

## 方案对比

- **A（选）**：toggle 启用时推断≠库值 → 自动把库值改为推断值（券商为事实源）。
- **B（否）**：仅 400 + 提示用户手动改。体验差，且 create/PUT 都以券商推断回填，toggle 应同源。

## 注意事项

- create（:660-661）与 update（:807-808）、toggle（:921）三处 `_cross_check_asset_class` 语义一致（声明类别 × 券商路径）。
- 自动修正只改 `cfg.asset_class`，不触碰 volume/pip 等其他回填字段（那些已各自处理）。
- 需在修正时写 audit 日志 + publish reload，保持可观测与引擎一致性。
- `SymbolConfig.asset_class` 列名 `String(16)` default `forex`（models.py:450）。
- 前端 `AssetClass` 值域含 `index`（lib/api.ts:328-343），修正后前端展示不会破坏。