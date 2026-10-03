"""Điều khiển ải/Boss của EventSinhNhat2026 theo capture thực tế."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional


class EventSinhNhatAiController:
    EVENT_NAME = "EventSinhNhat2026"
    BATTLE_CONTEXT = "suKien"

    def __init__(self, socket_client, stats=None, log_fn: Optional[Callable] = None):
        self.sc = socket_client
        self.stats = stats
        self.log = log_fn or print

        self.dragon_ids: List[str] = []
        self.dragon_catalog: List[dict] = []
        self.custom_dragon_ids: List[str] = []
        self.event_team_ids: List[str] = []

        self.level_index = 0
        self.difficulty_index = 0
        self.last_boss_info: Optional[dict] = None
        self.last_session: str = ""
        self.last_result: Optional[dict] = None

        self._stop = threading.Event()
        self._fighting = False

    def log_msg(self, msg: str) -> None:
        self.log(f"[Ải Event] {msg}")

    @staticmethod
    def _unwrap(resp: Any) -> Any:
        if isinstance(resp, list) and len(resp) == 1:
            return resp[0]
        return resp

    @staticmethod
    def _decode(value: Any) -> Any:
        import json
        cur = value
        for _ in range(4):
            if isinstance(cur, bytes):
                try:
                    cur = cur.decode("utf-8")
                except Exception:
                    return cur
            if not isinstance(cur, str):
                return cur
            text = cur.strip()
            if not text:
                return cur
            try:
                cur = json.loads(text)
            except Exception:
                return cur
        return cur

    @classmethod
    def _find_value(cls, blob: Any, key: str, max_depth: int = 8) -> Any:
        """Tìm key trong các ACK wrapper vì capture có thể thay đổi envelope."""
        blob = cls._decode(blob)
        if max_depth < 0:
            return None
        if isinstance(blob, dict):
            if key in blob:
                return blob.get(key)
            for child in blob.values():
                if isinstance(child, (dict, list, str, bytes)):
                    found = cls._find_value(child, key, max_depth - 1)
                    if found is not None:
                        return found
        elif isinstance(blob, list):
            for child in blob:
                found = cls._find_value(child, key, max_depth - 1)
                if found is not None:
                    return found
        return None

    def set_stage(self, level_index: int, difficulty_index: int) -> tuple[int, int]:
        try:
            level = max(0, min(4, int(level_index)))
        except Exception:
            level = 0
        try:
            difficulty = max(0, min(4, int(difficulty_index)))
        except Exception:
            difficulty = 0
        self.level_index = level
        self.difficulty_index = difficulty
        return level, difficulty

    def load_dragons_from_login(self, login_data: Any) -> int:
        ids: List[str] = []
        catalog: List[dict] = []
        try:
            from core.dragon import parse_dragons_from_login
            island, bag, _ = parse_dragons_from_login(
                login_data if isinstance(login_data, dict) else {}
            )
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
                    "sao": getattr(d, "sao", 0),
                    "level": getattr(d, "level", 0),
                    "hiem": getattr(d, "hiem", 0),
                })
        except Exception as e:
            self.log_msg(f"parse dragon login: {e}")

        if not ids and self.stats is not None:
            for r in getattr(self.stats, "bag_dragons", []) or []:
                if isinstance(r, dict) and r.get("id"):
                    did = str(r["id"])
                    if did not in ids:
                        ids.append(did)
                        catalog.append({
                            "id": did,
                            "name": r.get("name") or r.get("namerong") or "?",
                            "sao": r.get("sao", 0),
                            "level": r.get("level", 0),
                            "hiem": r.get("hiem", 0),
                        })

        self.dragon_ids = ids
        self.dragon_catalog = catalog
        if self.custom_dragon_ids:
            valid = set(ids)
            self.custom_dragon_ids = [x for x in self.custom_dragon_ids if x in valid]
        if not self.custom_dragon_ids:
            self.custom_dragon_ids = list(ids)
        return len(ids)

    def set_dragon_ids(self, ids: List[str]) -> None:
        valid = set(self.dragon_ids)
        chosen: List[str] = []
        for x in ids or []:
            sx = str(x)
            if sx in valid and sx not in chosen:
                chosen.append(sx)
        self.custom_dragon_ids = chosen
        self.log_msg(f"Đội hình event tùy biến: {len(chosen)} rồng")

    def get_selected_dragon_ids(self) -> List[str]:
        valid = set(self.dragon_ids)
        return [x for x in self.custom_dragon_ids if x in valid]

    def get_boss_info(self, level_index: Optional[int] = None,
                      difficulty_index: Optional[int] = None) -> Optional[dict]:
        if level_index is not None or difficulty_index is not None:
            self.set_stage(
                self.level_index if level_index is None else level_index,
                self.difficulty_index if difficulty_index is None else difficulty_index,
            )
        if not self.sc or not self.sc.is_connected():
            self.log_msg("Socket không connected")
            return None

        payload = {
            "class": self.EVENT_NAME,
            "method": "GetThongTinBoss",
            "data": {
                "levelIndex": str(self.level_index),
                "difficultyIndex": str(self.difficulty_index),
            },
        }
        resp = self._unwrap(self.sc.request("SendRequest2", payload, timeout=10))
        self.last_boss_info = resp if isinstance(resp, dict) else None
        if isinstance(resp, dict):
            info = resp.get("bossInfo") if isinstance(resp.get("bossInfo"), dict) else resp
            self.log_msg(
                f"GetThongTinBoss level={self.level_index} diff={self.difficulty_index} | "
                f"canOpen={info.get('canOpen','?')} bossPoint={info.get('bossPoint','?')}"
            )
        else:
            self.log_msg(f"GetThongTinBoss response bất thường: {resp!r}")
        return self.last_boss_info

    def start_battle(self) -> Optional[str]:
        if not self.sc or not self.sc.is_connected():
            self.log_msg("Socket không connected")
            return None

        start_operation_id = uuid.uuid4().hex
        payload = {
            "class": self.EVENT_NAME,
            "method": "ThamChienBoss",
            "data": {
                "levelIndex": str(self.level_index),
                "difficultyIndex": str(self.difficulty_index),
                "useBonus": "false",
                "startOperationId": start_operation_id,
            },
        }
        resp = self._unwrap(self.sc.request("SendRequest", payload, timeout=15))
        if not isinstance(resp, dict):
            self.log_msg(f"ThamChienBoss response bất thường: {resp!r}")
            return None

        status = str(resp.get("status", "")).lower()
        if status in {"1", "error", "fail", "failed"} or resp.get("errorCode"):
            self.log_msg(f"ThamChienBoss bị từ chối: {resp.get('message') or resp}")
            return None

        team = resp.get("doihinh")
        if not isinstance(team, list):
            nested = self._find_value(resp, "doihinh")
            team = nested if isinstance(nested, list) else []

        ids: List[str] = []
        for item in team:
            if isinstance(item, str):
                did = item
            elif isinstance(item, dict):
                did = item.get("id") or item.get("dragonId")
            else:
                did = None
            if did and str(did) not in ids:
                ids.append(str(did))
        self.event_team_ids = ids

        session = self._find_value(resp, "battleSessionId")
        if session:
            self.last_session = str(session)
            self.log_msg(
                f"ThamChienBoss OK | level={self.level_index} diff={self.difficulty_index} | "
                f"team={len(ids)} | session={self.last_session}"
            )
            return self.last_session

        self.log_msg(f"ThamChienBoss không có battleSessionId: {resp!r}")
        return None

    def _choose_ids_for_battle(self) -> List[str]:
        server_team = set(self.event_team_ids)
        selected = self.get_selected_dragon_ids()
        if server_team:
            usable = [x for x in selected if x in server_team]
            if usable:
                skipped = [x for x in selected if x not in server_team]
                if skipped:
                    self.log_msg(f"Bỏ {len(skipped)} rồng không nằm trong đội event hiện tại")
                return usable
            # Tránh trường hợp UI giữ selection cũ nhưng event team đã thay đổi.
            self.log_msg("Selection hiện tại không trùng đội event — dùng toàn bộ đội event server trả về")
            return list(self.event_team_ids)
        return selected or list(self.dragon_ids)

    def _summon_dragons(self, session: str, dragon_ids: Optional[List[str]] = None,
                        delay: float = 0.3) -> int:
        ids = list(dragon_ids) if dragon_ids is not None else self._choose_ids_for_battle()
        # ThamChienBoss trả doihinh; chỉ dùng các ID thuộc đội đó để tránh
        # lỗi dragon_not_in_battle_team giống lỗi đã gặp ở Boss.
        if self.event_team_ids:
            allowed = set(self.event_team_ids)
            filtered = [x for x in ids if x in allowed]
            if filtered:
                skipped = len(ids) - len(filtered)
                if skipped:
                    self.log_msg(f"Bỏ {skipped} ID ngoài doihinh event")
                ids = filtered
            else:
                self.log_msg("Không có ID được chọn nào nằm trong doihinh event — dùng toàn bộ đội server")
                ids = list(self.event_team_ids)
        if not ids:
            self.log_msg("Không có rồng để triệu hồi")
            return 0

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
        self.log_msg(f"Đã triệu hồi {count}/{len(ids)} rồng event")
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

    def finish_battle(self, session: str) -> Optional[dict]:
        payload = {
            "class": self.EVENT_NAME,
            "method": "KetQuaBoss",
            "data": {
                "ketqua": "win",
                "battleSessionId": session,
            },
        }
        resp = self._unwrap(self.sc.request("SendRequest2", payload, timeout=20))
        self.last_result = resp if isinstance(resp, dict) else None
        if isinstance(resp, dict):
            status = str(resp.get("status", "")).lower()
            if status in {"1", "error", "fail", "failed"} or resp.get("errorCode"):
                self.log_msg(f"KetQuaBoss thất bại: {resp.get('message') or resp}")
                return resp
            self.log_msg(f"KetQuaBoss OK | won={resp.get('won', '?')} committed={resp.get('resultCommitted', '?')}")
            return resp
        self.log_msg(f"KetQuaBoss response bất thường: {resp!r}")
        return None

    def flow(self, battle_seconds: int = 60, summon_interval: float = 0.5,
             summon_team: bool = True, summon_suphu: bool = True,
             dragon_ids: Optional[List[str]] = None, dragon_delay: float = 0.3,
             level_index: Optional[int] = None, difficulty_index: Optional[int] = None) -> bool:
        if self._fighting:
            self.log_msg("Đang có một trận event chạy")
            return False
        lock = getattr(self.sc, "combat_lock", None)
        if lock is not None:
            with lock:
                return self._flow_locked(
                    battle_seconds, summon_interval, summon_team, summon_suphu,
                    dragon_ids, dragon_delay, level_index, difficulty_index,
                )
        return self._flow_locked(
            battle_seconds, summon_interval, summon_team, summon_suphu,
            dragon_ids, dragon_delay, level_index, difficulty_index,
        )

    def _flow_locked(self, battle_seconds, summon_interval, summon_team,
                     summon_suphu, dragon_ids, dragon_delay,
                     level_index=None, difficulty_index=None) -> bool:
        self._fighting = True
        self._stop.clear()
        try:
            self.set_stage(
                self.level_index if level_index is None else level_index,
                self.difficulty_index if difficulty_index is None else difficulty_index,
            )
            self.log_msg(f"=== BẮT ĐẦU ẢI EVENT level={self.level_index} diff={self.difficulty_index} ===")

            info = self.get_boss_info()
            if isinstance(info, dict):
                bi = info.get("bossInfo") if isinstance(info.get("bossInfo"), dict) else info
                if bi.get("canOpen") is False:
                    self.log_msg("Server báo canOpen=false — dừng trận")
                    return False
                bp = bi.get("bossPoint")
                if bp is not None:
                    try:
                        if int(bp) <= 0:
                            self.log_msg("Đã hết bossPoint — dừng trận")
                            return False
                    except Exception:
                        pass

            session = self.start_battle()
            if not session:
                return False

            ids = list(dragon_ids) if dragon_ids is not None else self._choose_ids_for_battle()
            if summon_team:
                self._summon_dragons(session, dragon_ids=ids, delay=dragon_delay)
            else:
                self.log_msg("Tắt thả rồng đội hình")

            duration = max(5, int(battle_seconds or 60))
            if summon_suphu:
                self._summon_suphu_loop(duration, summon_interval)
            else:
                self.log_msg(f"Chờ battle {duration}s (không thả Sư Phụ)")
                end = time.time() + duration
                while time.time() < end and not self._stop.is_set():
                    time.sleep(0.2)

            if self._stop.is_set():
                self.log_msg("Đã dừng trận trước khi KetQuaBoss")
                return False

            result = self.finish_battle(session)
            if not isinstance(result, dict):
                return False
            status = str(result.get("status", "")).lower()
            if status in {"1", "error", "fail", "failed"} or result.get("errorCode"):
                return False
            if result.get("won") is False:
                return False

            self.log_msg("✅ Hoàn tất ải event")
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
                  progress_fn: Optional[Callable[[int, int, bool], None]] = None,
                  level_index: Optional[int] = None, difficulty_index: Optional[int] = None) -> dict:
        try:
            total = max(1, int(runs))
        except Exception:
            total = 1
        self._stop.clear()
        ok_count = 0
        fail_count = 0
        self.log_msg(f"AUTO bắt đầu: {total} lượt | level={level_index if level_index is not None else self.level_index} diff={difficulty_index if difficulty_index is not None else self.difficulty_index}")
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
                level_index=level_index,
                difficulty_index=difficulty_index,
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
        result = {
            "requested": total,
            "completed": ok_count + fail_count,
            "ok": ok_count,
            "fail": fail_count,
            "stopped": self._stop.is_set(),
        }
        self.log_msg(f"AUTO kết thúc: {result}")
        return result

    def stop(self) -> None:
        self._stop.set()


__all__ = ["EventSinhNhatAiController"]
