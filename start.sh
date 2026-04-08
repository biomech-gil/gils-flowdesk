#!/bin/bash
# ==========================================
# tmux 웹 컨트롤러 시작 스크립트 (WSL용)
# ==========================================

SESSION="main"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT=8888

echo "╔══════════════════════════════════════╗"
echo "║  tmux 웹 컨트롤러 시작 중...         ║"
echo "╚══════════════════════════════════════╝"

# tmux 설치 확인
if ! command -v tmux &> /dev/null; then
    echo "[!] tmux가 설치되어 있지 않습니다. 설치 중..."
    sudo apt update && sudo apt install -y tmux
fi

# Python3 확인
if ! command -v python3 &> /dev/null; then
    echo "[!] python3가 설치되어 있지 않습니다. 설치 중..."
    sudo apt update && sudo apt install -y python3
fi

# tmux.conf 심볼릭 링크 (없으면 생성)
if [ -f "$PROJECT_DIR/tmux.conf" ] && [ ! -f "$HOME/.tmux.conf" ]; then
    ln -s "$PROJECT_DIR/tmux.conf" "$HOME/.tmux.conf"
    echo "[+] tmux.conf 링크 생성: $HOME/.tmux.conf"
elif [ -f "$PROJECT_DIR/tmux.conf" ]; then
    echo "[i] ~/.tmux.conf 이미 존재 (기존 설정 유지)"
fi

# 리셋 모드: --reset 인자 시 기존 세션 완전 삭제
if [ "$1" = "--reset" ]; then
    echo "[!] 리셋 모드: 기존 세션 삭제..."
    tmux kill-server 2>/dev/null
    sleep 1
fi

# 기존 세션 확인
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "[i] 기존 tmux 세션 '$SESSION' 발견 - 재사용"
else
    echo "[+] tmux 세션 '$SESSION' 생성 중..."
    tmux new-session -d -s "$SESSION" -c "$HOME"
fi

# 기존 서버 종료
if lsof -i :$PORT &>/dev/null; then
    echo "[i] 포트 $PORT 사용 중인 프로세스 종료..."
    kill $(lsof -t -i :$PORT) 2>/dev/null
    sleep 1
fi

# 웹 서버 시작 (백그라운드)
echo "[+] 웹 서버 시작 (포트 $PORT)..."
cd "$PROJECT_DIR"
python3 server.py &
SERVER_PID=$!

echo ""
echo "══════════════════════════════════════"
echo "  tmux 세션: $SESSION"
echo "  웹 컨트롤러: http://localhost:$PORT"
echo "  서버 PID: $SERVER_PID"
echo "══════════════════════════════════════"
echo ""
echo "브라우저에서 http://localhost:$PORT 을 열어주세요"
echo "종료: Ctrl+C"
echo ""

# 서버 프로세스 대기 (Ctrl+C로 종료)
wait $SERVER_PID
