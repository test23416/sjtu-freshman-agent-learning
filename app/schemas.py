from pydantic import BaseModel,Field
from typing import Literal

class StudentProfile(BaseModel):
    campus:str | None = None
    college:str | None = None
    major:str | None = None
    dorm_area:str | None = None
    international_student:bool | None = None

class ChatMessage(BaseModel):
    role:Literal["user","assistant"]
    content:str

class ChatRequest(BaseModel):
    message: str
    history:list[ChatMessage] = Field(default_factory=list)
    profile:StudentProfile | None = None

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


