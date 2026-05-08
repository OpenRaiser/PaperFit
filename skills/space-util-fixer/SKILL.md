---
name: space-util-fixer
description: "PaperFit space util fixer expertise for LaTeX paper layout."
---

# Space Utilization Fixer Skill

## 概述

本技能专门处理 **Category A：空间利用缺陷**，包括：

- **A1**：孤行/寡行（Widow/Orphan Lines）
- **A2**：末页大面积留白（Excessive Trailing Whitespace）
- **A3**：页数预算违反（Page Budget Violation）
- **A4**：双栏末页左右栏高度不齐（Unbalanced Column Heights）
- **A5**：双栏页内列竖向空洞（栏内中段大块无图无表空白，常与 `figure*`/`[H]`/不当断页有关）

该技能由 `code-surgeon-agent` 或 `semantic-polish-agent` 调用，通过对段落级排版控制、浮动体调度和最小语义改写的组合运用，优化页面空间分配，使文档达到专业级的视觉均衡。

空间利用问题的修复常常需要多轮迭代，因为局部调整会影响全局分页。因此，每次修改后必须重新编译并审查页图。

宿主先核对检测建议：**请你看看这是否是对的**，根据当前页图和源码采纳、调整或拒绝。收到用户排版修复授权后，宿主可用原生工具编辑；无需调用可选批处理来获得相同授权。可选自动批处理默认只生成计划，写回需要显式 `--apply`，多轮批处理另需 `--max-rounds N` 及其 runtime 就绪、范围和当前证据检查。

---

## 适用场景

| 缺陷 ID | 描述 | 优先级 | 是否允许语义修改 |
|---------|------|--------|-----------------|
| A1 | 孤行/寡行 | High | 是（最后手段） |
| A2 | 末页大面积留白 | High | 是（最后手段） |
| A3 | 页数预算违反 | Critical | 是（最后手段） |
| A4 | 双栏末页左右栏高度不齐 | Medium | 否 |
| A5 | 双栏页内列竖向空洞（非末页、栏内大块无正文空白） | High | 视情况 |

---

## 输入规范

| 输入项 | 来源 | 说明 |
|--------|------|------|
| 主 `.tex` 文件路径 | 项目上下文 | 需修改的源文件 |
| 排版侦探报告 | `layout-detective-agent` 输出 | 包含 A 类缺陷的页码和描述 |
| 目标页数（若适用） | 用户约束 | A3 修复必须提供 |
| 模板类型 | `templates.yaml` 或上下文 | 单栏/双栏 |

---

## 输出规范

```json
{
  "skill": "space-util-fixer",
  "status": "success | partial | failed",
  "modified_files": ["main.tex"],
  "changes": [
    {
      "defect_id": "A1",
      "object": "第4页顶部孤行",
      "action": "在段落前添加 \\looseness=-1",
      "before_snippet": "The proposed method achieves...",
      "after_snippet": "{\\looseness=-1 The proposed method achieves...\\par}"
    }
  ],
  "unresolved": []
}
```

---

## 修复策略

### 通用原则

1. **先排版，后语义**：优先使用 LaTeX 原生的排版控制命令，仅在无效时才考虑语义级改写。
2. **局部修改，检查流动影响**：完整编译后先比较受影响页及邻页；大范围重排扩大检查，最终交付查看全部页。
3. **避免硬性分页**：禁止滥用 `\newpage` 或 `\clearpage` 来“凑页数”，这会破坏文档结构的自然性。

---

### A1：孤行/寡行修复

**问题特征**：
- 段落最后一行单独出现在下一页顶部（孤行）。
- 段落第一行单独留在上一页底部（寡行）。
- 段落末尾仅含 1-3 个单词的短行（段尾小尾巴）。

**候选策略**：按实际段落、页图和模板选择，可组合有共同原因的修改，不要求依次试完。

#### 策略 1：段落级收紧 (`\looseness=-1`)

通过在段落前设置 `\looseness=-1`，指示 TeX 引擎尝试将段落收缩一行，从而消除孤行或小尾巴。

```latex
% 修改前
The proposed method achieves state-of-the-art performance on several
benchmarks, demonstrating the effectiveness of our approach.

% 修改后
{\looseness=-1 The proposed method achieves state-of-the-art performance on several
benchmarks, demonstrating the effectiveness of our approach.\par}
```

*注意*：
- 必须将整个段落用花括号 `{ }` 括起，并在组内结束段落（`\par`），使 TeX 断行时仍使用局部设置。若先关闭组、再以空行结束段落，设置已经恢复，可能完全无效。
- 此设置只请求改变一行，TeX 未必能满足；必须比较编译后的行数、词间距和相邻页，拒绝无收益或视觉退化的候选。

#### 策略 2：段落级扩张 (`\looseness=1`)

若段落太紧，可尝试扩张一行，使段末行变长。

```latex
{\looseness=1 This paragraph will be typeset with one more line...\par}
```

#### 策略 3：给断行算法增加后备伸展量 (`\emergencystretch`)

在局部段落中试验后备伸展量，比较实际断行和词间距；它不是增加段落间或行间的竖向空白，也不保证改变行数。

```latex
{\emergencystretch=1em This paragraph content...\par}
```

#### 策略 4：微调上下文句长分布（需 `semantic-polish-agent`）

若排版控制无效，则进行最小语义改写：
- 在段落中增删 3-8 个单词（如将 `in order to` 改为 `to`，或将 `demonstrate` 改为 `show`）。
- 调整从句结构，但不改变学术原意。

```latex
% 修改前
The proposed method achieves state-of-the-art performance on several
benchmarks, demonstrating the effectiveness of our approach.

% 修改后
The proposed method achieves state-of-the-art results on several
benchmarks, showing the effectiveness of our approach.
```

*语义改写必须获得针对正文编辑的单独授权，并由 `semantic-polish-agent` 执行，本技能仅提出请求。布局修复授权不包含正文改写；布局验收会检查实际正文 token，候选标签不能覆盖正文差异。*

#### 策略 5：全局调整 widow/orphan 惩罚

在导言区增加以下设置（通常模板已包含，可作为兜底）：

```latex
\widowpenalty=10000
\clubpenalty=10000
\displaywidowpenalty=10000
```

*注意*：此设置影响全局，需谨慎。若模板已设置，则无需重复。

---

### A2-A3: End-page spacing and explicit page objectives

Read docs/PAGE_FIT_TOOLBOX.md from the PaperFit package root. Plan the requested
page objective, readability, template and float flow together from the start.
Use the host's native tools to test a coherent layout change.

- Distinguish exact from at_most, and body/ref/appendix scope. Exact misses
  in either direction remain unfinished; unknown scope is not a measured pass.
- Run paperfit page-fit --main <root.tex> --target-pages N --page-policy exact
  --output data/page_fit_01.json. Read source anchors, measured PDF identity,
  coverage and pending review.
- Choose or combine float flow, local gaps, permitted template spacing, native
  reflow, readable table spacing and equivalent rearrangement from current
  evidence. These categories are not a mandatory sequence. Inspect confirmed
  template deviations and large artificial gaps before many tiny padding trials.
- Preserve body font, template, every scientific fact/number/formula, captions,
  labels, references and assets. Do not add filler, invent analysis, delete
  sentences, create blank pages or move content out of the requested scope.
- Under-target papers need overcompression/readability/float-flow repair.
  A sparse extra page may satisfy equality yet be visually poor; inspect it.
- Keep useful checkpoints and compile/render each real candidate. Inspect
  changed pages, objects at reading zoom and neighbors during trials; broaden
  review after major reflow and inspect every final page. Revert a regressive
  trial and choose another intervention.
- Record a new report with --before-pdf <checkpoint.pdf> --attempt-tier N.
  Reports, CV counts, VLM DONE and batch round limits never replace full-page
  host inspection or finish an unmet target.

---

### A4：双栏末页左右栏高度不齐

**问题特征**：
- 双栏模板最后一页，左栏和右栏底部高度差超过 2 行。

**修复策略**：

#### 策略 1：使用 `\balance` 或 `flushend` 宏包

在导言区加载 `flushend` 宏包，自动平衡末页两栏高度。

```latex
\usepackage{flushend}
```

*注意*：`flushend` 与某些宏包（如 `multicol`）可能冲突，需在特定模板下测试。

#### 策略 2：手动平衡

若自动平衡无效，在最后一节结束前插入 `\balance` 命令（需 `balance` 宏包）。

```latex
\usepackage{balance}
...
\section{Conclusion}
...
\balance
\end{document}
```

#### 策略 3：微调最后一段的断行

对末页最后一段使用 `\looseness=-1` 或 `\looseness=1`，改变其行数，从而影响两栏高度。

#### 策略 4：调整浮动体位置

若末页有浮动体，尝试将其移至前一页或强制放在当前页底部，以改变栏内内容分布。

---

### A5：双栏页内列竖向空洞

**问题特征**：某一页左栏或右栏在标题/段落后出现大块竖向空白（无图无表），另一栏同期仍有连续正文；常见于 `figure*` 顶到下一页后在栏内留下“断层”、滥用 `\\` / `\clearpage`、`\FloatBarrier`、`[H]` 等。

**修复策略**（优先浮动体与断页，再动正文）：

1. 检查本页及前后页的 `figure*` / `table*`：尝试将浮动环境在源码中前移/后移，或改为单栏 `figure`/`table`（若尺寸允许）。
2. 搜索并移除不当的 `\\`（段内强制换行）、`\newpage`、`\vfill`；慎用 `[H]`。
3. 若使用 `\FloatBarrier`，评估是否可删除或挪到节末。
4. 与 **float-optimizer** 协同：处理 `Float too large` 日志后再看栏内空洞是否消失。
5. 比较空洞下方段落的原生断行，例如局部 `\looseness`。正文改写需要已有的单独授权和语义核验，不得为填空白添加句子。

---

## 修复验证

每完成一项修复后：

1. **重新编译并检查日志**；按工具和依赖需要重跑，使交叉引用稳定，不以固定次数替代检查。
2. **渲染页图**，对照缺陷逐项检查：
   - A1：孤行/寡行是否消失？
   - A2：末页是否有可避免的大块空白？结合自然结尾、模板和页数目标判断，不以固定20%阈值作为普遍验收规则。
   - A3：总页数是否达到目标？
   - A4：双栏末页高度差是否 ≤ 2 行？
   - A5：该页逐栏检查，栏内中段竖向空洞是否消失？
3. 若未通过，回滚并尝试下一策略。

---

## 与其它技能的协作

- **浮动体修复 (float-optimizer)**：A2 和 A3 常需配合浮动体调整。
- **语义润色 (writing-polish)**：当排版手段用尽时，请求语义改写。
- **一致性检查 (consistency-polisher)**：修改字号、间距后需确保全局一致。

---

**Space Utilization Fixer Skill 就绪。** 等待调用，优化页面空间分配。
