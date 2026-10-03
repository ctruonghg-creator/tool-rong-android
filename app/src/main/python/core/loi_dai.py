# core/loi_dai.py
"""Lôi Đài thường — protocol từ capture Main.getUserLoiDai/xemdauloidai/DanhLoiDai."""
from __future__ import annotations

import json
import time
from contextlib import nullcontext
from typing import Any, Callable, List, Optional


class LoiDaiController:
    def __init__(self, socket_client, stats, log_fn: Optional[Callable] = None):
        self.sc = socket_client
        self.stats = stats
        self.log = log_fn or print
        self.last_error = ""
        self.last_opponent = None

    def log_msg(self, msg: str) -> None:
        self.log(f"[LoiDai] {msg}")

    @staticmethod
    def _decode(v):
        for _ in range(3):
            if isinstance(v, bytes):
                try:
                    v = v.decode("utf-8")
                except Exception:
                    break
            if not isinstance(v, str):
                break
            try:
                v = json.loads(v)
            except Exception:
                break
        return v

    @classmethod
    def _obj(cls, v):
        v = cls._decode(v)
        if isinstance(v, dict):
            return v
        if isinstance(v, list):
            for x in v:
                x = cls._decode(x)
                if isinstance(x, dict):
                    return x
        return None

    def _request(self, event, payload, timeout=10):
        if not self.sc or not self.sc.is_connected():
            return None
        try:
            return self.sc.request(event, payload, timeout=timeout)
        except Exception as e:
            self.last_error = str(e)
            return None

    def _self_name(self) -> str:
        data = getattr(self.sc, "login_success", None)
        if isinstance(data, dict):
            return str(data.get("tenhienthi") or data.get("tenHienThi") or "")
        return ""

    def _self_id(self) -> str:
        return str(getattr(self.sc, "account_id", "") or "")

    def get_opponents(self) -> List[dict]:
        resp = self._request(
            "SendRequest2",
            {"class": "Main", "method": "getUserLoiDai"},
            timeout=8,
        )
        obj = self._obj(resp)
        if not obj:
            return []
        root = obj.get("usertop")
        arr = root.get("usertop") if isinstance(root, dict) else None
        if not isinstance(arr, list):
            return []
        out = []
        self_id = self._self_id()
        self_name = self._self_name()
        for x in arr:
            if not isinstance(x, dict):
                continue
            pid = str(x.get("idfb") or x.get("id") or "")
            pname = str(x.get("tenhienthi") or "")
            if not pid or pid == self_id or (pname and pname == self_name):
                continue
            out.append(x)
        return out

    def _check_opponent(self, opp: dict) -> Optional[dict]:
        pid = str(opp.get("idfb") or opp.get("id") or "")
        pname = str(opp.get("tenhienthi") or "")
        if not pid or not pname:
            return None
        resp = self._request(
            "SendRequest",
            {
                "class": "Main",
                "method": "xemdauloidai",
                "data": {"tenhienthi": pname, "idfr": pid},
            },
            timeout=8,
        )
        obj = self._obj(resp)
        if not obj:
            return None
        if str(obj.get("status")) in ("0", "ok", "success"):
            return {"idfr": pid, "tenhienthi": pname, "check": obj}
        msg = str(obj.get("message") or "")
        self.last_error = msg or str(obj)
        return None

    def one_fight(self) -> bool:
        """Đánh 1 lượt Lôi Đài thường; không tự mua lượt bằng Kim Cương."""
        self.last_error = ""
        lock = getattr(self.sc, "automation_lock", None)
        ctx = lock if lock is not None else nullcontext()
        with ctx:
            opponents = self.get_opponents()
            if not opponents:
                self.log_msg("Không lấy được danh sách đối thủ Lôi Đài")
                return False

            for opp in opponents:
                checked = self._check_opponent(opp)
                if not checked:
                    continue
                data = {
                    "tenhienthi": checked["tenhienthi"],
                    "idfr": checked["idfr"],
                    "boquatrandau": "False",
                    "muaLuotBangKimCuong": "False",
                }
                self.log_msg(
                    f"→ DanhLoiDai: {checked['tenhienthi']} ({checked['idfr']})"
                )
                resp = self._request(
                    "SendRequest2",
                    {"class": "Main", "method": "DanhLoiDai", "data": data},
                    timeout=20,
                )
                obj = self._obj(resp)
                if obj and str(obj.get("status")) in ("0", "ok", "success"):
                    self.last_opponent = checked
                    self.last_error = ""
                    self.log_msg("✓ Lôi Đài: server xác nhận DanhLoiDai")
                    # Battle payload/pushes follow immediately after this request in capture.
                    time.sleep(1.0)
                    return True
                msg = str((obj or {}).get("message") or self.last_error or obj or "")
                self.last_error = msg
                self.log_msg(f"  Đối thủ chưa đánh được: {msg}")
                # Nếu server còn cooldown thì không quét hàng loạt đối thủ.
                if "phải chờ" in msg.lower() or "chờ" in msg.lower():
                    return False

            return False
