"""Local JSON artifact helpers for evaluation and learning files.

Artifacts are developer/user-reviewed files such as real labels, shadow results,
and proposed memory. They are intentionally separate from runtime state such as
ActionProposal, audit log, preferences, notifications, and scans.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping


def load_json_artifact(path: str | Path, *, default: Mapping[str, Any]) -> dict[str, Any]:
    artifact_path = Path(path)
    if not artifact_path.exists():
        return deepcopy(dict(default))
    data = json.loads(artifact_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("artifact JSON root must be an object")
    return data


def write_json_artifact(path: str | Path, data: Mapping[str, Any]) -> None:
    artifact_path = Path(path)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(dict(data), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def artifact_mapping(data: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    return value if isinstance(value, dict) else {}
