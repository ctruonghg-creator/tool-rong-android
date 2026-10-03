# core/gift_socket.py
"""Quà ngày + gói đăng nhập 7 ngày + quảng cáo + quà online + quà 1 vàng.

Protocol trong module này bám theo capture mới:
- nhanquatanghangngay -> Info.nhanquatanghangngay
- LoginGiftPackages.GetPackages / ClaimDay
- Main.CheckXemQc -> XemQuangCaoXong
- Main.XemQuaOnline -> Main.NhanQuaOnline{qua:index}
- Main.XemShopCatalog -> MuaVatPham -> Info.muavatphamthanhcong
- xemqua -> nhanqua cho popup reward
"""
from __future__ import annotations

import json
import os
import time
import threading
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional


VN = timezone(timedelta(hours=7))


class GiftSocketController:
    LOGIN_PACKAGE_DEFAULT = "goi_dang_nhap_20260907131158"
    ONE_GOLD_ENTRY = "shopEntry*shop_mo8mbir0_91i18o"
    AD_REWARD_KEYS = (
        "GoiVatPhamNgauNhienNgay",
        "GoiVatPhamSuKienNgay",
    )

    def __init__(self, socket_client, stats, log_fn: Optional[Callable] = None):
        self.sc = socket_client
        self.stats = stats
        self.log = log_fn or print
        self.login_package_id = self.LOGIN_PACKAGE_DEFAULT
        self.state_file = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "vn_daily_state.json"
        )
        self._lock = threading.RLock()

    @staticmethod
    def _vn_day() -> str:
        return datetime.now(VN).date().isoformat()

    def _load_vn_state(self) -> Dict[str, Any]:
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                obj = json.load(f)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    def _save_vn_state(self, state: Dict[str, Any]) -> None:
        directory = os.path.dirname(self.state_file)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = self.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_file)

    def _account_key(self) -> str:
        return str(getattr(self.sc, "account_id", "unknown") or "unknown")

    def _account_state(self) -> Dict[str, Any]:
        state = self._load_vn_state()
        raw = state.get(self._account_key(), {})
        if isinstance(raw, str):
            raw = {"daily_reward_day": raw}
        return raw if isinstance(raw, dict) else {}

    def _update_state(self, **changes: Any) -> None:
        state = self._load_vn_state()
        key = self._account_key()
        raw = state.get(key, {})
        if isinstance(raw, str):
            raw = {"daily_reward_day": raw}
        if not isinstance(raw, dict):
            raw = {}
        raw.update(changes)
        state[key] = raw
        self._save_vn_state(state)

    def _automation_ctx(self):
        lock = getattr(self.sc, "automation_lock", None)
        return lock if lock is not None else nullcontext()

    def log_msg(self, msg: str) -> None:
        self.log(f"[Qua] {msg}")

    # ------------------------------------------------------------ raw events
    def _recent_events(self, limit: int = 80):
        events = getattr(self.sc, "last_events", None)
        if events is None:
            return []
        try:
            with self.sc.lock:
                return list(events)[-limit:]
        except Exception:
            try:
                return list(events)[-limit:]
            except Exception:
                return []

    @staticmethod
    def _unwrap(resp: Any) -> Any:
        if isinstance(resp, list) and len(resp) == 1:
            return resp[0]
        return resp

    @classmethod
    def _decode(cls, value: Any) -> Any:
        value = cls._unwrap(value)
        for _ in range(3):
            if isinstance(value, bytes):
                try:
                    value = value.decode("utf-8")
                except Exception:
                    break
            if not isinstance(value, str):
                break
            s = value.strip()
            if not s:
                break
            try:
                value = json.loads(s)
            except Exception:
                break
        return value

    @classmethod
    def _first_dict(cls, value: Any) -> Optional[Dict[str, Any]]:
        value = cls._decode(value)
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            for x in value:
                x = cls._decode(x)
                if isinstance(x, dict):
                    return x
        return None

    def _request(self, event: str, payload: Optional[dict] = None, timeout: float = 8.0):
        if not self.sc or not self.sc.is_connected():
            return None
        try:
            return self.sc.request(event, payload, timeout=timeout)
        except AttributeError:
            try:
                return self.sc.call(event, payload, timeout=timeout)
            except Exception:
                return None
        except Exception as e:
            self.log_msg(f"request {event} lỗi: {e}")
            return None

    def _emit(self, event: str, data: Any = None) -> bool:
        if not self.sc or not self.sc.is_connected():
            return False
        try:
            self.sc.emit(event, data)
            return True
        except Exception as e:
            self.log_msg(f"emit {event} lỗi: {e}")
            return False

    # ------------------------------------------------------------ quà ngày
    def claim_daily_rewards(self, times: int = 1, force: bool = False) -> bool:
        """Nhận quà hằng ngày với ACK Info; không spam 6 request như bản cũ."""
        if not self.sc or not self.sc.is_connected():
            self.log_msg("Chưa kết nối")
            return False
        day = self._vn_day()
        with self._lock, self._automation_ctx():
            rec = self._account_state()
            if not force and rec.get("daily_reward_day") == day:
                self.log_msg(f"Đã nhận quà hằng ngày ngày {day} (VN) — bỏ qua")
                return True

            attempts = max(1, min(int(times or 1), 3))
            for attempt in range(1, attempts + 1):
                client_id = uuid.uuid4().hex
                before = len(self._recent_events())
                self.log_msg(f"nhanquatanghangngay lần {attempt}/{attempts}")
                if not self._emit("nhanquatanghangngay", client_id):
                    continue

                deadline = time.monotonic() + 6.0
                while time.monotonic() < deadline:
                    for _, event, data in self._recent_events(160)[before:]:
                        if event != "Info":
                            continue
                        obj = self._decode(data)
                        if not isinstance(obj, dict):
                            continue
                        info = obj.get("nhanquatanghangngay")
                        if not isinstance(info, dict):
                            # Một số build nhét status thẳng vào Info root.
                            if str(obj.get("status") or "") in ("0", "ok", "success") and (
                                "nhanquatanghangngay" in str(obj).lower()
                            ):
                                self._update_state(daily_reward_day=day)
                                self.log_msg(f"✓ Quà hằng ngày đã xác nhận ngày {day} (VN) [Info root]")
                                return True
                            continue
                        rid = str(info.get("clientRequestId") or "")
                        status = str(info.get("status") or info.get("code") or "")
                        # Khớp clientRequestId là lý tưởng; fallback chấp nhận status OK
                        # trong cửa sổ sau emit (tránh kẹt khi server bỏ clientRequestId).
                        if status in ("0", "ok", "success", "1"):
                            if rid == client_id or not rid:
                                self._update_state(daily_reward_day=day)
                                self.log_msg(f"✓ Quà hằng ngày đã xác nhận ngày {day} (VN)")
                                return True
                    time.sleep(0.12)
                time.sleep(0.4)

            self.log_msg("⚠️ Chưa nhận được Info xác nhận quà hằng ngày — giữ retry")
            return False

    # ------------------------------------------------------------ gói 7 ngày
    def get_login_packages(self, package_id: Optional[str] = None) -> Optional[dict]:
        pkg = package_id or self.login_package_id
        resp = self._request(
            "SendRequest2",
            {
                "class": "LoginGiftPackages",
                "method": "GetPackages",
                "data": {"selectedPackageId": pkg},
            },
            timeout=8,
        )
        obj = self._first_dict(resp)
        if not obj:
            self.log_msg("GetPackages: ACK không đọc được")
            return None
        return obj

    @staticmethod
    def _packages_from(obj: Any) -> List[dict]:
        if not isinstance(obj, dict):
            return []
        arr = obj.get("packages")
        return [x for x in arr if isinstance(x, dict)] if isinstance(arr, list) else []

    def _select_free_active_package(self, obj: dict, package_id: Optional[str] = None) -> Optional[dict]:
        requested = package_id or self.login_package_id
        packages = self._packages_from(obj)
        active_id = str(obj.get("activePackageId") or requested)
        # Ưu tiên đúng package ID; nếu server đổi ID, chỉ fallback sang package free đang active.
        for p in packages:
            if str(p.get("id")) == requested and str(p.get("purchaseType")) == "free":
                return p
        for p in packages:
            if str(p.get("id")) == active_id and str(p.get("purchaseType")) == "free":
                return p
        for p in packages:
            if str(p.get("purchaseType")) == "free" and p.get("activated"):
                return p
        return None

    def claim_login_day(self, day: int, package_id: Optional[str] = None) -> bool:
        pkg = package_id or self.login_package_id
        try:
            day_i = int(day)
        except Exception:
            return False
        obj = self.get_login_packages(pkg)
        p = self._select_free_active_package(obj or {}, pkg) if obj else None
        if not p:
            self.log_msg("Không tìm thấy gói đăng nhập miễn phí đang active")
            return False

        days = p.get("days") if isinstance(p.get("days"), list) else []
        state = None
        reward_name = ""
        for d in days:
            if isinstance(d, dict) and int(d.get("day", -1) or -1) == day_i:
                state = str(d.get("state") or "").lower()
                reward = d.get("reward") if isinstance(d.get("reward"), dict) else {}
                reward_name = str(reward.get("displayName") or reward.get("itemKey") or "")
                break

        if state == "claimed":
            self.log_msg(f"Gói 7 ngày: ngày {day_i} đã nhận")
            return True
        if state != "claimable":
            self.log_msg(f"Gói 7 ngày: ngày {day_i} state={state or 'unknown'} — không gửi ClaimDay")
            return False

        self.log_msg(f"Gói 7 ngày: ClaimDay {day_i}" + (f" → {reward_name}" if reward_name else ""))
        resp = self._request(
            "SendRequest2",
            {
                "class": "LoginGiftPackages",
                "method": "ClaimDay",
                "data": {"packageId": str(p.get("id") or pkg), "day": str(day_i)},
            },
            timeout=8,
        )
        r = self._first_dict(resp)
        if r and str(r.get("status")) in ("0", "ok", "success"):
            self.log_msg(f"✓ Đã nhận quà đăng nhập ngày {day_i}")
            return True
        self.log_msg(f"⚠️ ClaimDay {day_i} chưa được xác nhận: {r or resp}")
        return False

    def claim_login_claimables(self, package_id: Optional[str] = None, max_passes: int = 8) -> bool:
        """Chỉ claim các day=claimable; không đụng day=locked, không mua gói trả phí."""
        ok = True
        pkg = package_id or self.login_package_id
        attempted = set()
        for _ in range(max(1, min(int(max_passes), 3))):
            obj = self.get_login_packages(pkg)
            if not obj:
                return False
            p = self._select_free_active_package(obj, pkg)
            if not p:
                self.log_msg("Không có gói đăng nhập miễn phí active")
                return False
            claimable = []
            for d in p.get("days", []) if isinstance(p.get("days"), list) else []:
                if not isinstance(d, dict):
                    continue
                if str(d.get("state") or "").lower() == "claimable":
                    try:
                        claimable.append(int(d.get("day")))
                    except Exception:
                        pass
            claimable = [d for d in sorted(set(claimable)) if d not in attempted]
            if not claimable:
                self.log_msg(
                    f"Gói 7 ngày: không còn mốc claimable mới | currentDay={p.get('currentDay')} "
                    f"claimedDays={p.get('claimedDays')}"
                )
                return ok
            progressed = False
            for day in claimable:
                attempted.add(day)
                if self.claim_login_day(day, package_id=str(p.get("id") or pkg)):
                    progressed = True
                    time.sleep(0.35)
                else:
                    ok = False
            if not progressed:
                return False
        return ok

    # ------------------------------------------------------------ online milestones
    def _extract_online_rewards(self, obj: Any) -> Optional[List[dict]]:
        obj = self._decode(obj)
        if isinstance(obj, dict):
            arr = obj.get("data")
            if isinstance(arr, list):
                return [x for x in arr if isinstance(x, dict)]
        if isinstance(obj, list):
            return [x for x in obj if isinstance(x, dict)]
        return None

    def claim_online_rewards(self) -> bool:
        """Nhận tất cả mốc XemQuaOnline đang `duocnhan`, bao gồm mốc đã trôi qua."""
        resp = self._request("SendRequest", {"class": "Main", "method": "XemQuaOnline"}, timeout=8)
        rewards = self._extract_online_rewards(resp)
        if rewards is None:
            self.log_msg("XemQuaOnline: không đọc được danh sách mốc")
            return False

        claimable = []
        for idx, item in enumerate(rewards):
            state = str(item.get("trangthai") or item.get("state") or "").lower()
            if state in ("duocnhan", "claimable", "can_nhan"):
                claimable.append(idx)

        if not claimable:
            self.log_msg("⏱️ Quà online: không có mốc đang được nhận")
            return True

        ok = True
        self.log_msg(f"🎁 Quà online: nhận {len(claimable)} mốc đã đủ thời gian")
        for idx in claimable:
            before = len(self._recent_events())
            r = self._request(
                "SendRequest",
                {"class": "Main", "method": "NhanQuaOnline", "data": {"qua": str(idx)}},
                timeout=8,
            )
            obj = self._first_dict(r)
            confirmed = bool(obj and str(obj.get("status")) in ("0", "ok", "success", "1"))
            if not confirmed:
                # Fallback: quét Info push trong vài giây (một số build không trả status trong ACK).
                deadline = time.monotonic() + 3.5
                while time.monotonic() < deadline and not confirmed:
                    for _, event, data in self._recent_events(120)[before:]:
                        if event != "Info":
                            continue
                        decoded = self._decode(data) if hasattr(self, "_decode") else data
                        if not isinstance(decoded, dict):
                            continue
                        # Bất kỳ Info liên quan online reward trong cửa sổ sau request.
                        blob = str(decoded).lower()
                        if "online" in blob or "quaonline" in blob or "nhanqua" in blob:
                            st = str(decoded.get("status") or "")
                            if st in ("0", "ok", "success", "1", ""):
                                confirmed = True
                                break
                    if confirmed:
                        break
                    time.sleep(0.15)
            if confirmed:
                self.log_msg(f"  ✓ NhanQuaOnline qua={idx}")
            else:
                ok = False
                self.log_msg(f"  ⚠️ NhanQuaOnline qua={idx} chưa xác nhận")
            time.sleep(0.3)

        return ok

    # ------------------------------------------------------------ 1 vàng
    @staticmethod
    def _number(value: Any) -> Optional[float]:
        try:
            return float(str(value).replace(",", "").strip())
        except Exception:
            return None

    def _latest_shop_purchase_limit(self) -> Optional[dict]:
        for _, event, data in reversed(self._recent_events(160)):
            if event != "Info" or not isinstance(data, dict):
                continue
            info = data.get("muavatphamthanhcong")
            if isinstance(info, dict):
                lim = info.get("shopPurchaseLimit")
                if isinstance(lim, dict):
                    return lim
        return None

    def buy_one_gold_reward(self, entry_id: Optional[str] = None) -> bool:
        """Mua đúng món quà 1 Vàng/ngày; không tự mua bằng Kim Cương."""
        entry = entry_id or self.ONE_GOLD_ENTRY
        day = self._vn_day()
        with self._lock, self._automation_ctx():
            rec = self._account_state()
            if rec.get("one_gold_day") == day:
                self.log_msg(f"Quà 1 Vàng: đã xử lý ngày {day} — bỏ qua")
                return True

            limit = self._latest_shop_purchase_limit()
            if isinstance(limit, dict) and bool(limit.get("reached")):
                period = str(limit.get("periodKey") or "")
                if not period or period == day:
                    self._update_state(one_gold_day=day)
                    self.log_msg("Quà 1 Vàng: server báo đã mua trong ngày")
                    return True

            catalog = self._request(
                "SendRequest",
                {"class": "Main", "method": "XemShopCatalog", "data": {"nameitem": entry}},
                timeout=8,
            )
            info = self._first_dict(catalog)
            if not info:
                self.log_msg("Quà 1 Vàng: không đọc được XemShopCatalog")
                return False

            price = self._number(info.get("gia"))
            currency = str(info.get("tienmua") or "")
            if price != 1 or currency.lower() != "vang":
                self.log_msg(
                    f"Quà 1 Vàng: catalog không khớp (gia={info.get('gia')} tienmua={currency}) — bỏ qua"
                )
                return False

            self.log_msg("🪙 Mua quà 1 Vàng")
            before = len(self._recent_events())
            if not self._emit("MuaVatPham", f"{entry}+1+"):
                return False

            deadline = time.monotonic() + 8.0
            success = False
            while time.monotonic() < deadline:
                for _, event, data in self._recent_events(200)[before:]:
                    if event != "Info":
                        continue
                    payload = data if isinstance(data, dict) else self._decode(data) if hasattr(self, "_decode") else None
                    if not isinstance(payload, dict):
                        continue
                    info2 = payload.get("muavatphamthanhcong")
                    if isinstance(info2, dict):
                        lim = info2.get("shopPurchaseLimit")
                        # Chấp nhận khi có shopPurchaseLimit.reached HOẶC status thành công.
                        if isinstance(lim, dict) and bool(lim.get("reached")):
                            success = True
                            break
                        st = str(info2.get("status") or info2.get("code") or "")
                        if st in ("0", "ok", "success", "1"):
                            success = True
                            break
                    # Fallback: Info root có status OK sau MuaVatPham.
                    st_root = str(payload.get("status") or "")
                    if st_root in ("0", "ok", "success") and (
                        "muavatpham" in str(payload).lower() or "shop" in str(payload).lower()
                    ):
                        success = True
                        break
                if success:
                    break
                time.sleep(0.12)

            if not success:
                # Kiểm tra lại limit sau request — có thể đã mua nhưng Info tới trễ / khác format.
                limit2 = self._latest_shop_purchase_limit()
                if isinstance(limit2, dict) and bool(limit2.get("reached")):
                    success = True
                else:
                    self.log_msg("⚠️ Mua 1 Vàng: chưa thấy Info.muavatphamthanhcong")
                    return False

            self._update_state(one_gold_day=day)
            self.log_msg("✓ Mua quà 1 Vàng thành công")
            self.collect_reward_popup()
            return True

    # ------------------------------------------------------------ popup queue / lọc quà
    def scan_reward_queue(self, wait: float = 0.6) -> List[dict]:
        """Quét popup quà hiện tại bằng xemqua.

        Trả về danh sách gói dạng:
        {"qualevel": ..., "qua": [...], "quaconlai": ...}
        Dựa trên capture: xemqua -> Thongbao{qualevel, qua, ...}.
        """
        if not self._emit("xemqua"):
            self.log_msg("xemqua: emit thất bại")
            return []
        deadline = time.monotonic() + max(0.1, float(wait or 0.6))
        found: List[dict] = []
        seen = set()
        while time.monotonic() < deadline:
            for _, event, data in self._recent_events(120):
                if event != "Thongbao" or not isinstance(data, dict):
                    continue
                qlevel = data.get("qualevel")
                qua = data.get("qua")
                if not qlevel or not isinstance(qua, list):
                    continue
                # Một capture có thể được lưu lại nhiều lần trong last_events.
                key = (str(qlevel), repr(qua), str(data.get("quaconlai")))
                if key in seen:
                    continue
                seen.add(key)
                found.append({
                    "qualevel": str(qlevel),
                    "qua": [x for x in qua if isinstance(x, dict)],
                    "quaconlai": data.get("quaconlai"),
                    "message": data.get("message"),
                })
            if found:
                break
            time.sleep(0.08)
        if found:
            self.log_msg(f"✓ Quét quà: {len(found)} gói")
        else:
            self.log_msg("Quét quà: chưa thấy gói popup")
        return found

    def claim_current_reward(self, wait: float = 0.5) -> bool:
        """Nhận popup hiện tại bằng nhanqua. Server nhận cả gói, không chọn từng item."""
        if not self._emit("nhanqua"):
            self.log_msg("nhanqua: emit thất bại")
            return False
        time.sleep(max(0.05, float(wait or 0.5)))
        return True

    @staticmethod
    def reward_item_summary(packages: List[dict]) -> Dict[str, int]:
        """Gom số lượng item quan sát được trong các popup đã quét."""
        out: Dict[str, int] = {}
        for pkg in packages:
            for item in pkg.get("qua", []):
                name = str(item.get("name") or "")
                if not name:
                    continue
                raw = item.get("soluong", 0)
                try:
                    # Một số gói dùng '1M', '2M'...; giữ nguyên dạng số nếu parse được.
                    val = int(str(raw).replace(",", ""))
                except Exception:
                    val = 1
                out[name] = out.get(name, 0) + val
        return out

    # ------------------------------------------------------------ ads / popup
    def collect_reward_popup(self) -> bool:
        """Theo capture: xemqua -> nhanqua để nhận reward popup hiện có."""
        if not self._emit("xemqua"):
            return False
        time.sleep(0.25)
        has_reward = False
        for _, event, data in reversed(self._recent_events(80)):
            if event != "Thongbao" or not isinstance(data, dict):
                continue
            if data.get("qualevel"):
                has_reward = True
                break
        if not has_reward:
            return True
        self._emit("nhanqua")
        time.sleep(0.5)
        return True

    def watch_ad_reward(self, namequaxem: str, force: bool = False) -> bool:
        day = self._vn_day()
        name = str(namequaxem or "").strip()
        if not name:
            return False
        state_key = f"ad_{name}"
        rec = self._account_state()
        if not force and rec.get(state_key) == day:
            self.log_msg(f"QC {name}: đã xử lý ngày {day} — bỏ qua")
            return True

        self.log_msg(f"📺 QC nhận quà: CheckXemQc {name}")
        ack = self._request(
            "SendRequest",
            {"class": "Main", "method": "CheckXemQc", "data": {"namequaxem": name}},
            timeout=8,
        )
        obj = self._first_dict(ack)
        if not obj:
            self.log_msg(f"  ⚠️ CheckXemQc {name}: không nhận ACK")
            return False
        if str(obj.get("status")) not in ("0", "ok", "success"):
            self.log_msg(f"  ⚠️ CheckXemQc từ chối {name}: {obj}")
            return False

        before = len(self._recent_events())
        self._emit("XemQuangCaoXong", name)
        deadline = time.monotonic() + 5.0
        delivered = False
        while time.monotonic() < deadline:
            for _, event, data in self._recent_events(160)[before:]:
                if event == "Thongbao" and isinstance(data, dict):
                    if data.get("addqua") is not None or data.get("qualevel") or data.get("tbnhanh"):
                        delivered = True
                        break
            if delivered:
                break
            time.sleep(0.15)

        if not delivered:
            # Một số bản chỉ push reward sau khi popup được mở.
            self.collect_reward_popup()
            delivered = True
        else:
            self.collect_reward_popup()

        if delivered:
            self._update_state(**{state_key: day})
            self.log_msg(f"✓ QC {name}: đã xử lý")
        return delivered

    def claim_all_base_rewards(self) -> bool:
        """Một pass idempotent: quà ngày -> login 7 ngày -> online -> 1 vàng -> 2 QC.

        Quà nền bắt buộc được tách khỏi quảng cáo tùy thời điểm: lỗi QC không
        được làm cả day-cycle chuyển FAIL, nhưng từng QC vẫn được ghi trạng
        thái và retry khi sang vòng sau.
        """
        with self._lock, self._automation_ctx():
            critical = []
            optional_ads = []
            critical.append(("quà ngày", self.claim_daily_rewards(times=1)))
            critical.append(("login 7 ngày", self.claim_login_claimables()))
            critical.append(("quà online", self.claim_online_rewards()))
            critical.append(("quà 1 vàng", self.buy_one_gold_reward()))
            for name in self.AD_REWARD_KEYS:
                try:
                    ok = self.watch_ad_reward(name)
                except Exception as e:
                    ok = False
                    self.log_msg(f"QC {name} lỗi: {e}")
                optional_ads.append((name, ok))

            critical_ok = all(ok for _, ok in critical)
            ads_ok = all(ok for _, ok in optional_ads)
            if critical_ok and ads_ok:
                summary = "OK"
            elif critical_ok:
                summary = "OK nền, QC còn retry"
            else:
                summary = "có mục nền pending"
            self.log_msg("=== Đồng bộ quà nền: " + summary + " ===")
            return critical_ok

    def claim_all(self) -> bool:
        return self.claim_all_base_rewards()

    # alias cho UI cũ
    def claim_all_daily(self) -> bool:
        return self.claim_all()
