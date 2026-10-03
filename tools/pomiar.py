"""Mierzy czas startu i zakończenia sesji (bez zmiany systemu: dry-run)."""

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["CISZA_DATA_DIR"] = tempfile.mkdtemp(prefix="cisza-czas-")
os.environ["CISZA_DRY_RUN"] = "1"
os.environ.pop("CISZA_SAFE", None)

from focuslock.config import Settings  # noqa: E402
from focuslock.controller import Controller  # noqa: E402
from focuslock.helperclient import HelperBridge  # noqa: E402
from focuslock.store import Store  # noqa: E402

store = Store(memory=True)
settings = Settings()
settings.env_overrides()
bridge = HelperBridge(dry_run=True)
controller = Controller(store, settings, bridge=bridge, emit=lambda *a: None, log=lambda m: print("  log:", m))

start = time.time()
result = controller.start_study({"apps": ["msedge.exe"], "study_minutes": 1})
print(f"start_study: {time.time() - start:.2f}s ok={result.get('ok')}")

time.sleep(1)
start = time.time()
end = controller.request_end("user")
print(f"request_end: {time.time() - start:.2f}s -> {end}")

start = time.time()
controller.shutdown()
print(f"shutdown: {time.time() - start:.2f}s")
