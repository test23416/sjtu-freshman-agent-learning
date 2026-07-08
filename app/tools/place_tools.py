from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote


PLACES_PATH = Path("data/places/campus_places.json")

ROUTE_WORDS = ["怎么去", "怎么到", "路线", "导航", "走到", "到哪里", "去哪里"]
PLACE_WORDS = ["在哪里", "在哪", "位置", "怎么去", "怎么到", "路线", "导航", "附近"]


def load_places() -> list[dict[str, Any]]:
    if not PLACES_PATH.exists():
        return []

    with PLACES_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def has_coordinate(place: dict[str, Any]) -> bool:
    return isinstance(place.get("lng"), (int, float)) and isinstance(place.get("lat"), (int, float))


def clean_place(place: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in place.items() if not key.startswith("_")}


def find_places(question: str, limit: int = 3) -> list[dict[str, Any]]:
    places = load_places()
    matched: list[dict[str, Any]] = []

    for place in places:
        score = 0
        match_index = 10**9

        names = [place.get("name", "")]
        names.extend(place.get("aliases", []))

        for name in names:
            if name and name in question:
                score += 10 if name == place.get("name") else 6
                match_index = min(match_index, question.find(name))

        category = place.get("category")
        if category and category in question:
            score += 2

        for tag in place.get("tags", []):
            if tag in question:
                score += 1

        if score > 0:
            item = dict(place)
            item["_score"] = score
            item["_match_index"] = match_index
            matched.append(item)

    matched.sort(key=lambda item: (-item["_score"], item["_match_index"]))

    return matched[:limit]


def build_marker_url(place: dict[str, Any]) -> str | None:
    if not has_coordinate(place):
        return None

    lng = place["lng"]
    lat = place["lat"]
    name = quote(place["name"])

    return (
        "https://uri.amap.com/marker"
        f"?position={lng},{lat}"
        f"&name={name}"
        "&src=sjtu-freshman-agent"
        "&coordinate=gaode"
        "&callnative=0"
    )


def format_navigation_point(place: dict[str, Any]) -> str:
    lng = place["lng"]
    lat = place["lat"]
    name = quote(place["name"])
    return f"{lng},{lat},{name}"


def build_navigation_url(
    destination: dict[str, Any],
    origin: dict[str, Any] | None = None,
) -> str | None:
    if not has_coordinate(destination):
        return None

    to_point = format_navigation_point(destination)

    if origin and has_coordinate(origin):
        from_point = format_navigation_point(origin)
    else:
        from_point = ""

    return (
        "https://uri.amap.com/navigation"
        f"?from={from_point}"
        f"&to={to_point}"
        "&mode=walk"
        "&src=sjtu-freshman-agent"
        "&callnative=0"
    )


def is_route_intent(question: str) -> bool:
    return any(word in question for word in ROUTE_WORDS)


def run_place_tools(question: str) -> dict[str, list[dict[str, Any]]]:
    matched_places = find_places(question)

    if not matched_places:
        return {
            "tool_results": [],
            "cards": [],
        }

    cards: list[dict[str, Any]] = []
    tool_lines: list[str] = []

    for place in matched_places:
        marker_url = build_marker_url(place)

        cards.append(
            {
                "type": "place",
                "title": place["name"],
                "data": {
                    "place": clean_place(place),
                    "map_url": marker_url,
                },
            }
        )

        coord_status = "已配置坐标" if has_coordinate(place) else "尚未配置精确坐标"
        tool_lines.append(
            f"地点：{place['name']}\n"
            f"类别：{place.get('category', '未知')}\n"
            f"校区：{place.get('campus', '未知')}\n"
            f"说明：{place.get('description', '')}\n"
            f"坐标状态：{coord_status}"
        )

    if is_route_intent(question):
        if len(matched_places) >= 2:
            origin = matched_places[0]
            destination = matched_places[1]
        else:
            origin = None
            destination = matched_places[0]

        route_url = build_navigation_url(destination=destination, origin=origin)

        cards.insert(
            0,
            {
                "type": "route",
                "title": (
                    f"{origin['name']} 到 {destination['name']}"
                    if origin
                    else f"导航到 {destination['name']}"
                ),
                "data": {
                    "from": clean_place(origin) if origin else None,
                    "to": clean_place(destination),
                    "mode": "walk",
                    "map_url": route_url,
                    "missing_origin": origin is None,
                },
            },
        )

        if origin is None:
            tool_lines.append(
                f"用户有路线意图，但只识别到目的地：{destination['name']}。"
                "如果需要精确路线，可以追问用户从哪里出发。"
            )
        else:
            tool_lines.append(
                f"用户有路线意图，识别到起点：{origin['name']}，终点：{destination['name']}。"
            )

    return {
        "tool_results": [
            {
                "name": "campus_place_tool",
                "content": "\n\n".join(tool_lines),
            }
        ],
        "cards": cards,
    }