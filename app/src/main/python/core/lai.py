# core/lai.py
import json
import time
import uuid


class LaiController:
    def __init__(self, socket_client, stats, log_fn=print):
        self.sc = socket_client
        self.stats = stats
        self.log = log_fn
        self.lai_list = []
        self.last_lai_item = None
        self.last_response = None
        self.last_thongbao = None
        self.lai_targets = []
        self.sc.on_itemlairong = self._on_itemlairong
        self.sc.on_laithanhcong = self._on_laithanhcong
        self.sc.on_thongbao = self._on_thongbao
        # 4315: server trả danh sách rồng có thể lai ra từ đúng cặp đang chọn.
        try:
            self.sc.on_lai_targets = self._on_lai_targets
        except Exception:
            pass

    def _on_itemlairong(self, data):
        itemlai = data.get("itemlai") if isinstance(data, dict) else None
        if isinstance(itemlai, dict):
            arr = itemlai.get("ItemrongLai") or []
            self.lai_list = [x for x in arr if isinstance(x, dict) and x.get("id")]
            self.log(f"📋 Có {len(self.lai_list)} rồng có thể lai")

    def _on_laithanhcong(self, data):
        self.last_response = data
        if isinstance(data, dict):
            add = data.get("additem")
            if isinstance(add, dict):
                self.last_lai_item = add
                self.log(f"✅ Rồng mới: {add.get('nameobject')} {add.get('sao')}* "
                         f"hiem={add.get('hiem', 0)} id={add.get('id')}")

    @staticmethod
    def _decode_json(value):
        for _ in range(4):
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
    def _extract_lai_target_body(cls, response):
        """Extract data from Main.xemRonglai ACK (4390)."""
        value = cls._decode_json(response)
        # ACK may be [{status:0,data:{...}}], or a dict, or nested JSON.
        candidates = []
        if isinstance(value, list):
            candidates.extend(value)
        else:
            candidates.append(value)
        for item in list(candidates):
            item = cls._decode_json(item)
            if isinstance(item, dict):
                data = item.get("data")
                if isinstance(data, dict) and isinstance(data.get("ronglai"), list):
                    return data
                if isinstance(item.get("ronglai"), list):
                    return item
        return None

    def request_lai_targets(self, dragon1, dragon2, timeout=8):
        """Call the real protocol used by the game when two parents are selected.

        Capture: Main.xemRonglai via SendRequest, response 4390.
        herong = he1 + he2 + level1 + level2 + namerong1 + namerong2.
        """
        self.lai_targets = []
        self.lai_target_item = None
        if not self.sc or not self.sc.is_connected():
            self.log("❌ Socket không connected")
            return []

        d1 = dragon1 or {}
        d2 = dragon2 or {}
        name1 = str(d1.get("namerong") or d1.get("nameobject") or "").strip()
        name2 = str(d2.get("namerong") or d2.get("nameobject") or "").strip()
        he1 = str(d1.get("he") or "").strip()
        he2 = str(d2.get("he") or "").strip()
        lv1 = str(d1.get("level") if d1.get("level") is not None else "").strip()
        lv2 = str(d2.get("level") if d2.get("level") is not None else "").strip()

        # Exact format observed in user's capture:
        # Dat-Cay+Lua-Sam+20+7+Rong2DauDatCay+RongLuaSam
        herong = "+".join((he1, he2, lv1, lv2, name1, name2))
        payload = {
            "class": "Main",
            "method": "xemRonglai",
            "data": {
                "herong": herong,
                "namerong1": name1,
                "namerong2": name2,
            },
        }
        self.log(f"→ Main.xemRonglai: {name1} x {name2}")
        self.log(f"  herong={herong}")
        try:
            response = self.sc.request("SendRequest", payload, timeout=timeout)
        except Exception as e:
            self.log(f"❌ xemRonglai lỗi: {e}")
            return []

        body = self._extract_lai_target_body(response)
        if not body:
            self.log("❌ Không đọc được response xemRonglai")
            return []

        seen = []
        for x in body.get("ronglai") or []:
            name = str(x or "").strip()
            if name and name not in seen:
                seen.append(name)
        self.lai_targets = seen
        self.lai_target_item = body.get("canitem")
        if seen:
            self.log(f"🎯 4390 — Có {len(seen)} rồng có thể lai: {', '.join(seen)}")
        else:
            self.log("⚠️ 4390 — Server không trả rồng có thể lai")
        if self.lai_target_item:
            self.log(f"  🧪 Nguyên liệu: {self.lai_target_item}")
        return seen

    def _on_lai_targets(self, data):
        """Parse packet 4315: {data:{ronglai:[...],canitem:{...}}}."""
        obj = data
        if isinstance(obj, (list, tuple)) and obj:
            obj = obj[0]
        if not isinstance(obj, dict):
            return
        body = obj.get("data") if isinstance(obj.get("data"), dict) else obj
        arr = body.get("ronglai") if isinstance(body, dict) else None
        if not isinstance(arr, list):
            return
        seen = []
        for x in arr:
            name = str(x or "").strip()
            if name and name not in seen:
                seen.append(name)
        self.lai_targets = seen
        self.lai_target_item = body.get("canitem") if isinstance(body, dict) else None
        if seen:
            self.log(f"🎯 Gợi ý rồng lai: {', '.join(seen)}")

    def _on_thongbao(self, data):
        msg = None
        if isinstance(data, dict):
            tbnhanh = data.get("tbnhanh")
            if isinstance(tbnhanh, dict):
                msg = tbnhanh.get("tb")
            else:
                msg = data.get("msg")
        else:
            msg = data
        if msg:
            self.last_thongbao = msg
            self.log(f"📢 {msg}")

    def refresh_list(self, timeout=5):
        self.lai_list = []
        if not self.sc or not self.sc.is_connected():
            self.log("❌ Socket không connected")
            return False
        if not self.sc.is_logged_in():
            self.log("⏳ Chưa login — chờ 3s rồi thử lại...")
            time.sleep(3)
            if not self.sc.is_logged_in():
                self.log("❌ Vẫn chưa login")
                return False
        self.sc.emit("GetItemRong", {"mode": "false", "requestId": uuid.uuid4().hex})
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.lai_list:
                break
            time.sleep(0.3)
        return len(self.lai_list) > 0

    def lai(self, id1, id2, timeout=10):
        self.last_lai_item = None
        self.last_response = None
        self.last_thongbao = None
        if not self.sc or not self.sc.is_connected():
            self.log("❌ Socket không connected")
            return None
        payload = f"{id1}!{id2}"
        self.log(f"→ LaiRong '{payload}'")
        self.sc.emit("LaiRong", payload)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.last_response is not None or self.last_thongbao is not None:
                break
            time.sleep(0.3)

        if self.last_lai_item:
            self.stats.add_dragon(self.last_lai_item)
            return self.last_lai_item
        elif self.last_thongbao:
            self.log(f"❌ Server báo: {self.last_thongbao}")
            return None
        else:
            self.log("❌ Timeout khi chờ kết quả lai")
            return None

    def ban(self, dragon_id):
        if not self.sc or not self.sc.is_connected():
            return
        self.log(f"→ BanRong '{dragon_id}'")
        self.sc.emit("BanRong", dragon_id)

    def chuc_phuc(self):
        if not self.sc or not self.sc.is_connected():
            return
        self.log("→ chucphuc")
        self.sc.emit("chucphuc")

    def cat(self, dragon_id):
        if not self.sc or not self.sc.is_connected():
            return
        self.log(f"→ Cất rồng '{dragon_id}'")
        for ev in ("CatRong", "Catrong", "CatRongVaoKho"):
            for p in ({"id": dragon_id, "dao": "0"}, {"id": dragon_id}, {"idrong": dragon_id}):
                try:
                    self.sc.emit(ev, p)
                except Exception:
                    pass
                time.sleep(0.2)

    def _lai_event_common(self, chuc_phuc_ngay=False, taytuy_ctrl=None):
        """Flow chung cho nhiệm vụ lai sự kiện; khác nhau ở thời điểm Chúc Phúc."""
        self.refresh_list(timeout=6)
        lst = [r for r in (self.lai_list or []) if isinstance(r, dict) and r.get("id")]
        if len(lst) < 2:
            msg = "Không đủ 2 rồng để lai cho nhiệm vụ Chúc Phúc" if chuc_phuc_ngay else "Không đủ 2 rồng để lai"
            self.log(f"❌ {msg}")
            return False

        def sao(r):
            try:
                return int(r.get("sao") or 0)
            except Exception:
                return 99

        lst.sort(key=lambda r: (sao(r), str(r.get("id"))))
        a, b = lst[0], lst[1]
        tag = "[Event Chúc Phúc]" if chuc_phuc_ngay else "[Event]"
        self.log(f"→ {tag} Lai: {a.get('nameobject')} {sao(a)}* x {b.get('nameobject')} {sao(b)}*")
        result = self.lai(a["id"], b["id"])
        if not result:
            return False

        try:
            hiem = int(result.get("hiem") if result.get("hiem") is not None else 0)
        except Exception:
            hiem = 0
        rid = result.get("id")
        name = result.get("nameobject") or "?"
        nameitem = str(result.get("nameitem") or result.get("namerong") or "")
        is_nguyen_lieu = "nguyenlieu" in nameitem.lower()

        if chuc_phuc_ngay:
            try:
                self.chuc_phuc()
                self.log(f"  ✨ Chúc phúc ngay: {name} hiem={hiem}")
                time.sleep(0.55)
            except Exception as e:
                self.log(f"  ⚠️ chúc phúc lỗi: {e}")
            keep = (hiem >= 1) or is_nguyen_lieu
        else:
            keep = hiem >= 1
            if keep:
                try:
                    self.chuc_phuc()
                except Exception:
                    pass
                time.sleep(0.6)

        if keep:
            label = "nguyên liệu" if is_nguyen_lieu else (
                "event" if hiem >= 3 else ("cực hiếm" if hiem >= 2 else "hiếm")
            )
            self.log(f"⭐ GIỮ rồng lai {name} hiem={hiem} ({label})")
            if rid:
                self.cat(rid)
            if chuc_phuc_ngay:
                time.sleep(0.4)
            return True

        self.log(f"💨 Rồng lai thường {name} hiem={hiem} → {'BÁN' if chuc_phuc_ngay else 'bán'}")
        if rid:
            self.ban(rid)
        if chuc_phuc_ngay:
            time.sleep(0.4)
        return True

    def lai_event_lowest_stars(self, taytuy_ctrl=None):
        return self._lai_event_common(chuc_phuc_ngay=False, taytuy_ctrl=taytuy_ctrl)

    def lai_event_chuc_phuc(self, taytuy_ctrl=None):
        return self._lai_event_common(chuc_phuc_ngay=True, taytuy_ctrl=taytuy_ctrl)
