#!/bin/bash
# PaperFit Installation Script
# Usage: ./install.sh [components...]
# Example: ./install.sh rules hooks agents

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo -e "${BLUE}🚀 PaperFit Installation${NC}"
echo "========================"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python 3 not found. Please install Python 3.8+${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Python 3: $(python3 --version)${NC}"

# Check pip
if ! command -v pip3 &> /dev/null; then
    echo -e "${YELLOW}⚠️  pip3 not found. Attempting to install Python dependencies may fail.${NC}"
fi

# Install Python dependencies
echo ""
echo -e "${BLUE}📦 Installing Python dependencies...${NC}"
if [ -f "$PROJECT_ROOT/requirements.txt" ]; then
    pip3 install -r "$PROJECT_ROOT/requirements.txt"
    echo -e "${GREEN}✅ Python dependencies installed${NC}"
else
    echo -e "${YELLOW}⚠️  requirements.txt not found. Skipping Python dependencies.${NC}"
fi

# Check system dependencies
echo ""
echo -e "${BLUE}🔍 Checking system dependencies...${NC}"

# Check poppler
if command -v pdfinfo &> /dev/null; then
    echo -e "${GREEN}✅ Poppler utilities${NC}"
else
    echo -e "${YELLOW}⚠️  Poppler not found. Install with: brew install poppler${NC}"
fi

# Check latexmk
if command -v latexmk &> /dev/null; then
    echo -e "${GREEN}✅ latexmk${NC}"
else
    echo -e "${YELLOW}⚠️  latexmk not found. Install MacTeX or TeX Live${NC}"
fi

# Install rules (optional)
echo ""
read -p "Install rules to ~/.claude/rules? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    mkdir -p "$HOME/.claude/rules"
    cp -r "$PROJECT_ROOT/rules" "$HOME/.claude/rules/" 2>/dev/null || {
        cp -r "$SCRIPT_DIR/../rules" "$HOME/.claude/rules/" 2>/dev/null || {
            echo -e "${YELLOW}⚠️  rules directory not found. Skipping.${NC}"
        }
    }
    echo -e "${GREEN}✅ Rules installed to ~/.claude/rules${NC}"
fi

# Install hooks (optional)
echo ""
echo -e "${BLUE}🪝  Configuring hooks...${NC}"
if [ -f "$PROJECT_ROOT/.claude/settings.json" ]; then
    echo -e "${GREEN}✅ Claude Code settings found${NC}"
else
    echo -e "${YELLOW}⚠️  .claude/settings.json not found. Hooks will not be configured automatically.${NC}"
fi

# Create symlinks for global access
echo ""
read -p "Create global 'paperfit' command? (requires sudo) (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo ln -sf "$SCRIPT_DIR/paperfit" /usr/local/bin/paperfit 2>/dev/null || {
        ln -sf "$SCRIPT_DIR/paperfit" ~/bin/paperfit 2>/dev/null || {
            echo -e "${YELLOW}⚠️  Could not create symlink. Add $SCRIPT_DIR to PATH manually.${NC}"
        }
    }
    echo -e "${GREEN}✅ Global command installed${NC}"
fi

echo ""
echo -e "${GREEN}✅ Installation complete!${NC}"
echo ""
echo "Next steps:"
echo "  1. Run: paperfit doctor"
echo "  2. Run: paperfit init"
echo "  3. Open your LaTeX project in Claude Code"
echo ""
