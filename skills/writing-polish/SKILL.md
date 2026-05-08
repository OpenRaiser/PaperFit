---
name: writing-polish
description: "PaperFit writing polish expertise for LaTeX paper layout."
---

# Writing Polish Skill

## 概述

本技能为 **Semantic Polish Agent** 提供具体、可执行的语义微调策略与禁区规则。它定义了在排版手段用尽后，如何通过最小化文字增删来消除孤行寡行、控制页数预算或优化末页留白，同时严格保持学术内容的原意、数据和结论不变。

该技能不直接被 `orchestrator-agent` 调用，而是作为 `semantic-polish-agent` 的知识库和行为规范。所有语义级改写必须遵循本技能中定义的技巧和约束。

Source-changing 边界：语义级改写是高风险 source mutation。默认只能生成候选改写、风险和 approval 状态；只有显式 `--apply` 或宿主等价授权存在时才允许写回。多轮语义改写必须同时显式启用 `--apply --max-rounds N`，并且每轮都通过 approval carry-forward、artifact freshness、candidate approval scope gate 和 gatekeeper `CONTINUE`；超出既有 approval scope 的语义修改必须重新获批。

## 适用场景

| 触发缺陷 | 操作方向 | 允许的改写幅度 |
|----------|----------|---------------|
| A1（孤行寡行） | 缩短 1-2 行 | 增删 3-8 词 |
| A2（末页留白） | 扩展 2-4 行 | 增加 20-50 词 |
| A3（页数预算） | 缩短或扩展多行 | 按需，但需分段执行 |
| 用户主动请求 | 精炼或扩写特定段落 | 用户指定 |

## 输入规范

| 输入项 | 来源 | 说明 |
|--------|------|------|
| 目标段落源码 | `semantic-polish-agent` 提取 | 需改写的一个或多个完整段落 |
| 改写目标 | 调用方请求 | `shorten` 或 `expand`，及期望行数变化 |
| 写作规范 | `config/writing_rules.yaml` | 时态、术语、禁用词等约束 |
| 上下文段落 | `semantic-polish-agent` 提取 | 前后各一段，用于保证语义连贯 |

## 输出规范

本技能输出改写后的文本及变更元数据，供 `semantic-polish-agent` 整合为最终报告。

```json
{
  "skill": "writing-polish",
  "changes": [
    {
      "paragraph_id": 3,
      "action": "shorten",
      "net_word_change": -6,
      "before_snippet": "It is worth noting that our method achieves state-of-the-art performance on several benchmark datasets.",
      "after_snippet": "Our method achieves state-of-the-art results on several benchmarks.",
      "techniques_used": ["remove_redundant", "phrase_to_word"],
      "rationale": "移除冗余修饰词，将 'achieves state-of-the-art performance on several benchmark datasets' 压缩为 'achieves state-of-the-art results on several benchmarks'。语义等价，数据未变。"
    }
  ],
  "warnings": []
}
```

## 改写策略

### 通用原则

1. **最小修改优先**：能改一词不改一句，能改一句不改一段。
2. **保持学术严谨**：绝不改变数据值、引用标记、专有名词、核心声明。
3. **局部影响评估**：每次改写后需编译验证，确保不引入新的孤行或溢出。
4. **可逆性**：保留改写前文本，便于人工审查或回滚。

---

### 策略组 1：缩短（Shorten）

目标：在不损失信息的前提下减少字数/行数。

#### 技巧 1.1：删除冗余修饰词

移除对学术内容无实质贡献的修饰语。

- 删除强调性副词：`very`、`quite`、`extremely`、`highly`
- 删除填充短语：`It is worth noting that`、`It should be emphasized that`、`It is important to mention that`
- 删除冗余限定：`in a certain sense`、`to some extent`

示例：

```
修改前：It is worth noting that our method achieves very competitive performance.
修改后：Our method achieves competitive performance.
减少：5 词
```

#### 技巧 1.2：短语替换为单词

用更简洁的单词或缩写替代多词短语。

| 原短语 | 替换为 |
|--------|--------|
| `in order to` | `to` |
| `a large number of` | `many` |
| `due to the fact that` | `because` |
| `at the present time` | `now` |
| `state-of-the-art methods` | `SOTA methods`（需已定义） |
| `with respect to` | `regarding` 或 `on` |

示例：

```
修改前：We conduct experiments in order to evaluate the performance of the proposed approach.
修改后：We conduct experiments to evaluate our approach.
减少：5 词
```

#### 技巧 1.3：被动语态转主动语态

主动语态通常更简短且更有力。

```
修改前：The experiments were conducted by us on three datasets.
修改后：We conducted experiments on three datasets.
减少：3 词
```

#### 技巧 1.4：合并相邻短句

将两个紧密相关的短句合并为一句。

```
修改前：We used the Adam optimizer. The learning rate was set to 1e-4.
修改后：We used Adam with a learning rate of 1e-4.
减少：6 词
```

#### 技巧 1.5：使用标准学术缩写

在全文首次定义后，使用公认缩写。

| 原词 | 缩写 |
|------|------|
| `state-of-the-art` | `SOTA` |
| `natural language processing` | `NLP` |
| `mean average precision` | `mAP` |

```
修改前：Our method outperforms previous state-of-the-art approaches on the natural language processing benchmark.
修改后：Our method outperforms previous SOTA approaches on the NLP benchmark.
减少：5 词（假设 SOTA/NLP 已定义）
```

#### 技巧 1.6：简化从句结构

将定语从句压缩为分词短语或前置定语。

```
修改前：The model which is trained on ImageNet achieves high accuracy.
修改后：The ImageNet-trained model achieves high accuracy.
减少：3 词
```

---

### 策略组 2：扩展（Expand）

目标：在不注水的前提下增加有实质内容的文字。

#### 技巧 2.1：显式化隐含因果关系

在结果陈述后补充简短的原因解释。

```
修改前：Our method outperforms the baseline by 3.2%.
修改后：Our method outperforms the baseline by 3.2%, likely because the attention mechanism better captures long-range dependencies.
增加：11 词
```

#### 技巧 2.2：补充结果解释

在表格或数据引用后，增加一句对关键发现的解读。

```
修改前：Table 2 shows the ablation results.
修改后：Table 2 summarizes the ablation study. Removing the temporal module causes a significant drop of 5.1%, confirming its importance for sequential modeling.
增加：18 词
```

#### 技巧 2.3：强化与相关工作的对比

在提及已有工作时，增加具体的差异说明。

```
修改前：Unlike previous work, we use a transformer-based architecture.
修改后：Unlike previous work that relied on recurrent networks with limited parallelization, we adopt a transformer architecture that scales more efficiently to long sequences.
增加：14 词
```

#### 技巧 2.4：添加局限性讨论

在结论或讨论部分，补充一句对当前方法局限性的客观陈述。

```
修改前：Future work will explore larger-scale datasets.
修改后：Future work will explore larger-scale datasets. A current limitation is the reliance on pre-trained word embeddings, which may not fully capture domain-specific terminology.
增加：17 词
```

#### 技巧 2.5：拆分长句为短句

通过增加句号拆分长句，可在不显著增加内容的情况下扩展行数。

```
修改前：Our method consists of three components: an encoder, a decoder, and a refinement module.
修改后：Our method consists of three components. First, the encoder extracts features from the input. Second, the decoder generates initial predictions. Finally, the refinement module iteratively improves the output.
增加：14 词，行数增加更多
```

#### 技巧 2.6：补充技术细节（谨慎）

在不泄露未公开信息的前提下，可适当补充已在论文其他部分出现过的技术细节。

```
修改前：We use a standard cross-entropy loss.
修改后：We use a standard cross-entropy loss with label smoothing of 0.1, following common practice in image classification.
增加：10 词
```

---

## 改写禁区（绝对禁止）

以下操作 **严禁** 进行，违反任何一条都不得输出修改。

### 禁区 1：篡改数据与结果

- 不得修改任何数值、百分比、指标名称。
- 不得增删或更改实验设定、数据集名称、模型参数。
- 不得修改表格中的任何单元格内容。

### 禁区 2：编造内容

- 不得引入原论文中不存在的引用、相关工作、方法细节。
- 不得虚构实验、消融研究、用户调查。
- 不得添加未经作者确认的局限性或未来工作方向（除非是论文其他部分明确提及的内容）。

### 禁区 3：改变核心声明

- 不得弱化或夸大论文的贡献声明。
- 不得修改结论段落中的主要论断。
- 不得改变任何 `\ref{}` 或 `\cite{}` 的引用关系。

### 禁区 4：引入非学术表达

- 不得使用口语化、情绪化、主观化的语言。
- 不得添加无意义的填充句（如 `This is a very interesting result.` 后无任何分析）。
- 不得违反 `config/writing_rules.yaml` 中的任何硬规则（如时态混乱、口语缩写）。

### 禁区 5：破坏 LaTeX 结构

- 不得修改 `\section`、`\label`、`\ref`、`\cite` 等关键命令。
- 不得增删或修改 `\begin{...}` 和 `\end{...}` 环境边界。
- 不得在公式环境内进行语义改写。

---

## 改写验证清单

每完成一次改写，`semantic-polish-agent` 必须自检以下项目：

- [ ] 所有数值、百分数、指标名称是否与原段落完全一致？
- [ ] 所有 `\ref{}`、`\cite{}` 命令是否未被触碰？
- [ ] 时态是否与上下文一致（相关工作用现在时，方法/实验用过去时）？
- [ ] 专有名词（方法名、模型名、数据集名）是否拼写正确且未变？
- [ ] 若引入了新缩写，是否在首次出现处已定义？
- [ ] 改写后的段落是否与前后文语义连贯？
- [ ] 是否有违反禁区的操作？

若任何一项未通过，必须回退并尝试其他改写方案。

---

## 与其它技能的协作

- **Space Utilization Fixer**：当排版手段（`\looseness` 等）无法解决孤行或页数问题时，向 `semantic-polish-agent` 发出请求。
- **Semantic Polish Agent**：本技能的直接使用者，严格按照本技能定义的策略和禁区执行改写。
- **Quality Gatekeeper Agent**：在最终验收时审查语义改写的合理性，确保未违反禁区。

## 常见问题与边界处理

**Q：如果段落已经很精炼，无法再缩短怎么办？**
A：标记为 `failed`，并向 `semantic-polish-agent` 返回明确原因（如“段落仅含 3 句，每句均含必要信息，无法在不损害语义的前提下缩短”）。

**Q：扩展时如何避免注水？**
A：优先使用技巧 2.1-2.4，这些技巧均基于论文已有信息进行显式化或深度解读。若确无扩展空间，同样标记 `failed`。

**Q：是否可以跨段落操作？**
A：原则上应优先在目标段落内解决。若确需跨段落（如将前一页的句子后移以消除孤行），需明确标注跨段落的改动范围，并确保逻辑连贯。

**Q：改写后是否需要重新编译？**
A：是。任何语义改写都可能改变分页，必须重新编译并经过视觉验收，确保达到了预期效果且未引入新缺陷。

---

**Writing Polish Skill 就绪。**
