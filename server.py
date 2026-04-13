#!/usr/bin/env python3
"""Gil's FlowDesk — Visual AI Workflow Editor Server (SQLite/PostgreSQL + Claude CLI)"""

import subprocess, json, os, re, uuid, sys, time, sqlite3, threading, hashlib, secrets, tempfile
try:
    import psycopg2
    import psycopg2.extras
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False
from datetime import datetime, timedelta
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

PORT = 8888
SESSION_NAME = "main"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

def load_config():
    """config.json에서 설정을 로드. 없으면 기본값 생성."""
    defaults = {
        "db_path": os.path.join(BASE_DIR, "canvas.db"),
        "port": 8888,
        "uploads_dir": os.path.join(BASE_DIR, "uploads")
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            # 설정 파일의 값으로 defaults 업데이트
            for k, v in cfg.items():
                defaults[k] = v
        except Exception as e:
            print(f"[WARN] config.json 읽기 실패: {e}, 기본값 사용")
    else:
        # 최초 실행: 기본 config.json 생성
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(defaults, f, indent=2, ensure_ascii=False)
        print(f"[+] config.json 생성됨: {CONFIG_PATH}")
    return defaults

CONFIG = load_config()
# 환경변수가 있으면 config.json보다 우선 (Docker 배포 지원)
DB_TYPE = os.environ.get("DB_TYPE") or CONFIG.get("db_type", "sqlite")
DB_PATH = os.environ.get("DB_PATH") or CONFIG.get("db_path", os.path.join(BASE_DIR, "canvas.db"))
PORT = int(os.environ.get("PORT") or CONFIG.get("port", 8888))
UPLOADS_DIR = os.environ.get("UPLOADS_DIR") or CONFIG.get("uploads_dir", os.path.join(BASE_DIR, "uploads"))
# PostgreSQL 환경변수 오버라이드
if os.environ.get("DB_HOST"): CONFIG["db_host"] = os.environ["DB_HOST"]
if os.environ.get("DB_PORT"): CONFIG["db_port"] = int(os.environ["DB_PORT"])
if os.environ.get("DB_NAME"): CONFIG["db_name"] = os.environ["DB_NAME"]
if os.environ.get("DB_USER"): CONFIG["db_user"] = os.environ["DB_USER"]
if os.environ.get("DB_PASSWORD"): CONFIG["db_password"] = os.environ["DB_PASSWORD"]

if DB_TYPE == "postgresql" and not HAS_PSYCOPG2:
    print("[FATAL] db_type is 'postgresql' but psycopg2 is not installed.")
    print("        Install it: pip install psycopg2-binary")
    sys.exit(1)

def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# ═══════════════════════════════════════
# DB Lock (동시 접속 방지)
# ═══════════════════════════════════════
import socket, platform, atexit

if DB_TYPE == "postgresql":
    LOCK_PATH = os.path.join(CONFIG.get("uploads_dir", BASE_DIR), ".server.lock")
else:
    LOCK_PATH = DB_PATH + ".lock"
LOCK_HEARTBEAT = 15  # 초

def _get_machine_id():
    hostname = socket.gethostname()
    user = os.environ.get("USER", os.environ.get("USERNAME", "unknown"))
    return f"{user}@{hostname}"

def _read_lock():
    """잠금 파일 읽기. {machine, pid, time} 반환 또는 None."""
    if not os.path.exists(LOCK_PATH):
        return None
    try:
        with open(LOCK_PATH, "r") as f:
            return json.load(f)
    except:
        return None

def _write_lock():
    """잠금 파일 생성/갱신."""
    lock_dir = os.path.dirname(LOCK_PATH)
    if lock_dir and not os.path.exists(lock_dir):
        os.makedirs(lock_dir, exist_ok=True)
    data = {
        "machine": _get_machine_id(),
        "pid": os.getpid(),
        "started": datetime.now().isoformat(),
        "heartbeat": datetime.now().isoformat()
    }
    with open(LOCK_PATH, "w") as f:
        json.dump(data, f)
    return data

def _remove_lock():
    """잠금 파일 제거."""
    try:
        if os.path.exists(LOCK_PATH):
            lock = _read_lock()
            # 내 잠금인 경우에만 제거
            if lock and lock.get("machine") == _get_machine_id() and lock.get("pid") == os.getpid():
                os.remove(LOCK_PATH)
                log("LOCK released")
    except:
        pass

def acquire_lock():
    """잠금 획득 시도. 실패 시 에러 메시지와 함께 종료."""
    me = _get_machine_id()
    lock = _read_lock()

    if lock:
        other = lock.get("machine", "?")
        hb = lock.get("heartbeat", "")

        # 같은 머신의 같은 PID → 이전 실행의 잔여 잠금 (무시)
        if other == me and lock.get("pid") == os.getpid():
            pass
        # 같은 머신의 다른 PID → 해당 프로세스가 살아있는지 확인
        elif other == me:
            try:
                os.kill(lock["pid"], 0)  # 프로세스 존재 확인
                print(f"\n╔══════════════════════════════════════════╗")
                print(f"║  ⚠️  이 PC에서 이미 서버가 실행 중입니다    ║")
                print(f"║  PID: {lock['pid']}                              ║")
                print(f"╚══════════════════════════════════════════╝")
                sys.exit(1)
            except OSError:
                log(f"LOCK stale (same machine, dead PID {lock['pid']}), overriding")
        else:
            # 다른 머신 → 하트비트 확인 (60초 초과하면 stale)
            try:
                last_hb = datetime.fromisoformat(hb)
                age = (datetime.now() - last_hb).total_seconds()
                if age < 60:
                    print(f"\n╔══════════════════════════════════════════════════╗")
                    print(f"║  ⛔ 다른 PC에서 사용 중 — 동시 접속 차단           ║")
                    print(f"║                                                  ║")
                    print(f"║  사용자: {other:<40s}║")
                    print(f"║  시작:   {lock.get('started','?'):<40s}║")
                    print(f"║  마지막: {hb:<40s}║")
                    print(f"║                                                  ║")
                    print(f"║  해당 PC에서 서버를 먼저 종료해주세요.              ║")
                    print(f"║  또는 잠금 파일을 수동 삭제:                       ║")
                    print(f"║  rm {LOCK_PATH:<44s}║")
                    print(f"╚══════════════════════════════════════════════════╝")
                    sys.exit(1)
                else:
                    log(f"LOCK stale ({other}, {age:.0f}s ago), overriding")
            except:
                log("LOCK corrupt, overriding")

    # 잠금 획득
    _write_lock()
    atexit.register(_remove_lock)
    log(f"LOCK acquired by {me} (PID {os.getpid()})")

    # 하트비트 스레드: 15초마다 잠금 파일 갱신
    def heartbeat():
        while True:
            time.sleep(LOCK_HEARTBEAT)
            try:
                lock = _read_lock()
                if lock and lock.get("pid") == os.getpid():
                    lock["heartbeat"] = datetime.now().isoformat()
                    with open(LOCK_PATH, "w") as f:
                        json.dump(lock, f)
            except:
                pass
    threading.Thread(target=heartbeat, daemon=True).start()

# ═══════════════════════════════════════
# Database (SQLite / PostgreSQL)
# ═══════════════════════════════════════
def _is_pg():
    return DB_TYPE == "postgresql"

def get_db():
    if _is_pg():
        conn = psycopg2.connect(
            host=CONFIG.get("db_host", "127.0.0.1"),
            port=CONFIG.get("db_port", 5432),
            dbname=CONFIG.get("db_name", "canvas_db"),
            user=CONFIG.get("db_user", "canvas"),
            password=CONFIG.get("db_password", ""),
            connect_timeout=10
        )
        conn.autocommit = True
        return conn
    else:
        # SQLite fallback
        db_dir = os.path.dirname(DB_PATH)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        if '/mnt/' in DB_PATH or '\\\\' in DB_PATH:
            conn.execute("PRAGMA journal_mode=DELETE")
        else:
            conn.execute("PRAGMA journal_mode=WAL")
        return conn

def _pg_adapt_sql(sql):
    """Convert SQLite-style SQL to PostgreSQL-compatible SQL."""
    # Parameter placeholder: ? → %s
    adapted = sql.replace("?", "%s")
    # datetime('now','-30 days') → NOW() - INTERVAL '30 days'
    adapted = re.sub(
        r"datetime\(\s*'now'\s*,\s*'-(\d+)\s+days'\s*\)",
        r"NOW() - INTERVAL '\1 days'",
        adapted
    )
    return adapted

def init_db():
    db = get_db()
    if _is_pg():
        cur = db.cursor()
        pg_tables = [
            """CREATE TABLE IF NOT EXISTS project_folders (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                icon TEXT DEFAULT '📂',
                color TEXT DEFAULT '',
                sort_order INTEGER DEFAULT 0,
                created TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                data TEXT NOT NULL,
                created TEXT NOT NULL,
                modified TEXT NOT NULL,
                favorite INTEGER DEFAULT 0,
                folder_id INTEGER
            )""",
            """CREATE TABLE IF NOT EXISTS temps (
                id SERIAL PRIMARY KEY,
                name TEXT,
                data TEXT NOT NULL,
                date TEXT NOT NULL,
                created TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS memo_folders (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                icon TEXT DEFAULT '📁',
                sort_order INTEGER DEFAULT 0,
                created TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS memos (
                id SERIAL PRIMARY KEY,
                name TEXT,
                content TEXT DEFAULT '',
                folder_id INTEGER,
                is_temp INTEGER DEFAULT 1,
                created TEXT NOT NULL,
                modified TEXT NOT NULL,
                FOREIGN KEY (folder_id) REFERENCES memo_folders(id)
            )""",
            """CREATE TABLE IF NOT EXISTS executions (
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
            )""",
            """CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                parent_exec_id TEXT,
                node_id TEXT NOT NULL,
                node_name TEXT NOT NULL,
                title TEXT,
                account_id INTEGER,
                created TEXT NOT NULL,
                FOREIGN KEY (parent_exec_id) REFERENCES executions(id)
            )""",
            """CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                conv_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                ts TEXT NOT NULL,
                FOREIGN KEY (conv_id) REFERENCES conversations(id)
            )""",
            """CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                created TEXT NOT NULL,
                expires TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS trash (
                id SERIAL PRIMARY KEY,
                original_table TEXT NOT NULL,
                original_id TEXT NOT NULL,
                name TEXT,
                data TEXT NOT NULL,
                deleted_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS claude_accounts (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                credentials TEXT NOT NULL,
                active INTEGER DEFAULT 0,
                priority INTEGER DEFAULT 0,
                created TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS fav_folders (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                parent_id INTEGER,
                sort_order INTEGER DEFAULT 0,
                color TEXT DEFAULT '',
                icon TEXT DEFAULT '⭐',
                created TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS fav_items (
                id SERIAL PRIMARY KEY,
                folder_id INTEGER NOT NULL,
                memo_id INTEGER NOT NULL,
                sort_order INTEGER DEFAULT 0,
                created TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS project_attachments (
                id SERIAL PRIMARY KEY,
                project_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                target_id TEXT NOT NULL,
                sort_order INTEGER DEFAULT 0,
                created TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS icons (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                data TEXT NOT NULL,
                kind TEXT DEFAULT 'image',
                created TEXT NOT NULL
            )""",
        ]
        for ddl in pg_tables:
            cur.execute(ddl)
        # Indexes
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_exec_node ON executions(node_id)",
            "CREATE INDEX IF NOT EXISTS idx_conv_node ON conversations(node_id)",
            "CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conv_id)",
        ]:
            cur.execute(idx_sql)
        # Migration columns (PostgreSQL: check column existence before ALTER)
        alter_checks = [
            ("projects", "favorite", "ALTER TABLE projects ADD COLUMN favorite INTEGER DEFAULT 0"),
            ("projects", "folder_id", "ALTER TABLE projects ADD COLUMN folder_id INTEGER"),
            ("memos", "pinned", "ALTER TABLE memos ADD COLUMN pinned INTEGER DEFAULT 0"),
            ("memos", "color", "ALTER TABLE memos ADD COLUMN color TEXT DEFAULT ''"),
            ("memo_folders", "color", "ALTER TABLE memo_folders ADD COLUMN color TEXT DEFAULT ''"),
            ("memo_folders", "parent_id", "ALTER TABLE memo_folders ADD COLUMN parent_id INTEGER"),
            ("claude_accounts", "priority", "ALTER TABLE claude_accounts ADD COLUMN priority INTEGER DEFAULT 0"),
            ("conversations", "account_id", "ALTER TABLE conversations ADD COLUMN account_id INTEGER"),
            ("project_folders", "parent_id", "ALTER TABLE project_folders ADD COLUMN parent_id INTEGER"),
            ("messages", "chat_only", "ALTER TABLE messages ADD COLUMN chat_only INTEGER DEFAULT 1"),
            ("claude_accounts", "rate_limited_until", "ALTER TABLE claude_accounts ADD COLUMN rate_limited_until TEXT"),
            ("claude_accounts", "rate_limit_reason", "ALTER TABLE claude_accounts ADD COLUMN rate_limit_reason TEXT"),
            ("fav_items", "kind", "ALTER TABLE fav_items ADD COLUMN kind TEXT DEFAULT 'project'"),
            ("fav_items", "target_id", "ALTER TABLE fav_items ADD COLUMN target_id TEXT"),
            ("fav_folders", "kind", "ALTER TABLE fav_folders ADD COLUMN kind TEXT DEFAULT 'project'"),
        ]
        for tbl, col, alter_sql in alter_checks:
            cur.execute("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name=%s AND column_name=%s
            """, (tbl, col))
            if not cur.fetchone():
                cur.execute(alter_sql)
        # NOT NULL 제거 — fav_items.memo_id를 nullable로 (project 항목 INSERT 가능하게)
        try:
            cur.execute("ALTER TABLE fav_items ALTER COLUMN memo_id DROP NOT NULL")
        except Exception:
            pass  # 이미 nullable이거나 컬럼 없음
        cur.close()
        db.close()
    else:
        # SQLite
        db.executescript("""
        CREATE TABLE IF NOT EXISTS project_folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            icon TEXT DEFAULT '📂',
            color TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0,
            created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            data TEXT NOT NULL,
            created TEXT NOT NULL,
            modified TEXT NOT NULL,
            favorite INTEGER DEFAULT 0,
            folder_id INTEGER
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
            account_id INTEGER,
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
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            created TEXT NOT NULL,
            expires TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trash (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_table TEXT NOT NULL,
            original_id TEXT NOT NULL,
            name TEXT,
            data TEXT NOT NULL,
            deleted_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated TEXT
        );
        CREATE TABLE IF NOT EXISTS claude_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            credentials TEXT NOT NULL,
            active INTEGER DEFAULT 0,
            priority INTEGER DEFAULT 0,
            created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS fav_folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            parent_id INTEGER,
            sort_order INTEGER DEFAULT 0,
            color TEXT DEFAULT '',
            icon TEXT DEFAULT '⭐',
            created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS fav_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            folder_id INTEGER NOT NULL,
            memo_id INTEGER NOT NULL,
            sort_order INTEGER DEFAULT 0,
            created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS project_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            target_id TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0,
            created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS icons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            data TEXT NOT NULL,
            kind TEXT DEFAULT 'image',
            created TEXT NOT NULL
        );
        """)
        try:
            db.execute("ALTER TABLE projects ADD COLUMN favorite INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        for stmt in [
            "ALTER TABLE memos ADD COLUMN pinned INTEGER DEFAULT 0",
            "ALTER TABLE memos ADD COLUMN color TEXT DEFAULT ''",
            "ALTER TABLE memo_folders ADD COLUMN color TEXT DEFAULT ''",
            "ALTER TABLE memo_folders ADD COLUMN parent_id INTEGER",
            "ALTER TABLE claude_accounts ADD COLUMN priority INTEGER DEFAULT 0",
            "ALTER TABLE project_folders ADD COLUMN parent_id INTEGER",
            "ALTER TABLE messages ADD COLUMN chat_only INTEGER DEFAULT 1",
            "ALTER TABLE claude_accounts ADD COLUMN rate_limited_until TEXT",
            "ALTER TABLE claude_accounts ADD COLUMN rate_limit_reason TEXT",
            "ALTER TABLE fav_items ADD COLUMN kind TEXT DEFAULT 'project'",
            "ALTER TABLE fav_items ADD COLUMN target_id TEXT",
            "ALTER TABLE fav_folders ADD COLUMN kind TEXT DEFAULT 'project'",
        ]:
            try:
                db.execute(stmt)
            except sqlite3.OperationalError:
                pass
        db.commit()
        db.close()
    log(f"DB initialized ({DB_TYPE})")

init_db()
db_lock = threading.Lock()

# ═══════════════════════════════════════
# Password hashing & default user
# ═══════════════════════════════════════
def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(32)
    hashed = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 100000)
    return hashed.hex(), salt

def _create_default_user():
    existing = db_exec("SELECT id FROM users WHERE username=?", ('gilhojong',), fetchone=True)
    if not existing:
        pw_hash, salt = hash_password('!!Il197119!!')
        db_exec("INSERT INTO users (username, password_hash, salt, created) VALUES (?,?,?,?)",
                ('gilhojong', pw_hash, salt, datetime.now().isoformat()))
        log("Default user created: gilhojong")

def db_exec(sql, params=(), fetch=False, fetchone=False):
    with db_lock:
        db = get_db()
        is_pg = _is_pg()
        try:
            if is_pg:
                adapted_sql = _pg_adapt_sql(sql)
                # For INSERT on SERIAL tables, add RETURNING id to get lastrowid
                need_returning = False
                if adapted_sql.strip().upper().startswith("INSERT") and not fetch and not fetchone:
                    # Tables with SERIAL id: temps, memo_folders, memos, messages
                    serial_tables = ("temps", "memo_folders", "memos", "messages", "claude_accounts",
                                     "project_folders", "fav_folders", "fav_items", "project_attachments", "icons")
                    sql_upper = adapted_sql.upper()
                    if any(f"INTO {t.upper()}" in sql_upper for t in serial_tables):
                        if "RETURNING" not in sql_upper:
                            adapted_sql = adapted_sql.rstrip().rstrip(";") + " RETURNING id"
                            need_returning = True
                cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(adapted_sql, params or ())
                if fetchone:
                    row = cur.fetchone()
                    return dict(row) if row else None
                if fetch:
                    return [dict(r) for r in cur.fetchall()]
                if need_returning:
                    row = cur.fetchone()
                    cur.close()
                    db.close()
                    return row["id"] if row else None
                cur.close()
                db.close()
                return None
            else:
                cur = db.execute(sql, params)
                if fetchone:
                    row = cur.fetchone()
                    return dict(row) if row else None
                if fetch:
                    return [dict(r) for r in cur.fetchall()]
                db.commit()
                return cur.lastrowid
        except Exception as e:
            log(f"DB ERROR: {e}\n  SQL: {sql[:200]}")
            raise
        finally:
            try:
                db.close()
            except:
                pass

# ═══════════════════════════════════════
# Manual memo initialization
# ═══════════════════════════════════════
def _create_manual_memo():
    """Create the manual memo if it doesn't exist yet."""
    MANUAL_NAME = "📖 Gil's FlowDesk 매뉴얼"
    manual_check = db_exec("SELECT id FROM memos WHERE name=?", (MANUAL_NAME,), fetchone=True)
    if manual_check:
        return
    manual_content = """# Gil's FlowDesk — JSON 구조 매뉴얼

## 프로젝트 JSON 구조

프로젝트는 다음 구조의 JSON으로 저장/내보내기/가져오기됩니다:

```json
{
  "cx": 0,           // 캔버스 X 오프셋
  "cy": 0,           // 캔버스 Y 오프셋
  "zoom": 1,         // 줌 레벨
  "cwd": "",         // 작업 디렉토리 경로
  "wfName": "",      // 프로젝트 이름
  "workflowId": "",  // 프로젝트 ID
  "nodes": [],       // 노드 배열
  "connections": [],  // 연결선 배열
  "canvasElements": [] // 캔버스 요소 (이미지 등)
}
```

## 노드 (nodes) 구조

```json
{
  "id": "n1234_1",        // 고유 ID
  "name": "Agent1",       // 노드 이름 ({{name}}으로 참조)
  "type": "agent",        // 타입: agent, memo, input, trigger
  "x": 100, "y": 100,    // 캔버스 위치
  "w": 280, "h": 240,    // 크기 (픽셀)
  "collapsed": false,     // 접힌 상태
  "chatOnly": true,       // true=대화전용(도구X), false=CLI모드(도구O)
  "inputTemplate": "",    // 입력 텍스트 ({{노드명}} 변수 사용 가능)
  "outputCapture": "",    // 실행 결과 (AI 응답)
  "systemPrompt": "",     // 시스템 프롬프트 (선택)
  "images": [],           // 첨부 이미지 경로 배열
  "status": "idle",       // idle, running, complete, error
  "history": []           // 실행 이력
}
```

## 노드 타입별 동작

| 타입 | 아이콘 | 실행 방식 |
|------|--------|-----------|
| agent | 🤖 | claude -p로 AI에 전송, 응답을 outputCapture에 저장 |
| memo | 📝 | inputTemplate을 그대로 outputCapture에 복사 (AI 호출 없음) |
| input | 📎 | memo와 동일 (데이터 입력용) |
| trigger | ⚡ | 즉시 complete, 하위 노드 실행 트리거 |

## 변수 시스템 ({{노드명}})

노드의 inputTemplate에 `{{다른노드이름}}`을 넣으면, 실행 시 해당 노드의 outputCapture 내용으로 자동 치환됩니다.

예시:
- 메모1 (type: memo): inputTemplate = "한국 체육과학 연구..."
- Agent1 (type: agent): inputTemplate = "다음 내용을 요약해: {{메모1}}"
  → 실행 시 {{메모1}}이 메모1의 내용으로 바뀌어 AI에 전송

## 연결선 (connections) 구조

```json
{
  "id": "conn_1",
  "sid": "n1234_1",  // 출발 노드 ID
  "tid": "n1234_2"   // 도착 노드 ID
}
```

연결선은 실행 순서를 결정합니다:
- 상위 노드가 모두 complete여야 하위 노드 실행 가능
- {{변수}} 치환과는 독립적 (변수명만 맞으면 연결 없이도 치환됨)

## 캔버스 요소 (canvasElements) 구조

```json
{
  "id": "ce_1",
  "type": "image",
  "x": 100, "y": 100,
  "w": 200, "h": 150,
  "src": "data:image/png;base64,...",
  "nodeType": "agent",     // 노드로 전환 시 설정
  "inputTemplate": "",
  "outputCapture": ""
}
```

## AI에게 프로젝트 생성 요청하기

이 JSON 구조를 AI에게 알려주면, 원하는 워크플로우를 JSON으로 생성받을 수 있습니다.

예시 프롬프트:
"다음 JSON 구조로 3단계 연구 분석 워크플로우를 만들어줘:
1. 메모 노드에 연구 데이터 입력
2. Agent1이 데이터 분석
3. Agent2가 분석 결과를 보고서로 작성
각 노드를 {{변수}}로 연결해줘."

생성된 JSON을 .json 파일로 저장 후 캔버스에 드래그앤드롭하면 바로 사용 가능합니다.

## 주요 API 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| POST | /api/node-exec | 노드 실행 (claude -p) |
| GET | /api/node-check?nodeId= | 실행 완료 확인 |
| POST | /api/project/save | 프로젝트 저장 |
| GET | /api/project/load?id= | 프로젝트 불러오기 |
| POST | /api/memo/save | 메모 저장 |
| GET | /api/memo/list | 메모 목록 |
| POST | /api/upload | 이미지 업로드 |

## 토큰 제한

- 한국어 기준 약 23,000자 이상의 입력은 타임아웃 가능
- 긴 텍스트는 메모를 분할하고 중간 요약 Agent를 넣어 처리
- 노드에 23,000자 초과 시 빨간 펄싱 경고 표시
"""
    # Create a manual folder
    manual_folder = db_exec("SELECT id FROM memo_folders WHERE name='📖 매뉴얼'", fetchone=True)
    if not manual_folder:
        fid = db_exec("INSERT INTO memo_folders (name, icon, color, created) VALUES (?,?,?,?)",
                      ("📖 매뉴얼", "📖", "#3B82F6", datetime.now().isoformat()))
    else:
        fid = manual_folder["id"]
    db_exec("INSERT INTO memos (name, content, folder_id, is_temp, pinned, created, modified) VALUES (?,?,?,?,?,?,?)",
            ("📖 Gil's FlowDesk 매뉴얼", manual_content, fid, 0, 1, datetime.now().isoformat(), datetime.now().isoformat()))
    log("Manual memo created")

_create_manual_memo()
_create_default_user()

# ═══════════════════════════════════════
# Claude credentials sync (DB → ~/.claude/.credentials.json)
# ═══════════════════════════════════════
def _sync_claude_credentials():
    """DB → ~/.claude/.credentials.json 동기화 (서버 시작 시).
    우선순위: claude_accounts(active=1) → 레거시 system_settings.claude_credentials."""
    try:
        # 1) New system: claude_accounts active row
        row = db_exec("SELECT credentials FROM claude_accounts WHERE active=1 LIMIT 1", fetchone=True)
        if row and row.get("credentials"):
            claude_dir = os.path.expanduser("~/.claude")
            os.makedirs(claude_dir, exist_ok=True)
            creds_path = os.path.join(claude_dir, ".credentials.json")
            with open(creds_path, "w", encoding="utf-8") as f:
                f.write(row["credentials"])
            try:
                os.chmod(creds_path, 0o600)
            except Exception:
                pass
            log("Claude credentials synced from active account")
            return
        # 2) Legacy: old system_settings key
        row = db_exec("SELECT value FROM system_settings WHERE key='claude_credentials'", fetchone=True)
        if row and row.get("value"):
            claude_dir = os.path.expanduser("~/.claude")
            os.makedirs(claude_dir, exist_ok=True)
            creds_path = os.path.join(claude_dir, ".credentials.json")
            if not os.path.exists(creds_path):
                with open(creds_path, "w", encoding="utf-8") as f:
                    f.write(row["value"])
                try:
                    os.chmod(creds_path, 0o600)
                except Exception:
                    pass
                log("Claude credentials synced from legacy system_settings")
    except Exception as e:
        log(f"Claude credentials sync failed: {e}")

# 서버 시작 시 항상 동기화 시도 (SQLite/PostgreSQL 공통)
_sync_claude_credentials()

# ═══════════════════════════════════════
# Per-account credential directories (multi-account simultaneous use)
# ═══════════════════════════════════════
def _get_account_dir(account_id):
    """각 계정의 CLAUDE_CONFIG_DIR 경로 반환. 없으면 생성."""
    base = os.path.join(tempfile.gettempdir(), "flowdesk-accts", str(account_id))
    os.makedirs(base, exist_ok=True)
    return base

def _sync_account_to_dir(account_id, credentials):
    """계정 credentials를 전용 디렉토리에 동기화.
    setup-token 토큰 (refreshToken 없음)은 파일 안 씀 (env var로만 사용)."""
    d = _get_account_dir(account_id)
    creds_path = os.path.join(d, ".credentials.json")
    # setup-token 여부 확인
    is_setup_token = False
    try:
        parsed = json.loads(credentials)
        oauth = parsed.get("claudeAiOauth", {})
        if oauth.get("accessToken") and not oauth.get("refreshToken"):
            is_setup_token = True
    except Exception:
        pass

    if is_setup_token:
        # 기존 credentials.json이 있으면 삭제 (setup-token이면 파일 쓰면 CLI가 혼동함)
        try:
            if os.path.exists(creds_path):
                os.remove(creds_path)
        except Exception:
            pass
    else:
        with open(creds_path, "w", encoding="utf-8") as f:
            f.write(credentials)
        try:
            os.chmod(creds_path, 0o600)
        except Exception:
            pass
    return d

def _sync_all_accounts():
    try:
        rows = db_exec("SELECT id, credentials FROM claude_accounts", fetch=True) or []
        for row in rows:
            _sync_account_to_dir(row["id"], row["credentials"])
        log(f"Synced {len(rows)} Claude accounts to temp dirs")
    except Exception as e:
        log(f"Sync accounts failed: {e}")

_sync_all_accounts()

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
    """claude CLI 명령 구성 — 고급 옵션 지원. prompt는 stdin으로 전달."""
    opts = opts or {}
    cmd = ["claude", "-p", "--dangerously-skip-permissions", "--effort", "max"]

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

    # 이미지 파일 경로가 있으면 프롬프트에 추가 텍스트 준비
    images = opts.get("images", [])
    final_prompt = prompt
    if images:
        img_instructions = "\n\n[첨부 이미지 파일 — Read 도구로 읽어서 분석하세요]:\n"
        img_instructions += "\n".join(f"- {img}" for img in images)
        final_prompt = prompt + img_instructions

    return cmd, final_prompt

# ═══════════════════════════════════════
# Claude login subprocess (web-based OAuth)
# ═══════════════════════════════════════
_claude_login_proc = None   # PID (int)
_claude_login_url = None
_claude_login_output = []
_claude_login_master_fd = None
_claude_login_lock = threading.Lock()

def _is_claude_proc_alive():
    global _claude_login_proc
    if not _claude_login_proc:
        return False
    try:
        wpid, _ = os.waitpid(_claude_login_proc, os.WNOHANG)
        if wpid == 0:
            return True
        return False
    except ChildProcessError:
        return False
    except Exception:
        return False

def get_claude_env():
    # nvm 또는 시스템 경로 자동 감지
    nvm_bin = os.path.expanduser("~/.nvm/versions/node/v22.22.2/bin")
    if not os.path.isdir(nvm_bin):
        # nvm 없으면 시스템 경로 사용
        import shutil
        claude_path = shutil.which("claude")
        nvm_bin = os.path.dirname(claude_path) if claude_path else "/usr/bin"
    env = os.environ.copy()
    env["PATH"] = nvm_bin + ":" + env.get("PATH", "")
    return env

# ─── Rate-limit 감지 + 자동 폴백 ────────────────────────────────────────
import re as _re_rl
RATE_LIMIT_PATTERNS = [
    # (정규식, 라벨, 쿨다운 시간(시간))
    (r"weekly\s+(usage\s+)?limit", "주간 한도", 24*7),
    (r"daily\s+(usage\s+)?limit", "일일 한도", 24),
    (r"5[\s-]?hour(?:ly)?\s+(usage\s+)?limit", "5시간 한도", 5),
    (r"hourly\s+(usage\s+)?limit", "1시간 한도", 1),
    (r"usage\s+limit\s+reached", "사용 한도", 5),
    (r"rate\s+limit\s+exceeded", "Rate limit", 1),
    (r"too\s+many\s+requests", "Too many requests", 1),
    (r"quota\s+exceeded", "할당량 초과", 24),
    (r"reached\s+your\s+(usage|message|conversation)\s+limit", "메시지 한도", 5),
]
def detect_rate_limit(text):
    """claude CLI 출력(stdout+stderr)을 검사해 rate limit 발견 시 (라벨, 쿨다운시간) 반환. 없으면 None."""
    if not text: return None
    low = text.lower()
    for pat, label, hours in RATE_LIMIT_PATTERNS:
        if _re_rl.search(pat, low):
            # "resets at HH:MM" 또는 "try again in N hours" 패턴이 있으면 그 시간 사용 시도
            m = _re_rl.search(r"try\s+again\s+in\s+(\d+)\s+hour", low)
            if m: hours = int(m.group(1))
            return (label, hours)
    return None

def mark_rate_limited(account_id, label, cooldown_hours):
    """계정에 cooldown 마킹"""
    if not account_id: return
    until = (datetime.now() + timedelta(hours=cooldown_hours)).isoformat()
    try:
        db_exec("UPDATE claude_accounts SET rate_limited_until=?, rate_limit_reason=? WHERE id=?",
                (until, label, int(account_id)))
        log(f"[RATE-LIMIT] 계정 {account_id} → {label} ({cooldown_hours}h cooldown until {until[:19]})")
    except Exception as e:
        log(f"[RATE-LIMIT] mark failed: {e}")

def is_account_available(account_id):
    """rate_limited_until 이 미래면 사용 불가"""
    if not account_id: return True
    try:
        row = db_exec("SELECT rate_limited_until FROM claude_accounts WHERE id=?", (int(account_id),), fetchone=True)
        if not row or not row.get("rate_limited_until"): return True
        until_str = row["rate_limited_until"]
        until = datetime.fromisoformat(until_str)
        if datetime.now() >= until:
            # 만료됨 → 자동 해제
            db_exec("UPDATE claude_accounts SET rate_limited_until=NULL, rate_limit_reason=NULL WHERE id=?", (int(account_id),))
            return True
        return False
    except Exception as e:
        log(f"[RATE-LIMIT] check failed: {e}")
        return True

def pick_available_account(exclude_ids=None):
    """rotation_mode 따라 사용 가능한 계정 1개 골라서 반환. 없으면 None."""
    exclude_ids = set(exclude_ids or [])
    try:
        mode_row = db_exec("SELECT value FROM system_settings WHERE key='claude_rotation_mode'", fetchone=True)
        mode = mode_row["value"] if mode_row and mode_row.get("value") else "round-robin"
        accounts = db_exec("SELECT id FROM claude_accounts ORDER BY priority ASC, id ASC", fetch=True) or []
        if not accounts: return None
        # manual 모드면 active만 시도
        if mode == "manual":
            active = db_exec("SELECT id FROM claude_accounts WHERE active=1 LIMIT 1", fetchone=True)
            if active and active["id"] not in exclude_ids and is_account_available(active["id"]):
                return active["id"]
            return None
        # round-robin / sequential: 사용 가능한 첫 계정
        for a in accounts:
            if a["id"] in exclude_ids: continue
            if is_account_available(a["id"]):
                return a["id"]
        return None
    except Exception as e:
        log(f"[RATE-LIMIT] pick failed: {e}")
        return None


def run_claude_safe(cmd_builder_fn, account_id, run_cwd=None, timeout=600, max_fallback=3):
    """Claude CLI 호출 + rate-limit 자동 폴백.
    cmd_builder_fn() → (cmd, final_prompt). 한도 감지되면 다른 계정으로 재시도.
    Returns (stdout, used_account_id, fallback_log)"""
    tried = set()
    last_err = None
    cur_id = account_id
    for attempt in range(max_fallback + 1):
        if not cur_id or cur_id in tried:
            cur_id = pick_available_account(exclude_ids=tried)
            if not cur_id:
                return ("", None, f"❌ 사용 가능한 계정 없음 ({last_err or '전부 한도 초과'})")
        tried.add(cur_id)
        env = get_claude_env_for_account(cur_id)
        try:
            cmd, final_prompt = cmd_builder_fn()
        except Exception as e:
            return ("", cur_id, f"cmd build failed: {e}")
        log(f"[CLAUDE] attempt={attempt+1} account={cur_id}")
        try:
            result = subprocess.run(cmd, input=final_prompt, capture_output=True, text=True,
                                    timeout=timeout, env=env, cwd=run_cwd)
            combined = (result.stdout or "") + "\n" + (result.stderr or "")
            rl = detect_rate_limit(combined)
            if rl:
                label, hours = rl
                mark_rate_limited(cur_id, label, hours)
                last_err = f"계정 {cur_id} → {label} ({hours}h)"
                log(f"[CLAUDE] rate-limited: {last_err} → 다른 계정 폴백")
                cur_id = None
                continue
            return (result.stdout.strip(), cur_id, f"✓ 계정 {cur_id}" + (f" (이전 {len(tried)-1}개 한도 초과로 폴백)" if len(tried)>1 else ""))
        except subprocess.TimeoutExpired:
            return ("", cur_id, "timeout")
        except Exception as e:
            log(f"[CLAUDE] error: {e}")
            return ("", cur_id, str(e))
    return ("", cur_id, f"max retry ({last_err})")


def get_claude_env_for_account(account_id=None):
    """특정 계정의 env 반환.
    - 전체 OAuth (refreshToken 있음): credentials.json 파일 사용
    - setup-token (refreshToken 없음): CLAUDE_CODE_OAUTH_TOKEN env var만 사용 (파일 없는 빈 dir)
    - account_id 없으면 active=1 계정으로 폴백
    """
    env = get_claude_env()
    # account_id 없으면 활성 계정으로 폴백
    if not account_id:
        try:
            active = db_exec("SELECT id FROM claude_accounts WHERE active=1 LIMIT 1", fetchone=True)
            if active and active.get("id"):
                account_id = active["id"]
        except Exception:
            pass
    if not account_id:
        return env
    try:
        row = db_exec("SELECT credentials FROM claude_accounts WHERE id=?", (int(account_id),), fetchone=True)
        if not row or not row.get("credentials"):
            return env
        try:
            data = json.loads(row["credentials"])
        except Exception:
            return env
        oauth = data.get("claudeAiOauth", {})
        token = oauth.get("accessToken", "")
        refresh = oauth.get("refreshToken", "")
        if token and not refresh:
            # setup-token 토큰 → env var만 사용, credentials.json 없는 빈 dir
            empty_dir = os.path.join(tempfile.gettempdir(), "flowdesk-envonly", str(account_id))
            os.makedirs(empty_dir, exist_ok=True)
            # 기존 credentials.json이 있으면 삭제 (과거 잘못 저장된 것 포함)
            try:
                stale = os.path.join(empty_dir, ".claude", ".credentials.json")
                if os.path.exists(stale):
                    os.remove(stale)
            except Exception:
                pass
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
            env["CLAUDE_CONFIG_DIR"] = empty_dir
            env["HOME"] = empty_dir
        else:
            # 전체 OAuth → credentials.json 파일 기반
            account_dir = _get_account_dir(int(account_id))
            env["CLAUDE_CONFIG_DIR"] = account_dir
            env["HOME"] = account_dir
    except Exception as e:
        log(f"Failed to set account env: {e}")
    return env

# ═══════════════════════════════════════
# HTTP Handler
# ═══════════════════════════════════════
class TmuxHandler(SimpleHTTPRequestHandler):

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        p = parsed.path

        # Auth check endpoint (no auth required)
        if p == "/api/auth/check":
            self._json(self._auth_check())
            return

        # Auth middleware for API routes
        if p.startswith("/api/") and p not in ("/api/auth/login", "/api/auth/check"):
            if not self._check_auth():
                self._json({"ok": False, "error": "unauthorized"}, 401)
                return

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
        elif p == "/api/project-folder/list": self._json(self._project_folder_list())
        elif p == "/api/exec/list": self._json(self._exec_list(params))
        elif p == "/api/conv/list": self._json(self._conv_list(params))
        elif p == "/api/conv/messages": self._json(self._conv_messages(params))
        elif p == "/api/scratchpad/load": self._json(self._scratchpad_load())
        elif p == "/api/memo/pinned": self._json(self._memo_pinned())
        elif p == "/api/fav/folders": self._json(self._fav_folders(params))
        elif p == "/api/fav/items": self._json(self._fav_items(params))
        elif p == "/api/proj-attach/list": self._json(self._proj_attach_list(params))
        elif p == "/api/icons/list": self._json(self._icons_list())
        elif p == "/api/state/load": self._json(self._state_load(params))
        elif p == "/api/pane-content": self._json(self._pane_content(params))
        elif p == "/api/pane-prompt-check": self._json(self._pane_prompt(params))
        elif p == "/api/trash/list": self._json(self._trash_list())
        elif p == "/api/settings/get": self._json(self._settings_get())
        elif p == "/api/claude/accounts/list": self._json(self._claude_accounts_list())
        elif p == "/api/claude/login/status": self._json(self._claude_login_status())
        elif p == "/api/fs/browse": self._json(self._browse_path(params))
        elif p == "/api/fs/browse-system": self._json(self._fs_browse_system(params))
        elif p == "/api/fs/download":
            self._fs_download(params)
            return
        elif p.startswith("/uploads/"):
            # uploads 디렉토리가 외부 경로일 수 있으므로 직접 서빙
            file_path = os.path.join(UPLOADS_DIR, p[len("/uploads/"):])
            if os.path.isfile(file_path):
                import mimetypes
                mime = mimetypes.guess_type(file_path)[0] or 'application/octet-stream'
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", os.path.getsize(file_path))
                self.end_headers()
                with open(file_path, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404, "File not found")
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

        # Auth login endpoint (no auth required)
        if p == "/api/auth/login":
            self._handle_auth_login(body)
            return
        if p == "/api/auth/logout":
            self._handle_auth_logout(body)
            return

        # Auth middleware for all other POST API routes
        if p.startswith("/api/"):
            if not self._check_auth():
                self._json({"ok": False, "error": "unauthorized"}, 401)
                return

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
            "/api/scratchpad/save": self._scratchpad_save,
            "/api/memo/pin": self._memo_pin,
            "/api/fav/folder/save": self._fav_folder_save,
            "/api/fav/folder/delete": self._fav_folder_delete,
            "/api/fav/item/add": self._fav_item_add,
            "/api/fav/item/remove": self._fav_item_remove,
            "/api/project/date-folder": self._project_date_folder,
            "/api/proj-attach/add": self._proj_attach_add,
            "/api/proj-attach/remove": self._proj_attach_remove,
            "/api/icon/save": self._icon_save,
            "/api/icon/delete": self._icon_delete,
            "/api/project/star": self._project_star,
            "/api/project/move": self._project_move,
            "/api/project-folder/save": self._project_folder_save,
            "/api/project-folder/delete": self._project_folder_delete,
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
            "/api/trash/restore": self._trash_restore,
            "/api/trash/delete": self._trash_delete,
            "/api/trash/empty": self._trash_empty,
            "/api/settings/set": self._settings_set,
            "/api/settings/delete": self._settings_delete,
            "/api/claude/accounts/save": self._claude_account_save,
            "/api/claude/accounts/delete": self._claude_account_delete,
            "/api/claude/accounts/activate": self._claude_account_activate,
            "/api/claude/accounts/test": self._claude_account_test,
            "/api/claude/accounts/next": self._claude_next_account,
            "/api/claude/accounts/next-preview": self._claude_next_preview,
            "/api/claude/accounts/unlock": self._claude_account_unlock,
            "/api/claude/accounts/reorder": self._claude_accounts_reorder,
            "/api/claude/login/start": self._claude_login_start,
            "/api/claude/login/submit": self._claude_login_submit,
            "/api/claude/login/cancel": self._claude_login_cancel,
            "/api/fs/mkdir": self._fs_mkdir,
            "/api/fs/mkdir-system": self._fs_mkdir_system,
            "/api/fs/delete": self._fs_delete,
            "/api/fs/trash-list": self._fs_trash_list,
            "/api/fs/trash-restore": self._fs_trash_restore,
            "/api/fs/trash-delete": self._fs_trash_delete,
            "/api/fs/trash-empty": self._fs_trash_empty,
        }
        handler = handlers.get(p)
        if handler:
            self._json(handler(body))
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        origin = self._get_cors_origin()
        self.send_header("Access-Control-Allow-Origin", origin)
        if origin != "*":
            self.send_header("Access-Control-Allow-Credentials", "true")
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
        account_id = body.get("accountId")
        if not node_id or not prompt:
            return {"ok": False, "error": "nodeId and prompt required"}

        exec_id = str(uuid.uuid4())[:8]
        out_file = f"/tmp/node_{node_id}_output.txt"
        done_file = f"/tmp/node_{node_id}_done"
        for f in [out_file, done_file]:
            if os.path.exists(f): os.remove(f)

        # DB에 실행 기록 생성 (project_id 비어있으면 NULL — FK 제약 회피)
        db_exec("INSERT INTO executions (id, project_id, node_id, node_name, input_raw, input_resolved, chat_only, started, status) VALUES (?,?,?,?,?,?,?,?,?)",
                (exec_id, project_id or None, node_id, node_name, body.get("inputRaw", ""), prompt, 1 if chat_only else 0, datetime.now().isoformat(), "running"))
        log(f"EXEC [{exec_id}] node={node_name} account={account_id} prompt={len(prompt)}자 chatOnly={chat_only}")
        if len(prompt) > 23000:
            log(f"EXEC [{exec_id}] WARNING: 프롬프트 {len(prompt)}자 — 23,000자 초과, 타임아웃 가능성 높음")

        def run():
            try:
                # env는 run_claude_safe 가 계정마다 새로 만듦 (폴백 시)
                run_cwd = None
                if cwd and os.path.isdir(cwd):
                    run_cwd = cwd
                else:
                    # Auto-create folder: {workspace_root}/{YYYYMMDD_project_name}/{node_name}/
                    try:
                        ws_row = db_exec("SELECT value FROM system_settings WHERE key='workspace_root'", fetchone=True)
                        if ws_row and ws_row.get("value"):
                            ws_root = ws_row["value"]
                            project_name = body.get("projectName", "") or "Untitled"
                            def safe_name(s):
                                return re.sub(r'[/\\:*?"<>|]', '_', s).strip()[:100]
                            date_str = datetime.now().strftime("%Y%m%d")
                            project_folder = f"{date_str}_{safe_name(project_name)}"
                            node_folder = safe_name(node_name)
                            run_cwd = os.path.join(ws_root, project_folder, node_folder)
                            os.makedirs(run_cwd, exist_ok=True)
                            log(f"EXEC [{exec_id}] auto-cwd: {run_cwd}")
                    except Exception as e:
                        log(f"EXEC [{exec_id}] auto-cwd failed: {e}")
                        run_cwd = None
                def _build():
                    return build_claude_cmd(prompt, {
                        "chatOnly": chat_only,
                        "systemPrompt": body.get("systemPrompt", ""),
                        "jsonSchema": body.get("jsonSchema", ""),
                        "maxTurns": body.get("maxTurns", 0),
                        "images": body.get("images", []),
                    })
                output, used_acc, fb_msg = run_claude_safe(_build, account_id, run_cwd=run_cwd, timeout=600)
                if used_acc and used_acc != account_id:
                    log(f"EXEC [{exec_id}] 계정 폴백: {account_id} → {used_acc}")
                    # node에 새 계정 영구 배정 (다음에도 같은 계정 쓰게)
                    # node_id는 frontend가 관리하므로 여기서 DB 업뎃 안 함, 응답에만 표시
                if not output:
                    log(f"EXEC [{exec_id}] FAILED: {fb_msg}")
                    output = f"(❌ 실행 실패: {fb_msg})"
                log(f"EXEC [{exec_id}] done! {len(output)}자 [{fb_msg}]")
                with open(out_file, "w", encoding="utf-8") as f: f.write(output)
                with open(done_file, "w") as f: f.write("done")
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

        # account_id 결정: body에 있으면 사용, 없으면 conversation에서 조회, 그것도 없으면 기본
        account_id = body.get("accountId")

        # 대화 세션 없으면 생성
        if not conv_id:
            conv_id = str(uuid.uuid4())[:8]
            # 새 conversation 생성 시 body의 accountId 사용 (없으면 다음 계정 자동 배정)
            if not account_id:
                # 자동 배정 (round-robin 등)
                try:
                    nxt = self._claude_next_account({})
                    if nxt.get("ok"): account_id = nxt.get("accountId")
                except: pass
            db_exec("INSERT INTO conversations (id, node_id, node_name, title, account_id, created) VALUES (?,?,?,?,?,?)",
                    (conv_id, node_id, node_name, message[:30], account_id, datetime.now().isoformat()))
        else:
            # 기존 conversation → 저장된 account_id 조회
            if not account_id:
                conv = db_exec("SELECT account_id FROM conversations WHERE id=?", (conv_id,), fetchone=True)
                if conv and conv.get("account_id"):
                    account_id = conv["account_id"]

        # 유저 메시지 저장 (chat_only 모드도 함께 — 각 메시지가 어느 모드로 처리됐는지 추적)
        db_exec("INSERT INTO messages (conv_id, role, content, ts, chat_only) VALUES (?,?,?,?,?)",
                (conv_id, "user", message, datetime.now().isoformat(), 1 if chat_only else 0))

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
                run_cwd = cwd if cwd and os.path.isdir(cwd) else None
                def _build():
                    return build_claude_cmd(full_prompt, {
                        "chatOnly": chat_only,
                        "systemPrompt": body.get("systemPrompt", ""),
                        "images": body.get("images", []),
                    })
                reply, used_acc, fb_msg = run_claude_safe(_build, account_id, run_cwd=run_cwd, timeout=600)
                if used_acc and used_acc != account_id:
                    log(f"CHAT [{conv_id}] 계정 폴백 {account_id} → {used_acc}")
                    # 대화에 새 계정 영구 배정 (다음 메시지부터 같은 계정 사용)
                    try: db_exec("UPDATE conversations SET account_id=? WHERE id=?", (used_acc, conv_id))
                    except: pass
                if not reply:
                    reply = f"(❌ 응답 실패: {fb_msg})"
                log(f"CHAT [{conv_id}] reply={len(reply)}자 [{fb_msg}]")
                db_exec("INSERT INTO messages (conv_id, role, content, ts, chat_only) VALUES (?,?,?,?,?)",
                        (conv_id, "assistant", reply, datetime.now().isoformat(), 1 if chat_only else 0))
                with open(done_file, "w") as f: f.write(reply)
            except Exception as e:
                log(f"CHAT [{conv_id}] ERROR: {e}")
                db_exec("INSERT INTO messages (conv_id, role, content, ts, chat_only) VALUES (?,?,?,?,?)",
                        (conv_id, "assistant", f"(오류: {e})", datetime.now().isoformat(), 1 if chat_only else 0))
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
        # 부모 노드의 account_id를 상속 (body에 있으면 사용)
        account_id = body.get("accountId")
        db_exec("INSERT INTO conversations (id, parent_exec_id, node_id, node_name, title, account_id, created) VALUES (?,?,?,?,?,?,?)",
                (conv_id, exec_id, node_id, node_name, title, account_id, datetime.now().isoformat()))

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
        rows = db_exec("SELECT id, role, content, ts, chat_only FROM messages WHERE conv_id=? ORDER BY id", (conv_id,), fetch=True)
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
        rows = db_exec("SELECT id, name, modified, favorite, folder_id FROM projects ORDER BY favorite DESC, modified DESC LIMIT 200", fetch=True) or []
        # __current__ 류는 Python에서 필터링 (LIKE ESCAPE 호환성 이슈 회피)
        rows = [r for r in rows if not (r.get("id") or "").startswith("__current")]
        log(f"[PROJECT_LIST] {len(rows)}개 반환")
        return {"ok": True, "projects": rows}

    def _project_list_meta(self):
        """메타데이터만 (data 필드 제외, 가벼움) — 노드 수도 함께"""
        rows = db_exec("SELECT id, name, modified, created, favorite, folder_id FROM projects ORDER BY favorite DESC, modified DESC LIMIT 200", fetch=True) or []
        # __current__ 류는 Python에서 필터링
        rows = [r for r in rows if not (r.get("id") or "").startswith("__current")]
        log(f"[PROJECT_LIST_META] {len(rows)}개 반환")
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

    def _project_move(self, body):
        pid = body.get("id", "")
        folder_id = body.get("folderId")
        if not pid: return {"ok": False, "error": "id required"}
        db_exec("UPDATE projects SET folder_id=? WHERE id=?", (folder_id, pid))
        return {"ok": True}

    # ── Project Folders ──

    def _project_folder_list(self):
        rows = db_exec("SELECT * FROM project_folders ORDER BY sort_order, name", fetch=True)
        return {"ok": True, "folders": rows}

    def _project_folder_save(self, body):
        fid = body.get("id")
        name = body.get("name", "새 폴더")
        icon = body.get("icon", "📂")
        color = body.get("color", "")
        parent_id = body.get("parentId")
        if parent_id in ("", 0, None): parent_id = None
        now = datetime.now().isoformat()
        if fid:
            # 부분 업데이트 — body에 있는 필드만 갱신
            updates, params = [], []
            if "name" in body: updates.append("name=?"); params.append(name)
            if "icon" in body: updates.append("icon=?"); params.append(icon)
            if "color" in body: updates.append("color=?"); params.append(color)
            if "parentId" in body: updates.append("parent_id=?"); params.append(parent_id)
            if updates:
                params.append(fid)
                db_exec(f"UPDATE project_folders SET {', '.join(updates)} WHERE id=?", tuple(params))
            return {"ok": True, "id": fid}
        else:
            new_id = db_exec("INSERT INTO project_folders (name, icon, color, parent_id, created) VALUES (?,?,?,?,?)", (name, icon, color, parent_id, now))
            return {"ok": True, "id": new_id}

    def _project_folder_delete(self, body):
        fid = body.get("id", "")
        if fid:
            # 하위 폴더는 부모 NULL로 (최상단으로 이동), 프로젝트도 폴더 NULL로
            db_exec("UPDATE project_folders SET parent_id=NULL WHERE parent_id=?", (fid,))
            db_exec("UPDATE projects SET folder_id=NULL WHERE folder_id=?", (fid,))
            db_exec("DELETE FROM project_folders WHERE id=?", (fid,))
        return {"ok": True}

    # ── 날짜 폴더 자동 생성 (YYYY > YYYYMMDD_xxx 계층) ──
    def _project_date_folder(self, body):
        """오늘 날짜의 폴더 자동 생성 (없으면). 연도 폴더 → 일자 폴더 계층"""
        from datetime import datetime
        today = datetime.now()
        year = today.strftime("%Y")
        date = today.strftime("%Y%m%d")
        suffix = (body.get("suffix") or "").strip()
        date_folder_name = f"{date}_{suffix}" if suffix else date
        # 1) 연도 폴더 찾기/생성
        year_row = db_exec("SELECT id FROM project_folders WHERE name=? AND parent_id IS NULL", (year,), fetchone=True)
        if year_row:
            year_id = year_row["id"]
        else:
            year_id = db_exec("INSERT INTO project_folders (name, icon, color, parent_id, created) VALUES (?,?,?,?,?)",
                              (year, "📅", "#3B82F6", None, today.isoformat()))
        # 2) 일자 폴더 찾기/생성 (연도 폴더 안에)
        date_row = db_exec("SELECT id FROM project_folders WHERE name=? AND parent_id=?", (date_folder_name, year_id), fetchone=True)
        if date_row:
            date_id = date_row["id"]
        else:
            date_id = db_exec("INSERT INTO project_folders (name, icon, color, parent_id, created) VALUES (?,?,?,?,?)",
                              (date_folder_name, "📂", "", year_id, today.isoformat()))
        return {"ok": True, "yearFolderId": year_id, "dateFolderId": date_id, "dateFolderName": date_folder_name}

    # ── 프로젝트 첨부 (메모, 대화) ──
    def _proj_attach_list(self, params):
        pid = params.get("projectId", [""])[0]
        if not pid: return {"ok": True, "attachments": []}
        rows = db_exec("SELECT id, kind, target_id, sort_order FROM project_attachments WHERE project_id=? ORDER BY sort_order, id", (pid,), fetch=True)
        # enrich with target metadata
        out = []
        for r in rows:
            item = {"id": r["id"], "kind": r["kind"], "targetId": r["target_id"], "sortOrder": r["sort_order"]}
            try:
                if r["kind"] == "memo":
                    m = db_exec("SELECT name, substr(content,1,80) as preview FROM memos WHERE id=?", (int(r["target_id"]),), fetchone=True)
                    if m: item["title"] = m.get("name") or "무제"; item["preview"] = m.get("preview") or ""
                elif r["kind"] == "conversation":
                    c = db_exec("SELECT title, node_name FROM conversations WHERE id=?", (r["target_id"],), fetchone=True)
                    if c: item["title"] = c.get("title") or "대화"; item["preview"] = c.get("node_name") or ""
            except Exception as e:
                pass
            out.append(item)
        return {"ok": True, "attachments": out}

    def _proj_attach_add(self, body):
        pid = body.get("projectId")
        kind = body.get("kind")
        target_id = body.get("targetId")
        if not pid or not kind or target_id is None:
            return {"ok": False, "error": "projectId, kind, targetId required"}
        # 중복 방지
        existing = db_exec("SELECT id FROM project_attachments WHERE project_id=? AND kind=? AND target_id=?",
                           (pid, kind, str(target_id)), fetchone=True)
        if existing: return {"ok": True, "id": existing["id"], "duplicate": True}
        new_id = db_exec("INSERT INTO project_attachments (project_id, kind, target_id, created) VALUES (?,?,?,?)",
                         (pid, kind, str(target_id), datetime.now().isoformat()))
        return {"ok": True, "id": new_id}

    def _proj_attach_remove(self, body):
        iid = body.get("id")
        if iid:
            db_exec("DELETE FROM project_attachments WHERE id=?", (int(iid),))
        return {"ok": True}

    # ── 아이콘 라이브러리 (커스텀 업로드 이미지) ──
    def _icons_list(self):
        rows = db_exec("SELECT id, name, data, kind, created FROM icons ORDER BY id DESC LIMIT 200", fetch=True)
        return {"ok": True, "icons": rows or []}

    def _icon_save(self, body):
        name = body.get("name", "icon")
        data = body.get("data", "")  # data URL
        kind = body.get("kind", "image")
        if not data: return {"ok": False, "error": "data required"}
        # 5MB 제한
        if len(data) > 6_500_000: return {"ok": False, "error": "icon too large (>5MB base64)"}
        new_id = db_exec("INSERT INTO icons (name, data, kind, created) VALUES (?,?,?,?)",
                         (name, data, kind, datetime.now().isoformat()))
        return {"ok": True, "id": new_id}

    def _icon_delete(self, body):
        iid = body.get("id")
        if iid: db_exec("DELETE FROM icons WHERE id=?", (int(iid),))
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
        if DB_TYPE == "postgresql":
            db_exec("DELETE FROM temps WHERE created::timestamp < NOW() - INTERVAL '30 days'")
        else:
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
        if not tid: return {"ok": False, "error": "id required"}
        row = db_exec("SELECT id, name, data, date, created FROM temps WHERE id=?", (tid,), fetchone=True)
        if row:
            trash_data = json.dumps(dict(row), ensure_ascii=False, default=str)
            db_exec("INSERT INTO trash (original_table, original_id, name, data, deleted_at) VALUES (?,?,?,?,?)",
                    ("temps", str(tid), row.get("name", ""), trash_data, datetime.now().isoformat()))
        db_exec("DELETE FROM temps WHERE id=?", (tid,))
        return {"ok": True}

    # ── Memo & Folders ──

    def _memo_list(self, params):
        """메모 목록 (folder_id 또는 is_temp 필터)"""
        folder_id = params.get("folderId", [None])[0]
        is_temp = params.get("isTemp", [None])[0]
        sql = "SELECT id, name, folder_id, is_temp, pinned, color, substr(content,1,100) as preview, created, modified FROM memos WHERE name!='__scratchpad__'"
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
        if cond: sql += " AND " + " AND ".join(cond)
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
        # Auto-create date folder for non-temp memos without a folder
        if not folder_id and not is_temp:
            today = datetime.now().strftime("%Y-%m-%d")
            existing = db_exec("SELECT id FROM memo_folders WHERE name=?", (today,), fetchone=True)
            if existing:
                folder_id = existing["id"]
            else:
                folder_id = db_exec("INSERT INTO memo_folders (name, icon, color, created) VALUES (?,?,?,?)",
                                   (today, "📅", "", now))
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
        if not mid: return {"ok": False, "error": "id required"}
        row = db_exec("SELECT id, name, content, folder_id, is_temp, pinned, color, created, modified FROM memos WHERE id=?", (mid,), fetchone=True)
        if row:
            trash_data = json.dumps(dict(row), ensure_ascii=False, default=str)
            db_exec("INSERT INTO trash (original_table, original_id, name, data, deleted_at) VALUES (?,?,?,?,?)",
                    ("memos", str(mid), row.get("name", ""), trash_data, datetime.now().isoformat()))
        db_exec("DELETE FROM memos WHERE id=?", (mid,))
        return {"ok": True}

    def _folder_list(self):
        rows = db_exec("SELECT f.id, f.name, f.icon, f.sort_order, f.color, f.parent_id, f.created, (SELECT COUNT(*) FROM memos WHERE folder_id=f.id) as memo_count FROM memo_folders f ORDER BY sort_order, name", fetch=True)
        return {"ok": True, "folders": rows}

    def _folder_save(self, body):
        fid = body.get("id")
        now = datetime.now().isoformat()
        if fid:
            # Partial update: only update fields explicitly provided in body.
            updates = []
            params = []
            if "name" in body:
                updates.append("name=?"); params.append(body.get("name") or "새 폴더")
            if "icon" in body:
                updates.append("icon=?"); params.append(body.get("icon") or "📁")
            if "color" in body:
                updates.append("color=?"); params.append(body.get("color") or "")
            if "parentId" in body:
                pid = body.get("parentId")
                updates.append("parent_id=?"); params.append(pid if pid else None)
            if updates:
                params.append(fid)
                db_exec(f"UPDATE memo_folders SET {', '.join(updates)} WHERE id=?", tuple(params))
            return {"ok": True, "id": fid}
        else:
            name = body.get("name", "새 폴더")
            icon = body.get("icon", "📁")
            color = body.get("color", "")
            parent_id = body.get("parentId")
            if parent_id in ("", 0): parent_id = None
            new_id = db_exec("INSERT INTO memo_folders (name, icon, color, parent_id, created) VALUES (?,?,?,?,?)", (name, icon, color, parent_id, now))
            return {"ok": True, "id": new_id}

    def _folder_delete(self, body):
        fid = body.get("id", "")
        if fid:
            db_exec("UPDATE memos SET folder_id=NULL WHERE folder_id=?", (fid,))
            db_exec("DELETE FROM memo_folders WHERE id=?", (fid,))
        return {"ok": True}

    def _project_delete(self, body):
        pid = body.get("id", "")
        if not pid: return {"ok": False, "error": "id required"}
        row = db_exec("SELECT id, name, data, created, modified, favorite, folder_id FROM projects WHERE id=?", (pid,), fetchone=True)
        if row:
            trash_data = json.dumps(dict(row), ensure_ascii=False, default=str)
            db_exec("INSERT INTO trash (original_table, original_id, name, data, deleted_at) VALUES (?,?,?,?,?)",
                    ("projects", str(pid), row.get("name", ""), trash_data, datetime.now().isoformat()))
        db_exec("DELETE FROM projects WHERE id=?", (pid,))
        return {"ok": True}

    # ── Trash (휴지통) ──

    def _trash_list(self):
        rows = db_exec("SELECT id, original_table, original_id, name, deleted_at FROM trash ORDER BY deleted_at DESC LIMIT 100", fetch=True)
        return {"ok": True, "items": rows}

    def _trash_restore(self, body):
        tid = body.get("id")
        if not tid: return {"ok": False, "error": "id required"}
        row = db_exec("SELECT * FROM trash WHERE id=?", (tid,), fetchone=True)
        if not row: return {"ok": False, "error": "not found"}
        data = json.loads(row["data"])
        table = row["original_table"]
        if table == "projects":
            db_exec("INSERT INTO projects (id, name, data, created, modified, favorite, folder_id) VALUES (?,?,?,?,?,?,?)",
                    (data.get("id"), data.get("name",""), data.get("data","{}"), data.get("created",""), data.get("modified",""), data.get("favorite",0), data.get("folder_id")))
        elif table == "temps":
            db_exec("INSERT INTO temps (name, data, date, created) VALUES (?,?,?,?)",
                    (data.get("name",""), data.get("data","{}"), data.get("date",""), data.get("created","")))
        elif table == "memos":
            db_exec("INSERT INTO memos (name, content, folder_id, is_temp, pinned, color, created, modified) VALUES (?,?,?,?,?,?,?,?)",
                    (data.get("name",""), data.get("content",""), data.get("folder_id"), data.get("is_temp",1), data.get("pinned",0), data.get("color",""), data.get("created",""), data.get("modified","")))
        db_exec("DELETE FROM trash WHERE id=?", (tid,))
        return {"ok": True}

    def _trash_delete(self, body):
        tid = body.get("id")
        if tid: db_exec("DELETE FROM trash WHERE id=?", (tid,))
        return {"ok": True}

    def _trash_empty(self, body):
        db_exec("DELETE FROM trash")
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
        upload_dir = os.path.join(UPLOADS_DIR, purpose + "s")
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
        upload_dir = os.path.join(UPLOADS_DIR, "pdfs")
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

    # ── Scratchpad ──

    def _scratchpad_load(self):
        row = db_exec("SELECT content FROM memos WHERE name='__scratchpad__' LIMIT 1", fetchone=True)
        return {"ok": True, "content": row["content"] if row else ""}

    def _scratchpad_save(self, body):
        content = body.get("content", "")
        now = datetime.now().isoformat()
        existing = db_exec("SELECT id FROM memos WHERE name='__scratchpad__'", fetchone=True)
        if existing:
            db_exec("UPDATE memos SET content=?, modified=? WHERE name='__scratchpad__'", (content, now))
        else:
            db_exec("INSERT INTO memos (name, content, is_temp, created, modified) VALUES (?,?,0,?,?)",
                    ("__scratchpad__", content, now, now))
        return {"ok": True}

    # ── Memo Pin ──

    def _memo_pinned(self):
        rows = db_exec("SELECT id, name, substr(content,1,40) as preview FROM memos WHERE pinned=1 AND name!='__scratchpad__' ORDER BY modified DESC LIMIT 5", fetch=True)
        return {"ok": True, "memos": rows}

    def _memo_pin(self, body):
        mid = body.get("id", "")
        pinned = 1 if body.get("pinned", True) else 0
        if mid:
            db_exec("UPDATE memos SET pinned=? WHERE id=?", (pinned, mid))
        return {"ok": True}

    # ── 즐겨찾기 폴더 (메모와 별개 카테고리, 같은 메모가 여러 폴더에 들어갈 수 있음) ──

    def _fav_folders(self, params):
        parent_id = params.get("parentId", [""])[0]
        kind = params.get("kind", ["project"])[0]  # 기본: 프로젝트 즐겨찾기
        if parent_id and parent_id != "null":
            rows = db_exec("SELECT id, name, parent_id, color, icon, sort_order, kind FROM fav_folders WHERE parent_id=? AND (kind=? OR kind IS NULL) ORDER BY sort_order, id", (int(parent_id), kind), fetch=True)
        else:
            rows = db_exec("SELECT id, name, parent_id, color, icon, sort_order, kind FROM fav_folders WHERE parent_id IS NULL AND (kind=? OR kind IS NULL) ORDER BY sort_order, id", (kind,), fetch=True)
        return {"ok": True, "folders": rows or []}

    def _fav_folder_save(self, body):
        fid = body.get("id")
        now = datetime.now().isoformat()
        if fid:
            updates = []
            params = []
            if "name" in body:
                updates.append("name=?"); params.append(body.get("name") or "새 폴더")
            if "color" in body:
                updates.append("color=?"); params.append(body.get("color") or "")
            if "icon" in body:
                updates.append("icon=?"); params.append(body.get("icon") or "⭐")
            if "parentId" in body:
                pid = body.get("parentId")
                updates.append("parent_id=?"); params.append(pid if pid else None)
            if "sortOrder" in body:
                updates.append("sort_order=?"); params.append(int(body.get("sortOrder", 0)))
            if updates:
                params.append(fid)
                db_exec(f"UPDATE fav_folders SET {', '.join(updates)} WHERE id=?", tuple(params))
            return {"ok": True, "id": fid}
        else:
            name = body.get("name", "새 폴더")
            color = body.get("color", "")
            icon = body.get("icon", "⭐")
            kind = body.get("kind", "project")
            parent_id = body.get("parentId")
            if parent_id in ("", 0, None): parent_id = None
            new_id = db_exec("INSERT INTO fav_folders (name, parent_id, color, icon, kind, created) VALUES (?,?,?,?,?,?)", (name, parent_id, color, icon, kind, now))
            return {"ok": True, "id": new_id}

    def _fav_folder_delete(self, body):
        fid = body.get("id")
        if not fid: return {"ok": False, "error": "id required"}
        # 하위 폴더는 부모 NULL로 (최상단으로 이동), 항목은 삭제
        db_exec("UPDATE fav_folders SET parent_id=NULL WHERE parent_id=?", (fid,))
        db_exec("DELETE FROM fav_items WHERE folder_id=?", (fid,))
        db_exec("DELETE FROM fav_folders WHERE id=?", (fid,))
        return {"ok": True}

    def _fav_items(self, params):
        fid = params.get("folderId", [""])[0]
        if not fid: return {"ok": True, "items": []}
        # 양쪽 호환: 신규는 target_id+kind, 레거시는 memo_id (kind='memo'로 간주)
        rows = db_exec("""
            SELECT id, memo_id, target_id, kind, sort_order
            FROM fav_items WHERE folder_id=? ORDER BY sort_order, id
        """, (int(fid),), fetch=True) or []
        out = []
        for r in rows:
            kind = r.get("kind") or ('memo' if r.get("memo_id") else 'project')
            tid = r.get("target_id") or (str(r.get("memo_id")) if r.get("memo_id") else None)
            if not tid: continue
            item = {"id": r["id"], "kind": kind, "targetId": tid, "sortOrder": r["sort_order"]}
            try:
                if kind == 'memo':
                    m = db_exec("SELECT name, substr(content,1,80) as preview FROM memos WHERE id=?", (int(tid),), fetchone=True)
                    if m: item["title"] = m.get("name") or "무제"; item["preview"] = m.get("preview") or ""
                elif kind == 'project':
                    p = db_exec("SELECT name, modified FROM projects WHERE id=?", (tid,), fetchone=True)
                    if p: item["title"] = p.get("name") or "무제"; item["preview"] = (p.get("modified") or "")[:16]
            except Exception:
                pass
            out.append(item)
        return {"ok": True, "items": out}

    def _fav_item_add(self, body):
        fid = body.get("folderId")
        kind = body.get("kind", "project")
        target_id = body.get("targetId") or body.get("memoId") or body.get("projectId")
        if not fid or target_id is None: return {"ok": False, "error": "folderId and targetId required"}
        target_id = str(target_id)
        # 중복 방지
        existing = db_exec("SELECT id FROM fav_items WHERE folder_id=? AND kind=? AND target_id=?",
                           (int(fid), kind, target_id), fetchone=True)
        if existing: return {"ok": True, "id": existing["id"], "duplicate": True}
        # ⚠ memo_id가 NOT NULL 제약이라 0 placeholder 사용 (project/non-memo 항목)
        # 실제 식별은 target_id+kind 사용
        memo_id = int(target_id) if kind == 'memo' and str(target_id).isdigit() else 0
        try:
            new_id = db_exec("INSERT INTO fav_items (folder_id, memo_id, target_id, kind, created) VALUES (?,?,?,?,?)",
                             (int(fid), memo_id, target_id, kind, datetime.now().isoformat()))
            log(f"[FAV] item added: folder={fid} kind={kind} target={target_id} → id={new_id}")
            return {"ok": True, "id": new_id}
        except Exception as e:
            log(f"[FAV] INSERT failed: {e}")
            # NOT NULL 제약 시도 우회 — 컬럼이 nullable로 안 바뀐 케이스
            try:
                # NOT NULL을 nullable로 ALTER (PG)
                if _is_pg():
                    db_exec("ALTER TABLE fav_items ALTER COLUMN memo_id DROP NOT NULL")
                # 재시도
                new_id = db_exec("INSERT INTO fav_items (folder_id, memo_id, target_id, kind, created) VALUES (?,?,?,?,?)",
                                 (int(fid), memo_id, target_id, kind, datetime.now().isoformat()))
                return {"ok": True, "id": new_id}
            except Exception as e2:
                return {"ok": False, "error": f"INSERT failed: {e2}"}

    def _fav_item_remove(self, body):
        iid = body.get("id")
        fid = body.get("folderId")
        kind = body.get("kind")
        target_id = body.get("targetId") or body.get("memoId")
        if iid:
            db_exec("DELETE FROM fav_items WHERE id=?", (int(iid),))
        elif fid and target_id is not None:
            if kind:
                db_exec("DELETE FROM fav_items WHERE folder_id=? AND kind=? AND target_id=?", (int(fid), kind, str(target_id)))
            else:
                db_exec("DELETE FROM fav_items WHERE folder_id=? AND (target_id=? OR memo_id=?)", (int(fid), str(target_id), int(target_id) if str(target_id).isdigit() else -1))
        return {"ok": True}

    def _state_save(self, body):
        """자동 저장 — workflowId 있으면 해당 프로젝트 갱신, 없으면 탭별 임시 슬롯
        (멀티 탭 분리: 각 탭이 sessionStorage tabId를 보내서 서로 안 섞임)"""
        now = datetime.now().isoformat()
        data = json.dumps(body, ensure_ascii=False)
        wf_id = body.get("workflowId")
        tab_id = body.get("tabId", "default")
        if wf_id:
            existing = db_exec("SELECT id FROM projects WHERE id=?", (wf_id,), fetchone=True)
            if existing:
                db_exec("UPDATE projects SET data=?, modified=? WHERE id=?", (data, now, wf_id))
                return {"ok": True, "savedTo": wf_id}
        recovery_id = f"__current_{tab_id}__"
        existing = db_exec("SELECT id FROM projects WHERE id=?", (recovery_id,), fetchone=True)
        if existing:
            db_exec("UPDATE projects SET data=?, modified=? WHERE id=?", (data, now, recovery_id))
        else:
            db_exec("INSERT INTO projects (id, name, data, created, modified) VALUES (?,?,?,?,?)",
                    (recovery_id, "__current__", data, now, now))
        return {"ok": True, "savedTo": recovery_id}

    def _state_load(self, params=None):
        """탭별 복구 슬롯에서 상태 복원 (없으면 legacy __current__ fallback)"""
        tab_id = "default"
        if params:
            v = params.get("tabId", ["default"])
            tab_id = v[0] if v else "default"
        recovery_id = f"__current_{tab_id}__"
        row = db_exec("SELECT data FROM projects WHERE id=?", (recovery_id,), fetchone=True)
        if not row:
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

        # 1. 세션 리셋 (main 세션만 삭제, 서버 세션은 유지)
        run_tmux("kill-session", "-t", SESSION_NAME)
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
                    cmd = "source ~/.nvm/nvm.sh 2>/dev/null; claude --dangerously-skip-permissions"
                    run_tmux("send-keys", "-t", target, cmd, "Enter")
                # 백그라운드: trust 다이얼로그 적극 자동 승인
                def auto_trust():
                    # 60초 동안 모든 패널에서 trust/permission 프롬프트 감지 → Enter로 자동 승인
                    trust_keywords = ["trust this folder", "yes, i trust", "do you trust",
                                      "safety check", "trust this project"]
                    done_keywords = ["bypass permissions"]  # claude에 진입 완료된 상태 (프롬프트 "❯" 가 보이는 상태)
                    done = set()
                    for sec in range(60):
                        time.sleep(1)
                        if len(done) >= len(pane_indices):
                            log(f"AUTO_TRUST all {len(done)} panes done")
                            break
                        for idx in pane_indices:
                            if idx in done:
                                continue
                            target = f"{SESSION_NAME}:.{idx}"
                            r = run_tmux("capture-pane", "-t", target, "-p")
                            if not r["ok"]:
                                continue
                            out = r["stdout"].lower()
                            # 이미 claude에 진입 완료?
                            if any(kw in out for kw in done_keywords):
                                done.add(idx)
                                log(f"AUTO_TRUST pane={idx} already in claude (sec={sec})")
                                continue
                            # trust 프롬프트 감지 → Enter 전송
                            if any(kw in out for kw in trust_keywords):
                                run_tmux("send-keys", "-t", target, "Enter")
                                log(f"AUTO_TRUST pane={idx} Enter sent (sec={sec})")
                                time.sleep(0.5)
                threading.Thread(target=auto_trust, daemon=True).start()
                log(f"SETUP_SESSION launched claude in {len(pane_indices)} panes + auto-trust")
                return {"ok": True, "panes": pane_indices, "launched": len(pane_indices)}

        return {"ok": True}

    def _reset_session(self, body):
        log("RESET SESSION")
        run_tmux("kill-session", "-t", SESSION_NAME)
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

    # ── Claude Accounts (multi-account manager) ──

    def _claude_accounts_list(self):
        try:
            rows = db_exec(
                "SELECT id, name, active, priority, created, rate_limited_until, rate_limit_reason FROM claude_accounts ORDER BY priority ASC, created DESC",
                fetch=True
            ) or []
            # 만료된 cooldown 자동 정리
            now = datetime.now()
            for r in rows:
                until = r.get("rate_limited_until")
                if until:
                    try:
                        u = datetime.fromisoformat(until)
                        if now >= u:
                            db_exec("UPDATE claude_accounts SET rate_limited_until=NULL, rate_limit_reason=NULL WHERE id=?", (r["id"],))
                            r["rate_limited_until"] = None
                            r["rate_limit_reason"] = None
                    except: pass
            return {"ok": True, "accounts": rows}
        except Exception as e:
            return {"ok": False, "error": str(e), "accounts": []}

    def _claude_account_unlock(self, body):
        """수동으로 rate-limit cooldown 해제"""
        aid = body.get("id")
        if not aid: return {"ok": False, "error": "id required"}
        db_exec("UPDATE claude_accounts SET rate_limited_until=NULL, rate_limit_reason=NULL WHERE id=?", (int(aid),))
        return {"ok": True}

    def _claude_account_save(self, body):
        aid = body.get("id")
        name = (body.get("name", "") or "").strip()
        credentials = (body.get("credentials", "") or "").strip()
        if not name:
            return {"ok": False, "error": "이름이 필요합니다"}
        if not credentials:
            return {"ok": False, "error": "credentials가 비어있습니다"}
        try:
            json.loads(credentials)
        except Exception as e:
            return {"ok": False, "error": f"유효한 JSON이 아닙니다: {e}"}
        now = datetime.now().isoformat()
        try:
            if aid:
                db_exec(
                    "UPDATE claude_accounts SET name=?, credentials=? WHERE id=?",
                    (name, credentials, aid)
                )
                try: _sync_account_to_dir(aid, credentials)
                except Exception as e: log(f"sync acct dir failed: {e}")
                return {"ok": True, "id": aid}
            else:
                new_id = db_exec(
                    "INSERT INTO claude_accounts (name, credentials, active, created) VALUES (?,?,?,?)",
                    (name, credentials, 0, now)
                )
                try: _sync_account_to_dir(new_id, credentials)
                except Exception as e: log(f"sync acct dir failed: {e}")
                return {"ok": True, "id": new_id}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _claude_account_delete(self, body):
        aid = body.get("id")
        if not aid:
            return {"ok": False, "error": "id required"}
        try:
            db_exec("DELETE FROM claude_accounts WHERE id=?", (aid,))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _claude_account_activate(self, body):
        aid = body.get("id")
        if not aid:
            return {"ok": False, "error": "id required"}
        try:
            row = db_exec("SELECT * FROM claude_accounts WHERE id=?", (aid,), fetchone=True)
            if not row:
                return {"ok": False, "error": "not found"}
            db_exec("UPDATE claude_accounts SET active=0")
            db_exec("UPDATE claude_accounts SET active=1 WHERE id=?", (aid,))

            # setup-token 여부 체크 (refreshToken 빈 값)
            is_setup_token = False
            try:
                parsed = json.loads(row["credentials"])
                oauth = parsed.get("claudeAiOauth", {})
                if oauth.get("accessToken") and not oauth.get("refreshToken"):
                    is_setup_token = True
            except Exception:
                pass

            claude_dir = os.path.expanduser("~/.claude")
            os.makedirs(claude_dir, exist_ok=True)
            creds_path = os.path.join(claude_dir, ".credentials.json")

            if is_setup_token:
                # setup-token 계정은 global credentials.json 안 만듦 (CLI가 혼동)
                # 오히려 기존 전체 OAuth 파일이 있다면 보존
                log(f"Claude account activated (setup-token, env var only): {row['name']}")
            else:
                # 전체 OAuth 계정이면 global에 저장
                with open(creds_path, "w", encoding="utf-8") as f:
                    f.write(row["credentials"])
            if not is_setup_token:
                try:
                    os.chmod(creds_path, 0o600)
                except Exception:
                    pass
            try: _sync_account_to_dir(row["id"], row["credentials"])
            except Exception as e: log(f"sync acct dir failed: {e}")
            if not is_setup_token:
                log(f"Claude account activated: {row['name']}")
            return {"ok": True, "active": row["name"], "setup_token": is_setup_token}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _claude_account_test(self, body):
        """계정으로 Claude 호출 테스트. 성공/실패 즉시 반환."""
        aid = body.get("id")
        if not aid: return {"ok": False, "error": "id required"}
        try:
            env = get_claude_env_for_account(int(aid))
            # 짧은 프롬프트로 빠르게 테스트
            result = subprocess.run(
                ["claude", "-p", "--dangerously-skip-permissions"],
                input="Reply only with: OK",
                capture_output=True, text=True, timeout=30, env=env
            )
            output = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            # 성공 판단: returncode 0 + 응답 있음 + "Not logged in" 아님
            if result.returncode == 0 and output and "Not logged in" not in output and "Please run" not in output:
                return {
                    "ok": True,
                    "authenticated": True,
                    "response": output[:100],
                    "msg": f"✅ 인증 성공! 응답: \"{output[:50]}\""
                }
            else:
                err = output or stderr or "(응답 없음)"
                return {
                    "ok": True,
                    "authenticated": False,
                    "error": err[:300],
                    "msg": f"❌ 인증 실패\n{err[:300]}"
                }
        except subprocess.TimeoutExpired:
            return {"ok": True, "authenticated": False, "msg": "❌ 타임아웃 (30초) — 인증 문제 가능성"}
        except Exception as e:
            return {"ok": True, "authenticated": False, "msg": f"❌ 오류: {e}"}

    def _claude_next_account(self, body):
        """회전 전략에 따라 다음 account_id 반환."""
        try:
            mode_row = db_exec("SELECT value FROM system_settings WHERE key='claude_rotation_mode'", fetchone=True)
            mode = mode_row["value"] if mode_row and mode_row.get("value") else "round-robin"

            accounts = db_exec("SELECT id, name, priority FROM claude_accounts ORDER BY priority ASC, id ASC", fetch=True) or []
            if not accounts:
                return {"ok": False, "error": "계정이 없습니다"}

            if mode == "manual":
                active = db_exec("SELECT id FROM claude_accounts WHERE active=1 LIMIT 1", fetchone=True)
                return {"ok": True, "accountId": active["id"] if active else accounts[0]["id"], "mode": mode}

            elif mode == "sequential":
                return {"ok": True, "accountId": accounts[0]["id"], "mode": mode}

            else:  # round-robin — rate-limited 계정 자동 스킵
                last_row = db_exec("SELECT value FROM system_settings WHERE key='claude_last_used_id'", fetchone=True)
                last_id = 0
                try:
                    if last_row and last_row.get("value"):
                        last_id = int(last_row["value"])
                except Exception:
                    last_id = 0
                ids = [a["id"] for a in accounts]
                try:
                    idx = ids.index(last_id)
                    next_idx = (idx + 1) % len(ids)
                except ValueError:
                    next_idx = 0
                # 한 바퀴 돌면서 사용 가능한 첫 계정 찾기
                next_id = None
                for _ in range(len(ids)):
                    cand = ids[next_idx]
                    if is_account_available(cand):
                        next_id = cand
                        break
                    next_idx = (next_idx + 1) % len(ids)
                if next_id is None:
                    return {"ok": False, "error": "모든 계정이 한도 초과 상태"}
                now = datetime.now().isoformat()
                existing = db_exec("SELECT key FROM system_settings WHERE key='claude_last_used_id'", fetchone=True)
                if existing:
                    db_exec("UPDATE system_settings SET value=?, updated=? WHERE key='claude_last_used_id'", (str(next_id), now))
                else:
                    db_exec("INSERT INTO system_settings (key, value, updated) VALUES (?,?,?)", ("claude_last_used_id", str(next_id), now))
                return {"ok": True, "accountId": next_id, "mode": mode}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _claude_next_preview(self, body):
        """다음에 사용될 계정만 반환 (counter 증가 안 함, UI 표시용 — rate-limited 스킵)"""
        try:
            nid = pick_available_account()
            return {"ok": True, "accountId": nid} if nid else {"ok": False, "error": "사용 가능한 계정 없음"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _claude_accounts_reorder(self, body):
        ids = body.get("ids", [])
        try:
            for priority, aid in enumerate(ids):
                db_exec("UPDATE claude_accounts SET priority=? WHERE id=?", (priority, aid))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Claude Web-based Login (OAuth via subprocess) ──

    def _claude_login_start(self, body):
        global _claude_login_proc, _claude_login_url, _claude_login_output, _claude_login_master_fd
        with _claude_login_lock:
            # Kill existing process if any
            try:
                if _claude_login_proc:
                    try:
                        os.kill(_claude_login_proc, 9)
                    except Exception:
                        pass
            except Exception:
                pass
            _claude_login_url = None
            _claude_login_output = []
            _claude_login_master_fd = None

            # PTY로 실행 - claude CLI가 터미널 크기를 자체 감지하므로
            # COLUMNS 환경변수로는 안 되고 PTY의 winsize를 크게 설정해야 함
            import pty, fcntl, termios, struct, tty
            try:
                master, slave = pty.openpty()
                # 터미널 크기 9999칸 (URL 줄바꿈 방지)
                ws = struct.pack('HHHH', 50, 9999, 0, 0)
                fcntl.ioctl(slave, termios.TIOCSWINSZ, ws)
                # ECHO만 끄기 (ICANON 유지 — CLI가 line 모드 기대)
                attrs = termios.tcgetattr(slave)
                attrs[3] = attrs[3] & ~termios.ECHO  # lflags: ECHO off
                termios.tcsetattr(slave, termios.TCSANOW, attrs)
            except Exception as e:
                return {"ok": False, "error": f"PTY 생성 실패: {e}"}

            env = get_claude_env()
            pid = os.fork()
            if pid == 0:
                # child process
                os.setsid()
                try:
                    fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
                except Exception:
                    pass
                os.close(master)
                os.dup2(slave, 0)
                os.dup2(slave, 1)
                os.dup2(slave, 2)
                os.close(slave)
                try:
                    # auth login 사용 (전체 권한: profile, inference, sessions:claude_code, mcp_servers, api_key, file_upload)
                    os.execvpe("claude", ["claude", "auth", "login"], env)
                except Exception:
                    os._exit(1)
            else:
                # parent
                os.close(slave)
                _claude_login_proc = pid
                _claude_login_master_fd = master

            def reader():
                global _claude_login_url, _claude_login_output
                import select as sel
                ansi_re = re.compile(rb'\x1b\[[0-9;?]*[a-zA-Z]')
                buf = b""
                last_url_update = 0  # URL 발견 후 경과 시간
                tentative_url = None
                try:
                    while True:
                        r, _, _ = sel.select([master], [], [], 0.3)
                        if r:
                            try:
                                chunk = os.read(master, 4096)
                            except OSError:
                                break
                            if not chunk:
                                break
                            buf += chunk
                            # 디버그: 원본 바이트를 파일에 덤프
                            try:
                                with open('/tmp/claude_login_raw.log', 'ab') as df:
                                    df.write(chunk)
                            except: pass
                            clean = ansi_re.sub(b'', buf).decode('utf-8', errors='replace')
                            _claude_login_output = clean.split('\n')

                            if _claude_login_url:
                                continue

                            # URL 추출: 줄바꿈+공백 제거 후 매칭
                            joined = re.sub(r'\n+\s*', '', clean)
                            best = None
                            for m in re.finditer(r'https?://[^\s]+', joined):
                                c = m.group(0).rstrip(').,"\'')
                                if 'oauth' in c.lower() or 'authorize' in c.lower():
                                    if not best or len(c) > len(best):
                                        best = c
                            if best:
                                # 조건: 새 URL이 이전과 같고 (더 이상 길어지지 않음)
                                #      그리고 최소 길이 200자 이상 (완전한 OAuth URL)
                                #      그리고 state= 포함 (URL 끝부분 파라미터)
                                if tentative_url == best and len(best) > 200 and 'state=' in best:
                                    _claude_login_url = best
                                else:
                                    tentative_url = best
                        else:
                            # 입력 없음: 잠깐 기다린 후 현재 tentative URL이 충분히 완성되었으면 commit
                            if tentative_url and not _claude_login_url:
                                if len(tentative_url) > 200 and 'state=' in tentative_url and 'redirect_uri' in tentative_url:
                                    _claude_login_url = tentative_url
                            # 프로세스 체크
                            try:
                                wpid, _ = os.waitpid(_claude_login_proc, os.WNOHANG)
                                if wpid != 0:
                                    break
                            except Exception:
                                break
                except Exception as e:
                    log(f"claude login PTY reader error: {e}")
            threading.Thread(target=reader, daemon=True).start()

            # URL 대기 (최대 25초) — 완전한 URL 확보 후 반환
            for _ in range(125):
                if _claude_login_url and 'redirect_uri' in _claude_login_url and 'state=' in _claude_login_url:
                    break
                time.sleep(0.2)

            if _claude_login_url:
                return {"ok": True, "url": _claude_login_url}
            # 프로세스 상태 확인
            exited = not _is_claude_proc_alive()
            return {
                "ok": False,
                "error": "URL 추출 실패 — 파일 업로드/붙여넣기를 사용하세요.",
                "exited": exited,
                "output": "\n".join(_claude_login_output[-40:])
            }

    def _claude_login_submit(self, body):
        global _claude_login_proc, _claude_login_output, _claude_login_master_fd
        code = (body.get("code", "") or "").strip()
        if not code:
            return {"ok": False, "error": "code required"}
        if not _claude_login_proc or not _is_claude_proc_alive():
            return {"ok": False, "error": "로그인 세션이 없습니다 (프로세스 종료됨)"}

        # claude auth login은 .credentials.json 파일에 저장
        creds_path = os.path.expanduser("~/.claude/.credentials.json")
        old_mtime = os.path.getmtime(creds_path) if os.path.exists(creds_path) else 0

        try:
            os.write(_claude_login_master_fd, (code + "\r").encode())
            log(f"CLAUDE_LOGIN submit code len={len(code)} has_hash={'#' in code}")
        except Exception as e:
            return {"ok": False, "error": f"PTY write 실패: {e}"}

        # .credentials.json 파일 갱신 대기 (최대 60초)
        cli_error = None
        creds = None
        for _iter in range(300):
            if os.path.exists(creds_path):
                new_mtime = os.path.getmtime(creds_path)
                if new_mtime > old_mtime:
                    time.sleep(0.5)  # 파일 완전 쓰기 대기
                    try:
                        with open(creds_path, "r", encoding="utf-8") as f:
                            creds = f.read()
                        json.loads(creds)  # 검증
                        log(f"CLAUDE_LOGIN creds updated size={len(creds)}")
                        break
                    except Exception as e:
                        log(f"creds read error: {e}")
            recent = "\n".join(_claude_login_output[-10:])
            if "Invalid code" in recent or "OAuth error" in recent or "authentication failed" in recent.lower():
                cli_error = recent
                log(f"CLAUDE_LOGIN CLI error: {recent[-300:]}")
                break
            if not _is_claude_proc_alive():
                log(f"CLAUDE_LOGIN process exited")
                # 마지막 확인
                if os.path.exists(creds_path) and os.path.getmtime(creds_path) > old_mtime:
                    try:
                        with open(creds_path, "r", encoding="utf-8") as f:
                            creds = f.read()
                        json.loads(creds)
                    except: pass
                break
            time.sleep(0.2)

        # 프로세스 정리
        if _is_claude_proc_alive():
            try: os.kill(_claude_login_proc, 9)
            except Exception: pass

        if creds:
            now = datetime.now().isoformat()
            name = (body.get("name") or "").strip() or f"Account {datetime.now().strftime('%m-%d %H:%M')}"
            try:
                aid = db_exec(
                    "INSERT INTO claude_accounts (name, credentials, active, created) VALUES (?,?,?,?)",
                    (name, creds, 0, now)
                )
                try: _sync_account_to_dir(aid, creds)
                except Exception as e: log(f"sync acct dir failed: {e}")
                return {"ok": True, "accountId": aid, "name": name, "scope": "full"}
            except Exception as e:
                return {"ok": False, "error": f"DB 저장 실패: {e}"}
        else:
            err_msg = "로그인 실패 — "
            if cli_error:
                if "Invalid code" in cli_error:
                    err_msg += "❌ 유효하지 않은 코드입니다.\n\n1. 취소 후 새 URL로 다시 시작\n2. Anthropic 승인 후 나오는 코드 복사 (# 포함 전체)\n3. 붙여넣고 완료"
                else:
                    err_msg += cli_error[-200:]
            else:
                err_msg += "credentials.json이 갱신되지 않았습니다. 코드가 만료되었을 수 있습니다."
            return {
                "ok": False,
                "error": err_msg,
                "output": "\n".join(_claude_login_output[-60:])
            }

    def _claude_login_status(self):
        if not _claude_login_proc:
            return {"ok": True, "running": False}
        running = _is_claude_proc_alive()
        return {
            "ok": True,
            "running": running,
            "url": _claude_login_url,
            "output": "\n".join(_claude_login_output[-10:])
        }

    def _claude_login_cancel(self, body):
        global _claude_login_proc
        if _claude_login_proc and _is_claude_proc_alive():
            try:
                os.kill(_claude_login_proc, 9)
            except Exception:
                pass
        return {"ok": True}

    # ── System Settings ──

    def _settings_get(self):
        rows = db_exec("SELECT key, value FROM system_settings", fetch=True)
        settings = {r["key"]: r["value"] for r in rows}
        return {"ok": True, "settings": settings}

    def _settings_set(self, body):
        key = body.get("key", "")
        value = body.get("value", "")
        if not key:
            return {"ok": False, "error": "key required"}
        now = datetime.now().isoformat()
        existing = db_exec("SELECT key FROM system_settings WHERE key=?", (key,), fetchone=True)
        if existing:
            db_exec("UPDATE system_settings SET value=?, updated=? WHERE key=?", (value, now, key))
        else:
            db_exec("INSERT INTO system_settings (key, value, updated) VALUES (?,?,?)", (key, value, now))

        # Special handling: claude_credentials → write to ~/.claude/.credentials.json
        if key == "claude_credentials" and value:
            try:
                json.loads(value)  # Validate JSON
                claude_dir = os.path.expanduser("~/.claude")
                os.makedirs(claude_dir, exist_ok=True)
                creds_path = os.path.join(claude_dir, ".credentials.json")
                with open(creds_path, "w", encoding="utf-8") as f:
                    f.write(value)
                try:
                    os.chmod(creds_path, 0o600)
                except Exception:
                    pass
                log(f"Claude credentials saved to {creds_path}")
            except json.JSONDecodeError as e:
                log(f"Failed to save Claude credentials: invalid JSON")
                return {"ok": False, "error": f"Invalid JSON: {e}"}
            except Exception as e:
                log(f"Failed to save Claude credentials: {e}")
                return {"ok": False, "error": str(e)}

        return {"ok": True}

    def _settings_delete(self, body):
        key = body.get("key", "")
        if key:
            db_exec("DELETE FROM system_settings WHERE key=?", (key,))
        return {"ok": True}

    # ── File Browser ──

    def _browse_path(self, params):
        """Browse filesystem from workspace root."""
        row = db_exec("SELECT value FROM system_settings WHERE key='workspace_root'", fetchone=True)
        root = row["value"] if row and row.get("value") else os.path.expanduser("~")
        root = os.path.normpath(root)

        rel_path = params.get("path", [""])[0]
        # Security: only allow paths within root
        target = os.path.normpath(os.path.join(root, rel_path.lstrip("/").lstrip("\\")))
        if not target.startswith(root):
            return {"ok": False, "error": "접근 거부 (root 밖)"}

        if not os.path.isdir(target):
            return {"ok": False, "error": "디렉토리 아님"}

        items = []
        try:
            for name in sorted(os.listdir(target)):
                if name.startswith("."):
                    continue
                # .trash 폴더는 일반 브라우징에서 숨김 (루트에서만 발생 가능)
                if name == ".trash":
                    continue
                full = os.path.join(target, name)
                try:
                    is_dir = os.path.isdir(full)
                    items.append({
                        "name": name,
                        "path": os.path.relpath(full, root).replace("\\", "/"),
                        "isDir": is_dir,
                        "size": os.path.getsize(full) if not is_dir else None
                    })
                except Exception:
                    continue
        except Exception as e:
            return {"ok": False, "error": str(e)}

        return {"ok": True, "root": root.replace("\\", "/"), "path": rel_path, "items": items}

    def _fs_mkdir(self, body):
        """workspace root 내에 폴더 생성"""
        row = db_exec("SELECT value FROM system_settings WHERE key='workspace_root'", fetchone=True)
        root = row["value"] if row and row.get("value") else os.path.expanduser("~")

        rel_path = body.get("path", "").strip()
        name = body.get("name", "").strip()
        if not name:
            return {"ok": False, "error": "폴더 이름이 필요합니다"}
        # Sanitize name
        if any(c in name for c in "/\\:*?\"<>|"):
            return {"ok": False, "error": "폴더 이름에 사용할 수 없는 문자가 있습니다"}

        target = os.path.normpath(os.path.join(root, rel_path.lstrip("/").lstrip("\\"), name))
        # Security: must be within root
        root_abs = os.path.abspath(root)
        target_abs = os.path.abspath(target)
        if not target_abs.startswith(root_abs):
            return {"ok": False, "error": "접근 거부 (root 밖)"}

        try:
            os.makedirs(target_abs, exist_ok=True)
            return {"ok": True, "path": os.path.relpath(target_abs, root_abs).replace("\\", "/")}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _fs_get_root(self):
        row = db_exec("SELECT value FROM system_settings WHERE key='workspace_root'", fetchone=True)
        root = row["value"] if row and row.get("value") else os.path.expanduser("~")
        return os.path.normpath(os.path.abspath(root))

    def _fs_delete(self, body):
        """파일/폴더를 휴지통으로 이동"""
        import shutil
        root = self._fs_get_root()
        rel = body.get("path", "").strip()
        if not rel: return {"ok": False, "error": "path required"}
        target = os.path.normpath(os.path.abspath(os.path.join(root, rel.lstrip("/").lstrip("\\"))))
        if not target.startswith(root):
            return {"ok": False, "error": "접근 거부"}
        if not os.path.exists(target):
            return {"ok": False, "error": "파일/폴더 없음"}
        # .trash 디렉토리 자체는 삭제 못함
        trash_dir = os.path.join(root, ".trash")
        if target == trash_dir or target.startswith(trash_dir + os.sep):
            return {"ok": False, "error": "휴지통 내부 항목은 휴지통 삭제/복원 기능 사용"}

        os.makedirs(trash_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        basename = os.path.basename(target.rstrip(os.sep))
        trash_name = f"{ts}_{basename}"
        trash_path = os.path.join(trash_dir, trash_name)
        # 중복 시 suffix
        i = 1
        while os.path.exists(trash_path):
            trash_path = os.path.join(trash_dir, f"{ts}_{i}_{basename}")
            i += 1
        try:
            shutil.move(target, trash_path)
            # 메타 저장
            meta = {
                "original_path": rel.replace("\\", "/"),
                "original_name": basename,
                "deleted_at": datetime.now().isoformat(),
                "was_dir": os.path.isdir(trash_path)
            }
            with open(trash_path + ".meta.json", "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False)
            return {"ok": True, "trashPath": os.path.basename(trash_path)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _fs_trash_list(self, body):
        root = self._fs_get_root()
        trash_dir = os.path.join(root, ".trash")
        if not os.path.isdir(trash_dir):
            return {"ok": True, "items": []}
        items = []
        for name in sorted(os.listdir(trash_dir), reverse=True):
            if name.endswith(".meta.json"): continue
            full = os.path.join(trash_dir, name)
            meta_path = full + ".meta.json"
            meta = {}
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                except: pass
            try:
                size = os.path.getsize(full) if os.path.isfile(full) else None
            except: size = None
            items.append({
                "trashName": name,
                "originalPath": meta.get("original_path", ""),
                "originalName": meta.get("original_name", name),
                "deletedAt": meta.get("deleted_at", ""),
                "wasDir": os.path.isdir(full),
                "size": size
            })
        return {"ok": True, "items": items}

    def _fs_trash_restore(self, body):
        import shutil
        root = self._fs_get_root()
        trash_dir = os.path.join(root, ".trash")
        trash_name = body.get("trashName", "").strip()
        if not trash_name or "/" in trash_name or "\\" in trash_name:
            return {"ok": False, "error": "잘못된 이름"}
        trash_path = os.path.join(trash_dir, trash_name)
        if not os.path.exists(trash_path):
            return {"ok": False, "error": "파일 없음"}
        # 메타 읽기
        meta_path = trash_path + ".meta.json"
        original_path = ""
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            original_path = meta.get("original_path", "")
        except: pass
        if not original_path:
            # 메타 없으면 루트에 복원
            original_path = body.get("originalName") or trash_name
        target = os.path.normpath(os.path.abspath(os.path.join(root, original_path.lstrip("/"))))
        if not target.startswith(root):
            return {"ok": False, "error": "경로 오류"}
        # 대상 이미 존재 시 suffix
        if os.path.exists(target):
            base, ext = os.path.splitext(target)
            i = 1
            while os.path.exists(f"{base}_restored{i}{ext}"):
                i += 1
            target = f"{base}_restored{i}{ext}"
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.move(trash_path, target)
            if os.path.exists(meta_path):
                try: os.remove(meta_path)
                except: pass
            return {"ok": True, "restoredTo": os.path.relpath(target, root).replace("\\", "/")}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _fs_trash_delete(self, body):
        import shutil
        root = self._fs_get_root()
        trash_dir = os.path.join(root, ".trash")
        trash_name = body.get("trashName", "").strip()
        if not trash_name or "/" in trash_name or "\\" in trash_name:
            return {"ok": False, "error": "잘못된 이름"}
        trash_path = os.path.join(trash_dir, trash_name)
        try:
            if os.path.isdir(trash_path):
                shutil.rmtree(trash_path)
            elif os.path.isfile(trash_path):
                os.remove(trash_path)
            meta_path = trash_path + ".meta.json"
            if os.path.exists(meta_path):
                os.remove(meta_path)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _fs_trash_empty(self, body):
        import shutil
        root = self._fs_get_root()
        trash_dir = os.path.join(root, ".trash")
        if not os.path.isdir(trash_dir):
            return {"ok": True}
        try:
            shutil.rmtree(trash_dir)
            os.makedirs(trash_dir, exist_ok=True)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _fs_download(self, params):
        """파일 다운로드 - workspace root 내만 허용"""
        row = db_exec("SELECT value FROM system_settings WHERE key='workspace_root'", fetchone=True)
        root = row["value"] if row and row.get("value") else os.path.expanduser("~")
        root = os.path.normpath(os.path.abspath(root))
        rel_path = params.get("path", [""])[0]
        target = os.path.normpath(os.path.abspath(os.path.join(root, rel_path.lstrip("/").lstrip("\\"))))
        if not target.startswith(root):
            self.send_error(403, "Access denied")
            return
        if not os.path.isfile(target):
            self.send_error(404, "Not a file")
            return
        try:
            import mimetypes, urllib.parse
            mime = mimetypes.guess_type(target)[0] or 'application/octet-stream'
            fname = os.path.basename(target)
            encoded = urllib.parse.quote(fname)
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", os.path.getsize(target))
            # 브라우저 미리보기 vs 다운로드 선택
            as_download = params.get("download", ["0"])[0] == "1"
            if as_download:
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{encoded}")
            else:
                self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{encoded}")
            self.end_headers()
            with open(target, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk: break
                    self.wfile.write(chunk)
        except Exception as e:
            self.send_error(500, str(e))

    def _fs_browse_system(self, params):
        """시스템 전체 파일 탐색 (workspace_root 선택용). 절대경로 기준."""
        path = params.get("path", ["/"])[0] or "/"
        try:
            path = os.path.abspath(path)
        except:
            path = "/"
        if not os.path.isdir(path):
            return {"ok": False, "error": "디렉토리가 아닙니다", "path": path}
        items = []
        try:
            for name in sorted(os.listdir(path)):
                # 숨김 폴더 건너뛰기
                if name.startswith("."): continue
                full = os.path.join(path, name)
                try:
                    if os.path.isdir(full):
                        items.append({"name": name, "path": full, "isDir": True})
                except:
                    continue
        except PermissionError:
            return {"ok": False, "error": "접근 권한 없음", "path": path}
        except Exception as e:
            return {"ok": False, "error": str(e), "path": path}
        # 추천 시작 경로들 (root일 때만)
        suggestions = []
        if path == "/":
            for p in ["/synology", "/workspace", "/app", "/mnt", "/home", "/volume1"]:
                if os.path.isdir(p):
                    suggestions.append(p)
        return {"ok": True, "path": path, "items": items, "suggestions": suggestions}

    def _fs_mkdir_system(self, body):
        """절대 경로에 폴더 생성 (workspace_root 설정용)."""
        parent = body.get("path", "").strip()
        name = body.get("name", "").strip()
        if not name:
            return {"ok": False, "error": "이름이 필요합니다"}
        if any(c in name for c in "/\\:*?\"<>|"):
            return {"ok": False, "error": "사용할 수 없는 문자가 있습니다"}
        if not parent or not os.path.isdir(parent):
            return {"ok": False, "error": "부모 경로가 올바르지 않습니다"}
        target = os.path.join(parent, name)
        try:
            os.makedirs(target, exist_ok=True)
            return {"ok": True, "path": target}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Auth ──

    def _check_auth(self):
        cookie_header = self.headers.get("Cookie", "")
        token = None
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith("session="):
                token = part[8:]
                break
        if not token:
            return False
        session = db_exec("SELECT * FROM sessions WHERE token=?", (token,), fetchone=True)
        if not session:
            return False
        if datetime.fromisoformat(session["expires"]) < datetime.now():
            db_exec("DELETE FROM sessions WHERE token=?", (token,))
            return False
        return True

    def _auth_check(self):
        if self._check_auth():
            return {"ok": True, "authenticated": True}
        return {"ok": True, "authenticated": False}

    def _handle_auth_login(self, body):
        username = body.get("username", "")
        password = body.get("password", "")
        if not username or not password:
            self._json({"ok": False, "error": "아이디와 비밀번호를 입력하세요"})
            return

        user = db_exec("SELECT id, username, password_hash, salt FROM users WHERE username=?", (username,), fetchone=True)
        if not user:
            time.sleep(1)
            self._json({"ok": False, "error": "아이디 또는 비밀번호가 잘못되었습니다"})
            return

        pw_hash, _ = hash_password(password, user["salt"])
        if pw_hash != user["password_hash"]:
            time.sleep(1)
            self._json({"ok": False, "error": "아이디 또는 비밀번호가 잘못되었습니다"})
            return

        token = secrets.token_hex(32)
        expires = (datetime.now() + timedelta(days=7)).isoformat()
        db_exec("INSERT INTO sessions (token, user_id, username, created, expires) VALUES (?,?,?,?,?)",
                (token, user["id"], username, datetime.now().isoformat(), expires))

        result = {"ok": True, "token": token, "username": username}
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        origin = self._get_cors_origin()
        self.send_header("Access-Control-Allow-Origin", origin)
        if origin != "*":
            self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Set-Cookie", f"session={token}; Path=/; HttpOnly; Max-Age=604800")
        self.end_headers()
        self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        log(f"AUTH login: {username}")

    def _handle_auth_logout(self, body):
        cookie_header = self.headers.get("Cookie", "")
        token = None
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith("session="):
                token = part[8:]
                break
        if token:
            db_exec("DELETE FROM sessions WHERE token=?", (token,))

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        origin = self._get_cors_origin()
        self.send_header("Access-Control-Allow-Origin", origin)
        if origin != "*":
            self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Set-Cookie", "session=; Path=/; HttpOnly; Max-Age=0")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8"))
        log("AUTH logout")

    # ── Response ──

    def _get_cors_origin(self):
        origin = self.headers.get("Origin", "")
        return origin if origin else "*"

    def _json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        origin = self._get_cors_origin()
        self.send_header("Access-Control-Allow-Origin", origin)
        if origin != "*":
            self.send_header("Access-Control-Allow-Credentials", "true")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def log_message(self, format, *args):
        pass  # 조용한 HTTP 로그


class ThreadedServer(ThreadingMixIn, HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

if __name__ == "__main__":
    os.chdir(BASE_DIR)
    acquire_lock()
    server = ThreadedServer(("0.0.0.0", PORT), TmuxHandler)
    if _is_pg():
        db_info = f"postgresql://{CONFIG.get('db_user','canvas')}@{CONFIG.get('db_host','127.0.0.1')}:{CONFIG.get('db_port',5432)}/{CONFIG.get('db_name','canvas_db')}"
    else:
        db_info = DB_PATH
    print(f"╔══════════════════════════════════════════════════╗")
    print(f"║  Gil's FlowDesk on ::{PORT}                    ║")
    print(f"║  DB: {db_info}")
    print(f"║  User: {_get_machine_id()}")
    print(f"╚══════════════════════════════════════════════════╝")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutdown")
        _remove_lock()
        server.server_close()
