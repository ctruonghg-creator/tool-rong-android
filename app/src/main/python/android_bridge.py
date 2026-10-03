"""Android bridge for the Da Rong mobile UI.

The existing core package remains Python. This module removes the desktop Tkinter
layer and exposes a small thread-safe API to Kotlin/Jetpack Compose.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from typing import Any, Dict

_CONFIGURED = False
_BASE_DIR = None
_SESSIONS: Dict[int, "AndroidSession"] = {}
_SESSIONS_LOCK = threading.RLock()


def configure(files_dir: str) -> bool:
    global _CONFIGURED, _BASE_DIR
    _BASE_DIR = os.path.abspath(files_dir)
    os.makedirs(_BASE_DIR, exist_ok=True)
    os.chdir(_BASE_DIR)
    _CONFIGURED = True
    return True


class AndroidSession:
    def __init__(self, slot: int, log_store: deque[str]):
        self.slot = int(slot)
        self.log_store = log_store
        self.log_lock = threading.RLock()
        self.session_lock = threading.RLock()
        self.auth = None
        self.socket = None
        self.stats = None
        self.lai = None
        self.feed = None
        self.island = None
        self.harvest = None
        self.vienchinh = None
        self.ai = None
        self.birthday = None
        self.thuyquai = None
        self.taytuy = None
        self.gift = None
        self.boss = None
        self.dautruong = None
        self.loidai = None
        self.event = None
        self.daily = None
        self.username = ""
        self.password = ""
        self.server_url = "https://daorongsv1.shop"
        self.login_error = ""
        self._closing = False

    def log(self, msg: Any) -> None:
        line = str(msg)
        with self.log_lock:
            self.log_store.append(line)
            if len(self.log_store) > 500:
                self.log_store.popleft()

    def _install_controllers(self, game_token: str) -> None:
        from core.auth import ServerMaintenanceError
        from core.socket_client import SocketClient
        from core.stats import PlayerStats
        from core.lai import LaiController
        from core.feed import FeedController
        from core.island import IslandController
        from core.harvest import HarvestController
        from core.vienchinh import VienChinhController
        from core.ai_thi_luyen import AiThiLuyenController
        from core.event_sinhnhat_ai import EventSinhNhatAiController
        from core.event_thuy_quai import EventThuyQuaiController
        from core.taytuy import TayTuyController
        from core.gift_socket import GiftSocketController
        from core.boss_socket import BossTheGioiController
        from core.dautruong import DauTruongThuThachController
        from core.loi_dai import LoiDaiController
        from core.event_socket import EventSocketController
        from core.daily_vn import DailyVNController

        self.stats = PlayerStats()
        self.socket = SocketClient(
            game_token,
            log_fn=self.log,
            account_id=self.username,
            game_url=self.server_url,
        )
        self.lai = LaiController(self.socket, self.stats, log_fn=self.log)
        self.feed = FeedController(self.socket, self.stats, log_fn=self.log)
        self.island = IslandController(self.socket, self.stats, log_fn=self.log)
        self.harvest = HarvestController(
            self.socket, self.stats, log_fn=self.log, island_ctrl=self.island
        )
        self.vienchinh = VienChinhController(self.socket, self.stats, log_fn=self.log)
        self.ai = AiThiLuyenController(self.socket, self.stats, log_fn=self.log)
        self.birthday = EventSinhNhatAiController(self.socket, self.stats, log_fn=self.log)
        self.thuyquai = EventThuyQuaiController(self.socket, self.stats, log_fn=self.log)
        self.taytuy = TayTuyController(
            self.socket,
            self.stats,
            log_fn=self.log,
            island_ctrl=self.island,
            lai_ctrl=self.lai,
        )
        self.taytuy.set_deps(island_ctrl=self.island, lai_ctrl=self.lai)
        self.gift = GiftSocketController(self.socket, self.stats, log_fn=self.log)
        self.boss = BossTheGioiController(self.socket, self.stats, log_fn=self.log)
        try:
            self.boss.set_daily_priority(True)
        except Exception:
            pass
        self.dautruong = DauTruongThuThachController(self.socket, self.stats, log_fn=self.log)
        self.loidai = LoiDaiController(self.socket, self.stats, log_fn=self.log)

        controllers = {
            "harvest": self.harvest,
            "vienchinh": self.vienchinh,
            "lai": self.lai,
            "feed": self.feed,
            "taytuy": self.taytuy,
            "dautruong": self.dautruong,
            "loi_dai": self.loidai,
            "boss": self.boss,
            "island": self.island,
            "gift": self.gift,
        }
        self.event = EventSocketController(
            self.socket,
            self.stats,
            app_controllers=controllers,
            log_fn=self.log,
        )
        try:
            self.boss.set_event_controller(self.event)
        except Exception:
            pass
        try:
            self.taytuy.set_event_pending_fn(
                lambda: (
                    getattr(self.event, "current_mission_key", None) == "TayTuyRong"
                    or "TayTuyRong" in (getattr(self.event, "last_pending", []) or [])
                )
            )
        except Exception:
            pass

        self.daily = DailyVNController(
            gift_ctrl=self.gift,
            event_ctrl=self.event,
            log_fn=self.log,
            state_file=os.path.join(_BASE_DIR or ".", f"daily_vn_acc_{self.slot}.json"),
        )
        try:
            self.island.set_event_item_ready(False)
            self.daily.set_item_ready_callback(
                lambda ready: self.island.set_event_item_ready(ready)
            )
        except Exception:
            pass

        self.socket.on_login_success = self._on_login_success
        self.socket.on_disconnect = lambda: self.log("❌ Socket bị ngắt — app sẽ giữ trạng thái chờ reconnect")
        self.socket.on_reconnect = lambda: self.log("🔄 Socket reconnect")
        self.socket.on_info = lambda data: None

    def _on_login_success(self, data: Any) -> None:
        self.login_error = ""
        try:
            self.stats.parse(data)
        except Exception as e:
            self.log(f"⚠️ Parse LoginSuccess: {e}")

        try:
            n = self.boss.load_dragons_from_login(data)
            self.log(f"🐉 Boss: {n} rồng sẵn sàng")
        except Exception as e:
            self.log(f"Boss load: {e}")
        try:
            n = self.vienchinh.load_dragons_from_login(data)
            self.log(f"⚔️ Viễn Chinh: {n} rồng sẵn sàng")
        except Exception as e:
            self.log(f"Viễn Chinh load: {e}")
        try:
            n = self.ai.load_dragons_from_login(data)
            self.log(f"🧪 Ải Thí Luyện: {n} rồng sẵn sàng")
        except Exception as e:
            self.log(f"Ải load: {e}")
        try:
            n = self.thuyquai.load_dragons_from_login(data)
            self.log(f"🐙 Thủy Quái: {n} rồng sẵn sàng")
        except Exception as e:
            self.log(f"Thủy Quái load: {e}")
        try:
            n = self.birthday.load_dragons_from_login(data)
            self.log(f"🎂 Sinh nhật: {n} rồng sẵn sàng")
        except Exception as e:
            self.log(f"Sinh nhật load: {e}")
        try:
            self.dautruong.set_display_suffix(self.username)
        except Exception:
            pass
        try:
            self.island.discover_from_login(data)
        except Exception as e:
            self.log(f"Đảo discover lỗi: {e}")

        try:
            self.daily.start(self.username)
        except Exception as e:
            self.log(f"VN Auto init lỗi: {e}")

        self.log(
            f"🎯 LoginSuccess — túi: {self.stats.bag_count}/{self.stats.bag_max} | "
            f"ThachAnh: {self.stats.thach_anh:,}"
        )

    def login(self, username: str, password: str, server_url: str) -> None:
        if not username or not password:
            self.log("❌ Chưa nhập User/Pass")
            return
        if self.socket:
            try:
                self.disconnect()
            except Exception:
                pass
        self.username = username.strip()
        self.password = password
        self.server_url = str(server_url or "https://daorongsv1.shop").strip().rstrip("/")
        self.login_error = ""
        self._closing = False

        def worker():
            try:
                from core.auth import Auth, ServerMaintenanceError
                self.log(f"🌐 ACC {self.slot} → {self.server_url}")
                self.auth = Auth(
                    self.username,
                    self.password,
                    game_url=self.server_url,
                    log_fn=self.log,
                )
                token = self.auth.login()
                self._install_controllers(token)
                self.socket.connect(wait_login=True, timeout=25)
                if self.socket.is_connected() and self.socket.is_logged_in():
                    self.log("✅ Kết nối game thành công")
                else:
                    self.log("⚠️ Socket chưa xác nhận LoginSuccess")
            except ServerMaintenanceError:
                self.login_error = "SERVER_MAINTENANCE"
                self.log("🛠️ Server đang bảo trì")
            except Exception as e:
                self.login_error = str(e)
                self.log(f"❌ Login fail: {e}")

        threading.Thread(target=worker, name=f"AndroidLogin-{self.slot}", daemon=True).start()

    def disconnect(self) -> None:
        self._closing = True
        for ctrl in (self.daily, self.ai, self.birthday, self.thuyquai):
            try:
                if ctrl:
                    ctrl.stop()
            except Exception:
                pass
        try:
            if self.island:
                self.island.stop_klt()
        except Exception:
            pass
        try:
            if self.boss:
                self.boss.stop_auto()
        except Exception:
            pass
        try:
            if self.socket:
                self.socket.disconnect()
        except Exception as e:
            self.log(f"⚠️ Disconnect lỗi: {e}")
        self.log("⛔ Đã ngắt kết nối")

    def stop_automations(self) -> None:
        self.log("⏹ Dừng automation (giữ kết nối)")
        for ctrl in (self.daily, self.ai, self.birthday, self.thuyquai):
            try:
                if ctrl:
                    ctrl.stop()
            except Exception:
                pass
        try:
            if self.island:
                self.island.stop_klt()
        except Exception:
            pass
        try:
            if self.boss:
                self.boss.stop_auto()
        except Exception:
            pass

    def stop_all(self) -> None:
        self.stop_automations()
        self.disconnect()
        self._closing = False

    def _async(self, label: str, fn) -> None:
        def worker():
            try:
                self.log(f"▶ {label}")
                result = fn()
                if result is not None:
                    self.log(f"✓ {label}: {result}")
            except Exception as e:
                self.log(f"❌ {label}: {e}")
        threading.Thread(target=worker, name=f"AndroidAction-{self.slot}", daemon=True).start()

    def action(self, name: str, args: dict) -> None:
        if not self.socket or not self.socket.is_connected():
            self.log("⚠️ Chưa login / socket chưa kết nối")
            return

        name = str(name or "").strip()
        args = args if isinstance(args, dict) else {}

        if name == "boss_start":
            self._async("Boss Auto", self.boss.start_auto)
        elif name == "boss_stop":
            self._async("Dừng Boss", self.boss.stop_auto)
        elif name == "harvest_all":
            self._async("Thu hoạch tất cả", lambda: self.harvest.thu_hoach_tat_ca(delay=0.8))
        elif name == "claim_all":
            self._async("Nhận toàn bộ quà", self.gift.claim_all)
        elif name == "refresh_lai":
            self._async("Làm mới Lãi", lambda: self.lai.refresh_list(timeout=8))
        elif name == "taytuy_once":
            self._async("Tẩy Tủy 1 lượt", lambda: self.taytuy.tay_tuy_once(timeout=15))
        elif name == "vienchinh":
            self._async("Viễn Chinh", lambda: self.vienchinh.full_flow(
                namemap=str(args.get("namemap") or "TinhTheXanh1"),
                chedo=str(args.get("chedo") or "0"),
                wait_battle=int(args.get("wait_battle") or 40),
            ))
        elif name == "dautruong_auto":
            self._async("Đấu Trường Full Auto", lambda: self.dautruong.full_auto(
                skill=args.get("skill") or None,
                max_battles=int(args.get("max_battles") or 0),
            ))
        elif name == "loidai_one":
            self._async("Lôi Đài 1 lượt", self.loidai.one_fight)
        elif name == "feed_auto":
            self._async("Feed Auto", lambda: self.feed.run(self.island))
        elif name == "island_scan":
            self._async("Quét đảo", lambda: self.island.scan_all(self.island.list_islands(), probe_next=True))
        elif name == "event_checkin":
            event_name = str(args.get("event") or "EventDaiChienThuyQuai")
            self._async(f"Điểm danh {event_name}", lambda: self.event.diem_danh(event_name))
        elif name == "event_flow":
            event_name = str(args.get("event") or "EventDaiChienThuyQuai")
            self._async(f"Event Flow {event_name}", lambda: self.event.run_event_flow(event_name))
        elif name == "ai_once":
            self._async("Ải Thí Luyện 1 lượt", lambda: self.ai.flow(
                battle_seconds=int(args.get("battle_seconds") or 60),
                summon_interval=float(args.get("summon_interval") or 0.5),
                summon_team=True,
                summon_suphu=True,
                dragon_ids=None,
                dragon_delay=float(args.get("dragon_delay") or 0.3),
                gate_key=args.get("gate_key") or None,
            ))
        elif name == "thuyquai_once":
            self._async("Thủy Quái 1 lượt", lambda: self.thuyquai.flow(
                battle_seconds=int(args.get("battle_seconds") or 60),
                summon_interval=float(args.get("summon_interval") or 0.5),
                summon_team=True,
                summon_suphu=True,
                dragon_ids=None,
                dragon_delay=float(args.get("dragon_delay") or 0.3),
                result_key=args.get("result_key") or None,
                che_do=args.get("che_do") or None,
            ))
        elif name == "birthday_once":
            self._async("Sinh nhật 1 lượt", lambda: self.birthday.flow(
                battle_seconds=int(args.get("battle_seconds") or 60),
                summon_interval=float(args.get("summon_interval") or 0.5),
                summon_team=True,
                summon_suphu=True,
                dragon_ids=None,
                dragon_delay=float(args.get("dragon_delay") or 0.3),
                level_index=int(args.get("level_index") or 0),
                difficulty_index=int(args.get("difficulty_index") or 0),
            ))
        else:
            self.log(f"⚠️ Action Android chưa map: {name}")

    def status(self) -> dict:
        connected = False
        logged = False
        try:
            connected = bool(self.socket and self.socket.is_connected())
            logged = bool(self.socket and self.socket.is_logged_in())
        except Exception:
            pass
        bag = int(getattr(self.stats, "bag_count", 0) or 0) if self.stats else 0
        bag_max = int(getattr(self.stats, "bag_max", 0) or 0) if self.stats else 0
        stone = int(getattr(self.stats, "thach_anh", 0) or 0) if self.stats else 0
        return {
            "slot": self.slot,
            "username": self.username,
            "connected": connected,
            "logged": logged,
            "bag": bag,
            "bag_max": bag_max,
            "thach_anh": stone,
            "login_error": self.login_error,
        }

    def drain_logs(self, count: int = 120) -> str:
        count = max(1, min(int(count or 120), 300))
        out = []
        with self.log_lock:
            for _ in range(min(count, len(self.log_store))):
                out.append(self.log_store.popleft())
        return "\n".join(out)


def _session(slot: int) -> AndroidSession:
    slot = int(slot)
    with _SESSIONS_LOCK:
        obj = _SESSIONS.get(slot)
        if obj is None:
            obj = AndroidSession(slot, deque())
            _SESSIONS[slot] = obj
        return obj


def login(slot: int, username: str, password: str, server_url: str) -> None:
    _session(slot).login(username, password, server_url)


def disconnect(slot: int) -> None:
    _session(slot).disconnect()


def stop_automations(slot: int) -> None:
    _session(slot).stop_automations()


def stop_all(slot: int) -> None:
    _session(slot).stop_all()


def action(slot: int, name: str, args_json: str = "{}") -> None:
    try:
        args = json.loads(args_json or "{}")
    except Exception:
        args = {}
    _session(slot).action(name, args)


def status(slot: int) -> str:
    return json.dumps(_session(slot).status(), ensure_ascii=False)


def drain_logs(slot: int, count: int = 120) -> str:
    return _session(slot).drain_logs(count)
