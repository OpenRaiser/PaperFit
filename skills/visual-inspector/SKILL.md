---
name: visual-inspector
description: "PaperFit visual inspector expertise for LaTeX paper layout."
---

# Visual Inspector Skill

## 概述

本技能是 PaperFit 视觉排版优化闭环中的关键环节，专门负责 **PDF 页图渲染与视觉验收指导**。它封装了将 PDF 转换为逐页高分辨率图片的标准化流程，并为 `layout-detective-agent` 和 `quality-gatekeeper-agent` 提供详细的逐页视觉检查清单。

该技能由 `orchestrator-agent` 在每次编译后调用，确保多模态证据链中的“页图”环节可靠、一致且可复现。

## 适用场景

- 每次编译成功后，需生成页图供视觉 Agent 审查。
- 手动触发视觉检查（如 `/check-visual` 命令）。
- 修复前后对比验证。

## 输入规范

| 输入项 | 来源 | 必需 | 说明 |
|--------|------|------|------|
| PDF 文件路径 | 编译输出 | ✅ | 通常为 `main.pdf` |
| 输出目录 | 配置或默认 | ✅ | 页图存放目录，默认为 `data/pages/` |
| DPI 参数 | 配置或调用方指定 | ✅ | 渲染分辨率，默认 220 DPI |
| 页码范围 | 调用方指定 | ⚠️ | 若为空，渲染全部页面 |
| 局部裁剪参数 | 调用方指定 | ⚠️ | 如 `{page: 5, bbox: [x,y,w,h]}`，用于表格/公式局部复查 |
| PDF native geometry | 编译输出 PDF | ⚠️ | 若安装 PyMuPDF，可作为 B2 宽度和 D1 overflow 的辅助机器信号 |

## 输出规范

本技能输出两份产物：

1. **页图文件集**：PNG 或 JPG 格式的逐页图片，命名规则为 `page_001.png`、`page_002.png` 等。
2. **渲染报告 JSON**：

```json
{
  "skill": "visual-inspector",
  "status": "success | partial | failed",
  "pdf_path": "main.pdf",
  "output_dir": "data/pages/",
  "dpi": 220,
  "pages_rendered": 9,
  "page_files": [
    {"page": 1, "file": "data/pages/page_001.png", "width": 1700, "height": 2200},
    {"page": 2, "file": "data/pages/page_002.png", "width": 1700, "height": 2200}
  ],
  "cropped_regions": [
    {
      "page": 5,
      "object": "Table 2",
      "file": "data/pages/page_005_table2.png",
      "bbox": [100, 450, 800, 300]
    }
  ],
  "errors": []
}
```

## 渲染流程

### 第一步：环境检查

1. 确认 PDF 文件存在且可读。
2. 检查 Python 环境及所需依赖：
   - `pdf2image` 库
   - Poppler 工具（`pdftoppm` 或 `pdftocairo`）

若 Poppler 未安装，根据操作系统提供安装指引：

```bash
# Debian/Ubuntu
sudo apt-get install poppler-utils

# macOS
brew install poppler

# Windows
# 下载 poppler 并添加到 PATH，或使用 conda install -c conda-forge poppler
```

3. 若依赖缺失，报告错误并终止，由上层 Agent 提示用户安装。

可选 native geometry 检测依赖：

- `PyMuPDF`（Python import 名为 `fitz`）：用于读取 PDF 原生 object/text bbox，辅助检测 B2 宽度不足/超宽与 D1 native overflow。
- 该信号不是页图验收替代品；即使 PyMuPDF 不可用，也应继续渲染页图并完成视觉检查。

### 第二步：执行渲染

**禁止**在用户 LaTeX 项目里假设存在 `scripts/render_pages.py`。页图渲染由 **PaperFit npm/CLI 包**提供，在论文项目根目录执行：

```bash
paperfit render <相对或绝对路径的.pdf> --output data/pages --dpi 220
# 示例
paperfit render main.pdf --dpi 300
```

前提：`npm install -g paperfit-cli`（或等价全局安装），`paperfit` 在 `PATH` 中。输出目录 `--output` 相对于**当前工作目录**（一般为论文根目录）。

**其它包内 Python/Bash**（如 `parse_log.py`、`state_manager.py`）一律在论文根目录使用 **`paperfit run scripts/<文件名> [参数…]`**，勿在用户项目里假设存在同名 `scripts/`。

若仅能通过 Python 调用包内脚本，先执行 `paperfit root` 得到包根目录，再：

`python3 "$(paperfit root)/scripts/render_pages.py" main.pdf --dpi 220`

（或直接调用 `pdf2image` 库，逻辑须与下方一致。）全局未装 `paperfit` 时可用：`npx paperfit-cli render …`。

#### 基础渲染命令（库级参考）

```python
from pdf2image import convert_from_path

pages = convert_from_path(
    pdf_path,
    dpi=220,
    fmt='png',
    thread_count=2,
    grayscale=False,
    size=None
)

for i, page in enumerate(pages, start=1):
    page.save(f"{output_dir}/page_{i:03d}.png", "PNG")
```

#### 渲染参数建议

| 场景 | DPI | 说明 |
|------|-----|------|
| 整页常规检查 | 180-220 | 平衡清晰度与文件大小 |
| 表格/公式细节复查 | 260-320 | 需清晰辨认小字号或密集内容 |
| 局部裁剪复查 | 320 | 聚焦特定区域，可接受较大文件 |

#### PyMuPDF native geometry（可选）

当 runtime 已记录编译 PDF 路径时，视觉聚合阶段可调用：

```bash
paperfit run scripts/visual_signal_aggregator.py data/pages --output data/visual_signal_report.json --pdf main.pdf
```

该报告会把 native PDF bbox 映射到渲染页图坐标，并输出：

- `source=pymupdf_native`
- B2 `subtype=underfilled_width` 或 `subtype=overflow_width`
- `width_context`、`object_width_ratio`、`object_width_page_ratio`
- D1 native overflow 的 `overflow_pt` 与 `pdf_bbox`

使用这些信号时必须回看对应 `page_*.png`；不要只凭 PDF 文本/对象抽取宣称视觉修复成功。

### 第三步：局部区域裁剪（可选）

当 `layout-detective-agent` 需要对特定表格、公式或段落进行高精度复查时，可请求渲染局部区域。

1. 首先以较高 DPI（如 320）渲染整页。
2. 根据调用方提供的边界框（bbox）裁剪图片。
3. 保存裁剪后的图片，命名包含对象标识（如 `page_005_table2.png`）。

裁剪示例：

```python
from PIL import Image

full_page = Image.open(f"{output_dir}/page_005.png")
cropped = full_page.crop((x1, y1, x2, y2))
cropped.save(f"{output_dir}/page_005_table2.png")
```

### 第四步：生成渲染报告

记录渲染结果，包括：
- 成功渲染的页数
- 每页图片的路径和尺寸
- 裁剪区域信息（如有）
- 错误或警告（如某些页渲染失败）

## 视觉检查清单

以下清单供 `layout-detective-agent` 在逐页审查时参考。本技能不执行检查，仅提供指导框架。

### 通用检查项（每页必查）

- [ ] 页面整体信息密度是否均衡？是否存在大面积无意义留白？
- [ ] 页眉、页脚、页码是否完整且位置正确？
- [ ] 是否有内容伸出页边距或栏宽？
- [ ] 图表是否清晰可读，无模糊或锯齿？
- [ ] 段落末尾是否有孤行或短尾巴？

### 首页专项

- [ ] 标题、作者、机构信息是否完整，格式是否正确？
- [ ] 摘要段是否与模板风格一致？
- [ ] 是否有不必要的空白或过大的标题间距？

### 正文页专项

- [ ] 章节标题是否突出且一致？
- [ ] 图表与正文的衔接是否自然？图表是否在引用附近？
- [ ] 跨页段落是否合理断开？
- [ ] 双栏布局中左右栏高度是否平衡（尤其末页）？
- [ ] **（A5 必查）双栏每一页**：左右栏**分别**审视是否存在「栏内中段占栏高约 30%+、无图无表、无正文」的竖向空洞，且另一栏同期仍有连续正文（节标题下大块白缝是典型的漏检点）
- [ ] **（A5 可选）** 已安装 OpenCV 时运行 **`paperfit run scripts/detect_column_void.py data/pages -o data/column_void_report.json`**，将机器投影结果与肉眼结论交叉验证

### 末页专项

- [ ] 参考文献是否完整，无被浮动体切断？
- [ ] 含 `Acknowledgements` / `References` / `Bibliography` 的页上是否出现正文图表标题或正文浮动体？若有，按硬失败处理。
- [ ] 末页留白是否在可接受范围（<20%）？
- [ ] 若为双栏，左右栏底部是否对齐？

### 表格专项

- [ ] 表格宽度是否匹配栏宽？有无超宽或过窄？
- [ ] 若 visual signal 中有 `source=pymupdf_native` 的 B2 报告，对照页图检查 `width_context`、`object_width_ratio` 和 bbox 是否合理。
- [ ] 列宽分配是否均衡？有无单列过宽挤压其他列？
- [ ] 表格字号是否与全篇其他表格一致？
- [ ] 表格线是否清晰？推荐使用 `booktabs` 风格。

### 图片专项

- [ ] 图片是否清晰，分辨率足够？
- [ ] 图片宽度是否充分利用栏宽？
- [ ] B2 宽度问题应区分单栏对象与跨栏对象；PyMuPDF 的 `width_context=column|page` 只能作为辅助判断。
- [ ] 图片标题是否在图片下方（表格标题在上方）？
- [ ] 图片中的文字（坐标轴标签、图例）是否可读？

### 公式专项

- [ ] 公式是否超出栏宽？
- [ ] 多行公式是否在合理位置断行并对齐？
- [ ] 公式编号是否在正确位置（通常右侧）？

## 与其它 Agent 的协作

- **上游调用者**：`orchestrator-agent` 在编译成功后调用本技能。
- **下游消费者**：
  - `layout-detective-agent` 使用页图进行视觉缺陷检测。
  - `quality-gatekeeper-agent` 使用页图进行最终验收对比。
  - `code-surgeon-agent` 在修复后可能请求局部页图验证特定修改。

## 异常处理

| 异常情况 | 处理方式 |
|----------|----------|
| Poppler 未安装 | 返回明确错误信息，包含安装指引 |
| PDF 文件损坏 | 报告错误，请求重新编译 |
| 部分页渲染失败 | 记录失败页码，尽可能渲染其余页，状态标记为 `partial` |
| 磁盘空间不足 | 报告错误，清理临时目录或提示用户释放空间 |

## 性能优化建议

- 对于大型 PDF（>20 页），可考虑仅渲染指定页码范围，避免不必要开销。
- 缓存机制：若 PDF 文件未修改且渲染参数相同，可复用已有页图。通过比较 PDF 文件哈希和渲染参数实现。

## 注意事项

- **页图是视觉验收的唯一依据**：严禁仅凭 PDF 文本抽取或日志判断排版质量。
- **保持页码对应**：页图文件名必须明确反映页码，便于缺陷定位。
- **高 DPI 不宜滥用**：过高 DPI 会导致图片体积庞大，影响传输和加载速度，按需使用。

---

**Visual Inspector Skill 就绪。**
