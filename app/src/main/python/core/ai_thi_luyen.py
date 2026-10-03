"""Ai Thi Luyen flow based on the supplied WebSocket capture."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable, List, Optional


class AiThiLuyenController:
    # Các ải dùng chung một flow: GetGateData -> StartBattle -> triệu hồi
    # -> WinBattle. gateKey chỉ thay đổi theo ải đang chọn trên UI.
    GATE_KEY = "Chung"
    GATES = ("Chung", "BatHoai", "KhangMa", "CuongSat", "CuongHuyet")

    def __init__(self, socket_client, stats=None, log_fn: Optional[Callable] = None):
        self.sc = socket_client
        self.stats = stats
        self.log = log_fn or print
        self.dragon_ids: List[str] = []
        self.dragon_catalog: List[dict] = []
        self.custom_dragon_ids: List[str] = []
        self._stop = threading.Event()
        self._fighting = False
        self.last_gate: Optional[dict] = None
        self.gate_key: str = self.GATE_KEY
        self.last_session: str = ""
        self.last_result: Optional[dict] = None

    def log_msg(self, msg: str) -> None:
        self.log(f"[AiThiLuyen] {msg}")

    def load_dragons_from_login(self, login_data: Any) -> int:
        ids: List[str] = []
        try:
            from core.dragon import parse_dragons_from_login
            island, bag, _ = parse_dragons_from_login(login_data if isinstance(login_data, dict) else {})
            for d in island + bag:
                did = getattr(d, "id", None)
                if did and str(did) not in ids:
                    ids.append(str(did))
        except Exception as e:
            self.log_msg(f"parse dragon login: {e}")
        if not ids and self.stats is not None:
            for r in getattr(self.stats, "bag_dragons", []) or []:
                if isinstance(r, dict) and r.get("id") and str(r["id"]) not in ids:
                    ids.append(str(r["id"]))
        self.dragon_ids = ids
        catalog = []
        seen = set()
        try:
            from core.dragon import parse_dragons_from_login
            island, bag, _ = parse_dragons_from_login(login_data if isinstance(login_data, dict) else {})
            for d in island + bag:
                did = str(getattr(d, "id", "") or "")
                if not did or did in seen:
                    continue
                seen.add(did)
                catalog.append({
                    "id": did,
                    "name": getattr(d, "name", "?"),
                    "sao": getattr(d, "sao", 0),
                    "level": getattr(d, "level", 0),
                    "hiem": getattr(d, "hiem", 0),
                })
        except Exception:
            pass
        self.dragon_catalog = catalog
        # Preserve an existing custom selection where possible; first login
        # defaults to the full available list, matching Viễn Chinh behaviour.
        if self.custom_dragon_ids:
            valid = set(ids)
            self.custom_dragon_ids = [x for x in self.custom_dragon_ids if x in valid]
        if not self.custom_dragon_ids:
            self.custom_dragon_ids = list(ids)
        return len(ids)

    def set_dragon_ids(self, ids: List[str]) -> None:
        valid = {str(x) for x in self.dragon_ids if x}
        chosen = []
        for x in ids or []:
            sx = str(x)
            if sx in valid and sx not in chosen:
                chosen.append(sx)
        self.custom_dragon_ids = chosen
        self.log_msg(f"Đội hình ải tùy biến: {len(chosen)} rồng")

    def get_selected_dragon_ids(self) -> List[str]:
        valid = {str(x) for x in self.dragon_ids if x}
        if not self.custom_dragon_ids:
            return []
        return [x for x in self.custom_dragon_ids if x in valid]

    @staticmethod
    def _unwrap(resp):
        if isinstance(resp, list) and len(resp) == 1:
            return resp[0]
        return resp

    def set_gate_key(self, gate_key: str) -> str:
        key = str(gate_key or self.GATE_KEY).strip()
        if key not in self.GATES:
            key = self.GATE_KEY
        self.gate_key = key
        return self.gate_key

    def get_gate_data(self, gate_key: Optional[str] = None) -> Optional[dict]:
        if gate_key is not None:
            self.set_gate_key(gate_key)
        gate_key = self.gate_key
        if not self.sc or not self.sc.is_connected():
            self.log_msg("Socket không connected")
            return None
        resp = self._unwrap(self.sc.request(
            "SendRequest2",
            {"class": "AiThiLuyen", "method": "GetGateData", "data": {"gateKey": gate_key}},
            timeout=10,
        ))
        self.last_gate = resp if isinstance(resp, dict) else None
        if isinstance(resp, dict):
            dw = resp.get("dailyWins") or {}
            if isinstance(dw, dict):
                self.log_msg(
                    f"Gate={dw.get('gateKey', gate_key)} | used={dw.get('used','?')} "
                    f"remaining={dw.get('remaining','?')} limit={dw.get('limit','?')}"
                )
        else:
            self.log_msg(f"GetGateData response bất thường: {resp!r}")
        return self.last_gate

    def start_battle(self, gate_key: Optional[str] = None) -> Optional[str]:
        if gate_key is not None:
            self.set_gate_key(gate_key)
        gate_key = self.gate_key
        if not self.sc or not self.sc.is_connected():
            self.log_msg("Socket không connected")
            return None
        resp = self._unwrap(self.sc.request(
            "SendRequest2",
            {"class": "AiThiLuyen", "method": "StartBattle", "data": {"gateKey": gate_key}},
            timeout=12,
        ))
        if isinstance(resp, dict):
            status = str(resp.get("status", "")).lower()
            if status in {"1", "error", "fail", "failed"} or resp.get("errorCode"):
                self.log_msg(f"StartBattle bị từ chối: {resp.get('message') or resp}")
                return None
            session = resp.get("battleSessionId")
            if session:
                self.last_session = str(session)
                self.log_msg(f"StartBattle OK | session={self.last_session}")
                return self.last_session
        self.log_msg(f"StartBattle không có battleSessionId: {resp!r}")
        return None

    def _summon_dragons(self, session: str, dragon_ids: Optional[List[str]] = None, delay: float = 0.25) -> int:
        ids = list(dragon_ids) if dragon_ids is not None else self.get_selected_dragon_ids()
        if not ids:
            ids = list(self.dragon_ids)
        count = 0
        for did in ids:
            if self._stop.is_set():
                break
            self.sc.emit("trieuhoirong", {
                "dragonId": did,
                "requestId": uuid.uuid4().hex,
                "reviveRequested": False,
                "battleSessionId": session,
            })
            count += 1
            time.sleep(max(0.05, delay))
        self.log_msg(f"Đã triệu hồi {count}/{len(ids)} rồng")
        return count

    def _summon_suphu_loop(self, duration: float, interval: float) -> int:
        count = 0
        end = time.time() + max(1.0, duration)
        self.log_msg("→ Bắt đầu triệu hồi Sư Phụ liên tục...")
        while time.time() < end and not self._stop.is_set() and self.sc.is_connected():
            self.sc.emit("trieuhoirong", "suphu")
            count += 1
            time.sleep(max(0.1, interval))
        self.log_msg(f"→ Đã gửi {count} lần triệu hồi Sư Phụ")
        return count

    def win_battle(self, session: str) -> Optional[dict]:
        resp = self._unwrap(self.sc.request(
            "SendRequest2",
            {"class": "AiThiLuyen", "method": "WinBattle", "data": {"battleSessionId": session}},
            timeout=15,
        ))
        self.last_result = resp if isinstance(resp, dict) else None
        if isinstance(resp, dict):
            status = str(resp.get("status", "")).lower()
            if status in {"1", "error", "fail", "failed"} or resp.get("errorCode"):
                self.log_msg(f"WinBattle thất bại: {resp.get('message') or resp}")
                return resp
            self.log_msg(f"WinBattle OK: {resp}")
            return resp
        self.log_msg(f"WinBattle response: {resp!r}")
        return None

    def flow(self, battle_seconds: int = 60, summon_interval: float = 0.5,
             summon_team: bool = True, summon_suphu: bool = True,
             dragon_ids: Optional[List[str]] = None, dragon_delay: float = 0.3,
             gate_key: Optional[str] = None) -> bool:
        if self._fighting:
            self.log_msg("Đang có một trận chạy")
            return False
        lock = getattr(self.sc, "combat_lock", None)
        if lock is not None:
            with lock:
                return self._flow_locked(battle_seconds, summon_interval, summon_team, summon_suphu, dragon_ids, dragon_delay, gate_key)
        return self._flow_locked(battle_seconds, summon_interval, summon_team, summon_suphu, dragon_ids, dragon_delay, gate_key)

    def _flow_locked(self, battle_seconds, summon_interval, summon_team, summon_suphu, dragon_ids, dragon_delay, gate_key=None) -> bool:
        self._fighting = True
        self._stop.clear()
        try:
            if gate_key is not None:
                self.set_gate_key(gate_key)
            selected_gate = self.gate_key
            self.log_msg(f"=== BẮT ĐẦU ẢI {selected_gate} ===")
            gate = self.get_gate_data(selected_gate)
            if isinstance(gate, dict):
                dw = gate.get("dailyWins") or {}
                rem = dw.get("remaining") if isinstance(dw, dict) else None
                try:
                    if rem is not None and int(rem) <= 0:
                        self.log_msg("Đã hết lượt hôm nay")
                        return False
                except Exception:
                    pass

            session = self.start_battle(selected_gate)
            if not session:
                return False

            if summon_team and self.dragon_ids:
                self._summon_dragons(session, dragon_ids=dragon_ids, delay=dragon_delay)
            elif summon_team:
                self.log_msg("Không có danh sách rồng login; bỏ qua thả đội hình")

            duration = max(5, int(battle_seconds or 60))
            if summon_suphu:
                self._summon_suphu_loop(duration, summon_interval)
            else:
                self.log_msg(f"Chờ battle {duration}s (không thả Sư Phụ)")
                end = time.time() + duration
                while time.time() < end and not self._stop.is_set():
                    time.sleep(0.2)

            if self._stop.is_set():
                self.log_msg("Đã dừng trận trước khi WinBattle")
                return False

            result = self.win_battle(session)
            if isinstance(result, dict):
                status = str(result.get("status", "")).lower()
                if status in {"1", "error", "fail", "failed"} or result.get("errorCode"):
                    return False

            time.sleep(0.6)
            reward = False
            try:
                with self.sc.lock:
                    events = list(self.sc.last_events)
                for _, ev, data in events[-80:]:
                    if ev == "Thongbao" and isinstance(data, dict) and data.get("addqua") == 1:
                        reward = True
                        break
            except Exception:
                pass
            self.get_gate_data(selected_gate)
            self.log_msg(f"✅ Hoàn tất ải {selected_gate}" + (" + nhận quà" if reward else ""))
            return True
        except Exception as e:
            self.log_msg(f"❌ Lỗi: {e}")
            return False
        finally:
            self._fighting = False

    def auto_flow(self, runs: int = 1, battle_seconds: int = 60, summon_interval: float = 0.5,
                  summon_team: bool = True, summon_suphu: bool = True,
                  dragon_ids: Optional[List[str]] = None, dragon_delay: float = 0.3,
                  between_runs: float = 0.8, progress_fn: Optional[Callable[[int, int, bool], None]] = None,
                  gate_key: Optional[str] = None) -> dict:
        """Run the normal battle flow sequentially for a fixed number of runs."""
        try:
            total = max(1, int(runs))
        except Exception:
            total = 1
        ok_count = 0
        fail_count = 0
        self._stop.clear()
        self.log_msg(f"AUTO bắt đầu: {total} lượt")
        for i in range(1, total + 1):
            if self._stop.is_set():
                self.log_msg(f"AUTO dừng trước lượt {i}")
                break
            ok = self.flow(
                battle_seconds=battle_seconds,
                summon_interval=summon_interval,
                summon_team=summon_team,
                summon_suphu=summon_suphu,
                dragon_ids=dragon_ids,
                dragon_delay=dragon_delay,
                gate_key=gate_key,
            )
            if ok:
                ok_count += 1
            else:
                fail_count += 1
            if progress_fn:
                try:
                    progress_fn(i, total, ok)
                except Exception:
                    pass
            if i < total and not self._stop.is_set():
                time.sleep(max(0.0, float(between_runs)))
        result = {"requested": total, "completed": ok_count + fail_count, "ok": ok_count, "fail": fail_count,
                  "stopped": self._stop.is_set()}
        self.log_msg(f"AUTO kết thúc: {result}")
        return result

    def stop(self):
        self._stop.set()


__all__ = ["AiThiLuyenController"]
