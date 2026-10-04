# /show-status — 查看当前任务状态与证据链

## 命令描述

显示当前 PaperFit 任务的运行状态与证据链。状态应来自 runtime status contract，而不是各宿主自行解释 legacy `state.json` 字段。适用于长时间运行任务的中途检查或恢复任务前确认上下文。

## 触发词

/show-status

## 行为

1. **读取 runtime status**：
   - 优先选择最近的适用 `RunResult`，如 `data/run_result_agent.json`、`data/run_result_template_migration.json`、`data/run_result_full_vto_nondry.json`、`data/run_result_full_vto_dry_run.json` 或 `data/run_result_check_visual.json`。
   - 同时读取 `data/state.json` 作为 mutable projection。
   - 与 `paperfit status-view` / `paperfit status` 使用同一状态合同。
2. **格式化输出**：
   - 项目主文件
   - 任务类型、运行状态、最近门禁决策
   - selected `RunResult` 路径与 runtime event 摘要
   - artifact freshness 与 terminal success guard
   - 缺陷摘要
   - repair plan / repair execution / approval 摘要
   - source-changing run 的 `repair_loop_policy`：stop condition、approval carry-forward、candidate approval gate、round artifact lineage、second-round readiness
   - 相关报告路径与下一步行动
3. **若任务已完成或被阻塞**：显示最终门禁状态、freshness/terminal guard 证据、approval/gate 阻塞原因和相关报告路径。

## 示例

```
/show-status
```

## 调度映射

- 不调用修复 Agent，不触发 compile/render/repair。
- 读取 runtime status contract；CLI 等价入口是 `paperfit status`，机器可读入口是 `paperfit status-view`。
- 需要可视化实时监控时，使用 `paperfit monitor` 启动只读的 PaperFit Observatory；它读取同一套 runtime artifacts，并以中文界面展示状态、视觉证据、修复决策与安全边界。
