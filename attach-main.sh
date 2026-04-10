#!/bin/bash
# main 세션이 없으면 생성, 있으면 attach
tmux has-session -t main 2>/dev/null || tmux new-session -d -s main
exec tmux attach -t main
