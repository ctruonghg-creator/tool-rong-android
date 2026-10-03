# core/dragon.py
class Dragon:
    def __init__(self, raw):
        self.raw = raw or {}
        self.id = self.raw.get("id", "")
        self.name = (self.raw.get("namerong") or self.raw.get("nameobject") or "?").strip() or "?"
        self.nameobject = self.raw.get("nameobject", "")
        self.sao = self.raw.get("sao", 0)
        self.level = self.raw.get("level", 0)
        self.he = self.raw.get("he", "")
        self.gen = self.raw.get("gen", "")
        self.hiem = self.raw.get("hiem", 0)
        chiso = self.raw.get("chiso") or {}
        self.hp = chiso.get("hp", 0) if isinstance(chiso, dict) else 0
        self.atk = chiso.get("sucdanh", 0) if isinstance(chiso, dict) else 0

    def __repr__(self):
        return f"<Dragon {self.name} {self.sao}* hiem={self.hiem} id={self.id}>"


def parse_dragons_from_login(data):
    island = []
    bag = []
    current_island = "0"
    if not isinstance(data, dict):
        return island, bag, current_island
    dao = data.get("dao") or []
    if isinstance(dao, list) and dao:
        # lấy tất cả đảo (0..4), ưu tiên đảo cao nhất làm current
        max_idx = "0"
        for block in dao:
            if not isinstance(block, dict):
                continue
            rong = block.get("rong") or []
            for r in rong:
                if isinstance(r, dict) and r.get("id"):
                    island.append(Dragon(r))
                    idx = str(r.get("islandIndex", "0"))
                    try:
                        if int(idx) >= int(max_idx):
                            max_idx = idx
                    except Exception:
                        pass
            # một số server gắn islandIndex ở block
            bi = block.get("islandIndex") or block.get("dao") or block.get("id")
            if bi is not None:
                try:
                    if int(bi) >= int(max_idx):
                        max_idx = str(bi)
                except Exception:
                    pass
        current_island = max_idx
    item = data.get("item") or {}
    for r in item.get("itemrong") or []:
        if isinstance(r, dict) and (r.get("id") or r.get("namerong")):
            bag.append(Dragon(r))
    return island, bag, current_island


def parse_lai_list(data):
    result = []
    if not isinstance(data, dict):
        return result
    itemlai = data.get("itemlai")
    if not isinstance(itemlai, dict):
        return result
    arr = itemlai.get("ItemrongLai") or []
    for x in arr:
        if isinstance(x, dict) and x.get("id"):
            result.append(Dragon(x))
    return result