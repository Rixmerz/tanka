"""Memory for Tanka: long-term memory for an assistant, one SQLite database per scope.

- Each workspace (scope) has HOME/<scope>.db, outside every workspace, so an assistant reaches it
  only through its tools: memory_search (read), memory_remember (draft), memory_archive (modify).
- Search is full text, ranked by bm25 when this Python's SQLite has FTS5, and a LIKE match on
  every word when it does not (or when TANKA_MEMORY_NO_FTS=1).
- Nothing an assistant does deletes a row; archiving hides it and can be undone. Only the user
  deletes, with `tanka memory forget`.

Standard library only.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

MODULE = Path(__file__).resolve().parent
REPO = MODULE.parents[1]
sys.path.insert(0, str(REPO / "plugin" / "scripts"))
import tanka_kit as kit  # noqa: E402
from tanka_kit import ToolError, run  # noqa: E402,F401

KINDS = ("fact", "decision", "preference", "person", "project", "note")
MAX_TAGS, TAG_CHARS, SOURCE_CHARS = 8, 32, 120
LINE_CHARS = 300
MAX_OUTPUT = 5800                     # under the harness's 6000-character cut
DEFAULT_LIMIT, MAX_LIMIT = 10, 30
SCHEMA_VERSION = 1
SCOPE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


# ---------------------------------------------------------------- settings (read on every call)

def home() -> Path:
    return Path(os.environ.get("TANKA_MEMORY_HOME") or kit.SHARED / "memory")


def max_rows() -> int:
    return int(os.environ.get("TANKA_MEMORY_MAX_ROWS") or 20000)


def max_chars() -> int:
    return int(os.environ.get("TANKA_MEMORY_MAX_CHARS") or 2000)


def fts_wanted() -> bool:
    return os.environ.get("TANKA_MEMORY_NO_FTS", "") not in ("1", "true", "yes")


def check_scope(scope: str) -> str:
    if not isinstance(scope, str) or not SCOPE_RE.fullmatch(scope):
        raise ToolError(f"'{scope}' is not a valid scope: use lowercase letters, digits, - and _. Tell the user.")
    return scope


def db_path(scope: str) -> Path:
    return home() / f"{check_scope(scope)}.db"


# ---------------------------------------------------------------- schema

def has_fts(con: sqlite3.Connection) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE name='memories_fts'").fetchone() is not None


def _v1(con: sqlite3.Connection) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        kind TEXT NOT NULL, text TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0,
        norm TEXT NOT NULL)""")
    con.execute("CREATE INDEX IF NOT EXISTS memories_norm ON memories(norm)")


MIGRATIONS = {1: _v1}  # version -> step that brings the schema to it; add new steps, never edit old ones


def _ensure_fts(con: sqlite3.Connection) -> None:
    if not fts_wanted() or has_fts(con):
        return
    try:
        con.execute("CREATE VIRTUAL TABLE memories_fts USING fts5(text, tags, content='memories', content_rowid='id')")
    except sqlite3.OperationalError:
        return  # this SQLite has no FTS5: search falls back to LIKE
    con.executescript("""
        CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
          INSERT INTO memories_fts(rowid, text, tags) VALUES (new.id, new.text, new.tags); END;
        CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
          INSERT INTO memories_fts(memories_fts, rowid, text, tags) VALUES ('delete', old.id, old.text, old.tags); END;
        CREATE TRIGGER memories_au AFTER UPDATE OF text, tags ON memories BEGIN
          INSERT INTO memories_fts(memories_fts, rowid, text, tags) VALUES ('delete', old.id, old.text, old.tags);
          INSERT INTO memories_fts(rowid, text, tags) VALUES (new.id, new.text, new.tags); END;
        INSERT INTO memories_fts(memories_fts) VALUES ('rebuild');
    """)


def migrate(con: sqlite3.Connection) -> int:
    version = con.execute("PRAGMA user_version").fetchone()[0]
    for v in sorted(MIGRATIONS):
        if v > version:
            with con:
                MIGRATIONS[v](con)
                con.execute(f"PRAGMA user_version = {int(v)}")
            version = v
    with con:
        _ensure_fts(con)
    return version


def connect(scope: str, create: bool = True) -> sqlite3.Connection:
    path = db_path(scope)
    if not create and not path.is_file():
        raise ToolError(f"No memories for scope '{scope}' yet.")
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 5000")
    con.execute("PRAGMA journal_mode = WAL")
    migrate(con)
    return con


# ---------------------------------------------------------------- rules

def stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def looks_secret(text: str) -> bool:
    return any(p.search(text) for p in kit.SECRET_PATTERNS)


def parse_tags(tags) -> str:
    out: list[str] = []
    for t in str(tags or "").split(","):
        t = "-".join(t.strip().lower().split())[:TAG_CHARS]
        if t and t not in out:
            out.append(t)
    if len(out) > MAX_TAGS:
        raise ToolError(f"At most {MAX_TAGS} tags; keep the {MAX_TAGS} that matter most.")
    return ",".join(out)


def remember(scope: str, text: str, kind: str = "note", tags: str = "", source: str = "") -> dict:
    text = str(text or "").strip()
    kind = kind or "note"
    if not text:
        raise ToolError("text is empty: say the fact to remember in one or two sentences.")
    if len(text) > max_chars():
        raise ToolError(f"text has {len(text)} characters; the limit is {max_chars()}. Store a shorter summary.")
    if kind not in KINDS:
        raise ToolError(f"kind must be one of: {', '.join(KINDS)}.")
    source = " ".join(str(source or "").split())[:SOURCE_CHARS]
    tag_s = parse_tags(tags)
    if looks_secret(f"{text} {tag_s} {source}"):
        raise ToolError("That looks like it contains a secret (a key, token or password). Memories never hold "
                        "secrets: store the fact without the secret, e.g. 'the API key is in the team vault'.")
    norm = normalize(text)
    with closing(connect(scope)) as con:
        row = con.execute("SELECT id, archived FROM memories WHERE norm = ?", (norm,)).fetchone()
        if row:
            return {"id": row["id"], "existed": True, "archived": bool(row["archived"])}
        count = con.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        if count >= max_rows():
            raise ToolError(f"This memory is full ({count} memories, limit {max_rows()}). Tell the user: they can "
                            "remove old ones with `tanka memory forget` or raise TANKA_MEMORY_MAX_ROWS.")
        now = stamp()
        with con:
            cur = con.execute("INSERT INTO memories(created_at, updated_at, kind, text, tags, source, archived, norm) "
                              "VALUES (?, ?, ?, ?, ?, ?, 0, ?)", (now, now, kind, text, tag_s, source, norm))
        return {"id": cur.lastrowid, "existed": False, "archived": False}


def get(scope: str, mid: int) -> dict:
    with closing(connect(scope)) as con:
        row = con.execute("SELECT * FROM memories WHERE id = ?", (int(mid),)).fetchone()
    if not row:
        raise ToolError(f"No memory with id {mid}. Use memory_search to see the valid ids.")
    return dict(row)


def set_archived(scope: str, mid: int, archived: bool) -> dict:
    get(scope, mid)
    with closing(connect(scope)) as con, con:
        con.execute("UPDATE memories SET archived = ?, updated_at = ? WHERE id = ?", (int(archived), stamp(), int(mid)))
    return get(scope, mid)


def _fts_query(query: str) -> str:
    words = re.findall(r"\w+", query.lower())
    return " OR ".join(f'"{w}"' for w in words)


def search(scope: str, query: str = "", kind: str | None = None, tag: str | None = None,
           limit: int = DEFAULT_LIMIT, archived: bool = False) -> list[dict]:
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    where, params = ["m.archived = ?"], [int(archived)]
    if kind:
        where.append("m.kind = ?")
        params.append(kind)
    if tag:
        where.append("(',' || m.tags || ',') LIKE ?")
        params.append(f"%,{parse_tags(tag)},%")
    words = re.findall(r"\w+", (query or "").lower())
    with closing(connect(scope)) as con:
        if words and has_fts(con):
            sql = (f"SELECT m.* FROM memories_fts f JOIN memories m ON m.id = f.rowid "
                   f"WHERE memories_fts MATCH ? AND {' AND '.join(where)} ORDER BY bm25(memories_fts), m.id DESC LIMIT ?")
            rows = con.execute(sql, [_fts_query(query)] + params + [limit]).fetchall()
        elif words:
            # Fallback: every word must appear; more matching words in the text ranks first.
            for w in words:
                where.append("(lower(m.text) LIKE ? OR lower(m.tags) LIKE ?)")
                params += [f"%{w}%", f"%{w}%"]
            rows = con.execute(f"SELECT m.* FROM memories m WHERE {' AND '.join(where)} "
                               f"ORDER BY m.updated_at DESC, m.id DESC LIMIT ?", params + [limit]).fetchall()
        else:
            rows = con.execute(f"SELECT m.* FROM memories m WHERE {' AND '.join(where)} "
                               f"ORDER BY m.created_at DESC, m.id DESC LIMIT ?", params + [limit]).fetchall()
    return [dict(r) for r in rows]


def forget(scope: str, mid: int) -> None:
    get(scope, mid)
    with closing(connect(scope)) as con, con:
        con.execute("DELETE FROM memories WHERE id = ?", (int(mid),))


def all_rows(scope: str) -> list[dict]:
    with closing(connect(scope)) as con:
        return [dict(r) for r in con.execute("SELECT * FROM memories ORDER BY id").fetchall()]


def stats(scope: str) -> dict:
    with closing(connect(scope)) as con:
        total, archived = con.execute("SELECT COUNT(*), COALESCE(SUM(archived), 0) FROM memories").fetchone()
        kinds = dict(con.execute("SELECT kind, COUNT(*) FROM memories WHERE archived = 0 GROUP BY kind").fetchall())
        return {"scope": scope, "path": str(db_path(scope)), "total": total, "archived": archived, "kinds": kinds,
                "search": "fts5 (bm25)" if has_fts(con) else "like (FTS5 unavailable or disabled)",
                "schema": con.execute("PRAGMA user_version").fetchone()[0], "max_rows": max_rows()}


# ---------------------------------------------------------------- output

def line(r: dict, full: bool = False) -> str:
    text = " ".join(r["text"].split())
    if not full and len(text) > LINE_CHARS:
        text = text[: LINE_CHARS - 1] + "…"
    flag = " (archived)" if r["archived"] else ""
    return f"{r['id']} | {r['created_at'][:10]} | {r['kind']}{flag} | {r['tags'] or '-'} | {text}"


def cap(text: str, n: int = MAX_OUTPUT) -> str:
    return text if len(text) <= n else text[:n] + "\n[truncated: narrow the search with query, kind or tag, or a lower limit]"


def render_search(scope: str, args: dict) -> str:
    if args.get("id") is not None:
        r = get(scope, args["id"])
        return cap(f"Memory {r['id']}:\n{line(r, full=True)}\nsource: {r['source'] or '-'}; updated {r['updated_at']}")
    q = (args.get("query") or "").strip()
    rows = search(scope, q, args.get("kind"), args.get("tag"), args.get("limit") or DEFAULT_LIMIT)
    what = f"matching '{q}'" if q else "most recent"
    if not rows:
        return f"0 memories {what}. Nothing stored about this: say so; do not guess."
    head = f"{len(rows)} memor{'y' if len(rows) == 1 else 'ies'} {what} (id | date | kind | tags | text):"
    return cap("\n".join([head] + [line(r) for r in rows]))
