# core/account_store.py
"""Persistent local account storage for the desktop tool.

On Windows, passwords are protected with the current user's DPAPI profile.
The JSON file therefore contains an encrypted blob rather than the raw password.
"""
import base64
import ctypes
import ctypes.wintypes as wt
import json
import os
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA") or (Path.home() / ".darong_tool")) / "DaRongTool"
STORE_PATH = APP_DIR / "accounts.json"
DEFAULT_SERVER = {"name": "Thảo Nguyên", "ipserver": "daorongsv1.shop", "url": "https://daorongsv1.shop"}
SERVERS = [
    DEFAULT_SERVER,
    {"name": "Sa Mạc", "ipserver": "daorongsv1.shop:52345", "url": "https://daorongsv1.shop:52345"},
    {"name": "Rừng Rậm", "ipserver": "daorongsv1.shop:52346", "url": "https://daorongsv1.shop:52346"},
]


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi_protect(raw: bytes) -> str:
    if os.name != "nt":
        return base64.b64encode(raw).decode("ascii")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_buf = ctypes.create_string_buffer(raw, len(raw))
    in_blob = _DATA_BLOB(len(raw), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_byte)))
    out_blob = _DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise OSError("CryptProtectData failed")
    try:
        encrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(encoded: str) -> bytes:
    blob = base64.b64decode(encoded.encode("ascii"))
    if os.name != "nt":
        return blob
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_buf = ctypes.create_string_buffer(blob, len(blob))
    in_blob = _DATA_BLOB(len(blob), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_byte)))
    out_blob = _DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


class AccountStore:
    """Save/load the accounts that the user wants the app to remember."""

    def __init__(self, path=STORE_PATH, log_fn=print):
        self.path = Path(path)
        self.log = log_fn
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self):
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            accounts = []
            for item in raw.get("accounts", []):
                user = str(item.get("username") or "").strip()
                enc = item.get("password") or ""
                if not user or not enc:
                    continue
                try:
                    password = _dpapi_unprotect(enc).decode("utf-8")
                except Exception as e:
                    self.log(f"⚠️ Không đọc được mật khẩu đã lưu cho {user}: {e}")
                    continue
                accounts.append({
                    "username": user,
                    "password": password,
                    "auto_login": bool(item.get("auto_login", True)),
                    "server_url": str(item.get("server_url") or DEFAULT_SERVER["url"]).strip().rstrip("/"),
                })
            return accounts
        except Exception as e:
            self.log(f"⚠️ Không đọc được danh sách ACC đã lưu: {e}")
            return []

    def save(self, accounts):
        payload = {"version": 2, "accounts": []}
        for item in accounts:
            user = str(item.get("username") or "").strip()
            pw = str(item.get("password") or "")
            if not user or not pw:
                continue
            payload["accounts"].append({
                "username": user,
                "password": _dpapi_protect(pw.encode("utf-8")),
                "auto_login": bool(item.get("auto_login", True)),
                "server_url": str(item.get("server_url") or DEFAULT_SERVER["url"]).strip().rstrip("/"),
            })
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.replace(tmp, self.path)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise
        if os.name != "nt":
            try:
                os.chmod(self.path, 0o600)
            except Exception:
                pass

    def upsert(self, username, password, auto_login=True, server_url=None):
        username = str(username or "").strip()
        if not username or not password:
            return
        accounts = self.load()
        found = False
        for item in accounts:
            if item["username"] == username:
                item["password"] = password
                item["auto_login"] = bool(auto_login)
                item["server_url"] = str(server_url or item.get("server_url") or DEFAULT_SERVER["url"]).strip().rstrip("/")
                found = True
                break
        if not found:
            accounts.append({
                "username": username,
                "password": password,
                "auto_login": bool(auto_login),
                "server_url": str(server_url or DEFAULT_SERVER["url"]).strip().rstrip("/"),
            })
        self.save(accounts)

    def get_servers(self):
        return [dict(x) for x in SERVERS]

    def remove(self, username):
        username = str(username or "").strip()
        self.save([a for a in self.load() if a.get("username") != username])
