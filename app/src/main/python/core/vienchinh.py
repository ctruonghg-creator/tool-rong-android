# core/vienchinh.py
"""Viễn Chinh — protocol flow theo capture TinhTheXanh1.
Không dùng replay/session giả cố định và không đụng Đấu Trường.
"""
import random
import time

VIENCHINH_MAPS = [
    "TinhTheXanh1", "TinhTheXanh2", "TinhTheXanh3", "AiNuiKeo",
    "TrungRongZaun", "KimTuThap", "HangRongU", "QuanTheCactus",
    "CaoNguyenPhep", "ChoiThoSan",
    "TrungRongIonia", "ThapAnhSangArgon", "HamMoBoiGiao", "NhaNam",
]
MAP_DISPLAY = {
    "TinhTheXanh1": "Tinh Thể Xanh", "TinhTheXanh2": "Tinh Thể Xanh 2",
    "TinhTheXanh3": "Tinh Thể Xanh 3", "AiNuiKeo": "Ải Núi Kẹo",
    "TrungRongZaun": "Trứng Rồng Zaun", "KimTuThap": "Kim Tự Tháp",
    "HangRongU": "Hang Rồng Ư", "QuanTheCactus": "Quần Thể Cactus",
    "CaoNguyenPhep": "Cao Nguyên Phép", "ChoiThoSan": "Chòi Thợ Săn",
    "TrungRongIonia": "Trứng Rồng Ionia", "ThapAnhSangArgon": "Tháp Ánh Sáng Argon",
    "HamMoBoiGiao": "Hầm Mộ Bội Giáo", "NhaNam": "Nhà Nấm",
}

class VienChinhController:
    def __init__(self, socket_client, stats, log_fn=None):
        self.socket = socket_client; self.stats = stats; self.log = log_fn or print
        self.dragon_ids = []
        self._vc_team_ids = []

    def log_msg(self, msg): self.log(f"[VienChinh] {msg}")

    def load_dragons_from_login(self, login_data):
        ids=[]
        try:
            from core.dragon import parse_dragons_from_login
            island, bag, _ = parse_dragons_from_login(login_data if isinstance(login_data,dict) else {})
            for d in island+bag:
                if getattr(d,'id',None) and d.id not in ids: ids.append(d.id)
        except Exception: pass
        if not ids and getattr(self.stats,'bag_dragons',None):
            for d in self.stats.bag_dragons:
                if isinstance(d,dict) and d.get('id') and str(d['id']) not in ids: ids.append(str(d['id']))
        self.dragon_ids=ids; self.log_msg(f"Rồng login: {len(ids)}"); return len(ids)

    def set_dragon_ids(self, ids): self.dragon_ids=[str(x) for x in ids if x]

    def get_team(self, mode_keys=None):
        """Lấy đội hình Viễn Chinh từ DoiHinh2 — không gửi toàn bộ rồng login.

        Server trả `dragon_not_in_battle_team` nếu dragonId không nằm trong
        đội hình của mode tương ứng. Thử vài modeKey phổ biến rồi fallback.
        """
        if not self.socket or not getattr(self.socket, "is_connected", lambda: False)():
            return list(self.dragon_ids)
        # Capture login: doihinh2.modes.vienChinh — đúng modeKey là "vienChinh".
        keys = mode_keys or (
            "vienChinh", "VienChinh", "vienchinh", "bossLanSu", "loiDai", "pve", "default",
        )
        for mode_key in keys:
            try:
                if hasattr(self.socket, "request"):
                    resp = self.socket.request(
                        "SendRequest2",
                        {"class": "DoiHinh2", "method": "GetData", "data": {"modeKey": mode_key}},
                        timeout=6,
                    )
                else:
                    continue
                if isinstance(resp, list) and len(resp) == 1:
                    resp = resp[0]
                if not isinstance(resp, dict):
                    continue
                team = resp.get("doihinh")
                if isinstance(team, dict):
                    team = team.get("doihinh") or team.get("ids") or team.get("list")
                if not isinstance(team, list) or not team:
                    continue
                ids = []
                for item in team:
                    if isinstance(item, dict):
                        did = item.get("id") or item.get("dragonId")
                    else:
                        did = item
                    if did and str(did) not in ids:
                        ids.append(str(did))
                if ids:
                    self._vc_team_ids = list(ids)
                    self.log_msg(f"Đội hình VC (modeKey={mode_key}): {len(ids)} rồng")
                    return list(ids)
            except Exception as e:
                self.log_msg(f"GetData DoiHinh2 modeKey={mode_key}: {e}")
        if self.dragon_ids:
            self.log_msg(
                f"Không đọc được đội hình DoiHinh2 — dùng {len(self.dragon_ids)} rồng login "
                "(có thể gặp dragon_not_in_battle_team)"
            )
        return list(self.dragon_ids)

    def _events_since(self, n):
        if not hasattr(self.socket,'last_events'): return []
        with self.socket.lock: return list(self.socket.last_events)[n:]

    def _wait_event(self, start, predicate, timeout=8):
        end=time.time()+timeout
        while time.time()<end:
            for _,ev,data in self._events_since(start):
                try:
                    if predicate(ev,data): return data
                except Exception: pass
            time.sleep(.15)
        return None

    def _new_session(self):
        # Format lấy từ capture: accountId:VienChinh:<ms>:<6-digit>.
        account=getattr(self.socket,'account_id','threshoooo') or 'threshoooo'
        return f"{account}:VienChinh:{int(time.time()*1000)}:{random.randint(100000,999999)}"

    def full_flow(self, namemap="TinhTheXanh1", chedo="0", wait_battle=60):
        namemap = str(namemap).strip(); chedo = str(chedo).strip()
        lock = getattr(self.socket, 'combat_lock', None)
        ctx = lock if lock else _NullLock()
        with ctx:
            try:
                self.log_msg(f"=== {namemap} | chedo={chedo} ===")

                # Captured protocol order, but use synchronous ACKs for the
                # request/response pairs so a transient gateway error can be retried.
                session = self._new_session()
                tc = None
                before = len(self.socket.last_events) if hasattr(self.socket, 'last_events') else 0

                for attempt in range(1, 4):
                    self.log_msg(f"Chuẩn bị Viễn Chinh (lần {attempt}/3)")
                    gd = self.socket.request(
                        "SendRequest",
                        {"class":"VienChinh","method":"GetData"},
                        timeout=8,
                    ) if hasattr(self.socket, 'request') else None
                    time.sleep(0.35)

                    info = self.socket.request(
                        "SendRequest2",
                        {"class":"VienChinh","method":"GetInfoMap","data":{"namemap":namemap}},
                        timeout=8,
                    ) if hasattr(self.socket, 'request') else None
                    time.sleep(0.35)

                    full = self.socket.request(
                        "SendRequest",
                        {"class":"VienChinh","method":"GetFullInfoMap","data":{"namemap":namemap,"chedo":chedo}},
                        timeout=8,
                    ) if hasattr(self.socket, 'request') else None
                    time.sleep(0.5)

                    payload = {"class":"VienChinh","method":"ThamChien","data":{"namemap":namemap,"chedo":chedo}}
                    if hasattr(self.socket, 'request'):
                        tc = self.socket.request("SendRequest", payload, timeout=10)
                    else:
                        self.socket.emit("SendRequest", payload)
                        tc = None

                    if isinstance(tc, list) and tc and isinstance(tc[0], dict):
                        tc = tc[0]

                    ok = isinstance(tc, dict) and not (tc.get('errorCode') or tc.get('status') in (1,'1','error'))
                    # Some captures expose the battle in the raw stream even
                    # when the ACK is an error wrapper. Reuse only an actual
                    # server battle object; never synthesize combat data.
                    if not ok:
                        raw = self._wait_event(before, lambda e,d: isinstance(d,dict) and d.get('allquai') is not None, 1.8)
                        if isinstance(raw, dict):
                            tc = raw; ok = True

                    if ok:
                        break

                    msg = tc.get('message') or tc.get('errorCode') if isinstance(tc, dict) else tc
                    self.log_msg(f"ThamChien bị từ chối (lần {attempt}/3): {msg or 'ACK bất thường'}")
                    if attempt < 3:
                        time.sleep(2.0 + attempt)

                if not isinstance(tc, dict):
                    self.log_msg(f"ThamChien không trả response hợp lệ: {tc!r}")
                    return False
                if tc.get('status') in (1,'1','error') or tc.get('errorCode'):
                    self.log_msg(f"ThamChien thất bại sau retry: {tc.get('message') or tc.get('errorCode') or tc}")
                    return False

                server_session = tc.get('battleSessionId') or tc.get('sessionId')
                if server_session:
                    session = server_session
                else:
                    for _, ev, d in self._events_since(before):
                        if isinstance(d, dict) and d.get('battleSessionId'):
                            session = d['battleSessionId']; break
                self.log_msg(f"ThamChien OK | session={session}")

                # Guardian config — synchronous ACK, captured transport unchanged.
                if hasattr(self.socket, 'request'):
                    self.socket.request(
                        "SendRequest2",
                        {"class":"DoiHinh2","method":"GetBattleGuardianSkills","data":{"battleContext":"VienChinh"}},
                        timeout=8,
                    )
                else:
                    self.socket.emit("SendRequest2", {"class":"DoiHinh2","method":"GetBattleGuardianSkills","data":{"battleContext":"VienChinh"}})
                time.sleep(0.5)

                # Ưu tiên đội hình DoiHinh2 (tránh dragon_not_in_battle_team).
                dragons = self.get_team()
                if not dragons:
                    self.log_msg("Không có rồng trong đội hình / login — không triệu hồi rồng giả")
                    return False
                self.log_msg(f"Triệu hồi {len(dragons)} rồng (đội hình)...")
                for did in dragons:
                    self.socket.emit("trieuhoirong", {
                        "dragonId": did,
                        "requestId": __import__('uuid').uuid4().hex,
                        "reviveRequested": False,
                        "battleSessionId": session,
                    })
                    time.sleep(0.3)
                battle_time = max(15, int(wait_battle or 60))
                self.log_msg(f"Chờ battle {battle_time}s...")

                # Triệu hồi Sư Phụ liên tục cho đến khi hết thời gian battle.
                suphu_interval = 0.5
                suphu_count = 0
                battle_end = time.time() + battle_time
                self.log_msg("→ Bắt đầu triệu hồi Sư Phụ liên tục...")
                while time.time() < battle_end:
                    self.socket.emit("trieuhoirong", "suphu")
                    suphu_count += 1
                    time.sleep(suphu_interval)
                self.log_msg(f"→ Đã gửi {suphu_count} lần triệu hồi Sư Phụ trong trận")

                ketqua_payload = {"class":"VienChinh","method":"KetQua","data":{"ketqua":"win","battleSessionId":session}}
                if hasattr(self.socket, 'request'):
                    kres = self.socket.request("SendRequest2", ketqua_payload, timeout=12)
                else:
                    self.socket.emit("SendRequest2", ketqua_payload); kres = None
                time.sleep(0.8)

                self.socket.emit("xemqua"); time.sleep(0.5)
                self.socket.emit("nhanqua"); time.sleep(1.0)
                reward = False
                for _, ev, d in self._events_since(before):
                    if ev == "Thongbao" and isinstance(d,dict):
                        if d.get("addqua") == 1 or isinstance(d.get("nhanqua"),dict):
                            reward = True
                self.socket.emit("SendRequest", {"class":"VienChinh","method":"GetData"})
                time.sleep(0.6)
                if reward:
                    self.log_msg(f"✓ Kết thúc + đã nhận quà {namemap}/{chedo}")
                else:
                    self.log_msg(f"✓ KetQua đã gửi {namemap}/{chedo}; chưa thấy reward")
                return True
            except Exception as e:
                self.log_msg(f"❌ Lỗi: {e}")
                return False

class _NullLock:
    def __enter__(self): return self
    def __exit__(self,*a): return False
