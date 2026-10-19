#!/usr/bin/env bash
# TG-JIQIREN Linux 构建脚本
# 使用 PyInstaller 将程序打包为单个可执行文件

set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}"
echo "  =============================================="
echo "    TG-JIQIREN 天宫 · Linux 版 · 打包脚本"
echo "  =============================================="
echo -e "${NC}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ─── 1. 检查 Python ───────────────────────────────
echo -e "${CYAN}[1/4] 检查 Python 环境...${NC}"
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}[✗] 未找到 python3，请先安装: sudo apt install python3 python3-pip${NC}"
    exit 1
fi
PY_VER=$(python3 --version)
echo -e "${GREEN}[✓] 检测到 ${PY_VER}${NC}"

# ─── 2. 安装 PyInstaller ──────────────────────────
echo -e "\n${CYAN}[2/4] 安装 PyInstaller...${NC}"
pip3 install pyinstaller --quiet --break-system-packages 2>/dev/null || \
pip3 install pyinstaller --quiet
echo -e "${GREEN}[✓] PyInstaller 就绪${NC}"

# ─── 3. 安装后端依赖 ──────────────────────────────
echo -e "\n${CYAN}[3/4] 安装后端依赖...${NC}"
if [ -f "tg_suite/requirements.txt" ]; then
    pip3 install -r tg_suite/requirements.txt --quiet --break-system-packages 2>/dev/null || \
    pip3 install -r tg_suite/requirements.txt --quiet
    echo -e "${GREEN}[✓] 后端依赖安装完成${NC}"
else
    echo -e "${YELLOW}[!] 未找到 requirements.txt，跳过${NC}"
fi

# ─── 4. PyInstaller 打包 ──────────────────────────
echo -e "\n${CYAN}[4/4] 打包可执行文件（约 1~3 分钟）...${NC}"

# 清理旧的构建产物
rm -rf dist/TG-JIQIREN-Linux build/

pyinstaller \
  --noconfirm \
  --onedir \
  --name "TG-JIQIREN-Linux" \
  --add-data "tg_suite:tg_suite" \
  --add-data "使用手册.html:." \
  --hidden-import "uvicorn" \
  --hidden-import "fastapi" \
  --hidden-import "telethon" \
  linux_start.py

# ─── 5. 整理发布目录 ──────────────────────────────
echo -e "\n${CYAN}[*] 整理发布目录...${NC}"

DIST_DIR="dist/TG-JIQIREN-Linux"

# 确保 tg_suite 在发布目录里（PyInstaller --add-data 会放进去）
# 但为了运行时找到 .env 等文件，再 copy 一份到根级
cp -rf tg_suite "$DIST_DIR/tg_suite" 2>/dev/null || true
cp 使用手册.html "$DIST_DIR/" 2>/dev/null || true

# 创建快捷启动脚本
cat > "$DIST_DIR/启动程序.sh" << 'LAUNCHER'
#!/bin/bash
cd "$(dirname "$0")"
./TG-JIQIREN-Linux
LAUNCHER
chmod +x "$DIST_DIR/启动程序.sh"
chmod +x "$DIST_DIR/TG-JIQIREN-Linux"

# ─── 完成 ─────────────────────────────────────────
echo ""
echo -e "${GREEN}  =============================================="
echo -e "    ✅ 打包成功！"
echo -e "  ==============================================${NC}"
echo ""
echo -e "  📂 程序位置: ${CYAN}${DIST_DIR}/${NC}"
echo ""
echo -e "  ▶  使用方法:"
echo -e "     双击 \`启动程序.sh\` 或在终端运行:"
echo -e "     ${CYAN}cd ${DIST_DIR} && ./TG-JIQIREN-Linux${NC}"
echo ""
