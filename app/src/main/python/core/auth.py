# core/auth.py
import re
import time
import uuid
import requests
import urllib3

urllib3.disable_warnings()

AUTH_URL = "https://auth.memobi.vn"
DEFAULT_GAME_URL = "https://daorongsv1.shop"


class ServerMaintenanceError(Exception):
    """Raised when the auth/game service explicitly reports maintenance."""


def _looks_like_maintenance(value):
    try:
        text = str(value).lower()
    except Exception:
        return False
    needles = ("server_maintenance", "maintenance", "bảo trì", "bao tri")
    return any(x in text for x in needles)


DEVICE_ID = "e48877b3bdde4c8194efed3e74ebe406"
APP_VERSION = "1.0.4"
CLIENT_BUILD = "1.0.4"


class Auth:
    def __init__(self, username, password, game_url=DEFAULT_GAME_URL, log_fn=print):
        self.username = username
        self.password = password
        self.game_url = str(game_url or DEFAULT_GAME_URL).strip().rstrip("/")
        # Login bridge is served by the common host; the selected ipserver is used for Socket.IO.
        self.auth_game_url = DEFAULT_GAME_URL
        self.log = log_fn
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "UnityPlayer/2022.3.62f2 (UnityWebRequest/1.0, libcurl/8.10.1-DEV)",
            "Accept": "*/*",
            "X-App-Version": APP_VERSION,
            "X-Client-Type": "Unity-WebView",
            "X-Platform": "Android",
            "X-Unity-Version": "2022.3.62f2",
        })

    def _get_csrf(self):
        r = self.session.get(f"{AUTH_URL}/login?client=unity&platform=android",
                             verify=False, allow_redirects=True, timeout=15)
        m = re.search(r'<meta\s+name="_csrf"\s+content="([^"]+)"', r.text)
        return m.group(1) if m else self.session.cookies.get("XSRF-TOKEN")

    def login(self):
        self.log("[1] Login...")
        csrf = self._get_csrf()
        if not csrf:
            raise Exception("CSRF fail")

        fields = {
            "redirect_uri": "https://account.memobi.vn/account",
            "username": self.username, "password": self.password,
            "captchaId": "", "captchaAnswer": "", "rememberCookies": "on",
            "_csrf": csrf, "deviceName": "Android Mobile",
            "browserName": "Brave", "platformName": "Mobile",
        }
        boundary = "----WebKitFormBoundary" + uuid.uuid4().hex[:16]
        body = ""
        for k, v in fields.items():
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
        body += f"--{boundary}--\r\n"

        r = self.session.post(f"{AUTH_URL}/internal/auth/login", data=body.encode(),
                              headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                                       "X-CSRF-TOKEN": csrf, "Origin": AUTH_URL},
                              verify=False, timeout=15)
        if r.status_code != 200:
            if _looks_like_maintenance(r.text):
                raise ServerMaintenanceError("server_maintenance")
            raise Exception(f"Login fail: {r.status_code}")

        new_csrf = self.session.cookies.get("XSRF-TOKEN") or csrf

        r = self.session.post(f"{AUTH_URL}/internal/auth/unity-login-code", json={},
                              headers={"X-CSRF-TOKEN": new_csrf, "Origin": AUTH_URL},
                              verify=False, timeout=15)
        try:
            data = r.json()
        except Exception:
            if _looks_like_maintenance(r.text):
                raise ServerMaintenanceError("server_maintenance")
            raise
        if _looks_like_maintenance(data):
            raise ServerMaintenanceError("server_maintenance")
        auth_code = data.get("data", {}).get("authCode") or data.get("authCode")
        if not auth_code:
            raise Exception(f"authCode fail: {data}")

        r = self.session.post(f"{AUTH_URL}/internal/auth/exchange-unity-code",
                              data={"username": self.username, "authCode": auth_code,
                                    "deviceId": DEVICE_ID, "platform": "android"},
                              verify=False, timeout=15)
        try:
            data = r.json()
        except Exception:
            if _looks_like_maintenance(r.text):
                raise ServerMaintenanceError("server_maintenance")
            raise
        if _looks_like_maintenance(data):
            raise ServerMaintenanceError("server_maintenance")
        d = data.get("data", data)
        bridge_token = d.get("bridgeToken")
        if not bridge_token:
            raise Exception(f"bridgeToken fail: {data}")

        self.session.post(f"{AUTH_URL}/internal/auth/verify-unity-bridge-token",
                          data={"bridgeToken": bridge_token}, verify=False, timeout=15)

        self.session.get(f"{self.auth_game_url}/AllServeRGet",
                         params={"bootstrap": "1", "platform": "android",
                                 "resourceDownloadPolicyVersion": "1",
                                 "clientVersion": APP_VERSION, "clientBuild": "3"},
                         verify=False, timeout=15)

        payload = {
            "bridgeToken": bridge_token, "taikhoan": self.username, "name": self.username,
            "hedieuhanh": "android", "version": APP_VERSION,
            "clientBuild": CLIENT_BUILD,
            "loginAttemptId": f"{int(time.time()*1000)}_{uuid.uuid4().hex}",
            "resourcePlanVersion": "1", "resourceDownloadPolicyVersion": "1",
        }
        r = self.session.post(f"{self.auth_game_url}/game/s3/dangnhapbridge", json=payload,
                              headers={"Content-Type": "application/json"},
                              verify=False, timeout=15)
        game_data = r.json()
        game_token = game_data.get("token")
        if not game_token:
            if _looks_like_maintenance(game_data):
                raise ServerMaintenanceError("server_maintenance")
            raise Exception(f"game_token fail: {game_data}")
        self.log("    ✅ Login OK")
        return game_token
