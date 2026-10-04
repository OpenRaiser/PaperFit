# 官方评估器（evaluator/）接入说明

日期：2026-10-05
来源：作者提供的 `PaperFit-evaluator-v3` 独立快照（原项目 `baselines_repro/lib`，
打包于 2026-09-29）。这是论文 arXiv:2605.10341 评测体系的官方实现。

## 它是什么

- **VLM 视觉评测**（`evaluate_vlm.py` + `lib/vlm_protocol.py`）：
  成对比较（扰动基线 B0 在左、候选在右），raw 分 =
  35% 绝对修复质量 + 40% 成对修复 + 25% 成品美学；约束门控产出
  `gated_v3_score`（渲染失败/编译脏/页预算/引用未解/新增缺陷会封顶）。
  prompts 与响应归一化都在 `lib/vlm_protocol.py`（V3_SYSTEM_PROMPT 等）。
- **程序化指标**（`lib/evaluators.py:basic_evaluation` + `aggregate.py`）：
  页预算、日志错误、交叉引用、源内容信号、语义锚点、变更行数。
- 自带离线测试（`tests/`）与诚实的已知缺陷说明（显著性助手的
  p 值分支问题），见其 README。

## 怎么跑（离线验证，无需 API）

```sh
cd evaluator
python -B -m unittest discover -s tests -v   # 协议归一化测试
python -B evaluate_vlm.py --help
python -B aggregate.py --help
```

## 怎么跑（真实 VLM 评测）

1. `cp configs/vlm_evaluation.env.example configs/vlm_evaluation.env`，
   填入自己的 OpenAI 兼容视觉端点/密钥/模型名（**密钥绝不入库**）。
2. 准备输入布局（评估器只消费已渲染图，不编译 LaTeX）：

   ```text
   <method-root>/<paper-id>/<case-id>/
     reports/evaluation_report.json
     pages/page_001.png ...
   ```

   case 元数据取自 `<case-root>/<paper-id>/<case-id>/benchmark_case.json`。
3. 运行（示例见其 README）：`--method-root B0=... --method-root OURS=...
   --evaluate-methods OURS --case-root ... --sample-size 200`。
   默认纳入 B0 无法渲染的 case，使用 render-rescue 评测；共同 case
   不足 200 时明确报错。仅在需要可渲染 B0 子集时使用
   `--require-b0-pages`。20 例后端对比需显式传入 `--sample-size 20`
   或固定 case-list。

## 与本仓库的衔接现状与缺口

- `paperfit-data`（200 case × 扰动）即评测语料；扰动基线就是 B0。
- **已接**：`scripts/export_evaluator_inputs.py` 把一次 PaperFit 运行的产物
  导出成评估器要的 method-root 布局，程序报告直接调官方
  `basic_evaluation`；case 资格沿用 runtime 的 case_status 词表
  （导出时缺页默认 `input_incomplete`）。
- **已接**：成对评测新增 `--swap-pair-order`（候选居左、B0 居右，角色
  仍由标签定义，判定语义不变）；`lib/pair_symmetry.py:reconcile_pair_runs`
  对账两个顺序的运行——一致则采信，冲突记 uncertain（位置偏差防控）。
- **缺口 1**：B0 侧（扰动 case）目前只有元数据与原始压缩包，
  批量生成扰动源 + 渲染基线页图的生成管线不在本仓库。
- **缺口 2**：`gated_v3_score` / win rate 接入 gatekeeper 与 RoundReport
  属于后续设计工作（与 docs/DEFERRED_DESIGN_WORK.md 的排序一并决策）。

## 与论文数字的可比性（诚实边界）

在以下条件未逐一核对前，本仓库产出的分数应表述为
**"PaperFit evaluator reimplementation"**，而非 "reproduced paper score"：
相同 case 集与扰动定义、相同渲染协议（DPI/页面拼接）、相同 VLM 模型与
prompt 版本、相同量表与 gate 规则、相同 baseline/candidate 配对与缺失
样本处理。另外：官方快照**不含**论文 Program Score 的
`content_embedding_similarity` 维——导出报告已如实标注
`not_available_in_official_snapshot`，不用自选 embedding 模型冒充复现。

在上述适配完成前，评估器作为独立目录使用，不阻塞主链路。
