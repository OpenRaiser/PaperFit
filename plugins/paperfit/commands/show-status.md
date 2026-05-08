# /show-status

查看当前 PaperFit 任务状态。

## Workflow

1. 读取 `data/state.json`。
2. 总结当前轮次、编译结果、页图状态、剩余缺陷、优先页和下一步动作。
3. 优先输出结论和风险，不要直接倾倒原始 JSON。
