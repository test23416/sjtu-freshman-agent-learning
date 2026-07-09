from app.knowledge_base import search_knowledge
from app.llm import generate_answer, plan_tool_use
from app.schemas import ChatRequest,ChatResponse
from app.tools.official import run_official_tools
from app.tools.places import run_place_tools

def chat_with_agent(request: ChatRequest) -> ChatResponse:
    results = search_knowledge(request.message)

    tool_results = run_official_tools(request.message)

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
        tool_plan=tool_plan,
    )
    tool_results.extend(place_result["tool_results"])

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
        cards=place_result["cards"],
    )
