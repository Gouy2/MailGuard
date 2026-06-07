"""Artifact storage for inbox cleaner dry-run previews."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..artifacts import load_json_artifact, write_json_artifact
from ..runtime_env import SERVER_ROOT


DEFAULT_CLEAN_PREVIEW_DIR = SERVER_ROOT / "data" / "clean_previews"


class CleanPreviewStorage:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else DEFAULT_CLEAN_PREVIEW_DIR

    def path_for(self, run_id: str) -> Path:
        return self.root / f"{run_id}.json"

    def save(self, run: dict[str, Any]) -> Path:
        path = self.path_for(str(run["run_id"]))
        run["artifact_path"] = str(path)
        write_json_artifact(path, run)
        return path

    def load(self, run_id: str) -> dict[str, Any]:
        return load_json_artifact(self.path_for(run_id), default={})
