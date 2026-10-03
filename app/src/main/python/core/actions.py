# core/actions.py
import time
import uuid


def get_lai_list(socket_client):
    if not socket_client or not socket_client.is_connected():
        return
    req_id = uuid.uuid4().hex
    socket_client.emit("GetItemRong", {"mode": "false", "requestId": req_id})


def lai_rong(socket_client, id1, id2):
    if not socket_client or not socket_client.is_connected():
        return
    payload = f"{id1}!{id2}"
    socket_client.emit("LaiRong", payload)


def chuc_phuc(socket_client):
    if not socket_client or not socket_client.is_connected():
        return
    socket_client.emit("chucphuc")


def ban_rong(socket_client, dragon_id):
    if not socket_client or not socket_client.is_connected():
        return
    socket_client.emit("BanRong", dragon_id)


def cat_rong(socket_client, dragon_id):
    if not socket_client or not socket_client.is_connected():
        return
    for ev in ("CatRong", "Catrong", "CatRongVaoKho"):
        for p in ({"id": dragon_id, "dao": "0"}, {"id": dragon_id}, {"idrong": dragon_id}):
            try:
                socket_client.emit(ev, p)
            except Exception:
                pass
            time.sleep(0.3)


def scan_island(socket_client, from_island, to_island, data_version):
    if not socket_client or not socket_client.is_connected():
        return
    payload = f"{from_island}+codao+{data_version}+{to_island}"
    socket_client.emit("QuaDao", payload)