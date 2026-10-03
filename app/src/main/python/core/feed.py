# core/feed.py
import time


class FeedController:
    def __init__(self, socket_client, stats, log_fn=print):
        self.sc = socket_client
        self.stats = stats
        self.log = log_fn

    def tha_thuc_an(self, food_name="ThucAnThit", dao="0", timeout=8):
        if not self.sc or not self.sc.is_connected():
            self.log("❌ Socket không connected")
            return None

        payload = {
            "namethucan": food_name,
            "dangodao": str(dao),
            "clientRequestId": str(int(time.time() * 1000))[-6:],
        }
        self.log(f"→ [B1] ThaThucAn {food_name} dao={dao}")

        if hasattr(self.sc, "request"):
            result = self.sc.request("SendRequest", {
                "class": "DragonIsland",
                "method": "ThaThucAn",
                "data": payload,
            }, timeout=timeout)
        else:
            result = self.sc.call("SendRequest", {
                "class": "DragonIsland",
                "method": "ThaThucAn",
                "data": payload,
            }, timeout=timeout)

        if not result:
            self.log("❌ [B1] Không nhận response")
            return None

        if isinstance(result, list) and result:
            result = result[0]
        if not isinstance(result, dict):
            return None

        if not result.get("feedSessionId") or not result.get("foodDrops"):
            return None

        self.log(f"✅ [B1] feedSessionId OK, {len(result.get('foodDrops', []))} food drops")
        return result

    def rong_an(self, dragon_id, food_name, dao, feed_sid, food_id):
        payload = {
            "namerong": dragon_id,
            "namethucan": food_name,
            "dangodao": str(dao),
            "feedSessionId": feed_sid,
            "foodId": food_id,
        }
        try:
            self.sc.emit("SendRequest2", {
                "class": "DragonIsland",
                "method": "RongAn",
                "data": payload,
            })
        except Exception as e:
            self.log(f"emit err: {e}")
            return False
        time.sleep(1)
        return True


    @staticmethod
    def _status_ok(result):
        """Đọc status từ ACK có thể là dict hoặc list[dict]."""
        value = result
        if isinstance(value, list) and value:
            value = value[0]
        if isinstance(value, dict):
            st = value.get("status")
            if st in (0, "0", "ok", "success", "true", True):
                return True
            if st in (1, "1", "error", "fail", "false", False):
                return False
        return None

    def than_long_bu_sua(self, dragon_name, timeout=8):
        """Cho Hỏa/Tuyết Thần Long uống sữa.

        UI: chọn Thần Long → Bình sữa → Cho uống.
        Thử nhiều biến thể packet vì capture chưa có đúng nút Cho uống.
        """
        if not self.sc or not self.sc.is_connected():
            self.log("❌ [Event] Socket không connected")
            return False
        if dragon_name not in ("HoaThanLong", "TuyetThanLong"):
            self.log(f"❌ [Event] Tên Thần Long không hợp lệ: {dragon_name}")
            return False

        # Các biến thể data/method khả dĩ
        variants = [
            ("SendRequest", "Main", "ThanLongBuSua", {"name": dragon_name}),
            ("SendRequest", "Main", "ThanLongBuSua", {"name": dragon_name, "item": "BinhSua"}),
            ("SendRequest", "Main", "ThanLongBuSua", {"name": dragon_name, "binhsua": "BinhSua"}),
            ("SendRequest", "Main", "ThanLongBuSua", {"name": dragon_name, "vatpham": "BinhSua"}),
            ("SendRequest", "Main", "ChoThanLongUongSua", {"name": dragon_name}),
            ("SendRequest", "Main", "ChoThanLongUongSua", {"name": dragon_name, "item": "BinhSua"}),
            ("SendRequest2", "Main", "ThanLongBuSua", {"name": dragon_name, "item": "BinhSua"}),
        ]

        for transport, cls, method, data in variants:
            payload = {"class": cls, "method": method, "data": data}
            self.log(f"→ [Event] {method} {data}")
            try:
                if hasattr(self.sc, "request"):
                    result = self.sc.request(transport, payload, timeout=timeout)
                else:
                    result = self.sc.call(transport, payload, timeout=timeout)
            except Exception as e:
                self.log(f"  ⚠️ {method} lỗi: {e}")
                continue

            # Log gọn ACK để debug
            brief = result
            if isinstance(result, list) and result:
                brief = result[0]
            if isinstance(brief, dict):
                self.log(f"  🔎 ACK status={brief.get('status')} keys={list(brief.keys())[:8]}")
            else:
                self.log(f"  🔎 ACK: {str(result)[:120]}")

            ok = self._status_ok(result)
            if ok is True:
                # Kiểm tra có tiêu BinhSua không
                milk_spent = False
                try:
                    if hasattr(self.sc, "last_events"):
                        import time as _t
                        deadline = _t.time() + 1.2
                        before = max(0, len(self.sc.last_events) - 40)
                        while _t.time() < deadline:
                            with self.sc.lock:
                                tail = list(self.sc.last_events)[before:]
                            for _, ev, blob in tail:
                                if ev in ("updateMoney", "UpdateMoney", "Info") and isinstance(blob, dict):
                                    add = blob.get("additem")
                                    if not isinstance(add, dict):
                                        for container in (blob.get("data"), blob.get("info"), blob.get("updateMoney")):
                                            if isinstance(container, dict) and isinstance(container.get("additem"), dict):
                                                add = container.get("additem")
                                                break
                                    if isinstance(add, dict) and "Sua" in str(add.get("nameitem") or ""):
                                        self.log(f"  🍼 Tiêu {add.get('nameitem')}: {add.get('soluong')}")
                                        try:
                                            if int(str(add.get("soluong")).replace(",", "") or 0) < 0:
                                                milk_spent = True
                                        except Exception:
                                            pass
                            if milk_spent:
                                break
                            _t.sleep(0.1)
                except Exception:
                    pass

                if milk_spent:
                    self.log(f"✅ [Event] {dragon_name} uống sữa (đã tiêu Bình sữa)")
                    return True
                # status=0 nhưng không tiêu sữa → có thể no-op; thử biến thể kế
                self.log(f"  ⚠️ {method} status=0 nhưng không thấy tiêu Bình sữa — thử biến thể khác")
                continue
            if ok is False:
                self.log(f"  ❌ {method} từ chối: {str(result)[:160]}")
                continue
        self.log(f"❌ [Event] Không cho {dragon_name} uống sữa được (hết biến thể)")
        return False

    def cho_an_hang_loat(self, dragon_ids, food_name="ThucAnThit", dao="0"):
        if not dragon_ids:
            return 0
        self.log(f"═══ Cho {len(dragon_ids)} rồng ăn dao={dao} ═══")

        res = self.tha_thuc_an(food_name=food_name, dao=dao)
        if not res:
            return 0

        feed_sid = res.get("feedSessionId")
        drops = res.get("foodDrops") or []

        food_map = {d.get("targetDragonId"): d.get("foodId") for d in drops if d.get("targetDragonId") and d.get("foodId")}

        success = 0
        for i, rid in enumerate(dragon_ids, 1):
            fid = food_map.get(rid)
            if not fid:
                continue
            self.rong_an(rid, food_name, dao, feed_sid, fid)
            success += 1
            time.sleep(1.2)
        self.log(f"═══ Đã cho {success}/{len(dragon_ids)} rồng ăn ═══")

    def run_for_event(self, island_ctrl=None, food_name="ThucAnThit"):
        """Cho 1 đảo ăn vài rồng — đủ nhiệm vụ sự kiện ChoRongAn."""
        dao = "0"
        ids = []
        if island_ctrl:
            rongs = island_ctrl.get_rong_cua_dao(dao) or []
            if not rongs:
                try:
                    island_ctrl.load_dao(dao)
                except Exception:
                    pass
                rongs = island_ctrl.get_rong_cua_dao(dao) or []
            ids = [r.get("id") for r in rongs if isinstance(r, dict) and r.get("id")]
        if not ids:
            bag = getattr(self.stats, "bag_dragons", None) or []
            ids = [d.get("id") for d in bag if isinstance(d, dict) and d.get("id")]
        ids = [x for x in ids if x][:8]
        if not ids:
            self.log("❌ [Event] Không có rồng để cho ăn")
            return False
        self.cho_an_hang_loat(ids, food_name=food_name, dao=dao)
        return True

    def cho_an(self, dragon_id, food_name="ThucAnThit", dao="0"):
        self.cho_an_hang_loat([dragon_id], food_name=food_name, dao=dao)

    def run(self, island_ctrl=None):
        return self.run_for_event(island_ctrl=island_ctrl)

