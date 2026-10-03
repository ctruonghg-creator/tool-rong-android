# core/island.py
"""Đảo động — tự nhận đảo mới từ login / PreloadDao thành công."""
import time
import os
import json
import threading

DV_FILE = "data_version.txt"
# 5 đảo mặc định: id 0,1,2,3,4
DEFAULT_ISLANDS = ["0", "1", "2", "3", "4"]


class IslandController:
    def __init__(self, socket_client, stats, log_fn=print):
        self.sc = socket_client
        self.stats = stats
        self.log = log_fn
        self.current_island = "0"
        self.account_id = str(getattr(socket_client, "account_id", "unknown") or "unknown")
        safe_account = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in self.account_id) or "unknown"
        self.dv_file = os.path.join(os.path.dirname(__file__), f"data_version_{safe_account}.json")
        self.data_versions = {}
        self.islands_cache = {}
        self._loaded_islands = set()
        # luôn bắt đầu với 0..4 (5 đảo); dò thêm nếu có đảo mới
        self.known_islands = set(DEFAULT_ISLANDS)
        self.on_islands_changed = None

        # Kẻ Lang Thang auto: the server broadcasts its island/id globally in
        # the Event stream. Each target gets its own short-lived tap worker.
        self.klt_lock = threading.RLock()
        self.klt_stop = threading.Event()
        self.klt_targets = {}
        self.klt_threads = {}
        self._item_fly_seq = 0
        self._last_item_fly = None
        self._klt_tap_times = {}
        self._picked_item_times = {}
        self.klt_pick_lock = threading.RLock()

        # Global event-item picker. The `dao` field in NhatItemRoi addresses
        # the island directly, so we can prioritize event drops across all
        # known islands without changing the visible island.
        self.event_item_lock = threading.RLock()
        self.event_item_stop = threading.Event()
        self.event_item_pending = {}
        self.event_item_confirmed = {}
        self.event_item_worker = None
        self._event_item_balance_seq = {}
        self._event_item_waiting_key = None
        self._item_event_scan_lock = threading.RLock()
        self._item_event_scan_thread = None
        self._item_event_scan_stop = threading.Event()
        self._event_item_seen_signals = set()
        self._event_item_signal_order = []
        self._event_item_signal_limit = 256
        self._island_io_lock = threading.RLock()
        self._last_datadao_by_island = {}
        self._last_item_scan = 0.0
        self._full_item_scan_interval = 20.0
        self._quiet_load_targets = set()
        self._event_item_priority = {
            "AoGiap": 0,
            "MuGiap": 1,
            "VuKhi": 2,
            "SocCon": 3,
            "QuaThong": 4,
        }
        self.event_item_enabled = True
        # Không tự nhặt item ngay trong giai đoạn LoginSuccess/PreloadDao đầu tiên.
        # VN Auto sẽ mở khóa sau khi xử lý xong quà + điểm danh + flow sự kiện.
        self.event_item_ready = False
        self.klt_enabled = True
        self._load_from_file()
        if self.sc:
            self.sc.on_info = self._on_info
            # These callbacks are separate from the generic on_raw hook, so
            # AccountPanel can still fan raw events out to all UI tabs safely.
            self.sc.on_game_event = self._on_game_event
            self.sc.on_item_fly = self._on_item_fly
            self.sc.on_event_item_balance_changed = self._on_event_item_balance_changed


    # ============ KẺ LANG THANG ============
    def set_event_item_ready(self, ready=True):
        """Mở/khóa toàn bộ pipeline nhặt item event.

        Khi OFF: vẫn nhận snapshot vào cache nhưng không gửi NhatItemRoi.
        Khi ON: khởi động đúng một watcher tuần tự quét các đảo.
        """
        ready = bool(ready)
        with self.event_item_lock:
            changed = self.event_item_ready != ready
            self.event_item_ready = ready
            enabled = self.event_item_enabled
            if not ready:
                self._item_event_scan_stop.set()
                self.event_item_stop.set()
                worker = self.event_item_worker
            else:
                self._item_event_scan_stop.clear()
                self.event_item_stop.clear()
                worker = self.event_item_worker

        if ready and enabled:
            if not worker or not worker.is_alive():
                with self.event_item_lock:
                    if self.event_item_pending and (self.event_item_worker is None or not self.event_item_worker.is_alive()):
                        self.event_item_worker = threading.Thread(
                            target=self._event_item_worker_loop,
                            name="EventItemPicker", daemon=True
                        )
                        self.event_item_worker.start()
            self._start_item_event_watcher()
        else:
            self._stop_item_event_watcher()
            if changed:
                self.log("🧺 Auto nhặt item event: CHỜ sau day-cycle")
                return
        if changed:
            self.log(f"🧺 Auto nhặt item event: {'ON' if ready else 'OFF'}")

    def set_klt_enabled(self, enabled=True):
        with self.klt_lock:
            self.klt_enabled = bool(enabled)
            if not self.klt_enabled:
                self.klt_targets.clear()
        self.log(f"🧭 Auto Kẻ Lang Thang: {'ON' if self.klt_enabled else 'OFF'}")

    # Tên item rơi đã quan sát trực tiếp trong capture EventDaiChienThuyQuai.
    # nameitemm là tên logic mà NhatItemRoi yêu cầu; nameitem là id động có hậu tố UUID.
    _DROPPED_ITEM_NAME_MAP = {
        "soccon": "SocCon",
        "mugiap": "MuGiap",
        "aogiap": "AoGiap",
        "vukhi": "VuKhi",
        "quathong": "QuaThong",
    }

    @classmethod
    def _nameitemm_from_itemdao(cls, itemdao):
        raw = str(itemdao or "").strip()
        prefix = raw.split("-", 1)[0].strip()
        if not prefix:
            return ""
        mapped = cls._DROPPED_ITEM_NAME_MAP.get(prefix.lower())
        if mapped:
            return mapped
        # Fallback conservative: giữ phần tên trước dấu '-' và đổi ký tự đầu.
        # Không tự chế thêm hậu tố/ID vào nameitemm.
        return prefix[:1].upper() + prefix[1:]

    @classmethod
    def _event_item_info(cls, itemdao, explicit_name=None):
        raw = str(itemdao or "").strip()
        if not raw or "-" not in raw:
            return None
        nameitemm = ""
        explicit = str(explicit_name or "").strip()
        if explicit:
            for k, mapped in cls._DROPPED_ITEM_NAME_MAP.items():
                if explicit.lower() in (k, mapped.lower()):
                    nameitemm = mapped
                    break
            if not nameitemm:
                # itemEvent may provide a logical event item name not present
                # in the small set observed in the AoGiap capture. Keep it.
                nameitemm = explicit
        if not nameitemm:
            nameitemm = cls._nameitemm_from_itemdao(raw)
        if not nameitemm:
            return None
        return raw, nameitemm

    @staticmethod
    def _walk_item_event(value):
        yield value
        if isinstance(value, dict):
            for k, v in value.items():
                yield k
                yield from IslandController._walk_item_event(v)
        elif isinstance(value, list):
            for v in value:
                yield from IslandController._walk_item_event(v)

    def _queue_item_event_snapshot(self, data, dao_hint=None):
        """Discover dropped event-item IDs from an island snapshot.

        The only concrete pickup request we have captured requires a dynamic
        id such as ``aogiap-<uuid>``.  The server has exposed an ``itemEvent``
        field in island snapshots, but an actual non-empty pre-pick snapshot
        has not yet been captured.  Therefore we use two evidence-driven
        layers:

        1) parse ``itemEvent`` recursively when present;
        2) as a fallback, scan the *whole island snapshot* for a supported
           dynamic event-item id.  This catches builds where the same object is
           moved/renamed outside ``itemEvent`` while still refusing plain
           logical names such as ``AoGiap``.
        """
        if not self.event_item_enabled or not isinstance(data, dict):
            return

        dao_default = str(dao_hint if dao_hint is not None else data.get("dao") or "").strip()
        if not dao_default.isdigit():
            dao_default = ""

        def normalize_name(value):
            raw = str(value or "").strip()
            if not raw:
                return ""
            for k, mapped in self._DROPPED_ITEM_NAME_MAP.items():
                if raw.lower() in (k, mapped.lower()):
                    return mapped
            return raw

        def is_dynamic_id(value):
            if not isinstance(value, str):
                return False
            raw = value.strip()
            if "-" not in raw or len(raw) < 8:
                return False
            prefix = raw.split("-", 1)[0].strip().lower()
            return prefix in self._DROPPED_ITEM_NAME_MAP

        candidates = {}

        def add_candidate(raw_id, dao, logical=""):
            item_id = str(raw_id or "").strip()
            d = str(dao or "").strip()
            if not is_dynamic_id(item_id) or not d.isdigit():
                return
            info = self._event_item_info(item_id, normalize_name(logical))
            if not info:
                return
            candidates[(d, item_id)] = info[1]

        def walk(value, local_dao="", inherited_logical="", key_hint=""):
            if isinstance(value, dict):
                dao = str(
                    value.get("dao")
                    or value.get("island")
                    or value.get("islandIndex")
                    or local_dao
                    or dao_default
                ).strip()
                own_logical = normalize_name(
                    value.get("nameitemm")
                    or value.get("nameitem")
                    or value.get("itemKey")
                    or value.get("displayName")
                    or value.get("name")
                    or value.get("key")
                    or key_hint
                ) or inherited_logical

                for fld in ("itemdao", "id", "itemId", "itemid", "uid", "uuid", "nameitemId", "iditem"):
                    val = value.get(fld)
                    if is_dynamic_id(val):
                        add_candidate(val, dao, own_logical)

                for k, v in value.items():
                    key = str(k)
                    if is_dynamic_id(key):
                        add_candidate(key, dao, own_logical)
                    walk(v, dao, own_logical, key)
            elif isinstance(value, list):
                for item in value:
                    walk(item, local_dao, inherited_logical, key_hint)
            elif isinstance(value, str) and is_dynamic_id(value):
                add_candidate(value, local_dao or dao_default, inherited_logical or key_hint)

        # First parse the observed itemEvent block, then scan the full snapshot.
        item_event = data.get("itemEvent")
        if isinstance(item_event, str):
            try:
                item_event = json.loads(item_event)
            except Exception:
                item_event = None
        if item_event not in (None, {}, [], ""):
            walk(item_event, dao_default, "", "itemEvent")

        # Fallback for accounts/builds where itemEvent itself stays empty or
        # the object is placed in another island-snapshot field.
        walk(data, dao_default, "", "")

        if not candidates:
            if item_event not in (None, {}, [], ""):
                try:
                    preview = repr(item_event)[:600]
                except Exception:
                    preview = "<unrepr>"
                self.log(f"🔎 itemEvent có dữ liệu nhưng chưa lấy được ID động: dao={dao_default} shape={preview}")
            return

        for (dao, itemdao), nameitemm in candidates.items():
            sig = (dao, itemdao, nameitemm)
            with self.event_item_lock:
                if sig in self._event_item_seen_signals:
                    continue
                self._event_item_seen_signals.add(sig)
                self._event_item_signal_order.append(sig)
                if len(self._event_item_signal_order) > self._event_item_signal_limit:
                    stale = self._event_item_signal_order.pop(0)
                    self._event_item_seen_signals.discard(stale)
            self.log(f"🎯 ITEM DROP DETECTED: {nameitemm} dao={dao} id={itemdao}")
            self._auto_pick_dropped_item({"itemdao": itemdao, "dao": dao, "nameitemm": nameitemm})

    def _start_item_event_watcher(self):
        with self._item_event_scan_lock:
            t = self._item_event_scan_thread
            if t and t.is_alive():
                return
            self._item_event_scan_stop.clear()
            self._item_event_scan_thread = threading.Thread(
                target=self._item_event_watcher_loop,
                name="EventItemWatcher", daemon=True
            )
            self._item_event_scan_thread.start()

    def _stop_item_event_watcher(self):
        self._item_event_scan_stop.set()
        with self._item_event_scan_lock:
            self._item_event_scan_thread = None

    def _scan_item_event_islands_once(self, full=False):
        """Refresh island snapshots for event-drop discovery.

        Full scans are serialized with island I/O and skipped during active
        combat, so the watcher cannot fight Viễn Chinh/Boss for PreloadDao.
        """
        if not self.event_item_enabled or not self.event_item_ready or not self.sc.is_connected():
            return

        with self.event_item_lock:
            worker = self.event_item_worker
            pending = bool(self.event_item_pending)
        if pending and worker and worker.is_alive():
            return

        # Item watcher phải nhường toàn bộ automation lane đang chạy
        # (mission/Boss/Lôi Đài/DTTT). Không được tự chặn hay chen request.
        automation_lock = getattr(self.sc, "automation_lock", None)
        if automation_lock is not None:
            automation_acquired = automation_lock.acquire(blocking=False)
            if not automation_acquired:
                return
        else:
            automation_acquired = False

        combat_lock = getattr(self.sc, "combat_lock", None)
        if combat_lock is not None:
            acquired = combat_lock.acquire(blocking=False)
            if not acquired:
                if automation_lock is not None and automation_acquired:
                    automation_lock.release()
                return
        else:
            acquired = False

        try:
            with self._island_io_lock:
                original = str(self.current_island)
                islands = self.list_islands() if full else [original if original.isdigit() else "0"]
                self.log(f"🔎 Item scan {'FULL' if full else 'hiện tại'}: đảo={islands}")
                for dao in islands:
                    if self._item_event_scan_stop.is_set() or not self.event_item_ready:
                        break
                    ok = self.load_dao(dao, timeout=8, quiet=True)
                    if not ok:
                        self.log(f"🔎 Item scan: đảo {dao} chưa tải được snapshot")
                        continue
                    snapshot = self._last_datadao_by_island.get(str(dao))
                    item_block = snapshot.get("itemEvent") if isinstance(snapshot, dict) else None
                    if item_block not in (None, {}, [], ""):
                        self.log(f"🔎 Item scan: đảo {dao} snapshot có itemEvent")
                    time.sleep(0.35)

                if original.isdigit() and original in self.known_islands and original != str(self.current_island):
                    self.load_dao(original, timeout=5, quiet=True)
                self._last_item_scan = time.monotonic()
        finally:
            if combat_lock is not None and acquired:
                try:
                    combat_lock.release()
                except Exception:
                    pass
            if automation_lock is not None and automation_acquired:
                try:
                    automation_lock.release()
                except Exception:
                    pass

    def _item_event_watcher_loop(self):
        # Current-island refresh is immediate. A full-island refresh follows
        # periodically so a drop on another island is still discovered.
        last_full = 0.0
        while not self._item_event_scan_stop.is_set():
            try:
                now = time.monotonic()
                full = (now - last_full) >= self._full_item_scan_interval
                self._scan_item_event_islands_once(full=full)
                if full:
                    last_full = time.monotonic()
            except Exception as e:
                self.log(f"🔎 Item watcher lỗi: {e}")
            if self._item_event_scan_stop.wait(5.0):
                break

    def _observe_item_event_confirmation_recursive(self, data):
        """Observe Event.itemdao as confirmation, or as a pre-pick trigger when new."""
        if not self.event_item_enabled:
            return
        if isinstance(data, dict):
            itemdao = str(data.get("itemdao") or "").strip()
            dao = str(data.get("dao") or "").strip()
            if itemdao and dao.isdigit():
                key = (dao, itemdao)
                with self.event_item_lock:
                    pending = key in self.event_item_pending
                    confirmed = key in self.event_item_confirmed
                if pending:
                    self._confirm_item_from_event(itemdao, dao)
                elif not confirmed:
                    # Capture thủ công cho thấy Event.itemdao xuất hiện SAU
                    # NhatItemRoi; tuyệt đối không coi đây là trigger nhặt mới.
                    pass
            if "itemEvent" in data:
                self._queue_item_event_snapshot(data, data.get("dao"))
            for value in data.values():
                if isinstance(value, (dict, list)):
                    self._observe_item_event_confirmation_recursive(value)
        elif isinstance(data, list):
            for value in data:
                if isinstance(value, (dict, list)):
                    self._observe_item_event_confirmation_recursive(value)

    def _confirm_item_from_event(self, itemdao, dao):
        """Complete exactly the matching pending item when Event.itemdao arrives."""
        if not itemdao or not str(dao).isdigit():
            return False
        key = (str(dao), str(itemdao))
        with self.event_item_lock:
            exists = key in self.event_item_pending
        if exists:
            self._event_item_complete(key, f"dao={dao} Event.itemdao={itemdao}")
            return True
        return False

    def _auto_pick_dropped_item(self, data):
        """Queue every supported event drop globally; AoGiap has highest priority."""
        if not self.event_item_enabled or not isinstance(data, dict):
            return
        itemdao = data.get("itemdao")
        dao = data.get("dao")
        if itemdao is None or dao is None:
            return
        info = self._event_item_info(itemdao)
        if info is None:
            return
        itemdao, nameitemm_from_id = info
        dao = str(dao).strip()
        explicit_name = str(data.get("nameitemm") or "").strip()
        nameitemm = explicit_name if explicit_name in self._DROPPED_ITEM_NAME_MAP.values() else nameitemm_from_id
        if not dao.isdigit():
            return

        key = (dao, itemdao)
        with self.event_item_lock:
            if key in self.event_item_confirmed:
                return
            if key not in self.event_item_pending:
                self.event_item_pending[key] = {
                    "dao": dao,
                    "itemdao": itemdao,
                    "nameitemm": nameitemm,
                    "priority": self._event_item_priority.get(nameitemm, 99),
                    "seq": len(self.event_item_pending) + 1,
                    "attempts": 0,
                    "last_send": 0.0,
                }
                self.log(f"🎯 Ưu tiên nhặt item event: {nameitemm} trên đảo {dao}")

            worker = self.event_item_worker
            if self.event_item_ready and (worker is None or not worker.is_alive()):
                self.event_item_stop.clear()
                self.event_item_worker = threading.Thread(
                    target=self._event_item_worker_loop,
                    name="EventItemPicker", daemon=True
                )
                self.event_item_worker.start()

    def _on_event_item_balance_changed(self, data):
        """Record the inventory-update confirmation produced by NhatItemRoi.

        The manual capture shows this push immediately after NhatItemRoi. It is
        therefore a success signal for the current item request, not a reason to
        keep hammering the server with duplicate requests. The matching
        Event.itemdao confirmation is still preferred when it arrives.
        """
        if not isinstance(data, dict):
            return
        item_key = str(data.get("itemKey") or "").strip()
        if item_key not in self._DROPPED_ITEM_NAME_MAP.values():
            return
        with self.event_item_lock:
            self._event_item_balance_seq[item_key] = int(
                self._event_item_balance_seq.get(item_key, 0)
            ) + 1
            waiting = self._event_item_waiting_key
            current = self.event_item_pending.get(waiting) if waiting is not None else None
            balance_total = data.get("total")
        if current and current.get("nameitemm") == item_key:
            waiting_key = waiting
            self.log(
                f"📦 {item_key}: server đã cập nhật kho (tổng={balance_total}) "
                f"— NHẶT THÀNH CÔNG"
            )
            if waiting_key is not None:
                self._event_item_complete(waiting_key, f"dao={waiting_key[0]} {item_key} EventItemBalanceChanged")
        else:
            self.log(f"📦 {item_key}: EventItemBalanceChanged (tổng={balance_total})")

    @staticmethod
    def _ack_detail(ack):
        """Trả về (status, error_code, message) từ ACK SendRequest2."""
        obj = ack
        if isinstance(obj, list):
            obj = obj[0] if obj else None
        if isinstance(obj, str):
            try:
                obj = json.loads(obj)
            except Exception:
                # ACK thô đôi khi chỉ là chuỗi mã, giữ lại để kiểm tra marker.
                return obj.strip().lower(), "", obj.strip().lower()
        if isinstance(obj, (int, float)):
            return str(obj).strip().lower(), "", str(obj).strip().lower()
        if isinstance(obj, list):
            obj = obj[0] if obj else None
        if not isinstance(obj, dict):
            return "", "", ""
        return (
            str(obj.get("status", "")).strip().lower(),
            str(obj.get("errorCode", obj.get("code", ""))).strip().lower(),
            str(obj.get("message", "")).strip().lower(),
        )

    @staticmethod
    def _ack_means_item_gone(status, error_code, message):
        """Các ACK rõ ràng cho biết item không còn để nhặt nữa."""
        haystack = " ".join(x for x in (status, error_code, message) if x)
        # ACK=3 trong log/capture của build hiện tại được trả khi item đã bị
        # nhặt (ví dụ: "đã được người khác nhặt!"). Đây là tín hiệu item đã
        # không còn khả dụng; không tiếp tục spam NhatItemRoi.
        if status == "3" or error_code == "3":
            return True
        gone_markers = (
            "item_not_found", "item_not_exist", "item_not_exists",
            "item_gone", "not_found", "not_exist", "already_picked",
            "already_collected", "item_collected", "item_picked",
            "khong tim thay", "không tìm thấy", "không còn",
            "da nhat", "đã nhặt", "đã được nhặt", "da duoc nhat",
            "item không tồn tại", "item khong ton tai",
        )
        return any(marker in haystack for marker in gone_markers)

    def _event_item_complete(self, key, reason):
        with self.event_item_lock:
            self.event_item_confirmed[key] = time.monotonic()
            self.event_item_pending.pop(key, None)
            if self._event_item_waiting_key == key:
                self._event_item_waiting_key = None
        self.log(f"✅ Item event đã biến mất/đã nhặt xong: {reason}")

    def _event_item_switch_island(self, dao):
        """NhatItemRoi dùng chudao="this" nên phải ở đúng đảo trước khi giữ item."""
        dao = str(dao).strip()
        if not dao.isdigit():
            return False
        if str(self.current_island) == dao and dao in self._loaded_islands:
            return True
        self.log(f"🛶 Item event: chuyển sang đảo {dao} để nhặt")
        try:
            return bool(self.load_dao(dao, timeout=8))
        except Exception as e:
            self.log(f"🛶 Item event: không load được đảo {dao}: {e}")
            return False

    def _event_item_worker_loop(self):
        """Replay the manually observed NhatItemRoi flow, without packet spam.

        Item picker is a low-priority worker: it never interrupts an active
        mission/Boss/Lôi Đài/DTTT action. It only acquires automation_lock when
        it is about to switch island and send the pickup request.
        """
        try:
            while self.event_item_enabled and self.event_item_ready and not self.event_item_stop.is_set():
                with self.event_item_lock:
                    if not self.event_item_pending:
                        break
                    key, item = min(
                        self.event_item_pending.items(),
                        key=lambda kv: (
                            int(kv[1].get("priority", 99)),
                            int(kv[1].get("seq", 0)),
                        ),
                    )
                    dao = item["dao"]
                    itemdao = item["itemdao"]
                    nameitemm = item["nameitemm"]
                    attempts = int(item.get("attempts", 0))
                    balance_before = int(self._event_item_balance_seq.get(nameitemm, 0))

                if self.event_item_stop.is_set():
                    break

                # Chỉ lấy ownership của automation lane khi chuẩn bị đổi đảo + nhặt.
                # Nếu lane đang bận, chờ vòng sau; không interrupt action hiện tại.
                automation_lock = getattr(self.sc, "automation_lock", None)
                got_automation = False
                if automation_lock is not None:
                    got_automation = automation_lock.acquire(blocking=False)
                    if not got_automation:
                        self.event_item_stop.wait(0.8)
                        continue

                try:
                    if not self._event_item_switch_island(dao):
                        with self.event_item_lock:
                            cur = self.event_item_pending.get(key)
                            if cur is not None:
                                cur["attempts"] = int(cur.get("attempts", 0)) + 1
                        self.event_item_stop.wait(1.0)
                        continue

                    payload = {
                        "class": "DragonIsland",
                        "method": "NhatItemRoi",
                        "data": {
                            "nameitem": itemdao,
                            "dao": dao,
                            "chudao": "this",
                            "trangthai": "nhat",
                            "nameitemm": nameitemm,
                        },
                    }

                    with self.event_item_lock:
                        self._event_item_waiting_key = key
                        cur = self.event_item_pending.get(key)
                        if cur is not None:
                            cur["last_send"] = time.monotonic()
                            cur["attempts"] = int(cur.get("attempts", 0)) + 1
                            attempts = cur["attempts"]

                    try:
                        ack = self.sc.request("SendRequest2", payload, timeout=4)
                        status, error_code, message = self._ack_detail(ack)

                        if self._ack_means_item_gone(status, error_code, message):
                            self._event_item_complete(
                                key,
                                f"dao={dao} {nameitemm} ACK={error_code or status or message}",
                            )
                            continue

                        if status in ("0", "ok", "success"):
                            self.log(
                                f"🤏 NhatItemRoi {nameitemm} dao={dao} ({itemdao}) "
                                f"— request lần {attempts}, chờ server xác nhận"
                            )

                            deadline = time.monotonic() + 4.0
                            confirmed_by_balance = False
                            while time.monotonic() < deadline and not self.event_item_stop.is_set():
                                with self.event_item_lock:
                                    if key not in self.event_item_pending:
                                        confirmed_by_balance = True
                                        break
                                    current_seq = int(self._event_item_balance_seq.get(nameitemm, 0))
                                if current_seq > balance_before:
                                    confirmed_by_balance = True
                                    break
                                time.sleep(0.08)

                            with self.event_item_lock:
                                still_pending = key in self.event_item_pending

                            if not still_pending:
                                continue

                            if confirmed_by_balance:
                                self._event_item_complete(
                                    key,
                                    f"dao={dao} {nameitemm} EventItemBalanceChanged",
                                )
                                continue

                            if attempts <= 3 or attempts % 3 == 0:
                                self.log(
                                    f"⏳ {nameitemm} dao={dao}: chưa thấy xác nhận sau 4s — "
                                    f"sẽ thử lại (lần {attempts + 1})"
                                )
                            self.event_item_stop.wait(0.8)
                            continue

                        if not status:
                            self.log(
                                f"⏳ {nameitemm} dao={dao}: ACK chưa có trạng thái — "
                                f"chờ xác nhận server"
                            )
                            self.event_item_stop.wait(0.8)
                            continue

                        if attempts <= 3 or attempts % 3 == 0:
                            self.log(
                                f"🖐️ {nameitemm} dao={dao}: ACK={status} "
                                f"({error_code or message or 'chưa rõ'}) — thử lại chậm"
                            )
                        self.event_item_stop.wait(0.8)
                    except Exception as e:
                        self.log(
                            f"🤏 NhatItemRoi dao={dao} lỗi lần {attempts}: {e} — thử lại sau 1s"
                        )
                        self.event_item_stop.wait(1.0)
                    finally:
                        with self.event_item_lock:
                            if self._event_item_waiting_key == key:
                                self._event_item_waiting_key = None
                finally:
                    if automation_lock is not None and got_automation:
                        try:
                            automation_lock.release()
                        except Exception:
                            pass
        finally:
            with self.event_item_lock:
                self.event_item_worker = None
                has_more = bool(self.event_item_pending)
            if has_more and self.event_item_enabled and self.event_item_ready and not self.event_item_stop.is_set():
                self.event_item_worker = threading.Thread(
                    target=self._event_item_worker_loop,
                    name="EventItemPicker-Restart", daemon=True
                )
                self.event_item_worker.start()

    @staticmethod
    def _klt_targets_from_event(data):
        if not isinstance(data, dict):
            return {}
        block = data.get("KeLangThang")
        if not isinstance(block, dict):
            return {}
        conlan = block.get("ConLan")
        if not isinstance(conlan, dict):
            return {}
        out = {}
        for key, obj in conlan.items():
            if not isinstance(obj, dict):
                continue
            rid = str(obj.get("id", key) or "").strip()
            dao = str(obj.get("dao", "") or "").strip()
            if not rid or not dao.isdigit():
                continue
            try:
                solandap = max(0, int(obj.get("solandap", 0) or 0))
            except Exception:
                solandap = 0
            try:
                maxlandap = max(solandap + 1, int(obj.get("maxlandap", 1) or 1))
            except Exception:
                maxlandap = solandap + 1
            out[(dao, rid)] = {
                "dao": dao,
                "id": rid,
                "solandap": solandap,
                "maxlandap": maxlandap,
            }
        return out

    def hit_klt_once(self, timeout: float = 6.0) -> bool:
        """Đập 1 lần Kẻ Lang Thang cho nhiệm vụ event. Dùng target đã server broadcast."""
        deadline = time.monotonic() + max(0.5, float(timeout))
        while time.monotonic() < deadline:
            with self.klt_lock:
                targets = [(k, dict(v)) for k, v in self.klt_targets.items()]
            if targets:
                key, st = min(targets, key=lambda kv: (int(kv[1].get("solandap", 0)), str(kv[0])))
                dao, rid = str(st.get("dao")), str(st.get("id"))
                if not dao.isdigit() or not rid:
                    return False
                try:
                    self.sc.emit("DapKeLangThang", {
                        "dao": dao, "id": rid, "nameobject": "ConLan",
                    })
                    self.log(f"⚔️ Event quest Kẻ Lang Thang: DapKeLangThang dao={dao} id={rid}")
                    return True
                except Exception as e:
                    self.log(f"Kẻ Lang Thang emit lỗi: {e}")
                    return False
            if self.klt_stop.wait(0.25):
                return False
        return False

    def _on_game_event(self, data):
        if not isinstance(data, dict):
            return

        # Item/event pickup is independent of KLT tapping. This lets the bot
        # drain event drops from every dao even when KLT itself is off.
        # Event.itemdao is POST-pick confirmation in the user's capture;
        # still scan the whole Event payload for a dynamic drop id, because
        # some builds can expose the object outside a named itemEvent field.
        self._observe_item_event_confirmation_recursive(data)
        if isinstance(data, dict):
            self._queue_item_event_snapshot(data, data.get("dao"))

        if not self.klt_enabled:
            return

        # ItemFly is itself nested inside the generic Event push in the captures.
        item_fly = data.get("ItemFly")
        if item_fly is not None:
            self._on_item_fly(item_fly)

        if "KeLangThang" not in data:
            return

        block = data.get("KeLangThang")
        if not isinstance(block, dict) or "ConLan" not in block:
            return
        conlan = block.get("ConLan")

        # A real ConLan={} update means the current target list disappeared.
        if isinstance(conlan, dict) and not conlan:
            with self.klt_lock:
                self.klt_targets.clear()
            return

        current = self._klt_targets_from_event(data)
        if not current:
            return

        self.log(f"🧭 Kẻ Lang Thang xuất hiện: {len(current)} con")
        with self.klt_lock:
            self.klt_targets = current
            for key, state in current.items():
                t = self.klt_threads.get(key)
                if not t or not t.is_alive():
                    self.klt_threads[key] = threading.Thread(
                        target=self._klt_worker, args=(key,),
                        name=f"KLT-{state['dao']}-{state['id']}", daemon=True
                    )
                    self.klt_threads[key].start()

    def _on_item_fly(self, data):
        with self.klt_lock:
            self._item_fly_seq += 1
            self._last_item_fly = data
        if isinstance(data, dict):
            name = data.get("name") or data.get("item") or "?"
            self.log(f"🎁 Kẻ Lang Thang rơi đồ → ItemFly: {name} (đã nhận tín hiệu nhặt đồ)")
        else:
            self.log("🎁 Kẻ Lang Thang rơi đồ → ItemFly (đã nhận tín hiệu nhặt đồ)")

    def _klt_worker(self, key):
        start_seq = 0
        with self.klt_lock:
            start_seq = self._item_fly_seq
            state = dict(self.klt_targets.get(key) or {})
        if not state:
            with self.klt_lock:
                self.klt_threads.pop(key, None)
            return

        dao = state["dao"]
        rid = state["id"]
        max_taps = max(1, state["maxlandap"] - state["solandap"])
        # Safety cap: enough to retry missed taps, but never an unbounded spam loop.
        max_taps = min(max_taps, 24)
        sent = 0
        self.log(f"⚔️ Kẻ Lang Thang dao={dao} id={rid}: bắt đầu đập liên tục ({max_taps} lượt tối đa)")

        try:
            for _ in range(max_taps):
                if self.klt_stop.is_set() or not self.klt_enabled:
                    break
                with self.klt_lock:
                    cur = self.klt_targets.get(key)
                    if cur is None:
                        break
                    if int(cur.get("solandap", 0)) >= int(cur.get("maxlandap", max_taps)):
                        break
                try:
                    with self.klt_pick_lock:
                        self._klt_tap_times[(dao, rid)] = time.monotonic()
                    self.sc.emit("DapKeLangThang", {
                        "dao": dao,
                        "id": rid,
                        "nameobject": "ConLan",
                    })
                    sent += 1
                except Exception as e:
                    self.log(f"KLT dao={dao} id={rid}: emit lỗi: {e}")
                    break
                # Let the server push the updated solandap/disappearance state.
                if self.klt_stop.wait(0.35):
                    break

            # KLT tap is the trigger for the event drop. Refresh every known island
            # once so `datadao.itemEvent` is visible to the picker even when the
            # server does not broadcast a pre-pick `itemdao` event.
            if sent > 0 and self.event_item_enabled and self.event_item_ready:
                threading.Thread(target=self._scan_item_event_islands_once,
                                 name="EventItemSweep", daemon=True).start()

            # Give the server a short grace period to remove the ConLan and push
            # ItemFly/itemdao/Thongbao. itemdao may arrive after the snapshot refresh.
            deadline = time.time() + 4.0
            while time.time() < deadline and not self.klt_stop.is_set():
                with self.klt_lock:
                    if key not in self.klt_targets or self._item_fly_seq > start_seq:
                        break
                time.sleep(0.2)

            with self.klt_lock:
                flew = self._item_fly_seq > start_seq
                still = key in self.klt_targets
            if flew:
                self.log(f"✅ Kẻ Lang Thang dao={dao} id={rid}: đã có ItemFly, nhặt đồ hoàn tất (gửi {sent} lượt)")
            elif still:
                self.log(f"⏱️ Kẻ Lang Thang dao={dao} id={rid}: chưa biến mất/không có ItemFly sau {sent} lượt")
            else:
                self.log(f"✅ Kẻ Lang Thang dao={dao} id={rid}: đã biến mất; chưa thấy ItemFly trong cửa sổ chờ")
        finally:
            with self.klt_lock:
                self.klt_threads.pop(key, None)
                # Keep the latest server state if still active; the next Event
                # update will refresh the worker if needed.

    def stop_klt(self):
        self.klt_stop.set()
        with self.klt_lock:
            self.klt_targets.clear()
        with self.klt_pick_lock:
            self._klt_tap_times.clear()
            self._picked_item_times.clear()
        self.event_item_stop.set()
        self._item_event_scan_stop.set()
        self._stop_item_event_watcher()
        with self.event_item_lock:
            self.event_item_pending.clear()
            self._event_item_waiting_key = None
            self.event_item_worker = None
            self._event_item_seen_signals.clear()
            self._event_item_signal_order.clear()
        # Create fresh events for a future login/restart.
        self.klt_stop = threading.Event()
        self.event_item_stop = threading.Event()
        self.event_item_ready = False


    def _notify(self):
        cb = self.on_islands_changed
        if not cb:
            return
        try:
            cb(self.list_islands())
        except Exception:
            pass

    def _load_from_file(self):
        try:
            if not os.path.exists(self.dv_file):
                return
            with open(self.dv_file, "r", encoding="utf-8") as f:
                raw = f.read().strip()
            if not raw or not raw.startswith("{"):
                return
            data = json.loads(raw)
            if not isinstance(data, dict):
                return
            self.data_versions = data.get("versions") or {}
            for k in self.data_versions.keys():
                if str(k).isdigit():
                    self.known_islands.add(str(k))
            saved = data.get("known_islands") or []
            for k in saved:
                if str(k).isdigit():
                    self.known_islands.add(str(k))
        except Exception:
            pass

    def _save_to_file(self):
        try:
            with open(self.dv_file, "w", encoding="utf-8") as f:
                json.dump({
                    "versions": self.data_versions,
                    "known_islands": sorted(
                        self.known_islands,
                        key=lambda x: int(x) if str(x).isdigit() else 0,
                    ),
                }, f, indent=2)
        except Exception:
            pass

    def register_island(self, idx, source="", notify=True):
        idx = str(idx)
        if not idx.isdigit():
            return False
        is_new = idx not in self.known_islands
        self.known_islands.add(idx)
        if is_new:
            self._save_to_file()
            self.log(f"🏝️ Nhận đảo mới: {idx}" + (f" ({source})" if source else ""))
            if notify:
                self._notify()
            return True
        return False

    def list_islands(self):
        """Danh sách đảo đã biết, sort số — tối thiểu 0..4."""
        def key(x):
            try:
                return int(x)
            except Exception:
                return 999
        ids = set(self.known_islands)
        for x in DEFAULT_ISLANDS:
            ids.add(x)
        numeric = [int(x) for x in ids if str(x).isdigit()]
        if not numeric:
            return list(DEFAULT_ISLANDS)
        mx = max(numeric)
        return [str(i) for i in range(mx + 1)]

    def max_island(self):
        ids = [int(x) for x in self.list_islands() if str(x).isdigit()]
        return max(ids) if ids else 4

    def discover_from_login(self, login_data):
        """Đọc LoginSuccess → đăng ký mọi đảo. 5 đảo = id 0,1,2,3,4."""
        if not isinstance(login_data, dict):
            self._notify()
            return self.list_islands()
        found = set(DEFAULT_ISLANDS)

        def _add_range_up_to(n, as_count=False):
            try:
                n = int(n)
            except Exception:
                return
            if n < 0 or n > 30:
                return
            end = n if as_count else n + 1
            for i in range(max(end, 0)):
                found.add(str(i))

        dao = login_data.get("dao") or []
        if isinstance(dao, list):
            for i, block in enumerate(dao):
                if not isinstance(block, dict):
                    continue
                idx = block.get("islandIndex", block.get("dao", block.get("id", i)))
                try:
                    found.add(str(int(idx)))
                except Exception:
                    found.add(str(idx))
                for r in block.get("rong") or []:
                    if isinstance(r, dict) and r.get("islandIndex") is not None:
                        found.add(str(r["islandIndex"]))
            if len(dao) > 0:
                _add_range_up_to(len(dao), as_count=True)

        for key in ("soDao", "sodao", "maxDao", "maxdao", "tongDao", "levelDao", "so_dao"):
            v = login_data.get(key)
            if v is not None:
                _add_range_up_to(v, as_count=True)
                _add_range_up_to(v, as_count=False)

        for nest in (login_data.get("user"), login_data.get("info"), login_data.get("player")):
            if not isinstance(nest, dict):
                continue
            for key in ("soDao", "sodao", "maxDao", "maxdao", "tongDao", "dao", "so_dao"):
                v = nest.get(key)
                if isinstance(v, (int, float)):
                    _add_range_up_to(v, as_count=True)
                    _add_range_up_to(v, as_count=False)
                elif isinstance(v, list):
                    _add_range_up_to(len(v), as_count=True)
                    for i, item in enumerate(v):
                        found.add(str(i))
                        if isinstance(item, dict):
                            for k2 in ("islandIndex", "dao", "id"):
                                if item.get(k2) is not None:
                                    found.add(str(item[k2]))

        numeric = []
        for idx in found:
            s = str(idx)
            if s.isdigit():
                numeric.append(int(s))
                self.register_island(s, source="login", notify=False)

        if numeric:
            mx = max(numeric)
            for i in range(mx + 1):
                self.register_island(str(i), source="login-fill", notify=False)

        for x in DEFAULT_ISLANDS:
            self.known_islands.add(x)
        self._save_to_file()
        self.log(f"🏝️ Đảo từ login: {self.list_islands()}")
        self._notify()
        return self.list_islands()

    def _on_info(self, data):
        if not isinstance(data, dict):
            return
        preload = data.get("preloaddao")
        if isinstance(preload, dict) and preload.get("status") == "thanhcong":
            dao = str(preload.get("dao", "?"))
            dv = data.get("dataVersion")
            datadao = preload.get("datadao")
            if datadao:
                self._last_datadao_by_island[dao] = datadao
                # `itemEvent` is the island-side snapshot of dropped event items.
                # It can contain a dynamic id as a dict key instead of `itemdao`.
                self._queue_item_event_snapshot(datadao, dao)
                rong = datadao.get("rong") or []
                clean = [r for r in rong if isinstance(r, dict) and r.get("id")]
                self.islands_cache[dao] = clean
                if dao not in self._quiet_load_targets:
                    self.log(f"📦 Đảo {dao}: {len(clean)} rồng")
                for r in clean:
                    if r.get("islandIndex") is not None:
                        self.register_island(r["islandIndex"], source="preload", notify=False)
            if dv:
                self.data_versions[dao] = dv
            self._loaded_islands.add(dao)
            self.register_island(dao, source="preload-ok", notify=True)
            self._save_to_file()

    def load_dao(self, target_idx, timeout=10, quiet=False):
        if not self.sc or not self.sc.is_connected():
            self.log("❌ Socket không connected")
            return False

        target = str(target_idx)
        with self._island_io_lock:
            self.islands_cache.pop(target, None)
            self._loaded_islands.discard(target)

            payload = f"{target}+khongcodao+{target}"
            if quiet:
                self._quiet_load_targets.add(target)
            else:
                self.log(f"→ PreloadDao '{payload}'")
            try:
                self.sc.emit("PreloadDao", payload)
            except Exception as e:
                self._quiet_load_targets.discard(target)
                self.log(f"emit err: {e}")
                return False

            deadline = time.time() + timeout
            while time.time() < deadline:
                if target in self.islands_cache:
                    self.current_island = target
                    self.register_island(target, source="load", notify=not quiet)
                    if quiet:
                        self._quiet_load_targets.discard(target)
                    else:
                        self.log(f"✅ Loaded đảo {target}")
                    return True
                time.sleep(0.2)

            self._quiet_load_targets.discard(target)
            if not quiet:
                self.log(f"⏱️ Timeout / chưa mở đảo {target}")
            return False

    def scan_all(self, islands=None, probe_next=True):
        if islands is None:
            islands = self.list_islands()
        results = {}
        for idx in islands:
            ok = self.load_dao(idx)
            results[str(idx)] = len(self.islands_cache.get(str(idx), [])) if ok else -1
            time.sleep(0.8)

        if probe_next:
            mx = self.max_island()
            for extra in range(mx + 1, mx + 4):
                ok = self.load_dao(str(extra))
                if ok:
                    results[str(extra)] = len(self.islands_cache.get(str(extra), []))
                    self.log(f"🏝️ Phát hiện đảo mới id={extra}")
                    time.sleep(0.8)
                else:
                    break
        self._notify()
        return results

    def get_rong_cua_dao(self, island_idx):
        return self.islands_cache.get(str(island_idx), [])
