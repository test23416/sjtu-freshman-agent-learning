from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request

from app.integrations.feishu import handle_message_event, is_url_verification


router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/api/feishu/health")
def feishu_health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/api/feishu/events")
async def feishu_events(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        logger.exception("飞书事件请求体不是合法 JSON")
        return {}

    try:
        if is_url_verification(payload):
            return handle_message_event(payload)

        background_tasks.add_task(handle_message_event, payload)
        return {}
    except Exception:
        logger.exception("飞书事件处理失败")
        return {}
