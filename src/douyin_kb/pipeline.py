from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .config import load_config
from .inbox import read_inbox
from .llm import analyze_daily, analyze_video
from .store import KnowledgeStore


def run_pipeline(config_path: Path) -> str:
    config = load_config(config_path)
    store = KnowledgeStore(config.root, config.state_file)
    records = read_inbox(config.inbox_jsonl)

    created: list[dict[str, Any]] = []
    skipped = 0

    for record in records:
        if not record.url and not record.title:
            skipped += 1
            continue
        if store.is_processed(record.key):
            skipped += 1
            continue

        analysis = analyze_video(record, config.llm)
        card_path = store.write_video_card(record, analysis)
        store.mark_processed(record.key, card_path)
        created.append(
            {
                "record": record,
                "analysis": analysis,
                "card_path": str(card_path.relative_to(config.root)),
            }
        )

    if created:
        today = datetime.now().date().isoformat()
        daily_items = store.read_video_cards_for_date(today)
        review = analyze_daily(daily_items, config.llm)
        store.write_daily_review(today, daily_items, review)
        store.append_hypothesis_pool(today, daily_items, review)
        store.write_index()

    return f"processed={len(created)} skipped={skipped}"
