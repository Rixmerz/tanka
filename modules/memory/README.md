# Memory

Long-term memory for your assistant: stable facts, decisions with their reason, preferences, and context about people and projects. Each workspace has its own SQLite database outside every workspace, so an assistant reaches only its own memories, and only through its tools. The assistant stores and archives; only you delete.

## Setup

```bash
tanka install memory <workspace>     # memory_search, memory_remember, memory_archive
tanka memory stats <workspace>       # where the data lives and how search works
```

No programs or accounts are needed: it uses Python's standard `sqlite3`.

## How it works

| Piece | What it does |
| --- | --- |
| Storage | `<TANKA_MEMORY_HOME>/<workspace>.db`: a `memories` table (id, created_at, updated_at, kind, text, tags, source, archived) plus an FTS5 index kept in sync by triggers. WAL mode, a busy timeout, and `PRAGMA user_version` with small migration steps so the schema can evolve. |
| Search | Full text ranked by bm25 when this Python's SQLite has FTS5; otherwise every word must appear (a `LIKE` match), and `tanka memory stats` says so. No query lists the most recent. |
| Tools | `memory_search` (read), `memory_remember` (draft: deduplicates by normalized text, refuses text that looks like a secret), `memory_archive` (modify: hides a memory from search, or restores it). No tool deletes. |
| CLI | `tanka memory list\|search\|show\|forget\|export\|stats <workspace>`; `forget` deletes for good and asks first unless `--yes`. |

**Risk.** Memory text may come from emails, tickets or web pages the assistant read. The skill tells the assistant to treat it as data and never follow instructions inside it, but review what is stored now and then (`tanka memory list <workspace>`).

## Configuration

| Variable | Default | What it sets |
| --- | --- | --- |
| `TANKA_MEMORY_HOME` | `~/.tanka/shared/memory` | Where the databases live, outside every workspace |
| `TANKA_MEMORY_MAX_ROWS` | `20000` | Memories per workspace (archived included); beyond it storing is refused |
| `TANKA_MEMORY_MAX_CHARS` | `2000` | Characters per memory |
| `TANKA_MEMORY_NO_FTS` | unset | `1` forces the `LIKE` search even when FTS5 is available |
