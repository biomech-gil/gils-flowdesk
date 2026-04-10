#!/usr/bin/env python3
"""tmux 노드 캔버스 서버 — SQLite + Claude CLI 통합"""

import subprocess, json, os, re, uuid, sys, time, sqlite3, threading
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

PORT = 8888
SESSION_NAME = "main"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "canvas.db")

def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# ═══════════════════════════════════════
# SQLite DB
# ═══════════════════════════════════════
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    db = get_db()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        data TEXT NOT NULL,
        created TEXT NOT NULL,
        modified TEXT NOT NULL,
        favorite INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS temps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        data TEXT NOT NULL,
        date TEXT NOT NULL,
        created TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS memo_folders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        icon TEXT DEFAULT '📁',
        sort_order INTEGER DEFAULT 0,
        created TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS memos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        content TEXT DEFAULT '',
        folder_id INTEGER,
        is_temp INTEGER DEFAULT 1,
        created TEXT NOT NULL,
        modified TEXT NOT NULL,
        FOREIGN KEY (folder_id) REFERENCES memo_folders(id)
    );
    CREATE TABLE IF NOT EXISTS executions (
        id TEXT PRIMARY KEY,
        project_id TEXT,
        node_id TEXT NOT NULL,
        node_name TEXT NOT NULL,
        input_raw TEXT,
        input_resolved TEXT,
        output TEXT,
        status TEXT DEFAULT 'running',
        chat_only INTEGER DEFAULT 1,
        started TEXT NOT NULL,
        finished TEXT,
        FOREIGN KEY (project_id) REFERENCES projects(id)
    );
    CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY,
        parent_exec_id TEXT,
        node_id TEXT NOT NULL,
        node_name TEXT NOT NULL,
        title TEXT,
        created TEXT NOT NULL,
        FOREIGN KEY (parent_exec_id) REFERENCES executions(id)
    );
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conv_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        ts TEXT NOT NULL,
        FOREIGN KEY (conv_id) REFERENCES conversations(id)
    );
    CREATE INDEX IF NOT EXISTS idx_exec_node ON executions(node_id);
    CREATE INDEX IF NOT EXISTS idx_conv_node ON conversations(node_id);
    CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conv_id);
    """)
    # 기존 DB에 favorite 컬럼이 없으면 추가 (마이그레이션)
    try:
        db.execute("ALTER TABLE projects ADD COLUMN favorite INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # 이미 있음
    db.commit()
    db.close()
    log("DB initialized")

init_db()
db_lock = threading.Lock()

def db_exec(sql, params=(), fetch=False, fetchone=False):
    with db_lock:
        db = get_db()
        try:
            cur = db.execute(sql, params)
            if fetchone:
                row = cur.fetchone()
                return dict(row) if row else None
            if fetch:
                return [dict(r) for r in cur.fetchall()]
            db.commit()
            return cur.lastrowid
        finally:
            db.close()

# ═══════════════════════════════════════
# tmux
# ═══════════════════════════════════════
def run_tmux(*args):
    cmd = ["tmux"] + list(args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        ok = result.returncode == 0
        r = {"ok": ok, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
        if not ok:
            log(f"  TMUX FAIL: {' '.join(args[:3])} → {result.stderr.strip()[:80]}")
        return r
    except Exception as e:
        return {"ok": False, "error": str(e)}

def get_session_info():
    info = {}
    r = run_tmux("list-sessions", "-F", "#{session_name}:#{session_windows}:#{session_attached}")
    if r["ok"] and r["stdout"]:
        info["sessions"] = []
        for line in r["stdout"].split("\n"):
            parts = line.split(":")
            if len(parts) >= 3:
                info["sessions"].append({"name": parts[0], "windows": int(parts[1]), "attached": parts[2] == "1"})
    r = run_tmux("list-panes", "-t", SESSION_NAME, "-F", "#{pane_index}:#{pane_width}x#{pane_height}:#{pane_current_command}:#{pane_current_path}")
    if r["ok"] and r["stdout"]:
        info["panes"] = []
        for line in r["stdout"].split("\n"):
            parts = line.split(":", 3)
            if len(parts) >= 4:
                info["panes"].append({"index": int(parts[0]), "size": parts[1], "command": parts[2], "path": parts[3]})
    r = run_tmux("list-windows", "-t", SESSION_NAME, "-F", "#{window_index}:#{window_name}:#{window_panes}:#{window_active}")
    if r["ok"] and r["stdout"]:
        info["windows"] = []
        for line in r["stdout"].split("\n"):
            parts = line.split(":")
            if len(parts) >= 4:
                info["windows"].append({"index": int(parts[0]), "name": parts[1], "panes": int(parts[2]), "active": parts[3] == "1"})
    return info

# ═══════════════════════════════════════
# Claude CLI
# ═══════════════════════════════════════
def build_claude_cmd(prompt, opts=None):
    """claude CLI 명령 구성 — 고급 옵션 지원"""
    opts = opts or {}
    cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions", "--effort", "max"]

    # 대화전용: Read만 허용 (이미지 인식용), 나머지 도구 차단
    chat_only = opts.get("chatOnly", True)
    if chat_only:
        cmd += ["--disallowedTools", "Bash,Write,Edit,Glob,Grep,Agent,NotebookEdit,WebFetch,WebSearch"]
        # Read는 허용 → 이미지 파일 인식 가능

    # 시스템 프롬프트
    sys_prompt = opts.get("systemPrompt", "")
    if sys_prompt:
        cmd += ["--append-system-prompt", sys_prompt]

    # 폴백 모델
    cmd += ["--fallback-model", "sonnet"]

    # JSON 스키마 (구조화 출력)
    json_schema = opts.get("jsonSchema", "")
    if json_schema:
        cmd += ["--output-format", "json", "--json-schema", json_schema]

    # 최대 턴 수 (무한루프 방지)
    max_turns = opts.get("maxTurns", 0)
    if max_turns > 0:
        cmd += ["--max-turns", str(max_turns)]

    # 이미지 파일 경로가 있으면 프롬프트에 포함 (Read 도구로 읽기 유도)
    images = opts.get("images", [])
    if images:
        img_instructions = "\n\n[첨부 이미지 파일 — Read 도구로 읽어서 분석하세요]:\n"
        img_instructions += "\n".join(f"- {img}" for img in images)
        # 프롬프트 끝에 이미지 경로 추가
        idx = cmd.index(prompt)
        cmd[idx] = prompt + img_instructions

    return cmd

def get_claude_env():
    nvm_bin = os.path.expanduser("~/.nvm/versions/node/v22.22.2/bin")
    env = os.environ.copy()
    env["PATH"] = nvm_bin + ":" + env.get("PATH", "")
    return env

# ═══════════════════════════════════════
# HTTP Handler
# ═══════════════════════════════════════
class TmuxHandler(SimpleHTTPRequestHandler):

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        p = parsed.path

        if p == "/api/status": self._json(get_session_info())
        elif p == "/api/node-check": self._json(self._node_check(params))
        elif p == "/api/chat-check": self._json(self._chat_check(params))
        elif p == "/api/chat-history": self._json(self._chat_history(params))
        elif p == "/api/project/list": self._json(self._project_list())
        elif p == "/api/project/list-meta": self._json(self._project_list_meta())
        elif p == "/api/project/load": self._json(self._project_load(params))
        elif p == "/api/temp/list": self._json(self._temp_list())
        elif p == "/api/temp/load": self._json(self._temp_load(params))
        elif p == "/api/memo/list": self._json(self._memo_list(params))
        elif p == "/api/memo/get": self._json(self._memo_get(params))
        elif p == "/api/folder/list": self._json(self._folder_list())
        elif p == "/api/exec/list": self._json(self._exec_list(params))
        elif p == "/api/conv/list": self._json(self._conv_list(params))
        elif p == "/api/conv/messages": self._json(self._conv_messages(params))
        elif p == "/api/state/load": self._json(self._state_load())
        elif p == "/api/pane-content": self._json(self._pane_content(params))
        elif p == "/api/pane-prompt-check": self._json(self._pane_prompt(params))
        elif p == "/":
            self.path = "/canvas.html"
            return SimpleHTTPRequestHandler.do_GET(self)
        else:
            return SimpleHTTPRequestHandler.do_GET(self)

    def do_POST(self):
        parsed = urlparse(self.path)
        content_len = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_len)) if content_len > 0 else {}
        p = parsed.path

        handlers = {
            "/api/node-exec": self._node_exec,
            "/api/chat": self._chat_send,
            "/api/chat/fork": self._chat_fork,
            "/api/project/save": self._project_save,
            "/api/project/delete": self._project_delete,
            "/api/state/save": self._state_save,
            "/api/temp/save": self._temp_save,
            "/api/temp/delete": self._temp_delete,
            "/api/memo/save": self._memo_save,
            "/api/memo/delete": self._memo_delete,
            "/api/folder/save": self._folder_save,
            "/api/folder/delete": self._folder_delete,
            "/api/project/star": self._project_star,
            "/api/upload": self._upload_file,
            "/api/pdf-split": self._pdf_split,
            "/api/list-pdf-images": self._list_pdf_images,
            "/api/reset-session": self._reset_session,
            "/api/add-pane": self._add_pane,
            "/api/setup-session": self._setup_session,
            "/api/split": self._split,
            "/api/send-command": self._send_command,
            "/api/preset": self._preset,
            "/api/layout": self._layout,
            "/api/kill-pane": self._kill_pane,
            "/api/new-window": self._new_window,
            "/api/select-window": self._select_window,
        }
        handler = handlers.get(p)
        if handler:
            self._json(handler(body))
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── Node Execution (캔버스 워크플로우) ──

    def _node_exec(self, body):
        node_id = body.get("nodeId", "")
        node_name = body.get("nodeName", node_id)
        prompt = body.get("prompt", "")
        cwd = body.get("cwd", "")
        chat_only = body.get("chatOnly", True)
        project_id = body.get("projectId", "")
        if not node_id or not prompt:
            return {"ok": False, "error": "nodeId and prompt required"}

        exec_id = str(uuid.uuid4())[:8]
        out_file = f"/tmp/node_{node_id}_output.txt"
        done_file = f"/tmp/node_{node_id}_done"
        for f in [out_file, done_file]:
            if os.path.exists(f): os.remove(f)

        # DB에 실행 기록 생성
        db_exec("INSERT INTO executions (id, project_id, node_id, node_name, input_raw, input_resolved, chat_only, started, status) VALUES (?,?,?,?,?,?,?,?,?)",
                (exec_id, project_id, node_id, node_name, body.get("inputRaw", ""), prompt, 1 if chat_only else 0, datetime.now().isoformat(), "running"))
        log(f"EXEC [{exec_id}] node={node_name} prompt={len(prompt)}자 chatOnly={chat_only}")

        def run():
            try:
                env = get_claude_env()
                run_cwd = cwd if cwd and os.path.isdir(cwd) else None
                cmd = build_claude_cmd(prompt, {
                    "chatOnly": chat_only,
                    "systemPrompt": body.get("systemPrompt", ""),
                    "jsonSchema": body.get("jsonSchema", ""),
                    "maxTurns": body.get("maxTurns", 0),
                    "images": body.get("images", []),
                })
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env, cwd=run_cwd)
                output = result.stdout.strip()
                log(f"EXEC [{exec_id}] done! {len(output)}자")
                with open(out_file, "w", encoding="utf-8") as f: f.write(output)
                with open(done_file, "w") as f: f.write("done")
                # DB 업데이트
                db_exec("UPDATE executions SET output=?, status='complete', finished=? WHERE id=?",
                        (output, datetime.now().isoformat(), exec_id))
            except Exception as e:
                log(f"EXEC [{exec_id}] ERROR: {e}")
                err_msg = f"(오류: {e})"
                with open(out_file, "w", encoding="utf-8") as f: f.write(err_msg)
                with open(done_file, "w") as f: f.write("error")
                db_exec("UPDATE executions SET output=?, status='error', finished=? WHERE id=?",
                        (err_msg, datetime.now().isoformat(), exec_id))

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True, "execId": exec_id, "nodeId": node_id}

    def _node_check(self, params):
        node_id = params.get("nodeId", [""])[0]
        done_file = f"/tmp/node_{node_id}_done"
        out_file = f"/tmp/node_{node_id}_output.txt"
        if os.path.exists(done_file):
            try:
                with open(out_file, "r", encoding="utf-8") as f: output = f.read().strip()
                return {"ok": True, "done": True, "output": output}
            except: return {"ok": True, "done": True, "output": ""}
        return {"ok": True, "done": False}

    # ── Chat (대화 세션) ──

    def _chat_send(self, body):
        conv_id = body.get("convId", "")
        message = body.get("message", "")
        cwd = body.get("cwd", "")
        chat_only = body.get("chatOnly", True)
        node_id = body.get("nodeId", "")
        node_name = body.get("nodeName", "")
        if not message: return {"ok": False, "error": "message required"}

        # 대화 세션 없으면 생성
        if not conv_id:
            conv_id = str(uuid.uuid4())[:8]
            db_exec("INSERT INTO conversations (id, node_id, node_name, title, created) VALUES (?,?,?,?,?)",
                    (conv_id, node_id, node_name, message[:30], datetime.now().isoformat()))

        # 유저 메시지 저장
        db_exec("INSERT INTO messages (conv_id, role, content, ts) VALUES (?,?,?,?)",
                (conv_id, "user", message, datetime.now().isoformat()))

        # 이전 대화 로드
        rows = db_exec("SELECT role, content FROM messages WHERE conv_id=? ORDER BY id", (conv_id,), fetch=True)
        prompt_parts = []
        for r in rows:
            prefix = "[User]" if r["role"] == "user" else "[Assistant]"
            prompt_parts.append(f"{prefix}: {r['content']}")
        full_prompt = "아래는 대화 기록입니다. 마지막 [User]에 답변하세요.\n\n" + "\n\n".join(prompt_parts)

        done_file = f"/tmp/chat_{conv_id}_done"
        if os.path.exists(done_file): os.remove(done_file)

        log(f"CHAT [{conv_id}] msg={len(message)}자 history={len(rows)}")

        def run():
            try:
                env = get_claude_env()
                run_cwd = cwd if cwd and os.path.isdir(cwd) else None
                cmd = build_claude_cmd(full_prompt, {
                    "chatOnly": chat_only,
                    "systemPrompt": body.get("systemPrompt", ""),
                    "images": body.get("images", []),
                })
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env, cwd=run_cwd)
                reply = result.stdout.strip()
                log(f"CHAT [{conv_id}] reply={len(reply)}자")
                db_exec("INSERT INTO messages (conv_id, role, content, ts) VALUES (?,?,?,?)",
                        (conv_id, "assistant", reply, datetime.now().isoformat()))
                with open(done_file, "w") as f: f.write(reply)
            except Exception as e:
                log(f"CHAT [{conv_id}] ERROR: {e}")
                db_exec("INSERT INTO messages (conv_id, role, content, ts) VALUES (?,?,?,?)",
                        (conv_id, "assistant", f"(오류: {e})", datetime.now().isoformat()))
                with open(done_file, "w") as f: f.write(f"(오류: {e})")

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True, "convId": conv_id}

    def _chat_check(self, params):
        conv_id = params.get("convId", [""])[0]
        done_file = f"/tmp/chat_{conv_id}_done"
        if os.path.exists(done_file):
            with open(done_file, "r", encoding="utf-8") as f: reply = f.read().strip()
            return {"ok": True, "done": True, "reply": reply}
        return {"ok": True, "done": False}

    def _chat_history(self, params):
        conv_id = params.get("convId", [""])[0]
        if not conv_id: return {"ok": True, "messages": []}
        rows = db_exec("SELECT role, content, ts FROM messages WHERE conv_id=? ORDER BY id", (conv_id,), fetch=True)
        return {"ok": True, "messages": rows}

    def _chat_fork(self, body):
        """실행 결과에서 대화 분기 생성"""
        exec_id = body.get("execId", "")
        node_id = body.get("nodeId", "")
        node_name = body.get("nodeName", "")
        if not exec_id and not node_id:
            return {"ok": False, "error": "execId or nodeId required"}

        conv_id = str(uuid.uuid4())[:8]
        title = f"{node_name} 분기"

        # 실행 기록에서 input/output 가져와서 초기 메시지로
        if exec_id:
            ex = db_exec("SELECT * FROM executions WHERE id=?", (exec_id,), fetchone=True)
            if ex:
                node_id = ex["node_id"]
                node_name = ex["node_name"]
                title = f"{node_name} @{ex['started'][:16]}"
        db_exec("INSERT INTO conversations (id, parent_exec_id, node_id, node_name, title, created) VALUES (?,?,?,?,?,?)",
                (conv_id, exec_id, node_id, node_name, title, datetime.now().isoformat()))

        # 실행 기록의 input/output을 초기 대화로 삽입
        if exec_id:
            ex = db_exec("SELECT * FROM executions WHERE id=?", (exec_id,), fetchone=True)
            if ex:
                if ex.get("input_resolved"):
                    db_exec("INSERT INTO messages (conv_id, role, content, ts) VALUES (?,?,?,?)",
                            (conv_id, "user", ex["input_resolved"], ex["started"]))
                if ex.get("output"):
                    db_exec("INSERT INTO messages (conv_id, role, content, ts) VALUES (?,?,?,?)",
                            (conv_id, "assistant", ex["output"], ex.get("finished", ex["started"])))
        log(f"FORK conv={conv_id} from exec={exec_id} node={node_name}")
        return {"ok": True, "convId": conv_id}

    # ── Execution History ──

    def _exec_list(self, params):
        node_id = params.get("nodeId", [""])[0]
        if node_id:
            rows = db_exec("SELECT id, node_name, status, started, finished, substr(input_resolved,1,50) as input_preview, substr(output,1,50) as output_preview FROM executions WHERE node_id=? ORDER BY started DESC LIMIT 50", (node_id,), fetch=True)
        else:
            rows = db_exec("SELECT id, node_name, status, started, finished, substr(input_resolved,1,50) as input_preview, substr(output,1,50) as output_preview FROM executions ORDER BY started DESC LIMIT 100", fetch=True)
        return {"ok": True, "executions": rows}

    # ── Conversation List ──

    def _conv_list(self, params):
        node_id = params.get("nodeId", [""])[0]
        if node_id:
            rows = db_exec("SELECT c.id, c.title, c.node_name, c.created, c.parent_exec_id, (SELECT COUNT(*) FROM messages WHERE conv_id=c.id) as msg_count FROM conversations c WHERE c.node_id=? ORDER BY c.created DESC LIMIT 50", (node_id,), fetch=True)
        else:
            rows = db_exec("SELECT c.id, c.title, c.node_name, c.created, c.parent_exec_id, (SELECT COUNT(*) FROM messages WHERE conv_id=c.id) as msg_count FROM conversations c ORDER BY c.created DESC LIMIT 100", fetch=True)
        return {"ok": True, "conversations": rows}

    def _conv_messages(self, params):
        conv_id = params.get("convId", [""])[0]
        rows = db_exec("SELECT role, content, ts FROM messages WHERE conv_id=? ORDER BY id", (conv_id,), fetch=True)
        return {"ok": True, "messages": rows}

    # ── Project CRUD ──

    def _project_save(self, body):
        pid = body.get("id", str(uuid.uuid4())[:8])
        name = body.get("name", "Untitled")
        now = datetime.now().isoformat()
        data = json.dumps(body, ensure_ascii=False)
        existing = db_exec("SELECT id FROM projects WHERE id=?", (pid,), fetchone=True)
        if existing:
            db_exec("UPDATE projects SET name=?, data=?, modified=? WHERE id=?", (name, data, now, pid))
        else:
            db_exec("INSERT INTO projects (id, name, data, created, modified) VALUES (?,?,?,?,?)", (pid, name, data, now, now))
        log(f"PROJECT save [{pid}] {name}")
        return {"ok": True, "id": pid}

    def _project_load(self, params):
        pid = params.get("id", [""])[0]
        row = db_exec("SELECT * FROM projects WHERE id=?", (pid,), fetchone=True)
        if not row: return {"ok": False, "error": "not found"}
        return {"ok": True, "project": json.loads(row["data"])}

    def _project_list(self):
        rows = db_exec("SELECT id, name, modified, favorite FROM projects WHERE id!='__current__' ORDER BY favorite DESC, modified DESC LIMIT 100", fetch=True)
        return {"ok": True, "projects": rows}

    def _project_list_meta(self):
        """메타데이터만 (data 필드 제외, 가벼움) — 노드 수도 함께"""
        rows = db_exec("SELECT id, name, modified, created, favorite FROM projects WHERE id!='__current__' ORDER BY favorite DESC, modified DESC LIMIT 100", fetch=True)
        # 노드 수만 추가 추출
        for r in rows:
            try:
                d = db_exec("SELECT data FROM projects WHERE id=?", (r["id"],), fetchone=True)
                if d:
                    pj = json.loads(d["data"])
                    r["nodeCount"] = len(pj.get("nodes", []))
                    r["connCount"] = len(pj.get("connections", []))
                else:
                    r["nodeCount"] = 0
                    r["connCount"] = 0
            except:
                r["nodeCount"] = 0
                r["connCount"] = 0
        return {"ok": True, "projects": rows}

    def _project_star(self, body):
        pid = body.get("id", "")
        fav = 1 if body.get("favorite") else 0
        if not pid: return {"ok": False, "error": "id required"}
        db_exec("UPDATE projects SET favorite=? WHERE id=?", (fav, pid))
        return {"ok": True}

    # ── Temp Saves (날짜 기준 자동 백업) ──

    def _temp_save(self, body):
        """일별 임시 저장 — 같은 날짜에 추가 저장 가능 (여러 개)"""
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        now = datetime.now().isoformat()
        name = body.get("name", "")
        data = json.dumps(body, ensure_ascii=False)
        db_exec("INSERT INTO temps (name, data, date, created) VALUES (?,?,?,?)", (name, data, today, now))
        # 30일 이상 된 temp 자동 정리
        db_exec("DELETE FROM temps WHERE created < datetime('now','-30 days')")
        return {"ok": True}

    def _temp_list(self):
        """temp 목록 (메타데이터만, 최신순)"""
        rows = db_exec("SELECT id, name, date, created FROM temps ORDER BY created DESC LIMIT 50", fetch=True)
        for r in rows:
            try:
                d = db_exec("SELECT data FROM temps WHERE id=?", (r["id"],), fetchone=True)
                if d:
                    pj = json.loads(d["data"])
                    r["nodeCount"] = len(pj.get("nodes", []))
                    r["connCount"] = len(pj.get("connections", []))
            except:
                r["nodeCount"] = 0
                r["connCount"] = 0
        return {"ok": True, "temps": rows}

    def _temp_load(self, params):
        tid = params.get("id", [""])[0]
        row = db_exec("SELECT data FROM temps WHERE id=?", (tid,), fetchone=True)
        if not row: return {"ok": False, "error": "not found"}
        return {"ok": True, "state": json.loads(row["data"])}

    def _temp_delete(self, body):
        tid = body.get("id", "")
        if tid: db_exec("DELETE FROM temps WHERE id=?", (tid,))
        return {"ok": True}

    # ── Memo & Folders ──

    def _memo_list(self, params):
        """메모 목록 (folder_id 또는 is_temp 필터)"""
        folder_id = params.get("folderId", [None])[0]
        is_temp = params.get("isTemp", [None])[0]
        sql = "SELECT id, name, folder_id, is_temp, substr(content,1,100) as preview, created, modified FROM memos"
        cond = []
        args = []
        if folder_id is not None:
            if folder_id == "null":
                cond.append("folder_id IS NULL")
            else:
                cond.append("folder_id=?")
                args.append(folder_id)
        if is_temp is not None:
            cond.append("is_temp=?")
            args.append(int(is_temp))
        if cond: sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY modified DESC LIMIT 200"
        rows = db_exec(sql, tuple(args), fetch=True)
        return {"ok": True, "memos": rows}

    def _memo_get(self, params):
        mid = params.get("id", [""])[0]
        row = db_exec("SELECT * FROM memos WHERE id=?", (mid,), fetchone=True)
        if not row: return {"ok": False, "error": "not found"}
        return {"ok": True, "memo": row}

    def _memo_save(self, body):
        mid = body.get("id")
        name = body.get("name", "")
        content = body.get("content", "")
        folder_id = body.get("folderId")
        is_temp = 1 if body.get("isTemp", True) else 0
        now = datetime.now().isoformat()
        if mid:
            db_exec("UPDATE memos SET name=?, content=?, folder_id=?, is_temp=?, modified=? WHERE id=?",
                    (name, content, folder_id, is_temp, now, mid))
            return {"ok": True, "id": mid}
        else:
            new_id = db_exec("INSERT INTO memos (name, content, folder_id, is_temp, created, modified) VALUES (?,?,?,?,?,?)",
                             (name or f"메모{int(time.time())}", content, folder_id, is_temp, now, now))
            return {"ok": True, "id": new_id}

    def _memo_delete(self, body):
        mid = body.get("id", "")
        if mid: db_exec("DELETE FROM memos WHERE id=?", (mid,))
        return {"ok": True}

    def _folder_list(self):
        rows = db_exec("SELECT f.*, (SELECT COUNT(*) FROM memos WHERE folder_id=f.id) as memo_count FROM memo_folders f ORDER BY sort_order, name", fetch=True)
        return {"ok": True, "folders": rows}

    def _folder_save(self, body):
        fid = body.get("id")
        name = body.get("name", "새 폴더")
        icon = body.get("icon", "📁")
        now = datetime.now().isoformat()
        if fid:
            db_exec("UPDATE memo_folders SET name=?, icon=? WHERE id=?", (name, icon, fid))
            return {"ok": True, "id": fid}
        else:
            new_id = db_exec("INSERT INTO memo_folders (name, icon, created) VALUES (?,?,?)", (name, icon, now))
            return {"ok": True, "id": new_id}

    def _folder_delete(self, body):
        fid = body.get("id", "")
        if fid:
            db_exec("UPDATE memos SET folder_id=NULL WHERE folder_id=?", (fid,))
            db_exec("DELETE FROM memo_folders WHERE id=?", (fid,))
        return {"ok": True}

    def _project_delete(self, body):
        pid = body.get("id", "")
        if pid: db_exec("DELETE FROM projects WHERE id=?", (pid,))
        return {"ok": True}

    # ── File Upload (이미지/시스템프롬프트 파일) ──

    def _upload_file(self, body):
        """base64로 인코딩된 파일을 WSL에 저장하고 경로 반환"""
        import base64
        filename = body.get("filename", "")
        b64data = body.get("data", "")
        purpose = body.get("purpose", "image")  # image | sysprompt
        if not filename or not b64data:
            return {"ok": False, "error": "filename and data required"}

        # 디렉토리: ~/tmux-controller/uploads/(images|sysprompts)/
        upload_dir = os.path.join(BASE_DIR, "uploads", purpose + "s")
        os.makedirs(upload_dir, exist_ok=True)

        # 파일명 sanitize + timestamp 추가 (덮어쓰기 방지)
        safe_name = re.sub(r'[^\w\s.\-_가-힣]', '_', filename)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        final_name = f"{ts}_{safe_name}"
        filepath = os.path.join(upload_dir, final_name)

        try:
            # base64 디코딩 (data:image/png;base64,xxx 형식 지원)
            if ',' in b64data:
                b64data = b64data.split(',', 1)[1]
            data = base64.b64decode(b64data)
            with open(filepath, "wb") as f:
                f.write(data)
            log(f"UPLOAD [{purpose}] {filename} → {filepath} ({len(data)} bytes)")
            return {"ok": True, "path": filepath, "filename": final_name}
        except Exception as e:
            log(f"UPLOAD ERROR: {e}")
            return {"ok": False, "error": str(e)}

    def _list_pdf_images(self, body):
        """이미지 경로에서 부모 PDF 디렉토리의 모든 이미지 목록 반환"""
        img_path = body.get("path", "")
        if not img_path or not os.path.exists(img_path):
            return {"ok": False, "error": "path not found"}
        pdf_dir = os.path.dirname(img_path)
        if not os.path.basename(pdf_dir).startswith(("20", "19")):
            # not a pdf split dir
            return {"ok": True, "images": []}
        try:
            files = sorted([f for f in os.listdir(pdf_dir) if f.startswith("page_") and f.endswith(".png")])
            images = [{"path": os.path.join(pdf_dir, f), "name": f} for f in files]
            return {"ok": True, "images": images, "dir": pdf_dir}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── PDF Split → 이미지 변환 ──

    def _parse_page_range(self, spec, total):
        """페이지 범위 파싱 '1,5,7-10' → [1,5,7,8,9,10]"""
        if not spec or spec.strip() == "":
            return list(range(1, total + 1))
        pages = []
        for part in spec.replace(" ", "").split(","):
            if "-" in part:
                a, b = part.split("-", 1)
                a = int(a) if a else 1
                b = int(b) if b else total
                pages.extend(range(a, min(b, total) + 1))
            else:
                p = int(part)
                if 1 <= p <= total: pages.append(p)
        return sorted(set(pages))

    def _pdf_split(self, body):
        """PDF base64 → 페이지별 PNG 이미지 변환 (600 DPI)"""
        import base64
        filename = body.get("filename", "doc.pdf")
        b64data = body.get("data", "")
        page_spec = body.get("pages", "")  # "1,5,7-10" or empty for all
        dpi = int(body.get("dpi", 600))

        if not b64data:
            return {"ok": False, "error": "data required"}

        try:
            import sys
            sys.path.insert(0, os.path.expanduser("~/.local/lib/python3.12/site-packages"))
            import fitz
        except Exception as e:
            log(f"PDF_SPLIT pymupdf import FAIL: {e}")
            return {"ok": False, "error": "pymupdf not installed"}

        # 디코딩
        try:
            if "," in b64data:
                b64data = b64data.split(",", 1)[1]
            pdf_bytes = base64.b64decode(b64data)
        except Exception as e:
            return {"ok": False, "error": f"decode failed: {e}"}

        # 임시 PDF 저장 + 변환
        upload_dir = os.path.join(BASE_DIR, "uploads", "pdfs")
        os.makedirs(upload_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = re.sub(r'[^\w\s.\-_가-힣]', '_', filename).replace(".pdf", "")
        pdf_dir = os.path.join(upload_dir, f"{ts}_{safe_name}")
        os.makedirs(pdf_dir, exist_ok=True)
        pdf_path = os.path.join(pdf_dir, "source.pdf")
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)

        try:
            doc = fitz.open(pdf_path)
            total_pages = len(doc)
            target_pages = self._parse_page_range(page_spec, total_pages)
            log(f"PDF_SPLIT {filename} total={total_pages} target={target_pages} dpi={dpi}")

            results = []
            for pnum in target_pages:
                page = doc[pnum - 1]
                # DPI 설정
                mat = fitz.Matrix(dpi / 72, dpi / 72)
                pix = page.get_pixmap(matrix=mat)
                img_path = os.path.join(pdf_dir, f"page_{pnum:03d}.png")
                pix.save(img_path)
                results.append({"page": pnum, "path": img_path})
            doc.close()

            log(f"PDF_SPLIT done: {len(results)} pages")
            return {"ok": True, "totalPages": total_pages, "pages": results, "pdfPath": pdf_path}
        except Exception as e:
            log(f"PDF_SPLIT ERROR: {e}")
            return {"ok": False, "error": str(e)}

    # ── Auto State (항상 최신 상태를 DB에 보관) ──

    def _state_save(self, body):
        """현재 캔버스 상태를 __current__ 프로젝트로 자동 저장"""
        now = datetime.now().isoformat()
        data = json.dumps(body, ensure_ascii=False)
        existing = db_exec("SELECT id FROM projects WHERE id='__current__'", fetchone=True)
        if existing:
            db_exec("UPDATE projects SET data=?, modified=? WHERE id='__current__'", (data, now))
        else:
            db_exec("INSERT INTO projects (id, name, data, created, modified) VALUES (?,?,?,?,?)",
                    ("__current__", "__current__", data, now, now))
        return {"ok": True}

    def _state_load(self):
        """__current__ 프로젝트에서 상태 복원"""
        row = db_exec("SELECT data FROM projects WHERE id='__current__'", fetchone=True)
        if row:
            return {"ok": True, "state": json.loads(row["data"])}
        return {"ok": True, "state": None}

    # ── tmux Controls ──

    def _send_to_pane(self, target, command):
        cmd_len = len(command)
        if '\n' in command or cmd_len > 200:
            CHUNK = 500
            for i in range(0, cmd_len, CHUNK):
                r = run_tmux("send-keys", "-l", "-t", target, command[i:i+CHUNK])
                if not r["ok"]: return r
        else:
            r = run_tmux("send-keys", "-l", "-t", target, command)
            if not r["ok"]: return r
        return run_tmux("send-keys", "-t", target, "Enter")

    def _send_command(self, body):
        pane = body.get("pane", "all")
        command = body.get("command", "")
        if not command: return {"ok": False, "error": "no command"}
        if pane == "all":
            r = run_tmux("list-panes", "-t", SESSION_NAME, "-F", "#{pane_index}")
            if not r["ok"] or not r["stdout"]: return r
            results = [self._send_to_pane(f"{SESSION_NAME}:.{idx.strip()}", command) for idx in r["stdout"].strip().split("\n")]
            failed = [r for r in results if not r["ok"]]
            return {"ok": len(failed) == 0, "sent": len(results), "failed": len(failed)}
        return self._send_to_pane(f"{SESSION_NAME}:.{pane}", command)

    def _split(self, body):
        d = body.get("direction", "h")
        return run_tmux("split-window", "-h" if d == "h" else "-v", "-t", body.get("target", SESSION_NAME))

    def _layout(self, body):
        return run_tmux("select-layout", "-t", SESSION_NAME, body.get("layout", "tiled"))

    def _kill_pane(self, body):
        return run_tmux("kill-pane", "-t", f"{SESSION_NAME}:.{body.get('pane', '')}")

    def _new_window(self, body):
        args = ["new-window", "-t", SESSION_NAME]
        name = body.get("name", "")
        if name: args += ["-n", name]
        return run_tmux(*args)

    def _select_window(self, body):
        return run_tmux("select-window", "-t", f"{SESSION_NAME}:{body.get('index', 0)}")

    def _preset(self, body):
        count = body.get("count", 2)
        layout = body.get("layout", "tiled")
        r = run_tmux("list-panes", "-t", SESSION_NAME, "-F", "#{pane_index}")
        if not r["ok"]: return r
        current = len(r["stdout"].strip().split("\n")) if r["stdout"].strip() else 1
        for _ in range(count - current):
            run_tmux("split-window", "-t", SESSION_NAME)
            run_tmux("select-layout", "-t", SESSION_NAME, "tiled")
        while current > count:
            run_tmux("kill-pane", "-t", f"{SESSION_NAME}:.{current - 1}")
            current -= 1
        return run_tmux("select-layout", "-t", SESSION_NAME, layout)

    def _add_pane(self, body):
        r = run_tmux("split-window", "-t", SESSION_NAME)
        if r["ok"]:
            run_tmux("select-layout", "-t", SESSION_NAME, "tiled")
            r2 = run_tmux("list-panes", "-t", SESSION_NAME, "-F", "#{pane_index}")
            if r2["ok"]:
                indices = [int(x) for x in r2["stdout"].strip().split("\n")]
                return {"ok": True, "paneIndex": max(indices), "total": len(indices)}
        return r

    def _setup_session(self, body):
        """N개 패널 생성 + 각 패널에 claude 자동 실행"""
        count = int(body.get("count", 1))
        launch_claude = body.get("launchClaude", True)
        log(f"SETUP_SESSION count={count} launchClaude={launch_claude}")

        # 1. 세션 리셋 (clean slate)
        run_tmux("kill-server")
        time.sleep(1)
        r = run_tmux("new-session", "-d", "-s", SESSION_NAME, "-c", os.path.expanduser("~"))
        if not r["ok"]:
            return {"ok": False, "error": "session create failed"}

        # 2. count - 1개 패널 추가 (이미 1개 있음)
        for _ in range(count - 1):
            run_tmux("split-window", "-t", SESSION_NAME)
            run_tmux("select-layout", "-t", SESSION_NAME, "tiled")
        run_tmux("select-layout", "-t", SESSION_NAME, "tiled")

        # 3. 각 패널에 claude 명령 전송
        if launch_claude:
            # -p 한번 실행으로 trust 디렉토리 사전 등록
            try:
                env = get_claude_env()
                subprocess.run(
                    ["claude", "-p", "ok", "--dangerously-skip-permissions"],
                    capture_output=True, text=True, timeout=15, env=env, cwd=os.path.expanduser("~")
                )
                log("SETUP_SESSION trust pre-registered via -p")
            except Exception as e:
                log(f"SETUP_SESSION trust pre-register failed: {e}")

            time.sleep(0.5)
            r = run_tmux("list-panes", "-t", SESSION_NAME, "-F", "#{pane_index}")
            if r["ok"]:
                pane_indices = [idx.strip() for idx in r["stdout"].strip().split("\n")]
                for idx in pane_indices:
                    target = f"{SESSION_NAME}:.{idx}"
                    cmd = "source ~/.nvm/nvm.sh && claude --dangerously-skip-permissions"
                    run_tmux("send-keys", "-t", target, cmd, "Enter")
                # 백그라운드: trust 다이얼로그 적극 자동 승인
                def auto_trust():
                    # 30초 동안 1초마다 모든 패널에 "1\n" 반복 전송
                    for sec in range(30):
                        time.sleep(1)
                        for idx in pane_indices:
                            target = f"{SESSION_NAME}:.{idx}"
                            r = run_tmux("capture-pane", "-t", target, "-p")
                            if r["ok"] and ("trust this folder" in r["stdout"] or "Yes, I trust" in r["stdout"] or "Do you trust" in r["stdout"]):
                                run_tmux("send-keys", "-t", target, "1", "Enter")
                                log(f"AUTO_TRUST pane={idx} sec={sec}")
                                time.sleep(0.3)
                threading.Thread(target=auto_trust, daemon=True).start()
                log(f"SETUP_SESSION launched claude in {len(pane_indices)} panes + auto-trust")
                return {"ok": True, "panes": pane_indices, "launched": len(pane_indices)}

        return {"ok": True}

    def _reset_session(self, body):
        log("RESET SESSION")
        run_tmux("kill-server")
        time.sleep(1)
        return run_tmux("new-session", "-d", "-s", SESSION_NAME, "-c", os.path.expanduser("~"))

    def _pane_content(self, params):
        pane = params.get("pane", ["1"])[0]
        r = run_tmux("capture-pane", "-t", f"{SESSION_NAME}:.{pane}", "-p", "-S", "-")
        if not r["ok"]: return r
        lines = r["stdout"].split("\n") if r["stdout"] else []
        return {"ok": True, "content": r["stdout"], "lines": lines, "lineCount": len(lines)}

    def _pane_prompt(self, params):
        pane = params.get("pane", ["1"])[0]
        r = run_tmux("capture-pane", "-t", f"{SESSION_NAME}:.{pane}", "-p")
        if not r["ok"]: return r
        content_lines = [l for l in r["stdout"].split("\n") if l.strip()
                         and 'bypass permissions' not in l and 'shift+tab' not in l and '⏵⏵' not in l
                         and all(c not in '─━═' for c in l.strip()[:3] if l.strip())]
        # 구분선 필터 보정
        content_lines = [l for l in content_lines if not (l.strip() and all(c in '─━═' for c in l.strip()))]
        idle = False
        for cl in content_lines[-3:]:
            if re.search(r'❯|~\$\s*$|\$\s*$', cl): idle = True; break
        return {"ok": True, "idle": idle, "lastLines": content_lines[-3:] if content_lines else []}

    # ── Response ──

    def _json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def log_message(self, format, *args):
        pass  # 조용한 HTTP 로그


class ThreadedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

if __name__ == "__main__":
    os.chdir(BASE_DIR)
    server = ThreadedServer(("0.0.0.0", PORT), TmuxHandler)
    print(f"╔══════════════════════════════════════╗")
    print(f"║  Canvas Server on ::{PORT}             ║")
    print(f"║  DB: {DB_PATH}")
    print(f"╚══════════════════════════════════════╝")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutdown")
        server.server_close()
