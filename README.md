<a id="top"></a>

<div align="center">

<img src="plugins/paperfit/assets/paperfit-readme-banner.png" alt="PaperFit - Vision-in-the-Loop Academic Typesetting" width="980" />

# PaperFit

**Vision-in-the-Loop Academic Typesetting Agent System**

PaperFit 是面向 `Claude Code`、`Codex`、`Cursor` 的视觉闭环 LaTeX 排版系统。PaperFit 组织“渲染页图—视觉诊断—受约束源码修复—重新渲染与视觉复验”的结构化循环，由当前多模态宿主执行。实际页图反馈是诊断和每个修复候选验收的必要环节，每个完整候选重新渲染后都检查全部物理页面，再决定保留、修改或回滚；独立视觉 API 可补充证据，不能把视觉反馈降为可选。最终交付前检查全部物理页面，并保留每轮诊断、修改、视觉观察与保留或回滚决策。

<p>
  <img src="https://img.shields.io/badge/status-active%20development-2ea44f?style=for-the-badge" alt="Project Status">
  <img src="https://img.shields.io/badge/python-3.8%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/node.js-18%2B-339933?style=for-the-badge&logo=node.js&logoColor=white" alt="Node.js">
  <img src="https://img.shields.io/badge/hosts-Claude%20Code%20%7C%20Codex%20%7C%20Cursor-1F5E4A?style=for-the-badge" alt="Hosts">
  <img src="https://img.shields.io/badge/license-MIT-blue?style=for-the-badge" alt="License">
</p>

<p>
  <a href="#为什么需要-paperfit"><b>为什么需要</b></a> ·
  <a href="#核心能力"><b>核心能力</b></a> ·
  <a href="#真实效果展示"><b>效果展示</b></a> ·
  <a href="#快速开始"><b>快速开始</b></a> ·
  <a href="#安装"><b>安装</b></a> ·
  <a href="#使用方法"><b>使用方法</b></a> ·
  <a href="#工作流"><b>工作流</b></a> ·
  <a href="#架构"><b>架构</b></a>
</p>

</div>

---

## 为什么需要 PaperFit

论文的 LaTeX 编译通过，并不代表版面已经合格。真实投稿前常见的问题往往发生在视觉层面：浮动体堆在一起、双栏页面出现大面积空洞、表格风格不一致、公式或表格溢出、模板迁移后对象位置失控。这些问题很难只靠 `.log` 文件发现，也很难靠一次性文本改写稳定解决。

PaperFit 把论文排版视为一个视觉闭环任务：先把 PDF 渲染成页面图像，再结合 LaTeX 日志、交叉引用和模板规则判断问题，最后回写源码并重新渲染验收。它的目标不是替代作者写论文，而是把“看版面、修源码、再检查”的重复劳动交给 Agent。

| 传统方式 | PaperFit |
|------|------|
| 主要关注编译错误 | 同时关注编译、视觉版面和内容完整性 |
| 人工翻 PDF 找问题 | 页图渲染后按缺陷类型系统诊断 |
| 手动调整浮动体和表格 | 由 Agent 生成修复策略并受控改源码 |
| 模板迁移后逐页救火 | 将模板规则、栏型、浮动体和表格一起处理 |
| 修完后靠肉眼确认 | 用视觉门禁决定继续修复还是交付 |

## 核心能力

| 能力 | 说明 |
|------|------|
| **视觉排版诊断** | 识别孤行、末页留白、双栏空洞、浮动体堆积、表格不一致、溢出与对齐问题。 |
| **完整 VTO 修复** | 串联编译、日志解析、页图渲染、视觉诊断、源码修复与复验。 |
| **模板迁移** | 支持 CVPR、ICLR、ACL、ACM 等常见学术模板之间的迁移与版式重整。 |
| **页数控制** | 区分页数上限与精确目标，按约定的正文、参考文献和附录范围联合优化页数与可读性；语义修改另需用户授权。 |
| **局部对象修复** | 针对单个表格、图、公式或页面问题执行更小范围的修复。 |
| **跨宿主分发** | 同一套能力可安装到 `Claude Code`、`Codex`、`Cursor`。 |

## 真实效果展示

下面的案例来自 PaperFit 的真实排版修复效果展示。

<p align="center">
  <a href="plugins/paperfit/assets/paperfit-case1.png">
    <img src="plugins/paperfit/assets/paperfit-case1.png" alt="PaperFit case 1 layout repair result" width="980" />
  </a>
</p>

<p align="center">
  <a href="plugins/paperfit/assets/paperfit-case2.png">
    <img src="plugins/paperfit/assets/paperfit-case2.png" alt="PaperFit case 2 layout repair result" width="980" />
  </a>
</p>

## 适合场景

- 投稿前检查论文是否存在明显视觉排版缺陷。
- 将已有论文迁移到新的会议模板，并尽量保持内容和对象稳定。
- 把正文压缩到目标页数，同时尽量不改变学术含义。
- 修复宽表、浮动体堆叠、双栏空洞、overfull、caption 不一致等问题。
- 在 Agent 宿主中用自然语言发起排版任务，而不是手动串脚本。

## 快速开始

安装完成后，在论文项目根目录直接对宿主说目标即可：

```text
用 PaperFit 分析这篇论文的排版问题
```

```text
$paperfit Inspect this paper's layout and tell me the main visual defects
```

```text
用 PaperFit 把这篇论文迁移到 CVPR 模板，并尽量保持图表和引用稳定
```

```text
用 PaperFit 把正文压到 8 页，尽量不要改学术内容
```

PaperFit 会自动推断主 `.tex` 文件、当前模板、页面预算和需要进入的修复路径。只有在项目结构不清楚、环境缺失或目标本身有歧义时，它才需要你补充信息。

## 安装

### 环境要求

- Node.js `18+`
- Python `3.8+`
- Poppler：用于 PDF 信息读取和页图渲染
- LaTeX 编译环境：如 `tectonic`、`pdflatex` 或模板要求的工具链

macOS 上可安装 Poppler：

```bash
brew install poppler
```

### npm 安装

```bash
npm install -g paperfit-cli
paperfit-install --target claude
```

也可以安装到其他宿主：

```bash
paperfit-install --target codex
paperfit-install --target cursor --project /path/to/paper
paperfit-install --target all
# 仅在当前论文项目安装，不写宿主全局目录
paperfit-install --target codex --scope project
```

安装后建议运行一次体检，并安装 Python 依赖：

```bash
paperfit doctor --target claude
pip3 install -r "$(npm root -g)/paperfit-cli/requirements.txt"
```

### 从源码安装

```bash
git clone https://github.com/OpenRaiser/PaperFit.git
cd PaperFit
npm install
bash install.sh --local --target claude
```

Claude Code 插件市场安装：

```text
/plugin marketplace add OpenRaiser/PaperFit
/plugin install paperfit@paperfit-vto
```

宿主入口与源码修复流程见 [宿主原生工作流](docs/HOST_NATIVE_WORKFLOW.md)。Codex 使用可发现的 `paperfit` skill（可显式输入 `$paperfit`），不依赖自定义 agent 注册。

Codex provider 相关说明见 [docs/CODEX_PROVIDER_SETUP.md](docs/CODEX_PROVIDER_SETUP.md)。

## 使用方法

PaperFit 的推荐入口是自然语言，而不是记忆内部命令。

| 宿主 | 推荐入口 |
|------|------|
| `Claude Code` | `/paperfit` 后描述排版目标；也可使用 `/fix-layout`、`/check-visual`、`/repair-table` 等快捷命令。 |
| `Codex` | 使用 `$paperfit` 或自然语言请求同名 skill，由当前宿主会话执行并验收。 |
| `Cursor` | 在论文项目中描述任务，项目级 rule 会引导 Cursor 调用 PaperFit 能力。 |

常见任务：

| 任务 | 示例 |
|------|------|
| 排版分析 | `用 PaperFit 分析 main.tex 的视觉排版问题` |
| 完整修复 | `$paperfit Repair this paper layout and verify the final PDF` |
| 视觉检查 | `Use PaperFit for visual inspection only` |
| 表格修复 | `用 PaperFit 修复这个跨栏表格，不要用 resizebox 硬压缩` |
| 模板迁移 | `用 PaperFit 把这篇论文迁移到 CVPR 模板` |
| 长度调整 | `用 PaperFit 把正文压到 8 页，语义修改要最小` |
| 状态查看 | `$paperfit Summarize the current layout status` |

宿主的编辑权限来自用户请求。用户已经授权修复后，宿主可以用自己的编辑工具修改相关源码、include、本地样式或配置，组合多个修法、重构相关文件，也可以选择 PaperFit 未列出的方案；普通授权范围内的修改不用逐条向 PaperFit 或用户重新申请。诊断和视觉模型建议都要结合当前源码与页图核验，宿主可以接受、调整或拒绝误报。

`paperfit fix-layout` 是可选的受控批处理路径。该工具默认 dry-run，显式 `--apply` 或等价的批处理授权才允许它执行 bounded source mutation；默认 `max_rounds=1`。只有同时显式提供 `--apply --max-rounds N` 时，runtime 才会在 approval carry-forward、artifact freshness、candidate approval gate 和 gatekeeper `CONTINUE` 全部通过后继续下一轮。这些工具范围和轮数不是宿主原生编辑的权限上限。`human_review`、`approval_required` 或不支持的操作应交给当前宿主核查并选择合适路径，不等于每次都要等另一位人类批准。

验证以一个连贯候选为单位：候选可以包含一批相关修改，完成这一批后重新编译、渲染并实际检查全部物理页，重点放大查看修改对象、移动浮动体和疑似退化。全页检查不要求本轮一次修完全部问题：内容和模板检查通过、没有确认的新退化时，保留已验证的改进，继续处理原有问题和未达目标。某一项无效果不应连带丢弃其他有效修复；某项造成退化时优先只修正或撤销该项，再重新验证合并结果，无法安全分离才恢复最小安全检查点。保留中间进展不等于最终完成。无需每个中间小改都跑完整工具链，但科学内容、用户模板要求和证据真实性不能放松，目标未达到或证据缺失不能宣称完成。详见[宿主原生工作流](docs/HOST_NATIVE_WORKFLOW.md)。

### DeepSeek 辅助看图

在启动宿主之前设置环境变量 `DEEPSEEK_API_KEY`，PaperFit 会使用
`https://api.deepseek.com` 的 `deepseek-flash` 检查编译后的页面。
Claude Code、Codex 或 Cursor 仍负责规划和修复；这个配置只增加图像检查能力。
检查结果会记录覆盖率、未完成页和图表编号，失败不会被记成“没有问题”。
配置、自然语言任务示例和调用预算见
[DeepSeek 看图指南](docs/DEEPSEEK_VISION_LAYOUT.md)。

## 工作流

PaperFit 的闭环可以概括为：

```text
LaTeX project
  -> compile and parse logs
  -> render PDF pages
  -> diagnose visual defects
  -> host checks advice and plans coherent source repairs
  -> host edits within the user's authorized project scope
  -> recompile and rerender
  -> host verifies the final result; optional batch gatekeeper checks its route
```

视觉检查是交付前的必要步骤。PaperFit 不会只因为编译通过就宣称排版完成，也不会为了压缩页数静默删除 figure、table、caption、label 或交叉引用。

## VTO 缺陷分类

PaperFit 使用 Visual Typesetting Optimization (VTO) taxonomy 来统一诊断和修复语言：

| 类别 | 关注问题 |
|------|------|
| **A 空间利用** | 孤行、寡行、末页留白、双栏高度失衡、页面大空洞。 |
| **B 浮动体** | 图表位置、尺寸、堆叠、跨栏与正文关系。 |
| **C 一致性** | 表格、图表、caption、间距、风格不统一。 |
| **D 溢出与对齐** | overfull、公式断行、表格超宽、对象边界错位。 |
| **E 模板迁移** | 单双栏变化、宏包兼容、会议模板规则差异。 |

完整定义见 [config/vto_taxonomy.yaml](config/vto_taxonomy.yaml)。

## 架构

PaperFit 由 Agent 角色、技能包、配置和执行层组成。当前宿主负责判断任务、选择方案、编辑和组织闭环；CLI 与脚本负责渲染、状态记录、日志解析和可重复执行的修复动作。角色说明和专业技能是可用知识，不要求每项任务注册额外 Agent，也不规定宿主只能执行已列出的工具动作。

| 组件 | 职责 |
|------|------|
| `agents/` | 调度、视觉诊断、规则检查、源码修复、语义微调、质量门禁的角色说明。 |
| `skills/` | VTO taxonomy、视觉检查、浮动体优化、溢出修复、模板迁移、写作微调等能力说明。 |
| `config/` | 模板元数据、版式规则、Agent 角色、写作边界和缺陷分类配置。 |
| `scripts/` | PDF 渲染、日志解析、视觉信号聚合、状态管理、修复执行和门禁检查。 |
| `bin/paperfit.js` | `paperfit` CLI 入口，供安装、体检、渲染和内部执行调用。 |

简化目录：

```text
PaperFit/
├── agents/                 # Agent role definitions
├── skills/                 # PaperFit capability bundle
├── config/                 # templates, layout rules, taxonomy
├── scripts/                # render, inspect, repair, gatekeeper
├── plugins/paperfit/       # Codex plugin assets
├── .claude/commands/       # Claude Code commands
├── bin/paperfit.js         # CLI entry
├── install.sh
└── README.md
```

## 开发与验证

在仓库根目录准备开发环境：

```bash
npm install
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

运行基础验证：

```bash
npm run verify
```

常用开发命令：

```bash
paperfit doctor --target claude
paperfit render paper.pdf --output data/pages
paperfit status
paperfit monitor
```

`paperfit monitor` 会启动只读的 PaperFit Observatory 本地监控台，用中文界面展示 runtime 状态、视觉证据、修复决策、安全边界和 benchmark evidence。它读取当前项目的 `data/` artifacts，不会触发源码修改。

更多安装和命令细节见：

- [docs/COMMANDS_SETUP.md](docs/COMMANDS_SETUP.md)
- [docs/RELEASE_AND_LOCAL_UPDATE.md](docs/RELEASE_AND_LOCAL_UPDATE.md)
- [.claude-plugin/README.md](.claude-plugin/README.md)

宿主读取视觉结果时，会核对诊断是否仍对应当前源码、PDF 和完整可解码的页图。
源文件或产物改变后，需要重新生成视觉证据再验收。
见 [源码导航与视觉证据有效性](docs/SOURCE_NAVIGATION.md)。
交付文件可单独运行 [可配置的交付审计](docs/DELIVERY_AUDIT.md)。

## 内容保护

PaperFit 的自动修复必须保护学术内容和关键对象：

- 不静默删除 figure、table、caption、label、引用或实验结果。
- 不用 `\resizebox`、`\scalebox` 作为默认的表格压缩手段；它们和显式分页、局部间距应按实际内容、可读性和模板上下文复核，不能只按命令名称断定修法错误。
- 优先使用 `table*`、`tabularx`、列宽重构、浮动体重排等版式级方案。
- 语义编辑必须在用户授权范围内、最小且可审计，并保留修复依据；单纯排版授权不允许改写科学结论或数据。

相关协议见 [protocols/content-integrity-protection.md](protocols/content-integrity-protection.md)。

---

## 引用

如有帮助，请引用：

```bibtex
@article{yu2026paperfit,
  title={PaperFit: Vision-in-the-Loop Typesetting Optimization for Scientific Documents},
  author={Yu, Bihui and Xu, Xinglong and Jiang, Junjie and Cheng, Jiabei and Jia, Caijun and Li, Siyuan and He, Conghui and Wei, Jingxuan and Tan, Cheng},
  journal={arXiv preprint arXiv:2605.10341},
  year={2026}
}
```

## 许可证

MIT 许可证，见 [LICENSE](LICENSE)。
