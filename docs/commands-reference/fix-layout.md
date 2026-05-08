### `commands/fix-layout.md`

# /fix-layout — 启动完整 VTO 排版优化

## 命令描述

`/fix-layout` 是 PaperFit 的专家快捷入口，用于对当前 LaTeX 项目执行完整的视觉排版优化（VTO）闭环。用户只需要表达目标，例如“修掉这篇论文的排版问题”“尽量不改语义地压到 8 页”，Agent 会自动完成主文件识别、编译、页图渲染、视觉检测、修复计划生成、门禁验收与结果交付。源码写回默认是 dry-run，只有显式 `--apply` 或宿主等价授权存在时才执行。

这条命令不是让用户手动编排内部 CLI；它只是一个更明确的意图入口。与之对应的普通自然语言表达，也应能触发相同的任务类型。

## 触发词

/fix-layout [选项]

也可由自然语言触发，例如：

- `用 PaperFit 修复这个项目的排版问题`
- `把这篇论文排到投稿状态，优先修视觉问题`
- `把正文压到 9 页，尽量不要改正文含义`

## 参数

| 参数 | 简写 | 类型 | 默认值 | 说明 |
|------|------|------|--------|------|
| `--target-pages` | `-p` | int | 无 | 目标页数（如 9），用于 A3 缺陷检测与修复 |
| `--template` | `-t` | string | 自动检测 | 指定模板类型（如 `ICLR2025`、`ECCV2024`） |
| `--strict` | `-s` | bool | false | 严格模式：Minor 缺陷也阻塞 DONE |
| `--max-rounds` | `-r` | int | 1 | 最大源码写回轮次；多轮必须显式与 `--apply` 一起授权 |
| `--only` | `-o` | string | 无 | 仅处理指定类别（如 `-o B,D` 只修浮动体和溢出） |

## 行为

1. **自动识别任务上下文**：识别主 `.tex`、模板类型、单双栏、页数预算、是否已有 `data/state.json` 可恢复。
2. **自动进入视觉闭环**：
   - 编译当前论文并收集日志、crossrefs 与状态信息
   - 渲染 PDF 页图，执行视觉检测，并在需要时融合规则检测结果
   - 根据缺陷类别自动调用对应修复 Agent 和 Skill
   - 默认生成 dry-run repair plan；若显式 `--apply --max-rounds N`，每轮只执行 bounded candidate batch，并在 readiness、approval carry-forward、artifact freshness、candidate gate 和 gatekeeper `CONTINUE` 全部通过后才继续下一轮
3. **自动门禁与交付**：
   - 只有在视觉结果与结构约束都通过时才报告完成
   - 交付 dry-run 候选计划或已授权写回后的 `.tex`、重新编译的 `.pdf`、诊断报告及状态摘要

## Agent 内部执行说明

- `paperfit runtime`、`paperfit run`、`paperfit render` 等能力是 Agent 的内部执行层。
- 正常使用时，用户不需要手动执行这些命令。
- 只有在开发调试、宿主接入排障或 Agent 无法自动恢复时，才需要显式查看底层执行细节。

## 示例

```bash
# 标准 VTO 优化（无页数要求）
/fix-layout

# 严格控制在 9 页，严格模式
/fix-layout --target-pages 9 --strict

# 仅修复浮动体和溢出问题；未加 --apply 时仍是 dry-run
/fix-layout --only B,D --max-rounds 5

# 显式授权最多 5 轮 bounded source mutation
/fix-layout --only B,D --apply --max-rounds 5
```

```text
用 PaperFit 修复当前论文的排版问题
用 PaperFit 把正文压到 9 页，尽量不要改语义
用 PaperFit 只处理浮动体和溢出问题
```

## 调度映射

- 调用 `agents/orchestrator-agent.md`
- 任务类型：`full_vto`
