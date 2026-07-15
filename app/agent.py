from app.knowledge_base import search_knowledge
from app.llm import generate_answer, plan_tool_use
from app.schemas import ChatRequest, ChatResponse
from app.tools.calendar import run_calendar_tools
from app.tools.checklist import run_checklist_tools
from app.tools.dining import run_dining_tools
from app.tools.official import run_official_tools
from app.tools.parent import run_parent_tools
from app.tools.places import run_place_tools


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

    checklist_result = run_checklist_tools(request.message)
    tool_results.extend(checklist_result["tool_results"])

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

    answer, used_llm = generate_answer(
        question=request.message,
        contexts=results,
        history=request.history,
        profile=request.profile,
        tool_results=tool_results,
    )

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
