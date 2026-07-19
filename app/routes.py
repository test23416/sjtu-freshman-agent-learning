from fastapi import APIRouter

from app.agent import chat_with_agent
from app.knowledge_base import search_knowledge
from app.schemas import ChatRequest, ChatResponse,SearchRequest,SearchResponse
from app.feishu_routes import router as feishu_router

router = APIRouter()
router.include_router(feishu_router)


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    return chat_with_agent(request)

@router.post("/api/search", response_model=SearchResponse)
def search(request: SearchRequest):
    results = search_knowledge(request.query)

    return SearchResponse(
        query=request.query,
        results=results,
    )
