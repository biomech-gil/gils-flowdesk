#!/bin/bash
# ============================================
# tmux 웹 컨트롤러 - 자동 설치 스크립트 (WSL Ubuntu용)
# ============================================
# 이 스크립트는 WSL Ubuntu 안에서 실행합니다.
# 새 PC에서 처음 설정할 때 1회만 실행하면 됩니다.
#
# 사용법:
#   bash setup.sh            # 일반 설치 (대화형)
#   bash setup.sh --auto     # 자동 설치 (확인 없이)
# ============================================

set -e

AUTO_MODE=false
[[ "$1" == "--auto" ]] && AUTO_MODE=true

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[x]${NC} $1"; }

INSTALL_DIR="$HOME/tmux-controller"
SCRIPT_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd || echo "$INSTALL_DIR")"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║  tmux 웹 컨트롤러 - 설치 스크립트    ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── 1. 시스템 패키지 ──
info "시스템 패키지 확인 중..."

NEED_INSTALL=""
command -v tmux   &>/dev/null || NEED_INSTALL="$NEED_INSTALL tmux"
command -v python3 &>/dev/null || NEED_INSTALL="$NEED_INSTALL python3"
command -v curl   &>/dev/null || NEED_INSTALL="$NEED_INSTALL curl"
command -v lsof   &>/dev/null || NEED_INSTALL="$NEED_INSTALL lsof"

if [ -n "$NEED_INSTALL" ]; then
    info "설치 필요:$NEED_INSTALL"
    sudo apt update && sudo apt install -y $NEED_INSTALL
    info "시스템 패키지 설치 완료"
else
    info "tmux, python3, curl, lsof - 모두 설치됨"
fi

# ── 2. nvm + Node.js ──
info "Node.js 확인 중..."

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

if ! command -v node &>/dev/null; then
    if [ ! -s "$NVM_DIR/nvm.sh" ]; then
        info "nvm 설치 중..."
        curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
        export NVM_DIR="$HOME/.nvm"
        . "$NVM_DIR/nvm.sh"
    fi
    info "Node.js 22 LTS 설치 중... (1-2분 소요)"
    nvm install 22
    info "Node.js $(node -v) 설치 완료"
else
    info "Node.js $(node -v) 이미 설치됨"
fi

# ── 3. Claude Code CLI ──
info "Claude Code CLI 확인 중..."

if ! command -v claude &>/dev/null; then
    info "Claude Code CLI 설치 중..."
    npm install -g @anthropic-ai/claude-code
    info "Claude Code $(claude --version 2>/dev/null || echo '설치됨') 설치 완료"
else
    info "Claude Code $(claude --version 2>/dev/null) 이미 설치됨"
fi

# ── 4. 프로젝트 파일 복사 ──
info "프로젝트 파일 설치 중..."

mkdir -p "$INSTALL_DIR"

# 스크립트가 실행된 위치에서 파일 복사 (git clone 디렉토리)
if [ -f "$SCRIPT_DIR/server.py" ]; then
    cp "$SCRIPT_DIR/server.py"  "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/index.html" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/start.sh"   "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/tmux.conf"  "$INSTALL_DIR/"
    info "파일 복사 완료: $SCRIPT_DIR → $INSTALL_DIR"
else
    # 이미 INSTALL_DIR에 파일이 있는 경우 (직접 복사된 경우)
    if [ -f "$INSTALL_DIR/server.py" ]; then
        info "파일이 이미 $INSTALL_DIR 에 있음"
    else
        error "server.py를 찾을 수 없습니다."
        error "이 스크립트를 프로젝트 디렉토리에서 실행하거나,"
        error "먼저 파일을 $INSTALL_DIR 에 복사하세요."
        exit 1
    fi
fi

# 줄바꿈 변환 (Windows → Unix)
sed -i 's/\r$//' "$INSTALL_DIR"/*.py "$INSTALL_DIR"/*.sh "$INSTALL_DIR"/*.html 2>/dev/null
chmod +x "$INSTALL_DIR/start.sh"

# ── 5. tmux.conf 심볼릭 링크 ──
if [ -f "$INSTALL_DIR/tmux.conf" ]; then
    ln -sf "$INSTALL_DIR/tmux.conf" "$HOME/.tmux.conf"
    info "~/.tmux.conf → $INSTALL_DIR/tmux.conf 링크 생성"
fi

# ── 6. 완료 ──
echo ""
echo "╔══════════════════════════════════════╗"
echo "║  설치 완료!                          ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "  설치 위치: $INSTALL_DIR"
echo "  Node.js:   $(node -v 2>/dev/null || echo 'N/A')"
echo "  npm:       $(npm -v 2>/dev/null || echo 'N/A')"
echo "  tmux:      $(tmux -V 2>/dev/null || echo 'N/A')"
echo "  Claude:    $(claude --version 2>/dev/null || echo 'N/A')"
echo ""
echo "  다음 단계:"
echo "  1. Windows 바탕화면에 tmux_컨트롤러.bat 생성 (README 참고)"
echo "  2. bat 파일 더블클릭으로 실행"
echo "  3. Claude 최초 실행 시 인증 필요 (claude 명령 1회 실행)"
echo ""
