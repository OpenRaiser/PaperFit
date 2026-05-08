#!/bin/bash
#
# PaperFit LaTeX 编译脚本
#
# 封装 LaTeX 编译流程，支持 latexmk 和 pdflatex 两种方式，
# 自动处理参考文献、交叉引用的多次编译，并输出结构化日志。
#
# 用法:
#   ./compile.sh <main_tex> [--clean] [--engine pdflatex|xelatex|lualatex]
#
# 示例:
#   ./compile.sh main.tex
#   ./compile.sh main.tex --clean
#   ./compile.sh main.tex --engine xelatex

set -e  # 遇到错误立即退出

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 默认参数
MAIN_TEX=""
CLEAN_MODE=false
ENGINE="pdflatex"
USE_LATEXMK=true
BIBER=false

# 帮助信息
usage() {
    cat << EOF
用法: $0 <main_tex> [选项]

选项:
  --clean           清理临时文件后重新编译
  --engine ENGINE   指定编译引擎 (pdflatex, xelatex, lualatex) [默认: pdflatex]
  --no-latexmk      禁用 latexmk，使用手动多次编译
  --biber           使用 biber 处理参考文献（默认使用 bibtex）
  -h, --help        显示此帮助信息

示例:
  $0 main.tex
  $0 main.tex --clean --engine xelatex
  $0 main.tex --no-latexmk --biber
EOF
    exit 0
}

# 解析参数
while [[ $# -gt 0 ]]; do
    case $1 in
        --clean)
            CLEAN_MODE=true
            shift
            ;;
        --engine)
            ENGINE="$2"
            shift 2
            ;;
        --no-latexmk)
            USE_LATEXMK=false
            shift
            ;;
        --biber)
            BIBER=true
            shift
            ;;
        -h|--help)
            usage
            ;;
        *.tex)
            MAIN_TEX="$1"
            shift
            ;;
        *)
            echo -e "${RED}未知参数: $1${NC}"
            usage
            ;;
    esac
done

# 检查主文件是否提供
if [[ -z "$MAIN_TEX" ]]; then
    echo -e "${RED}错误: 未指定主 .tex 文件${NC}"
    usage
fi

# 检查主文件是否存在
if [[ ! -f "$MAIN_TEX" ]]; then
    echo -e "${RED}错误: 文件不存在: $MAIN_TEX${NC}"
    exit 1
fi

# 获取文件名（不含扩展名）
BASENAME=$(basename "$MAIN_TEX" .tex)
DIRNAME=$(dirname "$MAIN_TEX")
if [[ "$DIRNAME" == "." ]]; then
    DIRNAME=""
fi
MAIN_NAME="${DIRNAME:+$DIRNAME/}$BASENAME"

# 进入主文件所在目录
cd "$(dirname "$MAIN_TEX")" || exit 1
MAIN_TEX_FILE=$(basename "$MAIN_TEX")

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}PaperFit LaTeX 编译${NC}"
echo -e "${GREEN}========================================${NC}"
echo "主文件: $MAIN_TEX"
echo "编译引擎: $ENGINE"
echo "使用 latexmk: $USE_LATEXMK"
echo "参考文献工具: $([ "$BIBER" = true ] && echo "biber" || echo "bibtex")"
echo ""

# 清理模式
if [[ "$CLEAN_MODE" == true ]]; then
    echo -e "${YELLOW}清理临时文件...${NC}"
    if [[ "$USE_LATEXMK" == true ]]; then
        latexmk -C "$MAIN_TEX_FILE"
    else
        rm -f "$BASENAME".{aux,log,out,toc,lof,lot,blg,bbl,bcf,run.xml,fls,fdb_latexmk,synctex.gz,nav,snm,vrb}
    fi
    echo -e "${GREEN}清理完成${NC}\n"
fi

# 编译函数
compile_with_latexmk() {
    echo -e "${YELLOW}使用 latexmk 编译...${NC}"
    
    local latexmk_opts="-pdf -interaction=nonstopmode"
    if [[ "$ENGINE" != "pdflatex" ]]; then
        latexmk_opts="$latexmk_opts -$ENGINE"
    fi
    
    if latexmk $latexmk_opts "$MAIN_TEX_FILE"; then
        echo -e "${GREEN}latexmk 编译成功${NC}"
        return 0
    else
        echo -e "${RED}latexmk 编译失败${NC}"
        return 1
    fi
}

compile_manual() {
    echo -e "${YELLOW}使用手动多次编译...${NC}"
    
    local tex_cmd="$ENGINE -interaction=nonstopmode"
    
    # 第一遍编译
    echo "第一遍 $ENGINE..."
    if ! $tex_cmd "$MAIN_TEX_FILE" > /dev/null 2>&1; then
        echo -e "${RED}第一遍编译失败，请查看 ${BASENAME}.log${NC}"
        return 1
    fi
    
    # 处理参考文献
    if grep -q "\\bibliography{" "$MAIN_TEX_FILE" || grep -q "\\bibdata{" "${BASENAME}.aux" 2>/dev/null; then
        echo "处理参考文献..."
        if [[ "$BIBER" == true ]]; then
            if biber "$BASENAME" > /dev/null 2>&1; then
                echo "biber 成功"
            else
                echo -e "${YELLOW}biber 失败，尝试 bibtex...${NC}"
                bibtex "$BASENAME" > /dev/null 2>&1 || echo -e "${YELLOW}警告: 参考文献处理可能有问题${NC}"
            fi
        else
            bibtex "$BASENAME" > /dev/null 2>&1 || echo -e "${YELLOW}警告: bibtex 失败或无需参考文献${NC}"
        fi
    fi
    
    # 第二遍编译（更新交叉引用）
    echo "第二遍 $ENGINE..."
    if ! $tex_cmd "$MAIN_TEX_FILE" > /dev/null 2>&1; then
        echo -e "${RED}第二遍编译失败${NC}"
        return 1
    fi
    
    # 检查是否需要第三次编译（交叉引用未稳定）
    if grep -q "Rerun to get" "${BASENAME}.log"; then
        echo "第三次 $ENGINE (稳定交叉引用)..."
        if ! $tex_cmd "$MAIN_TEX_FILE" > /dev/null 2>&1; then
            echo -e "${YELLOW}第三次编译有警告，但 PDF 已生成${NC}"
        fi
    fi
    
    echo -e "${GREEN}手动编译完成${NC}"
    return 0
}

# 执行编译
START_TIME=$(date +%s)

if [[ "$USE_LATEXMK" == true ]]; then
    compile_with_latexmk
    COMPILE_STATUS=$?
else
    compile_manual
    COMPILE_STATUS=$?
fi

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

# 检查 PDF 是否生成
PDF_FILE="${BASENAME}.pdf"
if [[ -f "$PDF_FILE" ]]; then
    PDF_SIZE=$(du -h "$PDF_FILE" | cut -f1)
    PDF_PAGES=$(pdfinfo "$PDF_FILE" 2>/dev/null | grep "Pages:" | awk '{print $2}' || echo "?")
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}编译结果${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo "PDF 文件: $PDF_FILE"
    echo "文件大小: $PDF_SIZE"
    echo "页数: $PDF_PAGES"
    echo "编译耗时: ${DURATION}s"
    
    # 输出日志摘要
    LOG_FILE="${BASENAME}.log"
    if [[ -f "$LOG_FILE" ]]; then
        ERROR_COUNT=$(grep -c "^!" "$LOG_FILE" 2>/dev/null || echo 0)
        WARNING_COUNT=$(grep -c "Warning:" "$LOG_FILE" 2>/dev/null || echo 0)
        OVERFULL_COUNT=$(grep -c "Overfull" "$LOG_FILE" 2>/dev/null || echo 0)
        echo "错误数: $ERROR_COUNT"
        echo "警告数: $WARNING_COUNT"
        echo "Overfull hbox: $OVERFULL_COUNT"
    fi
    echo -e "${GREEN}========================================${NC}"
else
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}编译失败: PDF 未生成${NC}"
    echo -e "${RED}========================================${NC}"
    echo "请查看日志文件: ${BASENAME}.log"
    exit 1
fi

# 根据编译状态返回退出码
if [[ $COMPILE_STATUS -eq 0 ]] && [[ -f "$PDF_FILE" ]]; then
    exit 0
else
    exit 1
fi