# /adjust-length — 调整页数

**作用**: 尝试通过排版微调或受控语义改写逼近目标页数。它是专家快捷入口；普通自然语言如“把正文压到 8 页，尽量不要改语义”也应能触发同类任务。

**用户入口说明**:
- 用户表达目标页数与约束即可，不需要手动执行内部命令。
- 长度调整仍然属于视觉闭环任务，不能只看字数或页数数字。
- 语义改写只能在排版手段用尽后受控触发。

## 工具调用约定

编译、渲染、日志解析等统一见 `CLAUDE.md`「系统架构与运行时边界」：**`paperfit render`**、**`paperfit run scripts/…`**。

长度调整属于 source-changing 任务。默认只生成诊断、候选计划和 approval 状态；只有用户显式授权 `--apply` 或等价自然语言许可时才允许写回 `.tex`。多轮源码写回必须同时显式授权 `--apply --max-rounds N`，并且第二轮及以后由 runtime 的 `repair_loop_policy.second_round_apply_readiness`、approval carry-forward、artifact freshness、candidate approval scope gate 和 gatekeeper `CONTINUE` 共同放行。

## 用法

```
/adjust-length
```

也可以由自然语言触发，例如：

```text
用 PaperFit 把正文压到 9 页
把这篇论文扩到满 8 页，但不要破坏结论与结果表达
```

## 执行流程

1. 分析当前页数与目标页数的偏差
2. 若超页：优先压缩浮动体、优化版面结构、精炼表述
3. 若不满页：优先做版面与结构补强，必要时受控扩写结论/讨论
4. 每轮都回到视觉验收，确认页数变化没有破坏整体版面
5. 语义干预仅在排版手段用尽后使用
6. 若未授权 `--apply`，输出 dry-run 修复计划、风险和 approval 状态，不执行源码写回

## 调度

- 语义润色：`agents/semantic-polish-agent.md`
- 技能：`skills/space-util-fixer/`、`skills/writing-polish/`
