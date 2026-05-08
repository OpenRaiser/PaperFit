# /paperfit — 统一自然语言入口

## 这是什么

`/paperfit` 是 PaperFit 的统一任务入口。它的意义不是“再发明一个命令前缀”，而是给所有宿主一个共同产品心智：

- 用户描述目标
- Agent 自动路由任务
- Agent 自动执行内部闭环
- 最终以视觉结果、修复策略与风险说明交付

无论在 `Claude Code`、`Codex` 还是 `Cursor`，PaperFit 都应尽量保持这套语义一致。

## 用户怎么说

以下都是合法入口：

- `/paperfit 分析这篇论文的排版问题`
- `/paperfit 修复当前项目的图表和溢出`
- `/paperfit 把正文压到 9 页，尽量不改语义`
- `/paperfit 把这篇论文迁移到 CVPR 模板`
- `用 PaperFit 看看这篇论文哪里排版不对`

## 系统会自动路由到什么任务

PaperFit 会把自然语言请求自动映射到这些主任务之一：

- `analyze_layout`：排版分析
- `visual_only`：只做视觉诊断
- `full_vto`：完整排版修复闭环
- `repair_table`：单表或局部对象修复
- `adjust_length`：长度与页数控制
- `template_migration`：模板迁移
- `status_query`：查看当前状态与最近结果
- `undo_last_change`：回滚最近一次自动写回

## 默认自动推断策略

在参数不全时，系统优先自动推断，而不是立刻要求用户补齐命令参数：

- 主文件：优先识别包含 `\documentclass` 的主 `.tex`
- 模板：优先从文档类、模板宏与项目结构推断
- 页数预算：优先从当前 PDF、模板常见限制或上下文要求推断
- 对象定位：优先从 `\label{}`、caption、crossrefs 与页图证据联合推断

只有在推断风险过高时，才向用户提出最小澄清问题。

## 交互与执行原则

- 长流程中必须主动汇报阶段进度，不能静默跑完整个闭环
- 遇到可恢复异常时，应优先自恢复
- 只有环境损坏、权限不足或目标本身不明确时，才向用户提出具体动作
- 对用户的说明必须围绕结果、视觉问题、修复策略和风险，而不是脚本命令表

## Source-Changing 安全边界

- 分析、编译、页图渲染、视觉诊断和 repair plan 生成可以自动执行。
- 修改 `.tex` 源码必须有显式 `--apply` 或宿主等价授权；没有授权时，只报告候选修复、risk、approval 状态和下一步建议。
- 默认 `max_rounds=1`。多轮源码写回必须同时显式授权 `--apply --max-rounds N`。
- 第二轮及以后必须由 runtime 的 `repair_loop_policy.second_round_apply_readiness`、approval carry-forward、artifact freshness、candidate approval scope gate 和 gatekeeper `CONTINUE` 共同放行。

## 验收原则

- 编译成功不是最终完成标准
- 所有主任务都必须回到视觉复验
- 图表、引用、对象完整性不能被破坏
- 所有自动写回都必须保留可回滚路径

## 宿主说明

- 在支持斜杠命令的宿主里，`/paperfit` 是统一快捷入口
- 在不强调斜杠命令的宿主里，普通自然语言也应触发同样的任务路由
- 宿主适配层只负责接入，不改变 PaperFit 的核心产品逻辑
