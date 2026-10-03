# core/taytuy.py
"""
Tẩy Tủy và Hóa Bụi là HAI thao tác khác nhau.

- Tẩy Tủy: ChonRongTayTuy -> TayTuy, tiêu Bụi Rồng và thay đổi chỉ số.
- Hóa Bụi: XemHoaBuiRong -> HoaBuiRong, biến rồng thành Bụi Rồng.

Không dùng flow Hóa Bụi để thực hiện nhiệm vụ Tẩy Tủy.
"""
import time


def _norm_dragon(raw, source=""):
    if not isinstance(raw, dict):
        return None
    rid = raw.get("id")
    if not rid:
        return None
    d = dict(raw)
    d["id"] = str(rid)
    d["_source"] = source
    if not d.get("nameobject"):
        d["nameobject"] = d.get("namerong") or d.get("name") or "?"
    if not d.get("nameitem"):
        d["nameitem"] = d.get("nameitem") or ""
    if d.get("islandIndex") is None and d.get("dao") is not None:
        d["islandIndex"] = d.get("dao")
    return d


def _unwrap(res):
    """Unwrap Socket.IO ACK; giữ nguyên dict có rongdao/status."""
    if isinstance(res, list) and res:
        res = res[0]
    if isinstance(res, dict):
        # Ưu tiên giữ dict đã có rongdao (capture XemRongTayTuy).
        if isinstance(res.get("rongdao"), list):
            return res
        for k in ("data", "result", "payload"):
            inner = res.get(k)
            if isinstance(inner, dict):
                if isinstance(inner.get("rongdao"), list):
                    return inner
                if "status" in inner or "id" in inner or "msg" in inner:
                    return inner
    return res


def _extract_rongdao(res):
    """Lấy list rongdao từ ACK XemRongTayTuy (nhiều lớp bọc)."""
    import json as _json

    def _decode(v):
        for _ in range(5):
            if isinstance(v, bytes):
                try:
                    v = v.decode("utf-8")
                except Exception:
                    return v
            if not isinstance(v, str):
                return v
            s = v.strip()
            if not s:
                return v
            try:
                v = _json.loads(s)
            except Exception:
                return v
        return v

    def _is_dragon(x):
        if not isinstance(x, dict):
            return False
        if not x.get("id"):
            return False
        # Tránh nhầm ACK wrapper; rồng thật thường có ít nhất một
        # trường mô tả rồng.
        return any(k in x for k in ("nameobject", "namerong", "sao", "level", "he"))

    def _walk(obj, depth=0):
        if depth > 8:
            return None
        obj = _decode(obj)
        if isinstance(obj, dict):
            arr = _decode(obj.get("rongdao"))
            if isinstance(arr, list):
                dragons = [x for x in arr if _is_dragon(x)]
                if dragons:
                    return dragons
            # Recurse vào mọi value để xử lý wrapper/data/result/payload lồng nhau.
            for v in obj.values():
                got = _walk(v, depth + 1)
                if got:
                    return got
        elif isinstance(obj, list):
            dragons = [x for x in obj if _is_dragon(x)]
            if dragons:
                return dragons
            for v in obj:
                got = _walk(v, depth + 1)
                if got:
                    return got
        return None

    got = _walk(res)
    return got or []


class TayTuyController:
    def __init__(self, socket_client, stats, log_fn=print, island_ctrl=None, lai_ctrl=None):
        self.sc = socket_client
        self.stats = stats
        self.log = log_fn
        self.island_ctrl = island_ctrl
        self.lai_ctrl = lai_ctrl
        self.taytuy_list = []
        self.last_response = None
        self.last_thongbao = None
        # Hai nhóm trạng thái độc lập:
        # - _hoa_bui_done_ids: rồng đã hóa bụi và thực sự không còn để Tẩy Tủy.
        # - _taytuy_used_ids: rồng đã Tẩy Tủy trong phiên event hiện tại, chỉ dùng
        #   để ưu tiên đổi sang rồng khác; khi hết lựa chọn thì cho phép dùng lại.
        self._hoa_bui_done_ids = set()
        self._taytuy_used_ids = set()
        self._fail_ids = set()
        self._server_rongdao_ids = set()
        # Trạng thái lần gọi XemRongTayTuy gần nhất: giúp event runner
        # phân biệt "server thật sự rỗng" với lỗi transport/parse.
        self._last_xem_empty = False
        self._last_xem_raw = None
        self._last_empty_log = 0.0
        self.event_pending_fn = None
        # Dragon được ChonRongTayTuy chọn. Capture cho thấy sau khi chọn,
        # client có thể gọi TayTuy nhiều lần liên tiếp mà không cần chọn lại.
        # Giữ selection để lần retry của nhiệm vụ event tiếp tục đúng session.
        self._selected_id = None

        if self.sc:
            self.sc.on_thongbao = self._on_thongbao

    def set_deps(self, island_ctrl=None, lai_ctrl=None):
        if island_ctrl is not None:
            self.island_ctrl = island_ctrl
        if lai_ctrl is not None:
            self.lai_ctrl = lai_ctrl

    def set_event_pending_fn(self, fn=None):
        """Cho daily/event runner báo khi TayTuyRong đang pending.

        Khi nhiệm vụ Tẩy Tủy còn pending, Auto Hóa Bụi phải nhường quyền để
        không ăn mất rồng cuối cùng trước khi event kịp xử lý.
        """
        self.event_pending_fn = fn if callable(fn) else None

    def _taytuy_event_pending(self):
        try:
            return bool(self.event_pending_fn()) if self.event_pending_fn else False
        except Exception:
            return False

    def _on_thongbao(self, data):
        msg = None
        if isinstance(data, dict):
            tbnhanh = data.get("tbnhanh")
            if isinstance(tbnhanh, dict):
                msg = tbnhanh.get("tb")
            else:
                msg = data.get("msg") or data.get("tb")
        else:
            msg = data
        if msg:
            self.last_thongbao = msg

    def _remove_from_caches(self, rid):
        """Đánh dấu rồng đã Hóa Bụi và xóa khỏi cache vì nó đã biến mất thật."""
        rid = str(rid)
        self._hoa_bui_done_ids.add(rid)
        self._fail_ids.discard(rid)
        if str(self._selected_id or "") == rid:
            self._selected_id = None
        self.taytuy_list = [r for r in self.taytuy_list if str(r.get("id")) != rid]

        bag = getattr(self.stats, "bag_dragons", None)
        if isinstance(bag, list):
            self.stats.bag_dragons = [
                r for r in bag
                if not (isinstance(r, dict) and str(r.get("id")) == rid)
            ]

        if self.island_ctrl and getattr(self.island_ctrl, "islands_cache", None):
            for dao, rongs in list(self.island_ctrl.islands_cache.items()):
                if not isinstance(rongs, list):
                    continue
                self.island_ctrl.islands_cache[dao] = [
                    r for r in rongs
                    if not (isinstance(r, dict) and str(r.get("id")) == rid)
                ]

        if self.lai_ctrl and getattr(self.lai_ctrl, "lai_list", None):
            self.lai_ctrl.lai_list = [
                r for r in self.lai_ctrl.lai_list
                if not (isinstance(r, dict) and str(r.get("id")) == rid)
            ]

    def _merge_from_sources(self):
        by_id = {}

        def add(raw, source):
            d = _norm_dragon(raw, source)
            if not d:
                return
            rid = d["id"]
            if rid in self._hoa_bui_done_ids:
                return
            if rid not in by_id:
                by_id[rid] = d
            else:
                old = by_id[rid]
                srcs = set(str(old.get("_source", "")).split("+"))
                srcs.add(source)
                old["_source"] = "+".join(s for s in srcs if s)
                for k, v in d.items():
                    if k == "_source":
                        continue
                    if k not in old or old.get(k) in (None, ""):
                        old[k] = v

        for r in self.taytuy_list:
            add(r, "server")

        bag = getattr(self.stats, "bag_dragons", None) or []
        for r in bag:
            add(r, "kho")

        if self.island_ctrl:
            for dao, rongs in (self.island_ctrl.islands_cache or {}).items():
                for r in rongs or []:
                    add(r, f"dao{dao}")

        if self.lai_ctrl:
            for r in getattr(self.lai_ctrl, "lai_list", None) or []:
                add(r, "lai")

        self.taytuy_list = list(by_id.values())
        return self.taytuy_list

    def refresh_list(self, timeout=5, refresh_lai=False):
        if not self.sc or not self.sc.is_connected():
            self.log("❌ Socket không connected")
            self.taytuy_list = []
            self._merge_from_sources()
            return len(self.taytuy_list) > 0

        server_list = []
        self.log("📋 Tải list + gộp Kho/Đảo/Lai...")

        try:
            if hasattr(self.sc, "request"):
                res = self.sc.request("SendRequest", {
                    "class": "TayTuy",
                    "method": "XemRongTayTuy",
                }, timeout=timeout)
            else:
                res = self.sc.call("SendRequest", {
                    "class": "TayTuy",
                    "method": "XemRongTayTuy",
                }, timeout=timeout)
            server_list = _extract_rongdao(res)
            # Retry 1 lần bằng SendRequest2 nếu rỗng (một số build chỉ trả qua transport này).
            if not server_list and hasattr(self.sc, "request"):
                res2 = self.sc.request("SendRequest2", {
                    "class": "TayTuy",
                    "method": "XemRongTayTuy",
                }, timeout=timeout)
                server_list = _extract_rongdao(res2)
        except Exception as e:
            self.log(f"  server list lỗi: {e}")

        # XemRongTayTuy là nguồn authoritative. Nếu server vừa trả lại một
        # ID mà phiên trước từng đánh dấu là "đã Hóa Bụi", thì trạng thái cache
        # đã cũ (hoặc server đã đưa rồng trở lại pool Tẩy Tủy). Gỡ ID khỏi
        # _hoa_bui_done_ids trước khi merge để không biến thành "gộp=0" giả.
        fresh_ids = {str(r.get("id")) for r in server_list if isinstance(r, dict) and r.get("id")}
        if fresh_ids:
            self._hoa_bui_done_ids.difference_update(fresh_ids)
        self.taytuy_list = list(server_list)
        self._server_rongdao_ids = set(fresh_ids)

        if refresh_lai and self.lai_ctrl:
            try:
                self.lai_ctrl.refresh_list(timeout=4)
            except Exception:
                pass

        bag_n = len(getattr(self.stats, "bag_dragons", None) or [])
        dao_n = 0
        if self.island_ctrl:
            for v in (self.island_ctrl.islands_cache or {}).values():
                dao_n += len(v or [])
        lai_n = len(getattr(self.lai_ctrl, "lai_list", None) or []) if self.lai_ctrl else 0

        # Chỉ merge kho/đảo/lai khi dùng cho Hóa Bụi / UI. Nhiệm vụ event Tẩy Tủy
        # PHẢI dùng đúng rongdao từ XemRongTayTuy — merge rồng ngoài list làm
        # ACK thành công nhưng Event server không ghi nhận tiến độ.
        if not self._taytuy_event_pending():
            self._merge_from_sources()
        elif not server_list:
            self.log(
                "⚠️ XemRongTayTuy trả rongdao rỗng — không merge kho/đảo "
                "(event chỉ tính rồng trong list server)"
            )

        self.log(
            f"📋 server={len(server_list)} kho={bag_n} đảo={dao_n} lai={lai_n} "
            f"| gộp={len(self.taytuy_list)} | đã hóa={len(self._hoa_bui_done_ids)} | "
            f"đã tẩy phiên={len(self._taytuy_used_ids)}"
        )
        return len(self.taytuy_list) > 0

    @staticmethod
    def _is_locked(r):
        v = r.get("lock")
        return v in (True, 1, "1", "true", "True", "yes", "YES")

    @staticmethod
    def _hiem_int(r):
        v = r.get("hiem")
        if v is None:
            return None
        try:
            return int(v)
        except Exception:
            s = str(v).lower()
            if "phổ" in s or "pho bien" in s or "phobien" in s or "thường" in s or "thuong" in s:
                return 0
            if "nguyên" in s or "nguyen" in s:
                return 4
            if "event" in s:
                return 3
            if "cực" in s or "cuc" in s:
                return 2
            if "hiếm" in s or "hiem" in s:
                return 1
            return None

    def _is_keep(self, r):
        """Giữ hiếm/cực hiếm/event/nguyên liệu/khóa. Phổ biến (hiem=0) → hóa bụi."""
        if self._is_locked(r):
            return True
        nameitem = str(r.get("nameitem") or "")
        hiem_i = self._hiem_int(r)

        if "-NguyenLieu" in nameitem or hiem_i == 4:
            return True
        if "-Event" in nameitem or hiem_i == 3:
            return True
        if "-CucHiem" in nameitem or hiem_i == 2:
            return True
        if "-Hiem" in nameitem or hiem_i == 1:
            return True
        # Phổ biến / thường
        if "-PhoBien" in nameitem or "-Thuong" in nameitem or hiem_i == 0:
            return False
        return False

    def _dao_flag_for(self, r):
        src = str(r.get("_source") or "")
        parts = set(src.split("+"))
        if "kho" in parts:
            return "false"
        for p in parts:
            if p.startswith("dao") and p[3:].isdigit():
                return p[3:]
        if r.get("islandIndex") is not None and "kho" not in parts:
            return str(r.get("islandIndex"))
        return "false"

    def tay_tuy_once(self, timeout=8):
        """Một lượt Tẩy Tủy theo đúng capture:
        XemRongTayTuy -> ChonRongTayTuy -> TayTuy.

        Khi XemRongTayTuy rỗng, thử thêm SendRequest2 và log raw response
        có throttle để phân biệt lỗi parse/transport với server thực sự rỗng.
        """
        self.last_response = None
        self.last_thongbao = None
        self._selected_id = None
        self._last_xem_empty = False
        self._last_xem_raw = None
        if not self.sc or not self.sc.is_connected():
            self.log("❌ Socket không connected")
            return False

        def req(method, data=None, transport="SendRequest"):
            payload = {"class": "TayTuy", "method": method}
            if data is not None:
                payload["data"] = data
            if hasattr(self.sc, "request"):
                return self.sc.request(transport, payload, timeout=timeout)
            return self.sc.call(transport, payload, timeout=timeout)

        # 1) XemRongTayTuy — thử SendRequest trước.
        try:
            res_list = req("XemRongTayTuy")
        except Exception as e:
            self.log(f"❌ XemRongTayTuy lỗi: {e}")
            return False

        self._last_xem_raw = res_list
        server_list = _extract_rongdao(res_list)

        # Một số build/socket transport chỉ trả payload qua SendRequest2.
        if not server_list:
            try:
                res2 = req("XemRongTayTuy", transport="SendRequest2")
                self._last_xem_raw = res2
                server_list = _extract_rongdao(res2)
                if server_list:
                    res_list = res2
            except Exception as e:
                self.log(f"  XemRongTayTuy (SR2) lỗi: {e}")

        if not server_list:
            self._last_xem_empty = True
            now = time.time()
            if now - self._last_empty_log >= 30.0:
                self._last_empty_log = now
                try:
                    raw = self._last_xem_raw
                    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
                        raw = raw[0]
                    keys = list(raw.keys())[:15] if isinstance(raw, dict) else type(raw).__name__
                    self.log(
                        f"⚠️ XemRongTayTuy rỗng | raw keys={keys} | "
                        f"raw={str(raw)[:300]}"
                    )
                except Exception:
                    pass
                self.log("⚠️ XemRongTayTuy không có rồng để Tẩy Tủy")
            return False

        fresh_ids = {str(r.get("id")) for r in server_list if isinstance(r, dict) and r.get("id")}
        self._server_rongdao_ids = set(fresh_ids)
        self.taytuy_list = list(server_list)
        self._hoa_bui_done_ids.difference_update(fresh_ids)

        # Ưu tiên rồng chưa dùng trong phiên; hết rồng mới cho phép dùng lại.
        all_candidates = []
        for r in server_list:
            if not isinstance(r, dict):
                continue
            rid = str(r.get("id") or "")
            if rid and rid not in self._hoa_bui_done_ids:
                all_candidates.append(r)
        candidates = [r for r in all_candidates if str(r.get("id")) not in self._taytuy_used_ids] or all_candidates
        if not candidates:
            self.log("⚠️ Không có rồng hợp lệ để Tẩy Tủy")
            return False

        r = candidates[0]
        selected = str(r.get("id"))
        selected_name = r.get("nameobject") or r.get("namerong") or "?"
        self.log(f"🧬 XemRongTayTuy → Chọn {selected_name} ({selected})")

        # 2) ChonRongTayTuy {id}
        try:
            chon = req("ChonRongTayTuy", {"id": selected})
        except Exception as e:
            self.log(f"❌ ChonRongTayTuy lỗi: {e}")
            return False
        if not self._is_ok(chon):
            self.last_response = chon
            self.log(f"⚠️ ChonRongTayTuy bị từ chối: {chon}")
            return False

        self._selected_id = selected

        # 3) TayTuy {id, khoa:"null"}
        try:
            res = req("TayTuy", {"id": selected, "khoa": "null"})
        except Exception as e:
            self.log(f"❌ TayTuy lỗi: {e}")
            self._selected_id = None
            return False

        self.last_response = res
        if not self._is_ok(res):
            self.log(f"⚠️ TayTuy bị từ chối: {str(res)[:240]}")
            self._selected_id = None
            return False

        # Capture: ACK status=0 + data chỉ số mới. updateMoney/Thongbao là push
        # đi kèm, không dùng việc bắt push làm điều kiện thất bại.
        self._taytuy_used_ids.add(selected)
        self._selected_id = None
        self.log(f"✅ Tẩy Tủy thành công: {selected_name} ({selected})")
        time.sleep(0.2)
        return True

    def xem_hoa_bui_rong(self, dragon_id, dao_flag="false", timeout=5):
        if not self.sc or not self.sc.is_connected():
            return None
        try:
            if hasattr(self.sc, "request"):
                return self.sc.request("SendRequest", {
                    "class": "TayTuy",
                    "method": "XemHoaBuiRong",
                    "data": {"id": dragon_id, "dao": str(dao_flag)},
                }, timeout=timeout)
            return self.sc.call("SendRequest", {
                "class": "TayTuy",
                "method": "XemHoaBuiRong",
                "data": {"id": dragon_id, "dao": str(dao_flag)},
            }, timeout=timeout)
        except Exception:
            return None

    @staticmethod
    def _is_ok(res):
        res = _unwrap(res)
        if not isinstance(res, dict):
            return False
        st = res.get("status")
        if st in (0, "0", "ok", "OK", "thanhcong", "success", True):
            return True
        if str(st).lower() in ("0", "ok", "thanhcong", "success"):
            return True
        return False

    def hoa_bui(self, dragon_id, dao_flag="false", timeout=6):
        self.last_response = None
        self.last_thongbao = None
        rid = str(dragon_id)
        if rid in self._hoa_bui_done_ids:
            return True
        if not self.sc or not self.sc.is_connected():
            self.log("❌ Socket không connected")
            return False

        self.xem_hoa_bui_rong(rid, dao_flag=dao_flag, timeout=timeout)
        time.sleep(0.25)

        payloads = [
            {"id": rid, "dao": str(dao_flag)},
            {"id": rid},
        ]
        if str(dao_flag) not in ("false", "False", ""):
            payloads.append({"id": rid, "dao": "false"})

        last_res = None
        for data in payloads:
            try:
                if hasattr(self.sc, "request"):
                    res = self.sc.request("SendRequest", {
                        "class": "TayTuy",
                        "method": "HoaBuiRong",
                        "data": data,
                    }, timeout=timeout)
                else:
                    res = self.sc.call("SendRequest", {
                        "class": "TayTuy",
                        "method": "HoaBuiRong",
                        "data": data,
                    }, timeout=timeout)
            except Exception as e:
                self.log(f"❌ Lỗi hóa bụi {rid}: {e}")
                continue
            last_res = res
            self.last_response = res
            if self._is_ok(res):
                self._remove_from_caches(rid)
                return True
            time.sleep(0.15)

        # Fail thật — KHÔNG đánh dấu đã mất, lần sau còn thử lại
        self._fail_ids.add(rid)
        tb = self.last_thongbao or ""
        self.log(f"  ⚠️ Fail {rid} dao={dao_flag} res={last_res} tb={tb}")
        return False

    def auto_hoa_bui_thong_minh(self, delay=0.5, protect_ids=None):
        self.log("=== QUÉT HÓA BỤI RỒNG PHỔ BIẾN / THƯỜNG ===")
        # Khi event đang có TayTuyRong pending, tuyệt đối nhường rồng cho event.
        if self._taytuy_event_pending():
            self.log("🔒 Event đang có TayTuyRong pending → tạm dừng Hóa Bụi để không ăn mất rồng")
            return 0
        # cho phép thử lại các id fail phiên trước
        self._fail_ids.clear()

        if not self.refresh_list(refresh_lai=False):
            self.log("Không còn rồng trong list (hoặc chỉ còn loại giữ).")
            return 0

        protect = set(str(x) for x in (protect_ids or []) if x)
        count = 0
        keep_n = 0
        skip_n = 0
        bag_pb = 0

        candidates = []
        for r in list(self.taytuy_list):
            rid = str(r.get("id") or "")
            if not rid:
                skip_n += 1
                continue
            if rid in self._hoa_bui_done_ids:
                skip_n += 1
                continue
            if rid in protect:
                skip_n += 1
                continue
            if self._is_keep(r):
                keep_n += 1
                continue
            if "kho" in str(r.get("_source") or ""):
                bag_pb += 1
            candidates.append(r)

        # Ưu tiên hóa rồng trong TÚI trước (đúng thứ user thấy trong game)
        def _prio(r):
            src = str(r.get("_source") or "")
            if "kho" in src:
                return 0
            if src.startswith("dao") or "dao" in src:
                return 1
            return 2
        candidates.sort(key=_prio)

        self.log(
            f"Giữ (hiếm/event/NL/khóa)={keep_n} | bỏ qua={skip_n} | "
            f"sẽ hóa={len(candidates)} | túi phổ biến={bag_pb}"
        )

        if delay < 0.4:
            delay = 0.4

        for r in candidates:
            rid = str(r.get("id"))
            if rid in self._hoa_bui_done_ids:
                continue
            name = r.get("nameobject") or "?"
            src = r.get("_source") or ""
            dao_flag = self._dao_flag_for(r)
            hiem = self._hiem_int(r)
            ok = self.hoa_bui(rid, dao_flag=dao_flag)
            if ok:
                count += 1
                self.log(f"✅ Hóa bụi {name} hiem={hiem} ({rid}) src={src}")
            else:
                self.log(f"⏭️ Fail {name} hiem={hiem} ({rid}) src={src} — sẽ thử lại lần sau")
            time.sleep(delay)

        self.log(
            f"=== XONG — hóa bụi OK {count}/{len(candidates)} | "
            f"tổng đã hóa phiên={len(self._hoa_bui_done_ids)} | fail={len(self._fail_ids)} ==="
        )
        return count
