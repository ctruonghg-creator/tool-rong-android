# core/stats.py

class PlayerStats:
    def __init__(self):
        self.bag_count = 0
        self.bag_max = 0
        self.thach_anh = 0
        self.bag_dragons = []
        # Thần Long đặc biệt dùng cho nhiệm vụ event: tên key đúng protocol
        # server là HoaThanLong / TuyetThanLong.
        self.special_dragons = {}

    def parse(self, data):
        if not isinstance(data, dict):
            return

        # LoginSuccess: HoaThanLong/TuyetThanLong thường nằm lồng (không phải root).
        # Quét nông+sâu toàn payload; chỉ lưu node dict thật từ server.
        def _find_special(obj, key, depth=0):
            if depth > 6 or obj is None:
                return None
            if isinstance(obj, dict):
                if key in obj and isinstance(obj[key], dict):
                    return obj[key]
                for v in obj.values():
                    found = _find_special(v, key, depth + 1)
                    if found is not None:
                        return found
            elif isinstance(obj, list):
                for v in obj[:200]:
                    found = _find_special(v, key, depth + 1)
                    if found is not None:
                        return found
            return None

        for special_key in ("HoaThanLong", "TuyetThanLong"):
            node = data.get(special_key)
            if not isinstance(node, dict):
                node = _find_special(data, special_key)
            if isinstance(node, dict):
                self.special_dragons[special_key] = dict(node)
            
        # Chỉ đọc Thạch Anh từ các key tài khoản / currency chính thức, tránh quét nhầm vào danh sách yêu cầu
        target_keys = ["thachanh", "thachAnh", "thach_anh", "kimcuong", "kimCuong"]
        profile_keys = ["user", "taikhoan", "player", "account", "info"]

        # 1. Kiểm tra trực tiếp ở root data
        for key in target_keys:
            if key in data and not isinstance(data[key], (dict, list)):
                try:
                    self.thach_anh = int(data[key])
                    break
                except Exception:
                    pass

        # 2. Kiểm tra trong các object thông tin người chơi nếu root chưa có
        if self.thach_anh == 0:
            for pk in profile_keys:
                sub_node = data.get(pk)
                if isinstance(sub_node, dict):
                    for key in target_keys:
                        if key in sub_node and not isinstance(sub_node[key], (dict, list)):
                            try:
                                self.thach_anh = int(sub_node[key])
                                break
                            except Exception:
                                pass
                    if self.thach_anh != 0:
                        break

        # Đọc Túi rồng
        bag_data = data.get("bag") or data.get("khoRong") or data.get("inventory") or data.get("item")
        
        if isinstance(bag_data, dict):
            self.bag_dragons = (
                bag_data.get("dragons") or 
                bag_data.get("list") or 
                bag_data.get("itemrong") or 
                bag_data.get("rong") or 
                self.bag_dragons
            )
            self.bag_count = bag_data.get("count", len(self.bag_dragons))
            self.bag_max = bag_data.get("max", bag_data.get("maxrong", self.bag_max))
        elif isinstance(data.get("dragons"), list):
            self.bag_dragons = data["dragons"]
            self.bag_count = len(self.bag_dragons)
        elif isinstance(data.get("itemrong"), list):
            self.bag_dragons = data["itemrong"]
            self.bag_count = len(self.bag_dragons)
            
        if self.bag_count == 0 and "itemrong" in data:
            if isinstance(data["itemrong"], list):
                self.bag_dragons = data["itemrong"]
                self.bag_count = len(self.bag_dragons)

    def add_dragon(self, dragon):
        if isinstance(dragon, dict):
            rid = dragon.get("id")
            if rid:
                self.bag_dragons = [d for d in self.bag_dragons if d.get("id") != rid]
            self.bag_dragons.append(dragon)
            self.bag_count = len(self.bag_dragons)

    def remove_dragon(self, dragon_id):
        self.bag_dragons = [d for d in self.bag_dragons if d.get("id") != dragon_id]
        self.bag_count = len(self.bag_dragons)

    def update_dragon_from_island(self, dragon):
        """Cập nhật dữ liệu rồng từ đảo mà không xóa rồng khỏi túi.

        Feed/RongAn chỉ thay đổi trạng thái đói; không coi snapshot đảo là
        danh sách túi rồng để tránh làm mất rồng trên tab thống kê.
        """
        if not isinstance(dragon, dict):
            return
        rid = dragon.get("id")
        if not rid:
            return
        for i, old in enumerate(self.bag_dragons):
            if isinstance(old, dict) and old.get("id") == rid:
                merged = dict(old)
                merged.update(dragon)
                self.bag_dragons[i] = merged
                return

    def co_the_lai(self):
        return True, ""