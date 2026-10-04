### `commands/paperfit-undo.md`

# /paperfit-undo — 回滚最近一次自动写回

## 命令描述

从 `data/backups/` 中恢复最近一次自动写回前的备份文件。默认优先回滚主 `.tex`；若存在 `state_*.json` 备份，可一并恢复 `data/state.json`。

## 触发词

/paperfit-undo

## 行为

1. 读取 `data/state.json`，确定 `main_tex`。
2. 查找 `data/backups/<main_tex文件名>.*.bak` 的最新备份。
3. 如存在 `data/backups/state_*.json`，可一并恢复最新状态备份。
4. 恢复后刷新画像，必要时建议执行 `/show-status` 或 `/check-visual`。

## 调度映射

- 不调用 Agent
- 基于 `data/backups/` 直接恢复文件
