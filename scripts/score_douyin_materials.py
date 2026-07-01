from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "00_收件箱" / "抖音问AI"
OUT_CSV = ROOT / "40_策略搜索任务" / "问AI素材评分表.csv"
OUT_MD = ROOT / "40_策略搜索任务" / "问AI高价值素材优先队列.md"


THEMES = {
    "涨停后路径": ["涨停后", "封单", "一字板", "高开", "低开", "回封", "炸板", "跌停", "撬板", "次日"],
    "尾盘隔夜": ["尾盘", "两点半", "2点半", "14:30", "收盘", "隔夜", "日内新高"],
    "集合竞价开盘": ["集合竞价", "竞价", "9:15", "9:20", "9:25", "9:30", "开盘", "低开", "高开"],
    "量比换手": ["量比", "换手", "成交量", "成交额", "放量", "缩量", "量能"],
    "盘口订单流": ["委比", "内盘", "外盘", "大单", "买盘", "卖盘", "订单", "盘口", "封单"],
    "题材龙头": ["题材", "主线", "龙头", "前排", "后排", "补涨", "板块", "中军", "抱团"],
    "情绪周期": ["情绪", "冰点", "退潮", "修复", "分歧", "一致", "亏钱效应", "赚钱效应"],
    "风控仓位": ["仓位", "止损", "止盈", "清仓", "减仓", "空仓", "回撤", "停手", "纪律"],
    "产业预期差": ["产业", "周期", "涨价", "供需", "库存", "订单", "政策", "价格传导", "预期差", "业绩"],
    "龙虎榜席位": ["龙虎榜", "席位", "游资", "营业部", "净买入", "上榜", "欢乐海岸", "赵老哥"],
    "监管异动": ["监管", "异动", "黑屋", "停牌", "重点监控", "异常波动", "严重异常"],
    "技术形态": ["K线", "均线", "支撑", "压力", "孕线", "突破", "回踩", "形态", "MACD", "布林"],
    "长期价值": ["高股息", "分红", "估值", "基本面", "长期持有", "财报", "龙头公司"],
}

METHOD_TERMS = [
    "策略", "方法", "步骤", "筛选", "条件", "规则", "信号", "买点", "卖点", "入场", "离场",
    "确认", "突破", "回踩", "承接", "失守", "失效", "观察", "验证", "模型", "体系",
]

QUANT_TERMS = [
    "涨幅", "跌幅", "收益", "回撤", "胜率", "盈亏比", "分钟", "小时", "成交量", "成交额",
    "换手率", "量比", "市值", "封单", "开盘", "收盘", "VWAP", "均价", "前高", "分位",
    "库存", "开工率", "毛利率", "订单", "公告", "龙虎榜", "涨跌停",
]

RISK_TERMS = [
    "风险", "止损", "清仓", "减仓", "空仓", "走弱", "跌停", "炸板", "回落", "兑现",
    "失守", "失败", "谨慎", "警惕", "回撤", "亏损", "停手", "不封板", "低开",
]

BOILERPLATE_TERMS = [
    "下载抖音精选", "网络谣言曝光台", "网上有害信息举报", "京ICP备", "京公网安备",
    "用户服务协议", "隐私政策", "营业执照", "互联网新闻信息服务许可证",
]


@dataclass
class ScoreRow:
    rank: int
    score: float
    grade: str
    author: str
    title: str
    video_id: str
    source_url: str
    ai_chars: int
    method_density: int
    quant_density: int
    risk_density: int
    numeric_count: int
    main_theme: str
    theme_tags: str
    next_action: str
    relative_path: str


def count_terms(text: str, terms: list[str]) -> int:
    return sum(text.count(term) for term in terms)


def unique_theme_hits(text: str) -> dict[str, int]:
    hits = {}
    for theme, terms in THEMES.items():
        score = sum(text.count(term) for term in terms)
        if score:
            hits[theme] = score
    return hits


def frontmatter_value(text: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}:\s*(.+)$", text, flags=re.MULTILINE)
    return m.group(1).strip() if m else ""


def extract_askai(text: str) -> str:
    marker = "## 问AI生成内容"
    if marker in text:
        return text.split(marker, 1)[1].strip()
    return text.strip()


def extract_title(text: str, path: Path) -> str:
    for pattern in [r"视频《([^》]{2,80})》", r"《([^》]{2,80})》"]:
        m = re.search(pattern, text)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()

    stem = path.stem
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}_\d{6}_\d+_", "", stem)
    stem = re.sub(r"_?\d{16,20}$", "", stem)
    return stem.replace("_", " ").strip()


def extract_video_id(path: Path, source_url: str) -> str:
    m = re.search(r"/video/(\d+)", source_url)
    if m:
        return m.group(1)
    m = re.search(r"(\d{16,20})", path.stem)
    return m.group(1) if m else ""


def grade_for(score: float) -> tuple[str, str]:
    if score >= 75:
        return "A-立即精提", "用统一模板提取，并进入反证队列"
    if score >= 60:
        return "B-主题合并", "同主题聚合后提取"
    if score >= 45:
        return "C-保留佐证", "只作为证据或边界"
    return "D-低价值", "保留原料，不主动提炼"


def score_file(path: Path) -> ScoreRow:
    text = path.read_text(encoding="utf-8", errors="ignore")
    askai = extract_askai(text)
    clean = askai
    source_url = frontmatter_value(text, "source_url")
    author = path.parent.name
    title = extract_title(askai, path)
    video_id = extract_video_id(path, source_url)

    ai_chars = len(clean)
    method_density = count_terms(clean, METHOD_TERMS)
    quant_density = count_terms(clean, QUANT_TERMS)
    risk_density = count_terms(clean, RISK_TERMS)
    numeric_count = len(re.findall(r"\d+(?:\.\d+)?%?|\d+:\d+", clean))
    theme_hits = unique_theme_hits(clean)
    theme_tags = [k for k, _ in sorted(theme_hits.items(), key=lambda kv: (-kv[1], kv[0]))]
    main_theme = theme_tags[0] if theme_tags else "未识别"

    text_score = min(20.0, ai_chars / 140.0)
    method_score = min(24.0, method_density * 1.8)
    quant_score = min(24.0, quant_density * 1.5 + numeric_count * 0.45)
    risk_score = min(14.0, risk_density * 1.5)
    theme_score = min(12.0, len(theme_tags) * 2.0 + sum(theme_hits.values()) * 0.12)
    time_axis_score = 4.0 if re.search(r"\d{2}:\d{2}|9:\d{2}|14:\d{2}|两点半|下午", clean) else 0.0
    source_score = 2.0 if source_url else 0.0

    boilerplate_hits = count_terms(text, BOILERPLATE_TERMS)
    noise_penalty = 0.0
    if ai_chars < 300:
        noise_penalty += 18.0
    elif ai_chars < 700:
        noise_penalty += 8.0
    if main_theme == "未识别":
        noise_penalty += 10.0
    if boilerplate_hits > 30:
        noise_penalty += 4.0
    if "未核验逐字" in text:
        noise_penalty += 1.5

    score = max(0.0, min(100.0, text_score + method_score + quant_score + risk_score + theme_score + time_axis_score + source_score - noise_penalty))
    grade, next_action = grade_for(score)

    return ScoreRow(
        rank=0,
        score=round(score, 1),
        grade=grade,
        author=author,
        title=title,
        video_id=video_id,
        source_url=source_url,
        ai_chars=ai_chars,
        method_density=method_density,
        quant_density=quant_density,
        risk_density=risk_density,
        numeric_count=numeric_count,
        main_theme=main_theme,
        theme_tags=";".join(theme_tags[:5]),
        next_action=next_action,
        relative_path=str(path.relative_to(ROOT)),
    )


def write_csv(rows: list[ScoreRow]) -> None:
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(ScoreRow.__dataclass_fields__.keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)


def md_table(rows: list[ScoreRow], limit: int = 50) -> list[str]:
    lines = [
        "| 排名 | 分数 | 等级 | 作者 | 主题 | 标题 | 下一步 |",
        "| --- | ---: | --- | --- | --- | --- | --- |",
    ]
    for row in rows[:limit]:
        title = row.title.replace("|", "/")
        if len(title) > 34:
            title = title[:33] + "…"
        lines.append(f"| {row.rank} | {row.score:.1f} | {row.grade} | {row.author} | {row.main_theme} | {title} | {row.next_action} |")
    return lines


def write_markdown(rows: list[ScoreRow]) -> None:
    by_grade = Counter(row.grade for row in rows)
    by_author = defaultdict(Counter)
    by_theme = Counter(row.main_theme for row in rows)
    for row in rows:
        by_author[row.author][row.grade] += 1

    grade_lines = ["| 等级 | 数量 | 处理口径 |", "| --- | ---: | --- |"]
    grade_order = ["A-立即精提", "B-主题合并", "C-保留佐证", "D-低价值"]
    action_by_grade = {
        "A-立即精提": "逐条进入统一模板，并进入反证队列",
        "B-主题合并": "同主题合并后再提取，避免重复口号",
        "C-保留佐证": "只补充证据、边界或反例",
        "D-低价值": "不主动提炼，只保留原料",
    }
    for grade in grade_order:
        grade_lines.append(f"| {grade} | {by_grade.get(grade, 0)} | {action_by_grade[grade]} |")

    author_lines = ["| 作者 | A | B | C | D | 合计 |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for author in sorted(by_author):
        c = by_author[author]
        author_lines.append(
            f"| {author} | {c.get('A-立即精提', 0)} | {c.get('B-主题合并', 0)} | {c.get('C-保留佐证', 0)} | {c.get('D-低价值', 0)} | {sum(c.values())} |"
        )

    theme_lines = ["| 主题 | 数量 |", "| --- | ---: |"]
    for theme, count in by_theme.most_common(15):
        theme_lines.append(f"| {theme} | {count} |")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    content = [
        "---",
        "类型: 搜索队列",
        "状态: 生效",
        "创建时间: 2026-07-01",
        "来源: 全量抖音问AI素材机器评分",
        "---",
        "",
        "# 问AI高价值素材优先队列",
        "",
        "## 目的",
        "",
        "这张队列用于把全量问AI素材先分级，再决定哪些内容进入方法论提取、哪些只作为佐证、哪些暂不处理。它服务于后续 Codex 自动研究量化交易策略，不替代人工判断。",
        "",
        f"生成时间：{now}",
        f"覆盖素材：{len(rows)} 条。",
        "",
        "完整评分表：`40_策略搜索任务/问AI素材评分表.csv`。",
        "",
        "## 评分口径",
        "",
        "- 文本有效性：问AI生成内容越完整，分数越高；过短或噪声过多会扣分。",
        "- 方法密度：出现策略、规则、条件、确认、入场、离场、失效等词越多，分数越高。",
        "- 可量化程度：出现时间、比例、成交量、换手、量比、封单、公告、库存等字段，分数越高。",
        "- 风控边界：出现止损、清仓、减仓、走弱、回撤、跌停、失败等风险词，分数越高。",
        "- 主题覆盖：能归入涨停路径、尾盘、竞价、量比、题材、仓位、产业、龙虎榜、监管等主题，分数越高。",
        "- 原料状态：所有问AI内容仍按“未核验逐字”处理，高分只代表研究优先级，不代表结论可信。",
        "",
        "## 等级统计",
        "",
        *grade_lines,
        "",
        "## 作者分布",
        "",
        *author_lines,
        "",
        "## 主题分布",
        "",
        *theme_lines,
        "",
        "## Top 50 优先处理素材",
        "",
        *md_table(rows, 50),
        "",
        "## 使用规则",
        "",
        "- A 级素材：进入 [[10_目标与SOP/问AI方法论提取模板|问AI方法论提取模板]]，并同步进入 [[50_实验验证/策略假设反证队列|策略假设反证队列]]。",
        "- B 级素材：同主题聚合，避免为重复口号新建浅卡。",
        "- C 级素材：只补证据、边界或反例。",
        "- D 级素材：保留原料，不主动提炼。",
        "- 不允许只因为作者知名、故事刺激或收益夸张就提升等级。",
    ]
    OUT_MD.write_text("\n".join(content) + "\n", encoding="utf-8")


def main() -> None:
    rows = [score_file(path) for path in INBOX.glob("*/*.md")]
    rows.sort(key=lambda row: (-row.score, row.author, row.title))
    for idx, row in enumerate(rows, start=1):
        row.rank = idx
    write_csv(rows)
    write_markdown(rows)
    print(f"scored={len(rows)} csv={OUT_CSV} md={OUT_MD}")


if __name__ == "__main__":
    main()
