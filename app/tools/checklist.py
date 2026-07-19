from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CHECKLIST_PATH = Path("data/checklists/freshman_checklist.json")
CHECKLIST_WORDS = [
    "清单",
    "准备什么",
    "要带什么",
    "入学准备",
    "报到准备",
    "报到当天",
    "开学前",
    "手续",
]


def is_checklist_question(question: str) -> bool:
    return any(word in question for word in CHECKLIST_WORDS)


def _fallback_checklist(error: str | None = None) -> dict[str, Any]:
    return {
        "title": "新生入学准备清单",
        "description": "本地 checklist 暂不可用，请稍后由维护者检查配置文件。",
        "groups": [],
        "error": error,
    }


def load_checklist() -> dict[str, Any]:
    if not CHECKLIST_PATH.exists():
        print("checklist 配置不存在:", CHECKLIST_PATH.as_posix())
        return _fallback_checklist(f"未找到 {CHECKLIST_PATH.as_posix()}")

    try:
        with CHECKLIST_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        print("读取 checklist 配置失败:", repr(error))
        return _fallback_checklist(f"读取 checklist 失败：{error}")

    if not isinstance(data, dict):
        return _fallback_checklist("checklist JSON 格式不正确，应为对象。")

    groups = data.get("groups")
    if not isinstance(groups, list):
        return _fallback_checklist("checklist 缺少 groups 数组。")

    normalized_groups = []
    for group in groups:
        if not isinstance(group, dict):
            continue

        items = []
        for item in group.get("items", []):
            if not isinstance(item, dict):
                continue

            text = str(item.get("text") or "").strip()
            item_id = str(item.get("id") or "").strip()
            if not text or not item_id:
                continue

            items.append(
                {
                    "id": item_id,
                    "text": text,
                    "priority": item.get("priority") or "medium",
                }
            )

        normalized_groups.append(
            {
                "title": group.get("title") or "未分组",
                "items": items,
            }
        )

    return {
        "title": data.get("title") or "新生入学准备清单",
        "description": data.get("description") or "清单来自服务器端维护的本地稳定资料。",
        "groups": normalized_groups,
        "updated_at": data.get("updated_at"),
        "source": data.get("source") or "data/checklists/freshman_checklist.json",
    }


def run_checklist_tools(question: str) -> dict[str, list[dict[str, Any]]]:
    if not is_checklist_question(question):
        return {"tool_results": [], "cards": []}

    data = load_checklist()
    item_count = sum(len(group.get("items", [])) for group in data.get("groups", []))
    content = (
        "新生 checklist 来自服务器端维护的本地稳定资料，不依赖外部网络。\n"
        f"分组数：{len(data.get('groups', []))}\n"
        f"事项数：{item_count}"
    )

    if data.get("error"):
        content += f"\n配置提示：{data['error']}"

    if data.get("error") or item_count == 0:
        return {
            "tool_results": [
                {
                    "name": "checklist_tool",
                    "content": content,
                }
            ],
            "cards": [],
        }

    return {
        "tool_results": [
            {
                "name": "checklist_tool",
                "content": content,
            }
        ],
        "cards": [
            {
                "type": "checklist",
                "title": data["title"],
                "data": data,
            }
        ],
    }
