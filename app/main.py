from fastapi import FastAPI

from app.routes import router


app = FastAPI(title = "SJTU Freshman Agent Learning")

app.include_router(router)