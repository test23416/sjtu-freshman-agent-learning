from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CALENDAR_PATH = Path("data/official/calendar.json")
OFFICIAL_DIR = Path("data/official")
RAW_DIR = Path("data/raw")
KNOWLEDGE_DIR = Path("data/knowledge")
CALENDAR_WORDS = [
    "校历",
    "放假",
    "寒假",
    "暑假",
    "开学",
    "考试周",
    "节假日",
    "假期",
    "国庆",
    "中秋",
    "元旦",
    "春节",
    "清明",
    "劳动节",
    "端午",
]
CALENDAR_DOCUMENT_WORDS = [
    "校历",
    "pdf",
    "PDF",
    "文件",
    "原版",
    "链接",
    "在哪里",
    "在哪",
    "给我",
    "发我",
    "打开",
    "下载",
    "查看",
]
CALENDAR_DETAIL_WORDS = [
    "几天",
    "什么时候",
    "哪天",
    "日期",
    "时间",
    "安排",
    "放几天",
    "开始",
    "结束",
    "多久",
]
OFFICIAL_NOTE = "校历来自服务器端维护的官方资料副本/链接，请以学校官网最新版本为准。"
CST = timezone(timedelta(hours=8), name="Asia/Shanghai")


def is_calendar_question(question: str) -> bool:
    return any(word in question for word in CALENDAR_WORDS)


def is_calendar_document_request(question: str) -> bool:
    if not is_calendar_question(question):
        return False

    if any(word in question for word in CALENDAR_DETAIL_WORDS):
        return False

    return "校历" in question and any(word in question for word in CALENDAR_DOCUMENT_WORDS)


def _current_academic_year(now: datetime) -> str:
    start = now.year if now.month >= 7 else now.year - 1
    return f"{start}-{start + 1}"


def _requested_academic_year(question: str, now: datetime) -> str | None:
    years = [int(item) for item in re.findall(r"20\d{2}", question)]

    for start, end in zip(years, years[1:]):
        if end == start + 1:
            return f"{start}-{end}"

    if len(years) == 1:
        return f"{years[0]}-{years[0] + 1}"

    relative_words = {
        "前年": -2,
        "上一年": -1,
        "上年": -1,
        "去年": -1,
        "今年": 0,
        "本年": 0,
        "当前": 0,
        "明年": 1,
        "下一年": 1,
        "下年": 1,
    }

    for word, offset in relative_words.items():
        if word in question:
            start = now.year + offset
            return f"{start}-{start + 1}"

    return None


def _requested_calendar_year(question: str, now: datetime) -> int:
    years = [int(item) for item in re.findall(r"20\d{2}", question)]
    if years:
        return years[0]

    relative_words = {
        "前年": -2,
        "上一年": -1,
        "上年": -1,
        "去年": -1,
        "今年": 0,
        "本年": 0,
        "当前": 0,
        "明年": 1,
        "下一年": 1,
        "下年": 1,
    }

    for word, offset in relative_words.items():
        if word in question:
            return now.year + offset

    return now.year


def _find_local_calendar_pdf(calendar_year: int) -> Path | None:
    candidates = [
        RAW_DIR / f"{calendar_year}_calendar.pdf",
        KNOWLEDGE_DIR / f"{calendar_year}_calendar.pdf",
    ]

    for path in candidates:
        if path.exists():
            return path

    return None


def _local_file_url(local_file: str | None) -> str | None:
    if not local_file:
        return None

    path = Path(local_file)
    try:
        path = path.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        return None

    if path.exists() and path.as_posix().startswith("data/raw/"):
        return _raw_file_url(path)

    return None


def _raw_file_url(path: Path) -> str:
    return "/" + path.as_posix().replace("\\", "/")


def _load_json_file(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        print("读取校历配置失败:", path.as_posix(), repr(error))
        return None

    if not isinstance(data, dict):
        print("校历配置格式不正确:", path.as_posix())
        return None

    return data


def _load_calendar_config() -> tuple[dict[str, Any], str | None]:
    records: list[dict[str, Any]] = []
    error: str | None = None

    if not CALENDAR_PATH.exists():
        print("校历配置不存在:", CALENDAR_PATH.as_posix())
        error = "当前暂未配置校历数据，建议先查看学校官网最新校历。"
        data: dict[str, Any] = {}
    else:
        data = _load_json_file(CALENDAR_PATH) or {}
        if not data:
            error = "当前暂未配置校历数据，建议先查看学校官网最新校历。"

    for path in sorted(OFFICIAL_DIR.glob("calendar_*.json")):
        item = _load_json_file(path)
        if item:
            records.append(item)

    existing_items = data.get("items")
    if isinstance(existing_items, list):
        records.extend(item for item in existing_items if isinstance(item, dict))

    if records:
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for item in records:
            key = item.get("school_year") or item.get("title") or str(id(item))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        data = {**data, "items": deduped}
        error = None

    return data, error


def _normalize_record(record: dict[str, Any], academic_year: str) -> dict[str, Any]:
    pdf_url = record.get("pdf_url") or record.get("calendar_url") or record.get("url")
    source_url = record.get("source_url") or "https://www.sjtu.edu.cn/"

    return {
        "title": record.get("title") or f"上海交通大学 {academic_year} 学年校历",
        "school_year": record.get("school_year") or academic_year,
        "academic_year": record.get("school_year") or academic_year,
        "semester": record.get("semester") or "",
        "pdf_url": pdf_url or source_url,
        "calendar_url": pdf_url or source_url,
        "local_file": record.get("local_file"),
        "source_url": source_url,
        "updated_at": record.get("updated_at"),
        "description": record.get("description") or OFFICIAL_NOTE,
        "auto_updated": False,
    }


def _select_calendar_record(config: dict[str, Any], academic_year: str) -> dict[str, Any]:
    items = config.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("school_year") == academic_year:
                return item

    fallbacks = config.get("fallbacks")
    if isinstance(fallbacks, dict):
        item = fallbacks.get(academic_year)
        if isinstance(item, dict):
            return {
                **item,
                "school_year": item.get("school_year") or academic_year,
                "pdf_url": item.get("pdf_url") or item.get("url") or item.get("image_url"),
            }

    return config


def get_calendar_card_data(question: str, now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now(CST)
    calendar_year = _requested_calendar_year(question, current)
    academic_year = _requested_academic_year(question, current) or _current_academic_year(current)
    config, error = _load_calendar_config()
    record = _select_calendar_record(config, academic_year) if config else {}
    data = _normalize_record(record, academic_year)
    configured_year = data.get("school_year")
    local_pdf = _find_local_calendar_pdf(calendar_year)
    configured_local_url = _local_file_url(data.get("local_file"))

    if configured_local_url:
        data["pdf_url"] = configured_local_url
        data["calendar_url"] = configured_local_url
        data["description"] = "校历 PDF 来自服务器本地维护的官方原版文件，请以学校官网最新版本为准。"
        error = None
    elif local_pdf:
        data["pdf_url"] = _raw_file_url(local_pdf)
        data["calendar_url"] = data["pdf_url"]
        data["local_file"] = local_pdf.as_posix()
        data["title"] = f"上海交通大学 {calendar_year} 年校历"
        data["description"] = "校历 PDF 来自服务器本地维护的官方原版文件，请以学校官网最新版本为准。"
        error = None

    if not data.get("pdf_url"):
        data["pdf_url"] = "https://www.sjtu.edu.cn/"
        data["calendar_url"] = data["pdf_url"]

    if configured_year and configured_year != academic_year and not error:
        error = f"本地配置未包含 {academic_year} 学年校历，当前返回 {configured_year} 学年配置。"

    data.update(
        {
            "current_year": current.year,
            "calendar_year": calendar_year,
            "requested_school_year": academic_year,
            "checked_at": current.isoformat(),
            "config_error": error,
            "offline_note": OFFICIAL_NOTE,
        }
    )
    return data


def run_calendar_tools(question: str) -> dict[str, list[dict[str, Any]]]:
    if not is_calendar_document_request(question):
        return {"tool_results": [], "cards": []}

    data = get_calendar_card_data(question)
    content_lines = [
        OFFICIAL_NOTE,
        f"当前年份：{data['current_year']}",
        f"目标学年：{data['requested_school_year']}",
        f"本地配置学年：{data.get('school_year') or '未配置'}",
        f"校历地址：{data['calendar_url']}",
        f"官网来源：{data['source_url']}",
    ]

    if data.get("config_error"):
        content_lines.append(f"配置提示：{data['config_error']}")

    return {
        "tool_results": [
            {
                "name": "calendar_tool",
                "content": "\n".join(content_lines),
            }
        ],
        "cards": [
            {
                "type": "calendar",
                "title": data["title"],
                "data": data,
            }
        ],
    }
