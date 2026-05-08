# Content Integrity Protection Protocol

## 目的与范围

本协议定义 PaperFit 系统在 VTO 迭代过程中**保护学术内容完整性**的强制性机制。适用于所有修复 Agent（`code-surgeon-agent`、`semantic-polish-agent`）和质量门禁验收流程。

**核心原则**：VTO 的目标是**排版优化**，而非内容编辑。任何实质性学术内容的删除、篡改或语义漂移均被视为**严重违规**。

---

## 内容分类体系

所有 `.tex` 文件内容应按以下分类处理：

| 内容类别 | 示例 | 可修改性 | 说明 |
|----------|------|----------|------|
| **C0：排版控制** | `\vspace`, `\hspace`, `\begin{table}`, `\includegraphics[width=...]` | ✅ 自由修改 | 纯 LaTeX 控制序列，无语义内容 |
| **C1：元数据** | `\title{}`, `\author{}`, `\affiliation{}`, `\keywords{}` | ⚠️ 谨慎修改 | 可调整格式，不可删改内容 |
| **C2：引用键** | `\cite{smith2023deep}`, `\label{fig:result}`, `\ref{tab:ablation}` | ❌ 禁止修改 | 必须保持原样，不可增删 |
| **C3：公式与符号** | `$\alpha$`, `\begin{equation}`, `$x \in \mathbb{R}^n$` | ❌ 禁止修改 | 公式结构、变量定义不可改 |
| **C4：数据与结果** | 表格中的数值、`accuracy=95.2%`、`p<0.001` | ❌ 禁止修改 | 实验数据、统计结果绝对不可改 |
| **C5：学术论述** | 正文段落、结论、讨论、贡献陈述 | ❌ 禁止删除/篡改 | **核心保护对象**——实质性学术内容 |
| **C6：图表标题** | `\caption{Performance comparison...}` | ⚠️ 格式可改，内容不可改 | 可统一标点格式，不可删改描述 |
| **C7：参考文献条目** | `\bibitem{}`, `.bib` 文件内容 | ❌ 禁止修改 | 参考文献列表不可改 |

---

## 修复前内容边界检测（Pre-Repair Boundary Detection）

在执行任何修复之前，`code-surgeon-agent` 必须执行以下步骤：

### 步骤 1：识别待修改区域的_content_类型

```python
# 伪代码示例
def classify_content_context(tex_content, target_line_range):
    """
    识别目标行范围内的内容类型
    返回：{
        'primary_type': 'C5',  # C0-C7
        'contains_academic_content': True,
        'safe_to_modify': False,
        'boundary_notes': 'Contains substantive conclusions paragraph'
    }
    """
```

**分类规则**：
- 若目标区域包含完整的句子（有主语 + 谓语）→ C5
- 若目标区域包含数值数据（`XX%`, `p=...`, `accuracy=...`）→ C4
- 若目标区域仅包含 `\begin{...}`, `\end{...}`, 参数 → C0
- 若目标区域包含 `$...$` 或 `\[...\]` → C3

### 步骤 2：计算待修改区域的语义哈希

在修改之前，对待修改的**学术内容部分**（非 LaTeX 命令）计算语义哈希：

```bash
# 提取学术内容（过滤掉 LaTeX 命令）
grep -v '^\\[a-z]' target_section.tex | sed 's/\\[a-z]*{//g' | sed 's/}//g' > academic_content.txt

# 计算哈希
sha256sum academic_content.txt > pre_repair_hash.txt
```

或使用 Python：
```python
import hashlib

def extract_academic_content(tex_snippet):
    """过滤 LaTeX 命令，提取纯文本学术内容"""
    import re
    # 移除 LaTeX 命令
    text = re.sub(r'\\[a-zA-Z]+(?:\[[^\]]*\])?(?:\{[^}]*\})?', '', tex_snippet)
    # 移除环境标记
    text = re.sub(r'\\begin\{[^}]+\}|\\end\{[^}]+\}', '', text)
    return text.strip()

def semantic_hash(tex_content):
    """计算学术内容的语义哈希"""
    academic = extract_academic_content(tex_content)
    return hashlib.sha256(academic.encode()).hexdigest()

# 修复前
pre_hash = semantic_hash(original_section)
```

### 步骤 3：记录到 `state.json`

```json
{
  "pre_repair_snapshot": {
    "timestamp": "2026-04-11T22:30:00Z",
    "defect_id": "A1",
    "target_file": "aaai24_antibody.tex",
    "target_line_range": [412, 425],
    "content_type": "C5",
    "semantic_hash": "a3f8b9c2d1e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0",
    "academic_word_count": 87,
    "sentence_count": 4
  }
}
```

---

## 修复后内容完整性验证（Post-Repair Validation）

修复完成后，必须执行以下验证：

### 验证 1：语义哈希对比

```python
# 修复后
post_hash = semantic_hash(repaired_section)

if pre_hash != post_hash:
    # 学术内容发生了变化
    original_sentences = extract_sentences(original_section)
    repaired_sentences = extract_sentences(repaired_section)
    
    deleted = set(original_sentences) - set(repaired_sentences)
    added = set(repaired_sentences) - set(original_sentences)
    
    if deleted:
        raise ContentIntegrityViolation(
            f"Academic content deleted during repair: {deleted}"
        )
```

### 验证 2：学术字数统计对比

```python
def count_academic_words(tex_content):
    """统计纯学术词汇数量（排除 LaTeX 命令）"""
    academic = extract_academic_content(tex_content)
    # 简单分词：按空格和标点分割
    words = re.findall(r'\b[a-zA-Z]+\b', academic)
    return len(words)

pre_word_count = count_academic_words(original_section)
post_word_count = count_academic_words(repaired_section)

# 允许 5% 的浮动（用于必要的语义微调）
if abs(post_word_count - pre_word_count) / pre_word_count > 0.05:
    flag_for_manual_review("Academic word count changed by more than 5%")
```

### 验证 3：关键段落存在性检查

对于论文的**关键章节**（Abstract, Introduction, Conclusions），必须验证其核心段落仍然存在：

```yaml
# config/content_boundaries.yaml
critical_sections:
  - label: "Abstract"
    start_pattern: "\\begin{abstract}"
    end_pattern: "\\end{abstract}"
    min_sentences: 3
    min_words: 100
    
  - label: "Conclusions"
    start_pattern: "\\section{Conclusion}"
    end_pattern: "(?=\\section{|\\bibliography|\\end{document})"
    min_sentences: 2
    min_words: 50
```

---

## 违规响应机制

当检测到内容完整性违规时，系统必须：

### 级别 1：轻微变更（学术内容变化 <5%）

- 记录到 `state.json` 的 `content_changes` 数组
- 在诊断报告中标注
- 继续流程，但标记为"需人工复核"

### 级别 2：中度变更（5%-15%）

- **暂停自动修复流程**
- 生成 `content_diff_report.md`，列出具体变更
- 等待用户确认后再继续

### 级别 3：严重变更（>15% 或关键段落删除）

- **立即中止 VTO 流程**
- **自动回滚**到修复前备份
- 生成 `CRITICAL_CONTENT_VIOLATION.md` 报告
- 向用户发出警报

```json
{
  "violation_level": 3,
  "action_taken": "auto_rollback",
  "rollback_target": "data/backups/aaai24_antibody_20260411_223000.tex",
  "violation_details": {
    "section": "Conclusions",
    "deleted_sentences": [
      "These findings demonstrate the practical utility of our approach.",
      "Future work will explore extensions to multi-modal settings."
    ],
    "deleted_word_count": 47
  }
}
```

---

## 质量门禁验收增强

`quality-gatekeeper-agent` 在验收时必须增加**内容完整性检查门**：

### 第五道门：内容完整性（新增）

| 检查项 | 证据来源 | 通过条件 |
|--------|----------|----------|
| 学术内容哈希无重大变化 | `content_integrity_check.json` | 哈希差异 <5% 或已人工确认 |
| 无实质性内容删除 | `content_diff_report.md` | 无级别 3 违规 |
| 关键章节完整 | `critical_sections_check.json` | Abstract/Conclusions 段落存在 |

**验收逻辑**：
- 若内容完整性检查失败 → `CONTINUE`（回滚后重试）或 `BLOCKED`（需人工介入）
- 仅当内容完整性通过时，才允许输出 `DONE`

---

## 诊断报告增强

在 `diagnostic_report_round{N}.md` 中增加以下章节：

```markdown
## 内容完整性验证

- 修复前学术内容哈希：`a3f8b9c2...`
- 修复后学术内容哈希：`a3f8b9c2...`（一致 ✅ / 不一致 ⚠️）
- 学术词汇数量变化：1234 → 1230（-0.3% ✅）
- 关键章节检查：
  - Abstract：完整 ✅
  - Conclusions：完整 ✅
- 内容变更详情：无实质性内容变更（仅排版调整）
```

---

## 实施清单

- [x] **创建 `scripts/content_integrity_check.py`**：实现语义哈希、内容提取、差异对比功能
- [x] **创建 `config/content_boundaries.yaml`**：定义关键章节的识别规则和最小阈值
- [x] **更新 `state_manager.py`**：增加 `pre_repair_snapshot` 和 `content_integrity` 字段
- [x] **更新 `diagnostic_report.template.md`**：增加内容完整性验证章节
- [x] **完成内容完整性能力校验与验证闭环**：覆盖语义哈希、内容提取与差异对比
- [ ] **更新 `code-surgeon-agent.md`**：在"第三步：备份源文件"之后增加"第四步：内容边界检测与哈希计算"
- [ ] **更新 `quality-gatekeeper-agent.md`**：增加"第五道门：内容完整性"
- [ ] **集成到 VTO 闭环**：在 orchestrator 中调用 content_integrity_check.py

---

## 与现有文档的关系

本协议扩展以下现有文档：

- `code-surgeon-agent.md` 中的"保持语义：不更改任何学术内容"原则 → **本协议提供强制执行机制**
- `quality-gatekeeper-agent.md` 中的验收标准 → **本协议增加第五道门**
- `vto_taxonomy.yaml` 中的 `semantic_constraints` → **本协议提供验证方法**

---

**协议版本**: 1.0  
**生效日期**: 2026-04-11  
**最后更新**: 2026-04-11（测试套件完成：26/26 测试通过）
