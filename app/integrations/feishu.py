from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from app.agent import chat_with_agent
from app.config import (
    FEISHU_APP_ID,
    FEISHU_APP_SECRET,
    FEISHU_OPEN_API_BASE_URL,
    FEISHU_VERIFICATION_TOKEN,
)
from app.schemas import ChatMessage, ChatRequest, StudentProfile


logger = logging.getLogger(__name__)

MAX_HISTORY_MESSAGES = 12
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


def infer_profile(text: str) -> StudentProfile:
    parent_words = ["家长", "送孩子", "陪同", "孩子", "接送"]
    role = "parent" if any(word in text for word in parent_words) else "student"

    campus = None
    if "闵行" in text:
        campus = "闵行校区"
    elif "徐汇" in text:
        campus = "徐汇校区"

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


def shorten(text: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


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


def reply_text(message_id: str, text: str) -> None:
    token = get_tenant_access_token()
    response = httpx.post(
        f"{FEISHU_OPEN_API_BASE_URL.rstrip('/')}/im/v1/messages/{message_id}/reply",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json={
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        },
        timeout=12,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 0:
        raise RuntimeError(f"飞书回复消息失败: {data}")


def safe_reply_text(message_id: str, text: str) -> None:
    try:
        reply_text(message_id, text)
    except Exception:
        logger.exception("飞书回复消息失败")


def handle_message_event(payload: dict[str, Any]) -> dict[str, Any]:
    if is_url_verification(payload):
        if not verify_event_token(payload):
            logger.warning("飞书 URL 校验 token 不匹配")
            return {}
        return {"challenge": payload["challenge"]}

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

    message = payload.get("event", {}).get("message", {})
    message_id = message.get("message_id")
    chat_id = message.get("chat_id") or message_id or "default"
    text = parse_text_message(message)

    if not message_id:
        logger.warning("飞书事件缺少 message_id")
        return {}

    if not text:
        safe_reply_text(message_id, "目前我先支持文字消息。你可以直接问：包图怎么走、推荐食堂、校历在哪里、报到要带什么。")
        return {}

    try:
        chat_response = chat_with_agent(
            ChatRequest(
                message=text,
                history=get_history(chat_id),
                profile=infer_profile(text),
            )
        )
        reply = format_feishu_answer(chat_response.answer, chat_response.cards)
        safe_reply_text(message_id, reply)
        save_turn(chat_id, text, chat_response.answer)
    except Exception:
        logger.exception("处理飞书消息失败")
        safe_reply_text(message_id, "服务暂时遇到问题，请稍后再试。如果问题持续，可以换一种问法。")

    return {}
