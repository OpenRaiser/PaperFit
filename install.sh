#!/usr/bin/env bash
# 一键安装 / 一键更新：全局 npm 包 + 同步目标宿主目录（paperfit install-global --target …）
# 用法:
#   ./install.sh                               # 首次：registry 安装 paperfit-cli + 同步 claude
#   ./install.sh --target codex               # 首次：同步 codex
#   ./install.sh --local --target cursor      # 首次：从本克隆路径 npm install -g + 同步 cursor
#   ./install.sh --update --target all        # 一键更新：paperfit-cli@latest + 同步全部宿主（需已有 npm/node）
#   ./install.sh --update --local --target all # 一键更新：从本克隆 npm install -g + 同步全部宿主（开发）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UPDATE=false
LOCAL_SRC=false
TARGET=claude
PROJECT_ROOT=""

for arg in "$@"; do
  case "$arg" in
    --update|--upgrade) UPDATE=true ;;
    --local) LOCAL_SRC=true ;;
    --target=*) TARGET="${arg#*=}" ;;
    --project=*) PROJECT_ROOT="${arg#*=}" ;;
  esac
done

for ((i=1; i<=$#; i++)); do
  if [[ "${!i}" == "--target" ]]; then
    next=$((i+1))
    if [[ $next -le $# ]]; then
      TARGET="${!next}"
    fi
  fi
  if [[ "${!i}" == "--project" ]]; then
    next=$((i+1))
    if [[ $next -le $# ]]; then
      PROJECT_ROOT="${!next}"
    fi
  fi
done

if ! command -v node >/dev/null 2>&1; then
  echo "Error: Node.js 18+ is required (https://nodejs.org)"
  exit 1
fi

sync_target_home() {
  echo ""
  echo "Syncing PaperFit assets into target home (${TARGET}) ..."
  INSTALL_ARGS=(--target "$TARGET")
  if [[ -n "$PROJECT_ROOT" ]]; then
    INSTALL_ARGS+=(--project "$PROJECT_ROOT")
  fi
  if command -v paperfit >/dev/null 2>&1; then
    paperfit install-global "${INSTALL_ARGS[@]}"
  elif command -v paperfit-install >/dev/null 2>&1; then
    paperfit-install "${INSTALL_ARGS[@]}"
  else
    echo "Error: 全局安装后仍找不到 paperfit / paperfit-install，请检查 npm 全局 bin 是否在 PATH（例如 export PATH=\"\$(npm bin -g):\$PATH\"）"
    exit 1
  fi
}

if [[ "$UPDATE" == true ]]; then
  echo "PaperFit — 一键更新部署"
  echo "======================="
  if [[ "$LOCAL_SRC" == true ]]; then
    echo "npm: 从本地克隆安装/覆盖全局包 → $ROOT"
    npm install -g "$ROOT"
  else
    echo "npm: paperfit-cli@latest（registry）"
    npm install -g paperfit-cli@latest
  fi
  sync_target_home
else
  echo "PaperFit installer"
  echo "=================="
  if [[ "$LOCAL_SRC" == true ]]; then
    echo "Installing from local clone: $ROOT"
    npm install -g "$ROOT"
  else
    echo "Installing from npm registry (paperfit-cli@latest)..."
    npm install -g paperfit-cli@latest
  fi
  sync_target_home
fi

echo ""
echo "Done."
echo "  pip3 install -r \"$(npm root -g)/paperfit-cli/requirements.txt\""
echo "  brew install poppler   # PDF 页图"
echo ""
if [[ "$TARGET" == "claude" || "$TARGET" == "all" ]]; then
  echo "Claude Code 插件（在会话里执行，与 npm 版本独立，需单独更新）："
  echo "  /plugin marketplace update paperfit-vto"
  echo "  /plugin update paperfit@paperfit-vto"
  echo ""
fi
echo "若本机尚无 paperfit 命令，请用本脚本一键安装/更新（勿只装插件）："
echo "  curl -fsSL https://raw.githubusercontent.com/OpenRaiser/PaperFit/main/install.sh | bash -s -- --update"
echo "  或在克隆目录: bash install.sh --update --local"
