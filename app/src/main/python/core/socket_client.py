# core/socket_client.py
import threading
import time
import socketio
import requests

from core.debug_logger import DebugLogger

DEFAULT_GAME_URL = "https://daorongsv1.shop"


class SocketClient:
    def __init__(self, game_token, log_fn=print, account_id="threshoooo", game_url=DEFAULT_GAME_URL):
        self.game_token = game_token
        self.game_url = str(game_url or DEFAULT_GAME_URL).strip().rstrip("/")
        self.log = log_fn
        self.sio = None
        self.login_success = None
        self.last_events = []
        self.lock = threading.RLock()
        # Chỉ một thread được thay đổi vòng đời socket tại một thời điểm.
        self.lifecycle_lock = threading.RLock()
        self._suppress_disconnect_callback = False
        self.stop_kick = False
        self.stop_keepalive = False
        self.account_id = account_id
        self.debug_logger = DebugLogger()
        # Chỉ một automation action (event mission / boss / combat) được
        # chạm gameplay state tại một thời điểm. Boss dùng lock này để có
        # thể được ưu tiên đúng lúc mà không chạy chồng với nhiệm vụ.
        self.automation_lock = threading.RLock()
        # Giữ tương thích với các controller combat cũ.
        self.combat_lock = threading.RLock()
        # Boss priority: khi tới khung giờ Boss, các automation khác tạm
        # đóng băng ở lớp network/gameplay và tự tiếp tục sau khi Boss xong.
        self._boss_priority = threading.Event()
        # Các thread thuộc lane Boss được phép gửi request trong lúc freeze.
        # Thread Boss chính + thread Sư Phụ 15 lần được đăng ký ở boss_socket.
        self._boss_priority_threads = set()
        self._boss_priority_lock = threading.RLock()
        # Callbacks
        self.on_login_success = None
        self.on_itemlairong = None
        self.on_laithanhcong = None
        self.on_thongbao = None
        self.on_lai_targets = None
        self.on_disconnect = None
        self.on_reconnect = None
        self.on_info = None
        self.on_raw = None
        self.on_quacongtrinh = None
        # Special game-push callbacks used by the island auto watcher.
        self.on_game_event = None
        self.on_item_fly = None
        self.on_event_item_balance_changed = None

    def _on_any(self, event, data):
        try:
            self.debug_logger.log_event(event, data)
        except Exception:
            pass

        with self.lock:
            self.last_events.append((time.time(), event, data))
            if len(self.last_events) > 300:
                self.last_events.pop(0)

        if self.on_raw:
            try:
                self.on_raw(event, data)
            except Exception:
                pass

        if event == "LoginSuccess":
            self.login_success = data
            if self.on_login_success:
                self.on_login_success(data)
        elif event == "itemlairong":
            if self.on_itemlairong:
                self.on_itemlairong(data)
        elif event == "laithanhcong":
            if self.on_laithanhcong:
                self.on_laithanhcong(data)
        elif str(event) == "4315":
            if self.on_lai_targets:
                self.on_lai_targets(data)
        elif event == "Info":
            if self.on_info:
                self.on_info(data)
        elif event == "quacongtrinh":
            if self.on_quacongtrinh:
                self.on_quacongtrinh(data)
        elif event == "Event":
            # Global event stream: includes KeLangThang/ConLan spawns and timers.
            if self.on_game_event:
                try:
                    self.on_game_event(data)
                except Exception:
                    pass
        elif event == "ItemFly":
            # Client/server push indicating a spawned item is flying to the player.
            if self.on_item_fly:
                try:
                    self.on_item_fly(data)
                except Exception:
                    pass
        elif event == "EventItemBalanceChanged":
            if self.on_event_item_balance_changed:
                try:
                    self.on_event_item_balance_changed(data)
                except Exception:
                    pass
        elif event in ("Thongbao", "ThongBao", "thongbao"):
            if self.on_thongbao:
                self.on_thongbao(data)

    def _send_login(self):
        try:
            self.sio.emit("Login", {
                "accountId": self.account_id,
                "resourceDownloadPolicyVersion": "1",
                "luaCombat": {
                    "apiVersion": "1",
                    "revision": "foundation-1",
                    "scriptHashes": {},
                    "capabilities": {},
                }
            })
            time.sleep(0.5)
            self.sio.emit("Info", {})
        except Exception as e:
            self.log(f"[socket] login emit err: {e}")

    def connect(self, wait_login=True, timeout=30):
        # AccountPanel đã có reconnect watchdog riêng. Tắt reconnect nội bộ
        # của python-socketio để tránh hai vòng reconnect chạy đồng thời.
        with self.lifecycle_lock:
            connect_url = f"{self.game_url}/?token={self.game_token}"

            # Hủy socket cũ trước khi thay bằng socket mới. Đây là handoff
            # có chủ đích nên không phát sinh thêm reconnect watchdog.
            old_sio = self.sio
            if old_sio is not None:
                self._suppress_disconnect_callback = True
                try:
                    old_sio.disconnect()
                except Exception:
                    pass
                finally:
                    self._suppress_disconnect_callback = False

            self.login_success = None
            self.stop_kick = False
            self.stop_keepalive = True

            # Session HTTP trực tiếp.
            http_session = requests.Session()
            http_session.verify = False

            self.sio = socketio.Client(
                logger=False, engineio_logger=False,
                ssl_verify=False,
                # Chỉ AccountPanel._reconnect_worker được phép reconnect.
                reconnection=False,
                request_timeout=30,
                http_session=http_session,
            )

            @self.sio.event
            def connect():
                self.log("[socket] ✅ Đã kết nối")

                def kick():
                    self._send_login()
                    for rnd in range(6):
                        if self.login_success is not None or self.stop_kick:
                            return
                        time.sleep(0.4 if rnd == 0 else 0.9)
                        if self.login_success is not None or self.stop_kick:
                            return
                        for ev in ("Info", "info", "login", "Login", "EnterGame"):
                            if self.login_success is not None or self.stop_kick:
                                return
                            try:
                                self.sio.emit(ev, {})
                            except Exception:
                                pass

                threading.Thread(target=kick, daemon=True).start()
                self._start_keepalive()

            @self.sio.event
            def disconnect():
                self.log("[socket] ❌ Mất kết nối")
                if self._suppress_disconnect_callback:
                    return
                if self.on_disconnect:
                    self.on_disconnect()

            # Giữ callback tương thích với code cũ/server cũ. Với
            # reconnection=False, python-socketio sẽ không tự gọi nhánh này.
            @self.sio.event
            def reconnect():
                self.log("[socket] 🔄 Đã reconnect")
                self.login_success = None
                self.stop_kick = False
                def _relogin():
                    time.sleep(0.5)
                    self._send_login()
                threading.Thread(target=_relogin, daemon=True).start()
                if self.on_reconnect:
                    self.on_reconnect()

            self.sio.on("*", self._on_any)
            self.log("[*] Kết nối Socket...")
            self.sio.connect(
                connect_url,
                socketio_path="socket.io",
                transports=["websocket"],
                wait_timeout=25,
            )

            if wait_login:
                for _ in range(timeout * 2):
                    if self.login_success is not None:
                        break
                    time.sleep(0.5)

            self.stop_kick = True
            time.sleep(1.0)
            return self.sio

    def _start_keepalive(self):
        self.stop_keepalive = False
        def _keepalive():
            while not self.stop_keepalive:
                time.sleep(25)
                if self.stop_keepalive:
                    return
                try:
                    if self.sio and self.sio.connected:
                        self.sio.emit("Info", {})
                except Exception:
                    pass
        threading.Thread(target=_keepalive, daemon=True).start()

    def set_boss_priority(self, active: bool) -> None:
        """Khóa gameplay của mọi automation khác trong lúc Boss đang chạy.

        Lane Boss được phép đi xuyên qua lock; mọi request/emit/call từ các
        automation khác sẽ chờ cho tới khi Boss xong rồi tự chạy tiếp.
        """
        with self._boss_priority_lock:
            if active:
                self._boss_priority_threads = {threading.get_ident()}
                self._boss_priority.set()
            else:
                self._boss_priority_threads.clear()
                self._boss_priority.clear()

    def add_boss_priority_thread(self, active: bool = True) -> None:
        """Đăng ký thread phụ của lane Boss (vd. Sư Phụ 15 lần)."""
        tid = threading.get_ident()
        with self._boss_priority_lock:
            if active:
                self._boss_priority_threads.add(tid)
            else:
                self._boss_priority_threads.discard(tid)

    def _wait_boss_priority(self) -> None:
        """Chặn request gameplay của automation khác khi Boss đang ưu tiên."""
        while self._boss_priority.is_set():
            with self._boss_priority_lock:
                if threading.get_ident() in self._boss_priority_threads:
                    return
            self._boss_priority.wait(0.25)

    def emit(self, event, data=None):
        self._wait_boss_priority()
        if self.sio and self.sio.connected:
            if data is None:
                self.sio.emit(event)
            else:
                self.sio.emit(event, data)
        else:
            self.log(f"⚠️  emit {event} thất bại — socket không connected")

    def request(self, event, data=None, timeout=8):
        """Emit a game request and return the Socket.IO callback payload.

        The game protocol used by SendRequest/SendRequest2 returns the business
        response as the Socket.IO ACK callback, so this must not be confused
        with the generic raw-event stream.
        """
        self._wait_boss_priority()
        if not self.sio or not self.sio.connected:
            self.log(f"⚠️ request {event} — socket không connected")
            return None
        result = [None]
        done = threading.Event()

        def _cb(response):
            result[0] = response
            # Keep the callback response visible to controllers too.
            with self.lock:
                self.last_events.append((time.time(), f"__ack__:{event}", response))
                if len(self.last_events) > 300:
                    self.last_events.pop(0)
            done.set()

        try:
            if data is None:
                self.sio.emit(event, callback=_cb)
            else:
                self.sio.emit(event, data, callback=_cb)
        except Exception as e:
            self.log(f"request err: {e}")
            return None
        done.wait(timeout=timeout)
        return result[0]

    def call(self, event, data=None, timeout=8):
        self._wait_boss_priority()
        if not self.sio or not self.sio.connected:
            self.log(f"⚠️  call {event} — socket không connected")
            return None

        result = [None]
        done = threading.Event()

        def _cb(response):
            result[0] = response
            done.set()

        try:
            if data is None:
                self.sio.emit(event, callback=_cb)
            else:
                self.sio.emit(event, data, callback=_cb)
        except Exception as e:
            self.log(f"call err: {e}")
            return None

        if done.wait(timeout=timeout):
            return result[0]
        return None

    def set_token(self, game_token):
        self.game_token = game_token
        self.login_success = None

    def is_connected(self):
        return self.sio is not None and self.sio.connected

    def is_logged_in(self):
        return self.login_success is not None

    def disconnect(self):
        # Explicit/manual shutdown must also be serialized with connect().
        with self.lifecycle_lock:
            self.stop_keepalive = True
            self.stop_kick = True
            sio = self.sio
            if sio is None:
                return

            self._suppress_disconnect_callback = True
            try:
                if sio.connected:
                    sio.disconnect()
            except Exception:
                pass
            finally:
                self._suppress_disconnect_callback = False
                self.sio = None
