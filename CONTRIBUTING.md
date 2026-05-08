# Contributing to PaperFit

感谢你考虑为 PaperFit 做出贡献！

## 项目概述

PaperFit 是一个基于 vision-in-the-loop 范式的多层智能体系统，专门对 LaTeX 学术论文执行视觉排版优化（VTO）。

## 如何贡献

### 报告问题

发现问题？请查看 [Issue 模板](.github/ISSUE_TEMPLATE/bug_report.md)，提供尽可能多的上下文：
- 执行的命令
- 编译日志
- 视觉缺陷类别
- 截图（如适用）

### 提出新功能

有改进建议？使用 [Feature Request 模板](.github/ISSUE_TEMPLATE/feature_request.md) 描述你的想法。

### 提交代码

1. **Fork 项目**
2. **创建分支**：`git checkout -b feature/amazing-feature`
3. **进行修改**
4. **运行发布前校验**：
   ```bash
   npm run verify
   ```
5. **提交变更**：使用清晰的提交信息
   ```
   feat: 添加新的 VTO 缺陷检测策略
   
   - 实现 Category X 的检测逻辑
   - 添加对应的 Skill
   - 更新文档
   ```
6. **推送分支**：`git push origin feature/amazing-feature`
7. **创建 Pull Request**

## 开发环境设置

### 系统依赖

```bash
# macOS
brew install poppler

# Ubuntu/Debian
sudo apt install poppler-utils
```

### Python 依赖

```bash
pip install -r requirements.txt
```

### Claude Code 设置

确保你已安装 Claude Code CLI，并在任意本地项目目录中运行本仓库（无需固定路径）。

## 架构指南

### 添加新的 Command

在 `.claude/commands/` 目录下创建新的 `.md` 文件：

```markdown
# /my-command — 命令描述

**作用**: 简短描述

## 用法

```
/my-command
```

## 执行流程

1. 步骤 1
2. 步骤 2
```

### 添加新的 Skill

在 `skills/` 目录下创建新的技能目录和 `SKILL.md` 文件。参考现有 Skill 的结构。

### 添加新的 Agent

在 `agents/` 目录下创建新的 `.md` 文件。参考 [CLAUDE.md](CLAUDE.md) 中的 Agent 职责定义。

## 代码风格

- 遵循项目现有的编码风格
- Python 代码遵循 PEP 8
- Markdown 文件使用统一的标题层级
- 注释使用中文（项目主要语言）

## 发布前校验

确保基础校验通过：

```bash
npm run verify
```

## 发布流程

项目维护者会定期发布新版本。发布前会：

1. 运行基础校验与必要 smoke
2. 更新 CHANGELOG.md
3. 打 Git 标签
4. 发布到 GitHub Releases

## 许可

本项目采用 MIT 许可 - 详见 [LICENSE](LICENSE) 文件。

## 联系方式

如有问题，请通过 GitHub Issues 联系项目维护者。
