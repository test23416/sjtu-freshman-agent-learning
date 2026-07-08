from pydantic import BaseModel,Field
from typing import Literal,Any

class StudentProfile(BaseModel):
    campus:str | None = None
    college:str | None = None
    major:str | None = None
    dorm_area:str | None = None
    international_student:bool | None = None

class KnowledgeContext(BaseModel):
    title:str
    source:str
    content:str
    score:float


class ResponseCard(BaseModel):
    type: str
    title: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

class ChatMessage(BaseModel):
    role:Literal["user","assistant"]
    content:str

class ChatRequest(BaseModel):
    message: str
    history:list[ChatMessage] = Field(default_factory=list)
    profile:StudentProfile | None = None

class ChatResponse(BaseModel):
    answer: str
    sources: list[KnowledgeContext] = Field(default_factory=list)
    used_llm: bool = False
    cards: list[ResponseCard] = Field(default_factory=list)

class SearchRequest(BaseModel):
    query: str

class SearchResponse(BaseModel):
    query: str
    results: list[KnowledgeContext]


