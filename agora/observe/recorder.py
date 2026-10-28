from __future__ import annotations

import json
import os
from typing import IO, Optional

from agora.core.events import Event

class Recorder:
    def __init__(self, run_dir: str) -> None:
        self.run_dir = run_dir
        os.makedirs(run_dir, exist_ok=True)
        os.makedirs(os.path.join(run_dir, "prompts"), exist_ok=True)
        self._fh: Optional[IO[str]] = open(os.path.join(run_dir, "events.jsonl"), "a", encoding="utf-8")
        self._metrics_fh: Optional[IO[str]] = open(os.path.join(run_dir, "metrics.jsonl"), "a", encoding="utf-8")
        self.count = 0

    def on_event(self, ev: Event) -> None:
        if self._fh is None:
            return
        self._fh.write(json.dumps(ev.to_dict(), ensure_ascii=False, default=str) + "\n")
        self.count += 1
        if ev.kind == "metrics" and self._metrics_fh:
            self._metrics_fh.write(json.dumps(ev.data, default=str) + "\n")

    def flush(self) -> None:
        for fh in (self._fh, self._metrics_fh):
            if fh:
                fh.flush()

    def write_text(self, rel: str, text: str) -> None:
        path = os.path.join(self.run_dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def write_json(self, rel: str, obj) -> None:
        self.write_text(rel, json.dumps(obj, indent=2, ensure_ascii=False, default=str))

    def close(self) -> None:
        for fh in (self._fh, self._metrics_fh):
            if fh:
                fh.close()
        self._fh = self._metrics_fh = None
