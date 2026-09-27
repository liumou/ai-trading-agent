# get_symbol_spec 契约文档 (Phase 3, Problem 8)

> 从 `2026-09-27-code-review-b8f1bbd-fixes`（commit b8f1bbd 审查修复）拆分的独立任务。原相位 Phase 3 问题 8。

## Goal

记录 `connector.get_symbol_spec` 的 API 契约破坏性变更（commit b8f1bbd 引入），供外部调用方/文档读者知晓。

## Background（来自评审 findings.md）

`app/mt5/connector.py` `get_symbol_spec` 从 `to_broker_alias(symbol)` 改为直接用 `symbol`（行 112-125 附近）。docstring 已说明"必须是券商侧名称"。调用方（symbols.py:1001 `alias`、symbol_validation.py:63 `broker_symbol`）均传券商名，**内部安全**。

但这是 API 契约破坏性变更：任何外部调用方若仍传 canonical 名（如 "GOLD"）现在会打到 `/symbol-spec/GOLD` 而非 `/symbol-spec/GOLD_`，可能 404。未在文档记录。

**关键事实（评审确认）**：仓库**无 CHANGELOG**（`ls docs/` 无，根目录与 backend/ 均无）。

## Phases

### Phase 1: 确认落点 — `Status: complete`
- docs/ 现有：`SYMBOL-PARAMETERS-TECH.md`（42 项审计 + 修复批次）、`SYMBOL-PARAMETERS.md` 等
- 无 CHANGELOG（确认）
- **落点选定**：`docs/SYMBOL-PARAMETERS-TECH.md` §9（该文档有明确"审计基线 commit"惯例，适合追加契约变更）

### Phase 2: 写入契约说明 — `Status: complete`
- ✅ 已写入 `docs/SYMBOL-PARAMETERS-TECH.md` §9.1（2026-09-27）：
  - 旧行为（canonical→alias 自动映射）vs 新行为（直传券商名，不映射）
  - 变更 commit：`fe35cfa`（原 b8f1bbd）
  - 内部调用方不受影响（均传券商名）；外部调用方若传 canonical 名会 404
  - 变更理由（防止旧别名覆盖新别名）
  - **对称差异**：`get_ohlcv`/`get_tick` 仍保留 to_broker_alias 映射
  - 相关测试：test_market_data_alias.py

### Phase 3: 复核 — `Status: complete`
- ✅ 读 connector.py:114-123 对照，文档描述与代码一致（直传 `symbol`，不映射）

## Errors Encountered

（无）

## Next Step

Task 全部完成（问题 8 closed）。无待办。