from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class VideoRecord:
    url: str
    title: str = ""
    author: str = ""
    liked_at: str = ""
    ai_summary: str = ""
    raw_text: str = ""
    transcript: str = ""
    transcript_source: str = ""
    transcript_status: str = ""
    tags: list[str] = field(default_factory=list)
    source_id: str = ""
    like_count: int = 0
    comment_count: int = 0
    favorite_count: int = 0
    share_count: int = 0
    interaction_count: int = 0
    search_query: str = ""
    source_level: str = ""
    needs_verification: bool = False

    @property
    def key(self) -> str:
        source = self.source_id or self.url or f"{self.title}:{self.author}:{self.liked_at}"
        return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]

    @property
    def date(self) -> str:
        value = self.liked_at.strip()
        if value:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
            except ValueError:
                pass
        return datetime.now(timezone.utc).astimezone().date().isoformat()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VideoRecord":
        tags = data.get("tags") or []
        if isinstance(tags, str):
            tags = [item.strip() for item in tags.split(",") if item.strip()]
        return cls(
            url=str(data.get("url", "")).strip(),
            title=str(data.get("title", "")).strip(),
            author=str(data.get("author", "")).strip(),
            liked_at=str(data.get("liked_at", "")).strip(),
            ai_summary=str(data.get("ai_summary", "")).strip(),
            raw_text=str(data.get("raw_text", "")).strip(),
            transcript=str(data.get("transcript", "")).strip(),
            transcript_source=str(data.get("transcript_source", "")).strip(),
            transcript_status=str(data.get("transcript_status", "")).strip(),
            tags=list(tags),
            source_id=str(data.get("source_id", "")).strip(),
            like_count=_to_int(data.get("like_count", 0)),
            comment_count=_to_int(data.get("comment_count", 0)),
            favorite_count=_to_int(data.get("favorite_count", 0)),
            share_count=_to_int(data.get("share_count", 0)),
            interaction_count=_to_int(data.get("interaction_count", 0)),
            search_query=str(data.get("search_query", "")).strip(),
            source_level=str(data.get("source_level", "")).strip(),
            needs_verification=bool(data.get("needs_verification", False)),
        )


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
