from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.agent import chat_with_agent
from app.config import (
    FEISHU_APP_ID,
    FEISHU_APP_SECRET,
    FEISHU_OPEN_API_BASE_URL,
    FEISHU_VERIFICATION_TOKEN,
)
from app.schemas import ChatMessage, ChatRequest, DiningPreference, StudentProfile
from app.tools.checklist import load_checklist
from app.tools.parent import load_parent_checklist


logger = logging.getLogger(__name__)

MAX_HISTORY_MESSAGES = 12
FEISHU_STATE_PATH = Path("data/feishu/checklist_state.json")
FEISHU_SETTINGS_PATH = Path("data/feishu/user_settings.json")
FEISHU_DINING_PREFS_PATH = Path("data/feishu/dining_preferences.json")
FEISHU_FEEDBACK_PATH = Path("data/feishu/feedback.json")
MODEL_OPTIONS = {
    "deepseek-chat": "DeepSeek V4 Flash",
    "deepseek-reasoner": "DeepSeek Reasoner",
    "qwen3.6-27b": "Qwen3.6-27B",
    "minimax-m2.7": "MiniMax-M2.7",
}
THINKING_TEXT = "收到，我先帮你整理一下。"
MENU_PROMPTS = {
    "checklist": "给我一份新生报到清单",
    "freshman_checklist": "给我一份新生报到清单",
    "calendar": "校历在哪里",
    "dining": "推荐几个食堂",
    "route": "校园导航怎么用",
    "tour": "给我推荐一条参观校园的路线",
    "parent": "送孩子报到要准备什么",
    "parent_checklist": "送孩子报到要准备什么",
}
_history_by_chat: dict[str, list[ChatMessage]] = {}
_processed_event_ids: set[str] = set()
_tenant_token_cache: dict[str, Any] = {"token": "", "expires_at": 0.0}


def verify_event_token(payload: dict[str, Any]) -> bool:
    if not FEISHU_VERIFICATION_TOKEN:
        return True

    token = payload.get("token") or payload.get("header", {}).get("token")
    return token == FEISHU_VERIFICATION_TOKEN


def is_url_verification(payload: dict[str, Any]) -> bool:
    return payload.get("type") == "url_verification" and bool(payload.get("challenge"))


def is_card_action_callback(payload: dict[str, Any]) -> bool:
    return payload.get("header", {}).get("event_type") == "card.action.trigger" or bool(
        payload.get("action")
    )


def is_bot_menu_event(payload: dict[str, Any]) -> bool:
    return payload.get("header", {}).get("event_type") == "application.bot.menu_v6"


def parse_text_message(message: dict[str, Any]) -> str:
    if message.get("message_type") != "text":
        return ""

    try:
        content = json.loads(message.get("content") or "{}")
    except json.JSONDecodeError:
        logger.warning("飞书消息 content 不是合法 JSON: %s", message.get("content"))
        return ""

    text = str(content.get("text") or "").strip()

    # 群聊中 @ 机器人的文本会包含 mention key，发给 Agent 前先去掉。
    for mention in message.get("mentions") or []:
        key = mention.get("key")
        if key:
            text = text.replace(key, "")

    return re.sub(r"\s+", " ", text).strip()


def load_json_dict(path: Path, log_name: str) -> dict[str, Any]:
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        logger.exception("读取飞书%s失败，已使用空数据", log_name)
        return {}

    return data if isinstance(data, dict) else {}


def save_json_dict(path: Path, data: dict[str, Any], log_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
    except OSError:
        logger.exception("保存飞书%s失败", log_name)


def load_user_settings() -> dict[str, Any]:
    return load_json_dict(FEISHU_SETTINGS_PATH, "用户设置")


def save_user_settings(settings: dict[str, Any]) -> None:
    save_json_dict(FEISHU_SETTINGS_PATH, settings, "用户设置")


def get_user_settings(user_id: str | None) -> dict[str, Any]:
    settings = load_user_settings()
    value = settings.get(user_id or "anonymous", {})
    return value if isinstance(value, dict) else {}


def update_user_setting(user_id: str | None, field: str, value: str | None) -> dict[str, Any]:
    settings = load_user_settings()
    key = user_id or "anonymous"
    user_settings = settings.get(key, {})
    if not isinstance(user_settings, dict):
        user_settings = {}

    if value:
        user_settings[field] = value
    else:
        user_settings.pop(field, None)

    settings[key] = user_settings
    save_user_settings(settings)
    return user_settings


def infer_profile(text: str, user_id: str | None = None) -> StudentProfile:
    settings = get_user_settings(user_id)
    parent_words = ["家长", "送孩子", "陪同", "孩子", "接送"]
    role = settings.get("role") or "student"
    if any(word in text for word in parent_words):
        role = "parent"

    campus = settings.get("campus")
    if "闵行" in text:
        campus = "闵行校区"
    elif "徐汇" in text:
        campus = "徐汇校区"
    elif "张江" in text:
        campus = "张江校区"

    return StudentProfile(role=role, campus=campus)


def get_history(chat_id: str) -> list[ChatMessage]:
    return list(_history_by_chat.get(chat_id, []))


def save_turn(chat_id: str, user_text: str, answer: str) -> None:
    history = _history_by_chat.setdefault(chat_id, [])
    history.extend(
        [
            ChatMessage(role="user", content=user_text),
            ChatMessage(role="assistant", content=answer),
        ]
    )
    del history[:-MAX_HISTORY_MESSAGES]


def sender_id_from_event(event: dict[str, Any]) -> str:
    sender = event.get("sender") or {}
    sender_id = sender.get("sender_id") or {}
    return (
        sender_id.get("open_id")
        or sender_id.get("user_id")
        or sender_id.get("union_id")
        or event.get("operator", {}).get("open_id")
        or event.get("operator", {}).get("user_id")
        or "anonymous"
    )


def receive_target_from_event(event: dict[str, Any]) -> tuple[str, str]:
    sender = event.get("sender") or {}
    sender_id = sender.get("sender_id") or {}
    if sender_id.get("user_id"):
        return sender_id["user_id"], "user_id"
    if sender_id.get("open_id"):
        return sender_id["open_id"], "open_id"
    if sender_id.get("union_id"):
        return sender_id["union_id"], "union_id"

    operator = event.get("operator") or {}
    if operator.get("user_id"):
        return operator["user_id"], "user_id"
    if operator.get("open_id"):
        return operator["open_id"], "open_id"

    return "anonymous", "open_id"


def sender_id_types_from_event(event: dict[str, Any]) -> list[str]:
    sender = event.get("sender") or {}
    sender_id = sender.get("sender_id") or {}
    return [key for key in ["user_id", "open_id", "union_id"] if sender_id.get(key)]


def short_debug_id(value: Any) -> str:
    text = str(value or "")
    if len(text) <= 12:
        return text
    return f"{text[:6]}...{text[-4:]}"


def log_event_diagnostics(
    payload: dict[str, Any],
    event: dict[str, Any],
    message: dict[str, Any],
    fallback_receive_id: str,
    fallback_receive_id_type: str,
) -> None:
    header = payload.get("header") or {}
    sender = event.get("sender") or {}
    sender_id = sender.get("sender_id") or {}

    logger.warning(
        "飞书事件诊断: header_app_id=%s env_app_id=%s tenant_key=%s event_type=%s "
        "message_id=%s chat_id=%s chat_type=%s sender_id=%s "
        "fallback_receive_id_type=%s fallback_receive_id=%s",
        header.get("app_id") or "",
        FEISHU_APP_ID,
        header.get("tenant_key") or "",
        header.get("event_type") or "",
        message.get("message_id") or "",
        message.get("chat_id") or "",
        message.get("chat_type") or "",
        json.dumps(sender_id, ensure_ascii=False),
        fallback_receive_id_type,
        short_debug_id(fallback_receive_id),
    )

    header_app_id = header.get("app_id")
    if header_app_id and FEISHU_APP_ID and header_app_id != FEISHU_APP_ID:
        logger.error(
            "飞书事件 app_id 与服务器 FEISHU_APP_ID 不一致: header_app_id=%s env_app_id=%s",
            header_app_id,
            FEISHU_APP_ID,
        )


def shorten(text: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def load_feishu_state() -> dict[str, Any]:
    return load_json_dict(FEISHU_STATE_PATH, "checklist 状态")


def save_feishu_state(state: dict[str, Any]) -> None:
    save_json_dict(FEISHU_STATE_PATH, state, "checklist 状态")


def checklist_state_key(user_id: str | None, card_type: str) -> str:
    return f"{user_id or 'anonymous'}:{card_type}"


def checked_items_for(state_key: str | None) -> set[str]:
    if not state_key:
        return set()

    state = load_feishu_state()
    items = state.get(state_key, [])
    return {str(item) for item in items} if isinstance(items, list) else set()


def toggle_checklist_item(state_key: str, item_id: str) -> bool:
    state = load_feishu_state()
    checked = set(state.get(state_key, []))

    if item_id in checked:
        checked.remove(item_id)
        is_checked = False
    else:
        checked.add(item_id)
        is_checked = True

    state[state_key] = sorted(checked)
    save_feishu_state(state)
    return is_checked


def load_dining_preferences_state() -> dict[str, Any]:
    return load_json_dict(FEISHU_DINING_PREFS_PATH, "食堂偏好")


def save_dining_preferences_state(state: dict[str, Any]) -> None:
    save_json_dict(FEISHU_DINING_PREFS_PATH, state, "食堂偏好")


def get_dining_preferences(user_id: str | None) -> list[DiningPreference]:
    state = load_dining_preferences_state()
    raw_items = state.get(user_id or "anonymous", [])
    if not isinstance(raw_items, list):
        return []

    preferences: list[DiningPreference] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        try:
            preferences.append(DiningPreference(**raw_item))
        except Exception:
            logger.warning("跳过无效飞书食堂偏好: %s", raw_item)

    return preferences


def record_dining_preference(user_id: str | None, canteen_id: str | None, canteen_name: str) -> DiningPreference:
    state = load_dining_preferences_state()
    key = user_id or "anonymous"
    raw_items = state.get(key, [])
    items = raw_items if isinstance(raw_items, list) else []
    now = datetime.now().isoformat(timespec="seconds")

    for item in items:
        if not isinstance(item, dict):
            continue
        same_id = canteen_id and str(item.get("canteen_id")) == str(canteen_id)
        same_name = item.get("canteen_name") == canteen_name
        if same_id or same_name:
            item["count"] = int(item.get("count") or 0) + 1
            item["last_visited_at"] = now
            state[key] = items
            save_dining_preferences_state(state)
            return DiningPreference(**item)

    new_item = {
        "canteen_id": canteen_id,
        "canteen_name": canteen_name,
        "count": 1,
        "last_visited_at": now,
    }
    items.append(new_item)
    state[key] = items
    save_dining_preferences_state(state)
    return DiningPreference(**new_item)


def load_feedback_state() -> dict[str, Any]:
    return load_json_dict(FEISHU_FEEDBACK_PATH, "回答反馈")


def save_feedback_state(state: dict[str, Any]) -> None:
    save_json_dict(FEISHU_FEEDBACK_PATH, state, "回答反馈")


def record_answer_feedback(
    user_id: str | None,
    rating: str,
    question: str | None = None,
    answer_preview: str | None = None,
) -> None:
    state = load_feedback_state()
    items = state.get("items", [])
    if not isinstance(items, list):
        items = []

    items.append(
        {
            "user_id": user_id or "anonymous",
            "rating": rating,
            "question": shorten(question or "", 180),
            "answer_preview": shorten(answer_preview or "", 220),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
    )

    state["items"] = items[-500:]
    save_feedback_state(state)


def build_amap_search_url(name: str) -> str:
    return (
        "https://uri.amap.com/search"
        f"?keyword={quote('上海交通大学 ' + name)}"
        "&src=sjtu-freshman-agent"
        "&callnative=0"
    )


def build_amap_marker_url(name: str, lng: Any, lat: Any) -> str | None:
    if lng is None or lat is None:
        return None

    return (
        "https://uri.amap.com/marker"
        f"?position={lng},{lat}"
        f"&name={quote(name)}"
        "&src=sjtu-freshman-agent"
        "&coordinate=gaode"
        "&callnative=0"
    )


def card_to_text(card: Any) -> str:
    card_data = card.model_dump() if hasattr(card, "model_dump") else dict(card)
    card_type = card_data.get("type")
    title = card_data.get("title") or "结果卡片"
    data = card_data.get("data") or {}

    if card_type == "calendar":
        url = data.get("calendar_url") or data.get("pdf_url") or ""
        source_url = data.get("source_url") or ""
        lines = [f"【校历】{title}", shorten(data.get("description", ""))]
        if url:
            lines.append(f"打开校历：{url}")
        if source_url:
            lines.append(f"官网来源：{source_url}")
        return "\n".join(line for line in lines if line)

    if card_type == "route":
        origin = (data.get("from") or {}).get("name") or "当前位置/待确认"
        destination = (data.get("to") or {}).get("name") or "目的地"
        route = data.get("route") or {}
        nav_url = data.get("navigation_url") or ""
        lines = [f"【路线】{origin} → {destination}"]
        if route:
            lines.append(f"步行约 {route.get('distance')} 米，预计约 {round(int(route.get('duration') or 0) / 60)} 分钟。")
        if data.get("fallback_reason"):
            lines.append(data["fallback_reason"])
        if nav_url:
            lines.append(f"导航链接：{nav_url}")
        return "\n".join(lines)

    if card_type in {"dining", "food_recommendation"}:
        lines = [f"【食堂推荐】{title}"]
        for index, item in enumerate((data.get("recommendations") or [])[:3], start=1):
            canteen = item.get("canteen") or {}
            crowd = item.get("crowd") or {}
            reason = item.get("reason") or item.get("display_reason") or canteen.get("location_desc") or ""
            lines.append(
                f"{index}. {canteen.get('name', '食堂')}："
                f"{crowd.get('crowd_text', '暂无实时拥挤度')}；{shorten(reason, 80)}"
            )
        return "\n".join(lines)

    if card_type in {"checklist", "parent_checklist"}:
        lines = [f"【清单】{title}"]
        for group in (data.get("groups") or [])[:4]:
            lines.append(f"- {group.get('title', '分组')}")
            for item in (group.get("items") or [])[:4]:
                lines.append(f"  · {item.get('text', '')}")
        return "\n".join(lines)

    if card_type == "campus_tour":
        lines = [f"【参观路线】{title}"]
        if data.get("duration"):
            lines.append(f"预计用时：{data['duration']}")
        if data.get("description"):
            lines.append(shorten(data["description"]))
        stops = [
            stop.get("place_name") or stop.get("title")
            for stop in data.get("stops", [])
            if stop.get("place_name") or stop.get("title")
        ]
        if stops:
            lines.append("路线：" + " → ".join(stops))
        return "\n".join(lines)

    if card_type == "place":
        place = data.get("place") or {}
        lines = [f"【地点】{place.get('name') or title}", shorten(place.get("description", ""))]
        if data.get("map_url"):
            lines.append(f"地图链接：{data['map_url']}")
        return "\n".join(line for line in lines if line)

    return f"【{title}】{shorten(json.dumps(data, ensure_ascii=False), 300)}"


def format_feishu_answer(answer: str, cards: list[Any]) -> str:
    sections = [answer.strip()]
    card_sections = [card_to_text(card) for card in cards]
    if card_sections:
        sections.append("\n\n".join(card_sections))

    text = "\n\n".join(section for section in sections if section)
    return text[:3800] if len(text) > 3800 else text


def md_text(content: str) -> dict[str, str]:
    return {"tag": "lark_md", "content": content}


def plain_text(content: str) -> dict[str, str]:
    return {"tag": "plain_text", "content": content}


def div(content: str) -> dict[str, Any]:
    return {"tag": "div", "text": md_text(content)}


def button(
    text: str,
    url: str | None = None,
    button_type: str = "primary",
    value: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = {
        "tag": "button",
        "text": plain_text(text),
        "type": button_type,
    }

    if url:
        data["url"] = url
    if value is not None:
        data["value"] = value

    return data


def action(buttons: list[dict[str, Any]]) -> dict[str, Any]:
    return {"tag": "action", "actions": buttons}


def base_interactive_card(title: str, elements: list[dict[str, Any]], template: str = "blue") -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
            "title": plain_text(title[:60] or "交大新生助手"),
        },
        "elements": elements[:12],
    }


def build_checklist_interactive(
    card_type: str,
    title: str,
    data: dict[str, Any],
    state_key: str | None = None,
) -> dict[str, Any]:
    checked = checked_items_for(state_key)
    elements = []

    for group in (data.get("groups") or [])[:4]:
        items = [item for item in group.get("items", []) if item.get("id") and item.get("text")]
        lines = [f"**{group.get('title', '分组')}**"]
        buttons = []

        for index, item in enumerate(items[:5], start=1):
            item_id = str(item["id"])
            is_checked = item_id in checked
            mark = "☑" if is_checked else "☐"
            lines.append(f"{mark} {index}. {item.get('text', '')}")

            if state_key:
                buttons.append(
                    button(
                        f"{'取消' if is_checked else '完成'} {index}",
                        button_type="default" if is_checked else "primary",
                        value={
                            "action": "toggle_checklist",
                            "state_key": state_key,
                            "card_type": card_type,
                            "item_id": item_id,
                        },
                    )
                )

        elements.append(div("\n".join(lines)))
        if buttons:
            elements.append(action(buttons))

    template = "purple" if card_type == "parent_checklist" else "blue"
    return base_interactive_card(title, elements or [div("当前清单暂不可用。")], template)


def card_to_interactive(
    card: Any,
    state_key: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    card_data = card.model_dump() if hasattr(card, "model_dump") else dict(card)
    card_type = card_data.get("type")
    title = card_data.get("title") or "结果卡片"
    data = card_data.get("data") or {}

    if card_type == "calendar":
        url = data.get("calendar_url") or data.get("pdf_url") or ""
        source_url = data.get("source_url") or ""
        elements = [
            div(shorten(data.get("description", "校历来自服务器端维护资料。"), 260)),
            div(f"**学年**：{data.get('school_year') or data.get('academic_year') or '未配置'}\n**学期**：{data.get('semester') or '全年'}"),
        ]
        actions = []
        if url:
            actions.append(button("打开校历", url))
        if source_url:
            actions.append(button("查看来源", source_url, "default"))
        if actions:
            elements.append(action(actions))
        return base_interactive_card(title, elements, "blue")

    if card_type == "route":
        origin = (data.get("from") or {}).get("name") or "当前位置/待确认"
        destination = (data.get("to") or {}).get("name") or "目的地"
        route = data.get("route") or {}
        lines = [f"**{origin} → {destination}**"]
        if route:
            duration = round(int(route.get("duration") or 0) / 60)
            lines.append(f"步行约 **{route.get('distance')} 米**，预计 **{duration} 分钟**。")
        elif data.get("fallback_reason"):
            lines.append(data["fallback_reason"])
        elements = [div("\n".join(lines))]
        nav_url = data.get("navigation_url") or ""
        if nav_url:
            elements.append(action([button("打开导航", nav_url)]))
        return base_interactive_card("校园路线", elements, "wathet")

    if card_type in {"dining", "food_recommendation"}:
        recommendations = data.get("recommendations") or []
        elements = []
        if data.get("fallback_reason"):
            elements.append(div(f"_{data['fallback_reason']}，先按本地食堂信息推荐。_"))
        for index, item in enumerate(recommendations[:4], start=1):
            canteen = item.get("canteen") or {}
            crowd = item.get("crowd") or {}
            reason = item.get("reason") or item.get("display_reason") or canteen.get("location_desc") or ""
            lines = [
                f"**{index}. {canteen.get('name', '食堂')}**",
                f"拥挤度：{crowd.get('crowd_text', '暂未获取')}",
            ]
            if reason:
                lines.append(shorten(reason, 120))
            elements.append(div("\n".join(lines)))
            canteen_name = canteen.get("name", "食堂")
            canteen_id = str(canteen.get("id") or "")
            actions = [
                button("打开地图", canteen.get("map_url") or build_amap_search_url(canteen_name), "default"),
                button(
                    "记为偏好",
                    button_type="primary",
                    value={
                        "action": "record_dining_preference",
                        "user_id": user_id,
                        "canteen_id": canteen_id,
                        "canteen_name": canteen_name,
                    },
                ),
            ]
            elements.append(action(actions))
        return base_interactive_card(title or "食堂推荐", elements or [div("暂时没有可展示的食堂推荐。")], "green")

    if card_type == "dining_preference_record":
        canteen = data.get("canteen") or {}
        canteen_name = canteen.get("name") or "这个食堂"
        elements = [
            div(f"已准备记录 **{canteen_name}** 为你的用餐偏好。后续食堂推荐会参考这个记录。"),
            action(
                [
                    button(
                        "确认记录",
                        value={
                            "action": "record_dining_preference",
                            "user_id": user_id,
                            "canteen_id": str(canteen.get("id") or ""),
                            "canteen_name": canteen_name,
                        },
                    )
                ]
            ),
        ]
        return base_interactive_card(title or "已记录用餐偏好", elements, "green")

    if card_type in {"checklist", "parent_checklist"}:
        return build_checklist_interactive(card_type, title, data, state_key=state_key)

    if card_type == "campus_tour":
        stops = [
            stop.get("place_name") or stop.get("title")
            for stop in data.get("stops", [])
            if stop.get("place_name") or stop.get("title")
        ]
        elements = [
            div(shorten(data.get("description", ""), 260)),
            div(f"**预计用时**：{data.get('duration') or '待定'}"),
        ]
        if stops:
            elements.append(div("**路线**：\n" + " → ".join(stops)))
        tips = data.get("tips") or []
        if tips:
            elements.append(div("**小提醒**：\n" + "\n".join(f"- {tip}" for tip in tips[:3])))
        stop_buttons = []
        for index, stop in enumerate((data.get("stops") or [])[:3], start=1):
            stop_name = stop.get("place_name") or stop.get("title")
            if not stop_name:
                continue
            map_url = build_amap_marker_url(stop_name, stop.get("lng"), stop.get("lat"))
            stop_buttons.append(button(f"第{index}站地图", map_url or build_amap_search_url(stop_name), "default"))
        if stop_buttons:
            elements.append(action(stop_buttons))
        return base_interactive_card(title, elements, "turquoise")

    if card_type == "place":
        place = data.get("place") or {}
        elements = [
            div(shorten(place.get("description", "暂无地点说明。"), 260)),
            div(f"**校区**：{place.get('campus') or '未知'}\n**类型**：{place.get('category') or '地点'}"),
        ]
        if data.get("map_url"):
            elements.append(action([button("打开地图", data["map_url"])]))
        return base_interactive_card(place.get("name") or title, elements, "wathet")

    return None


def get_tenant_access_token() -> str:
    now = time.time()
    if _tenant_token_cache["token"] and _tenant_token_cache["expires_at"] > now + 60:
        return _tenant_token_cache["token"]

    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        raise RuntimeError("FEISHU_APP_ID 或 FEISHU_APP_SECRET 未配置")

    response = httpx.post(
        f"{FEISHU_OPEN_API_BASE_URL.rstrip('/')}/auth/v3/tenant_access_token/internal",
        json={
            "app_id": FEISHU_APP_ID,
            "app_secret": FEISHU_APP_SECRET,
        },
        timeout=8,
    )
    response.raise_for_status()
    data = response.json()

    if data.get("code") != 0:
        raise RuntimeError(f"获取飞书 tenant_access_token 失败: {data}")

    token = data["tenant_access_token"]
    _tenant_token_cache["token"] = token
    _tenant_token_cache["expires_at"] = now + int(data.get("expire", 7200))
    return token


def reply_message(message_id: str, msg_type: str, content: dict[str, Any]) -> None:
    token = get_tenant_access_token()
    response = httpx.post(
        f"{FEISHU_OPEN_API_BASE_URL.rstrip('/')}/im/v1/messages/{message_id}/reply",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json={
            "msg_type": msg_type,
            "content": json.dumps(content, ensure_ascii=False),
            "uuid": str(uuid.uuid4()),
        },
        timeout=12,
    )

    if response.status_code < 200 or response.status_code >= 300:
        logger.warning(
            "飞书 reply_message 失败: message_id=%s msg_type=%s status=%s body=%s",
            message_id,
            msg_type,
            response.status_code,
            response.text,
        )
        raise RuntimeError(
            f"飞书回复消息 HTTP {response.status_code}: {response.text}"
        )

    data = response.json()
    if data.get("code") != 0:
        logger.warning(
            "飞书 reply_message 返回异常: message_id=%s msg_type=%s data=%s",
            message_id,
            msg_type,
            data,
        )
        raise RuntimeError(f"飞书回复消息失败: {data}")


def send_message(
    receive_id: str,
    receive_id_type: str,
    msg_type: str,
    content: dict[str, Any],
) -> None:
    token = get_tenant_access_token()
    response = httpx.post(
        f"{FEISHU_OPEN_API_BASE_URL.rstrip('/')}/im/v1/messages",
        params={"receive_id_type": receive_id_type},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json={
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": json.dumps(content, ensure_ascii=False),
            "uuid": str(uuid.uuid4()),
        },
        timeout=12,
    )

    if response.status_code < 200 or response.status_code >= 300:
        logger.warning(
            "飞书 send_message 失败: receive_id_type=%s receive_id=%s msg_type=%s status=%s body=%s",
            receive_id_type,
            short_debug_id(receive_id),
            msg_type,
            response.status_code,
            response.text,
        )
        raise RuntimeError(
            f"飞书发送消息 HTTP {response.status_code}: {response.text}"
        )

    data = response.json()
    if data.get("code") != 0:
        logger.warning(
            "飞书 send_message 返回异常: receive_id_type=%s receive_id=%s msg_type=%s data=%s",
            receive_id_type,
            short_debug_id(receive_id),
            msg_type,
            data,
        )
        raise RuntimeError(f"飞书发送消息失败: {data}")


def reply_text(message_id: str, text: str) -> None:
    reply_message(message_id, "text", {"text": text})


def reply_interactive(message_id: str, card: dict[str, Any]) -> None:
    reply_message(message_id, "interactive", card)


def send_text(receive_id: str, text: str, receive_id_type: str = "open_id") -> None:
    send_message(receive_id, receive_id_type, "text", {"text": text})


def send_interactive(
    receive_id: str,
    card: dict[str, Any],
    receive_id_type: str = "open_id",
) -> None:
    send_message(receive_id, receive_id_type, "interactive", card)


def safe_reply_text(
    message_id: str,
    text: str,
    fallback_chat_id: str | None = None,
    fallback_receive_id: str | None = None,
    fallback_receive_id_type: str = "open_id",
) -> bool:
    try:
        reply_text(message_id, text)
        return True
    except Exception:
        logger.exception("飞书回复消息失败")
        if fallback_chat_id and safe_send_text(
            fallback_chat_id,
            text,
            receive_id_type="chat_id",
        ):
            return True
        if fallback_receive_id:
            return safe_send_text(
                fallback_receive_id,
                text,
                receive_id_type=fallback_receive_id_type,
            )
        return False


def safe_reply_interactive(
    message_id: str,
    card: dict[str, Any],
    fallback_chat_id: str | None = None,
    fallback_receive_id: str | None = None,
    fallback_receive_id_type: str = "open_id",
) -> bool:
    try:
        reply_interactive(message_id, card)
        return True
    except Exception:
        logger.exception("飞书卡片回复失败")
        if fallback_chat_id and safe_send_interactive(
            fallback_chat_id,
            card,
            receive_id_type="chat_id",
        ):
            return True
        if fallback_receive_id:
            return safe_send_interactive(
                fallback_receive_id,
                card,
                receive_id_type=fallback_receive_id_type,
            )
        return False


def safe_send_text(receive_id: str, text: str, receive_id_type: str = "open_id") -> bool:
    try:
        send_text(receive_id, text, receive_id_type=receive_id_type)
        return True
    except Exception:
        logger.exception("飞书主动发送消息失败")
        return False


def safe_send_interactive(
    receive_id: str,
    card: dict[str, Any],
    receive_id_type: str = "open_id",
) -> bool:
    try:
        send_interactive(receive_id, card, receive_id_type=receive_id_type)
        return True
    except Exception:
        logger.exception("飞书主动发送卡片失败")
        return False


def build_feedback_card(question: str, answer: str) -> dict[str, Any]:
    return base_interactive_card(
        "这次回答有帮助吗？",
        [
            div("你的反馈会帮助我改进本地知识库和飞书端体验。"),
            action(
                [
                    button(
                        "有帮助",
                        value={
                            "action": "record_feedback",
                            "rating": "helpful",
                            "question": shorten(question, 180),
                            "answer_preview": shorten(answer, 220),
                        },
                    ),
                    button(
                        "不准确",
                        button_type="danger",
                        value={
                            "action": "record_feedback",
                            "rating": "inaccurate",
                            "question": shorten(question, 180),
                            "answer_preview": shorten(answer, 220),
                        },
                    ),
                    button(
                        "太笼统",
                        button_type="default",
                        value={
                            "action": "record_feedback",
                            "rating": "too_general",
                            "question": shorten(question, 180),
                            "answer_preview": shorten(answer, 220),
                        },
                    ),
                ]
            ),
        ],
        "grey",
    )


def reply_agent_response(
    message_id: str,
    answer: str,
    cards: list[Any],
    user_id: str | None = None,
    question: str | None = None,
    fallback_chat_id: str | None = None,
    fallback_receive_id: str | None = None,
    fallback_receive_id_type: str = "open_id",
) -> None:
    text = answer.strip() or "我整理好了，下面是结果。"
    reply_fallback_id = fallback_receive_id or user_id

    if not cards:
        safe_reply_text(
            message_id,
            text,
            fallback_chat_id=fallback_chat_id,
            fallback_receive_id=reply_fallback_id,
            fallback_receive_id_type=fallback_receive_id_type,
        )
        safe_reply_interactive(
            message_id,
            build_feedback_card(question or "", answer),
            fallback_chat_id=fallback_chat_id,
            fallback_receive_id=reply_fallback_id,
            fallback_receive_id_type=fallback_receive_id_type,
        )
        return

    safe_reply_text(
        message_id,
        text[:3000],
        fallback_chat_id=fallback_chat_id,
        fallback_receive_id=reply_fallback_id,
        fallback_receive_id_type=fallback_receive_id_type,
    )

    for card in cards[:3]:
        card_data = card.model_dump() if hasattr(card, "model_dump") else dict(card)
        card_type = card_data.get("type")
        state_key = (
            checklist_state_key(user_id, card_type)
            if card_type in {"checklist", "parent_checklist"}
            else None
        )
        interactive = card_to_interactive(card, state_key=state_key, user_id=user_id)
        if interactive and safe_reply_interactive(
            message_id,
            interactive,
            fallback_chat_id=fallback_chat_id,
            fallback_receive_id=reply_fallback_id,
            fallback_receive_id_type=fallback_receive_id_type,
        ):
            continue

        # 飞书卡片失败时退回文字版，保证用户仍能看到结构化结果。
        safe_reply_text(
            message_id,
            card_to_text(card)[:3000],
            fallback_chat_id=fallback_chat_id,
            fallback_receive_id=reply_fallback_id,
            fallback_receive_id_type=fallback_receive_id_type,
        )

    safe_reply_interactive(
        message_id,
        build_feedback_card(question or "", answer),
        fallback_chat_id=fallback_chat_id,
        fallback_receive_id=reply_fallback_id,
        fallback_receive_id_type=fallback_receive_id_type,
    )


def send_agent_response(
    receive_id: str,
    answer: str,
    cards: list[Any],
    user_id: str | None = None,
    question: str | None = None,
    receive_id_type: str = "open_id",
) -> None:
    text = answer.strip() or "我整理好了，下面是结果。"
    safe_send_text(receive_id, text[:3000], receive_id_type=receive_id_type)

    for card in cards[:3]:
        card_data = card.model_dump() if hasattr(card, "model_dump") else dict(card)
        card_type = card_data.get("type")
        state_key = (
            checklist_state_key(user_id, card_type)
            if card_type in {"checklist", "parent_checklist"}
            else None
        )
        interactive = card_to_interactive(card, state_key=state_key, user_id=user_id)
        if interactive and safe_send_interactive(receive_id, interactive, receive_id_type=receive_id_type):
            continue

        safe_send_text(receive_id, card_to_text(card)[:3000], receive_id_type=receive_id_type)

    safe_send_interactive(
        receive_id,
        build_feedback_card(question or "", answer),
        receive_id_type=receive_id_type,
    )


def extract_card_action_value(payload: dict[str, Any]) -> dict[str, Any]:
    action_data = payload.get("event", {}).get("action") or payload.get("action") or {}
    value = action_data.get("value") or {}
    return value if isinstance(value, dict) else {}


def checklist_data_for(card_type: str) -> dict[str, Any]:
    if card_type == "parent_checklist":
        return load_parent_checklist()
    return load_checklist()


def operator_id_from_payload(payload: dict[str, Any]) -> str:
    event = payload.get("event", {})
    operator = event.get("operator") or payload.get("operator") or {}
    operator_id = operator.get("operator_id") or {}
    if isinstance(operator_id, dict):
        return (
            operator_id.get("open_id")
            or operator_id.get("user_id")
            or operator_id.get("union_id")
            or "anonymous"
        )

    return (
        str(operator_id)
        if operator_id
        else operator.get("open_id") or operator.get("user_id") or "anonymous"
    )


def build_help_card() -> dict[str, Any]:
    elements = [
        div(
            "**可以直接这样问：**\n"
            "- 包图怎么走\n"
            "- 推荐几个食堂\n"
            "- 校历在哪里\n"
            "- 给我一份新生报到清单\n"
            "- 送孩子报到要准备什么\n"
            "- 给家长推荐一条参观校园的路线"
        ),
        div("也可以发送 **设置** 调整身份、校区和模型；发送 **清空对话** 清掉当前飞书会话上下文。"),
        action([button("打开设置", value={"action": "show_settings"})]),
    ]
    return base_interactive_card("交大新生助手使用帮助", elements, "blue")


def display_setting(settings: dict[str, Any], field: str, fallback: str = "默认") -> str:
    value = settings.get(field)
    if field == "role":
        return {"student": "新生", "parent": "家长"}.get(value, fallback)
    if field == "model":
        return MODEL_OPTIONS.get(value, value or fallback)
    return value or fallback


def build_settings_card(user_id: str | None) -> dict[str, Any]:
    settings = get_user_settings(user_id)
    elements = [
        div(
            "**当前设置**\n"
            f"- 身份：{display_setting(settings, 'role', '新生')}\n"
            f"- 校区：{display_setting(settings, 'campus', '未指定')}\n"
            f"- 模型：{display_setting(settings, 'model', '后端默认')}"
        ),
        div("**身份**"),
        action(
            [
                button("新生", button_type="primary" if settings.get("role") == "student" else "default", value={"action": "set_setting", "user_id": user_id, "field": "role", "value": "student"}),
                button("家长", button_type="primary" if settings.get("role") == "parent" else "default", value={"action": "set_setting", "user_id": user_id, "field": "role", "value": "parent"}),
            ]
        ),
        div("**校区**"),
        action(
            [
                button("闵行", button_type="primary" if settings.get("campus") == "闵行校区" else "default", value={"action": "set_setting", "user_id": user_id, "field": "campus", "value": "闵行校区"}),
                button("徐汇", button_type="primary" if settings.get("campus") == "徐汇校区" else "default", value={"action": "set_setting", "user_id": user_id, "field": "campus", "value": "徐汇校区"}),
                button("张江", button_type="primary" if settings.get("campus") == "张江校区" else "default", value={"action": "set_setting", "user_id": user_id, "field": "campus", "value": "张江校区"}),
                button("清除", button_type="danger", value={"action": "set_setting", "user_id": user_id, "field": "campus", "value": ""}),
            ]
        ),
        div("**模型**"),
        action(
            [
                button("DeepSeek", button_type="primary" if settings.get("model") == "deepseek-chat" else "default", value={"action": "set_setting", "user_id": user_id, "field": "model", "value": "deepseek-chat"}),
                button("Reasoner", button_type="primary" if settings.get("model") == "deepseek-reasoner" else "default", value={"action": "set_setting", "user_id": user_id, "field": "model", "value": "deepseek-reasoner"}),
                button("Qwen", button_type="primary" if settings.get("model") == "qwen3.6-27b" else "default", value={"action": "set_setting", "user_id": user_id, "field": "model", "value": "qwen3.6-27b"}),
                button("默认", button_type="danger", value={"action": "set_setting", "user_id": user_id, "field": "model", "value": ""}),
            ]
        ),
    ]
    return base_interactive_card("个人设置", elements, "purple" if settings.get("role") == "parent" else "blue")


def normalize_command(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def extract_menu_key(payload: dict[str, Any]) -> str:
    event = payload.get("event", {})
    raw_key = (
        event.get("event_key")
        or event.get("menu_key")
        or event.get("key")
        or event.get("action", {}).get("value", {}).get("event_key")
        or event.get("action", {}).get("event_key")
        or ""
    )
    key = str(raw_key).strip().lower().replace("-", "_")
    return key[5:] if key.startswith("menu_") else key


def receive_id_from_menu_event(payload: dict[str, Any]) -> str:
    event = payload.get("event", {})
    operator = event.get("operator") or {}
    operator_id = operator.get("operator_id") or {}
    if isinstance(operator_id, dict):
        return (
            operator_id.get("open_id")
            or operator_id.get("user_id")
            or operator_id.get("union_id")
            or "anonymous"
        )
    return str(operator_id or operator.get("open_id") or operator.get("user_id") or "anonymous")


def handle_bot_menu_event(payload: dict[str, Any]) -> dict[str, Any]:
    if not verify_event_token(payload):
        logger.warning("飞书机器人菜单事件 token 不匹配")
        return {}

    menu_key = extract_menu_key(payload)
    user_id = receive_id_from_menu_event(payload)
    history_key = f"menu:{user_id}"

    if menu_key in {"help", "guide"}:
        safe_send_interactive(user_id, build_help_card())
        return {}

    if menu_key in {"settings", "setting", "profile"}:
        safe_send_interactive(user_id, build_settings_card(user_id))
        return {}

    if menu_key in {"clear", "reset"}:
        _history_by_chat.pop(history_key, None)
        safe_send_text(user_id, "已清空你的飞书菜单会话上下文。")
        return {}

    prompt = MENU_PROMPTS.get(menu_key)
    if not prompt:
        safe_send_interactive(user_id, build_help_card())
        return {}

    try:
        safe_send_text(user_id, THINKING_TEXT)
        settings = get_user_settings(user_id)
        chat_response = chat_with_agent(
            ChatRequest(
                message=prompt,
                history=get_history(history_key),
                profile=infer_profile(prompt, user_id=user_id),
                dining_preferences=get_dining_preferences(user_id),
                model=settings.get("model"),
            )
        )
        send_agent_response(
            user_id,
            chat_response.answer,
            chat_response.cards,
            user_id=user_id,
            question=prompt,
        )
        save_turn(history_key, prompt, chat_response.answer)
    except Exception:
        logger.exception("处理飞书机器人菜单事件失败")
        safe_send_text(user_id, "服务暂时遇到问题，请稍后再试。")

    return {}


def handle_card_action(payload: dict[str, Any]) -> dict[str, Any]:
    if not verify_event_token(payload):
        logger.warning("飞书卡片回调 token 不匹配")
        return {}

    value = extract_card_action_value(payload)
    action_name = value.get("action")

    if action_name == "show_settings":
        user_id = value.get("user_id") or operator_id_from_payload(payload)
        return {
            "toast": {"type": "info", "content": "已打开设置"},
            "card": {"type": "raw", "data": build_settings_card(user_id)},
        }

    if action_name == "set_setting":
        user_id = value.get("user_id") or operator_id_from_payload(payload)
        field = str(value.get("field") or "")
        setting_value = str(value.get("value") or "")
        if field not in {"role", "campus", "model"}:
            return {"toast": {"type": "warning", "content": "这个设置项暂不支持。"}}
        if field == "model" and setting_value and setting_value not in MODEL_OPTIONS:
            return {"toast": {"type": "warning", "content": "这个模型暂不支持。"}}
        update_user_setting(user_id, field, setting_value or None)
        return {
            "toast": {"type": "success", "content": "设置已更新"},
            "card": {"type": "raw", "data": build_settings_card(user_id)},
        }

    if action_name == "record_dining_preference":
        user_id = value.get("user_id") or operator_id_from_payload(payload)
        canteen_name = str(value.get("canteen_name") or "这个食堂")
        canteen_id = str(value.get("canteen_id") or "") or None
        preference = record_dining_preference(user_id, canteen_id, canteen_name)
        return {
            "toast": {
                "type": "success",
                "content": f"已记录 {preference.canteen_name}，累计 {preference.count} 次",
            }
        }

    if action_name == "record_feedback":
        user_id = value.get("user_id") or operator_id_from_payload(payload)
        rating = str(value.get("rating") or "unknown")
        record_answer_feedback(
            user_id,
            rating,
            question=str(value.get("question") or ""),
            answer_preview=str(value.get("answer_preview") or ""),
        )
        return {
            "toast": {
                "type": "success",
                "content": "收到反馈，谢谢你帮我变得更好。",
            }
        }

    if action_name != "toggle_checklist":
        return {
            "toast": {
                "type": "info",
                "content": "这个操作暂时还没有接入。",
            }
        }

    card_type = value.get("card_type") or "checklist"
    item_id = str(value.get("item_id") or "")
    state_key = str(value.get("state_key") or "")

    if card_type not in {"checklist", "parent_checklist"} or not item_id or not state_key:
        return {
            "toast": {
                "type": "warning",
                "content": "清单操作参数不完整，请重新发送清单。",
            }
        }

    is_checked = toggle_checklist_item(state_key, item_id)
    data = checklist_data_for(card_type)
    title = data.get("title") or ("家长陪同报到清单" if card_type == "parent_checklist" else "新生入学准备清单")
    updated_card = build_checklist_interactive(card_type, title, data, state_key=state_key)

    return {
        "toast": {
            "type": "success",
            "content": "已标记完成" if is_checked else "已取消完成",
        },
        "card": {
            "type": "raw",
            "data": updated_card,
        },
    }


def handle_message_event(payload: dict[str, Any]) -> dict[str, Any]:
    if is_url_verification(payload):
        if not verify_event_token(payload):
            logger.warning("飞书 URL 校验 token 不匹配")
            return {}
        return {"challenge": payload["challenge"]}

    if is_bot_menu_event(payload):
        return handle_bot_menu_event(payload)

    if is_card_action_callback(payload):
        return handle_card_action(payload)

    if not verify_event_token(payload):
        logger.warning("飞书事件 token 不匹配")
        return {}

    event_type = payload.get("header", {}).get("event_type")
    if event_type != "im.message.receive_v1":
        return {}

    event_id = payload.get("header", {}).get("event_id")
    if event_id and event_id in _processed_event_ids:
        return {}
    if event_id:
        _processed_event_ids.add(event_id)
        if len(_processed_event_ids) > 1000:
            _processed_event_ids.clear()

    event = payload.get("event", {})
    message = event.get("message", {})
    message_id = message.get("message_id")
    chat_id = message.get("chat_id") or message_id or "default"
    user_id = sender_id_from_event(event)
    fallback_receive_id, fallback_receive_id_type = receive_target_from_event(event)
    text = parse_text_message(message)
    log_event_diagnostics(
        payload,
        event,
        message,
        fallback_receive_id,
        fallback_receive_id_type,
    )

    if not message_id:
        logger.warning("飞书事件缺少 message_id")
        return {}

    if not text:
        safe_reply_text(
            message_id,
            "目前我先支持文字消息。你可以直接问：包图怎么走、推荐食堂、校历在哪里、报到要带什么。",
            fallback_chat_id=chat_id,
            fallback_receive_id=fallback_receive_id,
            fallback_receive_id_type=fallback_receive_id_type,
        )
        return {}

    command = normalize_command(text)
    if command in {"/help", "help", "帮助", "使用帮助"}:
        if not safe_reply_interactive(
            message_id,
            build_help_card(),
            fallback_chat_id=chat_id,
            fallback_receive_id=fallback_receive_id,
            fallback_receive_id_type=fallback_receive_id_type,
        ):
            safe_reply_text(
                message_id,
                "可以问我：包图怎么走、推荐食堂、校历在哪里、给我一份新生报到清单。发送“设置”可以调整身份、校区和模型。",
                fallback_chat_id=chat_id,
                fallback_receive_id=fallback_receive_id,
                fallback_receive_id_type=fallback_receive_id_type,
            )
        return {}

    if command in {"/settings", "settings", "设置", "个人设置"}:
        if not safe_reply_interactive(
            message_id,
            build_settings_card(user_id),
            fallback_chat_id=chat_id,
            fallback_receive_id=fallback_receive_id,
            fallback_receive_id_type=fallback_receive_id_type,
        ):
            safe_reply_text(
                message_id,
                "设置卡片暂时发送失败。你仍然可以在问题里说明“我是家长”“闵行校区”等信息，我会按上下文处理。",
                fallback_chat_id=chat_id,
                fallback_receive_id=fallback_receive_id,
                fallback_receive_id_type=fallback_receive_id_type,
            )
        return {}

    if command in {"/clear", "clear", "清空", "清空对话", "清除上下文"}:
        _history_by_chat.pop(chat_id, None)
        safe_reply_text(
            message_id,
            "已清空当前飞书会话上下文。",
            fallback_chat_id=chat_id,
            fallback_receive_id=fallback_receive_id,
            fallback_receive_id_type=fallback_receive_id_type,
        )
        return {}

    try:
        safe_reply_text(
            message_id,
            THINKING_TEXT,
            fallback_chat_id=chat_id,
            fallback_receive_id=fallback_receive_id,
            fallback_receive_id_type=fallback_receive_id_type,
        )
        settings = get_user_settings(user_id)
        chat_response = chat_with_agent(
            ChatRequest(
                message=text,
                history=get_history(chat_id),
                profile=infer_profile(text, user_id=user_id),
                dining_preferences=get_dining_preferences(user_id),
                model=settings.get("model"),
            )
        )
        reply_agent_response(
            message_id,
            chat_response.answer,
            chat_response.cards,
            user_id=user_id,
            question=text,
            fallback_chat_id=chat_id,
            fallback_receive_id=fallback_receive_id,
            fallback_receive_id_type=fallback_receive_id_type,
        )
        save_turn(chat_id, text, chat_response.answer)
    except Exception:
        logger.exception("处理飞书消息失败")
        safe_reply_text(
            message_id,
            "服务暂时遇到问题，请稍后再试。如果问题持续，可以换一种问法。",
            fallback_chat_id=chat_id,
            fallback_receive_id=fallback_receive_id,
            fallback_receive_id_type=fallback_receive_id_type,
        )

    return {}
