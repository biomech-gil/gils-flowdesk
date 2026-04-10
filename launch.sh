#!/bin/bash
# tmux-config-korean 서버 기동 스크립트
# - tmux 세션 main 보장
# - 포트 8888이 비어있을 때만 server.py 분리 실행
set -e
tmux has-session -t main 2>/dev/null || tmux new-session -d -s main -c ~
cd ~/tmux-config-korean
if lsof -t -i :8888 >/dev/null 2>&1; then
  echo "[i] server already running on :8888"
else
  nohup python3 server.py > /tmp/tmux-web.log 2>&1 < /dev/null &
  disown
  echo "[+] server started, log: /tmp/tmux-web.log"
fi
