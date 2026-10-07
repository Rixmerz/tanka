"""Memory module: schema and migration, FTS ranking and the LIKE fallback, dedupe, secrets, scope
validation and isolation, archive, limits, output cap, the CLI, the tools and install."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "modules" / "memory"))
sys.path.insert(0, str(REPO / "plugin" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory as m  # noqa: E402
import tanka_modules as tm  # noqa: E402
import tanka_tools as tt  # noqa: E402
from workspace_case import FAKE_KEY, load_cli  # noqa: E402

cli = load_cli("memory")
TOOLS = REPO / "modules" / "memory" / "skill" / "tools"


class MemoryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env({"TANKA_MEMORY_HOME": str(self.tmp / "memory"), "TANKA_MEMORY_NO_FTS": None,
                  "TANKA_MEMORY_MAX_ROWS": None, "TANKA_MEMORY_MAX_CHARS": None})

    def env(self, values: dict):
        for k, v in values.items():
            old = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
            self.addCleanup(lambda k=k, old=old: os.environ.pop(k, None) if old is None else os.environ.__setitem__(k, old))

    def seed(self, scope="duck"):
        m.remember(scope, "Clara Client wants invoices on the 1st of each month.", "preference", "clara-client")
        m.remember(scope, "PROJ-123 uses PostgreSQL because the team already runs it.", "decision", "proj-123")
        m.remember(scope, "Invoices invoices invoices: Clara Client invoices go to someone@example.com.", "fact")


class TestSchema(MemoryCase):
    def test_creates_schema_with_version_wal_and_fts(self):
        with contextlib.closing(m.connect("duck")) as con:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], m.SCHEMA_VERSION)
            self.assertEqual(con.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertTrue(m.has_fts(con))
        self.assertTrue((self.tmp / "memory" / "duck.db").is_file())

    def test_migration_runs_new_steps_once(self):
        calls = []
        old = dict(m.MIGRATIONS)
        self.addCleanup(lambda: (m.MIGRATIONS.clear(), m.MIGRATIONS.update(old)))
        m.connect("duck").close()
        m.MIGRATIONS[2] = lambda con: calls.append(con.execute("ALTER TABLE memories ADD COLUMN extra TEXT"))
        m.connect("duck").close()
        m.connect("duck").close()
        self.assertEqual(len(calls), 1)
        with contextlib.closing(sqlite3.connect(m.db_path("duck"))) as con:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_fts_is_added_to_a_database_made_without_it(self):
        self.env({"TANKA_MEMORY_NO_FTS": "1"})
        m.remember("duck", "Clara Client prefers calls in the morning.")
        self.env({"TANKA_MEMORY_NO_FTS": None})
        self.assertEqual([r["id"] for r in m.search("duck", "morning")], [1])


class TestSearch(MemoryCase):
    def test_fts_ranks_and_filters(self):
        self.seed()
        rows = m.search("duck", "invoices")
        self.assertEqual(rows[0]["id"], 3)  # most mentions rank first under bm25
        self.assertEqual({r["id"] for r in rows}, {1, 3})
        self.assertEqual([r["id"] for r in m.search("duck", "invoices", kind="preference")], [1])
        self.assertEqual([r["id"] for r in m.search("duck", "", tag="proj-123")], [2])
        self.assertEqual([r["id"] for r in m.search("duck", "")], [3, 2, 1])  # recent mode
        self.assertEqual(m.search("duck", 'weird "quote" OR ('), [])  # query syntax never leaks to FTS

    def test_like_fallback(self):
        self.env({"TANKA_MEMORY_NO_FTS": "1"})
        self.seed()
        self.assertEqual({r["id"] for r in m.search("duck", "clara invoices")}, {1, 3})
        self.assertEqual([r["id"] for r in m.search("duck", "postgresql team")], [2])
        self.assertIn("like", m.stats("duck")["search"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["stats", "duck"])
        self.assertIn("FTS5 unavailable or disabled", out.getvalue())

    def test_render_and_output_cap(self):
        for i in range(30):
            m.remember("duck", f"Note {i} about Clara Client: " + "detail " * 280)
        text = m.render_search("duck", {"query": "Clara", "limit": 30})
        self.assertLessEqual(len(text), tt.MAX_OUTPUT_CHARS)
        self.assertIn("[truncated", text)
        self.assertTrue(text.startswith("30 memories matching 'Clara'"))
        one = m.render_search("duck", {"id": 1})
        self.assertIn("detail " * 200, one)
        self.assertIn("0 memories", m.render_search("duck", {"query": "nothing-here"}))


class TestRemember(MemoryCase):
    def test_dedupe_by_normalized_text(self):
        a = m.remember("duck", "Clara Client wants invoices on the 1st.")
        b = m.remember("duck", "  clara client WANTS invoices on the 1st ")
        self.assertEqual(a["id"], b["id"])
        self.assertTrue(b["existed"])
        self.assertEqual(m.stats("duck")["total"], 1)

    def test_refuses_secrets(self):
        for text in (f"The key is {FAKE_KEY}", "db password= hunter2hunter2", "-----BEGIN RSA PRIVATE KEY-----"):
            with self.subTest(text=text[:12]), self.assertRaisesRegex(m.ToolError, "without the secret"):
                m.remember("duck", text)
        self.assertFalse(m.db_path("duck").exists())

    def test_validation_and_limits(self):
        with self.assertRaisesRegex(m.ToolError, "kind must be"):
            m.remember("duck", "x", "gossip")
        with self.assertRaisesRegex(m.ToolError, "At most 8 tags"):
            m.remember("duck", "x", tags="a,b,c,d,e,f,g,h,i")
        self.env({"TANKA_MEMORY_MAX_CHARS": "20"})
        with self.assertRaisesRegex(m.ToolError, "limit is 20"):
            m.remember("duck", "x" * 21)
        self.env({"TANKA_MEMORY_MAX_ROWS": "2"})
        m.remember("duck", "one")
        m.remember("duck", "two")
        with self.assertRaisesRegex(m.ToolError, "full"):
            m.remember("duck", "three")
        self.assertTrue(m.remember("duck", "two")["existed"])  # a duplicate still answers


class TestScope(MemoryCase):
    def test_bad_scopes_never_touch_the_disk(self):
        for bad in ("../etc", "a/b", "Duck", "", ".hidden", "x" * 65, "a\x00b"):
            with self.subTest(scope=bad), self.assertRaises(m.ToolError):
                m.remember(bad, "Clara Client")
        self.assertFalse((self.tmp / "memory").exists())
        self.assertFalse((self.tmp / "etc.db").exists())

    def test_scopes_never_see_each_other(self):
        self.seed("duck")
        m.remember("goose", "Goose only fact about PROJ-123.")
        self.assertEqual(len(m.search("goose", "")), 1)
        self.assertEqual(m.search("goose", "invoices"), [])
        self.assertEqual(m.search("duck", "goose"), [])
        with self.assertRaises(m.ToolError):
            m.get("goose", 2)


class TestArchive(MemoryCase):
    def test_archive_hides_and_restore_brings_back(self):
        self.seed()
        self.assertTrue(m.set_archived("duck", 1, True)["archived"])
        self.assertNotIn(1, [r["id"] for r in m.search("duck", "invoices")])
        self.assertEqual([r["id"] for r in m.search("duck", "", archived=True)], [1])
        self.assertTrue(m.remember("duck", "Clara Client wants invoices on the 1st of each month.")["archived"])
        m.set_archived("duck", 1, False)
        self.assertIn(1, [r["id"] for r in m.search("duck", "invoices")])
        with self.assertRaisesRegex(m.ToolError, "memory_search"):
            m.set_archived("duck", 99, True)


class TestCli(MemoryCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def test_forget_needs_confirmation_or_yes(self):
        self.seed()
        code, out = self.run_cli("forget", "duck", "1")  # stdin is not a terminal in tests
        self.assertEqual(code, 1)
        self.assertIn("--yes", out)
        self.assertEqual(self.run_cli("forget", "duck", "1", "--yes")[0], 0)
        self.assertEqual(m.stats("duck")["total"], 2)
        self.assertEqual(m.search("duck", "month"), [])  # gone from the index too

    def test_list_show_search_export_stats(self):
        self.seed()
        self.assertIn("PROJ-123", self.run_cli("list", "duck")[1])
        self.assertIn("PostgreSQL", self.run_cli("search", "duck", "postgresql")[1])
        self.assertIn("source: -", self.run_cli("show", "duck", "2")[1])
        rows = json.loads(self.run_cli("export", "duck", "--json")[1])
        self.assertEqual(len(rows), 3)
        self.assertNotIn("norm", rows[0])
        self.assertIn("3 memories", self.run_cli("stats")[1])
        self.assertEqual(self.run_cli("list", "/tmp/Bad Name")[0], 1)
        self.assertEqual(self.run_cli("post-install", str(self.tmp / "ws"), "duck")[0], 0)


class TestTools(MemoryCase):
    def setUp(self):
        super().setUp()
        self.ws = self.tmp / "duck"
        (self.ws / ".tanka").mkdir(parents=True)
        (self.ws / ".tanka" / "policy.json").write_text("{}")
        tt.skills_dir(self.ws).mkdir(parents=True)

    def test_module_check_and_manifests(self):
        self.assertEqual(tm.check("memory"), [])
        for f in TOOLS.glob("*.json"):
            with self.subTest(tool=f.stem):
                self.assertEqual(tt.validate_manifest(json.loads(f.read_text()), "memory", f.stem), [])
        effects = {f.stem: json.loads(f.read_text())["effect"] for f in TOOLS.glob("*.json")}
        self.assertEqual(effects, {"memory_search": "read", "memory_remember": "draft", "memory_archive": "modify"})

    def test_install_and_run_the_tools(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tm.install("memory", self.ws, "duck"), 0)
        tools, problems = tt.scan(self.ws)
        self.assertEqual(problems, [])
        self.assertEqual(sorted(tools), ["memory_archive", "memory_remember", "memory_search"])
        self.assertIn('SCOPE = "duck"', (tt.skills_dir(self.ws) / "memory" / "tools" / "memory_search.py").read_text())
        env = dict(os.environ, TANKA_PLUGIN_DIR=str(REPO / "plugin"))

        def call(name, args):
            return subprocess.run([sys.executable, f"{name}.py"], input=json.dumps(args), capture_output=True,
                                  text=True, env=env, cwd=tt.skills_dir(self.ws) / "memory" / "tools")
        p = call("memory_remember", {"text": "Clara Client prefers email.", "kind": "preference"})
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "Stored memory 1."))
        self.assertIn("Already remembered as memory 1", call("memory_remember", {"text": "clara client prefers email"}).stdout)
        self.assertIn("1 memory matching", call("memory_search", {"query": "email"}).stdout)
        self.assertIn("Archived memory 1", call("memory_archive", {"id": 1}).stdout)
        self.assertIn("Restored memory 1", call("memory_archive", {"id": 1, "restore": True}).stdout)
        p = call("memory_remember", {"text": f"key {FAKE_KEY}"})
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("without the secret", p.stderr)
        self.assertTrue((self.tmp / "memory" / "duck.db").is_file())

    def test_tanka_modules_check_prints_ok(self):
        p = subprocess.run([str(REPO / "bin" / "tanka"), "modules", "check", "memory"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("memory: ok", p.stdout)


if __name__ == "__main__":
    unittest.main()
