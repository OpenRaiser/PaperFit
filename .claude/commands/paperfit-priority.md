# /paperfit-priority — 调整当前修复优先级

**作用**: 读取当前状态、视觉重点和修复计划，给出本轮应该先处理什么；若用户明确指定优先顺序，可将该顺序写入 `next_actions` 供后续 `/fix-layout` 直接遵循。

**用户入口说明**:
- 这是优先级解释与调整入口，不要求用户理解 repair plan 的底层结构。
- 若用户以自然语言说明“先修第 5 页空洞，再修表格”，也应路由到同类任务。
- 不要把它变成手动操作内部状态文件的流程说明。

**防呆约束**:
- 不要调用 `TaskCreate`、`TodoWrite`、`Plan` 之类的任务工具
- 不要在这个命令里直接修改论文源码
- 浮动体位置与尺寸问题未收敛前，不要把文本增删改列为第一优先级

## 工具调用约定

在论文项目根目录内，优先读取：

- `data/state.json`
- `data/visual_signal_report.json`
- `data/repair_plan.json`

若用户要求把人工优先级写回状态，可执行：

```bash
paperfit runtime --state data/state.json set-next-actions "<action1>" "<action2>" "<action3>"
```

## 用法

```bash
/paperfit-priority
```

也可以由自然语言触发，例如：

```text
先修第 5 页的视觉空洞，再处理表格
当前这篇论文应该先修什么
```

## 执行流程

1. 读取 `data/state.json`，提取：
   - `visual_signals_summary.priority_pages`
   - `visual_signals_summary.priority_objects`
   - `repair_plan_summary.top_candidates`
   - `next_actions`
2. 总结当前机器优先级。
3. 若用户给出人工优先级，则按以下真实执行顺序归并：
   - 先处理图表位置、跨栏选择、尺寸不匹配
   - 再处理 overfull / underfull / 对齐类问题
   - 最后才考虑 `semantic_budgeter` 或其它文本增删改
4. 若用户要求持久化该优先级，用 `paperfit runtime --state data/state.json set-next-actions ...` 覆盖 `next_actions`。
5. 明确提示下一步应继续执行 `/fix-layout`，并带着新的优先级约束推进。

## 输出结果

- 当前 priority pages / priority objects
- 当前 top repair-plan candidates
- 人工优先级是否已写回 `next_actions`
- 下一轮建议动作

## 调度

- 不直接调用 Agent
- 通过 `state.json`、`visual_signal_report.json`、`repair_plan.json` 做优先级解释与写回
