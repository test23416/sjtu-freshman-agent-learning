from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.schemas import ChatMessage, StudentProfile, UserLocation
from app.tools.amap import (
    get_walking_route,
    search_place_by_keyword,
    search_places_by_keyword,
)


PLACES_PATH = Path("data/places/places.json")
LEGACY_PLACES_PATH = Path("data/places/campus_places.json")
DINING_PATH = Path("data/dining/canteens.json")

ROUTE_WORDS = ["怎么去", "怎么到", "怎么走", "路线", "导航", "走到", "到哪里", "去哪里", "开车", "接送", "去", "到", "从"]
PLACE_WORDS = ["在哪里", "在哪", "位置", "怎么去", "怎么到", "路线", "导航", "附近"]


def resolve_place_coordinate(place: dict[str, Any]) -> dict[str, Any]:
    """
    优先使用本地坐标。
    如果本地没有坐标，就用高德 POI 搜索临时解析坐标。
    不直接写回 JSON，只在本次请求中使用。
    """
    if has_coordinate(place):
        return place

    keyword_parts = ["上海交通大学"]

    if place.get("campus"):
        keyword_parts.append(place["campus"])

    keyword_parts.append(place["name"])

    keyword = " ".join(keyword_parts)

    poi = search_place_by_keyword(keyword)

    if not poi:
        return place

    resolved = dict(place)
    resolved["lng"] = poi["lng"]
    resolved["lat"] = poi["lat"]
    resolved["amap_poi"] = poi
    resolved["coordinate_source"] = "amap"

    return resolved


def build_keyword_place(query: str, poi: dict[str, Any]) -> dict[str, Any]:
    name = poi.get("name") or query

    return {
        "id": poi.get("id") or f"amap:{query}",
        "name": name,
        "aliases": [query] if query != name else [],
        "campus": "未知",
        "category": poi.get("type") or "地点",
        "lng": poi["lng"],
        "lat": poi["lat"],
        "description": f"高德地图搜索到的地点：{name}。",
        "coordinate_source": "amap",
        "amap_poi": poi,
    }


def load_places() -> list[dict[str, Any]]:
    places_path = PLACES_PATH if PLACES_PATH.exists() else LEGACY_PLACES_PATH
    places: list[dict[str, Any]] = []

    if places_path.exists():
        with places_path.open("r", encoding="utf-8") as file:
            loaded = json.load(file)
            if isinstance(loaded, list):
                places = [item for item in loaded if isinstance(item, dict)]

    combined: list[dict[str, Any]] = []
    for place in places + load_dining_places():
        append_unique_place(combined, place)

    return combined


def load_dining_places() -> list[dict[str, Any]]:
    if not DINING_PATH.exists():
        return []

    with DINING_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)

    canteens = data.get("canteens", []) if isinstance(data, dict) else data
    places = []

    for canteen in canteens:
        if not isinstance(canteen, dict) or canteen.get("is_drink_only"):
            continue

        canteen_id = canteen.get("id")
        aliases = list(canteen.get("aliases", []))
        aliases.extend(
            {
                100: ["一餐", "第一餐厅", "第一食堂"],
                200: ["二餐", "第二餐厅", "第二食堂"],
                300: ["三餐", "第三餐厅", "第三食堂"],
                400: ["四餐", "第四餐厅", "第四食堂"],
                500: ["五餐", "第五餐厅", "第五食堂"],
                600: ["六餐", "第六餐厅", "第六食堂"],
                700: ["七餐", "第七餐厅", "第七食堂"],
                800: ["哈乐", "哈乐餐厅", "清真餐厅"],
                900: ["玉兰苑"],
            }.get(canteen_id, [])
        )

        places.append(
            {
                "id": f"dining:{canteen_id}",
                "name": canteen.get("name"),
                "aliases": [alias for alias in aliases if alias],
                "campus": canteen.get("campus"),
                "category": "食堂",
                "lng": canteen.get("lng"),
                "lat": canteen.get("lat"),
                "description": canteen.get("location_desc") or canteen.get("description", ""),
                "tags": ["食堂", "餐饮", "吃饭"],
            }
        )

    return places


def has_coordinate(place: dict[str, Any]) -> bool:
    return isinstance(place.get("lng"), (int, float)) and isinstance(place.get("lat"), (int, float))


def clean_place(place: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in place.items() if not key.startswith("_")}


def is_same_place(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    if not left or not right:
        return False

    if left.get("id") and right.get("id") and left["id"] == right["id"]:
        return True

    left_campus = str(left.get("campus") or "").replace("校区", "")
    right_campus = str(right.get("campus") or "").replace("校区", "")

    return (
        left.get("name") == right.get("name")
        and (not left_campus or not right_campus or left_campus == right_campus)
    )


def append_unique_place(
    places: list[dict[str, Any]],
    place: dict[str, Any],
) -> None:
    if any(is_same_place(item, place) for item in places):
        return

    places.append(place)


def sort_places_by_text_order(places: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(places, key=lambda place: place.get("_match_index", 10**9))


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


def score_poi_candidate(
    poi: dict[str, Any],
    query: str,
    preferred_names: list[str] | None = None,
    campus_hint: str | None = None,
) -> int:
    # 高德可能返回多个同名 POI；用 LLM 规范名、校区和地点类型一起给候选排序。
    preferred_names = [name for name in (preferred_names or []) if name]
    name = poi.get("name") or ""
    address = poi.get("address") or ""
    adname = poi.get("adname") or ""
    poi_type = poi.get("type") or ""
    haystack = f"{name} {address} {adname} {poi_type}"
    score = 0

    for preferred_name in preferred_names:
        if name == preferred_name:
            score += 120
        elif preferred_name in name:
            score += 80
        elif name and name in preferred_name:
            score += 35
        if preferred_name in haystack:
            score += 20

    if query and query in name:
        score += 45
    if query and query in haystack:
        score += 15

    if "上海交通大学" in haystack or "交大" in haystack:
        score += 40
    if "图书馆" in name:
        score += 30
    if "包玉刚" in name:
        score += 80

    campus_terms = [campus_hint] if campus_hint else []
    campus_terms.extend(["闵行", "东川路"])

    for term in campus_terms:
        if term and term in haystack:
            score += 30

    if "黄浦" in haystack or "重庆南路" in haystack:
        score -= 35

    return score


def choose_best_poi(
    candidates: list[dict[str, Any]],
    query: str,
    preferred_names: list[str] | None = None,
    campus_hint: str | None = None,
) -> dict[str, Any] | None:
    if not candidates:
        return None

    return max(
        candidates,
        key=lambda poi: score_poi_candidate(
            poi,
            query=query,
            preferred_names=preferred_names,
            campus_hint=campus_hint,
        ),
    )


def search_external_place(
    query: str | None,
    preferred_names: list[str] | None = None,
    campus_hint: str | None = None,
) -> dict[str, Any] | None:
    if not query:
        return None

    preferred_names = [name for name in (preferred_names or []) if name]
    search_terms = []

    for preferred_name in preferred_names:
        search_terms.append(f"上海交通大学 {preferred_name}".strip())

    search_terms.append(f"上海交通大学 {query}".strip())
    search_terms.append(query)

    seen_terms = set()
    candidates: list[dict[str, Any]] = []

    for term in search_terms:
        if not term or term in seen_terms:
            continue

        seen_terms.add(term)
        candidates.extend(search_places_by_keyword(term))

    poi = choose_best_poi(
        candidates,
        query=query,
        preferred_names=preferred_names,
        campus_hint=campus_hint,
    )

    if not poi:
        return None

    return build_keyword_place(preferred_names[0] if preferred_names else query, poi)


def resolve_place_query(
    query: str | None,
    preferred_names: list[str] | None = None,
    campus_hint: str | None = None,
) -> dict[str, Any] | None:
    if not query:
        return None

    local_queries = [query]
    local_queries.extend(name for name in (preferred_names or []) if name)

    for local_query in local_queries:
        local_matches = find_places(local_query, limit=1)

        if local_matches:
            return resolve_place_coordinate(local_matches[0])

    return search_external_place(
        query,
        preferred_names=preferred_names,
        campus_hint=campus_hint,
    )


def find_recent_context_places(
    history: list[ChatMessage] | None = None,
    profile: StudentProfile | None = None,
    limit: int = 6,
) -> list[dict[str, Any]]:
    recent_places: list[dict[str, Any]] = []

    for message in reversed(history or []):
        if message.role != "user":
            continue

        for place in sort_places_by_text_order(find_places(message.content, limit=3)):
            append_unique_place(recent_places, place)

        if len(recent_places) >= limit:
            return recent_places[:limit]

    if profile:
        profile_text = " ".join(
            value
            for value in [
                profile.campus,
                profile.college,
                profile.major,
                profile.dorm_area,
            ]
            if value
        )

        for place in find_places(profile_text, limit=3):
            append_unique_place(recent_places, place)

    return recent_places[:limit]


def build_current_location_place(location: UserLocation | None) -> dict[str, Any] | None:
    if not location:
        return None

    return {
        "id": "current_location",
        "name": "当前位置",
        "category": "定位",
        "lng": location.lng,
        "lat": location.lat,
        "accuracy": location.accuracy,
        "description": "浏览器定位提供的当前位置。",
    }


def resolve_route_endpoints(
    question: str,
    history: list[ChatMessage] | None = None,
    profile: StudentProfile | None = None,
    location: UserLocation | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
    question_places = sort_places_by_text_order(find_places(question, limit=4))
    context_places = find_recent_context_places(history, profile)

    if len(question_places) >= 2:
        return (
            resolve_place_coordinate(question_places[0]),
            resolve_place_coordinate(question_places[1]),
            "question",
        )

    if len(question_places) == 1:
        destination = resolve_place_coordinate(question_places[0])
        origin = next(
            (
                resolve_place_coordinate(place)
                for place in context_places
                if not is_same_place(place, destination)
            ),
            None,
        )

        if origin:
            return origin, destination, "context"

        current_location = build_current_location_place(location)
        if current_location:
            return current_location, destination, "current_location"

        return None, destination, "missing_origin"

    if len(context_places) >= 2:
        return (
            resolve_place_coordinate(context_places[0]),
            resolve_place_coordinate(context_places[1]),
            "context",
        )

    if len(context_places) == 1:
        destination = resolve_place_coordinate(context_places[0])
        current_location = build_current_location_place(location)
        if current_location:
            return current_location, destination, "current_location"

        return None, destination, "missing_origin"

    return None, None, "missing_destination"


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


def build_place_card(place: dict[str, Any]) -> dict[str, Any]:
    resolved_place = resolve_place_coordinate(place)
    marker_url = build_marker_url(resolved_place)

    return {
        "type": "place",
        "title": resolved_place["name"],
        "data": {
            "place": clean_place(resolved_place),
            "map_url": marker_url,
        },
    }


def build_place_tool_line(place: dict[str, Any]) -> str:
    coord_status = "已配置坐标" if has_coordinate(place) else "尚未配置精确坐标"

    return (
        f"地点：{place['name']}\n"
        f"类别：{place.get('category', '未知')}\n"
        f"校区：{place.get('campus', '未知')}\n"
        f"说明：{place.get('description', '')}\n"
        f"坐标状态：{coord_status}"
    )


def resolve_origin_for_route(
    origin_query: str | None,
    destination: dict[str, Any] | None,
    history: list[ChatMessage] | None = None,
    profile: StudentProfile | None = None,
    location: UserLocation | None = None,
    preferred_names: list[str] | None = None,
    campus_hint: str | None = None,
) -> tuple[dict[str, Any] | None, str]:
    if origin_query:
        origin = resolve_place_query(
            origin_query,
            preferred_names=preferred_names,
            campus_hint=campus_hint,
        )
        if origin:
            return origin, "llm"

    context_places = find_recent_context_places(history, profile)
    origin = next(
        (
            resolve_place_coordinate(place)
            for place in context_places
            if not is_same_place(place, destination)
        ),
        None,
    )

    if origin:
        return origin, "context"

    current_location = build_current_location_place(location)
    if current_location:
        return current_location, "current_location"

    return None, "missing_origin"


def run_planned_place_tool(
    tool_plan: dict[str, Any],
    history: list[ChatMessage] | None = None,
    profile: StudentProfile | None = None,
    location: UserLocation | None = None,
) -> dict[str, list[dict[str, Any]]]:
    action = tool_plan.get("action")
    cards: list[dict[str, Any]] = []
    tool_lines: list[str] = []

    if action == "place_search":
        place_query = tool_plan.get("place") or tool_plan.get("destination")
        normalized_place = (
            tool_plan.get("normalized_place")
            or tool_plan.get("normalized_destination")
        )
        campus_hint = tool_plan.get("campus")
        place = resolve_place_query(
            place_query,
            preferred_names=[normalized_place],
            campus_hint=campus_hint,
        )

        if not place:
            return {
                "tool_results": [
                    {
                        "name": "campus_place_tool",
                        "content": f"LLM 决定查询地点：{place_query}，但本地地点库和高德 POI 暂时都没有返回结果。",
                    }
                ],
                "cards": [],
            }

        cards.append(build_place_card(place))
        tool_lines.append(build_place_tool_line(place))

    elif action == "walking_route":
        destination_query = tool_plan.get("destination") or tool_plan.get("place")
        normalized_destination = (
            tool_plan.get("normalized_destination")
            or tool_plan.get("normalized_place")
        )
        campus_hint = tool_plan.get("campus")
        destination = resolve_place_query(
            destination_query,
            preferred_names=[normalized_destination],
            campus_hint=campus_hint,
        )

        if not destination:
            return {
                "tool_results": [
                    {
                        "name": "campus_place_tool",
                        "content": f"LLM 决定规划路线，但暂时没有识别到可用终点：{destination_query}。",
                    }
                ],
                "cards": [],
            }

        origin, route_source = resolve_origin_for_route(
            tool_plan.get("origin"),
            destination,
            history=history,
            profile=profile,
            location=location,
            preferred_names=[tool_plan.get("normalized_origin")],
            campus_hint=campus_hint,
        )

        route_data = None
        fallback_reason = None

        if origin and has_coordinate(origin) and has_coordinate(destination):
            route_data = get_walking_route(
                origin_lng=origin["lng"],
                origin_lat=origin["lat"],
                destination_lng=destination["lng"],
                destination_lat=destination["lat"],
            )

        if origin is None:
            fallback_reason = "缺少起点，请点击定位或补充起点。"
        elif not has_coordinate(origin) or not has_coordinate(destination):
            fallback_reason = "起点或终点暂未配置精确坐标。"
        elif route_data is None:
            fallback_reason = "暂时没有获取到可绘制路线。"

        cards.append(
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
                    "missing_origin": origin is None,
                    "route": route_data,
                    "route_provider": "amap" if route_data else None,
                    "route_source": route_source,
                    "navigation_url": build_navigation_url(destination, origin),
                    "fallback_reason": fallback_reason,
                    "error_message": fallback_reason,
                },
            }
        )

        if origin and route_data:
            tool_lines.append(
                f"LLM 决定调用步行路线工具：从 {origin['name']} 到 {destination['name']}，"
                f"距离约 {route_data.get('distance')} 米，"
                f"耗时约 {route_data.get('duration')} 秒。"
            )
        elif origin:
            tool_lines.append(
                f"LLM 决定调用步行路线工具，识别到起点：{origin['name']}，终点：{destination['name']}，"
                "但暂时未能获取高德步行路线。"
            )
        else:
            tool_lines.append(
                f"LLM 决定调用步行路线工具，识别到终点：{destination['name']}，"
                "但当前没有可用的起点或浏览器定位。"
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


def run_place_tools(
    question: str,
    history: list[ChatMessage] | None = None,
    profile: StudentProfile | None = None,
    location: UserLocation | None = None,
    tool_plan: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    if tool_plan:
        return run_planned_place_tool(
            tool_plan,
            history=history,
            profile=profile,
            location=location,
        )

    matched_places = find_places(question)

    if not matched_places and not is_route_intent(question):
        return {
            "tool_results": [],
            "cards": [],
        }

    cards: list[dict[str, Any]] = []
    tool_lines: list[str] = []

    for place in matched_places:
        resolved_place = resolve_place_coordinate(place)
        cards.append(build_place_card(resolved_place))
        tool_lines.append(build_place_tool_line(resolved_place))

    if is_route_intent(question):
        origin, destination, route_source = resolve_route_endpoints(
            question,
            history=history,
            profile=profile,
            location=location,
        )

        route_data = None
        fallback_reason = None

        if destination and origin and has_coordinate(origin) and has_coordinate(destination):
            route_data = get_walking_route(
                origin_lng=origin["lng"],
                origin_lat=origin["lat"],
                destination_lng=destination["lng"],
                destination_lat=destination["lat"],
            )

        if destination:
            if origin is None:
                fallback_reason = "缺少起点，请点击定位或补充起点。"
            elif not has_coordinate(origin) or not has_coordinate(destination):
                fallback_reason = "起点或终点暂未配置精确坐标。"
            elif route_data is None:
                fallback_reason = "暂时没有获取到可绘制路线。"

        if destination:
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
                        "missing_origin": origin is None,
                        "route": route_data,
                        "route_provider": "amap" if route_data else None,
                        "route_source": route_source,
                        "navigation_url": build_navigation_url(destination, origin),
                        "fallback_reason": fallback_reason,
                        "error_message": fallback_reason,
                    },
                },
            )

        if destination is None:
            tool_lines.append(
                "用户有路线意图，但暂时没有从当前问题或上下文中识别到明确终点。"
            )
        elif origin is None:
            tool_lines.append(
                f"用户有路线意图，但只识别到目的地：{destination['name']}。"
                "当前没有可用的历史起点或浏览器定位。"
            )
        elif route_data:
            source_text = {
                "question": "当前问题",
                "context": "上下文",
                "current_location": "当前位置",
            }.get(route_source, "路线补全")
            tool_lines.append(
                f"已根据{source_text}补全路线：从 {origin['name']} 到 {destination['name']}，"
                f"距离约 {route_data.get('distance')} 米，"
                f"耗时约 {route_data.get('duration')} 秒。"
            )
        else:
            tool_lines.append(
                f"用户有路线意图，识别到起点：{origin['name']}，终点：{destination['name']}，"
                "但暂时未能获取高德步行路线。"
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
