# core/boss_socket.py
"""
Boss Thế Giới — FULL AUTO theo lịch VN + thả rồng liên tục (đúng protocol log).

Lịch UI (giờ VN):
  Boss thường: 5h, 8h, 13h, 15h, 17h, 19h, 21h hàng ngày
  Boss liên SV: 20h thứ 3, 5, 7

Combat (danhboss.txt):
  - emit("trieuhoirong", {dragonId, requestId, reviveRequested})
  - server: VienChinh / UpdateHpBoss
  - rồng chết: emit("rongdie", ...) rồi thả lại
  - DanhBossTG = sát thương thực tế client báo (KHÔNG phải ô chỉnh dame)
  - Kết thúc: BossDie → XemDameBossTG → ReplayData → xong slot
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, List, Optional, Set, Tuple

VN = timezone(timedelta(hours=7))

BOSS_THUONG_HOURS = (5, 8, 13, 15, 17, 19, 21)
BOSS_LIEN_WEEKDAYS = (1, 3, 5)  # Tue Thu Sat
BOSS_LIEN_HOUR = 20


def now_vn() -> datetime:
    return datetime.now(VN)


def next_boss_thuong(from_dt: Optional[datetime] = None) -> datetime:
    base = from_dt or now_vn()
    d = base.replace(minute=0, second=0, microsecond=0)
    for _ in range(48):
        if d.hour in BOSS_THUONG_HOURS and d > base:
            return d
        d += timedelta(hours=1)
    return base + timedelta(hours=1)


def next_boss_lien(from_dt: Optional[datetime] = None) -> datetime:
    base = from_dt or now_vn()
    d = base.replace(minute=0, second=0, microsecond=0)
    for _ in range(24 * 8):
        if d.weekday() in BOSS_LIEN_WEEKDAYS and d.hour == BOSS_LIEN_HOUR and d > base:
            return d
        d += timedelta(hours=1)
    return base + timedelta(days=1)


def seconds_until(target: datetime, from_dt: Optional[datetime] = None) -> int:
    base = from_dt or now_vn()
    return max(0, int((target - base).total_seconds()))


def in_window(hours: Tuple[int, ...], from_dt: datetime, window_min: int) -> Optional[datetime]:
    """Trả về mốc mở nếu đang trong cửa sổ sau giờ mở."""
    for h in reversed(hours):
        open_at = from_dt.replace(hour=h, minute=0, second=0, microsecond=0)
        if open_at <= from_dt < open_at + timedelta(minutes=window_min):
            return open_at
    y = (from_dt - timedelta(days=1)).replace(hour=hours[-1], minute=0, second=0, microsecond=0)
    if y <= from_dt < y + timedelta(minutes=window_min):
        return y
    return None


class BossTheGioiController:
    def __init__(self, socket_client, stats, log_fn: Optional[Callable] = None):
        self.sc = socket_client
        self.stats = stats
        self.log = log_fn or print

        # --- cấu hình auto ---
        self.enter_before_sec: int = 0
        self.window_thuong_min: int = 20
        self.window_lien_min: int = 25
        self.fight_both: bool = True
        self.summon_delay: float = 0.28      # giữa 2 lần thả rồng
        self.attack_interval: float = 0.55
        self.attack_damage: int = 500
        self.max_fight_sec: int = 900         # timeout 1 trận
        self.poll_interval: float = 25

        # danh sách rồng để thả (id string) — set từ login / UI
        self.dragon_ids: List[str] = []

        # runtime
        self.auto_enabled: bool = True
        self._auto_thread: Optional[threading.Thread] = None
        self._stop_auto = threading.Event()
        self._fighting = False
        self._stop_fight = threading.Event()
        self._last_fight_key: str = ""  # tương thích log/cũ
        # Mỗi mốc Boss là một slot độc lập. Boss chung nhiều người chơi:
        # một người hạ boss chỉ kết thúc slot hiện tại, không ảnh hưởng các slot sau.
        self._handled_slots: Set[str] = set()
        # Kết quả mở Boss gần nhất: "", "dead" hoặc "error".
        # "dead" nghĩa server đã xác nhận slot hiện tại đã bị hạ, không retry.
        self._last_open_failure: str = ""
        self._boss_die_at: float = 0.0
        self._alive: Set[str] = set()         # rồng đang trong trận
        self._death_meta: dict[str, tuple[str, str]] = {}  # dragonId -> (deathRequestId, deathCycle)
        self.suphu_interval: float = 0.5
        self._summon_lock = threading.Lock()

        self.boss_thuong_seconds: Optional[int] = None
        self.boss_lien_seconds: Optional[int] = None
        self.last_hp: Optional[int] = None
        self.last_max_hp: Optional[int] = None
        self.last_rank: Optional[int] = None
        self.last_sat_thuong: int = 0
        self.last_tong_sat_thuong: int = 0
        self.boss_alive: bool = False
        self.status_text: str = "Chưa chạy"
        self._on_status: Optional[Callable[[str], None]] = None
        self._hooked_raw = False
        # DailyVN sets this during its startup/retry lane. While active, the
        # scheduler must not steal the battle team in the middle of a mission.
        self._daily_priority = False
        # Event controller được gắn từ AccountPanel để mỗi mốc Boss luôn
        # đọc lại nhiệm vụ Boss từ server; không dùng trạng thái "đã xong"
        # của daily cycle để bỏ qua mốc Boss tiếp theo.
        self._event_ctrl = None

    # ------------------------------------------------------------------ utils
    def log_msg(self, msg: str) -> None:
        self.log(f"[Boss] {msg}")

    def set_status_callback(self, fn: Callable[[str], None]) -> None:
        self._on_status = fn

    def _set_status(self, text: str) -> None:
        self.status_text = text
        if self._on_status:
            try:
                self._on_status(text)
            except Exception:
                pass

    def _fmt_sec(self, sec: Optional[int]) -> str:
        if sec is None:
            return "?"
        sec = max(0, int(sec))
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"

    def schedule_summary(self) -> str:
        n = now_vn()
        nt, nl = next_boss_thuong(n), next_boss_lien(n)
        return (
            f"VN {n.strftime('%H:%M %d/%m')} | "
            f"Thường {nt.strftime('%H:%M')} (còn {self._fmt_sec(seconds_until(nt, n))}) | "
            f"LiênSV {nl.strftime('%H:%M %a')} (còn {self._fmt_sec(seconds_until(nl, n))}) | "
            f"Rồng thả: {len(self.dragon_ids)}"
        )

    def set_dragon_ids(self, ids: List[str]) -> None:
        self.dragon_ids = [str(x) for x in ids if x]
        self.log_msg(f"Danh sách thả rồng: {len(self.dragon_ids)} con")

    def set_event_controller(self, event_ctrl) -> None:
        """Gắn EventSocketController chỉ cho lane Boss.

        Callback này phục vụ việc refresh/claim nhiệm vụ Boss sau từng mốc;
        không thay đổi các automation khác.
        """
        self._event_ctrl = event_ctrl

    def _sync_event_boss_missions(self, mode: str, phase: str = "before") -> None:
        """Refresh nhiệm vụ Boss ngay tại từng mốc Boss.

        DailyVN có thể đã kết thúc cycle trong ngày, nhưng nhiệm vụ Boss có
        thể vẫn còn tiến độ cần thêm ở mốc tiếp theo. Vì vậy lane Boss đọc
        GetData trực tiếp mỗi lần vào Boss và chỉ claim khi server đã đạt target.
        """
        ctrl = self._event_ctrl
        if not ctrl or not hasattr(ctrl, "fetch_pending_specs"):
            return
        key = "ThamGiaBossHuyetUng" if mode == "lienserver" else "ThamGiaBossTheGioi"
        event_names = ("EventDaiChienThuyQuai", "EventSinhNhat2026")
        for event_name in event_names:
            try:
                specs = ctrl.fetch_pending_specs(event_name, verbose=False)
                spec = specs.get(key) if isinstance(specs, dict) else None
                if not spec:
                    continue
                cur = int(spec.get("dalam", 0) or 0)
                target = int(spec.get("maxnhiemvu", 1) or 1)
                if phase == "before":
                    self.log_msg(
                        f"Nhiệm vụ {key} | {event_name}: {cur}/{target} → refresh tại mốc Boss"
                    )
                elif cur >= target and hasattr(ctrl, "claim_mission"):
                    if ctrl.claim_mission(event_name, key):
                        self.log_msg(
                            f"✓ Claim nhiệm vụ Boss: {event_name}/{key} ({cur}/{target})"
                        )
            except Exception as e:
                self.log_msg(f"Sync nhiệm vụ Boss {event_name}/{key} lỗi: {e}")

    def set_daily_priority(self, active: bool) -> None:
        self._daily_priority = bool(active)
        self.log_msg(f"DailyVN priority: {'ON' if self._daily_priority else 'OFF'}")

    def next_scheduled_slot(self, mode: str = "thuong", from_dt: Optional[datetime] = None) -> datetime:
        """Mốc Boss kế tiếp, dùng để defer khi server báo slot đã kết thúc."""
        base = from_dt or now_vn()
        return next_boss_lien(base) if mode == "lienserver" else next_boss_thuong(base)

    def is_scheduled_now(self, mode: str = "thuong", from_dt: Optional[datetime] = None) -> bool:
        """True chỉ khi đang trong cửa sổ boss đã mở theo giờ VN.

        Hàm này dùng cho mission runner: nhiệm vụ boss pending không được phép
        tự biến thành lệnh vào đánh trước giờ. Không dùng ``boss*Seconds == 0``
        làm điều kiện vì capture thực tế cho thấy server có thể trả 0 ngoài giờ.
        """
        n = from_dt or now_vn()
        if mode == "lienserver":
            if n.weekday() not in BOSS_LIEN_WEEKDAYS:
                return False
            open_at = n.replace(hour=BOSS_LIEN_HOUR, minute=0, second=0, microsecond=0)
            return open_at <= n < open_at + timedelta(minutes=self.window_lien_min)

        if n.hour not in BOSS_THUONG_HOURS:
            return False
        open_at = n.replace(minute=0, second=0, microsecond=0)
        return open_at <= n < open_at + timedelta(minutes=self.window_thuong_min)

    def load_dragons_from_login(self, login_data: Any) -> int:
        """Lấy id rồng từ LoginSuccess (đảo + túi)."""
        ids: List[str] = []
        try:
            from core.dragon import parse_dragons_from_login
            island, bag, _ = parse_dragons_from_login(login_data if isinstance(login_data, dict) else {})
            for d in island + bag:
                if d.id and d.id not in ids:
                    ids.append(d.id)
        except Exception as e:
            self.log_msg(f"parse dragon login: {e}")
        # fallback stats.bag_dragons
        if not ids and self.stats and getattr(self.stats, "bag_dragons", None):
            for r in self.stats.bag_dragons:
                if isinstance(r, dict):
                    rid = r.get("id")
                    if rid and rid not in ids:
                        ids.append(str(rid))
        if ids:
            self.dragon_ids = ids
            self.log_msg(f"Load {len(ids)} rồng từ login để thả boss")
        return len(ids)

    # ------------------------------------------------------------------ socket
    def _ensure_raw_hook(self) -> None:
        if self._hooked_raw or not self.sc:
            return
        prev = self.sc.on_raw

        def _hook(event, data):
            if prev:
                try:
                    prev(event, data)
                except Exception:
                    pass
            self._on_socket_event(event, data)

        self.sc.on_raw = _hook
        self._hooked_raw = True

    def _on_socket_event(self, event: str, data: Any) -> None:
        try:
            if event == "UpdateHpBoss" and isinstance(data, dict):
                hp = int(str(data.get("hp", 0)).replace(",", "") or 0)
                maxhp = int(str(data.get("maxhp", 0)).replace(",", "") or 0)
                self.last_hp = hp
                self.last_max_hp = maxhp
                self.boss_alive = hp > 0
                if hp <= 0 and self._fighting:
                    self._stop_fight.set()

            elif event == "BossDie":
                self.boss_alive = False
                self.last_hp = 0
                self._stop_fight.set()
                # Đánh dấu vừa xong 1 slot — không vào lại trong cooldown
                self._boss_die_at = time.time()
                extra = data.get("nguoitieudietboss", "") if isinstance(data, dict) else ""
                self.log_msg(f"BossDie {extra} — không auto vào lại trong 15 phút")
                self._set_status("Boss đã chết — out, chờ mốc sau")

            elif event == "rongdie":
                # Boss capture thật: khi rồng chết, resend với reviveRequested=true
                # + deathRequestId + deathCycle; không gửi battleSessionId.
                did = None
                if isinstance(data, dict):
                    did = data.get("dragonId")
                    death_id = data.get("deathRequestId")
                    death_cycle = data.get("deathCycle")
                else:
                    death_id = None
                    death_cycle = None
                    if isinstance(data, str):
                        did = data
                if did:
                    sdid = str(did)
                    with self._summon_lock:
                        self._alive.discard(sdid)
                        if death_id:
                            self._death_meta[sdid] = (str(death_id), str(death_cycle or "1"))
                    if self._fighting and not self._stop_fight.is_set():
                        self._trieu_hoi(sdid, revive=True, death_request_id=death_id, death_cycle=death_cycle)

            elif event == "VienChinh" and isinstance(data, dict):
                x = data.get("xanhtrieuhoi") or {}
                if isinstance(x, dict) and x.get("id"):
                    with self._summon_lock:
                        self._alive.add(str(x["id"]))
                failed = data.get("trieuhoithatbai")
                if isinstance(failed, dict):
                    # Chỉ log spam khi đang trong Boss fight; VC tự xử lý đội hình.
                    if getattr(self, "_fighting", False):
                        did = failed.get("dragonId") or "?"
                        code = failed.get("errorCode") or "?"
                        self.log_msg(f"Triệu hồi thất bại {did} | {code}: {failed.get('message', '')}")

            if isinstance(data, dict):
                if "bossThuongSeconds" in data:
                    self.boss_thuong_seconds = int(data["bossThuongSeconds"])
                if "bossLienServerSeconds" in data:
                    self.boss_lien_seconds = int(data["bossLienServerSeconds"])
                if data.get("status") == "ok" and ("satthuong" in data or "tongsatthuong" in data):
                    self.last_rank = data.get("rank")
                    self.last_sat_thuong = int(data.get("satthuong") or 0)
                    self.last_tong_sat_thuong = int(data.get("tongsatthuong") or 0)
        except Exception as e:
            self.log_msg(f"parse: {e}")

    def _trieu_hoi(
        self,
        dragon_id: str,
        revive: bool = False,
        death_request_id: Optional[str] = None,
        death_cycle: Optional[str] = None,
    ) -> None:
        if not self.sc or not dragon_id:
            return
        payload = {
            "dragonId": str(dragon_id),
            "requestId": uuid.uuid4().hex,
            "reviveRequested": bool(revive),
        }
        if revive:
            meta = None
            with self._summon_lock:
                if death_request_id and death_cycle:
                    meta = (str(death_request_id), str(death_cycle))
                else:
                    meta = self._death_meta.get(str(dragon_id))
            if meta:
                payload["deathRequestId"] = meta[0]
                payload["deathCycle"] = meta[1]
        # Boss capture thật KHÔNG có battleSessionId trong trieuhoirong.
        self.sc.emit("trieuhoirong", payload)

    def _trieu_suphu(self) -> None:
        if not self.sc:
            return
        self.sc.emit("trieuhoirong", "suphu")

    # ------------------------------------------------------------------ API nhẹ
    @staticmethod
    def _unwrap_ack(resp: Any) -> Any:
        """Socket.IO ACK đôi khi trả object, đôi khi là [object]."""
        if isinstance(resp, list) and len(resp) == 1:
            return resp[0]
        return resp

    def _extract_dragon_ids(self, obj: Any) -> List[str]:
        """Bóc ID đội hình Boss từ ACK dù server bọc 1/nhiều lớp.

        Không fallback sang rồng login ở đây: đội hình dùng để đánh Boss phải
        đến từ response của request mở Boss, nhưng response có thể là
        ``doihinh``, ``data.doihinh`` hoặc list wrapper.
        """
        ids: List[str] = []
        seen_nodes = set()

        def add_id(value: Any) -> None:
            if value is None:
                return
            sid = str(value).strip()
            if sid and sid not in ids:
                ids.append(sid)

        def walk(node: Any, depth: int = 0) -> None:
            if depth > 8 or len(ids) >= 20:
                return
            if isinstance(node, (dict, list)):
                marker = id(node)
                if marker in seen_nodes:
                    return
                seen_nodes.add(marker)

            if isinstance(node, dict):
                # Các tên ID thường gặp của đội hình Boss.
                for key in ("id", "dragonId", "idrong", "idRong"):
                    if key in node and isinstance(node.get(key), (str, int)):
                        add_id(node.get(key))
                        break

                # Ưu tiên các container chứa đội hình.
                for key in ("doihinh", "DoiHinh", "team", "ids", "dragonIds", "data"):
                    if key in node:
                        walk(node.get(key), depth + 1)

                # Nếu chưa thấy gì, tiếp tục tìm wrapper lồng sâu.
                if not ids:
                    for value in node.values():
                        walk(value, depth + 1)

            elif isinstance(node, list):
                # List các object rồng / slot đội hình.
                for item in node:
                    if isinstance(item, dict):
                        did = item.get("id") or item.get("dragonId") or item.get("idrong") or item.get("idRong")
                        if did is not None:
                            add_id(did)
                        # Trường hợp item còn bọc data/doihinh.
                        if not did:
                            walk(item, depth + 1)
                    elif isinstance(item, (str, int)):
                        add_id(item)

        walk(obj)
        return ids

    def start_boss_battle(self, mode: str = "thuong") -> List[str]:
        """Chuẩn bị đúng loại Boss rồi mới mở trận bằng DanhBossTG.

        ``XemBoss`` / ``XemBossLienServer`` là bước mode-specific hiện có trong
        protocol; bản cũ định nghĩa nhưng không hề gọi, khiến nút LiênSV thực
        chất vẫn chạy cùng một flow như Boss thường. Sau bước xem,
        ``DanhBossTG`` trả đội hình của trận; đội hình này mới được dùng để
        triệu hồi.
        """
        if not self.sc or not self.sc.is_connected():
            self._last_open_failure = "error"
            return []
        self._last_open_failure = ""
        try:
            # Quan trọng: chọn đúng lane Boss trước khi DanhBossTG.
            self.xem_boss(mode)

            resp = self.sc.request("SendRequest", {
                "class": "BossTheGioi",
                "method": "DanhBossTG",
            }, timeout=10)
            resp = self._unwrap_ack(resp)
            if isinstance(resp, dict):
                status = str(resp.get("status", "")).lower()
                if status not in {"ok", "0", "success", ""}:
                    msg = str(resp.get("status", "") or resp.get("message", ""))
                    low = msg.lower()
                    self.log_msg(f"DanhBossTG mở trận bị từ chối: {resp}")
                    if "đã bị tiêu diệt" in low or "boss đã chết" in low or "boss đã bị tiêu diệt" in low:
                        self._last_open_failure = "dead"
                    else:
                        self._last_open_failure = "error"
                    return []
                ids = self._extract_dragon_ids(resp)
                nameboss = resp.get("nameboss") or resp.get("bossname") or "?"
                self.log_msg(
                    f"DanhBossTG mở trận OK | boss={nameboss} | đội hình={len(ids)} rồng | "
                    f"buffdame={resp.get('buffdame', 0)} buffxuyengiap={resp.get('buffxuyengiap', 0)}"
                )
                if ids:
                    self.dragon_ids = ids
                    return ids
                self.log_msg(
                    f"DanhBossTG không bóc được đội hình | mode={mode} | "
                    f"keys={list(resp.keys())[:20]} | raw={str(resp)[:500]}"
                )
                self._last_open_failure = "error"
                return []
            self.log_msg(f"DanhBossTG ACK không hợp lệ: {resp!r}")
            self._last_open_failure = "error"
        except Exception as e:
            self.log_msg(f"DanhBossTG exception: {e}")
            self._last_open_failure = "error"
        return []

    def get_team(self) -> List[str]:
        """Giữ lại helper cũ, nhưng Boss thật lấy đội hình ngay từ DanhBossTG."""
        return list(self.dragon_ids)

    def danh_boss(self) -> None:
        if not self.sc: return
        # Sau khi đã mở trận, client gửi event này liên tục để báo combat/damage.
        self.sc.emit("DanhBossTG", str(self.attack_damage))

    def get_guardian_skills(self) -> None:
        if not self.sc:
            return
        self.sc.emit("SendRequest2", {
            "class": "DoiHinh2",
            "method": "GetBattleGuardianSkills",
            "data": {"battleContext": "Boss"},
        })
        time.sleep(0.4)

    def get_damage_info(self) -> None:
        if not self.sc:
            return
        self.sc.emit("SendRequest2", {
            "class": "BossTheGioi",
            "method": "GetBossBattleDamageInfo",
            "silent": "1",
        })
        time.sleep(0.4)

    def xem_thoi_gian(self) -> None:
        if not self.sc:
            return
        try:
            if hasattr(self.sc, "request"):
                resp = self.sc.request("SendRequest2", {
                    "class": "BossTheGioi",
                    "method": "XemThoiGianDauBoss",
                }, timeout=5)
            else:
                resp = self.sc.call("SendRequest2", {
                    "class": "BossTheGioi",
                    "method": "XemThoiGianDauBoss",
                }, timeout=5)
            if isinstance(resp, list) and resp and isinstance(resp[0], dict):
                resp = resp[0]
            if isinstance(resp, dict) and "bossThuongSeconds" in resp:
                self.boss_thuong_seconds = int(resp["bossThuongSeconds"])
                if "bossLienServerSeconds" in resp:
                    self.boss_lien_seconds = int(resp["bossLienServerSeconds"])
                return
        except Exception:
            pass
        self.sc.emit("SendRequest2", {
            "class": "BossTheGioi",
            "method": "XemThoiGianDauBoss",
        })
        time.sleep(0.6)

    def xem_boss(self, mode: str = "thuong") -> None:
        if not self.sc:
            return
        if mode == "lienserver":
            self.sc.emit("SendRequest", {
                "class": "BossTheGioi",
                "method": "XemBossLienServer",
                "data": {"bossMode": "lienserver"},
            })
        else:
            self.sc.emit("SendRequest", {
                "class": "BossTheGioi",
                "method": "XemBoss",
                "data": {"bossMode": "thuong"},
            })
        time.sleep(0.6)

    def xem_dame_tong(self) -> None:
        if not self.sc:
            return
        self.sc.emit("SendRequest", {
            "class": "BossTheGioi",
            "method": "XemDameBossTG",
        })
        time.sleep(0.5)

    def _finish_and_out(self, won: bool = False) -> None:
        try:
            self.xem_dame_tong()
            self.get_damage_info()
            self.log_msg(f"OUT xong | ST={self.last_tong_sat_thuong} rank={self.last_rank} {'BossDie' if won else 'hết slot'}")
        except Exception as e:
            self.log_msg(f"finish/out lỗi: {e}")

    # ------------------------------------------------------------------ FULL FIGHT
    def enter_and_fight(self, mode: str = "thuong") -> bool:
        # Boss phải xếp hàng với event mission / các automation action khác.
        # Khi đang tới giờ Boss, mission runner sẽ nhường lock để Boss đi trước.
        auto_lock = getattr(self.sc, "automation_lock", None)
        combat_lock = getattr(self.sc, "combat_lock", None)
        if auto_lock and combat_lock:
            with auto_lock:
                with combat_lock:
                    return self._enter_and_fight_locked(mode)
        if auto_lock:
            with auto_lock:
                return self._enter_and_fight_locked(mode)
        if combat_lock:
            with combat_lock:
                return self._enter_and_fight_locked(mode)
        return self._enter_and_fight_locked(mode)

    def _enter_and_fight_locked(self, mode: str = "thuong") -> bool:
        """
        Full auto 1 slot:
          vào map → thả rồng liên tục → boss chết / hết giờ → out
        """
        if not self.sc or not self.sc.is_connected():
            self.log_msg("Chưa kết nối")
            return False
        if self._fighting:
            self.log_msg("Đang đánh — bỏ qua")
            return False
        if not self.dragon_ids:
            self.log_msg("CHƯA CÓ DANH SÁCH RỒNG — không thả được. Cần login / set_dragon_ids")
            # vẫn vào map thử
        self._ensure_raw_hook()
        self._fighting = True
        # Tới đúng khung giờ Boss: tạm đóng băng mọi automation khác ở
        # lớp SocketClient. Các loop khác không bị tắt; request tiếp theo sẽ
        # tự chờ và chạy tiếp sau khi Boss kết thúc. Chỉ lane Boss được gửi.
        try:
            if hasattr(self.sc, "set_boss_priority"):
                self.sc.set_boss_priority(True)
                self.log_msg("⏸️ Đóng băng các automation khác → ưu tiên Boss")
        except Exception as e:
            self.log_msg(f"Bật Boss priority lỗi: {e}")
        self._stop_fight.clear()
        self.boss_alive = True
        self._alive.clear()
        label = "LiênSV" if mode == "lienserver" else "Thường"
        t0 = time.time()
        self._set_status(f"Vào boss {label}...")

        suphu_stop = None
        suphu_thread = None
        try:
            # Mỗi mốc Boss đều refresh nhiệm vụ từ server, không phụ thuộc
            # event_missions_day của DailyVN.
            self._sync_event_boss_missions(mode, phase="before")

            # Bắt đầu trận bằng đúng request thực tế của client.
            # Response DanhBossTG trả luôn doihinh của trận; dùng list này để
            # triệu hồi, thay vì gọi DoiHinh2.GetData trước khi mở trận.
            summon_ids = self.start_boss_battle(mode)
            if not summon_ids:
                self.log_msg("Không mở được Boss hoặc response không có đội hình — dừng để tránh thả sai rồng")
                self._set_status("Lỗi: DanhBossTG không trả đội hình")
                return False
            self.log_msg(f"Đội hình Boss từ DanhBossTG: {len(summon_ids)} con")
            self.get_guardian_skills()
            self.get_damage_info()
            time.sleep(0.3)

            # CHỈ lane Boss: triệu hồi Sư Phụ đúng 15 lần cho mỗi trận.
            # Không đụng tới Sư Phụ của Viễn Chinh / Ải Thí Luyện / Thủy Quái.
            suphu_stop = threading.Event()
            def _spam_suphu():
                if hasattr(self.sc, "add_boss_priority_thread"):
                    try:
                        self.sc.add_boss_priority_thread(True)
                    except Exception:
                        pass
                try:
                    for i in range(15):
                        if suphu_stop.is_set() or self._stop_fight.is_set() or self._stop_auto.is_set():
                            break
                        try:
                            self._trieu_suphu()
                            self.log_msg(f"Sư Phụ Boss: {i + 1}/15")
                        except Exception:
                            pass
                        if i < 14:
                            suphu_stop.wait(0.5)
                finally:
                    if hasattr(self.sc, "add_boss_priority_thread"):
                        try:
                            self.sc.add_boss_priority_thread(False)
                        except Exception:
                            pass

            suphu_thread = threading.Thread(target=_spam_suphu, name="BossSuphu15", daemon=True)
            suphu_thread.start()

            self.log_msg(
                f"Bắt đầu thả rồng x{len(summon_ids)} | delay={self.summon_delay}s | "
                f"timeout={self.max_fight_sec}s"
            )
            self._set_status(f"Đang thả rồng ({label})...")

            idx = 0
            last_info = 0.0
            last_suphu = time.time()
            while not self._stop_fight.is_set() and not self._stop_auto.is_set():
                if time.time() - t0 > self.max_fight_sec:
                    self.log_msg("Hết giờ trận — out")
                    break
                if not self.sc.is_connected():
                    self.log_msg("Mất kết nối")
                    break

                # thả lần lượt từng rồng; rồng đang sống thì skip
                if summon_ids:
                    did = summon_ids[idx % len(summon_ids)]
                    idx += 1
                    with self._summon_lock:
                        need = did not in self._alive
                    if need:
                        self._trieu_hoi(did, revive=False)
                else:
                    # không có list → không spam bậy
                    time.sleep(1.0)

                # Sư Phụ liên tục trong toàn bộ trận.
                if time.time() - last_suphu >= max(0.1, float(self.suphu_interval)):
                    self._trieu_suphu()
                    last_suphu = time.time()

                # Capture thực tế có DanhBossTG liên tục; không có lệnh này thì chỉ đứng trong map.
                self.danh_boss()

                # định kỳ hỏi damage / status
                if time.time() - last_info > 8:
                    self.get_damage_info()
                    last_info = time.time()
                    hp_s = (
                        f"{self.last_hp}/{self.last_max_hp}"
                        if self.last_hp is not None else "?"
                    )
                    self._set_status(
                        f"{label} | HP {hp_s} | ST {self.last_tong_sat_thuong} | "
                        f"alive {len(self._alive)}/{len(summon_ids)} | "
                        f"{int(time.time() - t0)}s"
                    )

                if self.last_hp is not None and self.last_hp <= 0:
                    break

                time.sleep(self.summon_delay)

            # Dừng spam Sư Phụ trước khi out khỏi trận.
            suphu_stop.set()
            try:
                suphu_thread.join(timeout=1.0)
            except Exception:
                pass

            won = self.last_hp is not None and self.last_hp <= 0
            self._set_status(f"Kết thúc {label} — đang out...")
            self._finish_and_out(won=won)
            # Sau mỗi trận, đọc lại tiến độ nhiệm vụ Boss và claim nếu server
            # đã đủ target. Mốc Boss kế tiếp vẫn sẽ refresh lại từ đầu.
            self._sync_event_boss_missions(mode, phase="after")
            self._set_status(
                f"XONG {label} | ST={self.last_tong_sat_thuong} rank={self.last_rank}"
            )
            return True
        except Exception as e:
            self.log_msg(f"Lỗi fight: {e}")
            self._set_status(f"Lỗi: {e}")
            try:
                self._finish_and_out(won=False)
            except Exception:
                pass
            return False
        finally:
            if suphu_stop is not None:
                try:
                    suphu_stop.set()
                except Exception:
                    pass
            if suphu_thread is not None:
                try:
                    suphu_thread.join(timeout=1.0)
                except Exception:
                    pass
            self._fighting = False
            self._alive.clear()
            with self._summon_lock:
                self._death_meta.clear()
            try:
                if hasattr(self.sc, "set_boss_priority"):
                    self.sc.set_boss_priority(False)
                    self.log_msg("▶️ Boss xong → mở lại các automation khác")
            except Exception as e:
                self.log_msg(f"Tắt Boss priority lỗi: {e}")

    # ------------------------------------------------------------------ AUTO SCHEDULER
    def start_auto(self) -> None:
        self.auto_enabled = True
        self._stop_auto.clear()
        self._ensure_raw_hook()
        if self._auto_thread and self._auto_thread.is_alive():
            self.log_msg("Auto đã chạy")
            return
        self._auto_thread = threading.Thread(target=self._auto_loop, daemon=True)
        self._auto_thread.start()
        self.log_msg("AUTO ON — vào/thả rồng/out theo lịch VN")
        self._set_status("Auto ON | " + self.schedule_summary())

    def stop_auto(self) -> None:
        self.auto_enabled = False
        self._stop_auto.set()
        self._stop_fight.set()
        self.log_msg("AUTO OFF")
        self._set_status("Auto OFF")

    def _slot_key(self, kind: str, when: datetime) -> str:
        return f"{kind}:{when.strftime('%Y%m%d%H')}"

    def _auto_loop(self) -> None:
        while not self._stop_auto.is_set() and self.auto_enabled:
            try:
                if not self.sc or not self.sc.is_connected():
                    self._set_status("Auto: chờ reconnect...")
                    self._stop_auto.wait(5)
                    continue

                n = now_vn()
                # Dọn các slot quá cũ để set không phình vô hạn qua nhiều ngày.
                if len(self._handled_slots) > 96:
                    today_prefix = n.strftime("%Y%m%d")
                    self._handled_slots = {k for k in self._handled_slots if today_prefix in k}
                # DailyVN có thể đang chạy nhiệm vụ, nhưng Boss vẫn phải được
                # kiểm tra độc lập theo lịch. Không được chặn toàn bộ Boss AUTO
                # chỉ vì daily_priority=True; nếu tới khung Boss thì Boss phải
                # giành quyền, đánh xong mới để automation khác tiếp tục.
                if self._daily_priority:
                    self._set_status("Auto Boss: DailyVN đang chạy — vẫn ưu tiên kiểm tra Boss | " + self.schedule_summary())
                try:
                    self.xem_thoi_gian()
                except Exception:
                    pass
                self._set_status("Auto ON | " + self.schedule_summary())

                # KHÔNG dùng cooldown toàn cục sau BossDie.
                # Boss là boss chung nhiều người chơi: nếu người khác hạ xong,
                # chỉ bỏ qua đúng slot hiện tại. Các mốc Boss tiếp theo trong ngày
                # vẫn phải được kiểm tra và đánh bình thường.

                # Ưu tiên countdown SERVER. Nếu còn lâu → không vào (tránh vào nhầm cửa sổ cũ).
                srv_thuong = self.boss_thuong_seconds
                srv_lien = self.boss_lien_seconds

                # --- Liên SV: đánh TẤT CẢ các mốc 20h T3/T5/T7 ---
                # Lịch local là nguồn quyết định slot. srv=0 ngoài giờ không có nghĩa boss đang mở.
                if self.fight_both:
                    nxt_l = next_boss_lien(n)
                    sec_l_local = seconds_until(nxt_l, n)
                    open_l = None
                    if n.weekday() in BOSS_LIEN_WEEKDAYS:
                        cand = n.replace(hour=BOSS_LIEN_HOUR, minute=0, second=0, microsecond=0)
                        if cand <= n < cand + timedelta(minutes=self.window_lien_min):
                            open_l = cand
                    lien_ready = False
                    if sec_l_local <= self.enter_before_sec:
                        lien_ready = True
                        slot_l = nxt_l
                    elif open_l is not None and 0 <= (n - open_l).total_seconds() <= self.window_lien_min * 60:
                        lien_ready = True
                        slot_l = open_l
                    else:
                        slot_l = nxt_l

                    if lien_ready:
                        key = self._slot_key("lien", slot_l)
                        if key not in self._handled_slots:
                            self.log_msg(
                                f"AUTO vào Liên SV ({key}) srv={srv_lien}s local_còn={sec_l_local}s"
                            )
                            ok = bool(self.enter_and_fight("lienserver"))
                            if ok:
                                self._handled_slots.add(key)
                                self._last_fight_key = key
                                self.log_msg(f"Đã xử lý xong slot {key} — các slot Boss sau vẫn hoạt động")
                                self._stop_auto.wait(15)
                            elif self._last_open_failure == "dead":
                                self._handled_slots.add(key)
                                self._last_fight_key = key
                                self.log_msg(
                                    f"Slot {key}: Boss chung đã bị người chơi khác tiêu diệt → "
                                    "bỏ qua ĐÚNG slot này, không khóa các mốc Boss sau"
                                )
                                self._stop_auto.wait(15)
                            else:
                                self.log_msg(f"Slot {key} chưa thành công → giữ quyền retry trong cửa sổ")
                                self._stop_auto.wait(8)
                            continue

                # --- Boss thường: đánh TẤT CẢ các mốc 05,08,13,15,17,19,21 mỗi ngày ---
                # srv=0 không được dùng để quyết định có/không có slot; lịch VN local quyết định.
                nxt_t = next_boss_thuong(n)
                sec_t_local = seconds_until(nxt_t, n)
                open_t = in_window(BOSS_THUONG_HOURS, n, self.window_thuong_min)
                thuong_ready = False

                # 1) Sắp tới giờ mở (còn <= enter_before_sec)
                if sec_t_local <= self.enter_before_sec:
                    thuong_ready = True
                    slot_dt = nxt_t
                # 2) Vừa qua giờ mở, còn trong cửa sổ ngắn (vd 20 phút)
                elif open_t is not None and 0 <= (n - open_t).total_seconds() <= self.window_thuong_min * 60:
                    thuong_ready = True
                    slot_dt = open_t
                else:
                    slot_dt = nxt_t

                # 3) Nếu đã nằm trong cửa sổ vừa mở (open_t), ƯU TIÊN lịch local
                # và không được dùng countdown của mốc kế tiếp để chặn trận hiện tại.
                # Ví dụ lúc 15:05, next_boss_thuong() là 17:00 nên sec_t_local=~2h;
                # dùng sec_t_local ở đây sẽ làm Boss 15h bị chặn dù cửa sổ 15h đang mở.
                # Countdown server chỉ dùng khi CHƯA mở cửa sổ hiện tại.
                if open_t is not None:
                    thuong_ready = True
                elif srv_thuong is not None and srv_thuong > max(self.enter_before_sec, 300):
                    # Chưa tới cửa sổ hiện tại và server còn xa → chờ.
                    thuong_ready = False

                if thuong_ready:
                    key = self._slot_key("thuong", slot_dt)
                    if key not in self._handled_slots:
                        self.log_msg(
                            f"AUTO vào Boss thường ({key}) srv={srv_thuong}s local_còn={sec_t_local}s"
                        )
                        ok = bool(self.enter_and_fight("thuong"))
                        if ok:
                            self._handled_slots.add(key)
                            self._last_fight_key = key
                            self.log_msg(f"Đã xử lý xong slot {key} — các slot Boss sau vẫn hoạt động")
                            self._stop_auto.wait(15)
                        elif self._last_open_failure == "dead":
                            self._handled_slots.add(key)
                            self._last_fight_key = key
                            self.log_msg(
                                f"Slot {key}: Boss chung đã bị người chơi khác tiêu diệt → "
                                "bỏ qua ĐÚNG slot này, không khóa các mốc Boss sau"
                            )
                            self._stop_auto.wait(15)
                        else:
                            self.log_msg(f"Slot {key} chưa thành công → giữ quyền retry trong cửa sổ")
                            self._stop_auto.wait(8)
                        continue

                sec = min(sec_t_local, seconds_until(next_boss_lien(n), n))
                if srv_thuong is not None:
                    sec = min(sec, max(0, srv_thuong))
                sleep_for = 10 if sec <= 180 else (20 if sec <= 900 else self.poll_interval)
                self._stop_auto.wait(sleep_for)
            except Exception as e:
                self.log_msg(f"Auto lỗi: {e}")
                self._stop_auto.wait(12)

        self._set_status("Auto đã dừng")
