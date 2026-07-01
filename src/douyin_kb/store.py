from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import VideoRecord


class KnowledgeStore:
    def __init__(self, root: Path, state_file: Path) -> None:
        self.root = root
        self.state_file = state_file
        self.state = self._load_state()
        self.video_dir = root / "20_视频卡片"
        self.review_dir = root / "40_每日复盘"
        self.index_path = root / "90_索引" / "抖音知识库索引.md"

    def is_processed(self, key: str) -> bool:
        return key in self.state.get("processed", {})

    def mark_processed(self, key: str, path: Path) -> None:
        self.state.setdefault("processed", {})[key] = str(path.relative_to(self.root))
        self._save_state()

    def write_video_card(self, record: VideoRecord, analysis: dict[str, Any]) -> Path:
        target_dir = self.video_dir / record.date
        target_dir.mkdir(parents=True, exist_ok=True)
        title = record.title or "未命名视频"
        filename = f"{record.date}_{_slugify(title)}_{record.key}.md"
        path = target_dir / filename

        tags = analysis.get("tags") or record.tags or ["抖音素材"]
        content = _video_card_markdown(record, analysis, tags)
        path.write_text(content, encoding="utf-8")
        return path

    def write_daily_review(
        self,
        date: str,
        created: list[dict[str, Any]],
        review: dict[str, Any],
    ) -> Path:
        self.review_dir.mkdir(parents=True, exist_ok=True)
        path = self.review_dir / f"{date}.md"
        path.write_text(_daily_review_markdown(date, created, review), encoding="utf-8")
        return path

    def append_hypothesis_pool(self, date: str, created: list[dict[str, Any]], review: dict[str, Any]) -> Path:
        path = self.root / "30_方法卡片" / "游资教学待验证思路池.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else _rule_pool_header()
        section = _rule_pool_section(date, created, review)
        pattern = rf"(?ms)^## {re.escape(date)}\n.*?(?=^## |\Z)"
        if re.search(pattern, existing):
            existing = re.sub(pattern, section, existing)
        elif section.strip() not in existing:
            existing = existing.rstrip() + "\n\n" + section
        path.write_text(existing.rstrip() + "\n", encoding="utf-8")
        return path

    def read_video_cards_for_date(self, date: str) -> list[dict[str, Any]]:
        target_dir = self.video_dir / date
        if not target_dir.exists():
            return []
        items: list[dict[str, Any]] = []
        for path in sorted(target_dir.rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            title = _extract_heading(text) or path.stem
            one_line = _extract_section(text, "一句话结论")
            url = _extract_prefixed_line(text, "来源链接：")
            author = _extract_prefixed_line(text, "作者：")
            transcript = _extract_section(text, "对白文本")
            tags = _extract_frontmatter_tags(text)
            items.append(
                {
                    "record": VideoRecord(url=url, title=title, author=author, tags=tags, transcript=transcript),
                    "analysis": {"one_line": one_line, "tags": tags},
                    "card_path": str(path.relative_to(self.root)),
                }
            )
        return items

    def write_index(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        processed: dict[str, str] = self.state.get("processed", {})
        lines = [
            "# 抖音知识库索引",
            "",
            f"已处理视频数：{len(processed)}",
            "",
            "## 视频卡片",
            "",
        ]
        for _, rel_path in sorted(processed.items(), key=lambda item: item[1], reverse=True):
            name = Path(rel_path).stem
            link = rel_path.replace("\\", "/").replace(".md", "")
            lines.append(f"- [[{link}|{name}]]")
        lines.append("")
        self.index_path.write_text("\n".join(lines), encoding="utf-8")

    def _load_state(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {"processed": {}}
        return json.loads(self.state_file.read_text(encoding="utf-8-sig"))

    def _save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")


def _video_card_markdown(record: VideoRecord, analysis: dict[str, Any], tags: list[str]) -> str:
    list_fields = {
        "key_concepts": "核心概念",
        "reasoning_chain": "推理链条",
        "boundaries": "适用边界与反例",
        "verification_questions": "复盘验证问题",
        "reusable_judgments": "可迁移判断",
        "actions": "后续学习动作",
    }
    lines = [
        "---",
        "类型: 抖音视频卡片",
        f"日期: {record.date}",
        f"作者: {record.author}",
        f"来源: {record.url}",
        f"来源层级: {record.source_level or '未标注'}",
        f"待核验: {str(record.needs_verification).lower()}",
        f"对白文本状态: {record.transcript_status or ('已获取' if record.transcript else '缺失')}",
        f"对白文本来源: {record.transcript_source or '无'}",
        f"逐字稿已核验: false",
        f"搜索词: {record.search_query}",
        f"点赞数: {record.like_count}",
        f"评论数: {record.comment_count}",
        f"收藏数: {record.favorite_count}",
        f"分享数: {record.share_count}",
        f"互动总数: {record.interaction_count}",
        "标签:",
    ]
    for tag in tags:
        lines.append(f"  - {tag}")
    lines.extend(
        [
            "---",
            "",
            f"# {record.title or '未命名视频'}",
            "",
            f"来源链接：{record.url}",
            f"作者：{record.author}",
            f"点赞时间：{record.liked_at}",
            f"搜索词：{record.search_query or '无'}",
            f"互动量：点赞 {record.like_count} / 评论 {record.comment_count} / 收藏 {record.favorite_count} / 分享 {record.share_count} / 总计 {record.interaction_count}",
            f"核验状态：{'待回看原视频核验' if record.needs_verification else '未标注'}",
            f"对白文本：{record.transcript_status or ('已获取' if record.transcript else '缺失')} / 来源：{record.transcript_source or '无'}",
            "",
            "## 一句话结论",
            "",
            str(analysis.get("one_line", "")).strip() or "待补充",
            "",
            "## 本视频回答的问题",
            "",
            str(analysis.get("core_question", "")).strip() or "待补充",
            "",
            "## 我为什么可能点赞",
            "",
            str(analysis.get("why_liked_inference", "")).strip() or "待确认",
            "",
            "## 教学层级",
            "",
            str(analysis.get("teaching_layer", analysis.get("knowledge_type", "待验证"))),
            "",
            "## 适用场景与前提",
            "",
            str(analysis.get("market_context", "未明确")),
            "",
            "## 从教学到复盘/行动的映射",
            "",
            str(analysis.get("application_mapping", "未明确")),
            "",
            "## AI 摘要",
            "",
            record.ai_summary or "暂无",
            "",
            "## 对白文本",
            "",
            record.transcript or "暂无",
            "",
        ]
    )

    for field, title in list_fields.items():
        lines.extend([f"## {title}", ""])
        values = analysis.get(field) or []
        if isinstance(values, str):
            values = [values]
        if values:
            lines.extend(f"- {value}" for value in values)
        else:
            lines.append("- 暂无")
        lines.append("")

    lines.extend(["## 原始文本", "", record.raw_text or "暂无", ""])
    return "\n".join(lines)


def _daily_review_markdown(date: str, created: list[dict[str, Any]], review: dict[str, Any]) -> str:
    lines = [
        "---",
        "类型: 每日复盘",
        f"日期: {date}",
        "标签:",
        "  - 抖音知识库",
        "  - 每日复盘",
        "---",
        "",
        f"# {date} 抖音知识复盘",
        "",
        "## 今日新增",
        "",
    ]

    for item in created:
        record: VideoRecord = item["record"]
        rel_path = item["card_path"].replace("\\", "/")
        one_line = item["analysis"].get("one_line", "")
        lines.append(f"- [[{rel_path.replace('.md', '')}|{record.title or '未命名视频'}]]：{one_line}")

    sections = [
        ("topics", "今天反复出现的主题"),
        ("changed_judgments", "真正改变判断的内容"),
        ("hypotheses_to_test", "待验证思路"),
        ("risk_boundaries", "共同适用边界"),
        ("actions", "明天可以执行或验证的学习动作"),
        ("discard_patterns", "可能只是爽感内容的模式"),
    ]
    for key, title in sections:
        lines.extend(["", f"## {title}", ""])
        values = review.get(key) or []
        if isinstance(values, str):
            values = [values]
        if values:
            lines.extend(f"- {value}" for value in values)
        else:
            lines.append("- 暂无")

    lines.extend(["", "## 明日关注", "", str(review.get("tomorrow_focus", "待补充")), ""])
    return "\n".join(lines)


def _rule_pool_header() -> str:
    return "\n".join(
        [
            "# 游资教学待验证思路池",
            "",
            "这里不保存买卖建议，只保存需要通过复盘和历史案例继续验证的交易假设、观察框架和方法草稿。",
            "",
            "每条思路至少要尽量回答：它解释什么问题、依赖什么前提、推理链是什么、如何观察验证、什么情况下失效。",
        ]
    )


def _rule_pool_section(date: str, created: list[dict[str, Any]], review: dict[str, Any]) -> str:
    lines = [f"## {date}", ""]
    hypotheses = review.get("hypotheses_to_test") or review.get("rules_to_test") or []
    if isinstance(hypotheses, str):
        hypotheses = [hypotheses]

    if hypotheses:
        lines.append("### 今日思路草稿")
        lines.append("")
        lines.extend(f"- {hypothesis}" for hypothesis in hypotheses)
        lines.append("")

    lines.append("### 来自视频卡片的验证问题")
    lines.append("")
    has_question = False
    for item in created:
        record: VideoRecord = item["record"]
        analysis = item["analysis"]
        questions = analysis.get("verification_questions") or []
        if isinstance(questions, str):
            questions = [questions]
        for question in questions:
            has_question = True
            lines.append(f"- {record.title or '未命名视频'}：{question}")
    if not has_question:
        lines.append("- 暂无")

    boundaries = review.get("risk_boundaries") or []
    if isinstance(boundaries, str):
        boundaries = [boundaries]
    lines.extend(["", "### 共同适用边界", ""])
    if boundaries:
        lines.extend(f"- {boundary}" for boundary in boundaries)
    else:
        lines.append("- 暂无")
    return "\n".join(lines)


def _slugify(value: str) -> str:
    clean = re.sub(r'[<>:"/\\|?*\n\r\t]+', "", value).strip()
    clean = re.sub(r"\s+", "-", clean)
    return clean[:50] or "video"


def _extract_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _extract_section(text: str, title: str) -> str:
    pattern = rf"(?ms)^## {re.escape(title)}\n\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, text)
    if not match:
        return ""
    return match.group(1).strip().splitlines()[0].strip("- ").strip()


def _extract_prefixed_line(text: str, prefix: str) -> str:
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return ""


def _extract_frontmatter_tags(text: str) -> list[str]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return []
    tags: list[str] = []
    in_tags = False
    for line in lines[1:]:
        if line == "---":
            break
        if line.startswith("标签:"):
            in_tags = True
            continue
        if in_tags:
            if line.startswith("  - "):
                tags.append(line[4:].strip())
            elif line and not line.startswith(" "):
                in_tags = False
    return tags
