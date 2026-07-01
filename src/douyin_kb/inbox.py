from __future__ import annotations

import json
from pathlib import Path

from .models import VideoRecord


def read_inbox(path: Path) -> list[VideoRecord]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return []

    records: list[VideoRecord] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        clean = line.strip()
        if not clean:
            continue
        try:
            data = json.loads(clean)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"Invalid record at {path}:{line_number}: expected object")
        records.append(VideoRecord.from_dict(data))
    return records


def append_inbox(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_inbox_if_new(path: Path, record: dict) -> bool:
    key = str(record.get("source_id") or record.get("url") or "").strip()
    if key and key in _existing_record_keys(path):
        return False
    append_inbox(path, record)
    return True


def _existing_record_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        clean = line.strip()
        if not clean:
            continue
        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        for field in ("source_id", "url"):
            value = str(data.get(field, "")).strip()
            if value:
                keys.add(value)
    return keys
