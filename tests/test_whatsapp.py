"""WhatsApp module: media decryption, role scoping and person records, all without a browser."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "modules" / "whatsapp"))
import media  # noqa: E402
import wa  # noqa: E402


def encrypt(plain: bytes, kind: str) -> tuple[bytes, str, str]:
    """What WhatsApp's CDN serves for `plain`: AES-256-CBC body plus a 10-byte MAC, and the key and hash."""
    key = os.urandom(32)
    keys = media.hkdf(key, media.INFO[kind])
    iv, cipher_key, mac_key = keys[:16], keys[16:48], keys[48:80]
    body = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-K", cipher_key.hex(), "-iv", iv.hex()],
                          input=plain, capture_output=True, check=True).stdout
    mac = hmac.new(mac_key, iv + body, hashlib.sha256).digest()[:10]
    return body + mac, base64.b64encode(key).decode(), base64.b64encode(hashlib.sha256(plain).digest()).decode()


@unittest.skipUnless(shutil.which("openssl"), "needs the openssl binary")
class TestMediaDecrypt(unittest.TestCase):
    def test_round_trip_for_every_kind(self):
        for kind in ("image", "ptt", "video", "document"):
            plain = os.urandom(1000) + b"voice note"
            enc, key, digest = encrypt(plain, kind)
            self.assertEqual(media.decrypt(enc, key, kind, digest), plain)

    def test_tampered_file_is_refused(self):
        enc, key, digest = encrypt(b"x" * 64, "image")
        bad = bytes([enc[0] ^ 1]) + enc[1:]
        with self.assertRaises(media.MediaError):
            media.decrypt(bad, key, "image", digest)

    def test_wrong_hash_is_refused(self):
        enc, key, _ = encrypt(b"x" * 64, "audio")
        with self.assertRaises(media.MediaError):
            media.decrypt(enc, key, "audio", base64.b64encode(b"0" * 32).decode())

    def test_too_big_is_not_downloaded(self):
        with self.assertRaises(media.MediaError):
            media.save({"size": media.MAX_BYTES + 1, "directPath": "/x", "mediaKey": "k"}, Path("/nonexistent"))


class RegistryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / "contacts.json").write_text(json.dumps({
            "roles": {"client": {"workspace": "personal", "read": True, "reply": True},
                      "friend": {"workspace": "personal", "read": False, "reply": False},
                      "student": {"workspace": "work", "read": True, "reply": True}},
            "contacts": {"+1 555 555 6666": {"name": "Clara Client", "role": "client"},
                         "+15551112222": {"name": "Ann Friend", "role": "friend"},
                         "+15553334444": {"name": "Pete Student", "role": "student"}}}))
        self.ws = self.tmp / "ws"
        (self.ws / "notes" / "people").mkdir(parents=True)
        patches = {"REGISTRY": self.tmp / "contacts.json", "HOME": self.tmp}
        for name, value in patches.items():
            old = getattr(wa, name)
            setattr(wa, name, value)
            self.addCleanup(setattr, wa, name, old)
        os.environ["TANKA_WORKSPACE"] = str(self.ws)
        self.addCleanup(os.environ.pop, "TANKA_WORKSPACE", None)
        # Any refusal must happen before the browser is touched.
        old = wa.session
        wa.session = lambda: (_ for _ in ()).throw(AssertionError("the browser must not be used"))
        self.addCleanup(setattr, wa, "session", old)


class TestRoleScope(RegistryCase):
    def test_role_that_cannot_be_read(self):
        with self.assertRaisesRegex(wa.ToolError, "does not allow reading"):
            wa.read_thread("personal", "Ann", 5)

    def test_role_that_cannot_be_answered(self):
        with self.assertRaisesRegex(wa.ToolError, "does not allow replying"):
            wa.reply("personal", "15551112222", "hi")

    def test_other_ambito_does_not_exist(self):
        with self.assertRaisesRegex(wa.ToolError, "is not a contact"):
            wa.read_thread("personal", "Pete", 5)
        with self.assertRaisesRegex(wa.ToolError, "is not a contact"):
            wa.reply("work", "+15555556666", "hi")

    def test_unknown_number_does_not_exist(self):
        with self.assertRaisesRegex(wa.ToolError, "is not a contact"):
            wa.reply("personal", "+15550000000", "hi")

    def test_numbers_match_whatever_the_formatting(self):
        num, person = wa.find_person(wa.scope("personal")[1], "+1 (555) 555-6666")
        self.assertEqual((num, person["name"]), ("15555556666", "Clara Client"))


class TestPersonRecords(RegistryCase):
    def test_fields_and_filter(self):
        (self.ws / "notes" / "people" / "15555556666.md").write_text(
            "---\ncompany: Corner Bakery\nSale_Status: Quoted\n---\n2026-09-30: wants the logo in green.\n")
        fields, notes = wa.person_record("15555556666")
        self.assertEqual(fields, {"company": "Corner Bakery", "sale_status": "Quoted"})
        self.assertIn("logo in green", notes)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            wa.list_people("personal", "sale_status=quoted")
        self.assertIn("1 person(s)", out.getvalue())
        self.assertIn("Clara Client", out.getvalue())
        self.assertNotIn("Pete", out.getvalue())

    def test_filter_needs_a_value(self):
        with self.assertRaisesRegex(wa.ToolError, "field=value"):
            wa.list_people("personal", "sale_status")


class TestInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = dict(os.environ, TANKA_WORKSPACES=str(self.tmp / "workspaces"),
                        TANKA_WHATSAPP_HOME=str(self.tmp / "wa-home"))
        self.tanka("init", "sales", "--strict")  # modules must fit the strict budget

    def tanka(self, *args):
        return subprocess.run([str(REPO / "bin" / "tanka"), *args], capture_output=True, text=True, env=self.env)

    def test_install_scopes_the_skill_and_passes_the_checks(self):
        p = self.tanka("install", "whatsapp", "sales")
        self.assertEqual(p.returncode, 0, p.stderr)
        tools = self.tmp / "workspaces" / "sales" / ".claude" / "skills" / "whatsapp" / "tools"
        self.assertIn('SCOPE = "sales"', (tools / "whatsapp_chats.py").read_text())
        self.assertTrue((self.tmp / "wa-home" / "contacts.json").is_file())
        check = self.tanka("tools", "check", "sales")
        self.assertIn("4/15 tools loaded, 0 problem(s), 0 warning(s)", check.stdout)

    def test_scope_override_and_no_overwrite(self):
        self.assertEqual(self.tanka("install", "whatsapp", "sales", "--scope", "shop").returncode, 0)
        tools = self.tmp / "workspaces" / "sales" / ".claude" / "skills" / "whatsapp" / "tools"
        self.assertIn('SCOPE = "shop"', (tools / "whatsapp_reply.py").read_text())
        again = self.tanka("install", "whatsapp", "sales")
        self.assertEqual(again.returncode, 1)
        self.assertIn("already exists", again.stderr)


class TestLauncherReservesWhatsapp(unittest.TestCase):
    def test_whatsapp_is_not_a_workspace_name(self):
        env = dict(os.environ, TANKA_WORKSPACES=tempfile.mkdtemp())
        p = subprocess.run([str(REPO / "bin" / "tanka"), "init", "whatsapp"], capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 1)
        self.assertIn("is a tanka command or module", p.stderr)


if __name__ == "__main__":
    unittest.main()
