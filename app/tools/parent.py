from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.schemas import StudentProfile


PARENT_CHECKLIST_PATH = Path("data/checklists/parent_checklist.json")
PARENT_WORDS = [
    "家长",
    "送孩子",
    "陪同",
    "报到",
    "注意什么",
    "准备什么",
    "接送",
    "住宿",
    "安全",
    "缴费",
    "防诈骗",
    "适应",
]


def is_parent_profile(profile: StudentProfile | None = None) -> bool:
    return bool(profile and profile.role == "parent")


def is_parent_question(question: str) -> bool:
    return any(word in question for word in PARENT_WORDS)


def _fallback_parent_checklist(error: str | None = None) -> dict[str, Any]:
    return {
        "title": "家长陪同报到清单",
        "description": "本地家长 checklist 暂不可用，请稍后由维护者检查配置文件。",
        "groups": [],
        "error": error,
    }


def load_parent_checklist() -> dict[str, Any]:
    if not PARENT_CHECKLIST_PATH.exists():
        return _fallback_parent_checklist(f"未找到 {PARENT_CHECKLIST_PATH.as_posix()}")

    try:
        with PARENT_CHECKLIST_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        return _fallback_parent_checklist(f"读取家长 checklist 失败：{error}")

    if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
        return _fallback_parent_checklist("家长 checklist JSON 格式不正确。")

    return data


def run_parent_tools(
    question: str,
    profile: StudentProfile | None = None,
) -> dict[str, list[dict[str, Any]]]:
    if not is_parent_profile(profile) or not is_parent_question(question):
        return {"tool_results": [], "cards": []}

    data = load_parent_checklist()

    return {
        "tool_results": [
            {
                "name": "parent_tool",
                "content": "已根据家长陪同报到清单生成建议，具体安排请以学校和学院最新通知为准。",
            }
        ],
        "cards": [
            {
                "type": "parent_checklist",
                "title": data.get("title") or "家长陪同报到清单",
                "data": data,
            }
        ],
    }
