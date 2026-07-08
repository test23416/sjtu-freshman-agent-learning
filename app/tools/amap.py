from __future__ import annotations

from typing import Any

import httpx

from app.config import AMAP_WEB_SERVICE_KEY


AMAP_PLACE_TEXT_URL = "https://restapi.amap.com/v5/place/text"
AMAP_WALKING_ROUTE_URL = "https://restapi.amap.com/v3/direction/walking"


def amap_available() -> bool:
    return bool(AMAP_WEB_SERVICE_KEY)


def search_place_by_keyword(keyword: str, city: str = "上海") -> dict[str, Any] | None:
    """
    用高德 POI 关键字搜索地点，返回最匹配的一个 POI。
    """
    if not amap_available():
        return None

    params = {
        "key": AMAP_WEB_SERVICE_KEY,
        "keywords": keyword,
        "region": city,
        "city_limit": "true",
        "page_size": 5,
    }

    try:
        response = httpx.get(AMAP_PLACE_TEXT_URL, params=params, timeout=8)
        response.raise_for_status()
        data = response.json()
    except Exception as error:
        print("高德地点搜索失败:", repr(error))
        return None

    if data.get("status") != "1":
        print("高德地点搜索返回异常:", data)
        return None

    pois = data.get("pois") or []
    if not pois:
        return None

    poi = pois[0]
    location = poi.get("location", "")

    if "," not in location:
        return None

    lng, lat = location.split(",", 1)

    return {
        "name": poi.get("name"),
        "address": poi.get("address"),
        "location": location,
        "lng": float(lng),
        "lat": float(lat),
        "adname": poi.get("adname"),
        "type": poi.get("type"),
        "id": poi.get("id"),
    }


def get_walking_route(
    origin_lng: float,
    origin_lat: float,
    destination_lng: float,
    destination_lat: float,
) -> dict[str, Any] | None:
    """
    调用高德步行路径规划 API。
    """
    if not amap_available():
        return None

    params = {
        "key": AMAP_WEB_SERVICE_KEY,
        "origin": f"{origin_lng},{origin_lat}",
        "destination": f"{destination_lng},{destination_lat}",
    }

    try:
        response = httpx.get(AMAP_WALKING_ROUTE_URL, params=params, timeout=8)
        response.raise_for_status()
        data = response.json()
    except Exception as error:
        print("高德步行路线规划失败:", repr(error))
        return None

    if data.get("status") != "1":
        print("高德步行路线规划返回异常:", data)
        return None

    route = data.get("route") or {}
    paths = route.get("paths") or []

    if not paths:
        return None

    path = paths[0]

    steps = []
    for step in path.get("steps", []):
        instruction = step.get("instruction")
        if instruction:
            steps.append(
                {
                    "instruction": instruction,
                    "distance": step.get("distance"),
                    "duration": step.get("duration"),
                    "polyline": step.get("polyline"),
                }
            )

    return {
        "distance": path.get("distance"),
        "duration": path.get("duration"),
        "steps": steps,
    }