# /repair-table — 修复表格

**作用**: 针对指定表格或当前论文中的表格问题执行修复闭环。它是专家快捷入口；普通自然语言如“修 Table 2 太挤的问题”也应能触发同类任务。

**用户入口说明**:
- 用户只需要描述哪张表有问题，或直接说明“修表格”。
- 不要要求用户手动执行 `paperfit render`、`compile.sh` 或其它内部命令。
- 表格修复必须以视觉可读性和结构完整性为主，而不是靠暴力缩放过关。

## 工具调用约定

验证阶段在论文根目录使用 **`paperfit run scripts/compile.sh`** / **`paperfit render`**，勿假设项目内存在包级 `scripts/`。

表格修复属于 source-changing 任务。默认只生成 repair plan、风险和 approval 状态；只有用户显式授权 `--apply` 或等价许可时才允许写回表格源码。多轮表格写回必须同时显式授权 `--apply --max-rounds N`，并由 runtime 的 approval carry-forward、artifact freshness、candidate approval scope gate、per-round lineage 和 gatekeeper `CONTINUE` 控制。

## 用法

```
/repair-table
```

也可以由自然语言触发，例如：

```text
用 PaperFit 修复这篇论文的表格问题
修一下 Table 2，当前太挤而且列宽不平衡
```

## 执行流程

1. 定位指定表格或自动识别主要表格问题
2. 分析表格溢出、一致性、列宽和可读性问题
3. 重构列格式、调整列间距、必要时改用更合适的表格布局策略
4. 重新编译并回到视觉验证
5. 未授权 `--apply` 时只交付候选修复、风险说明和 approval 状态

## 调度

- 代码外科医生：`agents/code-surgeon-agent.md`
- 技能：`skills/overflow-repair/`、`skills/consistency-polisher/`
