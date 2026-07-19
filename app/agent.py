from app.knowledge_base import search_knowledge
from app.llm import generate_answer, plan_tool_use
from app.schemas import ChatRequest, ChatResponse
from app.tools.calendar import run_calendar_tools
from app.tools.checklist import run_checklist_tools
from app.tools.dining import run_dining_tools
from app.tools.official import run_official_tools
from app.tools.parent import run_parent_tools
from app.tools.places import run_place_tools
from app.tools.tours import run_tour_tools


def merge_parent_contexts(results: list[dict], question: str) -> list[dict]:
    parent_hint = "家长 陪同 报到 接送 住宿 安全 医疗 缴费 防诈骗"
    if "适应" in question or "离家" in question or "大学" in question:
        parent_hint = "家长如何帮助新生适应大学 情绪 独立 生活适应"

    parent_results = search_knowledge(
        f"{question} {parent_hint}",
        top_k=3,
    )
    merged = []
    seen = set()

    for item in parent_results + results:
        key = (item.get("source"), item.get("title"), item.get("content"))
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)

    return merged[:5]


def build_calendar_answer(calendar_result: dict) -> str:
    card = calendar_result["cards"][0]
    data = card["data"]

    if data.get("config_error"):
        return (
            "当前暂未配置校历数据，建议先查看学校官网最新校历。"
            "具体安排以学校或学院最新通知为准。"
        )

    return (
        f"我先按当前时间判断，当前年份是 {data.get('current_year')} 年，"
        f"目标学年是 {data.get('requested_school_year') or data.get('academic_year')}。\n\n"
        "校历来自服务器端维护的官方资料副本/链接，请以学校官网最新版本为准。"
        "你可以直接点击下方校历卡片打开：\n"
        f"{data.get('calendar_url')}"
    )


def build_checklist_answer(checklist_result: dict) -> str:
    card = checklist_result["cards"][0]
    data = card["data"]
    group_count = len(data.get("groups", []))
    item_count = sum(len(group.get("items", [])) for group in data.get("groups", []))

    return (
        f"可以按下面这份清单来准备，共分成 {group_count} 个阶段、{item_count} 个事项。"
        "报到前先把材料、缴费、宿舍入住和近期学院通知核对好；下方事项可以直接勾选，方便你边准备边确认。"
    )


def build_tour_answer(tour_result: dict) -> str:
    card = tour_result["cards"][0]
    data = card["data"]
    stop_count = len(data.get("stops", []))

    return (
        f"可以走这条路线：{data.get('title')}，预计用时 {data.get('duration')}，"
        f"一共 {stop_count} 个点位。下面卡片里按顺序列出了每一站；有坐标的点位也会在地图上连成参观路线。"
    )


def build_route_fallback_answer(route_card: dict) -> str:
    data = route_card.get("data", {})
    destination = data.get("to") or {}

    if data.get("missing_origin"):
        return (
            f"我已经识别到目的地：{destination.get('name', '目的地')}，"
            "但还没有可用的起点。你可以点击定位，或补充“从哪里出发”，我再帮你规划路线。"
        )

    return (
        "我已经识别到目的地，但暂时没有获取到可绘制路线。"
        "你可以稍后重试，或先查看地点位置。"
    )


def build_dining_fallback_answer(dining_result: dict) -> str:
    card = dining_result["cards"][0]
    data = card.get("data", {})
    recommendations = data.get("recommendations", [])
    names = [
        item.get("canteen", {}).get("name")
        for item in recommendations
        if item.get("canteen", {}).get("name")
    ]

    prefix = (
        "当前没有获取到实时拥挤度，我先根据食堂位置和常见就餐信息给你推荐。"
        if data.get("fallback_reason")
        else "下面是结合食堂信息给你的推荐。"
    )

    if not names:
        return "当前暂时没有可推荐的食堂数据。你可以稍后再试，或补充校区后我再帮你筛选。"

    return f"{prefix}可以先看看：{'、'.join(names[:3])}。下方卡片里可以继续查看详情或发起导航。"


def chat_with_agent(request: ChatRequest) -> ChatResponse:
    # Static official tools are deterministic; route and dining tools still use the LLM planner.
    results = search_knowledge(request.message)
    if request.profile and request.profile.role == "parent":
        results = merge_parent_contexts(results, request.message)

    tool_results = run_official_tools(request.message)

    calendar_result = run_calendar_tools(request.message)
    tool_results.extend(calendar_result["tool_results"])

    if calendar_result["cards"]:
        return ChatResponse(
            answer=build_calendar_answer(calendar_result),
            sources=results,
            used_llm=False,
            cards=calendar_result["cards"],
        )

    parent_result = run_parent_tools(request.message, profile=request.profile)
    tool_results.extend(parent_result["tool_results"])

    tour_result = run_tour_tools(request.message, profile=request.profile)
    tool_results.extend(tour_result["tool_results"])

    if tour_result["cards"]:
        return ChatResponse(
            answer=build_tour_answer(tour_result),
            sources=results,
            used_llm=False,
            cards=tour_result["cards"],
        )

    checklist_result = run_checklist_tools(request.message)
    tool_results.extend(checklist_result["tool_results"])

    if checklist_result["tool_results"] and not checklist_result["cards"] and not parent_result["cards"]:
        return ChatResponse(
            answer="当前暂未配置新生报到清单数据，建议先查看学校或学院最新通知；你也可以补充具体场景，我先按通用报到准备帮你梳理。",
            sources=results,
            used_llm=False,
            cards=[],
        )

    if checklist_result["cards"] and not parent_result["cards"]:
        return ChatResponse(
            answer=build_checklist_answer(checklist_result),
            sources=results,
            used_llm=False,
            cards=checklist_result["cards"],
        )

    tool_plan = plan_tool_use(
        request.message,
        history=request.history,
        profile=request.profile,
        model=request.model,
    )

    place_result = run_place_tools(
        request.message,
        history=request.history,
        profile=request.profile,
        location=request.location,
        tool_plan=tool_plan if tool_plan and tool_plan.get("tool") == "campus_place_tool" else None,
    )
    tool_results.extend(place_result["tool_results"])

    dining_result = run_dining_tools(
        request.message,
        history=request.history,
        profile=request.profile,
        preferences=request.dining_preferences,
        tool_plan=tool_plan if tool_plan and tool_plan.get("tool") == "dining_tool" else None,
    )
    tool_results.extend(dining_result["tool_results"])

    route_cards = [card for card in place_result["cards"] if card.get("type") == "route"]
    if route_cards and not route_cards[0].get("data", {}).get("route") and not dining_result["cards"]:
        return ChatResponse(
            answer=build_route_fallback_answer(route_cards[0]),
            sources=results,
            used_llm=False,
            cards=parent_result["cards"] + checklist_result["cards"] + place_result["cards"],
        )

    answer, used_llm = generate_answer(
        question=request.message,
        contexts=results,
        history=request.history,
        profile=request.profile,
        tool_results=tool_results,
        model=request.model,
    )

    if not used_llm and dining_result["cards"]:
        answer = build_dining_fallback_answer(dining_result)

    return ChatResponse(
        answer=answer,
        sources=results,
        used_llm=used_llm,
        cards=(
            parent_result["cards"]
            + checklist_result["cards"]
            + place_result["cards"]
            + dining_result["cards"]
        ),
    )
