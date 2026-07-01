from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from .config import LlmConfig
from .models import VideoRecord


def analyze_video(record: VideoRecord, config: LlmConfig) -> dict[str, Any]:
    prompt = f"""
你是一个帮助股票交易学习者把“游资教学视频”沉淀为第二大脑知识卡片的助手。
你的目标不是给出买卖建议，也不是只提炼买点卖点，而是把教学内容拆成可复盘、可验证、可迁移的交易认知。
请只输出 JSON，不要输出 Markdown。

视频信息：
标题：{record.title}
作者：{record.author}
链接：{record.url}
已有标签：{", ".join(record.tags)}
AI 摘要：{record.ai_summary}
对白文本来源：{record.transcript_source or "无"}
对白文本状态：{record.transcript_status or ("已获取" if record.transcript else "缺失")}
对白文本：{record.transcript}
原始文本：{record.raw_text}

请输出字段：
one_line: 一句话结论
core_question: 这个视频主要在回答什么问题
teaching_layer: 从 市场生态/资金行为/情绪周期/题材演化/个股地位/盘口语言/交易框架/执行细节/风控约束/案例复盘/交易心理/待验证/丢弃 中选一个
why_liked_inference: 推测用户为什么点赞
key_concepts: 2-6 个核心概念
reasoning_chain: 2-6 条推理链或因果链
market_context: 这个思路依赖的市场环境、情绪阶段、题材阶段或观察前提
application_mapping: 如何映射到观察、复盘、选股、交易计划或执行；如果视频涉及买卖点/仓位/风控可以写入这里，但不要强行补
boundaries: 2-5 条适用边界、反例、失效条件或不能套用的情况
verification_questions: 2-5 条复盘时要验证的问题
reusable_judgments: 2-5 条可迁移判断
actions: 0-3 条后续学习或复盘动作，不要输出具体股票买卖建议
tags: 2-6 个中文标签
""".strip()

    fallback = fallback_video_analysis(record)
    response = _chat_json(prompt, config)
    if not response:
        return fallback

    parsed = _parse_json_object(response)
    if not parsed:
        return fallback

    return {**fallback, **parsed}


def analyze_daily(records: list[dict[str, Any]], config: LlmConfig) -> dict[str, Any]:
    compact_records = []
    for item in records:
        record = item.get("record")
        compact_records.append(
            {
                "title": getattr(record, "title", ""),
                "author": getattr(record, "author", ""),
                "url": getattr(record, "url", ""),
                "analysis": item.get("analysis", {}),
                "card_path": item.get("card_path", ""),
            }
        )
    compact = json.dumps(compact_records, ensure_ascii=False, indent=2)
    prompt = f"""
你是用户的每日游资教学知识复盘助手。
你只做学习复盘、认知框架提炼和待验证假设整理，不给出具体股票买卖建议。
请根据今天新增的抖音知识卡，输出 JSON，不要输出 Markdown。

材料：
{compact}

请输出字段：
topics: 今天反复出现的主题，数组
changed_judgments: 真正改变判断的内容，数组
hypotheses_to_test: 值得进入待验证思路池的交易假设、观察框架或方法，数组
risk_boundaries: 今天材料共同暴露出的适用边界、失效条件或风险，数组
actions: 明天可以执行或验证的学习动作，数组，不要输出具体股票买卖建议
discard_patterns: 可能只是情绪爽感、幸存者偏差或暂不值得沉淀的模式，数组
tomorrow_focus: 明天学习关注方向，一句话
""".strip()

    fallback = fallback_daily_analysis(records)
    response = _chat_json(prompt, config)
    if not response:
        return fallback

    parsed = _parse_json_object(response)
    if not parsed:
        return fallback

    return {**fallback, **parsed}


def fallback_video_analysis(record: VideoRecord) -> dict[str, Any]:
    text = record.transcript or record.ai_summary or record.raw_text or record.title or "暂无摘要"
    one_line = _first_sentence(text)
    tags = record.tags or ["抖音素材"]
    entry_logic = _extract_keyword_clause(text, ["买点", "介入", "上车", "低吸", "打板", "半路"])
    exit_logic = _extract_keyword_clause(text, ["卖点", "退出", "止盈", "减仓", "清仓"])
    position_risk = _extract_keyword_clause(text, ["仓位", "风控", "止损", "回撤", "风险"])
    execution_parts = [part for part in [entry_logic, exit_logic, position_risk] if part]
    return {
        "one_line": one_line,
        "core_question": "这个视频试图解释什么交易现象、市场结构或决策问题？",
        "teaching_layer": "待验证",
        "why_liked_inference": "需要人工确认：该视频可能触发了一个值得复盘的交易认知或模式线索。",
        "key_concepts": tags,
        "reasoning_chain": [one_line],
        "market_context": "未明确",
        "application_mapping": "；".join(execution_parts) if execution_parts else "未明确",
        "boundaries": ["未配置模型接口，需人工补充适用边界和反例。"],
        "verification_questions": ["这个观点在什么市场环境下成立？", "视频里的案例是否存在幸存者偏差？"],
        "reusable_judgments": [one_line],
        "actions": [],
        "tags": tags,
    }


def fallback_daily_analysis(records: list[dict[str, Any]]) -> dict[str, Any]:
    tags: list[str] = []
    for item in records:
        tags.extend(item.get("analysis", {}).get("tags", []))
    seen = []
    for tag in tags:
        if tag not in seen:
            seen.append(tag)

    return {
        "topics": seen[:5] or ["暂无明显主题"],
        "changed_judgments": [],
        "hypotheses_to_test": [],
        "risk_boundaries": ["未配置模型接口，今日复盘只做索引汇总。"],
        "actions": [],
        "discard_patterns": ["未配置模型接口，今日复盘只做索引汇总。"],
        "tomorrow_focus": "优先复核今天新增卡片里最能转成行动的一条。",
    }


def _chat_json(prompt: str, config: LlmConfig) -> str:
    api_key = os.environ.get(config.api_key_env, "").strip()
    if not api_key:
        return ""

    base_url = os.environ.get(config.base_url_env, config.default_base_url).rstrip("/")
    model = os.environ.get(config.model_env, config.default_model)
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你只输出可解析 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }

    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError):
        return ""

    try:
        data = json.loads(body)
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return ""


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return None

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    return data if isinstance(data, dict) else None


def _first_sentence(text: str) -> str:
    clean = " ".join(text.split())
    if not clean:
        return "暂无摘要"
    parts = re.split(r"[。！？.!?]", clean, maxsplit=1)
    return parts[0][:120]


def _extract_keyword_clause(text: str, keywords: list[str]) -> str:
    clean = " ".join(text.split())
    if not clean:
        return ""
    clauses = re.split(r"[。！？.!?；;]", clean)
    for clause in clauses:
        if any(keyword in clause for keyword in keywords):
            return clause.strip()[:160]
    return ""
