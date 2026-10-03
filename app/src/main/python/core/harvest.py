# core/harvest.py
import time


class HarvestController:
    def __init__(self, socket_client, stats, log_fn=print, island_ctrl=None):
        self.sc = socket_client
        self.stats = stats
        self.log = log_fn
        self.island_ctrl = island_ctrl

    def set_island_ctrl(self, island_ctrl):
        self.island_ctrl = island_ctrl

    def _dao_list(self):
        if self.island_ctrl:
            return self.island_ctrl.list_islands()
        return ["0", "1", "2", "3", "4"]

    def thu_hoach_1(self, dao, idct):
        if not self.sc or not self.sc.is_connected():
            self.log("❌ Socket không connected")
            return False
        payload = f"{dao}+{idct}"
        self.log(f"→ ThuHoachCT '{payload}'")
        try:
            self.sc.emit("ThuHoachCT", payload)
        except Exception as e:
            self.log(f"emit err: {e}")
            return False
        time.sleep(1.2)
        return True

    def thu_hoach_dao(self, dao, delay=1.2):
        if not self.sc or not self.sc.is_connected():
            self.log("❌ Socket không connected")
            return 0
        self.log(f"═══ Thu hoạch đảo {dao} ═══")
        ok = 0
        for idct in (0, 1, 2, 3):
            if self.thu_hoach_1(dao, idct):
                ok += 1
            time.sleep(delay)
        return ok

    def thu_hoach_tat_ca(self, delay=1.2):
        if not self.sc or not self.sc.is_connected():
            self.log("❌ Socket không connected")
            return
        daos = self._dao_list()
        self.log(f"═══ Thu hoạch TẤT CẢ đảo {daos} ═══")
        total = 0
        for dao in daos:
            total += self.thu_hoach_dao(dao, delay=delay)
            time.sleep(1)
        self.log(f"═══ Tổng: {total} công trình ═══")
        return total

    def run(self, delay=1.0):
        return self.thu_hoach_tat_ca(delay=delay)

