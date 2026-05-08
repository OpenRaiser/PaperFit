---
name: overflow-repair
description: "PaperFit overflow repair expertise for LaTeX paper layout."
---

# Overflow Repair Skill

## 概述

本技能专门处理 **Category D：溢出与对齐缺陷**，包括：

- **D1**：Overfull hbox（段落文本、表格单元格、公式溢出栏宽）
- **D2**：长公式未合理断行
- **D3**：URL/长标识符溢出

该技能由 `code-surgeon-agent` 调用，执行对 `.tex` 源码的精确修改，以消除或缓解溢出问题。所有修复遵循 **最小修改原则**，不改变学术内容。

Source-changing 边界：在 PaperFit runtime 中，本技能默认只贡献 repair plan 和风险说明；只有显式 `--apply` 或宿主等价授权存在时才可写回源码。多轮写回必须同时显式启用 `--apply --max-rounds N`，并且每轮通过 approval carry-forward、artifact freshness、candidate approval scope gate 和 gatekeeper `CONTINUE` 后才能继续。

---

## 适用场景

当 `layout-detective-agent` 或 `rule-engine-agent` 报告中出现以下缺陷 ID 时，路由至本技能：

| 缺陷 ID | 描述 | 优先级 |
|---------|------|--------|
| D1 | Overfull hbox（段落/表格/公式） | High |
| D2 | 长公式未断行 | High |
| D3 | URL/长标识符溢出 | Medium |

---

## 输入规范

| 输入项 | 来源 | 说明 |
|--------|------|------|
| 主 `.tex` 文件路径 | 项目上下文 | 需修改的源文件 |
| 规则引擎报告 | `rule-engine-agent` 输出 | 包含溢出位置（行号）、溢出量、对象 |
| 排版侦探报告 | `layout-detective-agent` 输出 | 包含视觉确认的溢出页面和对象 |
| 当前编译日志 | `.log` 文件 | 用于验证修复后溢出是否消除 |

---

## 输出规范

修改完成后，必须返回以下信息：

```json
{
  "skill": "overflow-repair",
  "status": "success | partial | failed",
  "modified_files": ["main.tex", "tables/table1.tex"],
  "changes": [
    {
      "defect_id": "D1",
      "object": "Table 2",
      "action": "替换 tabular 为 tabularx，设置列宽比例",
      "before": "\\begin{tabular}{|l|c|c|}",
      "after": "\\begin{tabularx}{\\linewidth}{|l|X|X|}"
    }
  ],
  "unresolved": []
}
```

---

## 修复策略

### 策略选择流程

1. 根据日志和视觉报告定位溢出位置与类型（段落/表格/公式/URL）。
2. 按照下文各类型的修复策略依次尝试，从最不侵入的方案开始。
3. 每次修改后，通知 `orchestrator-agent` 触发重新编译以验证效果。
4. 若当前策略无效，回退并尝试下一策略。

---

### D1：段落文本溢出

**问题特征**：
- 日志：`Overfull \hbox (Xpt too wide) in paragraph at lines A--B`
- 视觉：某行文本伸出右边界

**修复策略（按优先级）**：

1. **引入断词点**  
   在溢出单词的合适位置添加 `\-` 连字符，使 LaTeX 能在该处断行。
   ```latex
   % 修改前
   This is a verylongwordthatdoesnotbreak.
   % 修改后
   This is a very\-long\-word\-that\-does\-not\-break.
   ```

2. **调整段落级容差**  
   在段落前临时增加 `\emergencystretch` 或调整 `\tolerance`。
   ```latex
   {\emergencystretch=1em  % 允许额外拉伸 1em
    This is the problematic paragraph content...
   }
   ```
   *注意：修改后需恢复默认值，避免影响全局。*

3. **局部微调措辞**（需 `semantic-polish-agent` 介入）  
   若排版手段无法解决，可替换为稍短的近义词或调整语序。
   ```latex
   % 修改前
   the implementation of the proposed methodology
   % 修改后
   the implementation of our method
   ```

---

### D1：表格单元格溢出

**问题特征**：
- 日志：`Overfull \hbox (Xpt too wide) in alignment at lines A--B`
- 视觉：表格某单元格内容超出列宽，与相邻列重叠或伸出表格边界

**修复策略（按优先级）**：

1. **改用 `tabularx` 环境**  
   将 `tabular` 替换为 `tabularx`，并为文本列分配 `X` 列类型以允许自动换行。
   ```latex
   % 修改前
   \begin{tabular}{|l|c|c|}
   \hline
   Method & Description & Score \\
   \hline
   Ours & A very long description that causes overflow & 0.95 \\
   \hline
   \end{tabular}

   % 修改后
   \usepackage{tabularx}  % 确保导言区已引入
   ...
   \begin{tabularx}{\linewidth}{|l|X|X|}
   \hline
   Method & Description & Score \\
   \hline
   Ours & A very long description that causes overflow & 0.95 \\
   \hline
   \end{tabularx}
   ```
   *规则*：多个文本列应共同使用 `X` 列，避免单列过宽。

2. **手动设置列宽**  
   使用 `p{宽度}` 列类型固定文本列宽度。
   ```latex
   \begin{tabular}{|l|p{4cm}|c|}
   ```

3. **精简表头或内容**  
   若表头过长导致溢出，可缩写表头或使用换行。
   ```latex
   % 修改前
   \textbf{Mean Average Precision at IoU 0.5}
   % 修改后
   \textbf{mAP@0.5}
   ```

4. **调整字号（谨慎使用）**  
   在表格环境内使用 `\small` 或 `\footnotesize`，但需确保与全篇表格风格一致。
   ```latex
   \begin{table}
   \small
   \begin{tabular}{...}
   ...
   \end{tabular}
   \end{table}
   ```

5. **旋转表格**  
   对于列数过多的宽表，使用 `\rotatebox` 或 `sidewaystable` 环境。
   ```latex
   \usepackage{rotating}
   ...
   \begin{sidewaystable}
   ...
   \end{sidewaystable}
   ```

---

### D2：长公式未断行

**问题特征**：
- 视觉：公式长度接近或超过栏宽，未在运算符处断行
- 日志：可能伴随 `Overfull \hbox` 在公式环境

**修复策略（按优先级）**：

1. **替换为多行公式环境**  
   - 单行公式 → `multline`：首行左对齐，末行右对齐，中间行居中。
   - 多行对齐公式 → `align` 或 `aligned`：在等号或运算符处对齐。
   ```latex
   % 修改前（equation 环境）
   \begin{equation}
   f(x) = a_1 x^1 + a_2 x^2 + a_3 x^3 + a_4 x^4 + a_5 x^5 + a_6 x^6
   \end{equation}

   % 修改后（multline 环境）
   \begin{multline}
   f(x) = a_1 x^1 + a_2 x^2 + a_3 x^3 \\
   + a_4 x^4 + a_5 x^5 + a_6 x^6
   \end{multline}

   % 或 align 环境（在等号处对齐）
   \begin{align}
   f(x) &= a_1 x^1 + a_2 x^2 + a_3 x^3 \nonumber \\
        &\quad + a_4 x^4 + a_5 x^5 + a_6 x^6
   \end{align}
   ```

2. **使用 `split` 环境嵌套在 `equation` 中**  
   保留单个公式编号，但内部换行。
   ```latex
   \begin{equation}
   \begin{split}
   f(x) &= a_1 x^1 + a_2 x^2 + a_3 x^3 \\
        &\quad + a_4 x^4 + a_5 x^5 + a_6 x^6
   \end{split}
   \end{equation}
   ```

3. **引入中间变量简化表达式**  
   若公式过长且无明显断点，可拆分为多个子公式。
   ```latex
   % 修改前
   P(y|x) = \frac{\exp(\sum_i w_i f_i(x,y))}{\sum_{y'}\exp(\sum_i w_i f_i(x,y'))}

   % 修改后
   Let $S(x,y) = \sum_i w_i f_i(x,y)$, then
   \begin{equation}
   P(y|x) = \frac{\exp(S(x,y))}{\sum_{y'}\exp(S(x,y'))}
   \end{equation}
   ```

---

### D3：URL/长标识符溢出

**问题特征**：
- 视觉：参考文献中的 URL 或 DOI 伸出右边界
- 日志：`Overfull \hbox` 在参考文献区域

**修复策略（按优先级）**：

1. **使用 `\url` 命令**  
   `\url` 命令（由 `hyperref` 或 `url` 宏包提供）能自动在合适位置断行。
   ```latex
   % 修改前
   https://github.com/very/long/url/that/overflows/the/margin
   % 修改后
   \url{https://github.com/very/long/url/that/overflows/the/margin}
   ```

2. **启用参考文献断行**  
   在导言区添加：
   ```latex
   \usepackage{url}
   \def\UrlBreaks{\do\/\do-}  % 允许在 / 和 - 处断行
   ```
   或对于 `biblatex`：
   ```latex
   \usepackage[backend=biber, url=true]{biblatex}
   \setcounter{biburlnumpenalty}{100}
   \setcounter{biburlucpenalty}{100}
   \setcounter{biburllcpenalty}{100}
   ```

3. **手动添加断行点**  
   在 URL 中允许断行处插入 `\linebreak[0]` 或使用 `\path` 命令。
   ```latex
   \path{https://github.com/very/long/url/\linebreak[0]that/overflows}
   ```

---

## 修复验证

每执行一项修复后，必须：

1. 通知 `orchestrator-agent` 重新编译。
2. 检查编译日志中对应行的 `Overfull \hbox` 是否消除或溢出量显著减少。
3. 若问题持续存在，尝试下一优先级策略。
4. 若所有策略均无效，标记为 `partial` 或 `failed`，并在报告中说明原因（如“公式语义复杂，无法自动断行，建议人工调整”）。

---

## 注意事项

- **表格修复后需检查视觉一致性**：改用 `tabularx` 后，确保全篇表格风格仍统一（参见 `consistency-polisher` 技能）。
- **公式断行需保持数学语义正确**：不要在括号或函数名中间断行。
- **URL 修复需保留超链接功能**：使用 `\url` 时自动包含超链接（若加载 `hyperref`）。
- **避免全局字号修改**：表格内的字号调整应局部化，并在同一文档中保持一致。

---

## 与其它技能的交互

- 若表格溢出修复后仍需调整列宽均衡，可联动 `consistency-polisher` 技能。
- 若段落溢出需语义微调，转交 `semantic-polish-agent` 处理。
- 修复结果最终由 `quality-gatekeeper-agent` 验收。

---

**Overflow Repair Skill 就绪。** 等待 `code-surgeon-agent` 调用，执行溢出类修复任务。
