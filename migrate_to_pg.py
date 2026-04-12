#!/usr/bin/env python3
"""
SQLite → PostgreSQL Migration Script for Gil's FlowDesk

Usage:
    python3 migrate_to_pg.py [--sqlite-path /path/to/canvas.db]

Reads config.json for PostgreSQL connection info.
If --sqlite-path is not given, uses db_path from config.json.
"""

import json, os, sys, sqlite3

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("[FATAL] psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")


def load_config():
    if not os.path.exists(CONFIG_PATH):
        print(f"[FATAL] config.json not found at {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_pg_conn(cfg):
    return psycopg2.connect(
        host=cfg.get("db_host", "127.0.0.1"),
        port=cfg.get("db_port", 5432),
        dbname=cfg.get("db_name", "canvas_db"),
        user=cfg.get("db_user", "canvas"),
        password=cfg.get("db_password", ""),
        connect_timeout=10
    )


def create_pg_tables(cur):
    """Create all tables in PostgreSQL (idempotent)."""
    stmts = [
        """CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            data TEXT NOT NULL,
            created TEXT NOT NULL,
            modified TEXT NOT NULL,
            favorite INTEGER DEFAULT 0
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
            color TEXT DEFAULT '',
            created TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS memos (
            id SERIAL PRIMARY KEY,
            name TEXT,
            content TEXT DEFAULT '',
            folder_id INTEGER,
            is_temp INTEGER DEFAULT 1,
            pinned INTEGER DEFAULT 0,
            color TEXT DEFAULT '',
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
        "CREATE INDEX IF NOT EXISTS idx_exec_node ON executions(node_id)",
        "CREATE INDEX IF NOT EXISTS idx_conv_node ON conversations(node_id)",
        "CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conv_id)",
    ]
    for s in stmts:
        cur.execute(s)
    print("[+] PostgreSQL tables created")


def migrate_table(sqlite_conn, pg_cur, table, columns, has_serial_id=False):
    """Copy rows from SQLite table to PostgreSQL table."""
    sqlite_cur = sqlite_conn.cursor()
    col_list = ", ".join(columns)
    sqlite_cur.execute(f"SELECT {col_list} FROM {table}")
    rows = sqlite_cur.fetchall()
    if not rows:
        print(f"  [{table}] 0 rows (empty)")
        return 0

    placeholders = ", ".join(["%s"] * len(columns))

    # For SERIAL tables, we need to explicitly insert the id and reset the sequence
    insert_sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"

    count = 0
    for row in rows:
        try:
            pg_cur.execute(insert_sql, row)
            count += 1
        except Exception as e:
            print(f"  [{table}] SKIP row: {e}")

    # Reset SERIAL sequence if needed
    if has_serial_id:
        pg_cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}")
        max_id = pg_cur.fetchone()[0]
        pg_cur.execute(f"SELECT pg_catalog.setval(pg_get_serial_sequence('{table}', 'id'), %s, true)", (max_id,))

    print(f"  [{table}] {count}/{len(rows)} rows migrated")
    return count


def get_sqlite_columns(sqlite_conn, table):
    """Get actual column names from SQLite table."""
    cur = sqlite_conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cur.fetchall()]


def main():
    cfg = load_config()

    # Determine SQLite path
    sqlite_path = cfg.get("db_path", os.path.join(BASE_DIR, "canvas.db"))
    for arg in sys.argv[1:]:
        if arg.startswith("--sqlite-path"):
            # --sqlite-path=/path or --sqlite-path /path
            if "=" in arg:
                sqlite_path = arg.split("=", 1)[1]
            else:
                idx = sys.argv.index(arg)
                if idx + 1 < len(sys.argv):
                    sqlite_path = sys.argv[idx + 1]

    if not os.path.exists(sqlite_path):
        print(f"[FATAL] SQLite database not found: {sqlite_path}")
        sys.exit(1)

    print(f"╔══════════════════════════════════════════════════╗")
    print(f"║  SQLite → PostgreSQL Migration                  ║")
    print(f"╠══════════════════════════════════════════════════╣")
    print(f"║  From: {sqlite_path}")
    print(f"║  To:   {cfg.get('db_user','canvas')}@{cfg.get('db_host','127.0.0.1')}:{cfg.get('db_port',5432)}/{cfg.get('db_name','canvas_db')}")
    print(f"╚══════════════════════════════════════════════════╝")

    # Connect to both databases
    print("\n[1] Connecting to SQLite...")
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    print("[2] Connecting to PostgreSQL...")
    pg_conn = get_pg_conn(cfg)
    pg_conn.autocommit = False
    pg_cur = pg_conn.cursor()

    try:
        print("[3] Creating PostgreSQL tables...")
        create_pg_tables(pg_cur)
        pg_conn.commit()

        print("[4] Migrating data...")
        # Define tables and their columns (order matters for foreign keys)
        tables = [
            ("projects",      ["id", "name", "data", "created", "modified", "favorite"], False),
            ("memo_folders",  None, True),   # None = auto-detect columns
            ("temps",         None, True),
            ("memos",         None, True),
            ("executions",    ["id", "project_id", "node_id", "node_name", "input_raw",
                               "input_resolved", "output", "status", "chat_only", "started", "finished"], False),
            ("conversations", ["id", "parent_exec_id", "node_id", "node_name", "title", "created"], False),
            ("messages",      None, True),
        ]

        total = 0
        for table, columns, has_serial in tables:
            if columns is None:
                columns = get_sqlite_columns(sqlite_conn, table)
            total += migrate_table(sqlite_conn, pg_cur, table, columns, has_serial)

        pg_conn.commit()
        print(f"\n[OK] Migration complete! {total} total rows migrated.")

    except Exception as e:
        pg_conn.rollback()
        print(f"\n[FATAL] Migration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        pg_cur.close()
        pg_conn.close()
        sqlite_conn.close()


if __name__ == "__main__":
    main()
