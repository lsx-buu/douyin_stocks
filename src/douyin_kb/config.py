from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LlmConfig:
    api_key_env: str
    base_url_env: str
    model_env: str
    default_base_url: str
    default_model: str


@dataclass(frozen=True)
class DouyinWebConfig:
    profile_dir: Path
    target_url: str
    scroll_rounds: int
    headless: bool
    channel: str
    account_queries: list[str]
    account_urls: list[str]
    content_keywords: list[str]


@dataclass(frozen=True)
class AppConfig:
    root: Path
    inbox_jsonl: Path
    state_file: Path
    llm: LlmConfig
    douyin_web: DouyinWebConfig


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        example = path.with_name("config.example.json")
        if example.exists():
            path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            raise FileNotFoundError(f"Missing config file: {path}")

    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8-sig"))
    root = (path.parent / data.get("vault_root", ".")).resolve()
    llm_data = data.get("llm", {})
    llm = LlmConfig(
        api_key_env=llm_data.get("api_key_env", "OPENAI_API_KEY"),
        base_url_env=llm_data.get("base_url_env", "OPENAI_BASE_URL"),
        model_env=llm_data.get("model_env", "OPENAI_MODEL"),
        default_base_url=llm_data.get("default_base_url", "https://api.openai.com/v1"),
        default_model=llm_data.get("default_model", "gpt-4.1-mini"),
    )
    douyin_data = data.get("douyin_web", {})
    douyin_web = DouyinWebConfig(
        profile_dir=(root / douyin_data.get("profile_dir", ".state/douyin-web-profile")).resolve(),
        target_url=douyin_data.get("target_url", "https://www.douyin.com/user/self?showTab=like"),
        scroll_rounds=int(douyin_data.get("scroll_rounds", 12)),
        headless=bool(douyin_data.get("headless", False)),
        channel=str(douyin_data.get("channel", "chrome")).strip(),
        account_queries=list(douyin_data.get("account_queries", _default_account_queries())),
        account_urls=list(douyin_data.get("account_urls", [])),
        content_keywords=list(douyin_data.get("content_keywords", _default_douyin_keywords())),
    )
    return AppConfig(
        root=root,
        inbox_jsonl=(root / data.get("inbox_jsonl", "00_收件箱/抖音视频待处理.jsonl")).resolve(),
        state_file=(root / data.get("state_file", ".state/processed.json")).resolve(),
        llm=llm,
        douyin_web=douyin_web,
    )


def _default_douyin_keywords() -> list[str]:
    return [
        "股票",
        "游资",
        "A股",
        "短线",
        "龙头",
        "题材",
        "情绪",
        "涨停",
        "打板",
        "低吸",
        "半路",
        "竞价",
        "盘口",
        "板块",
        "主线",
        "资金",
        "仓位",
        "风控",
    ]


def _default_account_queries() -> list[str]:
    return [
        "北京炒家",
        "涅槃重升",
        "退学炒股",
        "炒股养家",
        "作手新一",
    ]
