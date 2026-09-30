"""Download, decrypt and convert WhatsApp media for the assistant to review.

WhatsApp stores media encrypted on its CDN. The message carries the key, so a
file is fetched from `mmg.whatsapp.net<directPath>` and decrypted here with the
documented scheme (HKDF-SHA256 -> AES-256-CBC + truncated HMAC), then checked
against the message's own SHA-256. Standard library plus the `openssl` binary;
no extra Python packages.

The CDN link expires a few hours after the message arrives. After that the file
can only come back if the sender's phone uploads it again, so an expired file is
reported as such instead of guessed at.
"""
import base64
import hashlib
import hmac
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

CDN = "https://mmg.whatsapp.net"
MAX_BYTES = int(os.environ.get("TANKA_WHATSAPP_MAX_MB", "25")) * 1024 * 1024
INFO = {"image": b"WhatsApp Image Keys", "sticker": b"WhatsApp Image Keys", "video": b"WhatsApp Video Keys",
        "gif": b"WhatsApp Video Keys", "audio": b"WhatsApp Audio Keys", "ptt": b"WhatsApp Audio Keys",
        "document": b"WhatsApp Document Keys"}
EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "video/mp4": ".mp4",
       "audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "application/pdf": ".pdf"}


class MediaError(Exception):
    """Why a file could not be made available, in words the assistant can pass on."""


def hkdf(key: bytes, info: bytes, length: int = 112) -> bytes:
    prk = hmac.new(b"\0" * 32, key, hashlib.sha256).digest()
    out, block = b"", b""
    for i in range(1, -(-length // 32) + 1):
        block = hmac.new(prk, block + info + bytes([i]), hashlib.sha256).digest()
        out += block
    return out[:length]


def decrypt(enc: bytes, media_key_b64: str, kind: str, filehash_b64: str | None = None) -> bytes:
    keys = hkdf(base64.b64decode(media_key_b64), INFO[kind])
    iv, cipher_key, mac_key = keys[:16], keys[16:48], keys[48:80]
    body, mac = enc[:-10], enc[-10:]
    if not hmac.compare_digest(hmac.new(mac_key, iv + body, hashlib.sha256).digest()[:10], mac):
        raise MediaError("the file does not match its key (bad signature)")
    p = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-K", cipher_key.hex(), "-iv", iv.hex()],
                       input=body, capture_output=True, timeout=120)
    if p.returncode != 0:
        raise MediaError("the file could not be decrypted")
    if filehash_b64 and base64.b64encode(hashlib.sha256(p.stdout).digest()).decode() != filehash_b64:
        raise MediaError("the decrypted file does not match its hash")
    return p.stdout


def fetch(direct_path: str) -> bytes:
    req = urllib.request.Request(CDN + direct_path, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read(MAX_BYTES + 1024)
    except urllib.error.HTTPError as e:
        if e.code in (403, 404, 410):
            raise MediaError("the WhatsApp link expired; it has to be opened on the phone")
        raise MediaError(f"WhatsApp answered {e.code} to the download")
    except (urllib.error.URLError, TimeoutError) as e:
        raise MediaError(f"the download failed ({e})")
    return data


def extension(mime: str, filename: str | None) -> str:
    if filename and Path(filename).suffix:
        return Path(filename).suffix.lower()[:10]
    base = (mime or "").split(";")[0].strip()
    return EXT.get(base) or ("." + base.split("/")[-1] if "/" in base else ".bin")


def save(m: dict, folder: Path) -> Path:
    """Decrypted copy of message `m` (as the page describes it) in `folder`; reused if already there."""
    if m.get("size") and m["size"] > MAX_BYTES:
        raise MediaError(f"it is {m['size'] // (1024 * 1024)} MB, over the {MAX_BYTES // (1024 * 1024)} MB download limit")
    if not m.get("directPath") or not m.get("mediaKey"):
        raise MediaError("the message does not carry what is needed to download it")
    name = f"{m['stamp']}-{m['type']}-{re.sub(r'[^A-Za-z0-9]', '', m['id'])[-8:]}{extension(m.get('mime'), m.get('filename'))}"
    path = folder / name
    if path.is_file():
        return path
    data = decrypt(fetch(m["directPath"]), m["mediaKey"], m["type"], m.get("filehash"))
    folder.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path
