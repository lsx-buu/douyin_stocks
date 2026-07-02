from __future__ import annotations

import html
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from .config import AppConfig
from .douyin_web import DOUYIN_VIEWPORT, _launch_context, _load_playwright


DEFAULT_DOUYIN_AI_PROMPT = "\u8bf7\u7ed9\u6211\u5b8c\u6574\u7684\u5bf9\u767d\u6587\u672c"


@dataclass(frozen=True)
class TranscriptCandidate:
    path: Path
    title: str
    author: str
    search_query: str
    source_url: str
    interaction_count: int


def fetch_transcripts_for_top_cards(
    config: AppConfig,
    *,
    date: str | None = None,
    limit: int = 3,
    min_interactions: int = 500,
    manual_seconds: int = 0,
    use_douyin_ai: bool = True,
    ai_prompt: str = "请给我完整的对白文本",
    use_asr: bool = False,
    whisper_model: str = "base",
) -> str:
    target_date = date or datetime.now().date().isoformat()
    candidates = _load_transcript_candidates(
        config.root,
        target_date,
        min_interactions=min_interactions,
        allow_chapter_only=use_asr or use_douyin_ai,
    )
    selected = candidates[: max(limit, 0)]
    if not selected:
        _write_transcript_queue(config.root, target_date, candidates)
        return f"transcripts=0 reason=no_candidates date={target_date}"

    sync_playwright = _load_playwright()
    fetched = 0
    missing = 0
    failed = 0

    with sync_playwright() as playwright:
        context = _launch_context(playwright, config, headless=config.douyin_web.headless)
        page = context.pages[0] if context.pages else context.new_page()
        for candidate in selected:
            detail_url = candidate.source_url if _is_detail_url(candidate.source_url) else ""
            result: dict[str, str] = {}
            search_click_status = ""
            try:
                if use_douyin_ai and (candidate.search_query or candidate.title):
                    result = _extract_transcript_from_search_result(
                        context,
                        page,
                        candidate.title or candidate.search_query,
                        candidate.title,
                        candidate.author,
                        manual_seconds=manual_seconds,
                        ai_prompt=ai_prompt,
                    )
                    search_click_status = str(result.get("source") or "")
                    detail_url = str(result.get("detail_url") or detail_url)

                if not detail_url:
                    detail_url = _find_detail_url_from_search(
                        page,
                        candidate.search_query or candidate.title,
                        candidate.title,
                        candidate.author,
                        manual_seconds=manual_seconds,
                    )
                if not detail_url:
                    _mark_transcript_status(candidate.path, "未找到原视频链接", search_click_status or "无", "")
                    missing += 1
                    continue

                if not result.get("transcript"):
                    detail_url = _canonical_detail_url(detail_url) or detail_url
                    result = _extract_transcript_from_detail(
                        context,
                        detail_url,
                        use_douyin_ai=use_douyin_ai,
                        ai_prompt=ai_prompt,
                        use_asr=use_asr,
                        whisper_model=whisper_model,
                        media_dir=config.root / ".state" / "media",
                    )
                if result.get("transcript"):
                    if not _transcript_matches_expected(
                        str(result["transcript"]),
                        " ".join([candidate.title, candidate.author, candidate.search_query]),
                    ):
                        _mark_transcript_status(
                            candidate.path,
                            "未获取到对白",
                            "抖音AI返回疑似错视频对白，未保存",
                            detail_url,
                        )
                        missing += 1
                        continue
                    _write_transcript_to_card(
                        candidate.path,
                        transcript=str(result["transcript"]).strip(),
                        source=str(result.get("source") or "未知"),
                        detail_url=detail_url,
                        status="已获取待核验",
                    )
                    fetched += 1
                else:
                    _mark_transcript_status(
                        candidate.path,
                        "未获取到对白",
                        str(result.get("source") or "无"),
                        detail_url,
                    )
                    missing += 1
            except Exception as exc:
                _mark_transcript_status(candidate.path, f"采集失败：{exc.__class__.__name__}: {str(exc)[:120]}", "无", detail_url)
                failed += 1
        context.close()

    _write_transcript_queue(config.root, target_date, candidates)
    return f"transcripts={fetched} missing={missing} failed={failed} candidates={len(candidates)} date={target_date}"


def fetch_douyin_ai_from_current_page(
    config: AppConfig,
    *,
    url: str = "https://www.douyin.com/",
    manual_seconds: int = 60,
    ai_prompt: str = "请给我完整的对白文本",
    output: Path | None = None,
    card_path: Path | None = None,
) -> str:
    sync_playwright = _load_playwright()
    output_path = output or (config.root / ".state" / "current_douyin_ai_answer.md")
    with sync_playwright() as playwright:
        context = _launch_context(playwright, config, headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.set_viewport_size(dict(DOUYIN_VIEWPORT))
        except Exception:
            pass
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        if manual_seconds > 0:
            print(f"manual_navigation={manual_seconds}s", flush=True)
            print("open_target_video_then_wait=current_page_only_no_search", flush=True)
            page.wait_for_timeout(manual_seconds * 1000)
        _disable_douyin_autoplay(page)
        ai_result = _extract_transcript_via_douyin_ai(page, ai_prompt)
        transcript = str(ai_result.get("transcript") or "").strip()
        source = str(ai_result.get("status") or "抖音AI未返回可保存内容")
        detail_url = page.url
        if transcript and card_path:
            _write_transcript_to_card(
                card_path,
                transcript=transcript,
                source=source,
                detail_url=detail_url,
                status="已获取待核验",
            )
        elif transcript:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                "\n".join(
                    [
                        "---",
                        f"created_at: {datetime.now().isoformat(timespec='seconds')}",
                        f"source_url: {detail_url}",
                        f"prompt: {ai_prompt}",
                        f"source: {source}",
                        "status: 已获取待核验",
                        "---",
                        "",
                        "# 当前抖音视频问AI生成内容",
                        "",
                        transcript,
                        "",
                    ]
                ),
                encoding="utf-8",
            )
        try:
            context.close()
        except Exception:
            pass
    if not transcript:
        return f"current_transcript=0 source={source} url={detail_url}"
    if card_path:
        return f"current_transcript=1 card={card_path} chars={len(transcript)} source={source}"
    return f"current_transcript=1 output={output_path} chars={len(transcript)} source={source}"


def review_then_fetch_douyin_ai_current_page(
    config: AppConfig,
    *,
    url: str = "https://www.douyin.com/",
    manual_seconds: int = 60,
    decision_timeout_seconds: int = 180,
    ai_prompt: str = "请给我完整的对白文本",
    output: Path | None = None,
    card_path: Path | None = None,
) -> str:
    sync_playwright = _load_playwright()
    output_path = output or (config.root / ".state" / "current_douyin_ai_answer.md")
    review_json = config.root / ".state" / "current_douyin_review.json"
    review_md = config.root / ".state" / "current_douyin_review.md"
    decision_path = config.root / ".state" / "current_douyin_decision.txt"
    screenshot_path = config.root / ".state" / "debug" / "current_douyin_review.png"
    review_json.parent.mkdir(parents=True, exist_ok=True)
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        decision_path.unlink()
    except FileNotFoundError:
        pass

    with sync_playwright() as playwright:
        context = _launch_context(playwright, config, headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.set_viewport_size(dict(DOUYIN_VIEWPORT))
        except Exception:
            pass
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        if manual_seconds > 0:
            print(f"manual_navigation={manual_seconds}s", flush=True)
            print("open_target_video_then_wait=review_gate_current_page_only", flush=True)
            page.wait_for_timeout(manual_seconds * 1000)

        _disable_douyin_autoplay(page)
        _open_comment_panel(page)
        page.wait_for_timeout(1200)
        bundle = _current_video_review_bundle(page)
        bundle["screenshot"] = str(screenshot_path)
        try:
            page.screenshot(path=str(screenshot_path), full_page=False)
        except Exception:
            pass
        review_json.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
        review_md.write_text(_review_bundle_markdown(bundle), encoding="utf-8")
        print(f"review_bundle={review_md}", flush=True)
        print(f"decision_file={decision_path}", flush=True)
        print("decision_required=write y or n to decision_file", flush=True)

        deadline = datetime.now().timestamp() + max(decision_timeout_seconds, 1)
        decision = ""
        while datetime.now().timestamp() < deadline:
            try:
                decision = decision_path.read_text(encoding="utf-8-sig").strip().lower()
            except FileNotFoundError:
                decision = ""
            if decision:
                break
            page.wait_for_timeout(1000)
        if decision not in {"y", "yes", "1", "true", "go"}:
            try:
                context.close()
            except Exception:
                pass
            return f"review_current=skipped review={review_md} url={bundle.get('url', '')}"

        ai_result = _extract_transcript_via_douyin_ai(page, ai_prompt)
        transcript = str(ai_result.get("transcript") or "").strip()
        source = str(ai_result.get("status") or "抖音AI未返回可保存内容")
        detail_url = page.url
        if transcript and card_path:
            _write_transcript_to_card(
                card_path,
                transcript=transcript,
                source=source,
                detail_url=detail_url,
                status="已获取待核验",
            )
        elif transcript:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                "\n".join(
                    [
                        "---",
                        f"created_at: {datetime.now().isoformat(timespec='seconds')}",
                        f"source_url: {detail_url}",
                        f"prompt: {ai_prompt}",
                        f"source: {source}",
                        "status: 已获取待核验",
                        f"review_bundle: {review_md}",
                        "---",
                        "",
                        "# 当前抖音视频问AI生成内容",
                        "",
                        "## 复核摘要",
                        "",
                        str(bundle.get("review_hint") or "").strip(),
                        "",
                        "## 可见评论片段",
                        "",
                        "\n".join(f"- {item}" for item in bundle.get("comments_preview", [])[:20]),
                        "",
                        "## 问AI生成内容",
                        "",
                        transcript,
                        "",
                    ]
                ),
                encoding="utf-8",
            )
        try:
            context.close()
        except Exception:
            pass
    if not transcript:
        return f"review_current=approved transcript=0 source={source} review={review_md} url={detail_url}"
    if card_path:
        return f"review_current=approved transcript=1 card={card_path} chars={len(transcript)} source={source}"
    return f"review_current=approved transcript=1 output={output_path} chars={len(transcript)} source={source}"


def mine_douyin_author_works(
    config: AppConfig,
    *,
    url: str,
    scroll_rounds: int = 12,
    limit: int = 0,
    manual_seconds: int = 0,
    ai_prompt: str = "请给我完整的对白文本",
    skip_existing: bool = True,
) -> str:
    ai_prompt = _clean_douyin_ai_prompt(ai_prompt)
    sync_playwright = _load_playwright()
    state_dir = config.root / ".state"
    debug_dir = state_dir / "debug"
    works_md = state_dir / "current_douyin_author_works.md"
    works_json = state_dir / "current_douyin_author_works.json"
    state_dir.mkdir(parents=True, exist_ok=True)
    debug_dir.mkdir(parents=True, exist_ok=True)

    fetched = 0
    missing = 0
    skipped = 0
    outputs: list[str] = []
    existing_urls = _existing_mined_source_urls(config.root) if skip_existing else set()

    with sync_playwright() as playwright:
        context = _launch_context(playwright, config, headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.set_viewport_size(dict(DOUYIN_VIEWPORT))
        except Exception:
            pass

        author_url = _author_home_url(url)
        playlist_result = _mine_author_manual_playlist_flow(
            page,
            config=config,
            author_url=author_url,
            works_md=works_md,
            state_dir=state_dir,
            debug_dir=debug_dir,
            existing_urls=existing_urls,
            ai_prompt=ai_prompt,
            skip_existing=skip_existing,
            max_items=limit if limit > 0 else 0,
            scroll_rounds=scroll_rounds,
            manual_seconds=manual_seconds,
        )
        try:
            context.close()
        except Exception:
            pass
        return (
            f"mine_author_manual fetched={playlist_result.get('fetched', 0)} "
            f"missing={playlist_result.get('missing', 0)} "
            f"skipped={playlist_result.get('skipped', 0)} "
            f"failed_step={playlist_result.get('failed_step', '')} "
            f"outputs={';'.join(playlist_result.get('outputs', []) or [])}"
        )

        page.goto(author_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(10000)
        if manual_seconds > 0:
            print(f"manual_navigation={manual_seconds}s author_page", flush=True)
            page.wait_for_timeout(manual_seconds * 1000)
        _dismiss_douyin_overlays(page)
        _click_author_works_tab(page)
        page.wait_for_timeout(1500)

        author_info = _author_profile_info(page, author_url)
        works_md.write_text(_author_works_markdown(author_info, []), encoding="utf-8")
        playlist_result = _mine_author_playlist_from_page(
            page,
            config=config,
            author_url=author_url,
            author_info=author_info,
            works_md=works_md,
            state_dir=state_dir,
            debug_dir=debug_dir,
            existing_urls=existing_urls,
            ai_prompt=ai_prompt,
            skip_existing=skip_existing,
            max_items=limit if limit > 0 else (_author_profile_work_count(page) or 1),
            first_item=None,
        )
        if playlist_result.get("started"):
            fetched += int(playlist_result.get("fetched", 0) or 0)
            missing += int(playlist_result.get("missing", 0) or 0)
            skipped += int(playlist_result.get("skipped", 0) or 0)
            outputs.extend(playlist_result.get("outputs", []) or [])
            try:
                context.close()
            except Exception:
                pass
            return (
                f"mine_author_playlist fetched={fetched} missing={missing} skipped={skipped} "
                f"outputs={';'.join(outputs)}"
            )

        works = _collect_author_work_items(page, scroll_rounds=scroll_rounds)
        works = _filter_author_work_items(works, author_info)
        if limit > 0:
            works = works[:limit]
        works_json.write_text(
            json.dumps({"author": author_info, "works": works}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        works_md.write_text(_author_works_markdown(author_info, works), encoding="utf-8")
        print(f"author_works={works_md}", flush=True)
        print(f"author={author_info.get('name', '')} works={len(works)}", flush=True)

        for index, item in enumerate(works, start=1):
            raw_url = str(item.get("url") or "")
            detail_url = _canonical_detail_url(raw_url) or raw_url
            if not detail_url:
                skipped += 1
                continue
            if skip_existing and (_canonical_detail_url(raw_url) in existing_urls or _canonical_detail_url(detail_url) in existing_urls):
                skipped += 1
                print(f"author_item={index}/{len(works)} skipped=existing", flush=True)
                continue

            open_url = _author_ai_detail_url(raw_url, str(author_info.get("url") or author_url)) or f"author_work:{_video_id_from_url(raw_url) or detail_url}"
            print(f"author_item={index}/{len(works)} open={open_url}", flush=True)
            work_page = context.new_page()
            try:
                work_page.set_viewport_size(dict(DOUYIN_VIEWPORT))
            except Exception:
                pass
            try:
                ai_result, bundle, item_review_md, final_url = _extract_author_item_ai_with_retries(
                    work_page,
                    item=item,
                    ai_prompt=ai_prompt,
                    state_dir=state_dir,
                    debug_dir=debug_dir,
                    author_info=author_info,
                    author_url=str(author_info.get("url") or author_url),
                    index=index,
                    total=len(works),
                )
                transcript = str(ai_result.get("transcript") or "").strip()
                source = str(ai_result.get("status") or "抖音AI未返回可保存内容")
                prompt_used = str(ai_result.get("prompt") or ai_prompt)
                if transcript:
                    target_output = _default_author_output_path(config.root, author_info, item, index)
                    source_url = _author_ai_detail_url(raw_url, str(author_info.get("url") or author_url)) or final_url or page.url
                    _write_mined_ai_answer(
                        target_output,
                        detail_url=source_url,
                        ai_prompt=prompt_used,
                        source=source,
                        candidates_md=works_md,
                        review_md=item_review_md,
                        picked=item,
                        bundle=bundle,
                        transcript=transcript,
                    )
                    outputs.append(str(target_output))
                    existing_urls.add(_canonical_detail_url(source_url))
                    fetched += 1
                    print(f"author_item={index}/{len(works)} saved={target_output} chars={len(transcript)}", flush=True)
                else:
                    missing += 1
                    print(f"author_item={index}/{len(works)} missing source={source}", flush=True)
            except Exception as exc:
                missing += 1
                print(f"author_item={index}/{len(works)} failed={exc.__class__.__name__}:{str(exc)[:120]}", flush=True)
            finally:
                try:
                    work_page.close()
                except Exception:
                    pass

        try:
            context.close()
        except Exception:
            pass

    return f"mine_author fetched={fetched} missing={missing} skipped={skipped} works={len(works)} outputs={';'.join(outputs)}"


def mine_douyin_search_with_review(
    config: AppConfig,
    *,
    query: str = "",
    url: str = "",
    decision_timeout_seconds: int = 180,
    ai_prompt: str = "请给我完整的对白文本",
    output: Path | None = None,
) -> str:
    sync_playwright = _load_playwright()
    output_path = output or (config.root / ".state" / "current_douyin_ai_answer.md")
    state_dir = config.root / ".state"
    debug_dir = state_dir / "debug"
    candidates_md = state_dir / "current_douyin_search_candidates.md"
    candidates_json = state_dir / "current_douyin_search_candidates.json"
    review_md = state_dir / "current_douyin_review.md"
    review_json = state_dir / "current_douyin_review.json"
    pick_path = state_dir / "current_douyin_pick.txt"
    decision_path = state_dir / "current_douyin_decision.txt"
    search_screenshot = debug_dir / "current_douyin_search_candidates.png"
    review_screenshot = debug_dir / "current_douyin_review.png"
    state_dir.mkdir(parents=True, exist_ok=True)
    debug_dir.mkdir(parents=True, exist_ok=True)
    for path in (pick_path, decision_path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    with sync_playwright() as playwright:
        context = _launch_context(playwright, config, headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.set_viewport_size(dict(DOUYIN_VIEWPORT))
        except Exception:
            pass
        if url:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(6500)
        else:
            _open_douyin_web_search_results(page, query)
        _disable_douyin_autoplay(page)

        candidates = _extract_search_video_candidates(page)
        try:
            page.screenshot(path=str(search_screenshot), full_page=False)
        except Exception:
            pass
        candidates_json.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")
        candidates_md.write_text(_search_candidates_markdown(page.url, candidates, search_screenshot), encoding="utf-8")
        print(f"search_candidates={candidates_md}", flush=True)
        print(f"pick_file={pick_path}", flush=True)
        print("pick_required=write candidate number(s), e.g. 2,6,1, or n", flush=True)

        pick = _wait_for_file_decision(page, pick_path, decision_timeout_seconds)
        if pick in {"n", "no", "skip", ""}:
            try:
                context.close()
            except Exception:
                pass
            return f"mine_search=skipped candidates={candidates_md}"

        indexes = _parse_pick_indexes(pick, len(candidates))
        if not indexes:
            try:
                context.close()
            except Exception:
                pass
            return f"mine_search=invalid_pick pick={pick} candidates={candidates_md}"

        search_url = page.url
        outputs: list[str] = []
        fetched = 0
        missing = 0
        skipped = 0
        for run_no, index in enumerate(indexes, start=1):
            if run_no > 1:
                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(3000)
                    _disable_douyin_autoplay(page)
                except Exception:
                    pass

            before_pages = {id(item) for item in context.pages if not item.is_closed()}
            before_url = page.url
            picked = candidates[index - 1]
            click_target = _find_current_candidate_for_pick(page, picked)
            if not click_target:
                skipped += 1
                print(f"review_item={index}/{len(candidates)} run={run_no}/{len(indexes)} match_failed=1", flush=True)
                continue
            if not _click_search_candidate_card(page, click_target):
                skipped += 1
                print(f"review_item={index}/{len(candidates)} run={run_no}/{len(indexes)} click_failed=1", flush=True)
                continue
            detail_page = _page_after_search_click(context, page, before_url, before_pages)
            try:
                detail_page.set_viewport_size(dict(DOUYIN_VIEWPORT))
            except Exception:
                pass
            detail_page.wait_for_timeout(3500)
            _disable_douyin_autoplay(detail_page)
            _open_comment_panel(detail_page)
            detail_page.wait_for_timeout(1200)

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            item_review_md = state_dir / f"current_douyin_review_{index}_{stamp}.md"
            item_review_json = state_dir / f"current_douyin_review_{index}_{stamp}.json"
            item_review_screenshot = debug_dir / f"current_douyin_review_{index}_{stamp}.png"

            bundle = _current_video_review_bundle(detail_page)
            bundle["picked_candidate"] = picked
            bundle["clicked_candidate"] = click_target
            bundle["screenshot"] = str(item_review_screenshot)
            try:
                detail_page.screenshot(path=str(item_review_screenshot), full_page=False)
            except Exception:
                pass
            item_review_json.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
            item_review_md.write_text(_review_bundle_markdown(bundle), encoding="utf-8")
            review_json.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
            review_md.write_text(_review_bundle_markdown(bundle), encoding="utf-8")
            try:
                decision_path.unlink()
            except FileNotFoundError:
                pass
            print(f"review_item={index}/{len(candidates)} run={run_no}/{len(indexes)}", flush=True)
            print(f"review_bundle={item_review_md}", flush=True)
            print(f"decision_file={decision_path}", flush=True)
            print("decision_required=write y or n", flush=True)

            decision = _wait_for_file_decision(detail_page, decision_path, decision_timeout_seconds)
            if decision not in {"y", "yes", "1", "true", "go"}:
                skipped += 1
                if detail_page is not page:
                    try:
                        detail_page.close()
                    except Exception:
                        pass
                continue

            ai_result = _extract_transcript_via_douyin_ai(detail_page, ai_prompt)
            transcript = str(ai_result.get("transcript") or "").strip()
            source = str(ai_result.get("status") or "抖音AI未返回可保存内容")
            detail_url = detail_page.url
            if transcript:
                target_output = output_path if output and len(indexes) == 1 else _default_mined_output_path(config.root, query or url, picked, index)
                _write_mined_ai_answer(
                    target_output,
                    detail_url=detail_url,
                    ai_prompt=ai_prompt,
                    source=source,
                    candidates_md=candidates_md,
                    review_md=item_review_md,
                    picked=picked,
                    bundle=bundle,
                    transcript=transcript,
                )
                outputs.append(str(target_output))
                fetched += 1
            else:
                missing += 1

            if detail_page is not page:
                try:
                    detail_page.close()
                except Exception:
                    pass
        try:
            context.close()
        except Exception:
            pass
    return f"mine_search_batch fetched={fetched} missing={missing} skipped={skipped} picked={','.join(map(str, indexes))} outputs={';'.join(outputs)}"


def _parse_pick_indexes(pick: str, max_index: int) -> list[int]:
    indexes: list[int] = []
    for part in re.split(r"[\s,，、;；]+", pick.strip()):
        if not part:
            continue
        try:
            value = int(part)
        except ValueError:
            continue
        if 1 <= value <= max_index and value not in indexes:
            indexes.append(value)
    return indexes


def _default_mined_output_path(root: Path, query: str, picked: dict[str, Any], index: int) -> Path:
    inbox = root / "00_收件箱" / "抖音问AI"
    inbox.mkdir(parents=True, exist_ok=True)
    text = str(picked.get("text") or query or "抖音问AI")
    slug = _slug_for_filename(_candidate_title_hint(text) or query or f"候选{index}")
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return inbox / f"{stamp}_{index:02d}_{slug}.md"


def _candidate_title_hint(text: str) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if not clean:
        return ""
    clean = re.sub(r"^(?:\d{1,2}:\d{2}(?::\d{2})?\s*)", "", clean)
    clean = re.sub(r"^(?:\d+(?:\.\d+)?[万wW]?\s*)", "", clean)
    clean = re.sub(r"@\S+.*$", "", clean)
    clean = re.sub(r"#\S+", "", clean)
    clean = clean.strip(" -_｜|，,。")
    punct = re.search(r"[。！？!?]", clean)
    if punct and punct.start() >= 8:
        clean = clean[: punct.start()]
    if len(clean) > 32:
        clean = clean[:32].rstrip()
    return clean


def _best_current_candidate_for_pick(
    current_candidates: list[dict[str, Any]],
    picked: dict[str, Any],
) -> dict[str, Any] | None:
    picked_text = str(picked.get("text") or "")
    best: dict[str, Any] | None = None
    best_score = 0.0
    for candidate in current_candidates:
        score = _candidate_match_score(picked_text, str(candidate.get("text") or ""))
        if score > best_score:
            best_score = score
            best = candidate
    return best if best and best_score >= 0.75 else None


def _find_current_candidate_for_pick(page: Any, picked: dict[str, Any]) -> dict[str, Any] | None:
    for attempt in range(4):
        current_candidates = _extract_search_video_candidates(page)
        best = _best_current_candidate_for_pick(current_candidates, picked)
        if best:
            return best
        try:
            page.mouse.wheel(0, 560 if attempt < 3 else -1680)
        except Exception:
            pass
        page.wait_for_timeout(700)
    return None


def _click_search_candidate_card(page: Any, candidate: dict[str, Any]) -> bool:
    text = str(candidate.get("text") or "")
    snippets = _candidate_click_snippets(text)
    selectors = [".search-result-card", '[class*="search-result-card"]', "article", "a"]
    for snippet in snippets:
        for selector in selectors:
            try:
                locator = page.locator(selector).filter(has_text=snippet).first
                if locator.count() <= 0:
                    continue
                locator.scroll_into_view_if_needed(timeout=2500)
                page.wait_for_timeout(300)
                locator.click(timeout=3500)
                return True
            except Exception:
                continue
    return False


def _candidate_click_snippets(text: str) -> list[str]:
    snippets: list[str] = []
    duration = _candidate_duration(text)
    title = _candidate_title_hint(text)
    if title:
        snippets.append(title[:24])
        if len(title) > 12:
            snippets.append(title[:12])
    if duration:
        snippets.append(duration)
    author = _candidate_author_raw(text)
    if author:
        snippets.append(author)
    out: list[str] = []
    for snippet in snippets:
        clean = re.sub(r"\s+", " ", snippet).strip()
        if len(clean) >= 3 and clean not in out:
            out.append(clean)
    return out


def _candidate_match_score(source_text: str, candidate_text: str) -> float:
    source_title = _normalize(_candidate_title_hint(source_text))
    candidate_title = _normalize(_candidate_title_hint(candidate_text))
    if not source_title or not candidate_title:
        return 0.0
    score = SequenceMatcher(None, source_title, candidate_title).ratio()
    if source_title in candidate_title or candidate_title in source_title:
        score += 0.45
    if _candidate_duration(source_text) and _candidate_duration(source_text) == _candidate_duration(candidate_text):
        score += 0.35
    if _candidate_author(source_text) and _candidate_author(source_text) == _candidate_author(candidate_text):
        score += 0.2
    return score


def _candidate_duration(text: str) -> str:
    match = re.search(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", text or "")
    return match.group(0) if match else ""


def _candidate_author(text: str) -> str:
    return _normalize(_candidate_author_raw(text))


def _candidate_author_raw(text: str) -> str:
    match = re.search(r"@([^\s#，,。·]+)", text or "")
    return match.group(1) if match else ""



def _slug_for_filename(value: str, max_len: int = 42) -> str:
    value = re.sub(r"\s+", "_", value.strip())
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"_+", "_", value).strip("._ ")
    if not value:
        value = "抖音问AI"
    return value[:max_len].rstrip("._ ")


def _write_mined_ai_answer(
    path: Path,
    *,
    detail_url: str,
    ai_prompt: str,
    source: str,
    candidates_md: Path,
    review_md: Path,
    picked: dict[str, Any],
    bundle: dict[str, Any],
    transcript: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    comments = bundle.get("comments_preview") or []
    picked_text = str(picked.get("text") or "").strip()
    review_hint = str(bundle.get("review_hint") or "").strip()
    lines = [
        "---",
        f"created_at: {datetime.now().isoformat(timespec='seconds')}",
        "类型: 抖音问AI原始素材",
        "状态: 待提炼",
        f"source_url: {detail_url}",
        f"prompt: {ai_prompt}",
        f"source: {source}",
        f"search_candidates: {candidates_md}",
        f"review_bundle: {review_md}",
        "---",
        "",
        "# 抖音搜索挖掘问AI生成内容",
        "",
        "## 选中候选",
        "",
        picked_text or "暂无",
        "",
        "## 复核摘要",
        "",
        review_hint or "暂无",
        "",
        "## 可见评论片段",
        "",
    ]
    if comments:
        lines.extend(f"- {item}" for item in comments[:30])
    else:
        lines.append("暂无")
    lines.extend(
        [
            "",
            "## 问AI生成内容",
            "",
            transcript.strip() or "暂无",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _wait_for_file_decision(page: Any, path: Path, timeout_seconds: int) -> str:
    deadline = datetime.now().timestamp() + max(timeout_seconds, 1)
    while datetime.now().timestamp() < deadline:
        try:
            value = path.read_text(encoding="utf-8-sig").strip().lower()
        except FileNotFoundError:
            value = ""
        if value:
            return value
        page.wait_for_timeout(1000)
    return ""


def _author_home_url(url: str) -> str:
    author_base = _author_user_url_base(url)
    if author_base:
        return f"{author_base}?from_tab_name=main"
    return url


def _author_modal_url(author_url: str, detail_url: str) -> str:
    canonical = _canonical_detail_url(detail_url)
    match = re.search(r"/video/(\d+)", canonical or "")
    if not match:
        return ""
    home = _author_home_url(author_url)
    separator = "&" if "?" in home else "?"
    return f"{home}{separator}modal_id={match.group(1)}"


def _extract_author_item_ai_with_retries(
    page: Any,
    *,
    item: dict[str, Any],
    ai_prompt: str,
    state_dir: Path,
    debug_dir: Path,
    author_info: dict[str, str],
    author_url: str,
    index: int,
    total: int,
) -> tuple[dict[str, str], dict[str, Any], Path, str]:
    ai_prompt = _clean_douyin_ai_prompt(ai_prompt)
    raw_url = str(item.get("url") or "")
    last_result: dict[str, str] = {"transcript": "", "status": "没有可打开的视频链接"}
    last_bundle: dict[str, Any] = {}
    last_review_md = state_dir / f"current_douyin_author_review_{index}_missing.md"
    final_url = ""

    for attempt in range(1, 2):
        if attempt > 1:
            print(f"author_item={index}/{total} retry={attempt} click_author_card", flush=True)
        try:
            opened = _open_author_work_from_home(page, author_url, raw_url, item)
            final_url = page.url or raw_url
            if not opened:
                last_result = {"transcript": "", "status": "未打开目标作品，未进入问AI"}
                continue
            has_ai_entry = _prepare_author_detail_page_for_ai(page)
            bundle, item_review_md = _write_author_item_review_bundle(
                page,
                state_dir=state_dir,
                debug_dir=debug_dir,
                author_info=author_info,
                item=item,
                index=index,
                attempt=attempt,
            )
            if not has_ai_entry:
                last_result = {"transcript": "", "status": "未发现问AI入口，未保存"}
                last_bundle = bundle
                last_review_md = item_review_md
                break
            item_prompt = _author_item_ai_prompt(ai_prompt, item)
            ai_result = _extract_author_transcript_via_douyin_ai_simple(page, item_prompt, item)
            ai_result["prompt"] = item_prompt
            transcript = str(ai_result.get("transcript") or "").strip()
            source = str(ai_result.get("status") or "抖音AI未返回可保存内容")
            last_result = ai_result
            last_bundle = bundle
            last_review_md = item_review_md
            final_url = page.url or raw_url
            if not transcript:
                last_result = {"transcript": "", "status": f"问AI未返回可提取文本，未保存：{source}"}
                break
            if transcript:
                if len(transcript) < 500:
                    _write_ai_debug(page, "short_answer", f"{transcript}\n\n--- panel ---\n{_safe_ai_panel_text(page)[:4000]}")
                    ai_result["status"] = f"抖音AI返回内容较短，已保留待后期判断：{source}"
                    return ai_result, bundle, item_review_md, final_url
                if _transcript_matches_author_item(transcript, item):
                    return ai_result, bundle, item_review_md, final_url
                _write_ai_debug(page, "mismatch_answer", f"{transcript[:4000]}\n\n--- panel ---\n{_safe_ai_panel_text(page)[:4000]}")
                ai_result["status"] = f"抖音AI返回疑似错视频，已保留待后期判断：{source}"
                return ai_result, bundle, item_review_md, final_url
            if not _should_retry_author_ai_status(source):
                break
            page.wait_for_timeout(1200)
        except Exception as exc:
            final_url = raw_url
            last_result = {"transcript": "", "status": f"打开视频或问AI失败：{exc.__class__.__name__}: {str(exc)[:120]}"}
            if attempt >= 2:
                break
            page.wait_for_timeout(1200)

    return last_result, last_bundle, last_review_md, final_url or raw_url


def _author_ai_detail_url(raw_url: str, author_url: str) -> str:
    variants = _author_ai_detail_url_variants(raw_url, author_url)
    return variants[0] if variants else ""


def _author_ai_detail_url_variants(raw_url: str, author_url: str) -> list[str]:
    variants: list[str] = []
    video_id = _video_id_from_url(raw_url)
    author_base = _author_user_url_base(author_url)
    candidates: list[str] = []
    if author_base and video_id:
        candidates.extend(
            [
                f"{author_base}?from_tab_name=main&modal_id={video_id}",
                f"{author_base}?modal_id={video_id}&type=general",
                f"{author_base}?from_tab_name=main&modal_id={video_id}&vid={video_id}",
                f"{author_base}?from_tab_name=main&vid={video_id}",
            ]
        )
    if video_id:
        candidates.append(f"https://www.douyin.com/jingxuan?modal_id={video_id}")
    if author_base:
        candidates.append(f"{author_base}?from_tab_name=main")
    for candidate in candidates:
        if candidate and candidate not in variants:
            variants.append(candidate)
    return variants


def _author_item_ai_prompt(base_prompt: str, item: dict[str, Any]) -> str:
    title = str(item.get("title") or item.get("text") or item.get("raw_text") or "").strip()
    title = re.sub(r"\s+", " ", title)
    if not title:
        return base_prompt
    title = title[:80]
    return f"{base_prompt}。当前视频标题：{title}"


def _author_search_query_for_item(item: dict[str, Any]) -> str:
    title = str(item.get("title") or item.get("text") or item.get("raw_text") or "").strip()
    match = re.match(r"\s*([^:：#]{2,24})[:：]", title)
    if match:
        return match.group(1).strip()
    author = _candidate_author_raw(title)
    if author:
        return author
    clean = re.sub(r"#.*$", "", title)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:24]


def _author_work_search_url(item: dict[str, Any]) -> str:
    query = _author_search_query_for_item(item)
    if not query:
        return ""
    return f"https://www.douyin.com/search/{quote(query)}?type=general"


def _author_item_title_snippets(item: dict[str, Any]) -> list[str]:
    title = str(item.get("title") or item.get("text") or item.get("raw_text") or "")
    title = re.sub(r"^\s*[^:：]{2,24}[:：]", "", title)
    title = re.sub(r"#.*$", "", title)
    title = re.sub(r"\s+", " ", title).strip(" ，,。.!！?？、")
    snippets: list[str] = []
    if title:
        snippets.extend([title[:28], title[:16], title[:10]])
    snippets.extend(_author_item_keyword_tokens(title))
    out: list[str] = []
    for snippet in snippets:
        snippet = snippet.strip(" ，,。.!！?？、")
        if len(snippet) >= 4 and snippet not in out:
            out.append(snippet)
    return out


def _author_item_text_fallback(item: dict[str, Any]) -> str:
    text = str(item.get("raw_text") or item.get("text") or item.get("title") or "").strip()
    if not text:
        return ""
    text = re.sub(r"^\s*\d+(?:\.\d+)?[万wW]?\s+", "", text)
    text = re.sub(r"\s+", " ", text)
    chunks = re.split(r"(?<=[。！？；])\s+| {2,}", text)
    lines: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        clean = chunk.strip(" ，。！？；:：-_/")
        if len(clean) < 8:
            continue
        if len(clean) > 240:
            parts = re.split(r"(?<=[。！？；])", clean)
        else:
            parts = [clean]
        for part in parts:
            part = part.strip(" ，。！？；:：-_/")
            if len(part) < 8 or len(part) > 260:
                continue
            key = _normalize(part)
            if key in seen:
                continue
            seen.add(key)
            lines.append(part)
    fallback = "\n".join(lines).strip()
    return fallback if len(fallback) >= 300 else ""


def _author_item_raw_capture(item: dict[str, Any], reason: str) -> str:
    url = str(item.get("url") or "").strip()
    title = str(item.get("title") or "").strip()
    text = str(item.get("text") or "").strip()
    raw_text = str(item.get("raw_text") or "").strip()
    stats = str(item.get("stats") or "").strip()
    source_id = str(item.get("source_id") or "").strip()
    lines = [
        "## 采集状态",
        "",
        reason.strip() or "未获取问AI内容，仅保存作者作品卡片原始数据",
        "",
        "## 作者作品元数据",
        "",
        f"- url: {url or '暂无'}",
        f"- source_id: {source_id or '暂无'}",
        f"- title: {title or '暂无'}",
        f"- stats: {stats or '暂无'}",
        "",
        "## 作者作品卡片文本",
        "",
        raw_text or text or title or "暂无",
        "",
    ]
    return "\n".join(lines).strip()


def _author_item_raw_only_reason(item: dict[str, Any]) -> str:
    title = str(item.get("title") or item.get("text") or item.get("raw_text") or "")
    if re.search(r"于\s*\d{6,}\s*发布的作品", title):
        return "作品标题过泛，无法稳定定位问AI页面，直接保留作者作品卡片原始数据"
    return ""


def _clean_douyin_ai_prompt(prompt: str) -> str:
    value = str(prompt or "").strip()
    if not value:
        return DEFAULT_DOUYIN_AI_PROMPT
    question_marks = value.count("?") + value.count("？")
    replacement_marks = value.count("\ufffd")
    ascii_letters = sum(1 for char in value if char.isascii() and char.isalpha())
    cjk_chars = sum(1 for char in value if "\u4e00" <= char <= "\u9fff")
    if cjk_chars == 0 and (question_marks + replacement_marks) >= max(3, len(value) // 2):
        return DEFAULT_DOUYIN_AI_PROMPT
    if cjk_chars == 0 and ascii_letters == 0 and len(value) <= 20:
        return DEFAULT_DOUYIN_AI_PROMPT
    return value


def _mine_author_manual_playlist_flow(
    page: Any,
    *,
    config: AppConfig,
    author_url: str,
    works_md: Path,
    state_dir: Path,
    debug_dir: Path,
    existing_urls: set[str],
    ai_prompt: str,
    skip_existing: bool,
    max_items: int,
    scroll_rounds: int = 12,
    manual_seconds: int = 0,
) -> dict[str, Any]:
    ai_prompt = _clean_douyin_ai_prompt(ai_prompt)
    result: dict[str, Any] = {
        "fetched": 0,
        "missing": 0,
        "skipped": 0,
        "outputs": [],
        "failed_step": "",
    }
    try:
        page.goto("about:blank", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(300)
        page.goto(_author_home_url(author_url), wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(10000)
        if manual_seconds > 0:
            print(f"manual_navigation={manual_seconds}s author_page", flush=True)
            page.wait_for_timeout(manual_seconds * 1000)
        _dismiss_douyin_soft_overlays(page)
        _click_author_works_tab(page)
        page.wait_for_timeout(1800)
        try:
            page.mouse.wheel(0, -5000)
        except Exception:
            pass
        page.wait_for_timeout(800)
    except Exception as exc:
        result["failed_step"] = f"open_author_home:{exc.__class__.__name__}"
        _write_author_playlist_start_debug(page, debug_dir, "manual_open_author_home")
        return result

    author_info = _author_profile_info(page, author_url)
    works = _collect_author_work_items(page, scroll_rounds=scroll_rounds)
    works = _filter_author_work_items(works, author_info)
    if max_items > 0:
        works = works[:max_items]
    try:
        _click_author_works_tab(page)
        page.mouse.wheel(0, -12000)
        page.wait_for_timeout(1200)
    except Exception:
        pass
    profile_total = _author_profile_work_count(page)
    total = max_items if max_items > 0 else max(len(works), profile_total, 1)
    works_md.write_text(_author_works_markdown(author_info, works), encoding="utf-8")
    print(
        f"author_manual_start author={author_info.get('name', '')} total={total} collected={len(works)} profile_total={profile_total}",
        flush=True,
    )

    if not _click_first_author_card_manual(page):
        result["failed_step"] = "click_first_work"
        _write_author_playlist_start_debug(page, debug_dir, "manual_click_first_work")
        return result
    if not _manual_video_controls_visible(page):
        result["failed_step"] = "wait_video_modal"
        _write_author_playlist_start_debug(page, debug_dir, "manual_wait_video_modal")
        return result
    if not _click_comment_button_manual(page):
        result["failed_step"] = "click_comment"
        _write_author_playlist_start_debug(page, debug_dir, "manual_click_comment")
        return result
    if not _click_author_side_works_tab(page):
        result["failed_step"] = "click_ta_works"
        _write_author_playlist_start_debug(page, debug_dir, "manual_click_ta_works")
        return result

    seen: set[str] = set()
    for index in range(1, total + 1):
        _disable_douyin_autoplay(page)
        page.wait_for_timeout(800)
        item = _current_author_playlist_item(page, author_info, index)
        other_author = _playlist_item_other_author(item, author_info)
        if other_author:
            result["failed_step"] = f"left_author_playlist:{other_author}"
            print(f"author_manual={index}/{total} stop=left_author_playlist other_author={other_author}", flush=True)
            break
        detail_url = _canonical_detail_url(str(item.get("url") or page.url)) or (page.url or "")
        if detail_url in seen:
            result["failed_step"] = "repeated_video"
            break
        seen.add(detail_url)

        if skip_existing and detail_url in existing_urls:
            result["skipped"] = int(result["skipped"]) + 1
            print(f"author_manual={index}/{total} skipped=existing url={detail_url}", flush=True)
        else:
            print(f"author_manual={index}/{total} ask_ai={detail_url}", flush=True)
            bundle, item_review_md = _write_author_item_review_bundle(
                page,
                state_dir=state_dir,
                debug_dir=debug_dir,
                author_info=author_info,
                item=item,
                index=index,
                attempt=1,
            )
            if not _force_open_author_comment_tab(page):
                ai_result = {"transcript": "", "status": "未能切到评论页，未判断问AI"}
            elif not _has_author_ai_entry(page):
                ai_result = {"transcript": "", "status": "评论页未发现问AI，跳过当前视频"}
            else:
                ai_result = _extract_author_transcript_via_douyin_ai_simple(page, ai_prompt, item)
            transcript = str(ai_result.get("transcript") or "").strip()
            source = str(ai_result.get("status") or "抖音AI未返回可保存内容")
            prompt_used = str(ai_result.get("prompt") or ai_prompt)
            if transcript:
                target_output = _default_author_output_path(config.root, author_info, item, index)
                _write_mined_ai_answer(
                    target_output,
                    detail_url=detail_url,
                    ai_prompt=prompt_used,
                    source=source,
                    candidates_md=works_md,
                    review_md=item_review_md,
                    picked=item,
                    bundle=bundle,
                    transcript=transcript,
                )
                result["outputs"].append(str(target_output))
                existing_urls.add(detail_url)
                result["fetched"] = int(result["fetched"]) + 1
                print(f"author_manual={index}/{total} saved={target_output} chars={len(transcript)}", flush=True)
            else:
                result["missing"] = int(result["missing"]) + 1
                print(f"author_manual={index}/{total} missing source={source}", flush=True)

        if index >= total:
            break
        if not _click_author_side_works_tab(page):
            result["failed_step"] = "return_to_ta_works"
            _write_author_playlist_start_debug(page, debug_dir, "manual_return_to_ta_works")
            break
        if not _advance_author_playlist_video(page, detail_url):
            result["failed_step"] = "advance_next_video"
            break
    return result


def mine_douyin_author_current_playlist(
    config: AppConfig,
    *,
    url: str,
    limit: int = 80,
    manual_seconds: int = 240,
    manual_confirm: bool = False,
    start_index: int = 1,
    ai_prompt: str = "请给我完整的对白文本",
    skip_existing: bool = True,
) -> str:
    ai_prompt = _clean_douyin_ai_prompt(ai_prompt)
    sync_playwright = _load_playwright()
    state_dir = config.root / ".state"
    debug_dir = state_dir / "debug"
    works_md = state_dir / "current_douyin_author_works.md"
    state_dir.mkdir(parents=True, exist_ok=True)
    debug_dir.mkdir(parents=True, exist_ok=True)
    existing_urls = _existing_mined_source_urls(config.root) if skip_existing else set()
    result: dict[str, Any] = {
        "fetched": 0,
        "missing": 0,
        "skipped": 0,
        "outputs": [],
        "failed_step": "",
    }

    with sync_playwright() as playwright:
        context = _launch_context(playwright, config, headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.set_viewport_size(dict(DOUYIN_VIEWPORT))
        except Exception:
            pass

        author_url = _author_home_url(url)
        try:
            page.goto(author_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(7000)
        except Exception as exc:
            result["failed_step"] = f"open_author_home:{exc.__class__.__name__}"
            _write_author_playlist_start_debug(page, debug_dir, "current_open_author_home")
            try:
                context.close()
            except Exception:
                pass
            return _format_author_current_result(result)

        if manual_confirm:
            print(
                "manual_confirm=press_enter_after_positioning_author_grid_or_video",
                flush=True,
            )
            try:
                input()
            except EOFError:
                if manual_seconds > 0:
                    page.wait_for_timeout(manual_seconds * 1000)
        elif manual_seconds > 0:
            print(
                "manual_position="
                f"{manual_seconds}s open_work_comment_ta_works_and_select_start_video",
                flush=True,
            )
            page.wait_for_timeout(manual_seconds * 1000)

        _dismiss_douyin_soft_overlays(page)
        author_info = _author_profile_info(page, author_url)
        works_md.write_text(_author_works_markdown(author_info, []), encoding="utf-8")

        if not _manual_video_controls_visible(page):
            print("manual_position=profile_grid try_click_visible_unmined_card", flush=True)
            if not _click_visible_unmined_author_card(page, existing_urls, author_url=author_url):
                result["failed_step"] = "manual_position_not_video_modal"
                _write_author_playlist_start_debug(page, debug_dir, "current_not_video_modal")
                try:
                    context.close()
                except Exception:
                    pass
                return _format_author_current_result(result)
        if not _click_comment_button_manual(page):
            result["failed_step"] = "manual_position_no_comment_panel"
            _write_author_playlist_start_debug(page, debug_dir, "current_no_comment_panel")
            try:
                context.close()
            except Exception:
                pass
            return _format_author_current_result(result)
        if not _click_author_side_works_tab(page):
            result["failed_step"] = "manual_position_no_ta_works"
            _write_author_playlist_start_debug(page, debug_dir, "current_no_ta_works")
            try:
                context.close()
            except Exception:
                pass
            return _format_author_current_result(result)

        total = max(limit, 0)
        print(
            f"author_current_start author={author_info.get('name', '')} start_index={start_index} limit={total}",
            flush=True,
        )
        seen: set[str] = set()
        for offset in range(total):
            index = max(start_index, 1) + offset
            _disable_douyin_autoplay(page)
            page.wait_for_timeout(800)
            item = _current_author_playlist_item(page, author_info, index)
            other_author = _playlist_item_other_author(item, author_info)
            if other_author:
                result["failed_step"] = f"left_author_playlist:{other_author}"
                print(f"author_current={index} stop=left_author_playlist other_author={other_author}", flush=True)
                break
            detail_url = _canonical_detail_url(str(item.get("url") or page.url)) or (page.url or "")
            if detail_url in seen:
                result["failed_step"] = "repeated_video"
                break
            seen.add(detail_url)

            if skip_existing and detail_url in existing_urls:
                result["skipped"] = int(result["skipped"]) + 1
                print(f"author_current={index} skipped=existing url={detail_url}", flush=True)
            else:
                print(f"author_current={index} ask_ai={detail_url}", flush=True)
                bundle, item_review_md = _write_author_item_review_bundle(
                    page,
                    state_dir=state_dir,
                    debug_dir=debug_dir,
                    author_info=author_info,
                    item=item,
                    index=index,
                    attempt=1,
                )
                if not _force_open_author_comment_tab(page):
                    ai_result = {"transcript": "", "status": "未能切到评论页，未判断问AI"}
                elif not _has_author_ai_entry(page):
                    ai_result = {"transcript": "", "status": "评论页未发现问AI，跳过当前视频"}
                else:
                    ai_result = _extract_author_transcript_via_douyin_ai_simple(page, ai_prompt, item)
                transcript = str(ai_result.get("transcript") or "").strip()
                source = str(ai_result.get("status") or "抖音AI未返回可保存内容")
                prompt_used = str(ai_result.get("prompt") or ai_prompt)
                if transcript:
                    target_output = _default_author_output_path(config.root, author_info, item, index)
                    _write_mined_ai_answer(
                        target_output,
                        detail_url=detail_url,
                        ai_prompt=prompt_used,
                        source=source,
                        candidates_md=works_md,
                        review_md=item_review_md,
                        picked=item,
                        bundle=bundle,
                        transcript=transcript,
                    )
                    result["outputs"].append(str(target_output))
                    existing_urls.add(detail_url)
                    result["fetched"] = int(result["fetched"]) + 1
                    print(f"author_current={index} saved={target_output} chars={len(transcript)}", flush=True)
                else:
                    result["missing"] = int(result["missing"]) + 1
                    print(f"author_current={index} missing source={source}", flush=True)

            if offset >= total - 1:
                break
            if not _click_author_side_works_tab(page):
                result["failed_step"] = "return_to_ta_works"
                _write_author_playlist_start_debug(page, debug_dir, "current_return_to_ta_works")
                break
            if not _advance_author_playlist_video(page, detail_url):
                result["failed_step"] = "advance_next_video"
                break

        try:
            context.close()
        except Exception:
            pass
    return _format_author_current_result(result)


def _format_author_current_result(result: dict[str, Any]) -> str:
    return (
        f"mine_author_current fetched={result.get('fetched', 0)} "
        f"missing={result.get('missing', 0)} "
        f"skipped={result.get('skipped', 0)} "
        f"failed_step={result.get('failed_step', '')} "
        f"outputs={';'.join(result.get('outputs', []) or [])}"
    )


def _dismiss_douyin_soft_overlays(page: Any) -> None:
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    script = r"""
() => {
  const labels = ['我知道了', '知道了', '稍后再说', '暂不开启'];
  for (const node of Array.from(document.querySelectorAll('button, [role="button"], div, span'))) {
    const text = String(node.innerText || node.textContent || '').trim();
    if (!labels.includes(text)) continue;
    const rect = node.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    (node.closest('button, [role="button"]') || node).click();
    return true;
  }
  return false;
}
"""
    try:
        page.evaluate(script)
    except Exception:
        pass


def _click_first_author_card_manual(page: Any) -> bool:
    for attempt in range(3):
        point = None
        script = r"""
() => {
  const visible = (node) => {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return rect.width >= 120 &&
      rect.height >= 160 &&
      rect.x > 120 &&
      rect.y > 180 &&
      rect.y < window.innerHeight - 40 &&
      style.visibility !== 'hidden' &&
      style.display !== 'none';
  };
  const anchors = Array.from(document.querySelectorAll('a[href*="/video/"], a[href*="/note/"]'))
    .filter(visible)
    .map((anchor) => ({ rect: anchor.getBoundingClientRect(), href: anchor.href }))
    .sort((a, b) => a.rect.y - b.rect.y || a.rect.x - b.rect.x);
  const first = anchors[0];
  if (!first) return null;
  return {
    x: first.rect.x + first.rect.width / 2,
    y: first.rect.y + Math.min(first.rect.height * 0.38, first.rect.height - 24),
    href: first.href,
  };
}
"""
        try:
            point = page.evaluate(script)
        except Exception:
            point = None
        if point:
            page.mouse.click(float(point["x"]), float(point["y"]))
            page.wait_for_timeout(6500)
            if _manual_video_controls_visible(page):
                return True
        try:
            page.mouse.wheel(0, -2200 if attempt == 0 else 900)
        except Exception:
            pass
        page.wait_for_timeout(1200)
    return False


def _click_visible_unmined_author_card(page: Any, existing_urls: set[str], *, author_url: str) -> bool:
    script = r"""
() => {
  const visible = (node) => {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return rect.width >= 120 &&
      rect.height >= 160 &&
      rect.x > 120 &&
      rect.y > 120 &&
      rect.y < window.innerHeight - 40 &&
      style.visibility !== 'hidden' &&
      style.display !== 'none';
  };
  return Array.from(document.querySelectorAll('a[href*="/video/"], a[href*="/note/"]'))
    .filter(visible)
    .map((anchor) => {
      const rect = anchor.getBoundingClientRect();
      return {
        x: rect.x + rect.width / 2,
        y: rect.y + Math.min(rect.height * 0.38, rect.height - 24),
        href: anchor.href,
        text: String(anchor.innerText || anchor.textContent || '').replace(/\s+/g, ' ').trim(),
      };
    })
    .sort((a, b) => a.y - b.y || a.x - b.x);
}
"""
    scroll_script = r"""
() => {
  const amount = 5200;
  window.scrollBy(0, amount);
  if (document.scrollingElement) document.scrollingElement.scrollTop += amount;
  if (document.documentElement) document.documentElement.scrollTop += amount;
  if (document.body) document.body.scrollTop += amount;
  for (const node of Array.from(document.querySelectorAll('div, main, section'))) {
    try {
      const rect = node.getBoundingClientRect();
      const style = window.getComputedStyle(node);
      const scrollable = node.scrollHeight > node.clientHeight + 300;
      const visible = rect.width > 300 && rect.height > 300 && style.display !== 'none' && style.visibility !== 'hidden';
      if (scrollable && visible) node.scrollTop += amount;
    } catch (_) {}
  }
  return {
    y: window.scrollY || document.documentElement?.scrollTop || document.body?.scrollTop || 0,
    textLength: String(document.body?.innerText || '').length,
  };
}
"""
    last_visible: list[dict[str, Any]] = []
    for attempt in range(90):
        try:
            anchors = page.evaluate(script) or []
        except Exception:
            anchors = []
        if anchors:
            last_visible = anchors
        for anchor in anchors:
            detail_url = _canonical_detail_url(str(anchor.get("href") or ""))
            if detail_url and detail_url not in existing_urls:
                try:
                    print(
                        f"author_grid_pick attempt={attempt + 1} url={detail_url} text={str(anchor.get('text') or '')[:80]}",
                        flush=True,
                    )
                    if _open_author_card_from_grid(page, detail_url):
                        return True
                    modal_url = _author_modal_url(author_url, detail_url)
                    if modal_url:
                        page.goto(modal_url, wait_until="domcontentloaded", timeout=60000)
                    else:
                        page.mouse.click(float(anchor["x"]), float(anchor["y"]))
                    page.wait_for_timeout(6500)
                    return _manual_video_controls_visible(page)
                except Exception:
                    return False
        try:
            scroll_info = page.evaluate(scroll_script) or {}
            print(f"author_grid_scroll attempt={attempt + 1} y={scroll_info.get('y', '')}", flush=True)
        except Exception:
            try:
                page.mouse.wheel(0, 5200)
            except Exception:
                pass
        page.wait_for_timeout(900)

    if not existing_urls and last_visible:
        try:
            anchor = last_visible[0]
            page.mouse.click(float(anchor["x"]), float(anchor["y"]))
            page.wait_for_timeout(6500)
            return _manual_video_controls_visible(page)
        except Exception:
            return False
    return False


def _open_author_card_from_grid(page: Any, detail_url: str) -> bool:
    match = re.search(r"/video/(\d+)", _canonical_detail_url(detail_url) or "")
    if not match:
        return False
    video_id = match.group(1)
    point_script = r"""
(videoId) => {
  const anchors = Array.from(document.querySelectorAll('a[href*="/video/"], a[href*="/note/"]'));
  const anchor = anchors.find((node) => String(node.href || '').includes(videoId));
  if (!anchor) return null;
  anchor.scrollIntoView({ block: 'center', inline: 'center' });
  const rect = anchor.getBoundingClientRect();
  return {
    x: rect.x + rect.width / 2,
    y: rect.y + Math.min(rect.height * 0.38, rect.height - 24),
  };
}
"""
    click_script = r"""
(videoId) => {
  const anchors = Array.from(document.querySelectorAll('a[href*="/video/"], a[href*="/note/"]'));
  const anchor = anchors.find((node) => String(node.href || '').includes(videoId));
  if (!anchor) return false;
  anchor.scrollIntoView({ block: 'center', inline: 'center' });
  const rect = anchor.getBoundingClientRect();
  const target = document.elementFromPoint(rect.x + rect.width / 2, rect.y + Math.min(rect.height * 0.38, rect.height - 24)) || anchor;
  for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
    target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
  }
  return true;
}
"""
    try:
        point = page.evaluate(point_script, video_id)
    except Exception:
        point = None
    if point:
        try:
            page.wait_for_timeout(500)
            page.mouse.click(float(point["x"]), float(point["y"]))
            page.wait_for_timeout(6500)
            if _manual_video_controls_visible(page):
                return True
        except Exception:
            pass
    try:
        clicked = bool(page.evaluate(click_script, video_id))
    except Exception:
        clicked = False
    if clicked:
        page.wait_for_timeout(6500)
        return _manual_video_controls_visible(page)
    return False


def _manual_video_controls_visible(page: Any) -> bool:
    for _ in range(10):
        if _author_video_modal_controls_visible(page):
            return True
        page.wait_for_timeout(700)
    return False


def _click_comment_button_manual(page: Any) -> bool:
    if _has_comment_or_ai_panel(page):
        return True
    for ratio in (0.59, 0.62, 0.56):
        try:
            size = _page_inner_size(page)
            page.mouse.click(size["width"] - 43, size["height"] * ratio)
            page.wait_for_timeout(1800)
            if _has_comment_or_ai_panel(page):
                return True
        except Exception:
            continue
    return False


def _mine_author_playlist_from_page(
    page: Any,
    *,
    config: AppConfig,
    author_url: str,
    author_info: dict[str, str],
    works_md: Path,
    state_dir: Path,
    debug_dir: Path,
    existing_urls: set[str],
    ai_prompt: str,
    skip_existing: bool,
    max_items: int,
    first_item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ai_prompt = _clean_douyin_ai_prompt(ai_prompt)
    result: dict[str, Any] = {
        "started": False,
        "fetched": 0,
        "missing": 0,
        "skipped": 0,
        "outputs": [],
    }
    if max_items <= 0:
        return result
    try:
        page.goto("about:blank", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(300)
        page.goto(_author_home_url(author_url), wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(10000)
        _dismiss_douyin_overlays(page)
        _click_author_works_tab(page)
        page.wait_for_timeout(2000)
    except Exception:
        print("author_playlist_start=failed step=open_author_home", flush=True)
        return result

    if _author_video_modal_controls_visible(page):
        _close_douyin_video_modal(page)
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        page.wait_for_timeout(1500)
        _click_author_works_tab(page)
        page.wait_for_timeout(900)
    if not _open_first_author_work_from_profile(page, first_item):
        _write_author_playlist_start_debug(page, debug_dir, "open_first_work")
        print("author_playlist_start=failed step=open_first_work", flush=True)
        return result
    if not _wait_for_author_video_modal(page, timeout_ms=12000):
        _write_author_playlist_start_debug(page, debug_dir, "wait_video_modal")
        print(f"author_playlist_start=failed step=wait_video_modal url={page.url}", flush=True)
        return result
    if not _open_author_playlist_comment_panel(page):
        _write_author_playlist_start_debug(page, debug_dir, "open_comment_panel")
        print(f"author_playlist_start=failed step=open_comment_panel url={page.url}", flush=True)
        return result
    if not _click_author_side_works_tab(page):
        _write_author_playlist_start_debug(page, debug_dir, "open_side_works")
        print(f"author_playlist_start=failed step=open_side_works url={page.url}", flush=True)
        return result

    result["started"] = True
    seen: set[str] = set()
    stagnant = 0
    total = max(max_items, 1)
    for index in range(1, total + 1):
        _disable_douyin_autoplay(page)
        page.wait_for_timeout(1200)
        item = _current_author_playlist_item(page, author_info, index)
        other_author = _playlist_item_other_author(item, author_info)
        if other_author:
            result["failed_step"] = f"left_author_playlist:{other_author}"
            print(f"author_playlist={index}/{total} stop=left_author_playlist other_author={other_author}", flush=True)
            break
        detail_url = _canonical_detail_url(str(item.get("url") or page.url))
        if not detail_url:
            detail_url = page.url or ""
        if detail_url in seen:
            stagnant += 1
            if stagnant >= 2:
                break
        else:
            stagnant = 0
        seen.add(detail_url)

        if skip_existing and detail_url in existing_urls:
            result["skipped"] = int(result["skipped"]) + 1
            print(f"author_playlist={index}/{total} skipped=existing url={detail_url}", flush=True)
        else:
            print(f"author_playlist={index}/{total} ask_ai={detail_url}", flush=True)
            try:
                bundle, item_review_md = _write_author_item_review_bundle(
                    page,
                    state_dir=state_dir,
                    debug_dir=debug_dir,
                    author_info=author_info,
                    item=item,
                    index=index,
                    attempt=1,
                )
                ai_result = _extract_author_transcript_via_douyin_ai_simple(page, ai_prompt, item)
                transcript = str(ai_result.get("transcript") or "").strip()
                source = str(ai_result.get("status") or "抖音AI未返回可保存内容")
                prompt_used = str(ai_result.get("prompt") or ai_prompt)
                if transcript:
                    target_output = _default_author_output_path(config.root, author_info, item, index)
                    _write_mined_ai_answer(
                        target_output,
                        detail_url=detail_url,
                        ai_prompt=prompt_used,
                        source=source,
                        candidates_md=works_md,
                        review_md=item_review_md,
                        picked=item,
                        bundle=bundle,
                        transcript=transcript,
                    )
                    result["outputs"].append(str(target_output))
                    existing_urls.add(detail_url)
                    result["fetched"] = int(result["fetched"]) + 1
                    print(f"author_playlist={index}/{total} saved={target_output} chars={len(transcript)}", flush=True)
                else:
                    result["missing"] = int(result["missing"]) + 1
                    print(f"author_playlist={index}/{total} missing source={source}", flush=True)
            except Exception as exc:
                result["missing"] = int(result["missing"]) + 1
                print(f"author_playlist={index}/{total} failed={exc.__class__.__name__}:{str(exc)[:120]}", flush=True)

        if index >= total:
            break
        if not _advance_author_playlist_video(page, detail_url):
            break
    return result


def _author_profile_work_count(page: Any) -> int:
    script = r"""
() => {
  const text = String(document.body?.innerText || '');
  const patterns = [/作品\s*(\d{1,5})/, /作品\s*\n\s*(\d{1,5})/];
  const values = [];
  for (const pattern of patterns) {
    for (const match of text.matchAll(new RegExp(pattern.source, 'g'))) {
      const value = Number(match[1] || 0);
      if (Number.isFinite(value)) values.push(value);
    }
  }
  return values.filter((value) => value > 0).sort((a, b) => b - a)[0] || 0;
}
"""
    try:
        return int(page.evaluate(script) or 0)
    except Exception:
        return 0


def _write_author_playlist_start_debug(page: Any, debug_dir: Path, step: str) -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_dir.mkdir(parents=True, exist_ok=True)
    screenshot = debug_dir / f"author_playlist_start_{step}_{stamp}.png"
    payload_path = debug_dir / f"author_playlist_start_{step}_{stamp}.json"
    try:
        payload = page.evaluate(
            r"""
() => ({
  url: location.href,
  title: document.title,
  text: String(document.body?.innerText || '').slice(0, 3000),
  anchors: Array.from(document.querySelectorAll('a[href*="/video/"],a[href*="/note/"]'))
    .map((a) => {
      const r = a.getBoundingClientRect();
      return {
        href: a.href,
        x: Math.round(r.x),
        y: Math.round(r.y),
        w: Math.round(r.width),
        h: Math.round(r.height),
        text: String(a.innerText || a.textContent || '').replace(/\s+/g, ' ').slice(0, 180),
      };
    })
    .slice(0, 30),
})
"""
        )
    except Exception as exc:
        payload = {"error": f"{exc.__class__.__name__}: {str(exc)[:200]}", "url": getattr(page, "url", "")}
    try:
        page.screenshot(path=str(screenshot), full_page=False)
    except Exception:
        pass
    payload["screenshot"] = str(screenshot)
    try:
        payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"author_playlist_debug={payload_path}", flush=True)
    except Exception:
        pass


def _open_first_author_work_from_profile(page: Any, item: dict[str, Any] | None = None) -> bool:
    video_id = _video_id_from_url(str((item or {}).get("url") or ""))
    if video_id:
        selectors = [
            f'a[href*="/video/{video_id}"]',
            f'a[href*="/note/{video_id}"]',
            f'a[href*="{video_id}"]',
        ]
        for _ in range(6):
            for selector in selectors:
                try:
                    locator = page.locator(selector).first
                    if locator.count() <= 0:
                        continue
                    locator.scroll_into_view_if_needed(timeout=3000)
                    page.wait_for_timeout(400)
                    box = locator.bounding_box(timeout=3000)
                    if box:
                        page.mouse.click(
                            float(box["x"] + box["width"] / 2),
                            float(box["y"] + min(box["height"] * 0.38, box["height"] - 24)),
                        )
                    else:
                        locator.click(timeout=5000)
                    page.wait_for_timeout(5000)
                    if _author_video_modal_visible(page):
                        return True
                except Exception:
                    continue
            try:
                page.mouse.wheel(0, -2600)
                page.wait_for_timeout(800)
            except Exception:
                pass
    script = r"""
() => {
  const visible = (node) => {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return rect.width >= 120 &&
      rect.height >= 160 &&
      rect.x > 120 &&
      rect.y > 180 &&
      rect.y < window.innerHeight - 40 &&
      style.visibility !== 'hidden' &&
      style.display !== 'none';
  };
  const anchors = Array.from(document.querySelectorAll('a[href*="/video/"], a[href*="/note/"]'))
    .filter(visible)
    .map((anchor) => ({ anchor, rect: anchor.getBoundingClientRect() }))
    .sort((a, b) => a.rect.y - b.rect.y || a.rect.x - b.rect.x);
  const first = anchors[0];
  if (!first) return null;
  return {
    x: first.rect.x + first.rect.width / 2,
    y: first.rect.y + Math.min(first.rect.height * 0.38, first.rect.height - 24),
  };
}
"""
    try:
        point = page.evaluate(script)
    except Exception:
        point = None
    if not point:
        return False
    page.mouse.click(float(point["x"]), float(point["y"]))
    page.wait_for_timeout(5000)
    return _author_video_modal_visible(page)


def _wait_for_author_video_modal(page: Any, *, timeout_ms: int) -> bool:
    waited = 0
    while waited <= max(timeout_ms, 1000):
        if _author_video_modal_visible(page):
            return True
        page.wait_for_timeout(700)
        waited += 700
    return False


def _author_video_modal_visible(page: Any) -> bool:
    script = r"""
() => {
  const text = String(document.body?.innerText || '');
  if ((text.includes('搜索 Ta 的作品') || text.includes('日期筛选')) &&
      !text.includes('发一条友好的弹幕吧') &&
      !text.includes('听抖音')) {
    return false;
  }
  if (text.includes('发一条友好的弹幕吧') || text.includes('清屏') || text.includes('倍速') || text.includes('听抖音')) {
    return true;
  }
  const videos = Array.from(document.querySelectorAll('video'))
    .map((node) => node.getBoundingClientRect())
    .filter((rect) => rect.width >= 260 && rect.height >= 360);
  const hasLargeVideo = videos.some((rect) => rect.x > window.innerWidth * 0.15 && rect.x < window.innerWidth * 0.75);
  const hasClose = Array.from(document.querySelectorAll('button, [role="button"], div, span'))
    .some((node) => {
      const rect = node.getBoundingClientRect();
      const style = window.getComputedStyle(node);
      if (rect.width < 24 || rect.height < 24 || rect.x > 130 || rect.y > 130) return false;
      if (style.visibility === 'hidden' || style.display === 'none') return false;
      const label = String(node.innerText || node.textContent || node.getAttribute('aria-label') || node.getAttribute('title') || '');
      return label.includes('关闭') || label.includes('×') || label.includes('X') || rect.width >= 38;
    });
  return hasLargeVideo && hasClose;
}
"""
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def _author_video_modal_controls_visible(page: Any) -> bool:
    script = r"""
() => {
  const text = String(document.body?.innerText || '');
  return text.includes('发一条友好的弹幕吧') ||
    text.includes('清屏') ||
    text.includes('倍速') ||
    text.includes('听抖音');
}
"""
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def _open_author_playlist_comment_panel(page: Any) -> bool:
    if _has_comment_or_ai_panel(page):
        return True
    if _click_comment_button_from_rail(page):
        page.wait_for_timeout(1200)
        if _has_comment_or_ai_panel(page):
            return True
    for ratio in (0.58, 0.56, 0.60):
        try:
            size = _page_inner_size(page)
            page.mouse.click(size["width"] - 43, size["height"] * ratio)
            page.wait_for_timeout(1400)
            if _has_comment_or_ai_panel(page):
                return True
        except Exception:
            continue
    return False


def _click_author_side_works_tab(page: Any) -> bool:
    script = r"""
() => {
  const textOf = (node) => String(
    node.innerText ||
    node.textContent ||
    node.getAttribute('aria-label') ||
    node.getAttribute('title') ||
    ''
  ).replace(/\s+/g, '').trim();
  const visible = (node) => {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return rect.width > 8 &&
      rect.height > 8 &&
      rect.x > window.innerWidth * 0.58 &&
      rect.y >= 0 &&
      rect.y < Math.min(140, window.innerHeight * 0.18) &&
      style.visibility !== 'hidden' &&
      style.display !== 'none';
  };
  const nodes = Array.from(document.querySelectorAll('button, [role="button"], [role="tab"], a, div, span'))
    .filter(visible);
  const tab = nodes.find((node) => textOf(node) === 'TA的作品' || textOf(node) === '他的作品');
  if (!tab) return false;
  const target = tab.closest('button, [role="button"], [role="tab"], a') || tab;
  target.click();
  return true;
}
"""
    try:
        clicked = bool(page.evaluate(script))
    except Exception:
        clicked = False
    if not clicked:
        return False
    page.wait_for_timeout(1800)
    return _author_side_works_visible(page)


def _author_side_works_visible(page: Any) -> bool:
    script = r"""
() => {
  const text = String(document.body?.innerText || '');
  if (text.includes('播放中')) return true;
  const cards = Array.from(document.querySelectorAll('a[href*="/video/"], a[href*="/note/"], img'))
    .map((node) => node.getBoundingClientRect())
    .filter((rect) => rect.width >= 60 && rect.height >= 60 && rect.x > window.innerWidth * 0.64);
  return cards.length >= 2;
}
"""
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def _current_author_playlist_item(page: Any, author_info: dict[str, str], index: int) -> dict[str, Any]:
    script = r"""
() => {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const url = location.href;
  const sideCards = Array.from(document.querySelectorAll('a[href*="/video/"], a[href*="/note/"], div'))
    .map((node) => ({ node, rect: node.getBoundingClientRect(), text: clean(node.innerText || node.textContent || '') }))
    .filter((item) => item.rect.x > window.innerWidth * 0.62 && item.rect.width >= 80 && item.rect.height >= 60)
    .sort((a, b) => b.rect.width * b.rect.height - a.rect.width * a.rect.height);
  const playing = sideCards.find((item) => item.text.includes('播放中')) || null;
  const leftText = Array.from(document.querySelectorAll('div, span, a'))
    .map((node) => ({ rect: node.getBoundingClientRect(), text: clean(node.innerText || node.textContent || node.getAttribute('title') || node.getAttribute('aria-label') || '') }))
    .filter((item) => item.rect.x < window.innerWidth * 0.62 && item.rect.y > window.innerHeight * 0.55 && item.text.length >= 2 && item.text.length <= 500)
    .map((item) => item.text)
    .filter(Boolean)
    .slice(0, 12);
  const raw = clean([playing?.text || '', ...leftText].join('\n'));
  const noisy = (line) => /下载抖音|精选|推荐|搜索|关注|朋友|我的|直播|放映厅|短剧|小游戏|开启读屏|读屏标签|2026|ICP备|隐私政策|用户服务|广告投放|理财有风险|投资需谨慎|播放中/.test(line);
  const title = clean((leftText.find((line) => !line.startsWith('@') && !noisy(line)) || playing?.text?.replace('播放中', '') || document.title || `作品${Date.now()}`));
  return { url, title, text: raw, raw_text: raw, stats: '', title_attr: document.title || '' };
}
"""
    try:
        value = page.evaluate(script) or {}
    except Exception:
        value = {}
    url = str(value.get("url") or page.url or "")
    video_id = _video_id_from_url(url)
    title = str(value.get("title") or f"作品{index}").strip()
    raw_text = str(value.get("raw_text") or value.get("text") or title).strip()
    author = str(author_info.get("name") or "").strip()
    if author and author not in raw_text:
        raw_text = f"@{author}\n{raw_text}"
    return {
        "url": url,
        "title": title[:220],
        "text": raw_text[:8000],
        "raw_text": raw_text[:8000],
        "stats": str(value.get("stats") or ""),
        "source_id": f"douyin:video:{video_id}" if video_id else f"douyin:author_playlist:{index}",
    }


def _playlist_item_other_author(item: dict[str, Any], author_info: dict[str, str]) -> str:
    expected = _normalize(str(author_info.get("name") or ""))
    if not expected:
        return ""
    raw_text = str(item.get("raw_text") or item.get("text") or "")
    for match in re.finditer(r"@\s*([^\s@，,。·|｜#]{2,30})", raw_text):
        name = match.group(1).strip()
        normalized = _normalize(name)
        if not normalized:
            continue
        if "." in name or "douyin" in normalized or "bytedance" in normalized:
            continue
        if any(token in normalized for token in ("私信", "加载", "详情", "下载抖音", "正在播放")):
            continue
        if normalized in expected or expected in normalized:
            continue
        return name[:40]
    return ""


def _advance_author_playlist_video(page: Any, current_detail_url: str) -> bool:
    old_key = _canonical_detail_url(current_detail_url) or current_detail_url
    old_item = _normalize(str(_current_author_playlist_item(page, {}, 0).get("raw_text") or ""))[:120]
    try:
        size = _page_inner_size(page)
        page.mouse.move(size["width"] * 0.45, size["height"] * 0.52)
        page.mouse.wheel(0, 820)
        page.wait_for_timeout(4200)
    except Exception:
        return False
    new_key = _canonical_detail_url(page.url or "") or (page.url or "")
    if new_key and new_key != old_key:
        return True
    new_item = _normalize(str(_current_author_playlist_item(page, {}, 0).get("raw_text") or ""))[:120]
    return bool(new_item and old_item and new_item != old_item)


def _open_author_work_from_home(page: Any, author_url: str, raw_url: str, item: dict[str, Any]) -> bool:
    video_id = _video_id_from_url(raw_url)
    if not video_id:
        return False
    variants = _author_ai_detail_url_variants(raw_url, author_url)
    for target_url in variants:
        if "modal_id=" not in target_url:
            continue
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(8000)
            _dismiss_douyin_overlays(page)
            if _author_page_points_to_work(page.url or target_url, video_id) or _current_url_has_video_id(page, video_id):
                return True
        except Exception:
            continue
    return False


def _click_author_search_result_card(page: Any, video_id: str, item: dict[str, Any]) -> bool:
    snippets = _author_item_title_snippets(item)
    if not snippets:
        return False
    script = r"""
(snippet) => {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const visibleRect = (node) => {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    if (rect.width <= 0 || rect.height <= 0 || style.visibility === 'hidden' || style.display === 'none') return null;
    return rect;
  };
  const textNodes = Array.from(document.querySelectorAll('div, span, p, a'))
    .map((node) => ({ node, rect: visibleRect(node), text: clean(node.innerText || node.textContent) }))
    .filter((item) => (
      item.rect &&
      item.rect.x > 120 &&
      item.rect.y > 120 &&
      item.rect.y < window.innerHeight - 40 &&
      item.text.includes(snippet)
    ));
  for (const item of textNodes.sort((a, b) => a.rect.y - b.rect.y || a.rect.x - b.rect.x)) {
    let node = item.node;
    for (let i = 0; i < 7 && node; i += 1, node = node.parentElement) {
      const rect = visibleRect(node);
      if (!rect) continue;
      const text = clean(node.innerText || node.textContent);
      const cardLike = rect.width >= 150 && rect.width <= 320 && rect.height >= 180 && rect.height <= 480 && text.includes(snippet);
      if (!cardLike) continue;
      return { x: rect.x + rect.width / 2, y: rect.y + Math.min(rect.height * 0.38, 150) };
    }
  }
  return null;
}
"""
    for _ in range(8):
        for snippet in snippets:
            try:
                point = page.evaluate(script, snippet)
                if not point:
                    continue
                page.mouse.click(float(point["x"]), float(point["y"]))
                page.wait_for_timeout(7000)
                if _current_url_has_video_id(page, video_id):
                    return True
                _close_douyin_video_modal(page)
                page.wait_for_timeout(900)
            except Exception:
                continue
        try:
            page.mouse.wheel(0, 1800)
        except Exception:
            pass
        page.wait_for_timeout(1500)
    return False


def _click_author_work_card(page: Any, video_id: str, item: dict[str, Any]) -> bool:
    selectors = [
        f'a[href*="/video/{video_id}"]',
        f'a[href*="/note/{video_id}"]',
        f'a[href*="{video_id}"]',
    ]
    for _ in range(24):
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.count() <= 0:
                    continue
                locator.scroll_into_view_if_needed(timeout=3000)
                page.wait_for_timeout(400)
                locator.click(timeout=5000)
                page.wait_for_timeout(5000)
                if _current_url_has_video_id(page, video_id):
                    return True
                _close_douyin_video_modal(page)
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                page.wait_for_timeout(800)
                return False
            except Exception:
                continue
        try:
            page.mouse.wheel(0, 2200)
        except Exception:
            pass
        page.wait_for_timeout(1300)
    return False


def _close_douyin_video_modal(page: Any) -> None:
    for _ in range(2):
        if "modal_id=" not in (page.url or ""):
            return
        try:
            page.mouse.click(52, 52)
            page.wait_for_timeout(1800)
        except Exception:
            return


def _current_url_has_video_id(page: Any, video_id: str) -> bool:
    if not video_id:
        return False
    url = page.url or ""
    if video_id in url:
        return True
    return _video_id_from_url(url) == video_id


def _author_page_points_to_work(url: str, video_id: str) -> bool:
    if not video_id:
        return False
    parsed = urlparse(url or "")
    query = parse_qs(parsed.query)
    modal_values = query.get("modal_id") or []
    if modal_values:
        return modal_values[0] == video_id
    vid_values = query.get("vid") or []
    if vid_values:
        return vid_values[0] == video_id
    return _video_id_from_url(url) == video_id


def _author_item_matches_visible_text(page: Any, item: dict[str, Any]) -> bool:
    try:
        text = _safe_page_text(page)
    except Exception:
        text = ""
    if not text:
        return False
    title = str(item.get("title") or item.get("text") or item.get("raw_text") or "")
    lesson = _lesson_number_from_text(title)
    if lesson and re.search(rf"第\s*{lesson}\s*课", text):
        return True
    tokens = _author_item_keyword_tokens(title)
    if not tokens:
        return True
    normalized_text = _normalize(text)
    hits = sum(1 for token in tokens if _normalize(token) in normalized_text)
    return hits >= min(2, len(tokens))


def _transcript_matches_author_item(transcript: str, item: dict[str, Any]) -> bool:
    title = str(item.get("title") or item.get("text") or item.get("raw_text") or "")
    lesson = _lesson_number_from_text(title)
    actual_lesson = _lesson_number_from_text(transcript)
    if lesson and actual_lesson and lesson != actual_lesson:
        return False
    if lesson and re.search(rf"第\s*{lesson}\s*课", transcript):
        return True
    tokens = _author_item_keyword_tokens(title)
    if not tokens:
        return True
    normalized_text = _normalize(transcript)
    hits = sum(1 for token in tokens if _normalize(token) in normalized_text)
    if hits >= min(2, len(tokens)):
        return True
    return any(len(_normalize(token)) >= 4 and _normalize(token) in normalized_text for token in tokens)


def _lesson_number_from_text(text: str) -> str:
    match = re.search(r"第\s*(\d{1,3})\s*课", text or "")
    return match.group(1) if match else ""


def _author_item_keyword_tokens(title: str) -> list[str]:
    clean = re.sub(r"#.*$", "", title or "")
    clean = re.sub(r"^.*?股市生存指南[》】]?", "", clean)
    clean = re.sub(r"第\s*\d{1,3}\s*课[，,:：、\s]*", "", clean)
    raw_tokens = re.split(r"[\s，,。.!！?？、&/（）()《》【】:_：#]+", clean)
    stopwords = {
        "机油手",
        "股市生存指南",
        "股票",
        "股市",
        "知识",
        "理财",
        "小白",
        "同花顺",
        "风险提示",
    }
    tokens: list[str] = []
    for token in raw_tokens:
        token = token.strip()
        candidates = [token]
        if "的" in token:
            candidates.append(token.split("的", 1)[0])
        generic_pattern = r"(?:\d+条)?(?:完整)?(?:经典)?(?:精华)?(?:语录)?(?:总结|心路历程|成长经验|问答精华)$"
        cleaned = re.sub(generic_pattern, "", token)
        if cleaned and cleaned != token:
            candidates.append(cleaned)
        for candidate in candidates:
            candidate = candidate.strip()
            candidate = re.sub(r"(?:\d+条)?(?:完整)?(?:经典)?(?:精华)?(?:语录)?(?:总结)$", "", candidate).strip()
            if len(candidate) < 2 or candidate in stopwords:
                continue
            if candidate not in tokens:
                tokens.append(candidate)
    return tokens[:5]


def _video_id_from_url(url: str) -> str:
    match = re.search(r"/(?:video|note)/(\d+)", url or "")
    if match:
        return match.group(1)
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("vid", "aweme_id", "modal_id", "group_id", "search_result_id"):
        values = query.get(key) or []
        if values and re.fullmatch(r"\d{8,}", values[0]):
            return values[0]
    return ""


def _author_user_url_base(url: str) -> str:
    parsed = urlparse(url or "")
    match = re.search(r"(/user/[^/?#]+)", parsed.path or "")
    if not match:
        return ""
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or "www.douyin.com"
    return f"{scheme}://{netloc}{match.group(1)}"


def _prepare_author_detail_page_for_ai(page: Any) -> bool:
    page.wait_for_timeout(7000)
    _disable_douyin_autoplay(page)
    if not _click_author_comment_simple(page):
        return _click_author_ai_rail_button(page) or _has_author_ai_entry(page)
    page.wait_for_timeout(3000)
    return _right_panel_has_comment_content(page) or _author_comment_tab_active(page) or _has_author_ai_entry(page)


def _click_author_comment_simple(page: Any) -> bool:
    if _right_panel_has_comment_content(page):
        return True
    if _click_author_comment_tab(page):
        page.wait_for_timeout(2000)
        if _right_panel_has_comment_content(page) or _author_comment_tab_active(page):
            return True
    return False


def _click_author_ai_tab_simple(page: Any) -> bool:
    if _ai_input_locator(page):
        return True
    script = """
() => {
  const textOf = (node) => (
    node.innerText ||
    node.textContent ||
    node.getAttribute('aria-label') ||
    node.getAttribute('title') ||
    ''
  ).trim().replace(/\\s+/g, '');
  const visible = (node) => {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return rect.width > 8 &&
      rect.height > 8 &&
      rect.x > window.innerWidth * 0.62 &&
      rect.y >= 0 &&
      rect.y < Math.min(170, window.innerHeight * 0.22) &&
      style.visibility !== 'hidden' &&
      style.display !== 'none';
  };
  const nodes = Array.from(document.querySelectorAll('button, [role="button"], [role="tab"], a, div, span'))
    .filter(visible);
  const tab = nodes.find((node) => {
    const text = textOf(node);
    return text === '问AI' || text === '问问AI';
  });
  if (!tab) return false;
  const target = tab.closest('button, [role="button"], [role="tab"], a') || tab;
  const rect = target.getBoundingClientRect();
  return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
}
"""
    try:
        point = page.evaluate(script)
        if point:
            page.mouse.click(float(point["x"]), float(point["y"]))
            page.wait_for_timeout(2000)
            return _wait_for_ai_input(page, timeout_ms=10000)
    except Exception:
        pass
    if _click_author_ai_rail_button(page):
        page.wait_for_timeout(2500)
        return _wait_for_ai_input(page, timeout_ms=10000)
    return False


def _click_author_ai_rail_button(page: Any) -> bool:
    script = """
() => {
  const labelOf = (node) => [
    node.innerText || '',
    node.textContent || '',
    node.getAttribute('aria-label') || '',
    node.getAttribute('title') || '',
    node.getAttribute('class') || '',
  ].join(' ').trim();
  const visible = (node) => {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return rect.width >= 22 &&
      rect.height >= 22 &&
      rect.width <= 96 &&
      rect.height <= 96 &&
      rect.x > window.innerWidth * 0.72 &&
      rect.y > window.innerHeight * 0.16 &&
      rect.y < window.innerHeight * 0.50 &&
      style.visibility !== 'hidden' &&
      style.display !== 'none';
  };
  const nodes = Array.from(document.querySelectorAll('button, [role="button"], a, div, span'))
    .filter(visible)
    .map((node) => {
      const target = node.closest('button, [role="button"], a') || node;
      const rect = target.getBoundingClientRect();
      return { node: target, rect, label: labelOf(target) || labelOf(node) };
    })
    .filter((item) => /(^|[^a-z])AI([^a-z]|$)|问AI|问问AI|ai/i.test(item.label));
  const dedup = [];
  for (const item of nodes.sort((a, b) => a.rect.y - b.rect.y || b.rect.x - a.rect.x)) {
    if (dedup.some((old) => Math.abs(old.rect.x - item.rect.x) < 8 && Math.abs(old.rect.y - item.rect.y) < 8)) continue;
    dedup.push(item);
  }
  const target = dedup[0]?.node;
  if (!target) return false;
  target.click();
  return true;
}
"""
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def _extract_author_transcript_via_douyin_ai_simple(page: Any, prompt: str, item: dict[str, Any]) -> dict[str, str]:
    prompt = _clean_douyin_ai_prompt(prompt)
    if not _click_author_ai_tab_simple(page):
        _write_ai_debug(page, "author_no_ask_ai_tab", _safe_page_text(page)[:3000])
        return {"transcript": "", "status": "作者页评论面板未找到问AI标签"}
    page.wait_for_timeout(2500)
    before_text = _safe_ai_panel_text(page)
    before_lines = {_normalize(line) for line in before_text.splitlines() if line.strip()}

    if not _submit_ai_prompt(page, prompt):
        _write_ai_debug(page, "author_submit_failed", _safe_ai_panel_text(page)[:3000] or _safe_page_text(page)[:3000])
        return {"transcript": "", "status": "抖音AI提交问题失败"}

    prompt_key = _normalize(prompt)
    if not _wait_for_ai_prompt_echo(page, prompt_key):
        _write_ai_debug(page, "author_prompt_not_echoed", _safe_ai_panel_text(page)[:3000] or _safe_page_text(page)[:3000])
        return {"transcript": "", "status": "抖音AI提交后未出现新问题，未复制旧答案"}

    best = ""
    rejected_best = ""
    last_after_text = ""
    for _ in range(180):
        try:
            if page.is_closed():
                return {"transcript": "", "status": "抖音AI页面已关闭，未完成复制"}
            page.wait_for_timeout(1000)
            after_text = _safe_ai_panel_text(page)
        except Exception:
            return {"transcript": "", "status": "抖音AI页面已关闭，未完成复制"}
        last_after_text = after_text
        candidate = _extract_ai_answer(after_text, before_lines, prompt_key)
        if _looks_like_usable_ai_answer(candidate):
            if len(candidate) > len(best):
                best = candidate
        elif len(candidate) > len(rejected_best):
            rejected_best = candidate

        if _ai_response_ready(page, after_text):
            copied = _copy_latest_ai_answer(page, prompt_key)
            if copied and _looks_like_usable_ai_answer(copied):
                return {"transcript": copied, "status": "抖音AI复制按钮返回生成内容，未核验逐字"}
            if copied and len(copied) > len(rejected_best):
                rejected_best = copied
            if best:
                return {"transcript": best, "status": "抖音AI返回生成内容，未核验逐字"}
    if _looks_ai_response_done(last_after_text) and _ai_generation_done_dom(page):
        copied = _copy_latest_ai_answer(page, prompt_key)
        if copied and _looks_like_usable_ai_answer(copied):
            return {"transcript": copied, "status": "抖音AI复制按钮返回生成内容，未核验逐字"}
    if _looks_like_usable_ai_answer(best):
        return {"transcript": best, "status": "抖音AI返回生成内容，未核验逐字"}
    if rejected_best:
        _write_ai_debug(page, "author_rejected", rejected_best)
        return {"transcript": "", "status": "抖音AI返回内容不完整或疑似网页噪声，未保存"}
    _write_ai_debug(page, "author_empty", last_after_text)
    return {"transcript": "", "status": "抖音AI未返回可提取文本"}


def _wait_for_author_ai_context(page: Any, item: dict[str, Any]) -> bool:
    tokens = _author_item_keyword_tokens(str(item.get("title") or item.get("text") or item.get("raw_text") or ""))
    if not tokens:
        return True
    normalized_tokens = [_normalize(token) for token in tokens if len(_normalize(token)) >= 2]
    if not normalized_tokens:
        return True
    for _ in range(30):
        panel_text = _normalize(_safe_ai_panel_text(page))
        hits = sum(1 for token in normalized_tokens if token in panel_text)
        if hits >= min(2, len(normalized_tokens)):
            return True
        page.wait_for_timeout(500)
    return False


def _force_click_rail_comment_button(page: Any) -> bool:
    if _right_panel_has_comment_content(page):
        return True
    if _click_comment_button_from_rail(page):
        page.wait_for_timeout(900)
        if _right_panel_has_comment_content(page) or _has_author_ai_entry(page):
            return True
    try:
        size = _page_inner_size(page)
        page.mouse.click(size["width"] - 42, size["height"] * 0.62)
        page.wait_for_timeout(1200)
        return _right_panel_has_comment_content(page) or _has_author_ai_entry(page)
    except Exception:
        return False


def _force_open_author_comment_tab(page: Any) -> bool:
    clicked = _click_author_comment_tab(page)
    if not clicked:
        clicked = _click_author_comment_tab_by_position(page)
    if not clicked:
        return False
    page.wait_for_timeout(1200)
    return _author_comment_tab_active(page) or _right_panel_has_comment_content(page)


def _click_author_comment_tab(page: Any) -> bool:
    script = """
() => {
  const textOf = (node) => (
    node.innerText ||
    node.textContent ||
    node.getAttribute('aria-label') ||
    node.getAttribute('title') ||
    ''
  ).trim().replace(/\\s+/g, '');
  const visible = (node) => {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return rect.width > 8 &&
      rect.height > 8 &&
      rect.x > window.innerWidth * 0.62 &&
      rect.y >= 0 &&
      rect.y < Math.min(150, window.innerHeight * 0.20) &&
      style.visibility !== 'hidden' &&
      style.display !== 'none';
  };
  const nodes = Array.from(document.querySelectorAll('button, [role="button"], [role="tab"], a, div, span'))
    .filter(visible);
  const tab = nodes.find((node) => textOf(node) === '评论');
  if (!tab) return false;
  const target = tab.closest('button, [role="button"], [role="tab"], a') || tab;
  const rect = target.getBoundingClientRect();
  return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
}
"""
    try:
        point = page.evaluate(script)
        if not point:
            return False
        page.mouse.click(float(point["x"]), float(point["y"]))
        return True
    except Exception:
        return False


def _click_author_comment_tab_by_position(page: Any) -> bool:
    try:
        size = _page_inner_size(page)
        page.mouse.click(size["width"] - 210, 24)
        return True
    except Exception:
        return False


def _author_comment_tab_active(page: Any) -> bool:
    script = """
() => {
  const textOf = (node) => (
    node.innerText ||
    node.textContent ||
    node.getAttribute('aria-label') ||
    node.getAttribute('title') ||
    ''
  ).trim().replace(/\\s+/g, '');
  const visible = (node) => {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return rect.width > 8 &&
      rect.height > 8 &&
      rect.x > window.innerWidth * 0.62 &&
      style.visibility !== 'hidden' &&
      style.display !== 'none';
  };
  const tabs = Array.from(document.querySelectorAll('button, [role="button"], [role="tab"], a, div, span'))
    .filter(visible)
    .filter((node) => {
      const rect = node.getBoundingClientRect();
      return rect.y >= 0 && rect.y < Math.min(150, window.innerHeight * 0.20) && textOf(node) === '评论';
    });
  return tabs.some((node) => {
    const target = node.closest('[aria-selected], button, [role="button"], [role="tab"], a') || node;
    const cls = String(target.getAttribute('class') || node.getAttribute('class') || '');
    return target.getAttribute('aria-selected') === 'true' ||
      target.getAttribute('aria-current') === 'true' ||
      /active|selected|checked|is-active|semi-tabs-tab-active/i.test(cls);
  });
}
"""
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def _right_panel_has_comment_content(page: Any) -> bool:
    script = """
() => {
  const nodes = Array.from(document.querySelectorAll('aside, section, main, div'))
    .map((node) => ({ node, rect: node.getBoundingClientRect(), text: String(node.innerText || node.textContent || '') }))
    .filter((item) => (
      item.rect.x > window.innerWidth * 0.62 &&
      item.rect.y > 50 &&
      item.rect.width > 220 &&
      item.rect.height > 160
    ))
    .sort((a, b) => (b.rect.width * b.rect.height) - (a.rect.width * a.rect.height));
  const text = nodes[0]?.text || '';
  return text.includes('全部评论') ||
    text.includes('留下你的精彩评论') ||
    text.includes('暂无评论') ||
    text.includes('条评论');
}
"""
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def _has_author_ai_entry(page: Any) -> bool:
    script = """
() => {
  const textOf = (node) => (
    node.innerText ||
    node.textContent ||
    node.getAttribute('aria-label') ||
    node.getAttribute('title') ||
    ''
  ).trim();
  const visible = (node) => {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return rect.width > 8 &&
      rect.height > 8 &&
      rect.x > window.innerWidth * 0.62 &&
      rect.y >= 0 &&
      rect.y < Math.min(140, window.innerHeight * 0.18) &&
      style.visibility !== 'hidden' &&
      style.display !== 'none';
  };
  return Array.from(document.querySelectorAll('button, [role="button"], [role="tab"], a, div, span'))
    .filter(visible)
    .some((node) => {
      const text = textOf(node).replace(/\\s+/g, '');
      return text === '问AI' || text === '问问AI';
    });
}
"""
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def _write_author_item_review_bundle(
    page: Any,
    *,
    state_dir: Path,
    debug_dir: Path,
    author_info: dict[str, str],
    item: dict[str, Any],
    index: int,
    attempt: int,
) -> tuple[dict[str, Any], Path]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"{index}_{attempt}_{stamp}"
    item_review_md = state_dir / f"current_douyin_author_review_{suffix}.md"
    item_review_json = state_dir / f"current_douyin_author_review_{suffix}.json"
    item_review_screenshot = debug_dir / f"current_douyin_author_review_{suffix}.png"
    bundle = _current_video_review_bundle(page)
    bundle["author"] = author_info
    bundle["picked_candidate"] = item
    bundle["attempt"] = attempt
    bundle["screenshot"] = str(item_review_screenshot)
    try:
        page.screenshot(path=str(item_review_screenshot), full_page=False)
    except Exception:
        pass
    item_review_json.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    item_review_md.write_text(_review_bundle_markdown(bundle), encoding="utf-8")
    return bundle, item_review_md


def _should_retry_author_ai_status(status: str) -> bool:
    return any(
        token in status
        for token in (
            "未打开问AI面板",
            "提交问题失败",
            "页面已关闭",
            "打开视频或问AI失败",
        )
    )


def _click_author_works_tab(page: Any) -> None:
    script = """
() => {
  const labels = ['作品', 'TA的作品', '视频'];
  const nodes = Array.from(document.querySelectorAll('button, [role="button"], [role="tab"], a, div, span'));
  for (const node of nodes) {
    const text = (node.innerText || node.textContent || node.getAttribute('aria-label') || node.getAttribute('title') || '').trim();
    if (!labels.includes(text)) continue;
    const rect = node.getBoundingClientRect();
    if (rect.width < 8 || rect.height < 8) continue;
    (node.closest('button, [role="button"], [role="tab"], a') || node).click();
    return true;
  }
  return false;
}
"""
    try:
        page.evaluate(script)
    except Exception:
        pass


def _author_profile_info(page: Any, url: str) -> dict[str, str]:
    script = """
() => {
  const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
  const invalid = (value) => {
    const item = clean(value);
    return !item ||
      item.length < 2 ||
      item.length > 40 ||
      /私信|加载|关注|粉丝|获赞|作品|喜欢|收藏|评论|问AI|相关推荐|下载抖音|正在播放/.test(item);
  };
  const title = clean(document.title || '');
  const meta = clean(document.querySelector('meta[name="description"]')?.getAttribute('content') || '');
  const text = clean(document.body?.innerText || '');
  const atMatches = Array.from(text.matchAll(/@\\s*([^\\s@，,。·|｜]{2,24})(?=\\s*(?:\\d|粉丝|获赞|关注|·|路|认证|$))/g))
    .map((match) => clean(match[1]))
    .filter((item) => !invalid(item));
  const candidates = Array.from(document.querySelectorAll('h1, h2, [data-e2e*="user"], [class*="name"], [class*="Name"]'))
    .map((node) => clean(node.innerText || node.textContent || ''))
    .filter((item) => !invalid(item));
  const titleName = clean(title.replace(/[-_｜|].*$/, '').replace(/的抖音.*$/, ''));
  const metaName = clean(meta.replace(/，.*$/, ''));
  const name = atMatches[0] ||
    candidates[0] ||
    (!invalid(titleName) ? titleName : '') ||
    (!invalid(metaName) ? metaName : '');
  return { name, title, meta, page_text: text.slice(0, 1200), url: location.href };
}
"""
    try:
        value = page.evaluate(script)
    except Exception:
        value = {}
    name = str(value.get("name") or "").strip() if isinstance(value, dict) else ""
    if not name:
        match = re.search(r"/user/([^/?#]+)", url)
        name = f"作者_{match.group(1)[:10]}" if match else "未知作者"
    return {
        "name": name[:40],
        "url": str(value.get("url") or url) if isinstance(value, dict) else url,
        "title": str(value.get("title") or "") if isinstance(value, dict) else "",
        "meta": str(value.get("meta") or "") if isinstance(value, dict) else "",
    }


def _collect_author_work_items(page: Any, *, scroll_rounds: int) -> list[dict[str, Any]]:
    items_by_url: dict[str, dict[str, Any]] = {}
    stale_rounds = 0
    for _ in range(max(scroll_rounds, 1)):
        before = len(items_by_url)
        for item in _extract_author_visible_video_items(page):
            url = str(item.get("url") or "")
            if url:
                items_by_url[url] = item
        try:
            page.mouse.wheel(0, 2200)
        except Exception:
            pass
        page.wait_for_timeout(1600)
        if len(items_by_url) == before:
            stale_rounds += 1
        else:
            stale_rounds = 0
        if stale_rounds >= 3:
            break
    return list(items_by_url.values())


def _filter_author_work_items(works: list[dict[str, Any]], author: dict[str, str]) -> list[dict[str, Any]]:
    author_name = _normalize(str(author.get("name") or ""))
    if not author_name or len(author_name) < 2:
        return works
    filtered: list[dict[str, Any]] = []
    for item in works:
        text = _normalize(
            " ".join(
                str(item.get(key) or "")
                for key in ("title", "text", "raw_text")
            )
        )
        if author_name in text:
            filtered.append(item)
    return filtered or works


def _extract_author_visible_video_items(page: Any) -> list[dict[str, Any]]:
    script = """
() => {
  const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
    const anchors = Array.from(document.querySelectorAll('a[href*="/video/"], a[href*="/note/"]'));
  const out = [];
  const seen = new Set();
  for (const anchor of anchors) {
    let url = '';
    try {
      url = new URL(anchor.getAttribute('href'), location.href).href.split('?')[0];
    } catch (e) {
      continue;
    }
    if (!url || seen.has(url)) continue;
    seen.add(url);
    const root = anchor.closest('[data-e2e="user-post-item"], li, article, section, div') || anchor;
    const imageAlt = clean(anchor.querySelector('img')?.getAttribute('alt') || '');
    const text = clean(root.innerText || anchor.innerText || anchor.getAttribute('title') || anchor.getAttribute('aria-label') || imageAlt || '');
    const lines = text.split(/\\n| {2,}/).map(clean).filter(Boolean);
    const title = clean(anchor.getAttribute('title') || anchor.getAttribute('aria-label') || imageAlt || lines.find((line) => !/^\\d+(\\.\\d+)?[万wW]?$/.test(line)) || text);
    const idMatch = url.match(/\\/(video|note)\\/([^/?#]+)/);
    const statText = lines.filter((line) => /^\\d+(\\.\\d+)?[万wW]?$/.test(line)).join(' ');
    out.push({
      url,
      title: title.slice(0, 220),
      text: text.slice(0, 8000),
      raw_text: text.slice(0, 8000),
      stats: statText,
      source_id: idMatch ? `douyin:${idMatch[1]}:${idMatch[2]}` : url,
    });
  }
  return out;
}
"""
    try:
        return list(page.evaluate(script) or [])
    except Exception:
        return []


def _author_works_markdown(author: dict[str, str], works: list[dict[str, Any]]) -> str:
    lines = [
        "---",
        f"created_at: {datetime.now().isoformat(timespec='seconds')}",
        f"author: {author.get('name', '')}",
        f"author_url: {author.get('url', '')}",
        f"works: {len(works)}",
        "---",
        "",
        "# 抖音作者作品采集清单",
        "",
    ]
    for index, item in enumerate(works, start=1):
        lines.extend(
            [
                f"## {index}. {str(item.get('title') or '未命名')[:80]}",
                "",
                f"- url: {item.get('url', '')}",
                f"- stats: {item.get('stats', '')}",
                "",
                str(item.get("raw_text") or item.get("text") or "")[:800],
                "",
            ]
        )
    return "\n".join(lines)


def _default_author_output_path(root: Path, author: dict[str, str], item: dict[str, Any], index: int) -> Path:
    author_name = _slug_for_filename(str(author.get("name") or "未知作者"), max_len=28)
    inbox = root / "00_收件箱" / "抖音问AI" / author_name
    inbox.mkdir(parents=True, exist_ok=True)
    title = _candidate_title_hint(str(item.get("title") or item.get("text") or item.get("raw_text") or "")) or f"作品{index}"
    if _looks_like_noisy_author_title(title):
        video_id = _video_id_from_url(str(item.get("url") or ""))
        title = f"作品{index}_{video_id}" if video_id else f"作品{index}"
    slug = _slug_for_filename(title, max_len=36)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return inbox / f"{stamp}_{index:02d}_{slug}.md"


def _looks_like_noisy_author_title(title: str) -> bool:
    text = str(title or "")
    return any(
        token in text
        for token in (
            "下载抖音",
            "京ICP备",
            "京公网安备",
            "11000002002046",
            "用户服务协议",
            "隐私政策",
            "广告投放",
        )
    )


def _existing_mined_source_urls(root: Path) -> set[str]:
    folder = root / "00_收件箱" / "抖音问AI"
    urls: set[str] = set()
    if not folder.exists():
        return urls
    for path in folder.rglob("*.md"):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except Exception:
            continue
        source_match = re.search(r"(?m)^source:\s*(.*?)\s*$", text)
        source = source_match.group(1).strip() if source_match else ""
        if "抖音AI" not in source:
            continue
        for match in re.finditer(r"(?m)^source_url:\s*(.*?)\s*$", text):
            url = _canonical_detail_url(match.group(1).strip())
            if url:
                urls.add(url)
    return urls


def _extract_search_video_candidates(page: Any) -> list[dict[str, Any]]:
    script = r"""
() => {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const visible = (node) => {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return rect.width >= 140 &&
      rect.height >= 90 &&
      rect.x >= 90 &&
      rect.y >= 110 &&
      rect.y < window.innerHeight - 20 &&
      style.visibility !== 'hidden' &&
      style.display !== 'none';
  };
  const bad = (text) => (
    !text ||
    text.includes('问问AI智能总结内容') ||
    text.includes('相关搜索') ||
    text.includes('为你生成回答') ||
    text.includes('用户服务协议') ||
    text.includes('隐私政策')
  );
  const nodes = Array.from(document.querySelectorAll('a, article, section, div'))
    .filter(visible)
    .map((node) => {
      let best = node;
      let text = clean(node.innerText || node.textContent || '');
      let parent = node.parentElement;
      for (let i = 0; i < 4 && parent; i += 1, parent = parent.parentElement) {
        const rect = parent.getBoundingClientRect();
        const parentText = clean(parent.innerText || parent.textContent || '');
        if (rect.width >= 140 && rect.width <= 620 && rect.height >= 90 && rect.height <= 820 && parentText.length > text.length && parentText.length < 1400) {
          best = parent;
          text = parentText;
        }
      }
      const rect = best.getBoundingClientRect();
      return {
        node: best,
        text,
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
        click_x: Math.round(rect.x + Math.min(rect.width * 0.50, rect.width - 24)),
        click_y: Math.round(rect.y + Math.min(rect.height * 0.38, rect.height - 24)),
      };
    })
    .filter((item) => item.text.length >= 18 && item.text.length <= 1200 && !bad(item.text));
  const dedup = [];
  for (const item of nodes.sort((a, b) => a.y - b.y || a.x - b.x)) {
    const key = item.text.slice(0, 80);
    if (dedup.some((old) => old.text.includes(key) || key.includes(old.text.slice(0, 80)))) continue;
    dedup.push(item);
    if (dedup.length >= 12) break;
  }
  return dedup.map(({node, ...rest}, index) => ({ index: index + 1, ...rest }));
}
"""
    try:
        value = page.evaluate(script)
    except Exception:
        value = []
    return list(value or [])


def _search_candidates_markdown(url: str, candidates: list[dict[str, Any]], screenshot: Path) -> str:
    lines = [
        "---",
        f"created_at: {datetime.now().isoformat(timespec='seconds')}",
        f"url: {url}",
        f"screenshot: {screenshot}",
        "---",
        "",
        "# 抖音搜索候选复核包",
        "",
    ]
    if not candidates:
        lines.extend(["暂无候选", ""])
        return "\n".join(lines)
    for item in candidates:
        lines.extend(
            [
                f"## {item.get('index')}. 候选视频",
                "",
                f"- 坐标: x={item.get('x')} y={item.get('y')} w={item.get('width')} h={item.get('height')}",
                f"- 点击: x={item.get('click_x')} y={item.get('click_y')}",
                "",
                str(item.get("text") or "")[:1200],
                "",
            ]
        )
    return "\n".join(lines)


def _current_video_review_bundle(page: Any) -> dict[str, Any]:
    text = _safe_page_text(page)
    payload = {}
    try:
        payload = page.evaluate(
            """
() => {
  const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
  const visible = (node) => {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return rect.width > 8 && rect.height > 8 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const items = Array.from(document.querySelectorAll('a, button, [role="button"], [role="tab"], div, span'))
    .filter(visible)
    .map((node) => {
      const rect = node.getBoundingClientRect();
      return {
        text: clean(node.innerText || node.textContent || node.getAttribute('aria-label') || node.getAttribute('title') || ''),
        className: node.getAttribute('class') || '',
        role: node.getAttribute('role') || '',
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      };
    })
    .filter((item) => item.text && item.text.length >= 2 && item.text.length <= 220)
    .slice(0, 240);
  return {
    url: location.href,
    title: document.title,
    body_text: String(document.body?.innerText || '').slice(0, 12000),
    visible_items: items,
  };
}
"""
        )
    except Exception:
        payload = {}
    body_text = str(payload.get("body_text") or text or "")
    comments = _comments_preview_from_text(body_text)
    review_hint = _review_hint_from_text(body_text)
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "url": str(payload.get("url") or getattr(page, "url", "")),
        "title": str(payload.get("title") or ""),
        "review_hint": review_hint,
        "comments_preview": comments,
        "body_text_head": body_text[:6000],
        "visible_items": payload.get("visible_items") or [],
    }


def _review_hint_from_text(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    useful: list[str] = []
    for line in lines:
        clean = re.sub(r"\s+", " ", line).strip()
        if len(clean) < 4 or len(clean) > 180:
            continue
        if _is_ai_panel_ui_line(clean):
            continue
        if clean in useful:
            continue
        useful.append(clean)
        if len(useful) >= 18:
            break
    return "\n".join(useful)


def _comments_preview_from_text(text: str) -> list[str]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in str(text or "").splitlines()]
    anchors = [i for i, line in enumerate(lines) if line in {"评论", "全部评论"} or "全部评论" in line]
    start = anchors[-1] + 1 if anchors else 0
    out: list[str] = []
    skip_tokens = {
        "问AI",
        "详情",
        "TA的作品",
        "合集",
        "相关推荐",
        "留下你的精彩评论吧",
        "登录",
        "发送",
        "下载抖音精选",
    }
    for line in lines[start:]:
        if not line or any(token in line for token in skip_tokens):
            continue
        if len(line) < 4 or len(line) > 180:
            continue
        if re.fullmatch(r"[\d.万wW]+", line):
            continue
        if line not in out:
            out.append(line)
        if len(out) >= 30:
            break
    return out


def _review_bundle_markdown(bundle: dict[str, Any]) -> str:
    comments = bundle.get("comments_preview") or []
    visible_items = bundle.get("visible_items") or []
    lines = [
        "---",
        f"created_at: {bundle.get('created_at', '')}",
        f"url: {bundle.get('url', '')}",
        f"title: {bundle.get('title', '')}",
        f"screenshot: {bundle.get('screenshot', '')}",
        "---",
        "",
        "# 当前抖音视频复核包",
        "",
        "## 判断线索",
        "",
        str(bundle.get("review_hint") or "").strip() or "暂无",
        "",
        "## 可见评论片段",
        "",
    ]
    lines.extend(f"- {item}" for item in comments[:30])
    if not comments:
        lines.append("暂无")
    lines.extend(["", "## 可见元素片段", ""])
    for item in visible_items[:80]:
        text = str(item.get("text") or "").strip()
        if text:
            lines.append(f"- {text}")
    lines.extend(["", "## 页面文本片段", "", str(bundle.get("body_text_head") or "")[:3000], ""])
    return "\n".join(lines)


def _load_transcript_candidates(
    root: Path,
    date: str,
    *,
    min_interactions: int,
    allow_chapter_only: bool = False,
) -> list[TranscriptCandidate]:
    date_root = root / "20_视频卡片" / date
    if not date_root.exists():
        return []

    candidates: list[TranscriptCandidate] = []
    for path in date_root.rglob("*.md"):
        text = path.read_text(encoding="utf-8-sig")
        source = _frontmatter_value(text, "对白文本来源")
        transcript = _section(text, "对白文本")
        if "疑似错视频" in source:
            continue
        is_chapter_only = source == "抖音章节要点" or transcript.startswith("【抖音章节要点")
        if _has_transcript(text) and not (allow_chapter_only and is_chapter_only):
            continue
        interaction = _to_int(_frontmatter_value(text, "互动总数"))
        if interaction < min_interactions:
            continue
        title = _heading(text) or path.stem
        candidates.append(
            TranscriptCandidate(
                path=path,
                title=title,
                author=_frontmatter_value(text, "作者"),
                search_query=_frontmatter_value(text, "搜索词"),
                source_url=_frontmatter_value(text, "原视频链接") or _frontmatter_value(text, "来源"),
                interaction_count=interaction,
            )
        )
    return sorted(candidates, key=lambda item: item.interaction_count, reverse=True)


def _find_detail_url_from_search(
    page: Any,
    query: str,
    title: str,
    author: str,
    *,
    manual_seconds: int,
) -> str:
    if not query:
        return ""
    _open_douyin_web_search_results(page, query)
    if manual_seconds > 0:
        print(f"manual_navigation={manual_seconds}s query={query}", flush=True)
        page.wait_for_timeout(manual_seconds * 1000)
    page.wait_for_timeout(2500)
    for _ in range(2):
        matches = _extract_search_detail_links(page)
        best = _best_detail_match(matches, title, author)
        if best:
            return best
        best = _open_search_result_by_title(page, title)
        if best:
            return best
        page.mouse.wheel(0, 1800)
        page.wait_for_timeout(1500)
    return ""


def _extract_transcript_from_search_result(
    context: Any,
    page: Any,
    query: str,
    title: str,
    author: str,
    *,
    manual_seconds: int,
    ai_prompt: str,
) -> dict[str, str]:
    if not query:
        return {"transcript": "", "source": "搜索词为空", "detail_url": ""}
    _open_douyin_web_search_results(page, query)
    if manual_seconds > 0:
        print(f"manual_navigation={manual_seconds}s query={query}", flush=True)
        page.wait_for_timeout(manual_seconds * 1000)
    page.wait_for_timeout(2500)
    last_url = page.url
    for _ in range(3):
        before_pages = {id(item) for item in context.pages if not item.is_closed()}
        clicked = _click_search_result_card(page, title, author)
        if clicked:
            detail_page = _page_after_search_click(context, page, last_url, before_pages)
            try:
                detail_page.set_viewport_size(dict(DOUYIN_VIEWPORT))
            except Exception:
                pass
            detail_page.wait_for_timeout(3500)
            _disable_douyin_autoplay(detail_page)
            ai_result = _extract_transcript_via_douyin_ai(detail_page, ai_prompt)
            transcript = str(ai_result.get("transcript") or "").strip()
            source = str(ai_result.get("status") or "搜索页点击后未获取对白")
            detail_url = detail_page.url
            if detail_page is not page:
                try:
                    detail_page.close()
                except Exception:
                    pass
            if transcript:
                return {"transcript": transcript, "source": source or "抖音AI对话", "detail_url": detail_url}
            return {"transcript": "", "source": source, "detail_url": detail_url}
        page.mouse.wheel(0, 1800)
        page.wait_for_timeout(1500)
        last_url = page.url
    return {"transcript": "", "source": "搜索页未找到可点击目标视频", "detail_url": ""}


def _open_douyin_web_search_results(page: Any, query: str) -> None:
    encoded = quote(query.strip(), safe="")
    if not encoded:
        return
    page.goto(f"https://www.douyin.com/search/{encoded}?type=general", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(6500)
    try:
        text = page.locator("body").inner_text(timeout=3000)
    except Exception:
        text = ""
    if "为你找到以下结果" not in text and "问问AI" not in text and "综合" not in text:
        page.goto(f"https://www.douyin.com/jingxuan/search/{encoded}?type=general", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(6500)


def _click_search_result_card(page: Any, title: str, author: str) -> bool:
    script = r"""
([title, author]) => {
  const norm = (value) => String(value || '').replace(/\s+/g, '').toLowerCase();
  const titleKey = norm(title);
  const authorKey = norm(author);
  const visible = (node) => {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return rect.width >= 120 &&
      rect.height >= 100 &&
      rect.width <= 520 &&
      rect.height <= 720 &&
      rect.x > 90 &&
      rect.y > 90 &&
      rect.y < window.innerHeight - 30 &&
      style.visibility !== 'hidden' &&
      style.display !== 'none';
  };
  const scoreText = (text) => {
    const hay = norm(text);
    if (!hay) return 0;
    let score = 0;
    if (titleKey && hay.includes(titleKey)) score += 140;
    if (titleKey && titleKey.length > 12 && hay.includes(titleKey.slice(0, 12))) score += 70;
    if (authorKey && hay.includes(authorKey)) score += 35;
    if (titleKey) {
      const chars = new Set([...titleKey]);
      let overlap = 0;
      for (const ch of chars) {
        if (hay.includes(ch)) overlap += 1;
      }
      score += Math.min(overlap * 2, 55);
    }
    if (hay.includes('问问ai智能总结内容') || hay.includes('相关搜索')) score -= 60;
    return score;
  };
  const nodes = Array.from(document.querySelectorAll('a, [role="link"], article, section, div'))
    .filter(visible)
    .map((node) => {
      let text = (node.innerText || node.textContent || '').trim();
      let best = node;
      let parent = node.parentElement;
      for (let i = 0; i < 4 && parent; i += 1, parent = parent.parentElement) {
        const rect = parent.getBoundingClientRect();
        const parentText = (parent.innerText || parent.textContent || '').trim();
        if (rect.width >= 120 && rect.width <= 560 && rect.height >= 100 && rect.height <= 760 && parentText.length > text.length && parentText.length < 1200) {
          text = parentText;
          best = parent;
        }
      }
      const rect = best.getBoundingClientRect();
      return { node: best, rect, text, score: scoreText(text) };
    })
    .filter((item) => item.score >= 20)
    .sort((a, b) => b.score - a.score || a.rect.y - b.rect.y);
  const best = nodes[0];
  if (!best) return null;
  return {
    x: Math.round(best.rect.x + Math.min(best.rect.width * 0.50, best.rect.width - 24)),
    y: Math.round(best.rect.y + Math.min(best.rect.height * 0.38, best.rect.height - 24)),
    score: best.score,
    text: best.text.slice(0, 160),
  };
}
"""
    try:
        target = page.evaluate(script, [title, author])
        if not target:
            return False
        page.mouse.click(float(target["x"]), float(target["y"]))
        return True
    except Exception:
        return False


def _page_after_search_click(context: Any, page: Any, before_url: str, before_pages: set[int]) -> Any:
    page.wait_for_timeout(4500)
    pages = [item for item in context.pages if not item.is_closed()]
    for candidate in reversed(pages):
        if candidate is not page and id(candidate) not in before_pages:
            try:
                text = candidate.locator("body").inner_text(timeout=1000)
            except Exception:
                text = ""
            if candidate.url != "about:blank" or "问AI" in text or "评论" in text:
                return candidate
    if page.url != before_url:
        return page
    return page


def _open_search_result_by_title(page: Any, title: str) -> str:
    if not title:
        return ""
    candidates = [title]
    compact = re.sub(r"\s+", "", title)
    if compact and compact != title:
        candidates.append(compact)
    for candidate in candidates:
        try:
            locator = page.get_by_text(candidate, exact=True).first
            if locator.count() <= 0:
                locator = page.get_by_text(candidate, exact=False).first
            if locator.count() <= 0:
                continue
            try:
                with page.expect_popup(timeout=5000) as popup_info:
                    locator.click(timeout=5000)
                popup = popup_info.value
                popup.wait_for_load_state("domcontentloaded", timeout=20000)
                url = popup.url
                popup.close()
                if _is_detail_url(url):
                    return url
            except Exception:
                before_url = page.url
                locator.click(timeout=5000)
                page.wait_for_timeout(5000)
                if page.url != before_url and _is_detail_url(page.url):
                    return page.url
        except Exception:
            continue
    return ""


def _extract_search_detail_links(page: Any) -> list[dict[str, str]]:
    script = """
() => {
  const anchors = Array.from(document.querySelectorAll('a[href*="/video/"], a[href*="/note/"]'));
  const out = [];
  const seen = new Set();
  for (const anchor of anchors) {
    let url = '';
    try {
      url = new URL(anchor.getAttribute('href'), location.href).href.split('?')[0];
    } catch (e) {
      continue;
    }
    if (seen.has(url)) continue;
    seen.add(url);

    let node = anchor;
    let text = '';
    for (let i = 0; i < 7 && node; i += 1) {
      const candidate = (node.innerText || '').trim();
      if (candidate.length > text.length && candidate.length < 1800) text = candidate;
      node = node.parentElement;
    }
    const imageAlt = anchor.querySelector('img')?.getAttribute('alt') || '';
    const title = anchor.getAttribute('title') || anchor.getAttribute('aria-label') || imageAlt || '';
    out.push({ url, title, text });
  }
  return out;
}
"""
    try:
        return page.evaluate(script)
    except Exception:
        return []


def _best_detail_match(items: list[dict[str, str]], title: str, author: str) -> str:
    normalized_title = _normalize(title)
    normalized_author = _normalize(author)
    best_score = 0
    best_url = ""
    for item in items:
        haystack = _normalize(" ".join([item.get("title", ""), item.get("text", "")]))
        if not haystack:
            continue
        score = 0
        if normalized_title and normalized_title in haystack:
            score += 100
        elif normalized_title:
            title_chars = set(normalized_title)
            overlap = len(title_chars & set(haystack))
            score += min(overlap, 50)
        if normalized_author and normalized_author in haystack:
            score += 30
        if score > best_score:
            best_score = score
            best_url = item.get("url", "")
    return best_url if best_score >= 24 else ""


def _extract_transcript_from_detail(
    context: Any,
    detail_url: str,
    *,
    use_douyin_ai: bool,
    ai_prompt: str,
    use_asr: bool,
    whisper_model: str,
    media_dir: Path,
) -> dict[str, str]:
    canonical_url = _canonical_detail_url(detail_url) or detail_url
    detail_url = _ai_detail_url(canonical_url) if use_douyin_ai else canonical_url
    media_urls: list[str] = []
    page = context.new_page()

    def remember_media(response: Any) -> None:
        try:
            content_type = response.headers.get("content-type", "").lower()
            if "video" in content_type or "audio" in content_type:
                media_urls.append(response.url)
        except Exception:
            return

    page.on("response", remember_media)
    try:
        try:
            page.set_viewport_size(dict(DOUYIN_VIEWPORT))
        except Exception:
            pass
        page.goto(detail_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4500)
        _disable_douyin_autoplay(page)

        ai_status = ""
        if use_douyin_ai:
            ai_result = _extract_transcript_via_douyin_ai(page, ai_prompt)
            transcript = ai_result.get("transcript", "")
            ai_status = ai_result.get("status", "")
            if transcript:
                return {"transcript": transcript, "source": ai_status or "抖音AI对话"}
            if detail_url != canonical_url:
                page.goto(canonical_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3500)

        payload = _detail_payload(page)
        transcript = _caption_text_from_payload(payload)
        if transcript:
            return {"transcript": transcript, "source": "页面字幕/结构化文本"}

        chapter_points = _chapter_points_from_text(str(payload.get("body_text") or ""))
        if chapter_points:
            _write_chapter_points_hint = chapter_points
        else:
            _write_chapter_points_hint = ""

        video_urls = list(payload.get("video_urls", [])) + media_urls
        if use_asr:
            media_url = next((url for url in video_urls if url and not url.startswith("blob:")), "")
            if media_url:
                media_path = _download_media(context, media_url, media_dir)
                transcript = _transcribe_media(media_path, whisper_model=whisper_model)
                if transcript:
                    return {"transcript": transcript, "source": f"ASR:{whisper_model}"}
        if _write_chapter_points_hint and not use_douyin_ai and not use_asr:
            return {"transcript": _write_chapter_points_hint, "source": "抖音章节要点"}
        if _write_chapter_points_hint:
            source = "仅发现抖音章节要点，未作为对白保存"
            if ai_status:
                source = f"{ai_status}；{source}"
            return {"transcript": "", "source": source}
        fallback_source = "未发现页面字幕" if not use_asr else "未发现可下载音频"
        return {"transcript": "", "source": ai_status or fallback_source}
    finally:
        page.close()


def _extract_transcript_via_douyin_ai(page: Any, prompt: str, *, dismiss_overlays: bool = True) -> dict[str, str]:
    prompt = _clean_douyin_ai_prompt(prompt)
    if dismiss_overlays:
        _dismiss_douyin_overlays(page)
        page.wait_for_timeout(800)

    if not _open_ai_dialog(page):
        _write_ai_debug(page, "open_ai_failed", _safe_page_text(page)[:3000])
        return {"transcript": "", "status": "抖音AI未打开问AI面板"}
    page.wait_for_timeout(1000)

    before_text = _safe_ai_panel_text(page)
    before_lines = {_normalize(line) for line in before_text.splitlines() if line.strip()}

    if not _submit_ai_prompt(page, prompt):
        _write_ai_debug(page, "submit_failed", _safe_ai_panel_text(page)[:3000] or _safe_page_text(page)[:3000])
        return {"transcript": "", "status": "抖音AI提交问题失败"}

    prompt_key = _normalize(prompt)
    if not _wait_for_ai_prompt_echo(page, prompt_key):
        _write_ai_debug(page, "prompt_not_echoed", _safe_ai_panel_text(page)[:3000] or _safe_page_text(page)[:3000])
        return {"transcript": "", "status": "抖音AI提交后未出现新问题，未复制旧答案"}
    best = ""
    rejected_best = ""
    last_after_text = ""
    for _ in range(180):
        try:
            if page.is_closed():
                return {"transcript": "", "status": "抖音AI页面已关闭，未完成复制"}
            page.wait_for_timeout(1000)
            after_text = _safe_ai_panel_text(page)
        except Exception:
            return {"transcript": "", "status": "抖音AI页面已关闭，未完成复制"}
        last_after_text = after_text
        candidate = _extract_ai_answer(after_text, before_lines, prompt_key)
        if _looks_like_usable_ai_answer(candidate):
            if len(candidate) > len(best):
                best = candidate
        elif len(candidate) > len(rejected_best):
            rejected_best = candidate

        if _ai_response_ready(page, after_text):
            copied = _copy_latest_ai_answer(page, prompt_key)
            if copied:
                if _looks_like_usable_ai_answer(copied):
                    return {"transcript": copied, "status": "抖音AI复制按钮返回生成内容，未核验逐字"}
                if len(copied) > len(rejected_best):
                    rejected_best = copied
            if best:
                return {"transcript": best, "status": "抖音AI返回生成内容，未核验逐字"}
    if _looks_ai_response_done(last_after_text) and _ai_generation_done_dom(page):
        copied = _copy_latest_ai_answer(page, prompt_key)
        if copied:
            if _looks_like_usable_ai_answer(copied):
                return {"transcript": copied, "status": "抖音AI复制按钮返回生成内容，未核验逐字"}
            if len(copied) > len(rejected_best):
                rejected_best = copied
    if _looks_like_usable_ai_answer(best):
        return {"transcript": best, "status": "抖音AI返回生成内容，未核验逐字"}
    if rejected_best:
        _write_ai_debug(page, "rejected", rejected_best)
        return {"transcript": "", "status": "抖音AI返回内容不完整或疑似网页噪声，未保存"}
    _write_ai_debug(page, "empty", last_after_text)
    return {"transcript": "", "status": "抖音AI未返回可提取文本"}


def _extract_ai_answer(panel_text: str, before_lines: set[str], prompt_key: str) -> str:
    lines: list[str] = []
    seen_prompt = False
    for raw_line in panel_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        key = _normalize(line)
        if not key:
            continue
        if key == prompt_key or (prompt_key and prompt_key in key and len(key) <= len(prompt_key) + 16):
            seen_prompt = True
            continue
        if _is_ai_panel_ui_line(line):
            continue
        if not seen_prompt and key in before_lines:
            continue
        lines.append(line)
    return _clean_transcript_lines(lines)


def _wait_for_ai_prompt_echo(page: Any, prompt_key: str) -> bool:
    for _ in range(40):
        text = _safe_ai_panel_text(page)
        if prompt_key and prompt_key in _normalize(text):
            return True
        page.wait_for_timeout(500)
    return False


def _write_ai_debug(page: Any, label: str, text: str) -> None:
    try:
        debug_dir = Path.cwd() / ".state" / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "_", label)[:32] or "debug"
        (debug_dir / f"douyin_ai_{safe_label}_{stamp}.txt").write_text(text or "", encoding="utf-8")
        page.screenshot(path=str(debug_dir / f"douyin_ai_{safe_label}_{stamp}.png"), full_page=False)
    except Exception:
        pass


def _ai_response_ready(page: Any, text: str) -> bool:
    if len(text or "") < 300:
        _set_ai_ready_state(page, 0, 0)
        return False
    if not _looks_ai_response_done(text) or not _ai_generation_done_dom(page):
        _set_ai_ready_state(page, len(text or ""), 0)
        return False

    current_len = len(text)
    state = getattr(page, "_douyin_kb_ai_ready_state", {"length": 0, "stable": 0})
    previous_len = int(state.get("length", 0))
    stable = int(state.get("stable", 0))
    if abs(current_len - previous_len) <= 3:
        stable += 1
    else:
        stable = 0
    _set_ai_ready_state(page, current_len, stable)
    return stable >= 3


def _set_ai_ready_state(page: Any, length: int, stable: int) -> None:
    try:
        setattr(page, "_douyin_kb_ai_ready_state", {"length": length, "stable": stable})
    except Exception:
        pass


def _ai_frame(page: Any) -> Any | None:
    try:
        main_frame = page.main_frame
    except Exception:
        main_frame = None
    for frame in page.frames:
        if main_frame is not None and frame == main_frame:
            continue
        if "search_ai_mobile" in (frame.url or ""):
            return frame
    for frame in page.frames:
        if main_frame is not None and frame == main_frame:
            continue
        try:
            if getattr(frame, "parent_frame", None) is None:
                continue
        except Exception:
            pass
        try:
            text = frame.locator("body").inner_text(timeout=500)
        except Exception:
            continue
        if "AI 总结" in text or "问AI" in text or "找答案" in text:
            return frame
    return None


def _ai_input_locator(page: Any) -> Any | None:
    frame = _ai_frame(page)
    if not frame:
        return None
    selectors = [
        '[contenteditable="true"]',
        '[role="textbox"]',
        "textarea",
        "input",
    ]
    for selector in selectors:
        try:
            locator = frame.locator(selector).last
            if locator.count() > 0 and locator.is_visible(timeout=1000):
                return locator
        except Exception:
            continue
    return None


def _ai_generation_done_dom(page: Any) -> bool:
    script = """
() => {
  const busyTokens = ['正在生成', '生成中', '思考中', '停止生成', '取消生成'];
  const nodes = Array.from(document.querySelectorAll('button, [role="button"], div, span'));
  return !nodes.some((node) => {
    const rect = node.getBoundingClientRect();
    if (rect.x < window.innerWidth * 0.55 || rect.y < 40) return false;
    const text = (node.innerText || node.textContent || node.getAttribute('aria-label') || node.getAttribute('title') || '').trim();
    return busyTokens.some((token) => text.includes(token));
  });
}
"""
    try:
        target = _ai_frame(page) or page
        return bool(target.evaluate(script))
    except Exception:
        return True


def _copy_latest_ai_answer(page: Any, prompt_key: str) -> str:
    try:
        before_clipboard = _read_clipboard_text(page)
        clicked = _click_ai_copy_button(page)
        if not clicked:
            return ""
        page.wait_for_timeout(500)
        copied = _read_clipboard_text(page)
        if not copied or copied == before_clipboard:
            page.wait_for_timeout(1000)
            copied = _read_clipboard_text(page)
    except Exception:
        return ""
    if not copied or copied == before_clipboard:
        return ""
    lines = []
    for raw_line in copied.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        key = _normalize(line)
        if prompt_key and (key == prompt_key or prompt_key in key):
            continue
        if _is_ai_panel_ui_line(line):
            continue
        lines.append(line)
    return _clean_transcript_lines(lines)


def _click_ai_copy_button(page: Any) -> bool:
    _scroll_ai_panel_to_bottom(page)
    page.wait_for_timeout(300)
    frame = _ai_frame(page)
    if frame:
        frame_script = """
() => {
  const textOf = (node) => (
    node.innerText ||
    node.textContent ||
    node.getAttribute('aria-label') ||
    node.getAttribute('title') ||
    ''
  ).trim();
  const visible = (node) => {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return rect.width >= 14 &&
      rect.height >= 14 &&
      rect.y > 24 &&
      rect.y < window.innerHeight - 80 &&
      style.visibility !== 'hidden' &&
      style.display !== 'none';
  };
  const clickable = Array.from(document.querySelectorAll('button, [role="button"], a, div, span'))
    .filter(visible);

  const semantic = clickable.find((node) => {
    const text = textOf(node);
    return text && text.length <= 24 && (text.includes('复制') || text.includes('拷贝') || text.toLowerCase().includes('copy'));
  });
  if (semantic) {
    (semantic.closest('button, [role="button"], a') || semantic).click();
    return true;
  }

  const bars = Array.from(document.querySelectorAll('[id*="search_ai_action_bar"], [class*="action"], [class*="Action"]'))
    .map((node) => ({ node, rect: node.getBoundingClientRect() }))
    .filter((item) => visible(item.node) && item.rect.width > 120 && item.rect.height >= 24)
    .sort((a, b) => b.rect.y - a.rect.y);
  const bar = bars[0];
  if (bar) {
    const x = Math.min(bar.rect.x + 32, bar.rect.right - 8);
    const y = bar.rect.y + bar.rect.height / 2;
    const hit = document.elementFromPoint(x, y);
    const target = hit?.closest?.('button, [role="button"], a, div, span') || hit || bar.node;
    target.click();
    return true;
  }

  const small = clickable
    .map((node) => ({ node, rect: node.getBoundingClientRect(), text: textOf(node) }))
    .filter((item) => (
      item.rect.width >= 22 &&
      item.rect.width <= 70 &&
      item.rect.height >= 22 &&
      item.rect.height <= 70 &&
      item.rect.x < window.innerWidth * 0.45 &&
      item.rect.y > window.innerHeight * 0.45
    ))
    .sort((a, b) => b.rect.y - a.rect.y || a.rect.x - b.rect.x);
  if (small[0]) {
    (small[0].node.closest('button, [role="button"], a') || small[0].node).click();
    return true;
  }
  return false;
}
"""
        try:
            return bool(frame.evaluate(frame_script))
        except Exception:
            return False

    script = """
() => {
  const panelLeft = window.innerWidth * 0.705;
  const textOf = (node) => (
    node.innerText ||
    node.textContent ||
    node.getAttribute('aria-label') ||
    node.getAttribute('title') ||
    ''
  ).trim();
  const visible = (node) => {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return rect.width >= 16 &&
      rect.height >= 16 &&
      rect.x > panelLeft &&
      rect.y > window.innerHeight * 0.18 &&
      rect.y < window.innerHeight * 0.92 &&
      style.visibility !== 'hidden' &&
      style.display !== 'none';
  };
  const nodes = Array.from(document.querySelectorAll('button, [role="button"], div, span, a'))
    .filter(visible);

  const semantic = nodes.find((node) => {
    const text = textOf(node);
    return text && text.length <= 24 && (text.includes('复制') || text.includes('拷贝') || text.toLowerCase().includes('copy'));
  });
  if (semantic) {
    (semantic.closest('button, [role="button"], a') || semantic).click();
    return true;
  }

  const answerBlocks = Array.from(document.querySelectorAll('div, section, article'))
    .map((node) => ({ node, rect: node.getBoundingClientRect(), text: node.innerText || node.textContent || '' }))
    .filter((item) => {
      const right = item.rect.x > panelLeft && item.rect.width > 220;
      const readable = item.text.length > 100;
      const notFullPanel = item.rect.width < window.innerWidth * 0.42 && item.rect.height < window.innerHeight * 0.75;
      return right && readable && notFullPanel;
    })
    .sort((a, b) => b.rect.y - a.rect.y || b.text.length - a.text.length);
  const block = answerBlocks[0];
  if (!block) return false;

  const iconCandidates = nodes
    .map((node) => ({ node, rect: node.getBoundingClientRect(), text: textOf(node) }))
    .filter((item) => {
      const small = item.rect.width >= 24 && item.rect.width <= 58 && item.rect.height >= 24 && item.rect.height <= 58;
      const belowBlock = item.rect.y >= block.rect.y + Math.min(block.rect.height, window.innerHeight * 0.55) - 36;
      const nearLeft = item.rect.x >= block.rect.x - 4 && item.rect.x <= block.rect.x + 120;
      return small && belowBlock && nearLeft;
    })
    .sort((a, b) => a.rect.x - b.rect.x || a.rect.y - b.rect.y);
  if (iconCandidates[0]) {
    (iconCandidates[0].node.closest('button, [role="button"], a') || iconCandidates[0].node).click();
    return true;
  }

  const lowerIcons = nodes
    .map((node) => ({ node, rect: node.getBoundingClientRect(), text: textOf(node) }))
    .filter((item) => {
      const small = item.rect.width >= 24 && item.rect.width <= 64 && item.rect.height >= 24 && item.rect.height <= 64;
      const inAnswerFooter = item.rect.x > panelLeft &&
        item.rect.x < window.innerWidth * 0.90 &&
        item.rect.y > window.innerHeight * 0.55 &&
        item.rect.y < window.innerHeight * 0.88;
      return small && inAnswerFooter;
    })
    .sort((a, b) => a.rect.y - b.rect.y || a.rect.x - b.rect.x);
  if (lowerIcons[0]) {
    (lowerIcons[0].node.closest('button, [role="button"], a') || lowerIcons[0].node).click();
    return true;
  }
  return false;
}
"""
    try:
        target = _ai_frame(page) or page
        return bool(target.evaluate(script))
    except Exception:
        return False


def _scroll_ai_panel_to_bottom(page: Any) -> None:
    target = _ai_frame(page)
    if target:
        script = """
() => {
  const nodes = Array.from(document.querySelectorAll('#scrollContainer, [class*="chatContainer"], main, section, div'))
    .map((node) => ({ node, rect: node.getBoundingClientRect(), scroll: node.scrollHeight - node.clientHeight }))
    .filter((item) => item.scroll > 40 && item.rect.width > 220 && item.rect.height > window.innerHeight * 0.30)
    .sort((a, b) => b.scroll - a.scroll || b.rect.height - a.rect.height);
  for (const item of nodes.slice(0, 4)) {
    item.node.scrollTop = item.node.scrollHeight;
  }
  return nodes.length;
}
"""
        try:
            target.evaluate(script)
        except Exception:
            pass
        return

    script = """
() => {
  const candidates = Array.from(document.querySelectorAll('aside, section, div'))
    .map((node) => ({ node, rect: node.getBoundingClientRect(), scroll: node.scrollHeight - node.clientHeight }))
    .filter((item) => (
      item.scroll > 60 &&
      item.rect.x > window.innerWidth * 0.705 &&
      item.rect.width > 220 &&
      item.rect.height > window.innerHeight * 0.35
    ))
    .sort((a, b) => b.scroll - a.scroll);
  for (const item of candidates.slice(0, 4)) {
    item.node.scrollTop = item.node.scrollHeight;
  }
  return candidates.length;
}
"""
    try:
        (target or page).evaluate(script)
    except Exception:
        pass
    try:
        size = _page_inner_size(page)
        page.mouse.click(size["width"] - 220, size["height"] * 0.58)
        for _ in range(8):
            page.keyboard.press("End")
            page.wait_for_timeout(120)
        page.mouse.move(size["width"] - 220, size["height"] * 0.58)
        for _ in range(36):
            page.mouse.wheel(0, 2200)
            page.wait_for_timeout(80)
    except Exception:
        pass


def _read_clipboard_text(page: Any) -> str:
    try:
        page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://www.douyin.com")
    except Exception:
        pass
    try:
        text = str(page.evaluate("navigator.clipboard.readText()") or "").strip()
        if text:
            return text
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=3,
        )
        if result.returncode == 0:
            return (result.stdout or "").strip()
    except Exception:
        pass
    return ""


def _looks_like_requested_transcript(text: str) -> bool:
    if len((text or "").strip()) < 120:
        return False
    if re.search(r"https?://|www\.|douyin\.com", text, re.I):
        return False
    if not re.search(r"[\u4e00-\u9fff]", text):
        return False
    head = text[:220]
    refusal_markers = ["无法提供完整", "不能提供完整", "无法给出完整", "无法生成完整", "不支持提供完整"]
    if any(marker in head for marker in refusal_markers):
        return False
    page_noise_markers = [
        "手机随时看更方便",
        "下载 APP",
        "京ICP备",
        "用户服务协议",
        "章节要点",
        "第1章：",
        "许可证",
        "内容由AI生成",
        "合集：",
        "更新至第",
        "理财有风险",
        "以上结果由问问AI",
        "全网内容生成",
        "结合专业知识审阅判断",
        "关键台词的提炼",
        "未包含玩家互动弹幕内容",
        "#股票",
        "#股民",
        "大家都在搜",
        "全部评论",
        "条回复",
        "留下你的精彩评论",
    ]
    if any(marker in text[:1200] for marker in page_noise_markers):
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    first_lines = lines[:10]
    bullet_lines = sum(1 for line in first_lines if line.startswith(("•", "-", "·")))
    if bullet_lines >= 2:
        return False
    summary_markers = ["AI 总结", "AI总结", "要点", "规则区分", "操作策略", "判断方法", "实战技巧", "总体来看", "提炼"]
    if any(marker in head for marker in summary_markers) and "：" in head:
        return False
    if "以上结果由问问AI" in text or "全网内容生成" in text or "关键台词" in text and "提炼" in text:
        return False
    comment_markers = ("大家都在搜", "全部评论", "条回复", "留下你的精彩评论")
    if sum(1 for marker in comment_markers if marker in text[:1200]) >= 2:
        return False
    if _looks_like_ai_summary_not_transcript(text):
        return False
    return True


def _looks_like_usable_ai_answer(text: str) -> bool:
    text = (text or "").strip()
    if len(text) < 80:
        return False
    if re.search(r"https?://|www\.|douyin\.com", text, re.I):
        return False
    if not re.search(r"[\u4e00-\u9fff]", text):
        return False
    head = text[:260]
    refusal_markers = [
        "无法提供",
        "不能提供",
        "无法生成",
        "不支持提供",
        "无逐字稿",
        "没有逐字稿",
    ]
    if any(marker in head for marker in refusal_markers):
        return False
    page_noise_markers = [
        "手机随时看更方便",
        "下载 APP",
        "用户服务协议",
        "许可证",
        "大家都在搜",
        "全部评论",
        "留下你的精彩评论",
        "条回复",
    ]
    if any(marker in text[:1200] for marker in page_noise_markers):
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    content_lines = [line for line in lines if not _is_ai_panel_ui_line(line)]
    return len(content_lines) >= 1 and len("\n".join(content_lines)) >= 80


def _looks_like_ai_summary_not_transcript(text: str) -> bool:
    sample = text[:1600]
    compact = re.sub(r"\s+", "", sample)
    summary_tokens = [
        "交易纪律：",
        "龙头战法：",
        "周期认知：",
        "情绪周期：",
        "仓位策略：",
        "买点逻辑：",
        "卖点逻辑：",
        "风控规则：",
        "时间脉络",
        "核心瓶颈",
        "方法论",
        "要点总结",
        "总结如下",
        "主要内容",
        "总体来看",
    ]
    if sum(1 for token in summary_tokens if token in sample) >= 2:
        return True
    lines = [line.strip() for line in sample.splitlines() if line.strip()]
    labelled = 0
    for line in lines[:12]:
        if re.match(r"^[\u4e00-\u9fffA-Za-z0-9]{2,14}[：:]", line):
            labelled += 1
    if labelled >= 4 and not re.search(r"[“”]", compact):
        return True
    return False


def _is_ai_panel_ui_line(line: str) -> bool:
    clean = re.sub(r"\s+", "", line)
    if not clean:
        return True
    exact = {
        "问AI",
        "详情",
        "TA的作品",
        "评论",
        "合集",
        "相关推荐",
        "更多功能指令",
        "视频总结",
        "问AI，找答案",
        "下载抖音精选",
        "你好，欢迎使用问问AI",
        "手机随时看更方便",
        "下载 APP",
        "用户服务协议",
        "内容由AI生成",
        "理财有风险，投资需谨慎",
    }
    if clean in {re.sub(r"\s+", "", item) for item in exact}:
        return True
    prefixes = (
        "看到视频这一段",
        "你可能想问",
        "以下是可能的问题",
        "点击",
        "发送",
        "正在生成",
        "思考中",
        "加载中",
        "京ICP备",
        "京公网安备",
        "互联网",
        "网络文化",
        "广播电视",
        "增值电信",
        "药品医疗器械",
        "章节要点",
        "第1章：",
        "合集：",
        "更新至第",
        "#股票",
        "#股民",
    )
    return any(clean.startswith(re.sub(r"\s+", "", prefix)) for prefix in prefixes)


def _dismiss_douyin_overlays(page: Any) -> None:
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    try:
        page.evaluate(
            """
() => {
  const labels = ['我知道了', '知道了'];
  for (const node of Array.from(document.querySelectorAll('button, [role="button"], div, span'))) {
    const text = (node.innerText || node.textContent || '').trim();
    if (labels.includes(text)) {
      (node.closest('button, [role="button"]') || node).click();
      return true;
    }
  }
  return false;
}
"""
        )
    except Exception:
        pass
    try:
        size = _page_inner_size(page)
        page.mouse.click(size["width"] * 0.5, size["height"] * 0.64)
    except Exception:
        pass


def _disable_douyin_autoplay(page: Any) -> None:
    script = """
() => {
  for (const video of Array.from(document.querySelectorAll('video'))) {
    try {
      video.pause();
      video.autoplay = false;
      video.loop = false;
    } catch (e) {}
  }
  const textOf = (node) => (
    node.innerText ||
    node.textContent ||
    node.getAttribute('aria-label') ||
    node.getAttribute('title') ||
    ''
  ).trim();
  const visible = (node) => {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return rect.width >= 16 &&
      rect.height >= 12 &&
      rect.y > window.innerHeight * 0.82 &&
      rect.x > window.innerWidth * 0.40 &&
      rect.x < window.innerWidth * 0.72 &&
      style.visibility !== 'hidden' &&
      style.display !== 'none';
  };
  const nodes = Array.from(document.querySelectorAll('button, [role="button"], div, span'))
    .filter(visible);
  const autoPlay = nodes.find((node) => {
    const text = textOf(node);
    const cls = node.getAttribute('class') || '';
    const active = cls.includes('active') || cls.includes('checked') || cls.includes('on') || node.getAttribute('aria-checked') === 'true';
    return active && text.includes('连播');
  });
  if (autoPlay) {
    (autoPlay.closest('button, [role="button"]') || autoPlay).click();
    return true;
  }
  return false;
}
"""
    try:
        page.evaluate(script)
        page.wait_for_timeout(300)
    except Exception:
        pass
    try:
        page.evaluate("Array.from(document.querySelectorAll('video')).forEach((video) => { try { video.pause(); } catch (e) {} })")
    except Exception:
        pass


def _open_comment_panel(page: Any) -> bool:
    if _has_comment_or_ai_panel(page):
        return True

    selectors = [
        'button:has-text("评论")',
        'div[role="button"]:has-text("评论")',
        'span:has-text("评论")',
        'text=评论',
        'text=条评论',
        '[aria-label*="评论"]',
        '[title*="评论"]',
    ]
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() > 0:
                locator.click(timeout=3000)
                page.wait_for_timeout(900)
                if _has_comment_or_ai_panel(page):
                    return True
        except Exception:
            continue

    script = """
() => {
  const tokens = ['评论', '条评论', '查看评论', '打开评论'];
  const nodes = Array.from(document.querySelectorAll('button, [role="button"], div, span, a'));
  for (const node of nodes) {
    const text = (node.innerText || node.textContent || node.getAttribute('aria-label') || node.getAttribute('title') || '').trim();
    if (!text || text.length > 80) continue;
    if (!tokens.some(token => text.includes(token))) continue;
    const clickable = node.closest('button, [role="button"], a') || node;
    clickable.click();
    return true;
  }
  return false;
}
"""
    try:
        clicked = bool(page.evaluate(script))
        if clicked:
            page.wait_for_timeout(900)
            if _has_comment_or_ai_panel(page):
                return True
    except Exception:
        pass

    if _click_comment_button_from_rail(page):
        page.wait_for_timeout(1200)
        if _has_comment_or_ai_panel(page):
            return True
    if _click_comment_button_by_position(page):
        page.wait_for_timeout(1200)
        if _has_comment_or_ai_panel(page):
            return True
    return False


def _click_comment_button_from_rail(page: Any) -> bool:
    script = """
() => {
  const textOf = (node) => (
    node.innerText ||
    node.textContent ||
    node.getAttribute('aria-label') ||
    node.getAttribute('title') ||
    ''
  ).trim();
  const labelOf = (node) => [
    node.innerText || '',
    node.textContent || '',
    node.getAttribute('aria-label') || '',
    node.getAttribute('title') || '',
    node.getAttribute('class') || ''
  ].join(' ').trim();
  const visible = (node) => {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return rect.width >= 20 &&
      rect.height >= 20 &&
      rect.x > window.innerWidth * 0.55 &&
      rect.y > window.innerHeight * 0.16 &&
      rect.y < window.innerHeight * 0.88 &&
      style.visibility !== 'hidden' &&
      style.display !== 'none';
  };
  const raw = Array.from(document.querySelectorAll('button, [role="button"], a, div, span'))
    .filter(visible)
    .map((node) => {
      const clickable = node.closest('button, [role="button"], a') || node;
      const rect = clickable.getBoundingClientRect();
      const label = labelOf(clickable) || labelOf(node);
      return {
        node: clickable,
        rect,
        label,
        text: textOf(clickable) || textOf(node),
        cx: rect.x + rect.width / 2,
        cy: rect.y + rect.height / 2,
      };
    })
    .filter((item) => {
      const compact = item.rect.width >= 24 &&
        item.rect.width <= 96 &&
        item.rect.height >= 24 &&
        item.rect.height <= 96;
      const rail = item.cx > window.innerWidth * 0.58 && item.cx < window.innerWidth * 0.98;
      return compact && rail;
    });

  const items = [];
  for (const item of raw.sort((a, b) => a.cy - b.cy || a.cx - b.cx)) {
    if (items.some((old) => Math.abs(old.cx - item.cx) < 8 && Math.abs(old.cy - item.cy) < 8)) continue;
    items.push(item);
  }
  const has = (item, words) => words.some((word) => item.label.includes(word) || item.text.includes(word));
  const bad = (item) => has(item, ['收藏', '点赞', '喜欢', '分享', '转发', '关注', '头像', '更多', '听抖音', 'AI']);

  const explicitComment = items.find((item) => has(item, ['评论', '留言', 'comment']) && !has(item, ['收藏', '点赞', '分享']));
  if (explicitComment) {
    explicitComment.node.click();
    return true;
  }

  const favorite = items.find((item) => has(item, ['收藏', 'favorite', 'collect', 'star']));
  if (favorite) {
    const aboveFavorite = items
      .filter((item) => item.cy < favorite.cy && favorite.cy - item.cy >= 38 && favorite.cy - item.cy <= 150)
      .filter((item) => Math.abs(item.cx - favorite.cx) <= 80 && !bad(item))
      .sort((a, b) => (favorite.cy - a.cy) - (favorite.cy - b.cy))[0];
    if (aboveFavorite) {
      aboveFavorite.node.click();
      return true;
    }
  }

  const like = items.find((item) => has(item, ['点赞', '喜欢', 'like']));
  if (like) {
    const belowLike = items
      .filter((item) => item.cy > like.cy && item.cy - like.cy >= 38 && item.cy - like.cy <= 150)
      .filter((item) => Math.abs(item.cx - like.cx) <= 80 && !bad(item))
      .sort((a, b) => (a.cy - like.cy) - (b.cy - like.cy))[0];
    if (belowLike) {
      belowLike.node.click();
      return true;
    }
  }

  const iconColumn = items
    .filter((item) => (
      item.rect.width >= 24 &&
      item.rect.width <= 76 &&
      item.rect.height >= 24 &&
      item.rect.height <= 76 &&
      item.cx > window.innerWidth * 0.82 &&
      item.cy > window.innerHeight * 0.40 &&
      item.cy < window.innerHeight * 0.80 &&
      !has(item, ['分享', '转发', '更多', '听抖音', 'AI', '关注'])
    ))
    .sort((a, b) => a.cy - b.cy);
  if (iconColumn.length >= 3) {
    iconColumn[1].node.click();
    return true;
  }
  return false;
}
"""
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def _click_comment_button_by_position(page: Any) -> bool:
    try:
        size = _page_inner_size(page)
        x = size["width"] - 44
        y = size["height"] * 0.66
        page.mouse.click(x, y)
        return True
    except Exception:
        return False


def _has_comment_or_ai_panel(page: Any) -> bool:
    script = """
() => {
  const nodes = Array.from(document.querySelectorAll('[role="tab"], .semi-tabs-tab, button, [role="button"], div, span, a'));
  let hasComment = false;
  let hasAskAi = false;
  for (const node of nodes) {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    if (rect.width < 8 || rect.height < 8 || style.visibility === 'hidden' || style.display === 'none') continue;
    if (rect.x < window.innerWidth * 0.56 || rect.y > window.innerHeight * 0.22) continue;
    const text = (node.innerText || node.textContent || node.getAttribute('aria-label') || node.getAttribute('title') || '').trim();
    if (text === '评论' || text.includes('全部评论')) hasComment = true;
    if (text === '问AI' || text.includes('问问AI')) hasAskAi = true;
  }
  return hasComment || hasAskAi;
}
"""
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def _open_ai_dialog(page: Any) -> bool:
    if not _open_comment_panel(page):
        return False
    page.wait_for_timeout(600)
    for _ in range(3):
        if _wait_for_ai_input(page, timeout_ms=1200):
            return True
        if _looks_ask_ai_tab_active(page) and _wait_for_ai_input(page, timeout_ms=2500):
            return True
        for selector in ("#semiTabai_card", '[id="semiTabai_card"]', 'text=问AI'):
            try:
                locator = page.locator(selector).last
                if locator.count() > 0 and locator.is_visible(timeout=800):
                    locator.click(timeout=1500)
                    page.wait_for_timeout(1600)
                    if _wait_for_ai_input(page, timeout_ms=4000):
                        return True
            except Exception:
                continue
        try:
            clicked = bool(
                page.evaluate(
                    """
() => {
  const clickNode = (node) => {
    if (!node) return false;
    const target = node.closest('button, [role="button"], [role="tab"], a') || node;
    target.click();
    return true;
  };
  const textOf = (node) => (
    node.innerText ||
    node.textContent ||
    node.getAttribute('aria-label') ||
    node.getAttribute('title') ||
    ''
  ).trim();
  const visible = (node) => {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return rect.width > 8 && rect.height > 8 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const nodes = Array.from(document.querySelectorAll('button, [role="button"], [role="tab"], div, span, a'))
    .filter(visible);

  const askTab = nodes.find((node) => textOf(node) === '问AI');
  if (askTab) return clickNode(askTab);

  const aiText = nodes.find((node) => {
    const text = textOf(node);
    return text && text.length <= 24 && (text.includes('问问AI') || text.includes('AI总结') || text.includes('视频总结') || text === 'AI');
  });
  if (aiText) return clickNode(aiText);

  const rail = nodes
    .map((node) => ({ node, rect: node.getBoundingClientRect(), text: textOf(node) }))
    .filter((item) => {
      const compact = item.rect.width >= 24 && item.rect.height >= 24 && item.rect.width <= 110 && item.rect.height <= 110;
      const onRail = item.rect.x > window.innerWidth * 0.58 &&
        item.rect.x < window.innerWidth * 0.99 &&
        item.rect.y > window.innerHeight * 0.16 &&
        item.rect.y < window.innerHeight * 0.56;
      return compact && onRail;
    });
  const aiIcon = rail.find((item) => item.text.includes('AI')) || rail.find((item) => item.rect.y > 250 && item.rect.y < 430);
  return aiIcon ? clickNode(aiIcon.node) : false;
}
"""
                )
            )
        except Exception:
            clicked = False
        if clicked:
            page.wait_for_timeout(1500)
            if _wait_for_ai_input(page, timeout_ms=3500):
                return True
            if _looks_ask_ai_tab_active(page) and _wait_for_ai_input(page, timeout_ms=2500):
                return True

    try:
        size = _page_inner_size(page)
        page.mouse.click(size["width"] - 44, size["height"] * 0.41)
        page.wait_for_timeout(1800)
        if _wait_for_ai_input(page, timeout_ms=2500):
            return True
    except Exception:
        pass
    return False


def _looks_ask_ai_tab_active(page: Any) -> bool:
    script = """
() => {
  const tabs = Array.from(document.querySelectorAll('[role="tab"], .semi-tabs-tab'));
  return tabs.some((node) => {
    const text = (node.innerText || node.textContent || '').trim();
    const cls = node.getAttribute('class') || '';
    const selected = node.getAttribute('aria-selected') === 'true' || cls.includes('active');
    return text === '问AI' && selected;
  });
}
"""
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def _wait_for_ai_input(page: Any, *, timeout_ms: int) -> bool:
    deadline = max(timeout_ms, 500)
    waited = 0
    while waited <= deadline:
        if _ai_input_locator(page):
            return True
        page.wait_for_timeout(500)
        waited += 500
    return False


def _find_ai_input_box(page: Any, *, coordinate_fallback: bool = False) -> dict[str, float] | None:
    script = """
() => {
  window.__douyinKbPickAiInput = () => {
    const nodes = Array.from(document.querySelectorAll('textarea, input, [contenteditable="true"], [role="textbox"], div, span'));
    const visibleItems = nodes.map((node) => {
      const rect = node.getBoundingClientRect();
      const style = window.getComputedStyle(node);
      const placeholder = node.getAttribute('placeholder') || '';
      const aria = node.getAttribute('aria-label') || node.getAttribute('title') || '';
      const text = (node.innerText || node.textContent || node.value || '').trim();
      const parentText = (node.parentElement?.innerText || node.parentElement?.textContent || '').trim();
      const isTextLike = ['TEXTAREA', 'INPUT'].includes(node.tagName) ||
        node.isContentEditable ||
        node.getAttribute('role') === 'textbox';
      const looksAi = [placeholder, aria, text, parentText].some((value) => (
        value.includes('问AI') || value.includes('找答案') || value.includes('提问')
      ));
      const bottomEnough = isTextLike ? rect.y > window.innerHeight * 0.55 : rect.y > window.innerHeight * 0.70;
      const visible = rect.width > 120 &&
        rect.height > 16 &&
        rect.x > window.innerWidth * 0.52 &&
        bottomEnough &&
        style.visibility !== 'hidden' &&
        style.display !== 'none';
      const score = (isTextLike ? 1000 : 0) + rect.y + rect.width / 10;
      return { node, rect, looksAi, visible, score };
    }).filter((item) => item.visible && item.looksAi);
    visibleItems.sort((a, b) => b.score - a.score);
    return visibleItems[0]?.node || null;
  };
  const node = window.__douyinKbPickAiInput();
  if (!node) return null;
  const rect = node.getBoundingClientRect();
  return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
}
"""
    try:
        value = page.evaluate(script)
    except Exception:
        return None
    if not value:
        if not coordinate_fallback:
            return None
        try:
            body = _safe_page_text(page)
            size = _page_inner_size(page)
        except Exception:
            body = ""
            size = DOUYIN_VIEWPORT
        if "问AI" in body or _looks_ask_ai_tab_active(page):
            width = float(size["width"])
            height = float(size["height"])
            return {
                "x": width * 0.72,
                "y": height - 64,
                "width": width * 0.27,
                "height": 52.0,
            }
        return None
    return {
        "x": float(value.get("x", 0)),
        "y": float(value.get("y", 0)),
        "width": float(value.get("width", 0)),
        "height": float(value.get("height", 0)),
    }


def _page_inner_size(page: Any) -> dict[str, float]:
    try:
        value = page.evaluate("() => ({ width: window.innerWidth, height: window.innerHeight })")
        return {"width": float(value.get("width") or DOUYIN_VIEWPORT["width"]), "height": float(value.get("height") or DOUYIN_VIEWPORT["height"])}
    except Exception:
        size = page.viewport_size or DOUYIN_VIEWPORT
        return {"width": float(size["width"]), "height": float(size["height"])}


def _safe_ai_panel_text(page: Any) -> str:
    frame = _ai_frame(page)
    if frame:
        try:
            return frame.locator("body").inner_text(timeout=3000)
        except Exception:
            pass
    script = """
() => {
  const input = window.__douyinKbPickAiInput?.();
  const textOf = (node) => (node.innerText || node.textContent || '').trim();
  if (input) {
    let node = input.parentElement;
    let best = null;
    for (let i = 0; i < 10 && node; i += 1, node = node.parentElement) {
      const rect = node.getBoundingClientRect();
      const widthOk = rect.width > 220 && rect.width < window.innerWidth * 0.48;
      const heightOk = rect.height > window.innerHeight * 0.45;
      const rightSide = rect.x > window.innerWidth * 0.50;
      if (widthOk && heightOk && rightSide) best = node;
    }
    if (best) return textOf(best);
  }
  const candidates = Array.from(document.querySelectorAll('aside, section, div'))
    .map((node) => ({ node, rect: node.getBoundingClientRect(), text: textOf(node) }))
    .filter((item) => (
      item.text &&
      item.rect.width > 220 &&
      item.rect.width < window.innerWidth * 0.50 &&
      item.rect.height > window.innerHeight * 0.45 &&
      item.rect.x > window.innerWidth * 0.50
    ));
  candidates.sort((a, b) => (b.rect.width * b.rect.height) - (a.rect.width * a.rect.height));
  return candidates[0]?.text || '';
}
"""
    try:
        panel_text = str(page.evaluate(script) or "")
        if len(panel_text.strip()) >= 100:
            return panel_text
        return ""
    except Exception:
        return ""


def _submit_ai_prompt(page: Any, prompt: str) -> bool:
    prompt = _clean_douyin_ai_prompt(prompt)
    frame_input = _ai_input_locator(page)
    if frame_input:
        try:
            frame_input.click(timeout=3000)
            try:
                frame_input.press("Control+A", timeout=1000)
                frame_input.press("Backspace", timeout=1000)
            except Exception:
                pass
            try:
                frame_input.fill(prompt, timeout=3000)
            except Exception:
                frame_input.evaluate(
                    """
(node, value) => {
  node.focus();
  if (node.isContentEditable) {
    node.textContent = value;
    node.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));
    return true;
  }
  const setter = Object.getOwnPropertyDescriptor(node.constructor.prototype, 'value')?.set;
  if (setter) setter.call(node, value);
  else node.value = value;
  node.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));
  node.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}
""",
                    prompt,
                )
            page.wait_for_timeout(500)
            if prompt not in _safe_ai_panel_text(page):
                frame_input.evaluate(
                    """
(node, value) => {
  node.focus();
  if (node.isContentEditable) {
    node.textContent = value;
    node.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));
    return true;
  }
  if ('value' in node) {
    node.value = value;
    node.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));
    node.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }
  return false;
}
""",
                    prompt,
                )
                page.wait_for_timeout(300)
            current_value = str(
                frame_input.evaluate(
                    """
node => String(
  node.isContentEditable
    ? (node.innerText || node.textContent || '')
    : (node.value || node.textContent || '')
)
"""
                )
                or ""
            )
            if prompt not in current_value:
                return False
            frame_input.press("Enter", timeout=3000)
            page.wait_for_timeout(800)
            _click_ai_frame_send_if_present(page)
            return True
        except Exception:
            return False

    return False


def _click_send_if_present(page: Any) -> None:
    frame = _ai_frame(page)
    if frame:
        _click_ai_frame_send_if_present(page)
        return

    selectors = [
        'button:has-text("发送")',
        'div[role="button"]:has-text("发送")',
        '[aria-label*="发送"]',
        '[title*="发送"]',
    ]
    for selector in selectors:
        try:
            locator = page.locator(selector).last
            if locator.count() > 0:
                locator.click(timeout=1000)
                return
        except Exception:
            continue
    try:
        box = _find_ai_input_box(page, coordinate_fallback=True)
        if box:
            page.mouse.click(box["x"] + box["width"] - 26, box["y"] + box["height"] / 2)
    except Exception:
        pass


def _click_ai_frame_send_if_present(page: Any) -> None:
    frame = _ai_frame(page)
    if not frame:
        return
    script = """
() => {
  const nodes = Array.from(document.querySelectorAll('button, [role="button"], div, span'));
  const items = nodes.map((node) => ({ node, rect: node.getBoundingClientRect(), text: (node.innerText || node.textContent || node.getAttribute('aria-label') || node.getAttribute('title') || '').trim() }))
    .filter((item) => {
      const style = window.getComputedStyle(item.node);
      const visible = item.rect.width >= 18 && item.rect.height >= 18 && style.visibility !== 'hidden' && style.display !== 'none';
      const lowerRight = item.rect.x > window.innerWidth * 0.76 && item.rect.y > window.innerHeight * 0.84;
      const semantic = item.text.includes('发送') || item.text.toLowerCase().includes('send');
      return visible && (semantic || lowerRight);
    })
    .sort((a, b) => b.rect.x - a.rect.x || b.rect.y - a.rect.y);
  const target = items[0]?.node?.closest('button, [role="button"]') || items[0]?.node;
  if (!target) return false;
  target.click();
  return true;
}
"""
    try:
        frame.evaluate(script)
    except Exception:
        pass


def _safe_page_text(page: Any) -> str:
    try:
        return page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""


def _looks_ai_response_done(text: str) -> bool:
    busy_words = ["正在生成", "思考中", "生成中", "加载中", "停止生成"]
    return not any(word in text for word in busy_words)


def _detail_payload(page: Any) -> dict[str, Any]:
    script = """
() => {
  const selectors = [
    '[class*="subtitle" i]',
    '[class*="caption" i]',
    '[class*="transcript" i]',
    '[data-e2e*="subtitle" i]',
    '[data-e2e*="caption" i]'
  ];
  const captionTexts = [];
  for (const selector of selectors) {
    for (const el of Array.from(document.querySelectorAll(selector))) {
      const text = (el.innerText || el.textContent || '').trim();
      if (text) captionTexts.push(text);
    }
  }
  const tracks = Array.from(document.querySelectorAll('track[src]')).map(track => {
    try { return new URL(track.getAttribute('src'), location.href).href; } catch (e) { return ''; }
  }).filter(Boolean);
  const videoUrls = Array.from(document.querySelectorAll('video')).flatMap(video => {
    const urls = [video.currentSrc || '', video.src || ''];
    for (const source of Array.from(video.querySelectorAll('source[src]'))) {
      try { urls.push(new URL(source.getAttribute('src'), location.href).href); } catch (e) {}
    }
    return urls;
  }).filter(Boolean);
  const scriptTexts = Array.from(document.scripts)
    .map(script => script.textContent || '')
    .filter(text => /caption|subtitle|subTitle|aweme|play_addr|video/i.test(text))
    .slice(0, 30)
    .join('\\n')
    .slice(0, 900000);
  return {
    url: location.href,
    body_text: (document.body?.innerText || '').slice(0, 120000),
    caption_texts: captionTexts.slice(0, 80),
    track_urls: tracks,
    video_urls: Array.from(new Set(videoUrls)).slice(0, 20),
    script_text: scriptTexts
  };
}
"""
    try:
        return page.evaluate(script)
    except Exception:
        return {}


def _caption_text_from_payload(payload: dict[str, Any]) -> str:
    direct = _clean_transcript_lines(payload.get("caption_texts", []))
    if len(direct) >= 80:
        return direct

    script_text = str(payload.get("script_text") or "")
    candidates: list[str] = []
    for pattern in (
        r'"(?:text|caption_text|sub_text|content)"\s*:\s*"([^"]{6,260})"',
        r'\\?"(?:text|caption_text|sub_text|content)\\?"\s*:\s*\\?"([^"\\]{6,260})',
    ):
        for match in re.finditer(pattern, script_text):
            candidates.append(_decode_js_string(match.group(1)))
    script_caption = _clean_transcript_lines(candidates)
    if len(script_caption) >= 100:
        return script_caption

    return ""


def _chapter_points_from_text(text: str) -> str:
    if "章节要点" not in text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    try:
        start = lines.index("章节要点")
    except ValueError:
        return ""
    end_markers = {"举报", "发布时间：", "全部评论", "留下你的精彩评论吧"}
    out: list[str] = []
    for line in lines[start + 1 :]:
        if line in end_markers or any(line.startswith(marker) for marker in end_markers):
            break
        if line in {"第29集", "推荐视频"}:
            break
        out.append(line)
    cleaned = _clean_transcript_lines(out)
    if len(cleaned) < 80:
        return ""
    return "【抖音章节要点，非逐字稿】\n" + cleaned


def _download_media(context: Any, url: str, media_dir: Path) -> Path:
    media_dir.mkdir(parents=True, exist_ok=True)
    target = media_dir / (re.sub(r"[^0-9A-Za-z_.-]+", "_", url.split("?")[0])[-80:] or "douyin_media.mp4")
    if not target.suffix:
        target = target.with_suffix(".mp4")
    response = context.request.get(url, timeout=180000)
    if not response.ok:
        raise RuntimeError(f"media_download_failed:{response.status}")
    target.write_bytes(response.body())
    return target


def _transcribe_media(path: Path, *, whisper_model: str) -> str:
    from faster_whisper import WhisperModel

    model = WhisperModel(whisper_model, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(path), language="zh", vad_filter=True, beam_size=5)
    lines = []
    for segment in segments:
        text = segment.text.strip()
        if text:
            lines.append(text)
    return _clean_transcript_lines(lines)


def _write_transcript_to_card(
    path: Path,
    *,
    transcript: str,
    source: str,
    detail_url: str,
    status: str,
) -> None:
    text = path.read_text(encoding="utf-8-sig")
    text = _set_frontmatter_field(text, "原视频链接", detail_url)
    text = _set_frontmatter_field(text, "对白文本状态", status)
    text = _set_frontmatter_field(text, "对白文本来源", source)
    text = _set_frontmatter_field(text, "逐字稿已核验", "false")
    text = _replace_or_append_info_line(text, "原视频链接：", detail_url)
    text = _replace_or_append_info_line(text, "对白文本：", f"{status} / 来源：{source}")
    text = _replace_section(text, "对白文本", transcript)
    path.write_text(text, encoding="utf-8")


def _mark_transcript_status(path: Path, status: str, source: str, detail_url: str) -> None:
    text = path.read_text(encoding="utf-8-sig")
    if detail_url:
        text = _set_frontmatter_field(text, "原视频链接", detail_url)
        text = _replace_or_append_info_line(text, "原视频链接：", detail_url)
    text = _set_frontmatter_field(text, "对白文本状态", status)
    text = _set_frontmatter_field(text, "对白文本来源", source)
    text = _replace_or_append_info_line(text, "对白文本：", f"{status} / 来源：{source}")
    if "## 对白文本" not in text:
        text = text.rstrip() + "\n\n## 对白文本\n\n暂无\n"
    path.write_text(text, encoding="utf-8")


def _write_transcript_queue(root: Path, date: str, candidates: list[TranscriptCandidate]) -> None:
    path = root / "90_索引" / "逐字稿采集队列.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 逐字稿采集队列",
        "",
        "高赞高评只是排序依据；真正进入方法抽取前，先补原视频对白文本。",
        "",
        f"日期：{date}",
        "",
        "|排序|互动|标题|作者|搜索词|卡片|",
        "|---:|---:|---|---|---|---|",
    ]
    for index, item in enumerate(candidates[:120], start=1):
        rel = item.path.relative_to(root).as_posix().replace(".md", "")
        lines.append(
            f"|{index}|{item.interaction_count}|{item.title}|{item.author}|{item.search_query}|[[{rel}|打开]]|"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _has_transcript(text: str) -> bool:
    transcript = _section(text, "对白文本")
    return bool(transcript and transcript.strip() not in {"暂无", "待补充"})


def _frontmatter_value(text: str, key: str) -> str:
    match = re.search(rf"(?m)^{re.escape(key)}:\s*(.*?)\s*$", text)
    return match.group(1).strip().strip('"') if match else ""


def _set_frontmatter_field(text: str, key: str, value: str) -> str:
    line = f"{key}: {value}"
    if re.search(rf"(?m)^{re.escape(key)}:", text):
        return re.sub(rf"(?m)^{re.escape(key)}:.*$", line, text, count=1)
    if text.startswith("---"):
        return text.replace("---\n", "---\n" + line + "\n", 1)
    return "---\n" + line + "\n---\n\n" + text


def _replace_or_append_info_line(text: str, prefix: str, value: str) -> str:
    line = prefix + value
    if re.search(rf"(?m)^{re.escape(prefix)}", text):
        return re.sub(rf"(?m)^{re.escape(prefix)}.*$", line, text, count=1)
    heading = re.search(r"(?m)^##\s+", text)
    if heading:
        return text[: heading.start()] + line + "\n" + text[heading.start() :]
    return text.rstrip() + "\n" + line + "\n"


def _replace_section(text: str, title: str, body: str) -> str:
    replacement = f"## {title}\n\n{body.strip() or '暂无'}\n"
    pattern = rf"(?ms)^##\s+{re.escape(title)}\s*\n.*?(?=^##\s+|\Z)"
    if re.search(pattern, text):
        return re.sub(pattern, replacement, text, count=1)
    return text.rstrip() + "\n\n" + replacement


def _section(text: str, title: str) -> str:
    match = re.search(rf"(?ms)^##\s+{re.escape(title)}\s*\n(.*?)(?=^##\s+|\Z)", text)
    return match.group(1).strip() if match else ""


def _heading(text: str) -> str:
    match = re.search(r"(?m)^#\s+(.+?)\s*$", text)
    return match.group(1).strip() if match else ""


def _is_detail_url(url: str) -> bool:
    return bool(re.search(r"(?:douyin\.com/(?:video|note)/|so\.douyin\.com/video/detail)", url or ""))


def _canonical_detail_url(url: str) -> str:
    if not url:
        return ""
    match = re.search(r"/(?:video|note)/(\d+)", url)
    if match:
        return f"https://www.douyin.com/video/{match.group(1)}"
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("aweme_id", "modal_id", "group_id", "search_result_id", "vid"):
        values = query.get(key) or []
        if values and re.fullmatch(r"\d{8,}", values[0]):
            return f"https://www.douyin.com/video/{values[0]}"
    return url


def _ai_detail_url(url: str) -> str:
    if not url:
        return ""
    match = re.search(r"/(?:video|note)/(\d+)", url)
    if match:
        return f"https://www.douyin.com/jingxuan?modal_id={match.group(1)}"
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("aweme_id", "modal_id", "group_id", "search_result_id"):
        values = query.get(key) or []
        if values and re.fullmatch(r"\d{8,}", values[0]):
            return f"https://www.douyin.com/jingxuan?modal_id={values[0]}"
    return url


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def _transcript_matches_expected(transcript: str, expected_context: str) -> bool:
    haystack = _normalize(transcript)
    if not haystack:
        return False
    raw_tokens = re.split(r"[\s,，/|｜#：:！!？?（）()《》“”\"'、\-]+", expected_context or "")
    stopwords = {
        "视频",
        "抖音",
        "股票",
        "股民",
        "股市",
        "财经",
        "技巧",
        "心法",
        "方法",
        "原则",
        "高手",
        "大佬",
        "短线",
        "交易",
        "复盘",
        "分享",
    }
    tokens = []
    for token in raw_tokens:
        key = _normalize(token)
        if len(key) < 2 or key in stopwords:
            continue
        if key not in tokens:
            tokens.append(key)
    if not tokens:
        return True
    return any(token in haystack for token in tokens)


def _to_int(value: str) -> int:
    try:
        return int(str(value).strip() or 0)
    except ValueError:
        return 0


def _decode_js_string(value: str) -> str:
    try:
        decoded = bytes(value, "utf-8").decode("unicode_escape")
    except Exception:
        decoded = value
    return html.unescape(decoded)


def _clean_transcript_lines(values: list[Any]) -> str:
    skip_words = {
        "关注",
        "点赞",
        "评论",
        "分享",
        "收藏",
        "登录",
        "抖音",
        "首页",
        "推荐",
        "相关搜索",
        "手机随时看更方便",
        "下载 APP",
        "用户服务协议",
        "隐私政策",
        "章节要点",
        "内容由AI生成",
        "理财有风险，投资需谨慎",
        "以上结果由问问AI及全网内容生成，请结合专业知识审阅判断",
        "大家都在搜",
        "全部评论",
        "留下你的精彩评论吧",
    }
    lines: list[str] = []
    seen: set[str] = set()
    for value in values:
        for raw_line in str(value).splitlines():
            clean = re.sub(r"\s+", " ", raw_line).strip(" /｜|，,")
            if len(clean) < 6 or len(clean) > 260:
                continue
            if not re.search(r"[\u4e00-\u9fff]", clean):
                continue
            if any(word == clean or clean.startswith(word + " ") for word in skip_words):
                continue
            if clean.startswith(("#", "合集：", "更新至第")):
                continue
            if clean.startswith("展开") and "条回复" in clean:
                continue
            if re.match(r"^\d+[天周月年前]+·", clean):
                continue
            if any(token in clean for token in ("京ICP备", "京公网安备", "许可证", "举报", "网络文化", "增值电信", "全网内容生成", "问问AI")):
                continue
            if re.match(r"^第\d+章[:：]", clean):
                continue
            if re.search(r"https?://|www\.|douyin\.com", clean, re.I):
                continue
            key = _normalize(clean)
            if key in seen:
                continue
            seen.add(key)
            lines.append(clean)
    return "\n".join(lines).strip()
