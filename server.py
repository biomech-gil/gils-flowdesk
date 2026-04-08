#!/usr/bin/env python3
"""tmux 웹 컨트롤러 서버 - HTML에서 tmux를 제어하는 API"""

import subprocess
import json
import os
import re
import uuid
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

PORT = 8888
SESSION_NAME = "main"
WORKFLOW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workflows")


import sys
import time

def log(msg):
    """서버 로그 출력"""
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def run_tmux(*args):
    """tmux 명령 실행 후 결과 반환"""
    cmd = ["tmux"] + list(args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        ok = result.returncode == 0
        r = {"ok": ok, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
        if not ok:
            log(f"  TMUX FAIL: {' '.join(args[:3])} → rc={result.returncode} stderr={result.stderr.strip()[:100]}")
        return r
    except Exception as e:
        log(f"  TMUX ERROR: {' '.join(args[:3])} → {e}")
        return {"ok": False, "error": str(e)}


def get_session_info():
    """현재 tmux 세션/윈도우/패널 정보 조회"""
    info = {}
    # 세션 목록
    r = run_tmux("list-sessions", "-F", "#{session_name}:#{session_windows}:#{session_attached}")
    if r["ok"] and r["stdout"]:
        sessions = []
        for line in r["stdout"].split("\n"):
            parts = line.split(":")
            if len(parts) >= 3:
                sessions.append({
                    "name": parts[0],
                    "windows": int(parts[1]),
                    "attached": parts[2] == "1"
                })
        info["sessions"] = sessions

    # 현재 세션의 패널 정보
    r = run_tmux("list-panes", "-t", SESSION_NAME, "-F",
                 "#{pane_index}:#{pane_width}x#{pane_height}:#{pane_current_command}:#{pane_current_path}")
    if r["ok"] and r["stdout"]:
        panes = []
        for line in r["stdout"].split("\n"):
            parts = line.split(":", 3)
            if len(parts) >= 4:
                panes.append({
                    "index": int(parts[0]),
                    "size": parts[1],
                    "command": parts[2],
                    "path": parts[3]
                })
        info["panes"] = panes

    # 윈도우 정보
    r = run_tmux("list-windows", "-t", SESSION_NAME, "-F",
                 "#{window_index}:#{window_name}:#{window_panes}:#{window_active}")
    if r["ok"] and r["stdout"]:
        windows = []
        for line in r["stdout"].split("\n"):
            parts = line.split(":")
            if len(parts) >= 4:
                windows.append({
                    "index": int(parts[0]),
                    "name": parts[1],
                    "panes": int(parts[2]),
                    "active": parts[3] == "1"
                })
        info["windows"] = windows

    return info


class TmuxHandler(SimpleHTTPRequestHandler):
    """HTTP 요청 핸들러 - 정적 파일 + tmux API"""

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/api/status":
            self._json_response(get_session_info())
        elif parsed.path == "/api/pane-content":
            self._json_response(self._handle_pane_content(params))
        elif parsed.path == "/api/pane-prompt-check":
            self._json_response(self._handle_pane_prompt_check(params))
        elif parsed.path == "/api/pane-line-count":
            self._json_response(self._handle_pane_line_count(params))
        elif parsed.path == "/api/node-check":
            self._json_response(self._handle_node_check(params))
        elif parsed.path == "/api/chat-history":
            self._json_response(self._handle_chat_history(params))
        elif parsed.path == "/api/chat-check":
            self._json_response(self._handle_chat_check(params))
        elif parsed.path == "/api/workflow/list":
            self._json_response(self._handle_workflow_list())
        elif parsed.path == "/api/workflow/load":
            self._json_response(self._handle_workflow_load(params))
        elif parsed.path == "/":
            self.path = "/canvas.html"
            return SimpleHTTPRequestHandler.do_GET(self)
        else:
            return SimpleHTTPRequestHandler.do_GET(self)

    def do_POST(self):
        parsed = urlparse(self.path)
        content_len = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_len)) if content_len > 0 else {}

        handlers = {
            "/api/split": self._handle_split,
            "/api/send-command": self._handle_send_command,
            "/api/layout": self._handle_layout,
            "/api/kill-pane": self._handle_kill_pane,
            "/api/new-window": self._handle_new_window,
            "/api/select-window": self._handle_select_window,
            "/api/rename-window": self._handle_rename_window,
            "/api/reset-session": self._handle_reset_session,
            "/api/add-pane": self._handle_add_pane,
            "/api/node-exec": self._handle_node_exec,
            "/api/chat": self._handle_chat,
            "/api/preset": self._handle_preset,
            "/api/workflow/save": self._handle_workflow_save,
            "/api/workflow/delete": self._handle_workflow_delete,
        }

        handler = handlers.get(parsed.path)
        if handler:
            self._json_response(handler(body))
        else:
            self._json_response({"ok": False, "error": "unknown endpoint"}, 404)

    def _handle_split(self, body):
        """패널 분할 - direction: h(수평) 또는 v(수직)"""
        direction = body.get("direction", "h")
        target = body.get("target", SESSION_NAME)
        flag = "-h" if direction == "h" else "-v"
        return run_tmux("split-window", flag, "-t", target)

    def _handle_add_pane(self, body):
        """패널 하나 추가 (tiled 레이아웃 유지)"""
        r = run_tmux("split-window", "-t", SESSION_NAME)
        if r["ok"]:
            run_tmux("select-layout", "-t", SESSION_NAME, "tiled")
            # 새 패널 번호 반환
            r2 = run_tmux("list-panes", "-t", SESSION_NAME, "-F", "#{pane_index}")
            if r2["ok"]:
                indices = [int(x) for x in r2["stdout"].strip().split("\n")]
                log(f"ADD_PANE: new pane count={len(indices)}, latest={max(indices)}")
                return {"ok": True, "paneIndex": max(indices), "total": len(indices)}
        return r

    def _handle_reset_session(self, body):
        """세션 완전 리셋: kill → 새로 생성"""
        log("RESET SESSION: killing tmux server...")
        run_tmux("kill-server")
        import time; time.sleep(1)
        log("RESET SESSION: creating new session...")
        r = run_tmux("new-session", "-d", "-s", SESSION_NAME, "-c", os.path.expanduser("~"))
        log(f"RESET SESSION: new-session ok={r['ok']}")
        return r

    def _send_to_pane(self, target, command):
        """패널에 텍스트 전송 (send-keys -l 리터럴 모드)"""
        cmd_len = len(command)
        log(f"SEND target={target} len={cmd_len} preview={command[:80]!r}")

        # send-keys -l: 리터럴 모드로 텍스트 전송 (bracketed paste 회피)
        # 긴 텍스트는 청크로 분할 (tmux 버퍼 한계 방지)
        CHUNK = 500
        if cmd_len > CHUNK:
            log(f"  → 청크 전송 ({cmd_len} bytes, {(cmd_len-1)//CHUNK+1} chunks)")
            for i in range(0, cmd_len, CHUNK):
                chunk = command[i:i+CHUNK]
                r = run_tmux("send-keys", "-l", "-t", target, chunk)
                if not r["ok"]:
                    log(f"  chunk {i//CHUNK} FAIL: {r}")
                    return r
        else:
            log(f"  → send-keys -l ({cmd_len} bytes)")
            r = run_tmux("send-keys", "-l", "-t", target, command)
            if not r["ok"]:
                log(f"  send-keys FAIL: {r}")
                return r

        # Enter 전송
        r = run_tmux("send-keys", "-t", target, "Enter")
        log(f"  Enter: ok={r['ok']}")
        return r

    def _handle_send_command(self, body):
        """특정 패널 또는 전체 패널에 명령어 전송"""
        pane = body.get("pane", "all")
        command = body.get("command", "")
        if not command:
            return {"ok": False, "error": "no command"}

        if pane == "all":
            r = run_tmux("list-panes", "-t", SESSION_NAME, "-F", "#{pane_index}")
            if not r["ok"] or not r["stdout"]:
                return r
            results = []
            for idx in r["stdout"].strip().split("\n"):
                target = f"{SESSION_NAME}:.{idx.strip()}"
                results.append(self._send_to_pane(target, command))
            failed = [r for r in results if not r["ok"]]
            return {"ok": len(failed) == 0, "sent": len(results), "failed": len(failed)}
        else:
            target = f"{SESSION_NAME}:.{pane}"
            return self._send_to_pane(target, command)

    def _handle_layout(self, body):
        """레이아웃 변경 - even-horizontal, even-vertical, main-horizontal, main-vertical, tiled"""
        layout = body.get("layout", "tiled")
        return run_tmux("select-layout", "-t", SESSION_NAME, layout)

    def _handle_kill_pane(self, body):
        """패널 종료"""
        pane = body.get("pane", "")
        target = f"{SESSION_NAME}:.{pane}"
        return run_tmux("kill-pane", "-t", target)

    def _handle_new_window(self, body):
        """새 윈도우 생성"""
        name = body.get("name", "")
        args = ["new-window", "-t", SESSION_NAME]
        if name:
            args += ["-n", name]
        return run_tmux(*args)

    def _handle_select_window(self, body):
        """윈도우 선택"""
        index = body.get("index", 0)
        return run_tmux("select-window", "-t", f"{SESSION_NAME}:{index}")

    def _handle_rename_window(self, body):
        """윈도우 이름 변경"""
        index = body.get("index", 0)
        name = body.get("name", "")
        return run_tmux("rename-window", "-t", f"{SESSION_NAME}:{index}", name)

    def _handle_preset(self, body):
        """프리셋 레이아웃 적용 - 패널 N개 자동 분할"""
        count = body.get("count", 2)
        layout = body.get("layout", "tiled")

        # 기존 패널 수 확인
        r = run_tmux("list-panes", "-t", SESSION_NAME, "-F", "#{pane_index}")
        if not r["ok"]:
            return r
        current = len(r["stdout"].strip().split("\n")) if r["stdout"].strip() else 1

        # 부족한 만큼 분할
        for _ in range(count - current):
            run_tmux("split-window", "-t", SESSION_NAME)
            run_tmux("select-layout", "-t", SESSION_NAME, "tiled")  # 공간 확보

        # 초과 패널 제거
        while current > count:
            run_tmux("kill-pane", "-t", f"{SESSION_NAME}:.{current - 1}")
            current -= 1

        # 최종 레이아웃 적용
        return run_tmux("select-layout", "-t", SESSION_NAME, layout)

    # ── 워크플로우 노드 실행 API ──

    def _build_claude_cmd(self, prompt, chat_only=False):
        """claude 명령 구성"""
        cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions"]
        if chat_only:
            cmd += ["--disallowedTools", "Bash,Read,Write,Edit,Glob,Grep,Agent,NotebookEdit"]
        return cmd

    def _handle_node_exec(self, body):
        """노드 실행: 서버 백그라운드에서 claude -p 직접 실행 (tmux 불필요)"""
        node_id = body.get("nodeId", "")
        prompt = body.get("prompt", "")
        cwd = body.get("cwd", "")
        chat_only = body.get("chatOnly", False)
        if not node_id or not prompt:
            return {"ok": False, "error": "nodeId and prompt required"}

        out_file = f"/tmp/node_{node_id}_output.txt"
        in_file = f"/tmp/node_{node_id}_input.txt"
        done_file = f"/tmp/node_{node_id}_done"

        # 이전 결과 정리
        for f in [out_file, done_file]:
            if os.path.exists(f):
                os.remove(f)

        try:
            with open(in_file, "w", encoding="utf-8") as f:
                f.write(prompt)
            log(f"NODE_EXEC [{node_id}] prompt_len={len(prompt)}")
            log(f"NODE_EXEC [{node_id}] preview: {prompt[:120]!r}")
        except Exception as e:
            log(f"NODE_EXEC [{node_id}] write FAIL: {e}")
            return {"ok": False, "error": str(e)}

        # 백그라운드 스레드에서 claude -p 실행
        import threading
        def run_claude():
            try:
                nvm_bin = os.path.expanduser("~/.nvm/versions/node/v22.22.2/bin")
                env = os.environ.copy()
                env["PATH"] = nvm_bin + ":" + env.get("PATH", "")

                cmd = self._build_claude_cmd(prompt, chat_only)
                run_cwd = cwd if cwd and os.path.isdir(cwd) else None
                log(f"NODE_RUN [{node_id}] claude -p 시작... cwd={run_cwd} chatOnly={chat_only}")
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=300, env=env, cwd=run_cwd
                )
                output = result.stdout.strip()
                log(f"NODE_RUN [{node_id}] 완료! output_len={len(output)}")
                log(f"NODE_RUN [{node_id}] output preview: {output[:120]!r}")

                with open(out_file, "w", encoding="utf-8") as f:
                    f.write(output)
                with open(done_file, "w") as f:
                    f.write("done")
            except Exception as e:
                log(f"NODE_RUN [{node_id}] ERROR: {e}")
                with open(out_file, "w", encoding="utf-8") as f:
                    f.write(f"(실행 오류: {e})")
                with open(done_file, "w") as f:
                    f.write("error")

        thread = threading.Thread(target=run_claude, daemon=True)
        thread.start()

        return {"ok": True, "nodeId": node_id}

    def _handle_node_check(self, params):
        """노드 완료 확인: done 파일 존재 + 출력 파일 읽기"""
        node_id = params.get("nodeId", [""])[0]
        out_file = f"/tmp/node_{node_id}_output.txt"
        done_file = f"/tmp/node_{node_id}_done"

        if os.path.exists(done_file):
            try:
                with open(out_file, "r", encoding="utf-8") as f:
                    output = f.read().strip()
                log(f"NODE_CHECK [{node_id}] DONE output_len={len(output)}")
                return {"ok": True, "done": True, "output": output}
            except Exception as e:
                log(f"NODE_CHECK [{node_id}] read FAIL: {e}")
                return {"ok": True, "done": True, "output": ""}
        else:
            return {"ok": True, "done": False}

    # ── 채팅 API (대화 히스토리 기반 claude -p) ──

    def _get_chat_file(self, chat_id):
        os.makedirs(os.path.join(WORKFLOW_DIR, "chats"), exist_ok=True)
        return os.path.join(WORKFLOW_DIR, "chats", f"{chat_id}.json")

    def _handle_chat(self, body):
        """채팅 메시지 전송: 히스토리 포함하여 claude -p 실행"""
        chat_id = body.get("chatId", "")
        message = body.get("message", "")
        cwd = body.get("cwd", "")
        chat_only = body.get("chatOnly", True)  # 채팅은 기본 대화 전용
        if not chat_id or not message:
            return {"ok": False, "error": "chatId and message required"}

        chat_file = self._get_chat_file(chat_id)
        done_file = f"/tmp/chat_{chat_id}_done"
        # 이전 done 파일 삭제
        if os.path.exists(done_file):
            os.remove(done_file)

        # 히스토리 로드
        history = []
        if os.path.exists(chat_file):
            with open(chat_file, "r", encoding="utf-8") as f:
                history = json.load(f)

        # 새 메시지 추가
        history.append({"role": "user", "content": message, "ts": datetime.now().isoformat()})

        # 전체 대화를 프롬프트로 구성
        prompt_parts = []
        # 1) 캔버스 워크플로우 히스토리 포함 (최초 맥락)
        canvas_hist = body.get("canvasHistory", [])
        if canvas_hist:
            prompt_parts.append("[시스템 맥락 - 이전 워크플로우에서의 대화]")
            for ch in canvas_hist:
                if ch.get("input"):
                    prompt_parts.append(f"[User 요청]: {ch['input']}")
                if ch.get("output"):
                    prompt_parts.append(f"[Assistant 응답]: {ch['output']}")
            prompt_parts.append("[현재 대화 시작]")
            prompt_parts.append("")

        # 2) 채팅 히스토리
        for msg in history:
            if msg["role"] == "user":
                prompt_parts.append(f"[User]: {msg['content']}")
            else:
                prompt_parts.append(f"[Assistant]: {msg['content']}")

        full_prompt = "아래는 이전 대화입니다. 맥락을 이해하고 마지막 [User] 메시지에 자연스럽게 답변하세요.\n\n" + "\n\n".join(prompt_parts)

        # 히스토리 저장 (응답 전)
        with open(chat_file, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

        log(f"CHAT [{chat_id}] msg_len={len(message)} history={len(history)} prompt_len={len(full_prompt)}")

        # 백그라운드 실행
        import threading
        def run():
            try:
                nvm_bin = os.path.expanduser("~/.nvm/versions/node/v22.22.2/bin")
                env = os.environ.copy()
                env["PATH"] = nvm_bin + ":" + env.get("PATH", "")
                run_cwd = cwd if cwd and os.path.isdir(cwd) else None

                cmd = self._build_claude_cmd(full_prompt, chat_only)
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=300, env=env, cwd=run_cwd
                )
                reply = result.stdout.strip()
                log(f"CHAT [{chat_id}] reply_len={len(reply)}")

                # 응답을 히스토리에 추가
                history.append({"role": "assistant", "content": reply, "ts": datetime.now().isoformat()})
                with open(chat_file, "w", encoding="utf-8") as f:
                    json.dump(history, f, ensure_ascii=False, indent=2)
                # 완료 마커
                with open(done_file, "w") as f:
                    f.write(reply)
            except Exception as e:
                log(f"CHAT [{chat_id}] ERROR: {e}")
                history.append({"role": "assistant", "content": f"(오류: {e})", "ts": datetime.now().isoformat()})
                with open(chat_file, "w", encoding="utf-8") as f:
                    json.dump(history, f, ensure_ascii=False, indent=2)
                with open(done_file, "w") as f:
                    f.write(f"(오류: {e})")

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True, "chatId": chat_id, "historyLen": len(history)}

    def _handle_chat_check(self, params):
        """채팅 응답 완료 확인"""
        chat_id = params.get("chatId", [""])[0]
        done_file = f"/tmp/chat_{chat_id}_done"
        if os.path.exists(done_file):
            with open(done_file, "r", encoding="utf-8") as f:
                reply = f.read().strip()
            return {"ok": True, "done": True, "reply": reply}
        return {"ok": True, "done": False}

    def _handle_chat_history(self, params):
        """채팅 히스토리 조회"""
        chat_id = params.get("chatId", [""])[0]
        chat_file = self._get_chat_file(chat_id)
        if os.path.exists(chat_file):
            with open(chat_file, "r", encoding="utf-8") as f:
                history = json.load(f)
            return {"ok": True, "history": history}
        return {"ok": True, "history": []}

    # ── 패널 캡처 API ──

    def _handle_pane_content(self, params):
        """패널 내용 캡처 (전체 히스토리)"""
        pane = params.get("pane", ["1"])[0]
        target = f"{SESSION_NAME}:.{pane}"
        r = run_tmux("capture-pane", "-t", target, "-p", "-S", "-")
        if not r["ok"]:
            log(f"CAPTURE FAIL pane={pane}: {r}")
            return r
        content = r["stdout"]
        all_lines = content.split("\n") if content else []
        log(f"CAPTURE pane={pane} lines={len(all_lines)}")
        return {"ok": True, "content": content, "lines": all_lines, "lineCount": len(all_lines)}

    def _handle_pane_prompt_check(self, params):
        """패널의 프롬프트(유휴) 상태 확인 - Claude Code 특화"""
        pane = params.get("pane", ["1"])[0]
        target = f"{SESSION_NAME}:.{pane}"
        r = run_tmux("capture-pane", "-t", target, "-p")
        if not r["ok"]:
            return r
        all_lines = r["stdout"].split("\n")
        # UI 장식 제거: 상태바, 구분선(─만 있는 줄), 빈 줄
        content_lines = []
        for l in all_lines:
            s = l.strip()
            if not s:
                continue
            # 상태바 필터
            if 'bypass permissions' in l or 'shift+tab' in l or '⏵⏵' in l:
                continue
            # 구분선 필터 (─ 문자만으로 이루어진 줄)
            if s and all(c in '─━═' for c in s):
                continue
            content_lines.append(l)

        # Claude Code 유휴 = 마지막 몇 줄에 ❯ 또는 $ 프롬프트 존재
        idle = False
        check_lines = content_lines[-3:] if content_lines else []
        for cl in check_lines:
            if re.search(r'❯|~\$\s*$|\$\s*$', cl):
                idle = True
                break

        last_preview = repr(content_lines[-1][:60]) if content_lines else 'empty'
        log(f"PROMPT pane={pane} idle={idle} last={last_preview} (checked {len(check_lines)} lines)")
        return {"ok": True, "idle": idle, "lastLines": check_lines}

    def _handle_pane_line_count(self, params):
        """패널의 현재 줄 수 반환"""
        pane = params.get("pane", ["1"])[0]
        target = f"{SESSION_NAME}:.{pane}"
        r = run_tmux("capture-pane", "-t", target, "-p", "-S", "-")
        if not r["ok"]:
            return r
        lines = r["stdout"].split("\n") if r["stdout"] else []
        return {"ok": True, "lineCount": len(lines)}

    # ── 워크플로우 CRUD API ──

    def _handle_workflow_save(self, body):
        """워크플로우를 JSON 파일로 저장"""
        os.makedirs(WORKFLOW_DIR, exist_ok=True)
        wf_id = body.get("id", str(uuid.uuid4()))
        body["id"] = wf_id
        body["modified"] = datetime.now().isoformat()
        if "created" not in body:
            body["created"] = body["modified"]
        filepath = os.path.join(WORKFLOW_DIR, f"{wf_id}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, indent=2)
        return {"ok": True, "id": wf_id}

    def _handle_workflow_load(self, params):
        """저장된 워크플로우 불러오기"""
        wf_id = params.get("id", [""])[0]
        if not wf_id:
            return {"ok": False, "error": "id required"}
        filepath = os.path.join(WORKFLOW_DIR, f"{wf_id}.json")
        if not os.path.exists(filepath):
            return {"ok": False, "error": "workflow not found"}
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"ok": True, "workflow": data}

    def _handle_workflow_list(self):
        """저장된 워크플로우 목록"""
        os.makedirs(WORKFLOW_DIR, exist_ok=True)
        workflows = []
        for fname in os.listdir(WORKFLOW_DIR):
            if fname.endswith(".json"):
                fpath = os.path.join(WORKFLOW_DIR, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    workflows.append({
                        "id": data.get("id", fname[:-5]),
                        "name": data.get("name", "Untitled"),
                        "modified": data.get("modified", ""),
                        "nodeCount": len(data.get("nodes", []))
                    })
                except Exception:
                    pass
        workflows.sort(key=lambda w: w["modified"], reverse=True)
        return {"ok": True, "workflows": workflows}

    def _handle_workflow_delete(self, body):
        """워크플로우 삭제"""
        wf_id = body.get("id", "")
        if not wf_id:
            return {"ok": False, "error": "id required"}
        filepath = os.path.join(WORKFLOW_DIR, f"{wf_id}.json")
        if os.path.exists(filepath):
            os.remove(filepath)
            return {"ok": True}
        return {"ok": False, "error": "not found"}

    def _json_response(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):
        """CORS preflight"""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        """간결한 로그"""
        print(f"[tmux-web] {args[0]}")


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    server = ThreadedHTTPServer(("0.0.0.0", PORT), TmuxHandler)
    print(f"╔══════════════════════════════════════╗")
    print(f"║  tmux 웹 컨트롤러 서버 시작          ║")
    print(f"║  http://localhost:{PORT}              ║")
    print(f"╚══════════════════════════════════════╝")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n서버 종료")
        server.server_close()
