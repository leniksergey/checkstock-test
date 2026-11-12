from fastapi import FastAPI

from .db import connect

app = FastAPI(title="CheckStock")


@app.get("/health")
def health():
    with connect() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}
