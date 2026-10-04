# /migrate-template — 用自然语言发起模板迁移

## 这是什么

`/migrate-template` 表示“把当前论文迁移到另一个投稿模板，并尽量保持版面稳定”。用户表达的是迁移目标，而不是一串脚本流程。PaperFit 负责自动完成模板识别、兼容性修改、图表重排、页数再平衡和视觉验收。

## 用户怎么说

以下表达都应进入模板迁移任务：

- `/migrate-template CVPR2026`
- `用 PaperFit 把这篇论文迁移到 AAAI 模板`
- `迁移到双栏模板，尽量不要破坏当前图表布局`
- `把这篇 paper 从 arXiv 版整理成会议投稿版`

## Agent 自动完成什么

1. 自动识别当前模板、目标模板和关键版式差异。
2. 自动检查迁移会影响的对象：
   - 文档类与模板宏
   - 单双栏切换
   - 浮动体尺寸与位置
   - 表格宽度与分页策略
   - 页数预算变化
3. 默认生成源码层迁移计划、风险和 approval 状态；当前 typed runtime 尚未提供 runtime-owned template migration executor，因此显式 `--apply` 会被阻断并报告需要补齐迁移 executor。
4. 自动进入视觉修复闭环，处理迁移带来的 E 类、B 类、A 类和 D 类问题。
5. 在视觉通过前，持续报告迁移后的主要缺陷与剩余风险。
6. 未来启用多轮迁移写回时，必须显式 `--apply --max-rounds N`，并由 approval carry-forward、artifact freshness、candidate approval scope gate、round lineage 和 gatekeeper `CONTINUE` 控制。

## 用户会得到什么

- 迁移是否成功
- 当前还剩哪些视觉问题
- 是否已经达到目标模板下的可交付状态
- 哪些问题已自动解决，哪些仍建议人工复核

## 迁移边界

- 模板迁移不是“只替换 `\documentclass`”
- 迁移完成前，不得只凭编译成功就宣称完成
- 长度调整、浮动体修复、表格重构都必须回到视觉复验
- 当前 `--apply` 不得回退到旧的直接迁移脚本；必须等待 runtime-owned migration executor
- 语义修改只能在排版手段用尽后受控触发

## 默认推断

- 未显式提供当前模板时，优先自动识别
- 未显式提供目标页数时，优先继承目标模板的常见预算或当前论文的显式约束
- 用户只说“迁到会议模板”时，若目标模板不明确，才发起最小澄清

## 调度映射

- 任务类型：`template_migration`
- 主调度：`orchestrator-agent`
- 验收标准：目标模板可编译 + 视觉闭环通过 + 关键对象完整
