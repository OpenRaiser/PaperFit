# /paperfit-undo — 回滚最近一次源码写入

**作用**: 恢复最近一次 PaperFit 自动写回前的备份版本，优先回滚主 `.tex`，必要时一并恢复 `data/state.json`。

**用户入口说明**:
- 这是回滚入口，用户只需要表达“撤销上次自动修改”之类的意图。
- 不要要求用户手动定位备份文件再执行恢复命令。
- 回滚后的输出应解释恢复了什么、当前状态如何，而不是只给文件路径。

**防呆约束**:
- 不要使用 `git reset --hard`、`git checkout --` 之类的破坏性命令
- 不要调用内部任务管理工具来“创建回滚任务”
- 若 `data/backups/` 中没有匹配备份，应明确说明无法自动回滚，而不是猜测性覆盖文件

## 工具调用约定

在论文项目根目录内：

- 主源码备份通常位于 `data/backups/<main_tex文件名>.*.bak`
- 状态备份通常位于 `data/backups/state_*.json`
- 回滚后可执行 `paperfit run scripts/paperfit_portrait.py refresh --main <main_tex>` 让画像重新对齐

## 用法

```bash
/paperfit-undo
```

也可以由自然语言触发，例如：

```text
撤销 PaperFit 上一次自动修改
回滚到上一个安全版本
```

## 执行流程

1. 读取 `data/state.json`，确定 `main_tex`。
2. 检查 `data/backups/` 中最新的 `main_tex` 备份文件，以及最近一个 `state_*.json`。
3. 先向用户说明将恢复哪两个文件：
   - `main_tex <- latest .bak`
   - `data/state.json <- latest state backup`（若存在）
4. 执行文件恢复。
5. 若画像文件存在，执行：

```bash
paperfit run scripts/paperfit_portrait.py refresh --main <main_tex>
```

6. 提示用户继续用：
   - `/show-status`
   - `/check-visual`
   - `/fix-layout`

## 输出结果

- 恢复的备份文件路径
- 当前主文件路径
- 是否同时恢复了 `data/state.json`
- 是否已刷新画像

## 调度

- 不调用 Agent
- 直接基于 `data/backups/` 与 `data/state.json` 执行恢复
