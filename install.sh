#!/bin/bash
# ===========================================================================
# Yanyu-Wit 一键安装配置脚本 (Mac / Linux)
# ===========================================================================
# 
# 本脚本提供两种模式：
# 1. 离线安装：将同目录下的 `wit` 二进制程序复制进系统的 PATH 中，并配置安全权限。
# 2. 在线安装：通过 curl 从官网下载对应平台架构的最新 `wit` 包并配置。

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=== 🚀 Yanyu-Wit 命令行助手安装程序 ===${NC}\n"

# 1. 检测操作系统与架构
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"

if [[ "$OS" == "darwin" ]]; then
    OS_NAME="macOS"
elif [[ "$OS" == "linux" ]]; then
    OS_NAME="Linux"
else
    echo -e "${RED}[ERROR] 本脚本仅支持 macOS 和 Linux 操作系统。${NC}"
    exit 1
fi

if [[ "$ARCH" == "x86_64" ]]; then
    ARCH_NAME="Intel x64"
elif [[ "$ARCH" == "arm64" || "$ARCH" == "aarch64" ]]; then
    ARCH_NAME="ARM64 (Apple Silicon / Aarch64)"
else
    echo -e "${YELLOW}[WARN] 未知 CPU 架构: $ARCH，将尝试以 x86_64 模式运行。${NC}"
    ARCH="x86_64"
fi

echo -e "${GREEN}[INFO] 检测到运行环境: ${OS_NAME} ($ARCH_NAME)${NC}"

# 2. 检查安装模式（离线还是在线）
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
LOCAL_WIT="${SCRIPT_DIR}/wit"
INSTALL_PATH="${HOME}/.yanyu-wit/bin"
TARGET_WIT="${INSTALL_PATH}/wit"

# 创建本地安装目录
mkdir -p "$INSTALL_PATH"

# 清理旧版本安装残留（早期版本安装到 /usr/local/bin）
OLD_WIT="/usr/local/bin/wit"
if [ -f "$OLD_WIT" ]; then
    echo -e "${YELLOW}[WARN] 检测到旧版本残留: ${OLD_WIT}，正在清理以避免冲突...${NC}"
    sudo rm -f "$OLD_WIT" 2>/dev/null || echo -e "${YELLOW}[WARN] 无法自动清理旧版本，请手动执行: sudo rm -f ${OLD_WIT}${NC}"
fi

if [ -f "$LOCAL_WIT" ]; then
    # ---------------- 离线安装模式 ----------------
    echo -e "${GREEN}[INFO] 检测到本地二进制文件，正在执行离线安装流程...${NC}"
    
    echo -e "👉 正在拷贝二进制到系统路径: ${TARGET_WIT}"
    cp -f "$LOCAL_WIT" "$TARGET_WIT"
    
else
    # ---------------- 在线安装模式 ----------------
    echo -e "${GREEN}[INFO] 未检测到本地文件，正在执行在线安装流程...${NC}"
    
    # 根据系统和架构确定下载 URL
    # TODO: 实际发布时，将以下地址替换为您在官网托管的下载链接
    DOWNLOAD_BASE="https://yanyu.com/downloads/releases"
    if [[ "$OS" == "darwin" ]]; then
        if [[ "$ARCH" == "arm64" ]]; then
            DOWNLOAD_URL="${DOWNLOAD_BASE}/wit-mac-arm64"
        else
            DOWNLOAD_URL="${DOWNLOAD_BASE}/wit-mac-x64"
        fi
    else
        DOWNLOAD_URL="${DOWNLOAD_BASE}/wit-linux-x64"
    fi
    
    echo -e "👉 正在从服务器拉取对应的二进制包..."
    echo -e "   下载链接: $DOWNLOAD_URL"
    
    # 临时下载到 /tmp
    TMP_WIT="/tmp/wit_download"
    curl -fsSL "$DOWNLOAD_URL" -o "$TMP_WIT"
    if [ $? -ne 0 ]; then
        echo -e "${RED}[ERROR] 从官网下载 wit 二进制包失败，请检查网络链接或官网服务状态。${NC}"
        exit 1
    fi
    
    echo -e "👉 正在拷贝二进制到系统路径: ${TARGET_WIT}"
    cp -f "$TMP_WIT" "$TARGET_WIT"
    rm -f "$TMP_WIT"
fi

# 3. 配置可执行权限与安全隔离策略 (macOS 特有)
echo -e "👉 正在配置执行权限..."
chmod +x "$TARGET_WIT"

if [[ "$OS" == "darwin" ]]; then
    echo -e "👉 正在解除 macOS 浏览器下载安全隔离标识 (Gatekeeper Bypass)..."
    # 这一步可以彻底抹掉苹果系统的安全警告拦截提示
    xattr -d com.apple.quarantine "$TARGET_WIT" 2>/dev/null || true
fi

# 4. 配置环境变量 PATH 并验证安装结果
# 检测是否已经在当前的 PATH 变量中
if [[ ":$PATH:" == *":$INSTALL_PATH:"* ]]; then
    PATH_OK=true
else
    PATH_OK=false
fi

# 检测并配置 Shell 环境变量 PATH
SHELL_NAME="$(basename "$SHELL")"
PATH_LINE="export PATH=\"\$HOME/.yanyu-wit/bin:\$PATH\""
ADDED_TO_PROFILE=false

update_profile() {
    local profile="$1"
    if [ -f "$profile" ]; then
        if ! grep -q ".yanyu-wit/bin" "$profile"; then
            echo -e "\n# Yanyu-Wit CLI PATH\n$PATH_LINE" >> "$profile"
            ADDED_TO_PROFILE=true
            echo -e "👉 已自动将安装路径加入到您的配置文件: ${profile}"
        fi
    fi
}

if [ "$PATH_OK" = false ]; then
    if [[ "$SHELL_NAME" == "zsh" ]]; then
        update_profile "${HOME}/.zshrc"
    elif [[ "$SHELL_NAME" == "bash" ]]; then
        update_profile "${HOME}/.bash_profile"
        update_profile "${HOME}/.bashrc"
    else
        # 兜底：都尝试写入
        update_profile "${HOME}/.zshrc"
        update_profile "${HOME}/.bashrc"
    fi
fi

# 检测 PATH 中是否已包含安装路径（或 profile 中已配置）
PROFILE_HAS_PATH=false
if [[ "$SHELL_NAME" == "zsh" ]] && [ -f "${HOME}/.zshrc" ] && grep -q ".yanyu-wit/bin" "${HOME}/.zshrc"; then
    PROFILE_HAS_PATH=true
elif [ -f "${HOME}/.bash_profile" ] && grep -q ".yanyu-wit/bin" "${HOME}/.bash_profile"; then
    PROFILE_HAS_PATH=true
elif [ -f "${HOME}/.bashrc" ] && grep -q ".yanyu-wit/bin" "${HOME}/.bashrc"; then
    PROFILE_HAS_PATH=true
fi

if [[ ":$PATH:" == *":$INSTALL_PATH:"* ]] || command -v wit &> /dev/null; then
    echo -e "\n${GREEN}==================================================${NC}"
    echo -e "${GREEN}🎉 Yanyu-Wit 命令行助手安装成功！${NC}"
    echo -e "=================================================="
    echo -e "您现在可以在终端的任意目录下直接运行以下命令初始化或使用："
    echo -e "  ${BLUE}wit init${NC}        - 初始化登录与模型配置"
    echo -e "  ${BLUE}wit start${NC}       - 启动本地前端 UI 界面"
    echo -e "  ${BLUE}wit --help${NC}      - 查看详细指令说明"
    echo -e "${GREEN}==================================================${NC}\n"
else
    echo -e "\n${GREEN}==================================================${NC}"
    echo -e "${GREEN}🎉 Yanyu-Wit 命令行助手安装成功！${NC}"
    echo -e "=================================================="
    if [ "$ADDED_TO_PROFILE" = true ] || [ "$PROFILE_HAS_PATH" = true ]; then
        echo -e "💡 PATH 配置已写入您的 Shell 配置文件，请运行以下命令使当前终端立即生效（或重新打开一个终端窗口）："
        if [[ "$SHELL_NAME" == "zsh" ]]; then
            echo -e "   ${BLUE}source ~/.zshrc${NC}"
        else
            echo -e "   ${BLUE}source ~/.bashrc${NC} 或 ${BLUE}source ~/.bash_profile${NC}"
        fi
    else
        echo -e "💡 请将以下内容手动添加到您的 Shell 配置文件（如 ~/.zshrc 或 ~/.bashrc）中："
        echo -e "   ${BLUE}export PATH=\"\$HOME/.yanyu-wit/bin:\$PATH\"${NC}"
        echo -e "然后运行 ${BLUE}source <配置文件>${NC} 使之生效。"
    fi
    echo -e "${GREEN}==================================================${NC}\n"
fi
