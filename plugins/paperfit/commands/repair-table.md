# /repair-table

聚焦单个或少量表格的定向修复。

## Workflow

1. 识别目标表格及其所在页面。
2. 优先使用宽度感知方案，例如 `table*`、`tabularx`、列宽重构。
3. 修完后重新编译并检查页图与交叉引用。

## Guardrails

- 不要用 `\resizebox` 或 `\scalebox` 当主修复手段。
- 不要破坏 caption、label、引用关系或表意。
