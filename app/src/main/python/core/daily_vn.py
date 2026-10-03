"""Orchestrator ngày VN: một pipeline tuần tự, không chạy chồng các phase."""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional

VN = timezone(timedelta(hours=7))
EVENT_NAMES = (
    "EventDaiChienThuyQuai",
    "EventSinhNhat2026",
)


class DailyVNController:
    """Điều phối duy nhất cho flow ngày.

    Thứ tự mỗi ngày:
      1) quà hằng ngày
      2) điểm danh + xử lý từng nhiệm vụ của EventDaiChienThuyQuai
      3) điểm danh + xử lý từng nhiệm vụ của EventSinhNhat2026
      4) mở lại watcher nhặt item

    Ở ranh giới từng nhiệm vụ, nếu đúng cửa sổ Boss thì Boss được ưu tiên
    trước rồi mới quay lại nhiệm vụ đang chờ.

    Nhiệm vụ còn pending do chưa tới giờ/thiếu điều kiện không làm cả pipeline
    chạy lại mỗi vài giây. Chỉ retry event sau một khoảng dài, và trong lúc
    retry watcher item được khóa để tránh tranh chấp PreloadDao.
    """

    RETRY_SECONDS = 300

    def __init__(
        self,
        gift_ctrl=None,
        event_ctrl=None,
        log_fn: Optional[Callable] = None,
        state_file: Optional[str] = None,
    ):
        self.gift_ctrl = gift_ctrl
        self.event_ctrl = event_ctrl
        self.log = log_fn or print
        self.state_file = state_file or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "vn_daily_state.json"
        )
        self._stop = threading.Event()
        self._thread = None
        self._run_lock = threading.Lock()
        self._running = False
        self._account = None
        self._next_retry_at = 0.0
        self._last_day = None
        self._last_online_sync = 0.0
        self._initial_cycle_started_day = None
        self._event_pending_day = None
        self.on_cycle_complete: Optional[Callable[[str, bool], None]] = None
        self.on_cycle_start: Optional[Callable[[str], None]] = None
        self._set_item_ready: Optional[Callable[[bool], None]] = None

    @staticmethod
    def vn_date() -> str:
        return datetime.now(VN).date().isoformat()

    def log_msg(self, msg: str) -> None:
        self.log(f"[VN Auto] {msg}")

    def _load_state(self) -> Dict:
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                obj = json.load(f)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    def _save_state(self, state: Dict) -> None:
        directory = os.path.dirname(self.state_file)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = self.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_file)

    def _account_state(self, account: str) -> Dict:
        state = self._load_state()
        raw = state.get(str(account), {})
        if isinstance(raw, str):
            raw = {"checkins_day": raw}
        return raw if isinstance(raw, dict) else {}

    def _update_account_state(self, account: str, **changes) -> None:
        state = self._load_state()
        raw = state.get(str(account), {})
        if isinstance(raw, str):
            raw = {"checkins_day": raw}
        if not isinstance(raw, dict):
            raw = {}
        raw.update(changes)
        state[str(account)] = raw
        self._save_state(state)

    def _socket_ready(self) -> bool:
        sc = getattr(self.gift_ctrl, "sc", None) or getattr(self.event_ctrl, "sc", None)
        if hasattr(sc, "is_connected"):
            try:
                return bool(sc.is_connected())
            except Exception:
                pass
        sio = getattr(sc, "sio", None)
        return bool(sio is not None and getattr(sio, "connected", False))

    def _item_watcher(self, ready: bool) -> None:
        """Khóa/mở auto-item quanh một cycle để không tranh chấp đảo."""
        cb = getattr(self, "_set_item_ready", None)
        if cb:
            try:
                cb(bool(ready))
            except Exception as e:
                self.log_msg(f"Item watcher callback lỗi: {e}")

    def set_item_ready_callback(self, callback: Optional[Callable[[bool], None]]) -> None:
        self._set_item_ready = callback

    def _session_gate(self, account: str, day: str) -> None:
        """Cổng login lại cùng ngày: quà + điểm danh + kiểm tra nhiệm vụ còn.

        Không tin mù event_missions_day trong state file — luôn hỏi server
        GetData. Nếu còn pending thì chạy event flow ngay (cùng hành vi
        như lần đầu trong ngày), tránh 2 acc cùng tool mà một acc chỉ gate.
        """
        self._item_watcher(False)
        boss = getattr(self.event_ctrl, "controllers", {}).get("boss") if self.event_ctrl else None
        if boss and hasattr(boss, "set_daily_priority"):
            try:
                boss.set_daily_priority(True)
            except Exception:
                pass
        any_pending = False
        try:
            if self.gift_ctrl:
                try:
                    self.log_msg("Startup gate: đồng bộ toàn bộ quà nền")
                    ok_rewards = bool(self.gift_ctrl.claim_all_base_rewards())
                    if ok_rewards:
                        self._update_account_state(account, daily_reward_day=day)
                except Exception as e:
                    self.log_msg(f"Startup gate quà lỗi: {e}")

            if self.event_ctrl:
                for event_name in EVENT_NAMES:
                    try:
                        self.log_msg(f"Startup gate: kiểm tra điểm danh {event_name}")
                        self.event_ctrl.diem_danh(event_name)
                    except Exception as e:
                        self.log_msg(f"Startup gate {event_name} lỗi: {e}")

                # Xác minh pending thật từ server — không tin state file.
                for event_name in EVENT_NAMES:
                    try:
                        pending_map = self.event_ctrl.fetch_pending_specs(event_name, verbose=False)
                        pending_keys = [
                            k for k, v in (pending_map or {}).items()
                            if not (v or {}).get("done")
                        ]
                        if pending_keys:
                            any_pending = True
                            self.log_msg(
                                f"Startup gate: {event_name} còn {len(pending_keys)} nhiệm vụ "
                                f"({', '.join(pending_keys[:4])}{'...' if len(pending_keys) > 4 else ''}) "
                                f"→ chạy event flow ngay"
                            )
                            self.log_msg(f"Điểm danh + nhiệm vụ: {event_name}")
                            self.event_ctrl.run_event_flow(event_name)
                            still = list(getattr(self.event_ctrl, "last_pending", []) or [])
                            if still:
                                self.log_msg(
                                    f"🔒 {event_name} vẫn pending sau gate → "
                                    f"retry sau {self.RETRY_SECONDS // 60} phút"
                                )
                                break
                            self.log_msg(f"✓ {event_name}: hết pending sau startup gate")
                        else:
                            self.log_msg(f"Startup gate: {event_name} không còn nhiệm vụ pending")
                    except Exception as e:
                        any_pending = True
                        pending_unverified = True
                        self.log_msg(f"Startup gate kiểm tra {event_name} lỗi: {e} → giữ watcher khóa, coi như còn pending")
                        break
        finally:
            if boss and hasattr(boss, "set_daily_priority"):
                try:
                    boss.set_daily_priority(False)
                except Exception:
                    pass

        if any_pending:
            still_pending = pending_unverified
            if self.event_ctrl and not pending_unverified:
                try:
                    still_pending = bool(getattr(self.event_ctrl, "last_pending", None))
                except Exception:
                    still_pending = True
            if still_pending:
                self._event_pending_day = day
                self._next_retry_at = time.time() + self.RETRY_SECONDS
                self._item_watcher(False)
            else:
                # Vừa xong hết trong gate
                self._event_pending_day = None
                self._update_account_state(account, event_missions_day=day)
                self._item_watcher(True)
        else:
            self._event_pending_day = None
            self._update_account_state(account, event_missions_day=day)
            self._item_watcher(True)

    def _run_initial_cycle(self, account: str, day: str) -> bool:
        self._item_watcher(False)
        if self.on_cycle_start:
            try:
                self.on_cycle_start(day)
            except Exception:
                pass

        rec = self._account_state(account)
        ok_daily = True
        all_event_api_ok = True
        any_pending = False

        self.log_msg(f"=== Ngày VN {day} ===")

        # Boss scheduler nhường quyền cho day-cycle từ lúc bắt đầu để không chen vào 08:xx/13:xx...
        boss = None
        if self.event_ctrl:
            boss = getattr(self.event_ctrl, "controllers", {}).get("boss")
        if boss and hasattr(boss, "set_daily_priority"):
            try:
                boss.set_daily_priority(True)
            except Exception:
                pass

        # Nếu vừa bước vào đúng cửa sổ Boss, đánh Boss trước toàn bộ
        # quà/nhiệm vụ. Sau đó pipeline mới tiếp tục tuần tự.
        if boss and hasattr(boss, "is_scheduled_now") and hasattr(boss, "enter_and_fight"):
            try:
                for mode, label in (("lienserver", "Boss LiênSV"), ("thuong", "Boss thường")):
                    if boss.is_scheduled_now(mode):
                        self.log_msg(f"⏰ {label} đang trong cửa sổ → ưu tiên đánh trước day-cycle")
                        started = bool(boss.enter_and_fight(mode))
                        if started:
                            self.log_msg(f"✓ {label} đã xử lý trước quà/nhiệm vụ")
                        else:
                            self.log_msg(f"⚠️ {label} chưa vào được — tiếp tục pipeline, nhiệm vụ Boss sẽ retry theo lịch")
                        break
            except Exception as e:
                self.log_msg(f"Ưu tiên Boss đầu cycle lỗi: {e}")

        if self.gift_ctrl:
            self.log_msg("Đồng bộ quà nền: quà ngày + 7 ngày + online + 1 vàng + QC...")
            try:
                ok_daily = bool(self.gift_ctrl.claim_all_base_rewards())
            except Exception as e:
                ok_daily = False
                self.log_msg(f"Quà nền lỗi: {e}")
        else:
            ok_daily = True

        if ok_daily:
            self._update_account_state(account, daily_reward_day=day)

        # Hai event chạy đúng thứ tự, không chạy song song.
        if self.event_ctrl:
            for event_name in EVENT_NAMES:
                try:
                    self.log_msg(f"Điểm danh + nhiệm vụ: {event_name}")
                    result = bool(self.event_ctrl.run_event_flow(event_name))
                    pending = list(getattr(self.event_ctrl, "last_pending", []) or [])
                    parsed = bool(getattr(self.event_ctrl, "last_missions_parsed", False))
                    flow_failed = bool(getattr(self.event_ctrl, "last_flow_error", False))
                    if not result or flow_failed or not parsed:
                        all_event_api_ok = False
                    if pending:
                        any_pending = True
                        self.log_msg(
                            f"🔒 {event_name} còn pending → KHÔNG chạy Event tiếp theo, "
                            f"không mở item watcher"
                        )
                        break
                except Exception as e:
                    all_event_api_ok = False
                    any_pending = True
                    self.log_msg(f"{event_name} lỗi: {e}")
                    break

        # `pending` không còn bị xem là FAIL toàn bộ day-cycle. Chúng được
        # retry theo lịch; chỉ lỗi API/parse mới là lỗi thật.
        if any_pending:
            self._event_pending_day = day
            self._next_retry_at = time.time() + self.RETRY_SECONDS
            self.log_msg(f"Còn nhiệm vụ pending — retry event sau {self.RETRY_SECONDS // 60} phút")
        else:
            self._event_pending_day = None
            self._next_retry_at = 0.0
            self._update_account_state(account, event_missions_day=day)

        self._initial_cycle_started_day = day
        self._update_account_state(account, initial_flow_day=day)

        status = "OK" if all_event_api_ok else "FAIL"
        if any_pending and all_event_api_ok:
            status = "PENDING"
        self.log_msg(
            f"Kết thúc day-cycle VN {day}: daily={'OK' if ok_daily else 'FAIL'}, events={status}"
        )

        if boss and hasattr(boss, "set_daily_priority"):
            try:
                boss.set_daily_priority(False)
            except Exception:
                pass

        # Chỉ mở watcher sau khi TOÀN BỘ event hiện tại đã hoàn thành.
        # Pending = khóa watcher để không chen ngang mission runner.
        self._item_watcher(not any_pending)
        result = bool(ok_daily and all_event_api_ok)
        cb = self.on_cycle_complete
        if cb:
            try:
                cb(day, result)
            except Exception as e:
                self.log_msg(f"Callback sau cycle lỗi: {e}")
        return result

    def _run_event_retry(self, account: str, day: str) -> bool:
        if not self.event_ctrl:
            self._event_pending_day = None
            return True
        self._item_watcher(False)
        boss = getattr(self.event_ctrl, "controllers", {}).get("boss") if self.event_ctrl else None
        if boss and hasattr(boss, "set_daily_priority"):
            try:
                boss.set_daily_priority(True)
            except Exception:
                pass
        self.log_msg(f"=== Retry event pending ngày {day} ===")
        api_ok = True
        any_pending = False
        try:
            for event_name in EVENT_NAMES:
                self.log_msg(f"Retry: {event_name}")
                result = bool(self.event_ctrl.run_event_flow(event_name))
                pending = list(getattr(self.event_ctrl, "last_pending", []) or [])
                parsed = bool(getattr(self.event_ctrl, "last_missions_parsed", False))
                flow_failed = bool(getattr(self.event_ctrl, "last_flow_error", False))
                if not result or flow_failed or not parsed:
                    api_ok = False
                if pending:
                    any_pending = True
                    self.log_msg(
                        f"🔒 {event_name} vẫn pending → chưa chuyển sang Event kế tiếp"
                    )
                    break
        except Exception as e:
            api_ok = False
            any_pending = True
            self.log_msg(f"Retry event lỗi: {e}")

        if any_pending:
            self._event_pending_day = day
            self._next_retry_at = time.time() + self.RETRY_SECONDS
        else:
            self._event_pending_day = None
            self._next_retry_at = 0.0
            self._update_account_state(account, event_missions_day=day)

        self.log_msg(
            f"Kết thúc retry event: API={'OK' if api_ok else 'FAIL'}, "
            f"pending={'YES' if any_pending else 'NO'}"
        )
        if boss and hasattr(boss, "set_daily_priority"):
            try:
                boss.set_daily_priority(False)
            except Exception:
                pass
        self._item_watcher(not any_pending)
        cb = self.on_cycle_complete
        if cb:
            try:
                cb(day, api_ok)
            except Exception as e:
                self.log_msg(f"Callback sau retry lỗi: {e}")
        return api_ok

    def run_checkins(self, account: str, force: bool = False) -> bool:
        account = str(account or "unknown")
        day = self.vn_date()
        with self._run_lock:
            if self._running:
                return False
            self._running = True
        try:
            if not self._socket_ready():
                self.log_msg("Socket chưa sẵn sàng — chưa chạy day-cycle")
                return False
            rec = self._account_state(account)
            initial_done = rec.get("initial_flow_day") == day
            if force or not initial_done:
                return self._run_initial_cycle(account, day)
            if self._event_pending_day == day and time.time() >= self._next_retry_at:
                return self._run_event_retry(account, day)
            return True
        finally:
            with self._run_lock:
                self._running = False

    def start(self, account: str) -> None:
        self.stop()
        self._account = str(account or "unknown")
        self._stop.clear()
        self._initial_cycle_started_day = None
        self._event_pending_day = None
        self._next_retry_at = 0.0
        self._last_day = None
        self._last_online_sync = 0.0
        self._thread = threading.Thread(target=self._loop, name="DailyVN", daemon=True)
        self._thread.start()
        self.log_msg(f"Theo dõi đổi ngày VN (UTC+7) cho tài khoản {self._account}")

    def stop(self) -> None:
        # Nếu dừng giữa cycle, trả quyền cho Boss scheduler ngay để tránh
        # trạng thái DailyVN priority bị kẹt ON.
        try:
            boss = getattr(self.event_ctrl, "controllers", {}).get("boss") if self.event_ctrl else None
            if boss and hasattr(boss, "set_daily_priority"):
                boss.set_daily_priority(False)
        except Exception:
            pass
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=1.5)
        self._thread = None
        self._account = None

    def _loop(self) -> None:
        first = True
        while not self._stop.is_set():
            current = self.vn_date()
            if self._account and self._socket_ready():
                rec = self._account_state(self._account)
                initial_done = rec.get("initial_flow_day") == current
                if first:
                    if initial_done:
                        # Login lại cùng ngày: vẫn phải đi qua cổng quà/check-in
                        # trước, tuyệt đối không để item watcher chạy trước.
                        self._session_gate(self._account, current)
                        if rec.get("event_missions_day") != current:
                            self._event_pending_day = current
                            self._next_retry_at = time.time() + self.RETRY_SECONDS
                            self._item_watcher(False)
                        else:
                            self._item_watcher(True)
                    first = False

                if not initial_done:
                    self.run_checkins(self._account)
                elif self._event_pending_day == current and time.time() >= self._next_retry_at:
                    self.run_checkins(self._account)
                elif self.gift_ctrl and time.monotonic() - self._last_online_sync >= 60.0:
                    # Mốc thời gian online có thể vừa đủ trong lúc tool đang mở;
                    # kiểm tra nhẹ mỗi phút, nhưng không chạm event/boss song song.
                    try:
                        self.gift_ctrl.claim_online_rewards()
                    except Exception as e:
                        self.log_msg(f"Đồng bộ mốc quà online lỗi: {e}")
                    self._last_online_sync = time.monotonic()

            # Chỉ tick nhẹ cho scheduler; không tạo work nếu chưa tới hạn.
            if self._stop.wait(2.0):
                break
