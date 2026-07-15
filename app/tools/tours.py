from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.schemas import StudentProfile
from app.tools.places import clean_place, find_places, has_coordinate, resolve_place_query


TOURS_PATH = Path("data/tours/campus_tours.json")
TOUR_WORDS = [
    "参观",
    "逛校园",
    "逛一下",
    "校园游",
    "游览",
    "参观路线",
    "校园路线",
    "熟悉校园",
    "参观一下",
    "走走校园",
    "带家长看看",
    "家长参观",
]


def is_tour_intent(question: str) -> bool:
    return any(word in question for word in TOUR_WORDS)


def load_tours() -> list[dict[str, Any]]:
    if not TOURS_PATH.exists():
        return []

    try:
        with TOURS_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return []

    if isinstance(data, dict):
        tours = data.get("tours", [])
    else:
        tours = data

    return [tour for tour in tours if isinstance(tour, dict)]


def normalize_campus(campus: str | None) -> str:
    text = campus or ""
    if "闵行" in text:
        return "闵行"
    if "徐汇" in text:
        return "徐汇"
    if "张江" in text:
        return "张江"
    return ""


def select_tour(question: str, profile: StudentProfile | None = None) -> dict[str, Any] | None:
    tours = load_tours()
    if not tours:
        return None

    role = profile.role if profile else "student"
    campus_hint = normalize_campus(profile.campus if profile else None) or normalize_campus(question) or "闵行"

    def score(tour: dict[str, Any]) -> int:
        value = 0
        if tour.get("audience") == role:
            value += 100
        if campus_hint and campus_hint in normalize_campus(tour.get("campus")):
            value += 50
        if role == "parent" and "家长" in tour.get("title", ""):
            value += 20
        if role == "student" and "新生" in tour.get("title", ""):
            value += 20
        return value

    return max(tours, key=score)


def enrich_stop(stop: dict[str, Any], campus_hint: str | None = None) -> dict[str, Any]:
    place_name = stop.get("place_name") or stop.get("title")
    enriched = dict(stop)

    local_matches = find_places(place_name or "", limit=1)
    if local_matches:
        clean = clean_place(local_matches[0])
        enriched["place"] = clean
        if has_coordinate(clean):
            enriched["lng"] = clean["lng"]
            enriched["lat"] = clean["lat"]
        return enriched

    place = resolve_place_query(
        place_name,
        preferred_names=[place_name],
        campus_hint=campus_hint,
    )

    if place:
        clean = clean_place(place)
        enriched["place"] = clean
        if has_coordinate(clean):
            enriched["lng"] = clean["lng"]
            enriched["lat"] = clean["lat"]

    return enriched


def build_tour_card_data(tour: dict[str, Any]) -> dict[str, Any]:
    campus_hint = normalize_campus(tour.get("campus"))
    stops = [
        enrich_stop(stop, campus_hint=campus_hint)
        for stop in tour.get("stops", [])
        if isinstance(stop, dict)
    ]

    return {
        "id": tour.get("id"),
        "title": tour.get("title"),
        "campus": tour.get("campus"),
        "audience": tour.get("audience"),
        "duration": tour.get("duration"),
        "description": tour.get("description"),
        "stops": stops,
        "tips": tour.get("tips", []),
    }


def run_tour_tools(
    question: str,
    profile: StudentProfile | None = None,
) -> dict[str, list[dict[str, Any]]]:
    if not is_tour_intent(question):
        return {"tool_results": [], "cards": []}

    tour = select_tour(question, profile)
    if not tour:
        return {
            "tool_results": [
                {
                    "name": "campus_tour_tool",
                    "content": "暂时没有配置可用的校园参观路线。",
                }
            ],
            "cards": [],
        }

    data = build_tour_card_data(tour)
    stop_names = " -> ".join(stop.get("place_name", "") for stop in data["stops"])

    return {
        "tool_results": [
            {
                "name": "campus_tour_tool",
                "content": (
                    f"已推荐校园参观路线：{data['title']}。\n"
                    f"适用人群：{data.get('audience')}；预计用时：{data.get('duration')}。\n"
                    f"站点顺序：{stop_names}"
                ),
            }
        ],
        "cards": [
            {
                "type": "campus_tour",
                "title": data["title"],
                "data": data,
            }
        ],
    }
