from app.knowledge_base import search_knowledge
from app.llm import generate_answer
from app.schemas import ChatRequest,ChatResponse


def chat_with_agent(request:ChatRequest) ->ChatResponse:
    results = search_knowledge(request.message)
    answer,used_llm = generate_answer(request.message,results)

    return ChatResponse(
        answer=answer,
        sources=results,
        used_llm=used_llm,
    )