from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_config
from .douyin_web import (
    discover_douyin_accounts,
    login_douyin,
    scrape_douyin_accounts,
    scrape_douyin_likes,
    scrape_douyin_search_queries,
)
from .pipeline import run_pipeline
from .server import run_server
from .teach import teach_douyin_ai
from .transcript import (
    fetch_douyin_ai_from_current_page,
    fetch_transcripts_for_top_cards,
    mine_douyin_author_works,
    mine_douyin_search_with_review,
    review_then_fetch_douyin_ai_current_page,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Douyin knowledge-base automation")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Ingest inbox items and write daily review")
    run_parser.add_argument("--config", default="config.json", help="Path to config JSON")

    serve_parser = subparsers.add_parser("serve", help="Receive video records over local HTTP")
    serve_parser.add_argument("--config", default="config.json", help="Path to config JSON")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--token", default="", help="Optional bearer token")

    login_parser = subparsers.add_parser("login-douyin", help="Open Douyin web and save login state")
    login_parser.add_argument("--config", default="config.json", help="Path to config JSON")
    login_parser.add_argument("--wait-seconds", type=int, default=300)

    scrape_parser = subparsers.add_parser("scrape-douyin", help="Scrape visible Douyin video links into inbox")
    scrape_parser.add_argument("--config", default="config.json", help="Path to config JSON")
    scrape_parser.add_argument("--url", default="", help="Override Douyin page URL")
    scrape_parser.add_argument("--scroll-rounds", type=int, default=-1)
    scrape_parser.add_argument("--detail-pages", type=int, default=0, help="Open first N videos for more visible text")
    scrape_parser.add_argument("--manual-seconds", type=int, default=0, help="Wait while you navigate to the target page")
    scrape_parser.add_argument("--no-keyword-filter", action="store_true")

    discover_parser = subparsers.add_parser("discover-douyin-accounts", help="Search Douyin account candidates")
    discover_parser.add_argument("--config", default="config.json", help="Path to config JSON")
    discover_parser.add_argument("--query", action="append", default=[], help="Account search query; repeatable")
    discover_parser.add_argument("--per-query", type=int, default=8)
    discover_parser.add_argument("--manual-seconds", type=int, default=0)

    accounts_parser = subparsers.add_parser("scrape-douyin-accounts", help="Scrape videos from confirmed account URLs")
    accounts_parser.add_argument("--config", default="config.json", help="Path to config JSON")
    accounts_parser.add_argument("--account-url", action="append", default=[], help="Account homepage URL; repeatable")
    accounts_parser.add_argument("--scroll-rounds", type=int, default=-1)
    accounts_parser.add_argument("--detail-pages-per-account", type=int, default=0)
    accounts_parser.add_argument("--no-keyword-filter", action="store_true")

    search_parser = subparsers.add_parser("scrape-douyin-search", help="Scrape video snippets from Douyin search queries")
    search_parser.add_argument("--config", default="config.json", help="Path to config JSON")
    search_parser.add_argument("--query", action="append", default=[], help="Search query; repeatable")
    search_parser.add_argument("--per-query", type=int, default=10)
    search_parser.add_argument("--manual-seconds", type=int, default=0)
    search_parser.add_argument("--no-keyword-filter", action="store_true")
    search_parser.add_argument("--min-likes", type=int, default=0)
    search_parser.add_argument("--min-comments", type=int, default=0)
    search_parser.add_argument("--min-interactions", type=int, default=0)
    search_parser.add_argument("--scroll-rounds", type=int, default=2)

    transcript_parser = subparsers.add_parser(
        "fetch-douyin-ai-transcripts",
        help="Open top engagement video cards, ask Douyin AI for dialogue text, and write transcripts back",
    )
    transcript_parser.add_argument("--config", default="config.json", help="Path to config JSON")
    transcript_parser.add_argument("--date", default="", help="Video-card date, default today")
    transcript_parser.add_argument("--limit", type=int, default=3)
    transcript_parser.add_argument("--min-interactions", type=int, default=500)
    transcript_parser.add_argument("--manual-seconds", type=int, default=0, help="Wait after opening search page for captcha/manual help")
    transcript_parser.add_argument(
        "--prompt",
        default="请给我完整的对白文本",
    )
    transcript_parser.add_argument("--no-douyin-ai", action="store_true")
    transcript_parser.add_argument("--use-asr", action="store_true", help="Fallback to local Whisper ASR when a media URL is available")
    transcript_parser.add_argument("--whisper-model", default="base")

    current_parser = subparsers.add_parser(
        "fetch-douyin-ai-current",
        help="Ask Douyin AI on the current manually opened video page; no search or result clicking",
    )
    current_parser.add_argument("--config", default="config.json", help="Path to config JSON")
    current_parser.add_argument("--url", default="https://www.douyin.com/", help="Initial page to open")
    current_parser.add_argument("--manual-seconds", type=int, default=60, help="Time for you to open the target video before automation starts")
    current_parser.add_argument("--prompt", default="请给我完整的对白文本")
    current_parser.add_argument("--output", default="", help="Markdown output path when not writing to a card")
    current_parser.add_argument("--card", default="", help="Optional existing video-card md path to update")

    review_current_parser = subparsers.add_parser(
        "review-douyin-ai-current",
        help="Create a current-video review bundle, wait for y/n approval, then ask Douyin AI on the same page",
    )
    review_current_parser.add_argument("--config", default="config.json", help="Path to config JSON")
    review_current_parser.add_argument("--url", default="https://www.douyin.com/", help="Initial page to open")
    review_current_parser.add_argument("--manual-seconds", type=int, default=60, help="Time for you to open the target video before review")
    review_current_parser.add_argument("--prompt", default="请给我完整的对白文本")
    review_current_parser.add_argument("--output", default="", help="Markdown output path when not writing to a card")
    review_current_parser.add_argument("--card", default="", help="Optional existing video-card md path to update")

    mine_search_parser = subparsers.add_parser(
        "mine-douyin-search",
        help="Review search candidates, wait for agent pick, open picked video, review detail, then ask Douyin AI",
    )
    mine_search_parser.add_argument("--config", default="config.json", help="Path to config JSON")
    mine_search_parser.add_argument("--query", default="", help="Search query")
    mine_search_parser.add_argument("--url", default="", help="Search page URL, overrides query")
    mine_search_parser.add_argument("--prompt", default="请给我完整的对白文本")
    mine_search_parser.add_argument("--output", default="", help="Markdown output path")

    mine_author_parser = subparsers.add_parser(
        "mine-douyin-author",
        help="Open a Douyin author page, collect works, ask Douyin AI for each video, and save answers",
    )
    mine_author_parser.add_argument("--config", default="config.json", help="Path to config JSON")
    mine_author_parser.add_argument("--url", required=True, help="Douyin author homepage URL")
    mine_author_parser.add_argument("--scroll-rounds", type=int, default=12)
    mine_author_parser.add_argument("--limit", type=int, default=0, help="0 means all collected works")
    mine_author_parser.add_argument("--manual-seconds", type=int, default=0, help="Wait on author page for captcha/login/manual help")
    mine_author_parser.add_argument("--prompt", default="请给我完整的对白文本")
    mine_author_parser.add_argument("--no-skip-existing", action="store_true")

    teach_parser = subparsers.add_parser(
        "teach-douyin-ai",
        help="Record one manual Douyin AI operation and save a reusable teach profile",
    )
    teach_parser.add_argument("--config", default="config.json", help="Path to config JSON")
    teach_parser.add_argument("--url", default="https://www.douyin.com/", help="Page to open before manual teaching")
    teach_parser.add_argument("--wait-seconds", type=int, default=240, help="How long to record manual operation")
    teach_parser.add_argument("--output", default="", help="Output profile JSON path")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "run":
        result = run_pipeline(Path(args.config))
        print(result)
        return

    if args.command == "serve":
        run_server(Path(args.config), host=args.host, port=args.port, token=args.token)
        return

    if args.command == "login-douyin":
        config = load_config(Path(args.config))
        print(login_douyin(config, wait_seconds=args.wait_seconds))
        return

    if args.command == "scrape-douyin":
        config = load_config(Path(args.config))
        rounds = None if args.scroll_rounds < 0 else args.scroll_rounds
        print(
            scrape_douyin_likes(
                config,
                target_url=args.url or None,
                scroll_rounds=rounds,
                detail_pages=args.detail_pages,
                manual_seconds=args.manual_seconds,
                keyword_filter=not args.no_keyword_filter,
            )
        )
        return

    if args.command == "discover-douyin-accounts":
        config = load_config(Path(args.config))
        print(
            discover_douyin_accounts(
                config,
                queries=args.query or None,
                per_query=args.per_query,
                manual_seconds=args.manual_seconds,
            )
        )
        return

    if args.command == "scrape-douyin-accounts":
        config = load_config(Path(args.config))
        rounds = None if args.scroll_rounds < 0 else args.scroll_rounds
        print(
            scrape_douyin_accounts(
                config,
                account_urls=args.account_url or None,
                scroll_rounds=rounds,
                detail_pages_per_account=args.detail_pages_per_account,
                keyword_filter=not args.no_keyword_filter,
            )
        )
        return

    if args.command == "scrape-douyin-search":
        config = load_config(Path(args.config))
        print(
            scrape_douyin_search_queries(
                config,
                queries=args.query or None,
                per_query=args.per_query,
                manual_seconds=args.manual_seconds,
                keyword_filter=not args.no_keyword_filter,
                min_likes=args.min_likes,
                min_comments=args.min_comments,
                min_interactions=args.min_interactions,
                scroll_rounds=args.scroll_rounds,
            )
        )
        return

    if args.command == "fetch-douyin-ai-transcripts":
        config = load_config(Path(args.config))
        print(
            fetch_transcripts_for_top_cards(
                config,
                date=args.date or None,
                limit=args.limit,
                min_interactions=args.min_interactions,
                manual_seconds=args.manual_seconds,
                use_douyin_ai=not args.no_douyin_ai,
                ai_prompt=args.prompt,
                use_asr=args.use_asr,
                whisper_model=args.whisper_model,
            )
        )
        return

    if args.command == "fetch-douyin-ai-current":
        config = load_config(Path(args.config))
        print(
            fetch_douyin_ai_from_current_page(
                config,
                url=args.url,
                manual_seconds=args.manual_seconds,
                ai_prompt=args.prompt,
                output=Path(args.output) if args.output else None,
                card_path=Path(args.card) if args.card else None,
            )
        )
        return

    if args.command == "review-douyin-ai-current":
        config = load_config(Path(args.config))
        print(
            review_then_fetch_douyin_ai_current_page(
                config,
                url=args.url,
                manual_seconds=args.manual_seconds,
                ai_prompt=args.prompt,
                output=Path(args.output) if args.output else None,
                card_path=Path(args.card) if args.card else None,
            )
        )
        return

    if args.command == "mine-douyin-search":
        config = load_config(Path(args.config))
        print(
            mine_douyin_search_with_review(
                config,
                query=args.query,
                url=args.url,
                ai_prompt=args.prompt,
                output=Path(args.output) if args.output else None,
            )
        )
        return

    if args.command == "mine-douyin-author":
        config = load_config(Path(args.config))
        print(
            mine_douyin_author_works(
                config,
                url=args.url,
                scroll_rounds=args.scroll_rounds,
                limit=args.limit,
                manual_seconds=args.manual_seconds,
                ai_prompt=args.prompt,
                skip_existing=not args.no_skip_existing,
            )
        )
        return

    if args.command == "teach-douyin-ai":
        config = load_config(Path(args.config))
        print(
            teach_douyin_ai(
                config,
                url=args.url,
                wait_seconds=args.wait_seconds,
                output=Path(args.output) if args.output else None,
            )
        )
        return

    parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
