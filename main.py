from contextlib import asynccontextmanager

from fastapi import FastAPI

from .db import connect, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="CheckStock", lifespan=lifespan)


@app.get("/health")
def health():
    with connect() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}
