from __future__ import annotations

import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.config import CAMPUSLIFE_DINING_URL
from app.schemas import ChatMessage, DiningPreference, StudentProfile


CANTEENS_PATH = Path("data/dining/canteens.json")
CAMPUSLIFE_API_BASE = "https://campuslife.sjtu.edu.cn"
CAMPUSLIFE_MAIN_URL = f"{CAMPUSLIFE_API_BASE}/api/jczs/main"
CAMPUSLIFE_SUB_URL = f"{CAMPUSLIFE_API_BASE}/api/jczs/sub"
CST = timezone(timedelta(hours=8))

CANTEEN_NAMES = {
    100: "第一餐饮大楼",
    200: "第二餐饮大楼",
    300: "第三餐饮大楼",
    400: "第四餐饮大楼",
    500: "第五餐饮大楼",
    600: "第六餐饮大楼",
    700: "第七餐饮大楼",
    800: "哈乐餐厅",
    1000: "徐汇第二食堂",
    1200: "张江食堂",
}

CANTEEN_CAMPUS = {
    100: "闵行",
    200: "闵行",
    300: "闵行",
    400: "闵行",
    500: "闵行",
    600: "闵行",
    700: "闵行",
    800: "闵行",
    1000: "徐汇",
    1200: "张江",
}

CROWD_LEVELS = [
    (10, "空闲"),
    (25, "适中"),
    (40, "较挤"),
    (60, "拥挤"),
    (float("inf"), "爆满"),
]

DINING_WORDS = ["吃", "食堂", "餐厅", "用餐", "饭", "推荐", "拥挤", "排队", "人多", "就餐"]
RECORD_WORDS = ["我去", "去了", "吃了", "刚吃", "常去", "喜欢"]

DEFAULT_CAMPUSLIFE_URLS = [
    "https://campuslife.sjtu.edu.cn/api/canteen",
    "https://campuslife.sjtu.edu.cn/api/canteens",
    "https://campuslife.sjtu.edu.cn/api/dining",
    "https://campuslife.sjtu.edu.cn/api/dining/crowd",
]


def load_canteens() -> list[dict[str, Any]]:
    # 本地食堂库提供位置、别名、特色等静态信息，实时拥挤度运行时再合并。
    if not CANTEENS_PATH.exists():
        return []

    with CANTEENS_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, dict):
        return data.get("canteens", [])

    if isinstance(data, list):
        return data

    return []


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text.lower())


def get_canteen_names(canteen: dict[str, Any]) -> list[str]:
    names = [canteen.get("name", "")]
    names.extend(canteen.get("aliases", []))

    canteen_id = canteen.get("id")
    shorthand_map = {
        100: ["一餐", "一饭", "第一食堂", "第一餐厅"],
        200: ["二餐", "二饭", "第二食堂", "第二餐厅"],
        300: ["三餐", "三饭", "第三食堂", "第三餐厅"],
        400: ["四餐", "四饭", "第四食堂", "第四餐厅"],
        500: ["五餐", "五饭", "第五食堂", "第五餐厅"],
        600: ["六餐", "六饭", "第六食堂", "第六餐厅"],
        700: ["七餐", "七饭", "第七食堂", "第七餐厅"],
        800: ["哈乐", "清真", "清真餐厅"],
        900: ["玉兰", "玉兰苑"],
        1000: ["徐汇食堂", "徐汇二餐", "徐汇第二食堂"],
        1200: ["张江食堂", "张江餐厅"],
    }
    names.extend(shorthand_map.get(canteen_id, []))

    return [name for name in names if name]


def normalize_campus(campus: str | None) -> str:
    campus = campus or ""

    if "闵行" in campus:
        return "闵行"
    if "徐汇" in campus:
        return "徐汇"
    if "张江" in campus:
        return "张江"

    return campus


def crowd_label(rate: float) -> str:
    for threshold, label in CROWD_LEVELS:
        if rate <= threshold:
            return label

    return "未知"


def find_canteens(text: str, limit: int = 3) -> list[dict[str, Any]]:
    normalized_text = normalize_text(text)
    matched = []

    for canteen in load_canteens():
        score = 0
        match_index = 10**9

        for name in get_canteen_names(canteen):
            normalized_name = normalize_text(name)
            if normalized_name and normalized_name in normalized_text:
                score += 10 if name == canteen.get("name") else 7
                match_index = min(match_index, normalized_text.find(normalized_name))

        campus = canteen.get("campus", "")
        if campus and campus in text:
            score += 3

        for feature in canteen.get("features", []):
            if feature in text:
                score += 2

        if score > 0:
            item = dict(canteen)
            item["_score"] = score
            item["_match_index"] = match_index
            matched.append(item)

    matched.sort(key=lambda item: (-item["_score"], item["_match_index"]))

    return matched[:limit]


def detect_campus(question: str, profile: StudentProfile | None = None) -> str:
    for campus in ["闵行", "徐汇", "张江"]:
        if campus in question or f"{campus}校区" in question:
            return campus

    if profile and profile.campus:
        return normalize_campus(profile.campus)

    return "闵行"


def is_dining_question(question: str) -> bool:
    return any(word in question for word in DINING_WORDS)


def is_preference_record(question: str) -> bool:
    return any(word in question for word in RECORD_WORDS) and bool(find_canteens(question, limit=1))


def fetch_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    # campuslife 接口不稳定时直接返回 None，推荐逻辑会退回本地知识库。
    try:
        response = httpx.get(
            url,
            params=params,
            headers={
                "User-Agent": "Mozilla/5.0 SJTU-Freshman-Agent/1.0",
                "Accept": "application/json",
            },
            timeout=8,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as error:
        print("campuslife 食堂拥挤度接口失败:", url, repr(error))
        return None

    if data.get("code") != 0:
        print("campuslife 食堂拥挤度接口返回异常:", data)
        return None

    return data


def parse_sub_area(sub: dict[str, Any]) -> dict[str, Any]:
    rates = sub.get("curRates") or []

    if not rates:
        return {
            "name": sub.get("name", "?"),
            "is_open": bool(sub.get("isOpen")),
            "close_desc": sub.get("closeDesc") or "",
            "current_rate": None,
            "current_label": "无数据",
            "trend": "—",
            "last_updated": None,
        }

    latest = rates[-1]
    current_rate = latest.get("rate")

    if current_rate in [None, 0]:
        return {
            "name": sub.get("name", "?"),
            "is_open": bool(sub.get("isOpen")),
            "close_desc": sub.get("closeDesc") or "",
            "current_rate": None,
            "current_label": "无数据",
            "trend": "—",
            "last_updated": latest.get("time"),
        }

    trend = "—"
    if len(rates) >= 10:
        previous_rate = rates[-10].get("rate", current_rate)
        if current_rate > previous_rate + 3:
            trend = "上升"
        elif current_rate < previous_rate - 3:
            trend = "下降"
        else:
            trend = "平稳"

    return {
        "name": sub.get("name", "?"),
        "is_open": bool(sub.get("isOpen")),
        "close_desc": sub.get("closeDesc") or "",
        "current_rate": round(float(current_rate), 1),
        "current_label": crowd_label(float(current_rate)),
        "trend": trend,
        "last_updated": latest.get("time"),
    }


def fetch_campuslife_crowds() -> list[dict[str, Any]]:
    main_url = CAMPUSLIFE_DINING_URL or CAMPUSLIFE_MAIN_URL
    main_data = fetch_json(main_url)

    if not main_data:
        return []

    canteens = main_data.get("data") or []
    results = []

    for canteen in canteens:
        canteen_id = canteen.get("id")

        if canteen_id is None:
            continue

        detail_data = fetch_json(CAMPUSLIFE_SUB_URL, params={"id": canteen_id})
        detail = detail_data.get("data", {}) if detail_data else {}
        subs = [parse_sub_area(sub) for sub in detail.get("subs", [])]
        sub_rates = [
            sub["current_rate"]
            for sub in subs
            if sub.get("current_rate") is not None
        ]
        overall_rate = round(sum(sub_rates) / len(sub_rates), 1) if sub_rates else None
        schedule_status = detail.get("scheduleStatus", 0)
        schedule_desc = detail.get("scheduleDesc", "")
        is_dining = schedule_status != 0 and "Non-Dining" not in schedule_desc
        canteen_name = CANTEEN_NAMES.get(canteen_id, canteen.get("name", "未知食堂"))
        campus = CANTEEN_CAMPUS.get(canteen_id, normalize_campus(canteen.get("campus")))
        crowd_text = (
            crowd_label(overall_rate)
            if overall_rate is not None
            else "非就餐时间" if not is_dining else "无数据"
        )

        results.append(
            {
                "id": canteen_id,
                "name": canteen_name,
                "campus": campus,
                "is_operational": bool(canteen.get("isOpen")),
                "is_dining": is_dining,
                "schedule_desc": schedule_desc,
                "crowd_text": crowd_text,
                "crowd_score": overall_rate,
                "overall_rate": overall_rate,
                "overall_label": crowd_text,
                "subs": subs,
                "fetched_at": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
                "raw": canteen,
            }
        )

    results.sort(
        key=lambda item: (
            item["overall_rate"] if item["overall_rate"] is not None else 999,
            item["id"],
        )
    )

    return results


def match_crowd(canteen: dict[str, Any], crowds: list[dict[str, Any]]) -> dict[str, Any] | None:
    names = [normalize_text(name) for name in get_canteen_names(canteen)]

    best_match = None
    best_score = 0

    for crowd in crowds:
        crowd_name = normalize_text(crowd["name"])
        score = 0

        for name in names:
            if name and name == crowd_name:
                score = max(score, 20)
            elif name and (name in crowd_name or crowd_name in name):
                score = max(score, 12)

        if score > best_score:
            best_match = crowd
            best_score = score

    return best_match


def get_preference_count(
    canteen: dict[str, Any],
    preferences: list[DiningPreference] | None = None,
) -> int:
    preferences = preferences or []
    canteen_names = {normalize_text(name) for name in get_canteen_names(canteen)}

    total = 0
    for preference in preferences:
        preference_name = normalize_text(preference.canteen_name)
        if (
            str(preference.canteen_id) == str(canteen.get("id"))
            or preference_name in canteen_names
        ):
            total += preference.count

    return total


def score_canteen(
    canteen: dict[str, Any],
    crowd: dict[str, Any] | None,
    preferences: list[DiningPreference] | None = None,
) -> float:
    score = 100.0

    if crowd and crowd.get("crowd_score") is not None:
        score -= min(max(float(crowd["crowd_score"]), 0), 100) * 0.6
    else:
        score -= 15

    preference_count = get_preference_count(canteen, preferences)
    score += min(preference_count, 5) * 6

    return score


def build_recommendations(
    question: str,
    profile: StudentProfile | None = None,
    preferences: list[DiningPreference] | None = None,
) -> list[dict[str, Any]]:
    campus = detect_campus(question, profile)
    matched = find_canteens(question, limit=3)
    crowds = fetch_campuslife_crowds()

    candidates = [
        canteen
        for canteen in load_canteens()
        if normalize_campus(canteen.get("campus")) == campus
        and not canteen.get("is_drink_only")
    ]

    if matched:
        matched_ids = {canteen["id"] for canteen in matched}
        candidates = sorted(
            candidates,
            key=lambda canteen: 0 if canteen["id"] in matched_ids else 1,
        )

    scored = []

    for canteen in candidates:
        crowd = match_crowd(canteen, crowds)
        scored.append(
            {
                "canteen": canteen,
                "crowd": crowd,
                "preference_count": get_preference_count(canteen, preferences),
                "score": score_canteen(canteen, crowd, preferences),
            }
        )

    scored.sort(key=lambda item: item["score"], reverse=True)

    return scored[:3]


def describe_recommendation_reason(item: dict[str, Any]) -> str:
    canteen = item["canteen"]
    reasons = []

    if item.get("crowd"):
        reasons.append(f"实时拥挤度：{item['crowd']['crowd_text']}")
    else:
        reasons.append("实时拥挤度暂未获取")

    if item["preference_count"] > 0:
        reasons.append(f"你已记录偏好 {item['preference_count']} 次")

    features = canteen.get("features", [])
    if features:
        reasons.append("特点：" + "、".join(features[:3]))

    return "；".join(reasons)


def build_dining_card(
    question: str,
    profile: StudentProfile | None = None,
    preferences: list[DiningPreference] | None = None,
) -> dict[str, Any]:
    recommendations = build_recommendations(question, profile, preferences)
    campus = detect_campus(question, profile)

    return {
        "type": "dining",
        "title": f"{campus}食堂推荐",
        "data": {
            "campus": campus,
            "recommendations": recommendations,
        },
    }


def build_preference_record_card(canteen: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "dining_preference_record",
        "title": "已记录用餐偏好",
        "data": {
            "canteen": canteen,
        },
    }


def run_dining_tools(
    question: str,
    history: list[ChatMessage] | None = None,
    profile: StudentProfile | None = None,
    preferences: list[DiningPreference] | None = None,
    tool_plan: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    action = tool_plan.get("action") if tool_plan else None
    should_recommend = action == "dining_recommend" or is_dining_question(question)
    should_record = action == "dining_record" or is_preference_record(question)

    if should_record:
        matched = find_canteens(question, limit=1)
        if matched:
            canteen = matched[0]
            return {
                "tool_results": [
                    {
                        "name": "dining_tool",
                        "content": f"用户表示去 {canteen['name']} 用餐，前端将记录为历史偏好。",
                    }
                ],
                "cards": [build_preference_record_card(canteen)],
            }

    if not should_recommend:
        return {
            "tool_results": [],
            "cards": [],
        }

    card = build_dining_card(question, profile, preferences)
    recommendations = card["data"]["recommendations"]

    lines = []
    has_realtime = any(
        item.get("crowd") and item["crowd"].get("crowd_score") is not None
        for item in recommendations
    )
    has_campuslife_status = any(item.get("crowd") for item in recommendations)
    has_preference = any(item.get("preference_count", 0) > 0 for item in recommendations)

    if has_realtime:
        lines.append("已获取到部分 campuslife 实时拥挤度，下面按拥挤度、历史偏好和本地食堂知识库综合推荐。")
    elif has_campuslife_status:
        lines.append("已连接 campuslife 食堂接口，但当前可能是非供餐时段或暂无数值，下面先按食堂状态、本地知识库和已记录偏好推荐。")
    else:
        lines.append("campuslife 实时拥挤度暂未获取，下面先按本地食堂知识库和已记录偏好给出可用推荐。")
        lines.append("回答时不要说无法推荐；应说明实时数据缺失，并直接给出下列本地知识库推荐。")

    if not has_preference:
        lines.append("当前没有历史偏好记录；用户点击推荐卡片里的“我去这里吃了”后，后续推荐会自动加权。")

    for index, item in enumerate(recommendations, start=1):
        canteen = item["canteen"]
        crowd = item.get("crowd")
        crowd_text = crowd["crowd_text"] if crowd else "暂未获取实时拥挤度"
        lines.append(
            f"{index}. {canteen['name']}：拥挤度 {crowd_text}，"
            f"历史偏好 {item['preference_count']} 次，推荐分 {item['score']:.1f}。"
            f"推荐理由：{describe_recommendation_reason(item)}。"
        )

    return {
        "tool_results": [
            {
                "name": "dining_tool",
                "content": "\n".join(lines) if lines else "暂时没有可推荐的食堂。",
            }
        ],
        "cards": [card],
    }
