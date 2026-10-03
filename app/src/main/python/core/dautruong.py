# core/dautruong.py
"""
Đấu Trường Thử Thách — FULL AUTO
- Tự GetData, tự chọn 7 rồng mạnh, tự chọn skill
- Đánh liên tục đến hết lượt / thua liên tiếp 3 trận (hết lượt ngày)
"""
from __future__ import annotations

import json
import random
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

SKILLS = [
    "LoiKeo",
    "SamLuc",
    "HoaLuc",
    "ThoLuc",
    "HungPhan",
    "TangTamDanh",
]


class DauTruongThuThachController:
    def __init__(self, socket_client, stats, log_fn: Optional[Callable] = None):
        self.sc = socket_client
        self.stats = stats
        self.log = log_fn or print
        self.last_rong: Dict[str, dict] = {}
        self.last_session: Optional[str] = None
        self.display_suffix: str = ""
        self.preferred_skill: str = "HungPhan"
        self.wait_battle: float = 10.0
        self.max_lose_streak: int = 3  # thua 3 liên tiếp = hết lượt ngày
        self.max_rounds_safety: int = 50
        self.max_team_size: int = 6
        self.suphu_summon_count: int = 10

        self.thanglientiep: int = 0
        self.thualientiep: int = 0
        self.luotdanh: int = 0
        self.so_tran_ngay: int = 0
        self.last_result: str = ""

    def log_msg(self, msg: str) -> None:
        self.log(f"[DauTruong] {msg}")

    def set_display_suffix(self, name: str) -> None:
        self.display_suffix = str(name or "").strip()

    def _emit(self, method: str, data: Optional[dict] = None, use2: bool = False) -> None:
        payload: Dict[str, Any] = {"class": "DauTruongThuThach", "method": method}
        if data is not None:
            payload["data"] = data
        self.sc.emit("SendRequest2" if use2 else "SendRequest", payload)

    def _call(self, method: str, data: Optional[dict] = None, use2: bool = False, timeout: float = 8):
        payload: Dict[str, Any] = {"class": "DauTruongThuThach", "method": method}
        if data is not None:
            payload["data"] = data
        ev = "SendRequest2" if use2 else "SendRequest"
        try:
            if hasattr(self.sc, "request"):
                return self.sc.request(ev, payload, timeout=timeout)
            return self.sc.call(ev, payload, timeout=timeout)
        except Exception:
            try:
                self.sc.emit(ev, payload)
            except Exception:
                return None
            time.sleep(0.9)
            return None

    def _scan_last(self, pred, limit: int = 50):
        if not hasattr(self.sc, "last_events"):
            return None
        with self.sc.lock:
            for _, ev, data in reversed(self.sc.last_events[-limit:]):
                try:
                    if pred(ev, data):
                        return data
                except Exception:
                    pass
        return None

    @staticmethod
    def _decode_json_blob(blob: Any) -> Any:
        value = blob
        for _ in range(4):
            if isinstance(value, bytes):
                try:
                    value = value.decode("utf-8")
                except Exception:
                    break
            if not isinstance(value, str):
                break
            txt = value.strip()
            if not txt:
                break
            try:
                value = json.loads(txt)
            except Exception:
                break
        return value

    @classmethod
    def _find_nested(cls, blob: Any, keys, max_depth: int = 10):
        wanted = tuple(keys)
        def walk(obj, depth=0):
            if depth > max_depth:
                return None
            obj = cls._decode_json_blob(obj)
            if isinstance(obj, dict):
                for key in wanted:
                    if key in obj:
                        return obj[key]
                for child in obj.values():
                    if isinstance(child, (dict, list, str)):
                        hit = walk(child, depth + 1)
                        if hit is not None:
                            return hit
            elif isinstance(obj, list):
                for child in obj:
                    hit = walk(child, depth + 1)
                    if hit is not None:
                        return hit
            return None
        return walk(blob)

    @staticmethod
    def _normalize_dragon_roster(roster: Any) -> Dict[str, dict]:
        if isinstance(roster, dict):
            out = {}
            for rid, info in roster.items():
                if isinstance(info, dict):
                    out[str(info.get("id") or rid)] = info
            return out
        if isinstance(roster, list):
            out = {}
            for info in roster:
                if not isinstance(info, dict):
                    continue
                rid = info.get("id") or info.get("idrong") or info.get("dragonId")
                if rid:
                    out[str(rid)] = info
            return out
        return {}

    def _parse_state(self, resp: dict) -> None:
        if not isinstance(resp, (dict, list, str)):
            return
        for key, attr in (
            ("thanglientiep", "thanglientiep"),
            ("thualientiep", "thualientiep"),
            ("luotdanh", "luotdanh"),
            ("soTranDanhThuongTrongNgay", "so_tran_ngay"),
        ):
            value = self._find_nested(resp, (key,))
            if value is None:
                continue
            try:
                setattr(self, attr, int(value or 0))
            except Exception:
                pass

    def get_data(self) -> Optional[dict]:
        self.log_msg("GetData...")
        resp = self._call("GetDataDauTruongThuThach", timeout=10)
        if resp is None:
            time.sleep(0.8)
            resp = self._scan_last(
                lambda e, d: isinstance(d, (dict, list, str))
                and self._find_nested(d, ("RongCoTheSuDung", "rongCoTheSuDung")) is not None
            )

        if resp is None:
            self.log_msg("Không lấy được data")
            return None

        # The live ACK can wrap data under multiple layers. Always search
        # recursively before falling back to the login roster.
        roster_raw = self._find_nested(resp, ("RongCoTheSuDung", "rongCoTheSuDung"))
        roster = self._normalize_dragon_roster(roster_raw)
        if roster:
            self.last_rong = roster
        else:
            # Fallback only when the server response omitted the roster field.
            # This preserves the actual server roster when it is present and
            # keeps the old bag-dragon fallback for older builds.
            bag = getattr(self.stats, "bag_dragons", None) or []
            fallback = self._normalize_dragon_roster(bag)
            if fallback:
                self.last_rong = fallback
                self.log_msg(f"GetData không có roster DTTT → fallback roster từ bag/login: {len(fallback)} rồng")

        self._parse_state(resp)
        self.log_msg(
            f"Rồng={len(self.last_rong)} | thắng LT={self.thanglientiep} "
            f"thua LT={self.thualientiep} | trận ngày={self.so_tran_ngay}"
        )
        return resp if isinstance(resp, dict) else {"raw": resp}

    def _pick_dragons(self, count: int = 7) -> List[str]:
        items = []
        for rid, info in (self.last_rong or {}).items():
            if not isinstance(info, dict):
                continue
            sao = int(info.get("sao") or 0)
            chiso = info.get("chiso") or {}
            atk = int(chiso.get("sucdanh") or 0) if isinstance(chiso, dict) else 0
            hp = int(chiso.get("hp") or 0) if isinstance(chiso, dict) else 0
            hiem = int(info.get("hiem") or 0)
            items.append(((sao, hiem, atk, hp), str(info.get("id") or rid)))
        items.sort(reverse=True)
        return [x[1] for x in items[:min(count, self.max_team_size)]]

    def chon_rong(self, dragon_ids: List[str]) -> None:
        for i, did in enumerate(dragon_ids[:self.max_team_size]):
            self._emit("ChonRongThuThach", {"idrong": did, "vitri": str(i)})
            time.sleep(0.28)

    def chon_skill(self, name: Optional[str] = None) -> None:
        skill = name or self.preferred_skill
        if skill not in SKILLS:
            skill = "HungPhan"
        self._emit("ChonSkillThuThach", {"nameskill": skill})
        time.sleep(0.4)

    def xac_nhan_doi_hinh(self) -> None:
        self._emit("XemXacNhanDoiHinh")
        time.sleep(0.5)
        self._emit("XacNhanDoiHinhThuthach")
        time.sleep(0.7)

    def bat_dau(self) -> Optional[str]:
        resp = self._call("BatDauDauTruongThuThach", timeout=12)
        if not isinstance(resp, dict):
            time.sleep(1.0)
            resp = self._scan_last(
                lambda e, d: isinstance(d, dict)
                and (d.get("battleSessionId") or d.get("DoiHinhDoiThu") is not None)
            )
        session = None
        if isinstance(resp, dict):
            session = resp.get("battleSessionId")
            # server từ chối / hết lượt?
            if resp.get("status") in ("1", 1, "error") or resp.get("errorCode"):
                msg = resp.get("message") or resp.get("errorCode") or "lỗi"
                self.log_msg(f"BatDau từ chối: {msg}")
                return None
        if not session:
            acc = getattr(self.sc, "account_id", "player") or "player"
            session = f"{acc}:DauTruongThuThach:{int(time.time() * 1000)}:{random.randint(100000, 999999)}"
        self.last_session = session
        return session

    def danh_dau_bat_dau(self, session: str) -> None:
        self._emit(
            "DanhDauBatDauTranDauTruongThuThach",
            {"battleSessionId": session},
            use2=True,
        )
        time.sleep(0.5)

    def _battle_dragon_id(self, base_id: str) -> str:
        suf = self.display_suffix
        if suf and not base_id.endswith(suf):
            return f"{base_id}{suf}"
        return base_id

    def tha_rong(self, dragon_ids: List[str], session: str) -> None:
        # Protocol thực tế của Đấu Trường Thử Thách dùng 6 vị trí (0..5).
        # Giữ nguyên cách thả rồng theo battleSessionId.
        for did in dragon_ids[:self.max_team_size]:
            self.sc.emit("trieuhoirong", {
                "dragonId": self._battle_dragon_id(did),
                "requestId": uuid.uuid4().hex,
                "reviveRequested": False,
                "battleSessionId": session,
            })
            time.sleep(0.25)

        # Sư Phụ: giữ cơ chế triệu hồi như trước nhưng spam đúng 10 lần/trận.
        for i in range(self.suphu_summon_count):
            try:
                self.sc.emit("trieuhoirong", "suphu")
            except Exception as e:
                self.log_msg(f"Thả Sư Phụ lần {i + 1}/{self.suphu_summon_count} lỗi: {e}")
                break
            time.sleep(0.5)

    def win(self, session: str) -> Tuple[str, Optional[dict]]:
        self._emit("WinDauTruongThuThach", {"battleSessionId": session}, use2=True)
        time.sleep(1.2)
        resp = self._scan_last(
            lambda e, d: isinstance(d, dict)
            and (d.get("committedResult") or d.get("result") in ("Win", "Lose", "Thang", "Thua"))
        )
        result = ""
        if isinstance(resp, dict):
            result = str(resp.get("committedResult") or resp.get("result") or "")
            self._parse_state(resp)
        self.last_result = result
        return result, resp if isinstance(resp, dict) else None

    def try_danh_nhanh(self, session: str) -> Optional[str]:
        self._emit(
            "DanhNhanhDauTruongThuThach",
            {
                "battleSessionId": session,
                "quickRequestId": uuid.uuid4().hex,
                "includeReplay": "true",
            },
            use2=True,
        )
        time.sleep(1.0)
        resp = self._scan_last(
            lambda e, d: isinstance(d, dict)
            and (
                d.get("errorCode") == "dttt_quick_disabled"
                or d.get("committedResult")
                or d.get("result")
            )
        )
        if isinstance(resp, dict):
            if resp.get("errorCode") == "dttt_quick_disabled":
                return None
            r = str(resp.get("committedResult") or resp.get("result") or "")
            if r:
                self.last_result = r
                return r
        return None

    def one_fight(self, skill: Optional[str] = None) -> str:
        """
        1 trận. Trả về: 'Win' | 'Lose' | 'Stop' | 'Error'
        """
        data = self.get_data()
        if self.thualientiep >= self.max_lose_streak:
            self.log_msg(f"Đã thua liên tiếp {self.thualientiep} → hết lượt ngày")
            return "Stop"

        if not self.last_rong:
            if not data:
                return "Error"
            return "Error"

        ids = self._pick_dragons(self.max_team_size)
        if not ids:
            self.log_msg("Không còn rồng hợp lệ")
            return "Stop"

        self.log_msg(f"Đội hình {len(ids)} rồng | skill={skill or self.preferred_skill} | Sư Phụ x{self.suphu_summon_count}")
        self.chon_rong(ids)
        self.chon_skill(skill or self.preferred_skill)
        self.xac_nhan_doi_hinh()

        session = self.bat_dau()
        if not session:
            return "Stop"

        quick = self.try_danh_nhanh(session)
        if quick:
            self.log_msg(f"Đánh nhanh → {quick}")
            return "Win" if quick.lower() in ("win", "thang") else "Lose"

        self.danh_dau_bat_dau(session)
        self.tha_rong(ids, session)
        time.sleep(self.wait_battle)
        result, resp = self.win(session)
        if not result:
            # fallback: đọc lại state
            self.get_data()
            result = self.last_result or "Win"  # giả định nếu không rõ

        is_win = result.lower() in ("win", "thang")
        if is_win:
            self.thanglientiep += 1
            self.thualientiep = 0
            self.log_msg(f"THẮNG (chuỗi {self.thanglientiep})")
            return "Win"
        else:
            self.thualientiep += 1
            self.thanglientiep = 0
            self.log_msg(f"THUA (chuỗi thua {self.thualientiep}/{self.max_lose_streak})")
            return "Lose"

    def full_auto(self, skill: Optional[str] = None, max_battles: int = 0) -> dict:
        """
        Đánh đến hết: thua 3 liên tiếp hoặc server Stop / safety max.
        """
        if not self.sc or not self.sc.is_connected():
            self.log_msg("Chưa kết nối")
            return {"ok": False, "wins": 0, "losses": 0}

        skill = skill or self.preferred_skill
        wins = losses = 0
        try:
            max_battles = max(0, int(max_battles or 0))
        except Exception:
            max_battles = 0
        limit = min(max_battles, self.max_rounds_safety) if max_battles else self.max_rounds_safety
        self.log_msg(
            f"FULL AUTO skill={skill} | số trận={max_battles if max_battles else 'không giới hạn'} | "
            f"dừng khi thua {self.max_lose_streak} liên tiếp"
        )

        for n in range(1, limit + 1):
            self.log_msg(f"----- Trận #{n} -----")
            try:
                r = self.one_fight(skill=skill)
            except Exception as e:
                self.log_msg(f"Lỗi trận: {e}")
                break

            if r == "Win":
                wins += 1
            elif r == "Lose":
                losses += 1
                if self.thualientiep >= self.max_lose_streak:
                    self.log_msg("Hết lượt ngày (thua 3 liên tiếp)")
                    break
            elif r == "Stop":
                self.log_msg("Dừng (hết lượt / server từ chối)")
                break
            else:
                self.log_msg("Lỗi — dừng")
                break
            time.sleep(1.2)

        summary = {
            "ok": True,
            "wins": wins,
            "losses": losses,
            "rounds": wins + losses,
            "max_battles": max_battles,
            "thanglientiep": self.thanglientiep,
            "thualientiep": self.thualientiep,
        }
        self.log_msg(
            f"XONG AUTO | thắng={wins} thua={losses} | "
            f"chuỗi thắng={self.thanglientiep} chuỗi thua={self.thualientiep}"
        )
        return summary
