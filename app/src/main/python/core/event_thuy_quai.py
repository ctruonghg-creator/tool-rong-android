"""Đại Chiến Thủy Quái flow based only on the supplied WebSocket capture."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable, List, Optional


class EventThuyQuaiController:
    EVENT_NAME = "EventDaiChienThuyQuai"
    CHE_DO = "kho"
    CHE_DO_VALUES = ("de", "thuong", "kho", "kho2", "acmong", "acmong2", "luyennguc", "winall")
    ALL_MODES_KEY = "__all__"
    MUA_BANG = "vang"

    def __init__(self, socket_client, stats=None, log_fn: Optional[Callable] = None):
        self.sc = socket_client
        self.stats = stats
        self.log = log_fn or print
        self.dragon_ids: List[str] = []
        self.dragon_catalog: List[dict] = []
        self.custom_dragon_ids: List[str] = []
        self.event_team_ids: List[str] = []
        self._stop = threading.Event()
        self._fighting = False
        self.last_data: Optional[dict] = None
        self.last_team: Optional[dict] = None
        self.last_price: Optional[dict] = None
        self.last_battle: Optional[dict] = None
        self.last_result: Optional[dict] = None
        self.last_battle_start_id: str = ""
        # Capture thực tế bạn cung cấp có KetQua kq="Thua"; Thang chưa xuất hiện trong capture.
        self.result_key: str = "Thua"
        # Chế độ/ải đánh. Giữ "kho" làm mặc định như bản cũ; "de" lấy theo capture mới.
        self.che_do: str = self.CHE_DO

    def log_msg(self, msg: str) -> None:
        self.log(f"[ThuyQuai] {msg}")

    @staticmethod
    def _unwrap(resp: Any) -> Any:
        if isinstance(resp, list) and len(resp) == 1:
            return resp[0]
        return resp

    def load_dragons_from_login(self, login_data: Any) -> int:
        ids: List[str] = []
        catalog: List[dict] = []
        try:
            from core.dragon import parse_dragons_from_login
            island, bag, _ = parse_dragons_from_login(login_data if isinstance(login_data, dict) else {})
            seen = set()
            for d in island + bag:
                did = str(getattr(d, "id", "") or "")
                if not did or did in seen:
                    continue
                seen.add(did)
                ids.append(did)
                catalog.append({
                    "id": did,
                    "name": getattr(d, "name", "?"),
                    "nameobject": getattr(d, "nameobject", ""),
                    "sao": getattr(d, "sao", 0),
                    "level": getattr(d, "level", 0),
                    "hiem": getattr(d, "hiem", 0),
                })
        except Exception as e:
            self.log_msg(f"parse dragon login: {e}")

        if not ids and self.stats is not None:
            for r in getattr(self.stats, "bag_dragons", []) or []:
                if isinstance(r, dict) and r.get("id") and str(r["id"]) not in ids:
                    did = str(r["id"])
                    ids.append(did)
                    catalog.append({
                        "id": did,
                        "name": r.get("namerong") or r.get("nameobject") or "?",
                        "nameobject": r.get("nameobject") or "",
                        "sao": r.get("sao", 0),
                        "level": r.get("level", 0),
                        "hiem": r.get("hiem", 0),
                    })

        self.dragon_ids = ids
        self.dragon_catalog = catalog
        valid = set(ids)
        if self.custom_dragon_ids:
            self.custom_dragon_ids = [x for x in self.custom_dragon_ids if x in valid]
        if not self.custom_dragon_ids:
            self.custom_dragon_ids = list(ids)
        return len(ids)

    def set_dragon_ids(self, ids: List[str]) -> None:
        valid = {str(x) for x in self.dragon_ids if x}
        chosen: List[str] = []
        for x in ids or []:
            sx = str(x)
            if sx in valid and sx not in chosen:
                chosen.append(sx)
        self.custom_dragon_ids = chosen
        self.log_msg(f"Đội hình Thủy Quái tùy biến: {len(chosen)} rồng")

    def get_selected_dragon_ids(self) -> List[str]:
        valid = {str(x) for x in self.dragon_ids if x}
        return [x for x in self.custom_dragon_ids if x in valid]

    def set_che_do(self, che_do: str) -> None:
        value = str(che_do or self.CHE_DO).strip().lower()
        if value not in self.CHE_DO_VALUES:
            value = self.CHE_DO
        self.che_do = value
        self.log_msg(f"Chế độ đánh: {self.che_do}")

    def get_available_modes(self) -> List[str]:
        """Lấy đúng các ải server đang mở từ EventDaiChienThuyQuai.GetData.
        Field allchedo trong capture chứa các key: de, thuong, kho, kho2,
        acmong, acmong2, luyennguc, winall. Chỉ trả về mode có giá trị true.
        """
        data = self.last_data
        if not isinstance(data, dict):
            data = self.get_data()
        if not isinstance(data, dict):
            return []
        payload = data.get("data") if isinstance(data.get("data"), dict) else data
        allchedo = payload.get("allchedo") if isinstance(payload, dict) else None
        if not isinstance(allchedo, dict):
            return []
        return [m for m in self.CHE_DO_VALUES if bool(allchedo.get(m))]

    def _request(self, transport: str, method: str, data=None, timeout: int = 10) -> Any:
        payload = {"class": self.EVENT_NAME, "method": method}
        if data is not None:
            payload["data"] = data
        return self._unwrap(self.sc.request(transport, payload, timeout=timeout))

    def get_data(self) -> Optional[dict]:
        if not self.sc or not self.sc.is_connected():
            self.log_msg("Socket không connected")
            return None
        resp = self._request("SendRequest", "GetData", timeout=10)
        self.last_data = resp if isinstance(resp, dict) else None
        if isinstance(resp, dict):
            data = resp.get("data") if isinstance(resp.get("data"), dict) else resp
            self.log_msg(
                f"GetData OK | VuKhi={data.get('VuKhi','?')} AoGiap={data.get('AoGiap','?')} "
                f"HuyHieuSocCon={data.get('HuyHieuSocCon','?')} VeChoiNhanh={data.get('VeChoiNhanh','?')}"
            )
        else:
            self.log_msg(f"GetData response bất thường: {resp!r}")
        return self.last_data

    def get_team(self) -> Optional[dict]:
        if not self.sc or not self.sc.is_connected():
            self.log_msg("Socket không connected")
            return None
        resp = self._request("SendRequest2", "GetDoiHinhChienDau", timeout=10)
        self.last_team = resp if isinstance(resp, dict) else None
        if isinstance(resp, dict):
            d = resp.get("DoiHinh") if isinstance(resp.get("DoiHinh"), dict) else {}
            active = [k for k, v in d.items() if bool(v)]
            self.log_msg(
                f"Đội hình event server: active={', '.join(active) if active else 'không có'} | "
                f"lượt={resp.get('luotdanh','?')}/{resp.get('maxluotdanh','?')}"
            )
        else:
            self.log_msg(f"GetDoiHinhChienDau response bất thường: {resp!r}")
        return self.last_team

    def get_price(self) -> Optional[dict]:
        if not self.sc or not self.sc.is_connected():
            return None
        resp = self._request("SendRequest2", "GetGiaMuaLuot", timeout=10)
        self.last_price = resp if isinstance(resp, dict) else None
        if isinstance(resp, dict):
            self.log_msg(f"Giá mua lượt: {resp.get('giamua') or resp.get('message') or resp}")
        return self.last_price

    def cancel_pending(self) -> bool:
        if not self.sc or not self.sc.is_connected():
            return False
        resp = self._request("SendRequest2", "CancelXucTuBattleStart", timeout=8)
        ok = isinstance(resp, dict) and str(resp.get("status", "")).lower() in {"0", "ok", "success", ""}
        self.log_msg(f"CancelXucTuBattleStart: {'OK' if ok else resp}")
        return ok

    def refresh_island_state(self) -> bool:
        """Bám đúng capture: refresh DragonIsland.GetCurrentConLan trước DanhXucTu."""
        if not self.sc or not self.sc.is_connected():
            return False
        try:
            resp = self.sc.request(
                "LuaSendRequest",
                {"class": "DragonIsland", "method": "GetCurrentConLan", "data": {}},
                timeout=8,
            )
            value = self._unwrap(resp)
            self.log_msg(f"GetCurrentConLan: {'OK' if isinstance(value, dict) else 'đã nhận'}")
            return True
        except Exception as e:
            self.log_msg(f"GetCurrentConLan lỗi: {e}")
            return False

    def start_battle(self) -> Optional[str]:
        if not self.sc or not self.sc.is_connected():
            self.log_msg("Socket không connected")
            return None

        resp = self._request(
            "SendRequest",
            "DanhXucTu",
            {
                "chedodanh": self.che_do,
                "muabang": self.MUA_BANG,
                "battleStartProtocol": "2",
            },
            timeout=12,
        )
        self.last_battle = resp if isinstance(resp, dict) else None
        if not isinstance(resp, dict):
            self.log_msg(f"DanhXucTu response bất thường: {resp!r}")
            return None

        status = str(resp.get("status", "")).lower()
        if status not in {"0", "ok", "success", ""}:
            # Một số response thực tế trả status số (ví dụ 1) nhưng message rỗng.
            # Giữ nguyên toàn bộ payload trong log để không đoán sai nguyên nhân.
            self.log_msg(
                f"DanhXucTu bị từ chối | status={resp.get('status')!r} | "
                f"message={resp.get('message')!r} | response={resp!r}"
            )
            return None

        battle_id = resp.get("battleStartId")
        if not battle_id:
            self.log_msg(f"DanhXucTu không có battleStartId: {resp!r}")
            return None
        self.last_battle_start_id = str(battle_id)
        self.log_msg(
            f"DanhXucTu OK | boss={resp.get('NameBoss','?')} hp={resp.get('hp','?')} "
            f"battleStartId={self.last_battle_start_id} paymentPending={resp.get('paymentPending','?')}"
        )
        return self.last_battle_start_id

    def _capture_server_team_from_events(self, wait_seconds: float = 2.0) -> List[str]:
        """Capture VienChinh payload emitted after DoiHinhDanh, when present."""
        deadline = time.time() + max(0.2, wait_seconds)
        found: List[str] = []
        while time.time() < deadline and not self._stop.is_set():
            try:
                with self.sc.lock:
                    events = list(self.sc.last_events)
                for _, ev, data in reversed(events[-80:]):
                    if ev != "VienChinh" or not isinstance(data, dict):
                        continue
                    team = data.get("doihinh")
                    if not isinstance(team, list):
                        continue
                    for item in team:
                        if isinstance(item, dict):
                            did = item.get("id") or item.get("dragonId")
                        elif isinstance(item, str):
                            did = item
                        else:
                            did = None
                        if did and str(did) not in found:
                            found.append(str(did))
                    if found:
                        self.event_team_ids = found
                        self.log_msg(f"DoiHinhDanh → server trả {len(found)} rồng")
                        return found
            except Exception:
                pass
            time.sleep(0.1)
        return found

    def configure_battle_team(self) -> List[str]:
        if not self.sc or not self.sc.is_connected():
            return []
        try:
            self.sc.emit("DoiHinhDanh", f"BossXucTu/XucTu/suKien/{self.last_battle_start_id}")
            self.log_msg(f"DoiHinhDanh → BossXucTu/XucTu/suKien/{self.last_battle_start_id}")
        except Exception as e:
            self.log_msg(f"DoiHinhDanh emit lỗi: {e}")
            return []
        return self._capture_server_team_from_events(1.5)

    def get_guardian_skills(self) -> Optional[dict]:
        if not self.sc or not self.sc.is_connected():
            return None
        resp = self._unwrap(self.sc.request(
            "SendRequest2",
            {
                "class": "DoiHinh2",
                "method": "GetBattleGuardianSkills",
                "data": {"battleContext": "suKien"},
            },
            timeout=10,
        ))
        if isinstance(resp, dict):
            self.log_msg(
                f"Guardian skills OK | mode={resp.get('modeKey','?')} maxSelected={resp.get('maxSelected','?')}"
            )
        else:
            self.log_msg(f"GetBattleGuardianSkills response: {resp!r}")
        return resp if isinstance(resp, dict) else None

    def confirm_battle_start(self) -> bool:
        if not self.last_battle_start_id:
            return False
        resp = self._request(
            "SendRequest2",
            "ConfirmXucTuBattleStart",
            {"battleStartId": self.last_battle_start_id},
            timeout=12,
        )
        if isinstance(resp, dict):
            status = str(resp.get("status", "")).lower()
            ok = status in {"0", "ok", "success", ""}
            self.log_msg(
                f"ConfirmXucTuBattleStart: {'OK' if ok else 'FAIL'} | "
                f"luotdanh={resp.get('luotdanh','?')} paymentCommitted={resp.get('paymentCommitted','?')}"
            )
            return ok
        self.log_msg(f"ConfirmXucTuBattleStart response: {resp!r}")
        return False

    def _summon_dragons(self, dragon_ids: Optional[List[str]] = None, delay: float = 0.3) -> int:
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
            })
            count += 1
            time.sleep(max(0.05, delay))
        self.log_msg(f"Đã triệu hồi {count}/{len(ids)} rồng Thủy Quái")
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

    def finish_battle(self, result_key: Optional[str] = None) -> Optional[dict]:
        key = str(result_key or self.result_key or "Thua")
        resp = self._request(
            "SendRequest",
            "KetQua",
            {
                "kq": key,
                "battleStartId": self.last_battle_start_id,
                "chedodanh": self.che_do,
            },
            timeout=20,
        )
        self.last_result = resp if isinstance(resp, dict) else None
        if isinstance(resp, dict):
            status = str(resp.get("status", "")).lower()
            ok = status in {"0", "ok", "success", ""}
            self.log_msg(f"KetQua {key}: {'OK' if ok else 'FAIL'} | {resp.get('message') or resp}")
            return resp
        self.log_msg(f"KetQua response bất thường: {resp!r}")
        return None

    def flow(self, battle_seconds: int = 60, summon_interval: float = 0.5,
             summon_team: bool = True, summon_suphu: bool = True,
             dragon_ids: Optional[List[str]] = None, dragon_delay: float = 0.3,
             result_key: Optional[str] = None, che_do: Optional[str] = None) -> bool:
        if che_do is not None:
            self.set_che_do(che_do)
        if self._fighting:
            self.log_msg("Đang có một trận Thủy Quái chạy")
            return False
        lock = getattr(self.sc, "combat_lock", None)
        if lock is not None:
            with lock:
                return self._flow_locked(
                    battle_seconds, summon_interval, summon_team, summon_suphu,
                    dragon_ids, dragon_delay, result_key,
                )
        return self._flow_locked(
            battle_seconds, summon_interval, summon_team, summon_suphu,
            dragon_ids, dragon_delay, result_key,
        )

    def _flow_locked(self, battle_seconds, summon_interval, summon_team, summon_suphu,
                     dragon_ids, dragon_delay, result_key=None) -> bool:
        self._fighting = True
        self._stop.clear()
        try:
            # Ải "de" phải bám đúng thứ tự packet capture: GetGiaMuaLuot ->
            # GetCurrentConLan -> DanhXucTu. Không gửi các request dọn state
            # của ải cũ trước DanhXucTu vì có thể làm battleStart bị từ chối.
            if self.che_do == "de":
                self.get_price()
                self.refresh_island_state()
            else:
                self.cancel_pending()
                self.get_data()
                self.get_team()
                self.get_price()
                self.refresh_island_state()

            battle_id = self.start_battle()
            if not battle_id and not self._stop.is_set():
                # Không tự mua lượt hay đổi chế độ; chỉ refresh lại state rồi thử lại 1 lần.
                self.log_msg("DanhXucTu FAIL → refresh state + thử lại 1 lần")
                self.refresh_island_state()
                time.sleep(0.4)
                battle_id = self.start_battle()
            if not battle_id:
                return False

            self.configure_battle_team()
            self.get_guardian_skills()
            if not self.confirm_battle_start():
                return False

            ids = list(dragon_ids) if dragon_ids is not None else self.get_selected_dragon_ids()
            if summon_team:
                self._summon_dragons(ids, delay=dragon_delay)
            else:
                self.log_msg("Tắt thả đội hình rồng")

            duration = max(5, int(battle_seconds or 60))
            if summon_suphu:
                self._summon_suphu_loop(duration, summon_interval)
            else:
                self.log_msg(f"Chờ battle {duration}s (không thả Sư Phụ)")
                end = time.time() + duration
                while time.time() < end and not self._stop.is_set():
                    time.sleep(0.2)

            if self._stop.is_set():
                self.log_msg("Đã dừng trước khi KetQua")
                return False

            key = result_key or self.result_key
            result = self.finish_battle(key)
            if not isinstance(result, dict):
                return False
            status = str(result.get("status", "")).lower()
            if status not in {"0", "ok", "success", ""}:
                return False
            return True
        except Exception as e:
            self.log_msg(f"❌ Lỗi: {e}")
            return False
        finally:
            self._fighting = False

    def auto_flow(self, runs: int = 1, battle_seconds: int = 60,
                  summon_interval: float = 0.5, summon_team: bool = True,
                  summon_suphu: bool = True, dragon_ids: Optional[List[str]] = None,
                  dragon_delay: float = 0.3, between_runs: float = 0.8,
                  result_key: Optional[str] = None, che_do: Optional[str] = None,
                  progress_fn: Optional[Callable[[int, int, bool], None]] = None) -> dict:
        total = max(1, int(runs))
        ok_count = 0
        fail_count = 0

        # Chọn "Tất cả ải đang mở": mỗi chu kỳ đánh lần lượt mọi mode
        # mà server báo true trong allchedo. Không tự đoán thêm mode.
        if che_do == self.ALL_MODES_KEY:
            modes = self.get_available_modes()
            if not modes:
                self.log_msg("Không tìm thấy ải đang mở trong allchedo")
                return {"requested": total, "ok": 0, "fail": 0, "modes": []}
            self.log_msg(f"AUTO TẤT CẢ ẢI: {', '.join(modes)}")
            for cycle in range(1, total + 1):
                if self._stop.is_set():
                    break
                for mode in modes:
                    if self._stop.is_set():
                        break
                    self.set_che_do(mode)
                    ok = self.flow(
                        battle_seconds=battle_seconds,
                        summon_interval=summon_interval,
                        summon_team=summon_team,
                        summon_suphu=summon_suphu,
                        dragon_ids=dragon_ids,
                        dragon_delay=dragon_delay,
                        result_key=result_key,
                        che_do=mode,
                    )
                    if ok:
                        ok_count += 1
                    else:
                        fail_count += 1
                    if progress_fn:
                        try:
                            progress_fn(cycle, total, ok)
                        except Exception:
                            pass
                    if not self._stop.is_set():
                        time.sleep(max(0.2, between_runs))
            return {"requested": total, "ok": ok_count, "fail": fail_count, "modes": modes}

        for i in range(1, total + 1):
            if self._stop.is_set():
                break
            ok = self.flow(
                battle_seconds=battle_seconds,
                summon_interval=summon_interval,
                summon_team=summon_team,
                summon_suphu=summon_suphu,
                dragon_ids=dragon_ids,
                dragon_delay=dragon_delay,
                result_key=result_key,
                che_do=che_do,
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
                time.sleep(max(0.2, between_runs))
        return {"requested": total, "ok": ok_count, "fail": fail_count}

    def stop(self) -> None:
        self._stop.set()
        self.log_msg("⏹ Đã yêu cầu dừng")
