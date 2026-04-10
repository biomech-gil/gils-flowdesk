#!/bin/bash
# 서버를 tmux server-bg 세션에서 실행
cd ~/tmux-config-korean

# 기존 정리
tmux kill-session -t server-bg 2>/dev/null

# server-bg 세션 생성 + 서버 실행
tmux new-session -d -s server-bg -c ~/tmux-config-korean
tmux send-keys -t server-bg 'python3 server.py' Enter

echo "[+] Server started in tmux session 'server-bg'"
