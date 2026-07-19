import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.routes import router


app = FastAPI(title = "SJTU Freshman Agent Learning")
logger = logging.getLogger(__name__)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "null",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("未捕获异常: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=200,
        content={
            "answer": "服务暂时遇到问题，请稍后再试。如果问题持续，可以换一种问法。",
            "sources": [],
            "used_llm": False,
            "cards": [],
        },
    )


app.mount("/data/raw", StaticFiles(directory="data/raw"), name="raw_data")
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
