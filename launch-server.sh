#!/bin/bash
# Claude Flow Canvas — 서버 백그라운드 실행
cd ~/claude-flow-canvas

# 기존 서버 정리
tmux kill-session -t server-bg 2>/dev/null

# server-bg 세션에서 서버 실행
tmux new-session -d -s server-bg -c ~/claude-flow-canvas
tmux send-keys -t server-bg 'python3 server.py' Enter

echo "[+] Claude Flow Canvas server started (http://127.0.0.1:8888)"
