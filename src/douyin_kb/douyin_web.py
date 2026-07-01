from __future__ import annotations

import time
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from .config import AppConfig
from .inbox import append_inbox_if_new


DOUYIN_VIEWPORT = {"width": 1280, "height": 860}
DOUYIN_WINDOW_SIZE = {"width": 1280, "height": 940}


def login_douyin(config: AppConfig, wait_seconds: int = 300) -> str:
    sync_playwright = _load_playwright()
    with sync_playwright() as playwright:
        context = _launch_context(playwright, config, headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(config.douyin_web.target_url, wait_until="domcontentloaded", timeout=60000)
        started = time.time()
        while time.time() - started < wait_seconds:
            if _has_login_cookie(context):
                context.close()
                return "login=detected"
            if not page.is_closed():
                page.wait_for_timeout(3000)
            else:
                context.close()
                return "login=browser_closed"
        context.close()
    return "login=timeout"


def scrape_douyin_likes(
    config: AppConfig,
    target_url: str | None = None,
    scroll_rounds: int | None = None,
    detail_pages: int = 0,
    manual_seconds: int = 0,
    keyword_filter: bool = True,
) -> str:
    sync_playwright = _load_playwright()
    url = target_url or config.douyin_web.target_url
    rounds = scroll_rounds if scroll_rounds is not None else config.douyin_web.scroll_rounds

    with sync_playwright() as playwright:
        context = _launch_context(playwright, config, headless=config.douyin_web.headless)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        if manual_seconds > 0:
            print(f"manual_navigation={manual_seconds}s", flush=True)
            page.wait_for_timeout(manual_seconds * 1000)

        if _looks_logged_out(page):
            context.close()
            return "scraped=0 reason=login_required"

        if manual_seconds <= 0 and target_url is None and "/user/" not in page.url:
            context.close()
            return "scraped=0 reason=target_page_required"

        items: dict[str, dict[str, Any]] = {}
        stale_rounds = 0
        for _ in range(max(rounds, 1)):
            before = len(items)
            for item in _extract_visible_video_items(page):
                items[item["url"]] = item
            page.mouse.wheel(0, 2400)
            page.wait_for_timeout(1800)
            if len(items) == before:
                stale_rounds += 1
            else:
                stale_rounds = 0
            if stale_rounds >= 3:
                break

        if detail_pages > 0:
            _enrich_from_detail_pages(context, list(items.values())[:detail_pages])

        appended = 0
        duplicates = 0
        filtered = 0
        now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        for item in items.values():
            if keyword_filter and not _matches_keywords(item, config.douyin_web.content_keywords):
                filtered += 1
                continue
            record = {
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "author": item.get("author", ""),
                "liked_at": now,
                "ai_summary": item.get("description", ""),
                "raw_text": item.get("raw_text", ""),
                "tags": ["游资教学", "抖音采集"],
                "source_id": item.get("source_id", item.get("url", "")),
            }
            if append_inbox_if_new(config.inbox_jsonl, record):
                appended += 1
            else:
                duplicates += 1

        context.close()
    return f"scraped={appended} duplicates={duplicates} filtered={filtered}"


def discover_douyin_accounts(
    config: AppConfig,
    queries: list[str] | None = None,
    per_query: int = 8,
    manual_seconds: int = 0,
) -> str:
    sync_playwright = _load_playwright()
    account_dir = config.root / "00_账号候选"
    account_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = account_dir / "抖音账号候选.jsonl"
    md_path = account_dir / "抖音账号候选.md"
    query_list = queries or config.douyin_web.account_queries

    all_candidates: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        context = _launch_context(playwright, config, headless=config.douyin_web.headless)
        page = context.pages[0] if context.pages else context.new_page()
        for query in query_list:
            _open_search_results(page, query)
            if manual_seconds > 0:
                print(f"manual_navigation={manual_seconds}s query={query}", flush=True)
                page.wait_for_timeout(manual_seconds * 1000)
            _click_user_tab(page)
            page.wait_for_timeout(3000)
            candidates = _extract_account_candidates(page, query)[:per_query]
            all_candidates.extend(candidates)
        context.close()

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for candidate in all_candidates:
        key = candidate.get("url") or candidate.get("source_id") or candidate.get("douyin_id") or candidate.get("title")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(candidate)

    for candidate in unique:
        append_inbox_if_new(jsonl_path, candidate)

    _write_account_candidates_md(md_path, unique)
    confirmed_path = account_dir / "已确认账号.txt"
    if not confirmed_path.exists():
        confirmed_path.write_text("# 每行放一个确认要订阅采集的抖音账号主页 URL\n", encoding="utf-8")
    return f"candidates={len(unique)}"


def scrape_douyin_accounts(
    config: AppConfig,
    account_urls: list[str] | None = None,
    scroll_rounds: int | None = None,
    detail_pages_per_account: int = 0,
    keyword_filter: bool = True,
) -> str:
    urls = account_urls or _load_confirmed_account_urls(config)
    if not urls:
        return "scraped=0 reason=no_confirmed_accounts"

    sync_playwright = _load_playwright()
    rounds = scroll_rounds if scroll_rounds is not None else config.douyin_web.scroll_rounds
    total = 0
    duplicates = 0
    filtered = 0
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    with sync_playwright() as playwright:
        context = _launch_context(playwright, config, headless=config.douyin_web.headless)
        page = context.pages[0] if context.pages else context.new_page()
        for account_url in urls:
            page.goto(account_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            if _looks_logged_out(page):
                continue

            items: dict[str, dict[str, Any]] = {}
            stale_rounds = 0
            for _ in range(max(rounds, 1)):
                before = len(items)
                for item in _extract_visible_video_items(page):
                    item["account_url"] = account_url
                    items[item["url"]] = item
                page.mouse.wheel(0, 2400)
                page.wait_for_timeout(1800)
                if len(items) == before:
                    stale_rounds += 1
                else:
                    stale_rounds = 0
                if stale_rounds >= 3:
                    break

            if detail_pages_per_account > 0:
                _enrich_from_detail_pages(context, list(items.values())[:detail_pages_per_account])

            for item in items.values():
                if keyword_filter and not _matches_keywords(item, config.douyin_web.content_keywords):
                    filtered += 1
                    continue
                record = {
                    "url": item.get("url", ""),
                    "title": item.get("title", ""),
                    "author": item.get("author", ""),
                    "liked_at": now,
                    "ai_summary": item.get("description", ""),
                    "raw_text": item.get("raw_text", ""),
                    "tags": ["游资教学", "账号采集"],
                    "source_id": item.get("source_id", item.get("url", "")),
                    "account_url": item.get("account_url", account_url),
                }
                if append_inbox_if_new(config.inbox_jsonl, record):
                    total += 1
                else:
                    duplicates += 1

        context.close()
    return f"scraped={total} duplicates={duplicates} filtered={filtered} accounts={len(urls)}"


def scrape_douyin_search_queries(
    config: AppConfig,
    queries: list[str] | None = None,
    per_query: int = 10,
    manual_seconds: int = 0,
    keyword_filter: bool = True,
    min_likes: int = 0,
    min_comments: int = 0,
    min_interactions: int = 0,
    scroll_rounds: int = 2,
) -> str:
    query_list = queries or config.douyin_web.account_queries
    if not query_list:
        return "scraped=0 reason=no_queries"

    sync_playwright = _load_playwright()
    total = 0
    duplicates = 0
    filtered = 0
    engagement_filtered = 0
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    with sync_playwright() as playwright:
        context = _launch_context(playwright, config, headless=config.douyin_web.headless)
        page = context.pages[0] if context.pages else context.new_page()
        for query in query_list:
            _open_search_results(page, query)
            if manual_seconds > 0:
                print(f"manual_navigation={manual_seconds}s query={query}", flush=True)
                page.wait_for_timeout(manual_seconds * 1000)
            page.wait_for_timeout(2000)
            items_by_id: dict[str, dict[str, Any]] = {}
            for _ in range(max(scroll_rounds, 1)):
                for item in _extract_search_video_items(page, query):
                    items_by_id[item.get("source_id", "")] = item
                page.mouse.wheel(0, 2200)
                page.wait_for_timeout(1600)
            items = sorted(
                items_by_id.values(),
                key=lambda item: (
                    int(item.get("comment_count", 0) or 0),
                    int(item.get("like_count", 0) or 0),
                    int(item.get("interaction_count", 0) or 0),
                ),
                reverse=True,
            )
            for item in items:
                if keyword_filter and not _matches_keywords(item, config.douyin_web.content_keywords):
                    filtered += 1
                    continue
                if not _meets_engagement_thresholds(
                    item,
                    min_likes=min_likes,
                    min_comments=min_comments,
                    min_interactions=min_interactions,
                ):
                    engagement_filtered += 1
                    continue
                record = {
                    "url": item.get("url", page.url),
                    "title": item.get("title", ""),
                    "author": item.get("author", ""),
                    "liked_at": now,
                    "ai_summary": item.get("description", ""),
                    "raw_text": item.get("raw_text", ""),
                    "tags": ["游资教学", "搜索采集", query],
                    "source_id": item.get("source_id", ""),
                    "search_query": query,
                    "like_count": item.get("like_count", 0),
                    "comment_count": item.get("comment_count", 0),
                    "favorite_count": item.get("favorite_count", 0),
                    "share_count": item.get("share_count", 0),
                    "interaction_count": item.get("interaction_count", 0),
                    "source_level": "搜索结果线索",
                    "needs_verification": True,
                }
                if append_inbox_if_new(config.inbox_jsonl, record):
                    total += 1
                else:
                    duplicates += 1
                if total >= per_query * len(query_list):
                    break
        context.close()
    return (
        f"scraped={total} duplicates={duplicates} filtered={filtered} "
        f"engagement_filtered={engagement_filtered} queries={len(query_list)}"
    )


def _load_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: pip install playwright"
        ) from exc
    return sync_playwright


def _open_search_results(page: Any, query: str) -> None:
    page.goto("https://so.douyin.com/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)
    try:
        page.fill('input[type="search"]', query, timeout=8000)
        page.keyboard.press("Enter")
    except Exception:
        page.goto(f"https://so.douyin.com/s?keyword={quote(query)}", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(6000)


def _click_user_tab(page: Any) -> None:
    selectors = [
        "text=用户",
        'a:has-text("用户")',
        'button:has-text("用户")',
        'div:has-text("用户")',
    ]
    for selector in selectors:
        try:
            page.locator(selector).first.click(timeout=3000)
            return
        except Exception:
            continue


def _launch_context(playwright: Any, config: AppConfig, headless: bool):
    profile_dir = config.douyin_web.profile_dir
    profile_dir.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {
        "user_data_dir": str(profile_dir),
        "headless": headless,
        "viewport": dict(DOUYIN_VIEWPORT),
        "screen": dict(DOUYIN_WINDOW_SIZE),
        "device_scale_factor": 1,
        "args": [f"--window-size={DOUYIN_WINDOW_SIZE['width']},{DOUYIN_WINDOW_SIZE['height']}"],
        "locale": "zh-CN",
    }
    if config.douyin_web.channel:
        kwargs["channel"] = config.douyin_web.channel
    try:
        return playwright.chromium.launch_persistent_context(**kwargs)
    except Exception:
        kwargs.pop("channel", None)
        return playwright.chromium.launch_persistent_context(**kwargs)


def _has_login_cookie(context: Any) -> bool:
    names = {cookie.get("name", "").lower() for cookie in context.cookies()}
    login_markers = {"sessionid", "sessionid_ss", "sid_guard", "uid_tt", "uid_tt_ss", "sid_tt"}
    return bool(names & login_markers)


def _looks_logged_out(page: Any) -> bool:
    text = _safe_inner_text(page)
    url = page.url.lower()
    if "login" in url:
        return True
    login_words = ["登录后", "扫码登录", "手机号登录", "验证码登录"]
    return any(word in text for word in login_words) and not _has_video_anchor(page)


def _has_video_anchor(page: Any) -> bool:
    return bool(page.locator('a[href*="/video/"], a[href*="/note/"]').count())


def _safe_inner_text(page: Any) -> str:
    try:
        return page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""


def _extract_visible_video_items(page: Any) -> list[dict[str, Any]]:
    script = """
() => {
  const anchors = Array.from(document.querySelectorAll('a[href*="/video/"], a[href*="/note/"]'));
  const out = [];
  const seen = new Set();
  const isCount = (value) => /^\\d+(\\.\\d+)?万?$/.test(value) || /^\\d+$/.test(value);
  for (const anchor of anchors) {
    let url;
    try {
      url = new URL(anchor.getAttribute('href'), location.href).href.split('?')[0];
    } catch (e) {
      continue;
    }
    if (seen.has(url)) continue;
    seen.add(url);
    const root = anchor.closest('[data-e2e="user-post-item"], li, article') || anchor;
    const imageAlt = anchor.querySelector('img')?.getAttribute('alt') || '';
    const text = (root.innerText || anchor.innerText || anchor.getAttribute('title') || imageAlt || '').trim();
    const lines = text.split('\\n').map(x => x.trim()).filter(Boolean);
    const titleCandidates = [
      anchor.getAttribute('title') || '',
      anchor.getAttribute('aria-label') || '',
      imageAlt,
      ...lines,
      document.title || '',
    ].map(x => x.trim()).filter(Boolean);
    const title = (titleCandidates.find(x => !isCount(x) && !x.includes('抖音网页版')) || titleCandidates[0] || '').trim();
    const authorLine = lines.find(x => x.startsWith('@')) || '';
    const idMatch = url.match(/\\/(video|note)\\/([^/?#]+)/);
    out.push({
      url,
      title: title.slice(0, 160),
      author: authorLine.replace(/^@/, '').slice(0, 80),
      description: lines.slice(0, 4).join(' / ').slice(0, 500),
      raw_text: text.slice(0, 1200),
      source_id: idMatch ? `douyin:${idMatch[1]}:${idMatch[2]}` : url,
    });
  }
  return out;
}
"""
    try:
        return page.evaluate(script)
    except Exception:
        return []


def _extract_account_candidates(page: Any, query: str) -> list[dict[str, Any]]:
    script = """
(query) => {
  const anchors = Array.from(document.querySelectorAll('a[href*="/user/"]'));
  const out = [];
  const seen = new Set();
  for (const anchor of anchors) {
    let url;
    try {
      url = new URL(anchor.getAttribute('href'), location.href).href.split('?')[0];
    } catch (e) {
      continue;
    }
    if (seen.has(url) || url.includes('/user/self')) continue;
    seen.add(url);
    const root = anchor.closest('li, article, [data-e2e], div') || anchor;
    const text = (root.innerText || anchor.innerText || anchor.getAttribute('title') || '').trim();
    const lines = text.split('\\n').map(x => x.trim()).filter(Boolean);
    const display = (anchor.getAttribute('title') || anchor.innerText || lines[0] || '').trim();
    out.push({
      query,
      url,
      title: display.slice(0, 120),
      raw_text: text.slice(0, 1000),
      source_id: `douyin_account:${url}`,
    });
  }
  return out;
}
"""
    try:
        candidates = page.evaluate(script, query)
    except Exception:
        candidates = []
    candidates.extend(_extract_account_candidates_from_text(page, query))
    return candidates


def _extract_account_candidates_from_text(page: Any, query: str) -> list[dict[str, Any]]:
    text = _safe_inner_text(page)
    if not text:
        return []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    candidates: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if "抖音号：" not in line:
            continue
        previous = lines[index - 2 : index + 1]
        nickname = previous[0] if previous else query
        followers = next((item for item in previous if "粉丝" in item), "")
        douyin_id = line.split("抖音号：", 1)[-1].strip()
        raw_text = " / ".join(lines[max(index - 4, 0) : index + 8])
        candidates.append(
            {
                "query": query,
                "url": "",
                "title": nickname,
                "douyin_id": douyin_id,
                "followers": followers,
                "raw_text": raw_text[:1000],
                "source_id": f"douyin_account:{query}:{douyin_id}",
            }
        )
    return candidates


def _extract_search_video_items(page: Any, query: str) -> list[dict[str, Any]]:
    text = _safe_inner_text(page)
    if not text:
        return []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    items: list[dict[str, Any]] = []
    duration_pattern = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")
    skip_titles = {"综合", "AI搜索", "视频", "直播", "用户", "登录账号", "相关搜索", "使用前必读"}
    for index, line in enumerate(lines):
        if not duration_pattern.match(line):
            continue
        if index == 0:
            continue
        title = lines[index - 1]
        if title in skip_titles or len(title) < 4:
            continue
        window = lines[index : index + 8]
        author = _guess_author_from_search_window(window)
        metrics = _parse_search_metrics(window)
        raw = " / ".join(lines[max(index - 1, 0) : index + 10])
        metric_key = ":".join(str(metrics.get(key, 0)) for key in ("like_count", "comment_count", "favorite_count", "share_count"))
        source_id = f"douyin_search:{query}:{title}:{line}:{author}:{metric_key}"
        items.append(
            {
                "url": page.url,
                "title": title[:160],
                "author": author,
                "description": raw[:800],
                "raw_text": raw[:1200],
                "source_id": source_id,
                "search_query": query,
                **metrics,
            }
        )
    return items


def _parse_search_metrics(lines: list[str]) -> dict[str, int]:
    date_pattern = re.compile(r"^(\d{1,2}月\d{1,2}日|20\d{2}年\d{1,2}月\d{1,2}日)$")
    date_index = next((index for index, line in enumerate(lines) if date_pattern.match(line)), len(lines))
    metric_lines = lines[1 : max(date_index - 1, 1)]
    counts: list[int] = []
    for line in metric_lines:
        parsed = _parse_count(line)
        if parsed is None:
            if line in {"评论", "分享", "收藏"}:
                counts.append(0)
            continue
        counts.append(parsed)
        if len(counts) >= 4:
            break
    while len(counts) < 4:
        counts.append(0)
    like_count, comment_count, favorite_count, share_count = counts[:4]
    return {
        "like_count": like_count,
        "comment_count": comment_count,
        "favorite_count": favorite_count,
        "share_count": share_count,
        "interaction_count": like_count + comment_count + favorite_count + share_count,
    }


def _parse_count(value: str) -> int | None:
    clean = value.strip().replace(",", "")
    match = re.match(r"^(\d+(?:\.\d+)?)(万)?$", clean)
    if not match:
        return None
    number = float(match.group(1))
    if match.group(2):
        number *= 10000
    return int(number)


def _meets_engagement_thresholds(
    item: dict[str, Any],
    *,
    min_likes: int = 0,
    min_comments: int = 0,
    min_interactions: int = 0,
) -> bool:
    return (
        int(item.get("like_count", 0) or 0) >= min_likes
        and int(item.get("comment_count", 0) or 0) >= min_comments
        and int(item.get("interaction_count", 0) or 0) >= min_interactions
    )


def _guess_author_from_search_window(lines: list[str]) -> str:
    date_pattern = re.compile(r"^(\d{1,2}月\d{1,2}日|20\d{2}年\d{1,2}月\d{1,2}日)$")
    for index, line in enumerate(lines):
        if date_pattern.match(line) and index > 0:
            return lines[index - 1][:80]
    for line in lines:
        if line.startswith("@"):
            return line[1:80]
    return ""


def _write_account_candidates_md(path, candidates: list[dict[str, Any]]) -> None:
    lines = [
        "# 抖音账号候选",
        "",
        "这里是搜索得到的账号候选，不代表账号身份真实。确认后，把账号主页 URL 复制到 `已确认账号.txt`，每行一个。",
        "",
    ]
    for candidate in candidates:
        title = candidate.get("title") or candidate.get("url")
        query = candidate.get("query", "")
        url = candidate.get("url", "")
        raw_text = candidate.get("raw_text", "").replace("\n", " / ")
        lines.extend(
            [
                f"## {title}",
                "",
                f"- 搜索词：{query}",
                f"- 主页：{url}",
                f"- 页面文本：{raw_text[:300]}",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _load_confirmed_account_urls(config: AppConfig) -> list[str]:
    urls = [url.strip() for url in config.douyin_web.account_urls if str(url).strip()]
    confirmed_path = config.root / "00_账号候选" / "已确认账号.txt"
    if confirmed_path.exists():
        for line in confirmed_path.read_text(encoding="utf-8-sig").splitlines():
            clean = line.strip()
            if not clean or clean.startswith("#"):
                continue
            urls.append(clean)
    seen: set[str] = set()
    unique: list[str] = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        unique.append(url)
    return unique


def _matches_keywords(item: dict[str, Any], keywords: list[str]) -> bool:
    if not keywords:
        return True
    text = " ".join(
        str(item.get(field, ""))
        for field in ("title", "author", "description", "raw_text")
    ).lower()
    return any(keyword.lower() in text for keyword in keywords)


def _enrich_from_detail_pages(context: Any, items: list[dict[str, Any]]) -> None:
    for item in items:
        page = context.new_page()
        try:
            page.goto(item["url"], wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
            text = _safe_inner_text(page)
            if text:
                item["raw_text"] = text[:2500]
                if not item.get("description"):
                    item["description"] = " / ".join(text.splitlines()[:6])[:800]
        except Exception:
            pass
        finally:
            page.close()
