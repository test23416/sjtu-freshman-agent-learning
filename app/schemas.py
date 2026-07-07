from pydantic import BaseModel,Field


class ChatRequest(BaseModel):
    message: str


class KnowledgeContext(BaseModel):
    title:str
    source:str
    content:str
    score:int

class ChatResponse(BaseModel):
    answer: str
    sources: list[KnowledgeContext] = Field(default_factory=list)
    used_llm: bool = False


class SearchRequest(BaseModel):
    query: str


class SearchResponse(BaseModel):
    query: str
    results: list[KnowledgeContext]