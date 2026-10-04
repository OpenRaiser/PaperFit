### `commands/paperfit-priority.md`

# /paperfit-priority — 调整本轮修复优先级

## 命令描述

读取 `state.json`、视觉重点对象与修复计划摘要，解释当前应该先修什么；若用户给出人工优先级，可将其写回 `next_actions`，供后续 `/fix-layout` 使用。

## 触发词

/paperfit-priority

## 行为

1. 读取 `visual_signals_summary.priority_pages`、`priority_objects`。
2. 读取 `repair_plan_summary.top_candidates`。
3. 优先确保图表位置与尺寸问题先于文本增删改处理。
4. 如用户要求持久化优先级，由 Agent 自动写回状态，供后续 `/fix-layout` 或 `/paperfit` 闭环继续使用。

## 调度映射

- 不直接调用 Agent
- 通过状态与 repair plan 做优先级解释和写回
