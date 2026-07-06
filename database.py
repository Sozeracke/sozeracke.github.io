import os
import re
import sqlite3

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

USE_POSTGRES = DATABASE_URL.startswith("postgresql://")
BASE_DIR = os.path.dirname(__file__)
SQLITE_PATH = os.path.join(BASE_DIR, "blog.db")


class Row(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class CursorResult:
    def __init__(self, cursor=None, rows=None, lastrowid=None):
        self._cursor = cursor
        self._rows = rows
        self.lastrowid = lastrowid

    def fetchone(self):
        if self._cursor is not None:
            row = self._cursor.fetchone()
            if row is None:
                return None
            return Row(dict(row))
        if not self._rows:
            return None
        row = self._rows[0]
        self._rows = self._rows[1:]
        return Row(row)

    def fetchall(self):
        if self._cursor is not None:
            return [Row(dict(row)) for row in self._cursor.fetchall()]
        rows = self._rows or []
        self._rows = []
        return [Row(row) for row in rows]


class DatabaseConnection:
    def __init__(self, conn, backend):
        self._conn = conn
        self._backend = backend

    def _adapt_sql(self, sql):
        if self._backend != "postgres":
            return sql
        sql = re.sub(
            r"INSERT\s+OR\s+IGNORE\s+INTO",
            "INSERT INTO",
            sql,
            flags=re.IGNORECASE,
        )
        if "post_tags" in sql.lower() and "ON CONFLICT" not in sql.upper():
            sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        return sql.replace("?", "%s")

    def execute(self, sql, params=()):
        sql = self._adapt_sql(sql)
        if self._backend == "postgres":
            import psycopg2.extras

            cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            is_insert = sql.strip().upper().startswith("INSERT")
            exec_sql = sql
            if is_insert and "RETURNING" not in sql.upper():
                exec_sql = sql.rstrip().rstrip(";") + " RETURNING id"
            cur.execute(exec_sql, params)
            lastrowid = None
            if is_insert:
                row = cur.fetchone()
                if row:
                    lastrowid = row.get("id")
                return CursorResult(lastrowid=lastrowid)
            rows = cur.fetchall()
            return CursorResult(rows=rows)

        cur = self._conn.execute(sql, params)
        return CursorResult(cursor=cur, lastrowid=cur.lastrowid)

    def executescript(self, script):
        if self._backend == "postgres":
            statements = [s.strip() for s in script.split(";") if s.strip()]
            for statement in statements:
                self.execute(statement)
            return
        self._conn.executescript(script)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def connect():
    if USE_POSTGRES:
        import psycopg2

        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return DatabaseConnection(conn, "postgres")
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return DatabaseConnection(conn, "sqlite")


def table_columns(db, table):
    if USE_POSTGRES:
        rows = db.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (table,),
        ).fetchall()
        return {row["column_name"] for row in rows}
    return {row[1] for row in db._conn.execute(f"PRAGMA table_info({table})")}


def postgres_schema():
    return """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            bio TEXT NOT NULL DEFAULT '',
            avatar TEXT,
            last_seen TEXT
        );

        CREATE TABLE IF NOT EXISTS categories (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            created_by INTEGER REFERENCES users (id)
        );

        CREATE TABLE IF NOT EXISTS posts (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users (id),
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            image TEXT,
            updated_at TEXT,
            category_id INTEGER REFERENCES categories (id)
        );

        CREATE TABLE IF NOT EXISTS comments (
            id SERIAL PRIMARY KEY,
            post_id INTEGER NOT NULL REFERENCES posts (id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users (id),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tags (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS post_tags (
            post_id INTEGER NOT NULL REFERENCES posts (id) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL REFERENCES tags (id) ON DELETE CASCADE,
            PRIMARY KEY (post_id, tag_id)
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id SERIAL PRIMARY KEY,
            user1_id INTEGER NOT NULL REFERENCES users (id),
            user2_id INTEGER NOT NULL REFERENCES users (id),
            created_at TEXT NOT NULL,
            UNIQUE (user1_id, user2_id)
        );

        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            conversation_id INTEGER NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
            sender_id INTEGER NOT NULL REFERENCES users (id),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0
        );
    """


def sqlite_schema():
    return """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        );

        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (post_id) REFERENCES posts (id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users (id)
        );

        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS post_tags (
            post_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            PRIMARY KEY (post_id, tag_id),
            FOREIGN KEY (post_id) REFERENCES posts (id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user1_id INTEGER NOT NULL,
            user2_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (user1_id, user2_id),
            FOREIGN KEY (user1_id) REFERENCES users (id),
            FOREIGN KEY (user2_id) REFERENCES users (id)
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE,
            FOREIGN KEY (sender_id) REFERENCES users (id)
        );
    """