# /show-status — 查看任务状态

**作用**: 显示当前 PaperFit 任务的 runtime 状态、证据链、approval/gate 情况和下一步行动；这是当前默认的“摘要 / 解释”入口。

**用户入口说明**:
- `/show-status` 是查询入口，用户不需要理解 `state.json` 的字段结构。
- 输出应优先解释当前任务状态、视觉问题、剩余风险和下一步动作，而不是直接倾倒内部 JSON。
- 若用户用普通自然语言询问“当前 PaperFit 到哪一步了”“还有哪些问题没修完”，也应路由到同类状态查询。

## 工具调用约定

优先使用 runtime status contract：

```bash
paperfit status
paperfit status-view
```

若需读写状态文件以外的包内工具，在论文根目录使用 **`paperfit run scripts/state_manager.py …`**，勿将 `scripts/` 当作用户仓库内路径。

## 用法

```
/show-status
```

也可以由自然语言触发，例如：

```text
查看当前 PaperFit 状态
这篇论文现在还剩哪些排版问题
```

## 执行流程

1. 读取 `paperfit status-view` 或等价 runtime status。
2. 优先展示 selected `RunResult`、任务类型、运行状态、最近门禁决策和 runtime event 摘要。
3. 显示 artifact freshness、terminal success guard、content integrity。
4. 显示缺陷摘要、视觉重点页与重点对象。
5. 显示 repair plan / repair execution / approval 摘要。
6. 对 source-changing run，必须显示 `repair_loop_policy`：
   - approval carry-forward
   - candidate approval scope gate
   - round artifact lineage
   - second-round apply readiness
7. 若状态为 blocked，显示阻塞原因：dry-run approval required、scope gate blocked、freshness failed、round limit reached 或 gatekeeper blocked。
8. 显示相关报告路径与下一步行动。

## 输出内容

- 项目主文件
- 任务类型与约束
- 当前轮次 / 最大轮次
- 编译结果与页图渲染状态
- Artifact freshness 与 terminal success guard
- Approval 状态、candidate gate 状态、second-round readiness
- 缺陷摘要（已修复/剩余）
- 视觉重点页与重点对象
- 修复计划摘要与最近执行结果
- 最近一次门禁决策及下一步行动
- 诊断报告路径

## 调度

- 不调用修复 Agent，不触发 compile/render/repair。
- 读取 runtime status contract；CLI 等价入口是 `paperfit status`，机器可读入口是 `paperfit status-view`。
