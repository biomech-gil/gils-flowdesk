#!/usr/bin/env python3
"""Quick migration: SQLite → PostgreSQL (no FK constraints)"""
import json, os, sqlite3, psycopg2, psycopg2.extras

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, "config.json")) as f:
    cfg = json.load(f)

# Connect
sqlite_path = cfg.get("db_path", os.path.join(BASE_DIR, "canvas.db"))
print(f"SQLite: {sqlite_path}")
sq = sqlite3.connect(sqlite_path)
sq.row_factory = sqlite3.Row

pg = psycopg2.connect(
    host=cfg["db_host"], port=cfg["db_port"],
    dbname=cfg["db_name"], user=cfg["db_user"], password=cfg["db_password"]
)
pg.autocommit = True
cur = pg.cursor()

# Drop all tables and recreate WITHOUT foreign keys
cur.execute("DROP TABLE IF EXISTS messages, conversations, executions, memos, temps, memo_folders, projects CASCADE")
print("Dropped all tables")

cur.execute("""
CREATE TABLE projects (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, data TEXT NOT NULL,
    created TEXT NOT NULL, modified TEXT NOT NULL, favorite INTEGER DEFAULT 0
);
CREATE TABLE temps (
    id SERIAL PRIMARY KEY, name TEXT, data TEXT NOT NULL,
    date TEXT NOT NULL, created TEXT NOT NULL
);
CREATE TABLE memo_folders (
    id SERIAL PRIMARY KEY, name TEXT NOT NULL, icon TEXT DEFAULT '📁',
    color TEXT DEFAULT '', sort_order INTEGER DEFAULT 0, created TEXT NOT NULL
);
CREATE TABLE memos (
    id SERIAL PRIMARY KEY, name TEXT, content TEXT DEFAULT '',
    folder_id INTEGER, is_temp INTEGER DEFAULT 1, pinned INTEGER DEFAULT 0,
    color TEXT DEFAULT '', created TEXT NOT NULL, modified TEXT NOT NULL
);
CREATE TABLE executions (
    id TEXT PRIMARY KEY, project_id TEXT, node_id TEXT NOT NULL,
    node_name TEXT NOT NULL, input_raw TEXT, input_resolved TEXT,
    output TEXT, status TEXT DEFAULT 'running', chat_only INTEGER DEFAULT 1,
    started TEXT NOT NULL, finished TEXT
);
CREATE TABLE conversations (
    id TEXT PRIMARY KEY, parent_exec_id TEXT, node_id TEXT NOT NULL,
    node_name TEXT NOT NULL, title TEXT, created TEXT NOT NULL
);
CREATE TABLE messages (
    id SERIAL PRIMARY KEY, conv_id TEXT NOT NULL, role TEXT NOT NULL,
    content TEXT NOT NULL, ts TEXT NOT NULL
);
""")
print("Created all tables (no FK)")

# Migrate data table by table
tables = [
    ("projects", "id, name, data, created, modified, favorite"),
    ("memo_folders", "id, name, icon, sort_order, created"),
    ("temps", "id, name, data, date, created"),
    ("memos", "id, name, content, folder_id, is_temp, created, modified"),
    ("executions", "id, project_id, node_id, node_name, input_raw, input_resolved, output, status, chat_only, started, finished"),
    ("conversations", "id, parent_exec_id, node_id, node_name, title, created"),
    ("messages", "id, conv_id, role, content, ts"),
]

for table, cols in tables:
    try:
        rows = sq.execute(f"SELECT {cols} FROM {table}").fetchall()
    except:
        print(f"  [{table}] not found in SQLite, skip")
        continue

    col_list = [c.strip() for c in cols.split(",")]
    placeholders = ", ".join(["%s"] * len(col_list))
    count = 0
    for row in rows:
        vals = [row[c] for c in col_list]
        # empty string → None for project_id etc
        vals = [None if v == '' else v for v in vals]
        try:
            cur.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) ON CONFLICT DO NOTHING", vals)
            count += 1
        except Exception as e:
            pg.rollback() if not pg.autocommit else None
            print(f"  [{table}] skip row: {str(e)[:80]}")

    # Reset serial sequences
    if table in ("temps", "memo_folders", "memos", "messages"):
        try:
            cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}")
            max_id = cur.fetchone()[0]
            cur.execute(f"SELECT pg_catalog.setval(pg_get_serial_sequence('{table}', 'id'), {max_id + 1})")
        except:
            pass

    print(f"  [{table}] {count}/{len(rows)} rows migrated")

# Add pinned/color columns if missing (memos might not have them)
for stmt in [
    "ALTER TABLE memos ADD COLUMN IF NOT EXISTS pinned INTEGER DEFAULT 0",
    "ALTER TABLE memos ADD COLUMN IF NOT EXISTS color TEXT DEFAULT ''",
    "ALTER TABLE memo_folders ADD COLUMN IF NOT EXISTS color TEXT DEFAULT ''",
]:
    try:
        cur.execute(stmt)
    except:
        pass

print("\n✅ Migration complete!")
cur.close()
pg.close()
sq.close()
