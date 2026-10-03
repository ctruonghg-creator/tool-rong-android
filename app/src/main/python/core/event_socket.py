# core/event_socket.py
"""Sự kiện — điểm danh + chỉ chạy nhiệm vụ CHƯA xong bằng module sẵn có trên tool."""
import json
import time
from typing import Any, Callable, Dict, List, Optional, Set


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


# Legacy keys that may exist in older payloads. The strict runner never skips a
# pending event mission; unsupported keys stay locked/pending until they can progress.
SKIP_KEYS = {
    "TangQuaBanBe",
    "GuiQuaBanBe",
    "TangQua",
}

# Map key server → hành động tool
MISSION_LABEL = {
    "ChoRongAn": "Cho rồng ăn",
    "ThuHoachCongTrinh": "Thu hoạch công trình",
    "ThamGiaVienChinh": "Viễn chinh TinhTheXanh1 / chedo 0",
    "LaiRong": "Lai 2 rồng sao thấp nhất",
    "TayTuyRong": "Tẩy tủy Rồng",
    "ThamGiaDauTruongThuThach": "Đấu trường thử thách",
    "ThamGiaLoiDai": "Lôi Đài",
    "thamgialoidai": "Lôi Đài",
    "ChucPhucRong": "Chúc phúc",
    "ChoThanLongUongSua": "Cho thần long uống sữa",
    "ThamGiaBossTheGioi": "Xem/tham gia Boss thế giới",
    "ThamGiaBossHuyetUng": "Boss huyết ưng",
    "ApRong": "Ấp rồng (chưa đủ protocol capture)",
    "ThamChienBachTuoc1Lan": "Bạch Tuộc (chưa đủ protocol capture)",
    "MuaQua1Vang": "Mua quà 1 Vàng/ngày",
    "TreuLan": "Trêu lan / đập Kẻ Lang Thang",
    "DanhRongBanBe": "Đánh rồng bạn bè",
    "DanhKePhaHoai": "Đánh kẻ phá hoại (chưa đủ protocol capture)",
}


class EventSocketController:
    def __init__(
        self,
        socket_client,
        stats,
        app_controllers: Optional[Dict[str, Any]] = None,
        log_fn: Optional[Callable] = None,
    ):
        self.sc = socket_client
        self.stats = stats
        self.controllers = app_controllers or {}
        self.log = log_fn or print
        self.last_pending: List[str] = []
        self.last_missions_parsed = False
        self.last_flow_error = False
        # Throttle log cho nhiệm vụ boss chưa tới giờ; quan trọng hơn là
        # tuyệt đối không gọi enter_and_fight ngoài cửa sổ boss tự động.
        self._boss_gate_log_at: Dict[str, float] = {}
        self._flow_lock = __import__("threading").RLock()
        self.last_mission_specs: Dict[str, dict] = {}
        self.current_mission_key: Optional[str] = None
        self.current_mission_attempts: int = 0
        self._mission_wait_log_at: Dict[str, float] = {}
        # Trạng thái chọn bạn cho nhiệm vụ DanhRongBanBe. Mỗi lần action
        # chọn một người bạn khác trước khi quay lại kiểm tra tiến độ server.
        self._friend_cursor: int = 0
        self._friend_last_targets: Set[str] = set()
        # Số action Tẩy Tủy đã ACK thành công cho task hiện tại.
        # Dùng để tránh spam Tẩy Tủy khi server GetData cập nhật chậm/stale.
        self._taytuy_action_counts: Dict[str, int] = {}
        # Persist across repeated scheduler/UI calls to run_event_flow().
        # A successful TayTuy ACK must lock the current account/task until
        # Event GetData itself reports progress; otherwise the scheduler can
        # restart the flow and accidentally Tẩy another dragon.
        self._taytuy_server_sync_wait: Dict[str, bool] = {}
        # Timestamp khi bắt đầu chờ server sync sau ACK TayTuy (per event).
        # Nếu quá lâu mà GetData vẫn 0 → mở khóa, thử rồng khác (tránh kẹt vĩnh viễn).
        self._taytuy_sync_wait_since: Dict[str, float] = {}
        # Số lần đã unlock-retry vì server không ghi nhận (tránh spam vô hạn).
        self._taytuy_unlock_retries: Dict[str, int] = {}
        # Số lần liên tiếp XemRongTayTuy trả rỗng. Chỉ tăng khi controller
        # xác nhận đúng pattern server-rỗng, không tăng cho lỗi transport khác.
        self._taytuy_empty_streak: int = 0
        # Task bị defer tạm (vd. TayTuy sau max unlock vẫn 0) → làm task khác trước.
        # key -> unix time khi được phép retry lại.
        self._deferred_until: Dict[str, float] = {}

    def log_msg(self, msg: str) -> None:
        self.log(f"[SuKien] {msg}")

    def _call(self, event_name: str, method: str, data=None, timeout=8, transport="SendRequest"):
        # SendRequest/SendRequest2 trả business-response qua Socket.IO ACK.
        # Dùng request() để không bị lệ thuộc vào raw event stream.
        payload: Dict[str, Any] = {"class": event_name, "method": method}
        if data is not None:
            payload["data"] = data
        try:
            res = self.sc.request(transport, payload, timeout=timeout)
        except AttributeError:
            # Fallback cho build cũ không có request().
            before = len(self.sc.last_events) if hasattr(self.sc, "last_events") else 0
            try:
                self.sc.emit("SendRequest", payload)
            except Exception:
                return None
            end_t = time.time() + timeout
            while time.time() < end_t:
                if hasattr(self.sc, "last_events"):
                    with self.sc.lock:
                        events = list(self.sc.last_events)[before:]
                    for _, _ev, blob in events:
                        if isinstance(blob, dict) and ("status" in blob or "errorCode" in blob):
                            return blob
                        if isinstance(blob, list) and blob and isinstance(blob[0], dict):
                            return blob[0]
                time.sleep(0.15)
            return None
        return res

    @staticmethod
    def _decode_json_blob(blob: Any) -> Any:
        """Decode ACKs that may arrive as a JSON string or nested JSON string."""
        value = blob
        for _ in range(3):
            if isinstance(value, bytes):
                try:
                    value = value.decode("utf-8")
                except Exception:
                    break
            if not isinstance(value, str):
                break
            text = value.strip()
            if not text:
                break
            try:
                value = json.loads(text)
            except Exception:
                break
        return value

    @classmethod
    def _first_dict(cls, blob: Any) -> Optional[Dict[str, Any]]:
        blob = cls._decode_json_blob(blob)
        if isinstance(blob, dict):
            return blob
        if isinstance(blob, list):
            for item in blob:
                item = cls._decode_json_blob(item)
                if isinstance(item, dict):
                    return item
        return None

    @classmethod
    def _find_nested_dict_key(cls, blob: Any, key: str, max_depth: int = 8) -> Optional[Dict[str, Any]]:
        """Find a dict-valued key anywhere in a bounded ACK wrapper."""
        def walk(value: Any, depth: int) -> Optional[Dict[str, Any]]:
            value = cls._decode_json_blob(value)
            if depth > max_depth:
                return None
            if isinstance(value, dict):
                hit = value.get(key)
                if isinstance(hit, dict):
                    return hit
                for child in value.values():
                    if isinstance(child, (dict, list, str)):
                        got = walk(child, depth + 1)
                        if got is not None:
                            return got
            elif isinstance(value, list):
                for child in value:
                    got = walk(child, depth + 1)
                    if got is not None:
                        return got
            return None
        return walk(blob, 0)

    def _attendance_from(self, blob: Any) -> Optional[Dict[str, Any]]:
        return self._find_nested_dict_key(blob, "attendance")

    def _extract_mission_items(self, data) -> Optional[List[Any]]:
        """Extract the mission list from the event GetData ACK.

        Captures show allNhiemvu nested under response.data; real ACK wrappers
        can add another dict/list/string layer, so walk a bounded set of
        containers without assuming a single exact envelope.
        """
        value = self._decode_json_blob(data)
        if value is None:
            return None

        mission_keys = (
            "allNhiemvu", "danhSachNhiemVuDangLam", "nhiemVuDangLam",
            "nhiemvu", "listNhiemVu", "listnhiemvu", "quests", "missions",
            "datanhiemvu", "dataNhiemVu", "nv", "list", "items",
        )

        def walk(obj: Any, depth: int = 0) -> Optional[List[Any]]:
            if depth > 8:
                return None
            obj = self._decode_json_blob(obj)
            if isinstance(obj, dict):
                for k in mission_keys:
                    v = obj.get(k)
                    if isinstance(v, list):
                        return v
                    if isinstance(v, dict) and v:
                        # allNhiemvu is normally a list; for generic legacy
                        # wrappers preserve dict entries as (key, value) pairs.
                        if k == "allNhiemvu":
                            vals = list(v.values())
                            if vals and all(isinstance(x, (dict, str)) for x in vals):
                                return vals
                        else:
                            return list(v.items())
                for child in obj.values():
                    if isinstance(child, (dict, list, str)):
                        got = walk(child, depth + 1)
                        if got:
                            return got
                return None
            if isinstance(obj, list):
                # A top-level ACK commonly looks like [{status,data...}].
                for item in obj:
                    got = walk(item, depth + 1)
                    if got:
                        return got
                # Only treat the list itself as the mission array when its
                # entries look like mission objects/keys.
                if obj and all(isinstance(x, (dict, str)) for x in obj):
                    return obj
                return None
            return None

        return walk(value)

    def _boss_before_mission(self, key: str) -> bool:
        """Gate Boss task trước khi execute_mission gửi lệnh thực tế.

        Hàm này chỉ kiểm tra lịch/quyền chạy; không tự gọi enter_and_fight,
        tránh mở Boss 2 lần khi task thành công.
        """
        boss = self.controllers.get("boss")
        mode = self._boss_mode_for_mission(key)
        if not boss or not mode:
            return True

        if getattr(boss, "_fighting", False):
            self.log_msg(f"  Boss đang đánh → chờ Boss xong rồi mới xử lý {key}")
            deadline = time.time() + float(getattr(boss, "max_fight_sec", 900)) + 90.0
            while getattr(boss, "_fighting", False) and time.time() < deadline:
                time.sleep(1.0)
            return False

        try:
            if not boss.is_scheduled_now(mode):
                return False
            label = "Boss LiênSV" if mode == "lienserver" else "Boss thường"
            self.log_msg(f"  ⏰ Đúng giờ {label} → sẵn sàng thực hiện task {key}")
            return True
        except Exception as e:
            self.log_msg(f"  Không xác định được lịch Boss: {e} — giữ nhiệm vụ pending")
            return False

    def _item_key(self, item) -> Optional[str]:
        if isinstance(item, str) and item:
            return item
        if isinstance(item, (tuple, list)) and len(item) >= 1:
            k = item[0]
            if isinstance(k, str):
                return k
            if isinstance(item[1], dict):
                item = item[1]
            else:
                return str(k)
        if not isinstance(item, dict):
            return None
        for k in ("key", "id", "ma", "name", "ten", "code", "nhiemvu", "missionKey"):
            v = item.get(k)
            if isinstance(v, str) and v and v not in ("0", "1"):
                return v
        return None

    def _item_done(self, item) -> bool:
        """True = đã xong / đã nhận."""
        if isinstance(item, str):
            return False
        if isinstance(item, (tuple, list)) and len(item) >= 2 and isinstance(item[1], dict):
            item = item[1]
        if not isinstance(item, dict):
            return False
        for k in ("done", "hoanthanh", "hoanThanh", "finished", "claimed", "danhan", "daHoanThanh"):
            v = item.get(k)
            if v in (True, 1, "1", "true", "True", "ok", "xong"):
                return True
        st = item.get("status")
        # nhiều server: 1 = xong, 0 = chưa
        if st in (1, "1", "done", "hoanthanh", "claimed"):
            return True
        cur = item.get("current", item.get("tiendo", item.get("progress", item.get("solan", item.get("dalam")))))
        mx = item.get("max", item.get("yeucau", item.get("need", item.get("target", item.get("maxnhiemvu")))))
        if cur is not None and mx is not None:
            try:
                if int(cur) >= int(mx) and int(mx) > 0:
                    return True
            except Exception:
                pass
        return False

    def fetch_pending_specs(self, event_name: str, verbose: bool = True) -> Dict[str, dict]:
        """Đọc allNhiemvu và giữ lại key/dalam/maxnhiemvu/info cho mission runner."""
        self.last_flow_error = False
        res = self._call(event_name, "GetData", timeout=8)
        items = self._extract_mission_items(res) if res is not None else None
        if not items:
            alt = self._call(event_name, "GetData", timeout=8, transport="SendRequest2")
            items = self._extract_mission_items(alt) if alt is not None else None
            res = alt if alt is not None else res
        if not items:
            self.last_missions_parsed = False
            self.last_pending = []
            self.last_mission_specs = {}
            self.last_flow_error = True
            if verbose:
                raw = res
                detail = f"; ACK type={type(raw).__name__}"
                if isinstance(raw, dict):
                    detail += f"; keys={list(raw.keys())[:12]}"
                self.log_msg(f"Không đọc được danh sách nhiệm vụ{detail} — dừng phase")
            return {}

        self.last_missions_parsed = True
        specs: Dict[str, dict] = {}
        for it in items:
            key = self._item_key(it)
            if not key or key in specs:
                continue
            if key in SKIP_KEYS and verbose:
                self.log_msg(f"  ○ {key}: nhiệm vụ bạn bè đang pending, chưa tự động claim")
            spec = {}
            if isinstance(it, dict):
                spec.update(it)
            cur = spec.get("dalam", spec.get("current", spec.get("tiendo", spec.get("progress", 0))))
            mx = spec.get("maxnhiemvu", spec.get("max", spec.get("yeucau", spec.get("target", 1))))
            try: cur_i = int(cur or 0)
            except Exception: cur_i = 0
            try: max_i = max(1, int(mx or 1))
            except Exception: max_i = 1
            spec["key"] = key
            spec["dalam"] = cur_i
            spec["maxnhiemvu"] = max_i
            done = cur_i >= max_i or self._item_done(spec)
            spec["done"] = bool(done)
            specs[key] = spec
            if verbose:
                self.log_msg(f"  {'✓ đã xong' if done else '○ chưa xong'}: {key} ({cur_i}/{max_i})")

        self.last_mission_specs = specs
        pending = [k for k, v in specs.items() if not v.get("done")]
        self.last_pending = pending
        return specs

    def fetch_pending_missions(self, event_name: str, verbose: bool = True) -> List[str]:
        specs = self.fetch_pending_specs(event_name, verbose=verbose)
        return [k for k, spec in specs.items() if not spec.get("done")]

    def _find_nested_value(self, blob: Any, key: str, max_depth: int = 8) -> Any:
        """Tìm value của một key trong ACK/event wrapper lồng nhau."""
        def walk(value: Any, depth: int):
            if depth > max_depth:
                return None
            value = self._decode_json_blob(value)
            if isinstance(value, dict):
                if key in value:
                    return value.get(key)
                for child in value.values():
                    if isinstance(child, (dict, list, str)):
                        got = walk(child, depth + 1)
                        if got is not None:
                            return got
            elif isinstance(value, list):
                for child in value:
                    got = walk(child, depth + 1)
                    if got is not None:
                        return got
            return None
        return walk(blob, 0)

    def _friend_candidates(self, response: Any) -> List[dict]:
        """Chuẩn hóa Main.GetListFriend -> danh sách bạn có thể mở.

        Event *Đánh rồng bạn bè* không yêu cầu mỗi lượt phải là một người khác.
        Vì vậy không loại bạn đã dùng trước đó: nếu danh sách chỉ có 1 người
        thì vẫn được phép quay lại chính người đó cho lượt 2, 3, 4...
        """
        allfriend = self._find_nested_value(response, "allfriend")
        if not isinstance(allfriend, dict):
            return []

        out: List[dict] = []
        own_id = str(getattr(self.sc, "account_id", "") or "")
        for value in allfriend.values():
            value = self._decode_json_blob(value)
            if not isinstance(value, dict):
                continue
            name = str(value.get("name") or value.get("tenhienthi") or "").strip()
            idfr = str(value.get("idfb") or value.get("id") or value.get("objectId") or "").strip()
            if not name or not idfr or name == "0" or idfr == "0":
                continue
            if own_id and idfr == own_id:
                continue
            out.append({"name": name, "idfr": idfr})
        return out

    def _wait_friend_push(self, friend_name: str, before_ts: float, timeout: float = 10.0) -> bool:
        """Chờ server xác nhận đã mở nhà bạn / solo chiến tướng.

        Capture:
          GetFriend -> quanhafriend -> GetAvtFriend -> solochientuong -> Friend

        Với nhiệm vụ Event, tiến độ thường chỉ hiện qua Event.GetData (không
        bắt buộc updateMoney.danhrongbanbe của daily). Vì vậy coi
        solochientuong / Friend.solochientuong là action OK; runner sẽ verify
        GetData sau đó.
        """
        deadline = time.time() + timeout
        saw_friend_push = False
        while time.time() < deadline:
            events = []
            if hasattr(self.sc, "last_events"):
                try:
                    with self.sc.lock:
                        snapshot = list(self.sc.last_events)
                    events = [
                        row for row in snapshot
                        if isinstance(row, (tuple, list))
                        and len(row) >= 3
                        and float(row[0]) >= before_ts
                    ]
                except Exception:
                    events = []

            for _, event, data in events:
                payload = self._decode_json_blob(data)
                if event == "solochientuong":
                    # Capture: solochientuong payload = tên hiển thị bạn (string)
                    # hoặc dict; chấp nhận mọi solochientuong sau GetFriend.
                    if not saw_friend_push:
                        self.log_msg(f"  🤝 {friend_name}: nhận push solochientuong")
                    saw_friend_push = True
                    # Đủ để tính 1 lượt mở nhà / đánh bạn bè.
                    return True

                elif event == "Friend":
                    solo = self._find_nested_value(payload, "solochientuong")
                    if isinstance(solo, dict) or solo is not None:
                        if not saw_friend_push:
                            self.log_msg(f"  🤝 {friend_name}: nhận Friend/solochientuong")
                        saw_friend_push = True
                        return True

                elif event in ("updateMoney", "UpdateMoney"):
                    up = self._find_nested_value(payload, "updatenhiemvu")
                    if isinstance(up, dict):
                        name = str(up.get("namenv") or "").lower()
                        # Daily: danhrongbanbe / thamnhahangxom; cả hai chứng tỏ đã vào nhà bạn.
                        if "danhrong" in name or "banbe" in name or "hangxom" in name or "nhahang" in name:
                            self.log_msg(
                                f"  ✅ {friend_name}: server push updatenhiemvu "
                                f"{up.get('namenv')} → {up.get('sonhiemvu', '?')}"
                            )
                            return True
            time.sleep(0.15)
        if saw_friend_push:
            return True
        self.log_msg(
            f"  ⚠️ {friend_name}: chưa thấy push solochientuong/Friend trong {timeout:.0f}s"
        )
        return False

    def _friend_battle_once(self) -> bool:
        """Đánh rồng bạn bè: danh sách bạn → chọn bạn → nút Chiến đấu.

        Capture + mô tả user:
          GetListFriend → GetFriend → quanhafriend → GetAvtFriend
          → emit solochientuong(tên bạn)  ≈ nút Chiến đấu
          → server push Friend {solochientuong: ...}
        Không thả rồng.
        """
        own_id = str(getattr(self.sc, "account_id", "") or "").strip()
        if not own_id:
            self.log_msg("  DanhRongBanBe: không có account_id để gọi GetListFriend")
            return False

        res = self._call("Main", "GetListFriend", data={"id": own_id}, timeout=8)
        candidates = self._friend_candidates(res)
        if not candidates:
            time.sleep(0.5)
            res = self._call("Main", "GetListFriend", data={"id": own_id}, timeout=8)
            candidates = self._friend_candidates(res)
        if not candidates and self._friend_last_targets:
            self._friend_last_targets.clear()
            candidates = self._friend_candidates(res)
        if not candidates:
            self.log_msg("  DanhRongBanBe: GetListFriend không trả bạn hợp lệ — giữ pending")
            return False

        names = [c["name"] for c in candidates[:8]]
        self.log_msg(f"  📋 Bạn bè: {len(candidates)} — {names}{'...' if len(candidates) > 8 else ''}")

        friend = candidates[self._friend_cursor % len(candidates)]
        self._friend_cursor += 1
        friend_name = friend["name"]
        friend_id = friend["idfr"]

        self.log_msg(f"  🤝 Chọn bạn {friend_name} ({friend_id}) — GetFriend")
        before_ts = time.time()
        get_friend = self._call(
            "Main", "GetFriend",
            data={"idFriend": friend_name, "id": own_id, "idfr": friend_id},
            timeout=10,
        )
        status = self._response_status_ok(get_friend)
        if status is False or get_friend is None:
            self.log_msg(f"  ❌ GetFriend {friend_name} thất bại: {self._first_dict(get_friend) or get_friend}")
            return False

        try:
            self.sc.emit("quanhafriend", "quanha")
            self.log_msg("  → quanhafriend (vào nhà)")
            time.sleep(0.35)
        except Exception as e:
            self.log_msg(f"  ⚠️ quanhafriend lỗi: {e}")

        for avt_try in range(3):
            avt = self._call("Main", "GetAvtFriend", data={"id": friend_id}, timeout=5, transport="SendRequest2")
            avt_status = self._response_status_ok(avt)
            if avt_status is not False:
                break
            err = self._first_dict(avt) if isinstance(self._first_dict(avt), dict) else {}
            if isinstance(err, dict) and err.get("errorCode") == "inventory_mutation_busy":
                self.log_msg(f"  ⏳ GetAvtFriend đang bận (lần {avt_try+1}/3) → retry")
                time.sleep(0.5 + avt_try * 0.4)
                continue
            break

        # Nút Chiến đấu — capture: solochientuong + tên hiển thị bạn.
        try:
            self.sc.emit("solochientuong", friend_name)
            self.log_msg(f"  ⚔️ Chiến đấu: solochientuong '{friend_name}'")
            time.sleep(0.55)
        except Exception as e:
            self.log_msg(f"  ⚠️ solochientuong lỗi: {e}")
            return False

        pushed = self._wait_friend_push(friend_name, before_ts, timeout=6.0)
        if not pushed:
            # Fallback: thử id tài khoản thay vì tên hiển thị.
            try:
                self.sc.emit("solochientuong", friend_id)
                self.log_msg(f"  ⚔️ Chiến đấu fallback: solochientuong id='{friend_id}'")
                time.sleep(0.5)
            except Exception:
                pass
            pushed = self._wait_friend_push(friend_name, before_ts, timeout=5.0)
        if pushed:
            self.log_msg(f"  ✅ {friend_name}: đã chiến đấu — chờ Event.GetData xác nhận tiến độ")
        else:
            self.log_msg(
                f"  ✅ {friend_name}: đã gửi Chiến đấu (chưa thấy push) — "
                "Event.GetData sẽ xác nhận"
            )
        time.sleep(0.5)
        return True

    def _boss_mode_for_mission(self, key: str) -> Optional[str]:
        if key == "ThamGiaBossTheGioi":
            return "thuong"
        if key == "ThamGiaBossHuyetUng":
            return "lienserver"
        return None

    def _boss_not_due(self, key: str) -> bool:
        """True khi nhiệm vụ Boss hiện tại chưa tới cửa sổ đánh."""
        mode = self._boss_mode_for_mission(key)
        if not mode:
            return False
        boss = self.controllers.get("boss")
        if not boss or not hasattr(boss, "is_scheduled_now"):
            return False
        try:
            return not bool(boss.is_scheduled_now(mode))
        except Exception:
            return False

    def _is_deferred(self, key: str) -> bool:
        until = float(self._deferred_until.get(key) or 0.0)
        if until <= 0:
            return False
        if time.time() >= until:
            self._deferred_until.pop(key, None)
            return False
        return True

    def _pick_next_pending(self, pending: List[str]) -> Optional[str]:
        """Strict-order; Boss chưa tới giờ và task bị defer tạm được bỏ qua."""
        if not pending:
            return None

        def _blocked(key: str) -> bool:
            return self._boss_not_due(key) or self._is_deferred(key)

        first = pending[0]
        if not _blocked(first):
            return first
        for key in pending[1:]:
            if not _blocked(key):
                reason = "chưa tới giờ Boss" if self._boss_not_due(first) else "đang defer tạm"
                self.log_msg(
                    f"⏭️ {first}: {reason} → DEFER; tiếp tục task {key}"
                )
                return key
        # Chỉ còn task bị block → trả first để inner loop chờ (Boss) hoặc log defer.
        return first

    def execute_mission(self, key: str) -> bool:
        c = self.controllers
        self.log_msg(f"→ Làm: {MISSION_LABEL.get(key, key)}")
        flow_lock = getattr(self.sc, "automation_lock", None)
        ctx = flow_lock if flow_lock is not None else _NullLock()
        try:
            with ctx:
                return self._execute_mission_locked(key)
        except Exception as e:
            self.log_msg(f"Lỗi nhiệm vụ {key}: {e}")
            return False

    def _execute_mission_locked(self, key: str) -> bool:
        c = self.controllers
        try:
            if key in SKIP_KEYS:
                self.log_msg("  nhiệm vụ legacy chưa có protocol riêng — giữ pending, KHÔNG claim")
                return False

            if key == "DanhRongBanBe":
                return bool(self._friend_battle_once())

            if key == "ThuHoachCongTrinh":
                h = c.get("harvest")
                if h:
                    h.thu_hoach_tat_ca(delay=0.8)
                else:
                    self.sc.emit("ThuHoachCT", "0+0")

            elif key == "ThamGiaVienChinh":
                vc = c.get("vienchinh")
                if vc:
                    # user: TinhTheXanh 1, che do 0
                    vc.full_flow(namemap="TinhTheXanh1", chedo="0", wait_battle=40)
                else:
                    self.log_msg("  chưa có vienchinh_ctrl")

            elif key in ("ChoRongAn",):
                fd = c.get("feed")
                if fd:
                    fd.run_for_event(island_ctrl=c.get("island"), food_name="ThucAnThit")
                else:
                    self.log_msg("  chưa có feed_ctrl")

            elif key in ("ChoThanLongUongSua", "ChoHoaThanLongUongSua"):
                # UI: chọn Thần Long → Bình sữa → Cho uống.
                fd = c.get("feed")
                if not fd or not hasattr(fd, "than_long_bu_sua"):
                    self.log_msg("  chưa có protocol ThanLongBuSua")
                    return False

                special = getattr(self.stats, "special_dragons", {}) or {}
                candidates = []
                for name in ("HoaThanLong", "TuyetThanLong"):
                    if isinstance(special.get(name), dict):
                        candidates.append(name)
                if not candidates:
                    candidates = ["HoaThanLong", "TuyetThanLong"]
                # Xoay Hỏa/Tuyết theo lần gọi để không kẹt 1 con ACK no-op.
                n = int(getattr(self, "_thanlong_try_n", 0) or 0)
                self._thanlong_try_n = n + 1
                ordered = candidates[n % len(candidates):] + candidates[: n % len(candidates)]
                self.log_msg(f"  Thần Long thử: {ordered}")

                any_ok = False
                for name in ordered:
                    self.log_msg(f"  → Cho uống sữa: {name}")
                    if bool(fd.than_long_bu_sua(name, timeout=6)):
                        any_ok = True
                        break
                return any_ok

            elif key == "LaiRong":
                lai = c.get("lai")
                if lai:
                    lai.lai_event_lowest_stars(taytuy_ctrl=c.get("taytuy"))
                else:
                    self.log_msg("  chưa có lai_ctrl")

            elif key == "TayTuyRong":
                tt = c.get("taytuy")
                if tt and hasattr(tt, "tay_tuy_once"):
                    # Tẩy Tủy != Hóa Bụi. Nhiệm vụ này phải đi đúng protocol
                    # ChonRongTayTuy -> TayTuy, tuyệt đối không gọi HoaBuiRong.
                    return bool(tt.tay_tuy_once(timeout=8))
                self.log_msg("  chưa có taytuy_ctrl / protocol Tẩy Tủy")
                return False

            elif key == "ChucPhucRong":
                # Nhiệm vụ Chúc Phúc của event phải tạo ra rồng lai trước,
                # rồi CHÚC PHÚC NGAY rồng con nếu kết quả là rồng giữ lại.
                # Rồng thường thì bán, không chúc phúc và vòng event sẽ kiểm tra
                # GetData trước khi quyết định làm lượt lai tiếp theo.
                lai = c.get("lai")
                if not lai:
                    self.log_msg("  chưa có lai_ctrl")
                    return False
                return bool(lai.lai_event_chuc_phuc(taytuy_ctrl=c.get("taytuy")))

            elif key == "ThamGiaDauTruongThuThach":
                dt = c.get("dautruong")
                if not dt or not hasattr(dt, "one_fight"):
                    self.log_msg("  chưa có dautruong_ctrl")
                    return False
                result = dt.one_fight()
                # Nhiệm vụ là "tham gia", nên thua vẫn là một lượt hợp lệ.
                # Chỉ coi Error/Stop là action chưa thực hiện được.
                result_norm = str(result).lower()
                return result_norm in ("win", "thang", "lose", "thua", "ok")

            elif key in ("ThamGiaLoiDai", "thamgialoidai"):
                ld = c.get("loi_dai")
                if not ld:
                    self.log_msg("  chưa có loi_dai_ctrl")
                    return False
                return bool(ld.one_fight())

            elif key == "TreuLan":
                island = c.get("island")
                if not island or not hasattr(island, "hit_klt_once"):
                    self.log_msg("  chưa có island_ctrl cho Kẻ Lang Thang")
                    return False
                return bool(island.hit_klt_once(timeout=8))

            elif key == "DanhKePhaHoai":
                # Capture hiện có chưa cho đủ request bắt đầu/đánh "kẻ phá hoại"
                # riêng biệt. Không đoán packet; giữ pending để retry khi có protocol.
                self.log_msg("  DanhKePhaHoai: chưa có request chính xác trong capture — giữ pending")
                return False

            elif key == "MuaQua1Vang":
                gift = c.get("gift")
                return bool(gift and hasattr(gift, "buy_one_gold_reward") and gift.buy_one_gold_reward())

            elif key in ("ThamGiaBossTheGioi", "ThamGiaBossHuyetUng"):
                boss = c.get("boss")
                mode = "lienserver" if key == "ThamGiaBossHuyetUng" else "thuong"
                if not boss:
                    self.log_msg("  chưa có boss_ctrl — KHÔNG tự gửi request boss")
                    return False

                # Nhiệm vụ có thể vẫn còn pending suốt cả ngày, nhưng không
                # được biến việc đọc nhiệm vụ thành lệnh đánh boss ngay lập tức.
                # Chỉ cho phép mission runner vào boss sau khi tới đúng cửa sổ
                # giờ VN; scheduler boss riêng vẫn chịu trách nhiệm auto theo lịch.
                try:
                    ready = bool(boss.is_scheduled_now(mode))
                except Exception as e:
                    self.log_msg(f"  Không xác định được giờ boss: {e} — KHÔNG đánh")
                    return False
                if not ready:
                    now = time.time()
                    last = self._boss_gate_log_at.get(key, 0.0)
                    if now - last >= 60.0:
                        self.log_msg(
                            f"  Chưa đến giờ {MISSION_LABEL.get(key, key)} — giữ nhiệm vụ pending, KHÔNG vào đánh"
                        )
                        self._boss_gate_log_at[key] = now
                    return False

                started = bool(boss.enter_and_fight(mode))
                if started:
                    return True
                if getattr(boss, "_last_open_failure", "") == "dead":
                    try:
                        nxt = boss.next_scheduled_slot(mode)
                        self._deferred_until[key] = nxt.timestamp() + 1.0
                        self.log_msg(
                            f"  ⏭️ {key}: server báo Boss đã bị tiêu diệt → "
                            f"DEFER tới {nxt.strftime('%H:%M %d/%m')}"
                        )
                    except Exception as e:
                        self.log_msg(f"  Không tính được mốc Boss kế tiếp: {e}")
                        self._deferred_until[key] = time.time() + 900.0
                return False

            else:
                self.log_msg(f"  chưa gắn controller: {key} — giữ pending, KHÔNG claim")
                return False

            time.sleep(1.0)
            return True
        except Exception as e:
            self.log_msg(f"Lỗi nhiệm vụ {key}: {e}")
            return False

    def _response_status_ok(self, blob: Any) -> Optional[bool]:
        """Read status from arbitrary nested ACK wrappers. None means unknown."""
        value = self._decode_json_blob(blob)
        def walk(obj, depth=0):
            if depth > 8:
                return None
            obj = self._decode_json_blob(obj)
            if isinstance(obj, dict):
                if "errorCode" in obj and obj.get("errorCode"):
                    return False
                if "status" in obj:
                    st = obj.get("status")
                    if st in (0, "0", "ok", "success", "true", True):
                        return True
                    if st in (1, "1", "error", "fail", "false", False):
                        return False
                for child in obj.values():
                    if isinstance(child, (dict, list, str)):
                        got = walk(child, depth + 1)
                        if got is not None:
                            return got
            elif isinstance(obj, list):
                for child in obj:
                    got = walk(child, depth + 1)
                    if got is not None:
                        return got
            return None
        return walk(value)

    def claim_mission(self, event_name: str, key: str) -> bool:
        """Claim only after the server has reached the target, then verify the claim."""
        last_err = None
        for attempt in range(1, 5):
            res = self._call(event_name, "NhanQuaNhiemVu", data={"key": key}, timeout=8)
            status = self._response_status_ok(res)
            if status is False:
                err = self._first_dict(res) if isinstance(self._first_dict(res), dict) else {}
                last_err = err or res
                if isinstance(err, dict) and err.get("errorCode") == "inventory_mutation_busy":
                    self.log_msg(
                        f"  ⏳ Claim {key} inventory_mutation_busy (lần {attempt}/4) → chờ rồi retry"
                    )
                    time.sleep(1.0 + attempt * 0.8)
                    continue
                self.log_msg(f"  Claim {key} bị server từ chối: {last_err}")
                return False
            break
        else:
            self.log_msg(f"  Claim {key} bị server từ chối: {last_err}")
            return False

        time.sleep(0.65)

        verify = self.fetch_pending_specs(event_name, verbose=False)
        if self.last_flow_error:
            return False
        fin = verify.get(key)
        if fin is None or bool(fin.get("done")):
            self.log_msg(f"  ✓ server xác nhận đã nhận quà nhiệm vụ: {key}")
            return True

        self.log_msg(
            f"  ⚠ claim {key} chưa được server xác nhận — còn "
            f"{fin.get('dalam', 0)}/{fin.get('maxnhiemvu', 1)}"
        )
        return False

    def diem_danh(self, event_name: str) -> bool:
        """Điểm danh theo trạng thái server: checkedToday=true thì tuyệt đối không gửi lại."""
        self.log_msg(f"Kiểm tra điểm danh {event_name}...")
        before = self._call(event_name, "GetDiemDanh", timeout=8)
        att = self._attendance_from(before)
        if not att:
            before_alt = self._call(event_name, "GetDiemDanh", timeout=8, transport="SendRequest2")
            att = self._attendance_from(before_alt)
            if att:
                before = before_alt
        if not att:
            detail = ""
            if isinstance(before, dict):
                detail = f" message={before.get('message', '')} status={before.get('status', '')}"
            self.log_msg(f"  Không đọc được trạng thái điểm danh {event_name} — ACK type={type(before).__name__}{detail}")
            return False

        checked = bool(att.get("checkedToday"))
        can_check = bool(att.get("canCheckIn"))

        if checked:
            self.log_msg(f"  Đã điểm danh hôm nay: {event_name} — bỏ qua, không gọi lại")
            latest = att
        elif not can_check:
            self.log_msg(f"  Server báo chưa thể điểm danh lúc này: {event_name} — bỏ qua")
            return True
        else:
            self.log_msg(f"  Chưa điểm danh hôm nay → DiemDanhEvent")
            res = self._call(event_name, "DiemDanhEvent", timeout=8)
            latest = self._attendance_from(res) or att
            # Một số build trả response đầy đủ ngay trong ACK, một số chỉ cập nhật state.
            if not bool(latest.get("checkedToday")):
                after = self._call(event_name, "GetDiemDanh", timeout=8)
                latest = self._attendance_from(after) or latest

        # Chỉ nhận các mốc server cho phép nhận.
        for m in latest.get("milestones", []) if isinstance(latest, dict) else []:
            if not isinstance(m, dict):
                continue
            state = str(m.get("state", "")).lower()
            legacy = str(m.get("legacyState", "")).lower()
            btn = str(m.get("btn", "")).lower()
            if state != "claimable" and legacy != "duocnhan" and btn != "true":
                continue
            mid = m.get("id")
            if not mid:
                continue
            idx = m.get("index", 0)
            self.log_msg(f"  Nhận mốc điểm danh: {mid} (vitri={idx})")
            self._call(
                event_name, "NhanQuaDiemDanh",
                data={"vitri": str(idx), "milestoneId": mid}, timeout=8
            )
            time.sleep(0.4)

        if bool(latest.get("checkedToday")):
            self.log_msg(f"  ✓ {event_name}: checkedToday=true")
            return True
        self.log_msg(f"  ⚠ {event_name}: server chưa xác nhận điểm danh hôm nay")
        return False

    def run_event_flow(self, event_name: str) -> bool:
        """Chạy event theo *strict serial pipeline*.

        Quy tắc bắt buộc:
          1) Chọn đúng nhiệm vụ pending đầu tiên server trả về.
          2) Chỉ được thực hiện nhiệm vụ đó cho tới khi đủ target.
          3) Đọc lại server sau từng action.
          4) Khi đủ target thì NhanQuaNhiemVu + verify.
          5) Chỉ sau khi claim xác nhận mới được chọn nhiệm vụ tiếp theo.

        Ngoại lệ duy nhất: nếu task đầu tiên là nhiệm vụ Boss nhưng CHƯA tới giờ,
        task Boss được defer tạm thời để làm task thường phía sau; Boss vẫn phải
        quay lại xử lý trước khi event được coi là hoàn tất.
        """
        if not self.sc:
            self.last_flow_error = True
            self.last_pending = []
            return False

        with self._flow_lock:
            self.last_flow_error = False
            self.last_pending = []
            self.current_mission_key = None
            self.current_mission_attempts = 0
            try:
                if not self.diem_danh(event_name):
                    self.last_flow_error = True
                    self.log_msg(f"Bỏ phase nhiệm vụ {event_name}: điểm danh chưa xác nhận")
                    return False

                self.log_msg(f"🔒 STRICT MODE {event_name}: bắt buộc xong + nhận quà task hiện tại mới chuyển task kế tiếp")

                # Chỉ hoàn tất khi server không còn task pending.
                while True:
                    specs = self.fetch_pending_specs(event_name, verbose=True)
                    if self.last_flow_error:
                        return False

                    pending = [k for k, v in specs.items() if not v.get("done")]
                    self.last_pending = list(pending)
                    if not pending:
                        self._taytuy_server_sync_wait.pop(event_name, None)
                        self._taytuy_sync_wait_since.pop(event_name, None)
                        self._taytuy_unlock_retries.pop(event_name, None)
                        self._taytuy_action_counts.pop("__current__", None)
                        self.current_mission_key = None
                        self.log_msg(f"✓ Xong TOÀN BỘ nhiệm vụ {event_name}")
                        return True

                    current_key = self._pick_next_pending(pending)
                    if current_key is None:
                        self.current_mission_key = None
                        continue
                    # Nếu task được pick vẫn đang defer (chỉ còn task defer/boss) → chờ ngắn rồi thoát flow.
                    if self._is_deferred(current_key) and not self._boss_not_due(current_key):
                        until = float(self._deferred_until.get(current_key) or 0)
                        left = max(0, int(until - time.time()))
                        self.log_msg(
                            f"  ⏸️ Chỉ còn task đang defer ({current_key}, còn ~{left}s) — "
                            "tạm dừng flow event, sẽ retry sau"
                        )
                        self.current_mission_key = None
                        return False
                    if self.current_mission_key != current_key:
                        self._taytuy_action_counts.pop("__current__", None)
                    self.current_mission_key = current_key
                    self.current_mission_attempts += 1
                    current = specs.get(current_key) or {}
                    cur = int(current.get("dalam", 0) or 0)
                    target = max(1, int(current.get("maxnhiemvu", 1) or 1))

                    # Persistent per-account TayTuy gate. run_event_flow() may be
                    # called again by daily_vn/UI while the previous action is
                    # still waiting for the event backend to register progress.
                    # Never send another TayTuy in that window — TRỪ KHI đã chờ
                    # quá lâu mà GetData vẫn không tăng (server có thể không ghi
                    # nhận rồng vừa tẩy → cần thử rồng khác).
                    if current_key == "TayTuyRong" and self._taytuy_server_sync_wait.get(event_name):
                        wait_since = float(self._taytuy_sync_wait_since.get(event_name) or 0.0)
                        waited = (time.time() - wait_since) if wait_since > 0 else 0.0
                        unlock_n = int(self._taytuy_unlock_retries.get(event_name, 0) or 0)
                        # Sau ~25s không progress: mở khóa 1 lần để thử rồng khác.
                        # Tối đa 3 lần unlock/ngày-task để không spam Tẩy vô hạn.
                        if waited >= 25.0 and unlock_n < 3:
                            self.log_msg(
                                f"  🔓 TayTuyRong: chờ server {waited:.0f}s vẫn 0/{target} "
                                f"→ mở khóa thử rồng khác (unlock #{unlock_n + 1}/3)"
                            )
                            self._taytuy_server_sync_wait.pop(event_name, None)
                            self._taytuy_sync_wait_since.pop(event_name, None)
                            self._taytuy_action_counts["__current__"] = 0
                            self._taytuy_unlock_retries[event_name] = unlock_n + 1
                            # Xóa selection cũ để taytuy_ctrl chọn rồng khác.
                            tt = self.controllers.get("taytuy")
                            if tt is not None:
                                try:
                                    tt._selected_id = None
                                except Exception:
                                    pass
                        elif waited >= 25.0 and unlock_n >= 3:
                            # Hết lượt unlock mà server vẫn 0 → DEFER 10 phút, làm task khác.
                            self.log_msg(
                                f"  ⏭️ TayTuyRong: đã thử {unlock_n} lần unlock, server vẫn 0/{target} "
                                "→ DEFER 10 phút, chuyển sang task khác"
                            )
                            self._deferred_until["TayTuyRong"] = time.time() + 600.0
                            self._taytuy_server_sync_wait.pop(event_name, None)
                            self._taytuy_sync_wait_since.pop(event_name, None)
                            self._taytuy_action_counts.pop("__current__", None)
                            continue  # outer while: pick next non-deferred task
                        else:
                            self._taytuy_action_counts["__current__"] = max(
                                target, int(self._taytuy_action_counts.get("__current__", 0) or 0)
                            )

                    self.log_msg(
                        f"🔒 TASK HIỆN TẠI: {current_key} {cur}/{target} — "
                        f"không chuyển sang task khác cho tới khi server xác nhận xong + claim"
                    )

                    # Inner loop: KHÓA ở current_key. Không iterate qua `pending[1:]`.
                    stagnant = 0
                    wait_sec = 2.0
                    last_state_log = 0.0
                    unlocked_for_retry = False  # True khi mở khóa TayTuy để thử rồng khác
                    while True:
                        # Reload the current task before every attempt so a concurrent
                        # server update can finish the task without another action.
                        latest_specs = self.fetch_pending_specs(event_name, verbose=False)
                        if self.last_flow_error:
                            return False
                        latest = latest_specs.get(current_key)

                        # Task disappeared = normally claimed/removed by server. Verify
                        # via fresh full list, then continue only when no longer pending.
                        if latest is None:
                            refreshed = self.fetch_pending_specs(event_name, verbose=False)
                            if self.last_flow_error:
                                return False
                            if current_key not in refreshed:
                                self.log_msg(f"✓ {current_key}: server đã bỏ khỏi danh sách pending")
                                break
                            latest = refreshed.get(current_key)

                        lcur = int(latest.get("dalam", 0) or 0) if latest else 0
                        ltarget = max(target, int(latest.get("maxnhiemvu", target) or target)) if latest else target
                        if latest and (bool(latest.get("done")) or lcur >= ltarget):
                            self.log_msg(f"✓ {current_key}: đạt {lcur}/{ltarget} → chuẩn bị nhận quà")
                            if self.claim_mission(event_name, current_key):
                                if current_key == "TayTuyRong":
                                    self._taytuy_server_sync_wait.pop(event_name, None)
                                    self._taytuy_sync_wait_since.pop(event_name, None)
                                    self._taytuy_unlock_retries.pop(event_name, None)
                                    self._taytuy_action_counts.pop("__current__", None)
                                break
                            self.log_msg(f"  ⚠ chưa claim được {current_key} — GIỮ NGUYÊN task hiện tại")
                            time.sleep(2.5)
                            continue

                        cur = lcur
                        target = ltarget

                        # Chỉ task Boss được phép defer khi CHƯA tới giờ. Các task
                        # khác vẫn giữ strict serial, không được nhảy qua.
                        if self._boss_not_due(current_key):
                            now = time.time()
                            if now - self._boss_gate_log_at.get(current_key, 0.0) >= 300.0:
                                mode = self._boss_mode_for_mission(current_key)
                                self.log_msg(
                                    f"  ⏰ {current_key}: Boss mode={mode} chưa tới giờ "
                                    f"→ không đánh, task Boss được defer"
                                )
                                self._boss_gate_log_at[current_key] = now
                            # Nếu còn task thường, outer loop sẽ chọn task kế tiếp.
                            # Nếu chỉ còn Boss, giữ chờ cho tới khi tới giờ.
                            has_non_deferred_task = any(
                                key != current_key and not self._boss_not_due(key)
                                for key in pending
                            )
                            if has_non_deferred_task:
                                break
                            time.sleep(10.0)
                            continue

                        # Chỉ Boss task đang đúng giờ mới được vào flow Boss.
                        if self._boss_mode_for_mission(current_key):
                            if not self._boss_before_mission(current_key):
                                now = time.time()
                                if now - self._mission_wait_log_at.get(current_key, 0.0) >= 30.0:
                                    self.log_msg(f"  ⏳ {current_key}: chưa vào Boss được — giữ đúng task Boss")
                                    self._mission_wait_log_at[current_key] = now
                                time.sleep(5.0)
                                continue

                        self.current_mission_attempts += 1
                        # Nếu Tay Tủy đã đủ số ACK theo target nhưng backend chưa
                        # phản ánh GetData/claim, chỉ chờ đồng bộ; tuyệt đối không
                        # gửi thêm TayTuy gây lặp vô hạn — trừ khi đã quá timeout
                        # (xử lý unlock ở outer gate phía trên).
                        if current_key == "TayTuyRong" and int(self._taytuy_action_counts.get("__current__", 0)) >= target:
                            wait_since = float(self._taytuy_sync_wait_since.get(event_name) or 0.0)
                            waited = (time.time() - wait_since) if wait_since > 0 else 0.0
                            unlock_n = int(self._taytuy_unlock_retries.get(event_name, 0) or 0)
                            if waited >= 25.0 and unlock_n < 3:
                                # Đánh dấu để outer loop mở khóa + thử lại, KHÔNG coi là xong task.
                                unlocked_for_retry = True
                                break
                            if waited >= 25.0 and unlock_n >= 3:
                                self.log_msg(
                                    f"  ⏭️ {current_key}: max unlock, server vẫn {cur}/{target} "
                                    "→ DEFER 10 phút"
                                )
                                self._deferred_until["TayTuyRong"] = time.time() + 600.0
                                self._taytuy_server_sync_wait.pop(event_name, None)
                                self._taytuy_sync_wait_since.pop(event_name, None)
                                self._taytuy_action_counts.pop("__current__", None)
                                unlocked_for_retry = True
                                break
                            now = time.time()
                            if now - self._mission_wait_log_at.get(current_key, 0.0) >= 5.0:
                                self.log_msg(
                                    f"  ⏳ {current_key}: đã ACK đủ {target} lượt nhưng server chưa xác nhận "
                                    f"(chờ {waited:.0f}s) → chỉ đồng bộ/claim, không thực hiện thêm Tẩy Tủy"
                                )
                                self._mission_wait_log_at[current_key] = now
                            time.sleep(2.0)
                            continue
                        action_ok = False
                        try:
                            action_ok = bool(self.execute_mission(current_key))
                        except Exception as e:
                            self.log_msg(f"  Action {current_key} lỗi: {e}")

                        # Sau MỖI action thành công phải load lại danh sách nhiệm vụ
                        # ngay lập tức. Không chờ nhiều giây rồi mới retry, và không
                        # coi ACK của controller là bằng chứng nhiệm vụ đã tăng.
                        if action_ok:
                            if current_key == "TayTuyRong":
                                # Có ACK/action thật → reset chuỗi "server rỗng".
                                self._taytuy_empty_streak = 0
                            self.log_msg(f"  🔄 {current_key}: action ACK OK → load lại nhiệm vụ ngay")
                            if current_key == "TayTuyRong":
                                # ACK của TayTuy chỉ xác nhận thao tác TayTuy thành công.
                                # KHÔNG được coi đó là bằng chứng nhiệm vụ Event đã hoàn thành.
                                # Chỉ GetData/Event server đạt target mới được phép claim.
                                # Bộ đếm ACK chỉ là khóa chống spam khi GetData còn stale.
                                n = int(self._taytuy_action_counts.get("__current__", 0)) + 1
                                self._taytuy_action_counts["__current__"] = n
                                # Persist the lock across a later run_event_flow()
                                # invocation (daily scheduler/UI can invoke it again).
                                self._taytuy_server_sync_wait[event_name] = True
                                if event_name not in self._taytuy_sync_wait_since:
                                    self._taytuy_sync_wait_since[event_name] = time.time()
                                if n >= target:
                                    self.log_msg(
                                        f"  🧬 TayTuyRong: ACK đủ {n}/{target}, "
                                        "nhưng CHƯA tính hoàn thành → bắt buộc chờ Event server báo đủ rồi mới claim"
                                    )
                            verify = self.fetch_pending_specs(event_name, verbose=False)
                            # Một số push/ghi trạng thái event có thể tới chậm hơn ACK
                            # một nhịp rất ngắn; đọc lần 2 nhưng vẫn KHÔNG thực hiện
                            # action thứ hai trước khi đã kiểm tra server.
                            if not self.last_flow_error:
                                probe = verify.get(current_key) if verify else None
                                probe_cur = int(probe.get("dalam", 0) or 0) if probe else 0
                                if probe is not None and probe_cur <= cur:
                                    # Một số nhiệm vụ cập nhật progress bất đồng bộ.
                                    # Vẫn giữ strict-serial, nhưng cho server thêm vài nhịp
                                    # để ghi quest trước khi kết luận action bị vô hiệu.
                                    extra_delays = (0.55, 1.0, 1.8) if current_key in ("TayTuyRong", "DanhRongBanBe") else (0.5,)
                                    for extra_delay in extra_delays:
                                        time.sleep(extra_delay)
                                        verify = self.fetch_pending_specs(event_name, verbose=False)
                                        if self.last_flow_error:
                                            break
                                        probe = verify.get(current_key) if verify else None
                                        probe_cur = int(probe.get("dalam", 0) or 0) if probe else 0
                                        if probe is None or probe_cur > cur:
                                            break
                        else:
                            time.sleep(min(3.0, wait_sec))
                            verify = self.fetch_pending_specs(event_name, verbose=False)
                            # XemRongTayTuy rỗng liên tục là trường hợp khác với
                            # action lỗi thông thường: retry vô hạn chỉ spam socket.
                            # Controller đã tự thử SendRequest + SendRequest2 và ghi
                            # _last_xem_empty=True khi cả hai đều không tìm thấy rồng.
                            if current_key == "TayTuyRong":
                                tt = self.controllers.get("taytuy")
                                empty_now = bool(getattr(tt, "_last_xem_empty", False)) if tt else False
                                if empty_now:
                                    self._taytuy_empty_streak += 1
                                    if self._taytuy_empty_streak >= 3:
                                        self.log_msg(
                                            f"  ⏭️ {current_key}: XemRongTayTuy rỗng "
                                            f"{self._taytuy_empty_streak} lần "
                                            "→ server không có rồng để tẩy, DEFER 10 phút"
                                        )
                                        self._deferred_until["TayTuyRong"] = time.time() + 600.0
                                        self._taytuy_empty_streak = 0
                                        unlocked_for_retry = True
                                        break
                                else:
                                    self._taytuy_empty_streak = 0
                            else:
                                self._taytuy_empty_streak = 0
                        if self.last_flow_error:
                            return False
                        after = verify.get(current_key)

                        if after is None:
                            # Current task has disappeared. Full refresh confirms before move.
                            full = self.fetch_pending_specs(event_name, verbose=False)
                            if self.last_flow_error:
                                return False
                            if current_key not in full:
                                self.log_msg(f"✓ {current_key}: hoàn thành phía server")
                                break
                            after = full.get(current_key)

                        new_cur = int(after.get("dalam", 0) or 0) if after else 0
                        new_target = max(target, int(after.get("maxnhiemvu", target) or target)) if after else target
                        target = new_target

                        if new_cur > cur:
                            self.log_msg(f"  ↳ {current_key}: server tăng tiến độ {cur}/{target} → {new_cur}/{target}")
                            stagnant = 0
                            wait_sec = 2.0
                            cur = new_cur
                            if current_key == "TayTuyRong":
                                # Server đã ghi nhận → bỏ khóa chờ sync.
                                self._taytuy_server_sync_wait.pop(event_name, None)
                                self._taytuy_sync_wait_since.pop(event_name, None)
                                self._taytuy_unlock_retries.pop(event_name, None)
                            if new_cur >= target:
                                if self.claim_mission(event_name, current_key):
                                    break
                            continue

                        # No progress: stay on the same task and back off instead of
                        # switching to the next task.
                        stagnant += 1
                        # Đánh bạn bè: vào nhà nhiều lần mà Event không tăng → defer
                        # để làm ChucPhuc / task khác, tránh kẹt vô hạn.
                        if current_key == "DanhRongBanBe" and stagnant >= 6:
                            self.log_msg(
                                f"  ⏭️ DanhRongBanBe: stagnant={stagnant}, server vẫn "
                                f"{new_cur}/{target} → DEFER 10 phút, chuyển task khác"
                            )
                            self._deferred_until["DanhRongBanBe"] = time.time() + 600.0
                            unlocked_for_retry = True
                            break
                        if current_key in ("ChoThanLongUongSua", "ChoHoaThanLongUongSua") and stagnant >= 5:
                            self.log_msg(
                                f"  ⏭️ {current_key}: stagnant={stagnant}, server vẫn "
                                f"{new_cur}/{target} → DEFER 10 phút (protocol sữa có thể thiếu capture)"
                            )
                            self._deferred_until[current_key] = time.time() + 600.0
                            unlocked_for_retry = True
                            break
                        wait_sec = min(30.0, wait_sec * 1.5 if stagnant > 1 else wait_sec)
                        now = time.time()
                        if now - last_state_log >= 20.0:
                            self.log_msg(
                                f"  ⏳ {current_key}: server vẫn {new_cur}/{target} — "
                                f"giữ task hiện tại (stagnant={stagnant}), retry sau {wait_sec:.1f}s"
                            )
                            last_state_log = now
                        time.sleep(wait_sec)

                    # Nếu vừa mở khóa TayTuy để thử rồng khác → quay outer loop,
                    # KHÔNG log "ĐÃ XONG + CLAIM".
                    if unlocked_for_retry:
                        continue

                    # Current task is claimed/finished. Refresh before selecting the next task.
                    refreshed = self.fetch_pending_specs(event_name, verbose=False)
                    if self.last_flow_error:
                        return False
                    next_pending = [k for k, v in refreshed.items() if not v.get("done")]
                    self.last_pending = list(next_pending)
                    if next_pending:
                        self.log_msg(
                            f"✅ ĐÃ XONG + CLAIM {current_key} → chỉ bây giờ mới chuyển task kế: {next_pending[0]}"
                        )
                    else:
                        self.log_msg(f"✅ ĐÃ XONG + CLAIM {current_key} → không còn task pending")

            except Exception as e:
                self.last_flow_error = True
                self.log_msg(f"Lỗi {event_name}: {e}")
                return False

