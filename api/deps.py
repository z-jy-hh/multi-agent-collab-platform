"""App-wide singletons, created once per server process."""

from __future__ import annotations

import concurrent.futures
import threading

from core.events import EventBus
from core.paths import configs_dir
from core.registry import load_registry


class AppState:
    def __init__(self) -> None:
        self.registry = load_registry(configs_dir())
        self.bus = EventBus()
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="graph-run")
        self.cancels: dict[str, threading.Event] = {}

    def register_cancel(self, run_id: str) -> threading.Event:
        ev = threading.Event()
        self.cancels[run_id] = ev
        return ev


STATE = AppState()
