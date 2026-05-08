# PaperFit — Claude Code plugin & marketplace

本仓库**可以作为 Claude Code 插件**使用，并已提供 **Marketplace 清单**（`.claude-plugin/marketplace.json`），用户可通过 `/plugin marketplace add` 订阅后安装插件。单插件清单为 `.claude-plugin/plugin.json`。

安装完成后的推荐使用方式仍然是：在论文项目根目录直接用自然语言描述目标，例如“用 PaperFit 分析这篇论文的排版问题”。插件与 marketplace 是接入层，不改变 PaperFit 的产品心智。

## 清单约定（plugin.json）

以下为当前 manifest 与常见校验要求的对应关系（细节以 [官方插件 / Marketplace 文档](https://code.claude.com/docs/en/plugin-marketplaces) 为准）：

| 项 | PaperFit 现状 |
|----|----------------|
| 清单路径 | `.claude-plugin/plugin.json` |
| `version` | 已填写 |
| `agents` | 均为**具体 .md 文件**路径（未使用目录占位） |
| `skills` | 数组形式：`["./skills/"]` |
| `commands` | 数组形式：枚举 `.claude/commands/*.md` |
| `hooks` | 未在 manifest 中声明；若日后增加 `hooks/hooks.json`，请遵循当前 CLI 对「约定加载 vs 显式声明」的说明，避免重复注册 |

## Marketplace（推荐分发）

- 清单文件：[marketplace.json](./marketplace.json)
- Marketplace ID：`paperfit-vto`（刻意与 `OpenRaiser/PaperFit` 克隆临时目录名区分，避免 macOS 大小写不敏感盘上仅改大小写的 `rename` 失败）
- 插件 ID：`paperfit`

在 Claude Code 中（命令以你当前 CLI 为准，参见 [官方文档](https://code.claude.com/docs/en/plugin-marketplaces)）：

```text
/plugin marketplace add OpenRaiser/PaperFit
/plugin install paperfit@paperfit-vto
```

或使用 HTTPS：`/plugin marketplace add https://github.com/OpenRaiser/PaperFit`

已克隆到本地时：

```text
/plugin marketplace add /path/to/PaperFit
/plugin install paperfit@paperfit-vto
```

更新市场：

```text
/plugin marketplace update paperfit-vto
```

## 直接安装插件（不经 marketplace）

```bash
git clone https://github.com/OpenRaiser/PaperFit.git
cd PaperFit
claude plugin validate .claude-plugin/plugin.json
claude plugin add .
```

```bash
claude plugin add https://github.com/OpenRaiser/PaperFit
```

**与插件并列的方式（不经过 plugin 系统）：**

```bash
npm install -g paperfit-cli
paperfit-install   # 复制到 ~/.claude，任意项目可用斜杠命令
```

两种方式可同时存在；插件由 Claude CLI 管理，`paperfit-install` 是直接写入 `~/.claude` 的宿主资产安装器。

注意：

- 插件负责 Claude Code 侧的接入与分发。
- `paperfit-cli` / `paperfit-install` 提供共享 runtime、skills 与宿主资产。
- 只安装插件并不会自动带来本地全局 `paperfit` 命令。

## 校验失败时

官方校验器较严，常见报错如 `agents: Invalid input` 多因：**agents 写成目录**或**缺少 `version`**。可在项目根执行：

```bash
claude plugin validate .claude-plugin/marketplace.json
claude plugin validate .claude-plugin/plugin.json
```

并根据报错调整路径或字段形状。

## 主文档

完整安装与系统依赖见仓库根目录 [README.md](../README.md)。
