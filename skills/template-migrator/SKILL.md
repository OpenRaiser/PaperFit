---
name: template-migrator
description: "PaperFit template migrator expertise for LaTeX paper layout."
---

# Template Migrator Skill

## 概述

本技能专门处理 **Category E：跨模板迁移缺陷**，包括：

- **E1**：单栏↔双栏图表尺寸失配
- **E2**：页数预算不匹配（如 9 页→14 页的内容重分布）
- **E3**：模板特定宏兼容性

该技能由 `code-surgeon-agent` 在 `/migrate-template` 命令触发时调用，负责将一篇论文从原模板平滑迁移至目标模板，并自动适配图表尺寸、页数预算和宏包兼容性。这是 PaperFit 最具差异化价值的能力，直接解决了科研工作者切换会议投稿时的真实痛点。

能力边界：当前 typed runtime 没有自动模板迁移 executor，可选自动批处理的 `--apply` 必须报告不支持，不能偷偷回退到旧脚本。用户已授权迁移时，宿主可以用原生编辑工具完成并验证迁移，无需等待自动 executor。先核对建议：**请你看看这是否是对的**；保留原模板/源码及可回退候选，确认目标模板、当前编译页和内容。

## 适用场景

| 缺陷 ID | 描述 | 优先级 | 是否允许语义修改 |
|---------|------|--------|-----------------|
| E1 | 单栏↔双栏图表尺寸失配 | Critical | 否 |
| E2 | 页数预算不匹配 | Critical | 是（最后手段） |
| E3 | 模板特定宏兼容性 | Critical | 否 |

## 输入规范

| 输入项 | 来源 | 说明 |
|--------|------|------|
| 主 `.tex` 文件路径 | 项目上下文 | 需修改的源文件 |
| 目标模板名称 | 用户命令参数 | 如 `ECCV2024`、`ICLR2025` |
| 模板配置 | `config/templates.yaml` | 包含目标模板的栏数、默认字号、页宽、预期页数等 |
| 原模板信息 | 自动检测或用户指定 | 当前 `\documentclass` 及主要宏包 |
| 排版侦探报告 | `layout-detective-agent` | 迁移后首次编译的 E 类缺陷列表 |

## 输出规范

```json
{
  "skill": "template-migrator",
  "status": "success | partial | failed",
  "modified_files": ["main.tex", "figures/fig1.tex"],
  "changes": [
    {
      "defect_id": "E1",
      "object": "Figure 1",
      "action": "单栏图改为跨栏图",
      "before": "\\begin{figure}\n\\includegraphics[width=\\linewidth]{fig1.pdf}\n\\end{figure}",
      "after": "\\begin{figure*}\n\\includegraphics[width=\\textwidth]{fig1.pdf}\n\\end{figure*}"
    }
  ],
  "macro_fixes": [
    {
      "issue": "\\theoremstyle undefined in ECCV",
      "fix": "改用 \\newtheorem 直接定义"
    }
  ],
  "unresolved": [],
  "page_budget_status": {
    "current_pages": 9,
    "target_pages": 14,
    "gap": 5,
    "action_taken": "核对用户页数政策并比较保持内容的布局候选"
  }
}
```

## 迁移流程

### 第一步：加载模板配置

1. 从 `config/templates.yaml` 读取目标模板的完整配置。
2. 配置项包括：
   - `documentclass`：如 `\documentclass[10pt,twocolumn]{article}` 或 `\documentclass{iclr2025}`
   - `column_type`：`single` 或 `double`
   - `default_figure_width`：`\linewidth` 或 `\textwidth`
   - `expected_pages`：配置中的页数参考值；不能代替用户指定的 exact/at_most、正文/参考文献/附录范围或当前投稿规则
   - `forbidden_packages`：与新模板冲突的宏包列表
   - `required_packages`：新模板必须加载的宏包

示例配置（`config/templates.yaml` 片段）：

```yaml
ECCV2024:
  documentclass: "\documentclass[10pt,twocolumn]{article}"
  column_type: double
  default_figure_width: "\linewidth"
  expected_pages: 14
  forbidden_packages: ["amsthm", "algorithm2e"]
  required_packages: ["graphicx", "amsmath", "amssymb"]
  float_behavior: "figures may use figure* for wide content"
```

### 第二步：分析原模板特征

1. 读取当前主 `.tex` 文件的 `\documentclass` 声明。
2. 识别当前栏数（单栏/双栏）。
3. 列出当前加载的宏包列表（`\usepackage{...}`）。
4. 若用户未明确指定原模板，尝试根据 `\documentclass` 自动推断。

### 第三步：执行模板替换

**候选管理**：

1. 写入前保留迁移前源码和资源，避免覆盖作者尚未保存的改动。
2. 宿主可用正常 patch/编辑工具形成连贯候选，合理组合 class、宏包和对象适配；无需强制调用指定写盘函数。
3. 编译失败时先检查并修复根因，或恢复最近可用候选；保留失败记录和有用的独立改动。
4. 中间编译可用于定位兼容问题，半完成状态不得当作最终迁移成功。

#### 3.1 替换 `\documentclass`

将原 `\documentclass` 替换为目标模板的声明。

```latex
% 修改前（ICLR 2025 单栏）
\documentclass{iclr2025}

% 修改后（ECCV 2024 双栏）
\documentclass[10pt,twocolumn]{article}
```

**注意**：若目标模板有多个可选参数（如 `review`、`final`），询问用户偏好或使用默认值。

#### 3.2 处理宏包冲突

1. 对比原宏包列表与目标模板的 `forbidden_packages`。
2. 若存在冲突，执行以下操作之一：
   - **移除**：直接注释或删除该 `\usepackage` 行。
   - **替换**：提供替代方案（如 `algorithm2e` → `algorithmic`）。
   - **条件编译**：使用 `\ifdefined` 等实现跨模板兼容。

```latex
% 修改前（含 amsthm，与新模板冲突）
\usepackage{amsthm}
\newtheorem{theorem}{Theorem}

% 修改后（改用 LaTeX 原生定义）
% \usepackage{amsthm}  % removed for template compatibility
\newtheorem{theorem}{Theorem}
```

#### 3.3 添加必需宏包

若目标模板要求特定宏包（如 `graphicx`），确保导言区已加载。若缺失，添加之。

```latex
% 确保必需宏包存在
\usepackage{graphicx}
\usepackage{amsmath}
```

### 第四步：图表尺寸适配（E1 修复）

这是跨模板迁移中最关键、最易出错的环节。

#### 4.1 判断单栏/双栏切换方向

| 原模板 | 目标模板 | 处理策略 |
|--------|----------|----------|
| 单栏 | 双栏 | 所有图表默认改为单栏宽（`\columnwidth`），宽图改为跨栏（`figure*`） |
| 双栏 | 单栏 | 所有跨栏图（`figure*`）改为普通图（`figure`），宽度改为 `\linewidth` |
| 双栏 | 双栏 | 保持原策略，仅检查宽度是否适配新模板的栏宽 |
| 单栏 | 单栏 | 基本不变，仅检查页宽是否变化 |

#### 4.2 智能判断哪些图应跨栏（单栏→双栏时）

对于原单栏中的全宽图，在双栏中若仍用单栏会显得过小。需根据图片**宽高比**智能决策：

- 若图片宽度 > 高度 × 1.5（宽高比 > 1.5），建议改为跨栏 `figure*`。
- 若图片宽度 ≤ 高度 × 1.5，保留单栏 `figure`，但宽度设为 `\columnwidth`。

```latex
% 原单栏全宽图（宽度 = \linewidth）
\begin{figure}
\includegraphics[width=\linewidth]{wide_arch.pdf}
\end{figure}

% 迁移后（宽高比大，改为跨栏）
\begin{figure*}
\includegraphics[width=\textwidth]{wide_arch.pdf}
\end{figure*}
```

#### 4.3 处理表格宽度

- 单栏表格在双栏中：宽度改为 `\columnwidth`。
- 若原表格为 `tabularx{\linewidth}`，改为 `tabularx{\columnwidth}`。
- 若表格列数过多，考虑改为跨栏 `table*` 并使用 `\textwidth`。

```latex
% 修改前（单栏宽表）
\begin{table}
\begin{tabularx}{\linewidth}{|l|X|X|}
...
\end{tabularx}
\end{table}

% 修改后（双栏单栏宽表）
\begin{table}
\begin{tabularx}{\columnwidth}{|l|X|X|}
...
\end{tabularx}
\end{table}
```

#### 4.4 特殊对象处理

- **长公式**：双栏中公式宽度受限，可能需将 `equation` 改为 `multline` 或 `align` 并手动断行。
- **算法伪代码**：双栏中宽度减半，可能需要调整缩进或改为跨栏 `figure*`。

### 第五步：编译并检测遗留问题

1. 完成上述修改后，执行首次编译。
2. 若编译失败，解析日志中的 `Undefined control sequence` 等错误（E3），返回第三步修正宏包冲突。
3. 若编译成功，渲染页图，调用 `layout-detective-agent` 检测 E1/E2 缺陷。

### 第六步：页数预算调整（E2 修复）

1. 获取当前 PDF 总页数。
2. 按用户授权的目标、exact/at_most 政策和计页范围计算偏差；配置中的 `expected_pages` 仅为参考。
3. exact 差一页仍未完成；at_most 只判定是否超上限，不能把不足页数当作必须扩写的缺陷。未知范围不得称为已满足。
4. 从迁移开始同时考虑页数和阅读质量。按 `space-util-fixer` 比较原生布局、人工空白、对象容器和自然流动；增页修改应同步考虑补偿候选。
5. 不默认扩写、删句或填空白。正文编辑只在已有单独授权内进行，并独立核对学术原意；没有 `--keep-content` 不是改内容的授权。

### 第七步：生成迁移报告

输出一份 Markdown 迁移报告，包含：

- 目标模板信息
- 已修改的文件列表
- 图表尺寸变更清单（哪些图改为跨栏、哪些保留单栏）
- 宏包冲突及解决方案
- 页数调整结果（初始页数 → 最终页数）
- 人工检查建议（如“请确认 Figure 3 改为跨栏后视觉效果”）

## 双栏迁移的特别注意事项

双栏模板的排版行为与单栏有本质不同：

1. **跨栏位置取决于活动模板和输出例程**：先检查模板支持的放置方式，并对照当前及邻页编译结果。不要一概断言只能页顶、必然下一页或永远不支持页底；不为某个通用规则盲目添加宏包。
2. **浮动体调度更复杂**：双栏中浮动体更容易堆积（B3），可能需要手动插入 `\FloatBarrier` 或调整源码位置。
3. **公式编号**：双栏中公式编号通常在右侧，若原单栏模板编号在左侧，需检查是否冲突。

## 与其它技能的协作

- **浮动体优化 (float-optimizer)**：迁移后检查是否存在新的浮动体问题，需要时配合修复。
- **空间利用修复 (space-util-fixer)**：页数调整依赖此技能。
- **语义润色 (semantic-polish-agent)**：扩写/缩写时调用。

## 验证标准

迁移成功的判定标准：

- [ ] 编译成功，无阻塞性错误
- [ ] 所有图表在新模板下尺寸适配（无溢出、无过窄）
- [ ] 页数符合目标模板预期（或用户接受偏差）
- [ ] 无未解决的宏包冲突
- [ ] 视觉风格与目标模板一致

---

**Template Migrator Skill 就绪。**
