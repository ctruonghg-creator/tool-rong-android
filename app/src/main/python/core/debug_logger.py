import os
import threading


class DebugLogger:
    """Bounded on-disk debug event logger."""
    _file_lock = threading.RLock()

    def __init__(self, path="debug_log.txt", max_bytes=10 * 1024 * 1024, backups=3, sample_every=50):
        self.path = path
        self.max_bytes = max(1, int(max_bytes))
        self.backups = max(1, int(backups))
        self.sample_every = max(1, int(sample_every))
        self.event_count = 0

    def log_event(self, event, data):
        self.event_count += 1
        if self.event_count != 1 and self.event_count % self.sample_every != 0:
            return
        line = f"Event: {event} | Data: {data}\n"
        with self._file_lock:
            try:
                self._rotate_if_needed(len(line.encode("utf-8")))
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line)
            except Exception:
                pass

    def _rotate_if_needed(self, incoming_bytes):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            size = 0
        if size == 0 or size + incoming_bytes <= self.max_bytes:
            return
        oldest = f"{self.path}.{self.backups}"
        try:
            if os.path.exists(oldest):
                os.remove(oldest)
        except OSError:
            pass
        for i in range(self.backups - 1, 0, -1):
            src = f"{self.path}.{i}"
            dst = f"{self.path}.{i + 1}"
            try:
                if os.path.exists(src):
                    os.replace(src, dst)
            except OSError:
                pass
        try:
            if os.path.exists(self.path):
                os.replace(self.path, f"{self.path}.1")
        except OSError:
            pass
