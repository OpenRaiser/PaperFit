# 修改代码后：推 GitHub、发 npm、本机更新插件/Agent

本文档供维护者在**改完仓库内文件**后，按固定顺序发布并更新本机环境。包名以 **`paperfit-cli`**、远程以 **`OpenRaiser/PaperFit`** 为例，请按你的 fork/组织替换。

---

## 一、推送到 GitHub

在**包仓库根目录**（含 `package.json` 的目录）执行：

```bash
git status
git diff
git add -A
git commit -m "feat: 简述本次改动"
git pull origin main --rebase   # 若团队用 main；有冲突先解决
git push origin main
```

**若本次发 npm 且使用 `npm version` 打的 tag**（见下文），还需：

```bash
git push origin v1.0.6   # 换成实际版本 tag
```

**可选**：在 GitHub 网页 **Releases** 里基于该 tag 写 Release notes，便于用户查看变更。

---

## 二、发布到 npm（`paperfit-cli`）

### 2.1 前置条件

- 已安装 Node.js（建议 ≥18，与 `package.json` 的 `engines` 一致）。
- 已登录 npm：`npm whoami` 能显示用户名。
- 当前账号对 **`paperfit-cli`** 有 **publish** 权限（`npm owner ls paperfit-cli`）。
- **工作区干净**：`npm version` 会要求无未提交改动；否则先 `git commit` 或使用 `npm version patch --no-git-tag-version`（仅改版本号，不自动 commit）。

### 2.2 版本号

在项目根目录任选其一：

```bash
# 推荐：自动改 package.json + package-lock.json，并生成 git commit 与 tag
npm version patch -m "chore: release %s"    # 1.0.5 → 1.0.6
# npm version minor
# npm version major
```

若工作区不干净：

```bash
npm version patch --no-git-tag-version
# 再手动 git add package.json package-lock.json && git commit && git tag v… && push
```

### 2.3 发布

```bash
npm publish --access public
```

- 若账号开启 **2FA（auth-and-writes）**：需使用验证器里的 **6 位 TOTP**，例如  
  `npm publish --access public --otp=123456`（**123456 仅为格式示例，请换成实时码**）。  
- 或使用 npm 网站生成的 **Granular Access Token** 写入 `~/.npmrc` 的 `//registry.npmjs.org/:_authToken=...`，可减少交互式 OTP 场景（以 npm 当前策略为准）。

### 2.4 发版后自检

```bash
npm view paperfit-cli version
npm install -g paperfit-cli@latest
paperfit --version
paperfit doctor --target claude
```

### 2.4.1 发布前校验说明

发布前至少完成以下基础校验：

| 命令 | 覆盖范围 | 适用时机 |
|------|------|------|
| `npm run verify` | 配置校验、Node 语法检查、Python warning gate | 日常提交前、发版前 |
| `paperfit doctor --target <host>` | 宿主环境与安装状态体检 | 本机发版验证、安装器改动后 |

对应 CI：

- `.github/workflows/ci.yml`：基础校验链，运行 `npm run verify`

### 2.5 常见问题

| 现象 | 处理 |
|------|------|
| `Git working directory not clean` | 先提交或 `stash`，或 `--no-git-tag-version`。 |
| `E404` / 无权限 | 确认 `npm whoami`，联系包 owner 执行 `npm owner add <你> paperfit-cli`。 |
| `EOTP` | 使用认证器**当前** 6 位码；不要用占位数字。 |
| 同一版本不能二次发布 | 再执行一次 `npm version patch` 升版本后 `publish`。 |

**注意**：发布前确认 `package.json` 的 `files` 字段不会把 `__pycache__`、`*.pyc` 等打进包；若本地有 `scripts/__pycache__/`，应加入 `.gitignore` / `.npmignore` 或在发版前删除。

---

## 三、本机更新「新版 CLI + 插件/Agent/命令」

全局安装的 **`paperfit`** 与复制到宿主目录的 commands/skills/agents/rules **不是同一步**。安装器默认保留并跳过已有文件；升级 npm 包后，需显式运行 `paperfit install-global --target <host> --force`，才会用包内 canonical 资产刷新所选 PaperFit 副本及共享配置。安装 receipt 的新版版本号或 doctor 的技能元数据检查，不证明已跳过的文件内容已经升级。

### 3.0 更新 CLI，再刷新宿主资产

**仅更新 Claude 插件不会出现 `paperfit` 命令**；必须装/更新 **npm 全局包 `paperfit-cli`**。

| 情况 | 命令 |
|------|------|
| **任意目录**（不必 `cd` 克隆） | `npx -y paperfit-cli@latest upgrade --target claude` |
| 本机**已有** `paperfit` | `paperfit upgrade --target claude` 或 `cd <克隆根> && paperfit upgrade --local --target claude` |
| **仅在克隆根目录**（必须有 `package.json`） | `bash install.sh --update --local --target claude` |
| **还没有** `paperfit`、也不 `npx` | `bash install.sh --update --target claude` 或 `curl …/install.sh \| bash -s -- --update --target claude` |

也可以改成 `--target codex`、`--target cursor` 或 `--target all`。

以上 `upgrade` / `install.sh --update` 命令会更新 CLI，并尝试合并宿主资产；已有副本仍会被跳过。要刷新已有 PaperFit 技能、命令、角色、规则及共享配置，再运行：

```bash
paperfit install-global --target claude --force
```

将 `claude` 替换为所需宿主。若安装了项目副本，另加 `--project "<论文项目>"`；只刷新项目安装时使用 `--scope project --project "<论文项目>" --force`。`--force` 覆盖选中的 PaperFit 资产，不改写无关的宿主配置。

**常见错误**：在 **`~` 家目录**执行 `npm run upgrade` → npm 会找 `~/package.json`，不存在即 **ENOENT**。请先 **`cd` 到克隆根**，或改用 **`npx -y paperfit-cli@latest upgrade --target ...`** / **`paperfit upgrade --target ...`**。

完成后**仍**建议在 Claude 里执行 **§3.3** 的 `/plugin marketplace update` 与 `/plugin update`，插件版本与 npm 独立。

### 3.1 更新全局 CLI（npm 包本体）

```bash
npm install -g paperfit-cli@latest
# 或指定版本
npm install -g paperfit-cli@1.0.6
paperfit --version
```

### 3.2 把包内命令/技能/Agent 合并到本机 Claude Code

在**任意目录**均可执行（脚本会读已安装包路径）：

```bash
paperfit install-global --target claude --force
```

首次想预览将写入的文件：

```bash
paperfit install-global --target claude --force --dry-run
```

带 `--force` 的步骤会按 `config/install_targets.json` 刷新 **`paperfit-cli` 包内的**所选 commands / skills / agents / rules / config。省略 `--force` 仅复制缺少的文件，不能用它确认已有资产已更新；`--dry-run` 只打印计划。

### 3.3 用 Claude Code 插件更新（只贴**斜杠命令**，在聊天框逐条执行）

**前提**：市场名 **`paperfit-vto`**、插件标识 **`paperfit@paperfit-vto`** 与仓库内 `PaperFit-release/.claude-plugin/marketplace.json` 一致；若你 fork 了仓库，把下面 GitHub 路径换成你的。

#### 首次：添加市场 + 安装插件

```text
/plugin marketplace add OpenRaiser/PaperFit
```

```text
/plugin install paperfit@paperfit-vto
```

**或**用 HTTPS 添加市场（与上一行二选一即可）：

```text
/plugin marketplace add https://github.com/OpenRaiser/PaperFit
```

```text
/plugin install paperfit@paperfit-vto
```

#### 日常：拉到 GitHub 上最新插件内容

先看已装列表（确认名字，一般是 `paperfit`）：

```text
/plugin list
```

**优先尝试更新**（若 CLI 提示不支持该子命令，改用下面「卸载再装」）：

```text
/plugin update paperfit@paperfit-vto
```

**强制与远端一致**（等价于重装最新 commit）：

```text
/plugin uninstall paperfit@paperfit-vto
```

```text
/plugin install paperfit@paperfit-vto
```

#### 和 npm 的关系（两条线）

| 做什么 | 用哪里 |
|--------|--------|
| 更新 **`paperfit` / `paperfit run` / Python 脚本** | **终端**：`npm install -g paperfit-cli@latest`（见 3.1） |
| 更新 **斜杠命令 / Agent / Skill 文本**（来自 Git 的插件） | **Claude**：上表 **`/plugin update`** 或 **uninstall + install** |

发完 npm 且已 `git push` 后，建议 **终端 3.1 + 3.2** 与 **Claude 本节** 都做一遍，避免只升一半。

### 3.4 只跟 Git 开发、不发 npm 时

**终端**（克隆目录内）：

```bash
git pull
npm install
paperfit install-global --target claude
```

**Claude**：仍须执行 **3.3** 里的 **`/plugin update paperfit@paperfit-vto`**；若无该命令，则用 **`/plugin uninstall`** + **`/plugin install`**，否则编辑器里仍是旧版插件资源。

---

## 四、推荐一条龙（发 npm 的 release 日）

**终端：**

```bash
git pull origin main
# … 改代码 …
git add -A && git commit -m "feat: …"
npm version patch -m "chore: release %s"
git push origin main
git push origin --tags
npm publish --access public --otp=<实时6位>   # 或已配置 token
paperfit upgrade --target claude
# 或在克隆目录: npm run upgrade
```

**Claude（同一台机器、要更新插件时，在会话里再执行）：**

```text
/plugin update paperfit@paperfit-vto
```

若无 `update` 或失败：

```text
/plugin uninstall paperfit@paperfit-vto
```

```text
/plugin install paperfit@paperfit-vto
```

---

## 五、版本与文档同步

- 发 npm 前确认 **`package.json` 的 `version`** 与 **CHANGELOG / README** 中对外版本描述一致（若有）。  
- **GitHub 默认分支**上的 `README.md` 与 **npm 包内 `files`** 包含的 README 应同源，避免用户看到两套说明。

---

*维护者可按团队规范增删「CI 发版」「GitHub Actions」等段落；本文仅覆盖本地手工流程。若某条 `/plugin` 子命令在你当前 Claude Code 版本中不存在，以官方命令补全或插件面板按钮为准。*
