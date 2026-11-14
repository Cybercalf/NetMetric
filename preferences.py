"""只保存界面列布局；错误或旧版本偏好不影响应用启动。"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import tempfile

log = logging.getLogger(__name__)


class Preferences:
    def __init__(self, path: Path | None = None):
        self.path = path or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "NetMetric" / "ui.json"

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) and data.get("version") == 1 else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            log.warning("无法读取界面布局，将使用默认值：%s", exc)
            return {}

    def save(self, columns: dict[str, int], show_route_metric: bool):
        data = {"version": 1, "columns": columns, "show_route_metric": show_route_metric}
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix="ui-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            temporary.replace(self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
