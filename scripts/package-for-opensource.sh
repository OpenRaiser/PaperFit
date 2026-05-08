#!/usr/bin/env bash
#
# PaperFit 开源打包脚本
#
# 功能：
# 1. 清理所有本地数据、编译产物和个人配置
# 2. 保留核心项目文件
# 3. 生成干净的发布版本
#
# 用法：
#   ./scripts/package-for-opensource.sh [目标目录]
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
TARGET_DIR="${1:-$PROJECT_ROOT/dist}"

echo "============================================================"
echo "PaperFit 开源打包脚本"
echo "============================================================"
echo ""
echo "源目录：$PROJECT_ROOT"
echo "目标目录：$TARGET_DIR"
echo ""

# 创建目标目录
mkdir -p "$TARGET_DIR"

# 定义需要复制的核心目录和文件
CORE_DIRS=(
    "agents"
    "bin"
    "config"
    "docs"
    "scripts"
    "skills"
)

CORE_FILES=(
    "CLAUDE.md"
    "README.md"
    "package.json"
    "package-lock.json"
    "requirements.txt"
    ".gitignore"
)

# 定义需要排除的模式
EXCLUDE_PATTERNS=(
    "*.aux"
    "*.log"
    "*.out"
    "*.bbl"
    "*.blg"
    "*.fls"
    "*.fdb_latexmk"
    "*.pdf"
    "*.png"
    "*.jpg"
    "*.DS_Store"
    "__pycache__"
    "*.pyc"
    "*.pyo"
    ".DS_Store"
)

echo "正在复制核心文件..."

# 复制核心目录
for dir in "${CORE_DIRS[@]}"; do
    if [ -d "$PROJECT_ROOT/$dir" ]; then
        echo "  → 复制 $dir/"
        cp -r "$PROJECT_ROOT/$dir" "$TARGET_DIR/"
    fi
done

# 复制核心文件
for file in "${CORE_FILES[@]}"; do
    if [ -f "$PROJECT_ROOT/$file" ]; then
        echo "  → 复制 $file"
        cp "$PROJECT_ROOT/$file" "$TARGET_DIR/"
    fi
done

# 复制 .claude 目录（排除敏感配置）
echo "  → 复制 .claude/ (排除敏感配置)..."
mkdir -p "$TARGET_DIR/.claude"
if [ -d "$PROJECT_ROOT/.claude/commands" ]; then
    cp -r "$PROJECT_ROOT/.claude/commands" "$TARGET_DIR/.claude/"
fi
# 不复制 settings.json 和 settings.local.json（包含个人配置）

# 复制 data 目录的结构（不包含实际数据）
echo "  → 创建 data/ 目录结构..."
mkdir -p "$TARGET_DIR/data"
mkdir -p "$TARGET_DIR/data/benchmarks/samples"

# 如果有 sample 文件，可以选择性复制
# if [ -d "$PROJECT_ROOT/data/benchmarks/samples" ]; then
#     cp -r "$PROJECT_ROOT/data/benchmarks/samples" "$TARGET_DIR/data/benchmarks/"
# fi

# 清理编译产物
echo ""
echo "正在清理编译产物..."

find "$TARGET_DIR" -type f -name "*.aux" -delete
find "$TARGET_DIR" -type f -name "*.log" -delete
find "$TARGET_DIR" -type f -name "*.out" -delete
find "$TARGET_DIR" -type f -name "*.bbl" -delete
find "$TARGET_DIR" -type f -name "*.blg" -delete
find "$TARGET_DIR" -type f -name "*.fls" -delete
find "$TARGET_DIR" -type f -name "*.fdb_latexmk" -delete
find "$TARGET_DIR" -type f -name "*.pdf" -delete
find "$TARGET_DIR" -type f -name "*.png" -delete
find "$TARGET_DIR" -type f -name "*.jpg" -delete
find "$TARGET_DIR" -type f -name ".DS_Store" -delete
find "$TARGET_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$TARGET_DIR" -type f -name "*.pyc" -delete
find "$TARGET_DIR" -type f -name "*.pyo" -delete

echo ""
echo "============================================================"
echo "打包完成！"
echo "============================================================"
echo ""
echo "发布的文件位于：$TARGET_DIR"
echo ""
echo "下一步操作："
echo "  1. cd $TARGET_DIR"
echo "  2. git init"
echo "  3. git add -A"
echo "  4. git commit -m 'Initial commit: PaperFit VTO System'"
echo "  5. git remote add origin <your-repo-url>"
echo "  6. git push -u origin main"
echo ""
