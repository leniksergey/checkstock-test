from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .db import connect, init_db
from .stock import get_stock, location_id_by_kind, on_hand
from .transfers import router as transfers_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="CheckStock", lifespan=lifespan)

app.include_router(transfers_router)


@app.get("/health")
def health():
    with connect() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/stock")
def stock():
    with connect() as conn:
        return get_stock(conn)


class ReceiptIn(BaseModel):
    sku: str
    warehouse_id: int
    qty: int = Field(gt=0)


@app.post("/receipts", status_code=201)
def create_receipt(body: ReceiptIn):
    with connect() as conn:
        product = conn.execute("SELECT id FROM products WHERE sku = %s", (body.sku,)).fetchone()
        if not product:
            raise HTTPException(404, f"Товар {body.sku} не найден")
        wh = conn.execute(
            "SELECT id, is_archived FROM locations WHERE id = %s AND kind = 'ff'", (body.warehouse_id,)
        ).fetchone()
        if not wh:
            raise HTTPException(404, "Склад не найден")
        if wh["is_archived"]:
            raise HTTPException(409, "Склад в архиве, приход на него запрещён")
        conn.execute(
            """INSERT INTO movements (product_id, qty, from_location_id, to_location_id, op_type)
               VALUES (%s, %s, %s, %s, 'receipt')""",
            (product["id"], body.qty, location_id_by_kind(conn, "external"), wh["id"]),
        )
        return {"sku": body.sku, "warehouse_id": wh["id"], "on_hand": on_hand(conn, product["id"], wh["id"])}
