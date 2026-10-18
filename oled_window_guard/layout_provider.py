"""Read-only, revision-aware access to Window Layouts KDE's KConfig data.

The provider deliberately exposes plain JSON-shaped records. It does not infer
which layout a live window occupies and never invokes a writer.
"""
from __future__ import annotations

import hashlib
import copy
import json
import math
import os
from pathlib import Path
import subprocess
import time
from typing import Callable, Sequence


SOURCE_ID = "kde:window-layouts"
GROUP = "Script-windowlayouts"
KEYS = ("CustomLayouts", "CustomGroups", "GroupOrder", "LayoutPadding")
MAX_LAYOUTS = 20
MAX_GROUPS = 64
_DEFAULTS = {"CustomLayouts": "[]", "CustomGroups": "[]", "GroupOrder": "[]", "LayoutPadding": "0"}


class LayoutProvider:
    """Read and validate a stable Window Layouts snapshot.

    `runner` receives an argv sequence and timeout and returns stdout text; it
    is injectable so tests never need a live KWin configuration. A snapshot is
    sampled twice with a short debounce between samples because upstream saves
    related KConfig keys separately.
    """

    def __init__(self, *, runner: Callable[[Sequence[str], float], str] | None = None,
                 config_file: os.PathLike[str] | str | None = None,
                 timeout: float = 1.5, debounce_seconds: float = 0.05):
        if timeout <= 0 or debounce_seconds < 0:
            raise ValueError("timeout must be positive and debounce non-negative")
        self.runner = runner or self._run
        self.config_file = Path(config_file) if config_file is not None else self._default_config_file()
        self.timeout = timeout
        self.debounce_seconds = debounce_seconds
        self.last_good: dict | None = None

    @staticmethod
    def _default_config_file() -> Path:
        config_home = os.environ.get("XDG_CONFIG_HOME")
        root = Path(config_home).expanduser() if config_home else Path.home() / ".config"
        if not root.is_absolute():
            root = Path.home() / ".config"
        return root / "kwinrc"

    @staticmethod
    def _run(argv: Sequence[str], timeout: float) -> str:
        completed = subprocess.run(argv, check=True, capture_output=True, text=True,
                                   timeout=timeout)
        return completed.stdout.rstrip("\n")

    @staticmethod
    def _missing_token(key: str) -> str:
        return f"__OLED_WINDOW_GUARD_MISSING_{key}__"

    def _sample(self) -> dict[str, str]:
        sample = {}
        for key in KEYS:
            token = self._missing_token(key)
            argv = ["kreadconfig6", "--file", str(self.config_file), "--group", GROUP,
                    "--key", key, "--default", token]
            sample[key] = self.runner(argv, self.timeout)
        return sample

    @staticmethod
    def _empty(status: str, diagnostic: str, *, revision: str | None = None,
               last_good: dict | None = None) -> dict:
        fallback = last_good or {}
        return {
            "sourceId": SOURCE_ID, "status": status, "revision": revision,
            "executionReady": False,
            "layouts": copy.deepcopy(fallback.get("layouts", [])),
            "groups": copy.deepcopy(fallback.get("groups", [])),
            "groupOrder": copy.deepcopy(fallback.get("groupOrder", [])),
            "padding": fallback.get("padding", 0),
            "diagnostics": [diagnostic],
        }

    def read_snapshot(self) -> dict:
        """Return a stable validated snapshot; unstable data is display-only."""
        try:
            first = self._sample()
            if self.debounce_seconds:
                time.sleep(self.debounce_seconds)
            second = self._sample()
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            return self._empty("unavailable", f"cannot read Window Layouts settings: {exc}",
                               last_good=self.last_good)
        if first != second:
            return self._empty("unstable", "Window Layouts settings changed during the read; retry after save completes",
                               last_good=self.last_good)
        if all(first[key] == self._missing_token(key) for key in KEYS):
            return self._empty("missing", "Window Layouts custom data is not configured")
        try:
            normalized = self._validate(first)
        except ValueError as exc:
            revision = self._revision(first)
            return self._empty("invalid", str(exc), revision=revision, last_good=self.last_good)
        revision = self._revision(first)
        result = {"sourceId": SOURCE_ID, "status": "available", "revision": revision,
                  "executionReady": True, **normalized, "diagnostics": []}
        self.last_good = copy.deepcopy(result)
        return result

    def is_current(self, revision: str) -> bool:
        """Re-read all canonical keys; only an available matching revision passes."""
        if not isinstance(revision, str) or not revision:
            return False
        current = self.read_snapshot()
        return current["status"] == "available" and current["executionReady"] and current["revision"] == revision

    @staticmethod
    def _revision(raw: dict[str, str]) -> str:
        canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def _validate(cls, raw: dict[str, str]) -> dict:
        parsed = {}
        for key in KEYS[:3]:
            value = raw[key]
            if value == cls._missing_token(key):
                value = _DEFAULTS[key]
            try:
                parsed[key] = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{key} is malformed JSON") from exc
        for key in KEYS[:3]:
            if not isinstance(parsed[key], list):
                raise ValueError(f"{key} must be a JSON array")

        groups_in = parsed["CustomGroups"]
        if len(groups_in) > MAX_GROUPS:
            raise ValueError(f"CustomGroups exceeds the supported limit of {MAX_GROUPS}")
        groups = []
        group_ids = set()
        for item in groups_in:
            if not isinstance(item, dict):
                raise ValueError("CustomGroups contains a non-object record")
            group_id, name = item.get("id"), item.get("name")
            if not isinstance(group_id, str) or not group_id or not isinstance(name, str) or not name.strip():
                raise ValueError("CustomGroups contains a missing or invalid id/name")
            if group_id in group_ids:
                raise ValueError(f"duplicate group ID: {group_id}")
            group_ids.add(group_id)
            groups.append({"id": group_id, "name": name.strip(), "layoutIds": []})

        layouts_in = parsed["CustomLayouts"]
        if len(layouts_in) > MAX_LAYOUTS:
            raise ValueError(f"CustomLayouts exceeds the supported limit of {MAX_LAYOUTS}")
        layouts, layout_ids = [], set()
        for index, item in enumerate(layouts_in):
            if not isinstance(item, dict):
                raise ValueError("CustomLayouts contains a non-object record")
            layout_id = item.get("id")
            if not isinstance(layout_id, str) or not layout_id:
                raise ValueError(f"layout {index + 1} has no stable ID")
            if layout_id in layout_ids:
                raise ValueError(f"duplicate layout ID: {layout_id}")
            layout_ids.add(layout_id)
            name = item.get("name")
            if not isinstance(name, str):
                raise ValueError(f"layout {layout_id} has an invalid name")
            geometry = [item.get(field) for field in ("x", "y", "width", "height")]
            if any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(value) for value in geometry):
                raise ValueError(f"layout {layout_id} has non-finite geometry")
            x, y, width, height = geometry
            if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1.000001 or y + height > 1.000001:
                raise ValueError(f"layout {layout_id} is outside normalized bounds")
            group_id = item.get("groupId", "")
            if not isinstance(group_id, str):
                raise ValueError(f"layout {layout_id} has an invalid groupId")
            if group_id and group_id not in group_ids:
                raise ValueError(f"layout {layout_id} references missing group {group_id}")
            layout = {"id": layout_id, "name": name.strip() or f"Custom Layout {index + 1}",
                      "x": x, "y": y, "width": width, "height": height, "groupId": group_id}
            slot = item.get("shortcutSlot")
            if isinstance(slot, int) and not isinstance(slot, bool) and 1 <= slot <= MAX_LAYOUTS:
                layout["shortcutSlot"] = slot
            layouts.append(layout)
            if group_id:
                next(group for group in groups if group["id"] == group_id)["layoutIds"].append(layout_id)

        order = parsed["GroupOrder"]
        if any(not isinstance(value, str) for value in order):
            raise ValueError("GroupOrder must contain only string IDs")
        # GroupOrder is UI presentation metadata. Retain recognized IDs once;
        # do not interpret unrecognized built-in IDs as assignments.
        group_order = list(dict.fromkeys(value for value in order if value in group_ids))

        padding_raw = raw["LayoutPadding"]
        if padding_raw == cls._missing_token("LayoutPadding"):
            padding_raw = "0"
        try:
            numeric_padding = float(padding_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("LayoutPadding is not numeric") from exc
        if not math.isfinite(numeric_padding):
            raise ValueError("LayoutPadding is not finite")
        padding = max(0, min(200, math.floor(numeric_padding + 0.5)))
        return {"layouts": layouts, "groups": groups, "groupOrder": group_order, "padding": padding}
