from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .db import connect
from .stock import available, insufficient_stock, lock_product, location_id_by_kind

router = APIRouter()


class FBOShipmentIn(BaseModel):
    sku: str
    warehouse_id: int
    qty: int = Field(gt=0)


def _shipment_response(conn, shipment, replayed: bool):
    product = conn.execute("SELECT sku FROM products WHERE id = %s", (shipment["product_id"],)).fetchone()
    wh = conn.execute("SELECT name FROM locations WHERE id = %s", (shipment["from_location_id"],)).fetchone()
    return {
        "id": shipment["id"],
        "sku": product["sku"],
        "qty": shipment["qty"],
        "status": shipment["status"],
        "warehouse_id": shipment["from_location_id"],
        "warehouse": wh["name"],
        "replayed": replayed,
    }


@router.get("/fbo")
def list_fbo():
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.product_id, s.qty, s.status, s.created_at, s.from_location_id,
                   p.sku, l.name AS warehouse
            FROM fbo_shipments s
            JOIN products p ON p.id = s.product_id
            JOIN locations l ON l.id = s.from_location_id
            ORDER BY s.created_at DESC
            """
        ).fetchall()
        return [
            {
                "id": r["id"],
                "sku": r["sku"],
                "qty": r["qty"],
                "status": r["status"],
                "warehouse_id": r["from_location_id"],
                "warehouse": r["warehouse"],
                "replayed": False,
            }
            for r in rows
        ]


@router.post("/fbo", status_code=201)
def create_fbo(body: FBOShipmentIn):
    with connect() as conn:
        product = conn.execute("SELECT id FROM products WHERE sku = %s", (body.sku,)).fetchone()
        if not product:
            raise HTTPException(404, f"Товар {body.sku} не найден")
        wh = conn.execute(
            "SELECT id, is_archived FROM locations WHERE id = %s AND kind = 'ff' FOR SHARE",
            (body.warehouse_id,),
        ).fetchone()
        if not wh:
            raise HTTPException(404, "Склад не найден")
        if wh["is_archived"]:
            raise HTTPException(409, "Склад в архиве, отправка с него запрещена")
        row = conn.execute(
            """
            INSERT INTO fbo_shipments (product_id, qty, from_location_id, status)
            VALUES (%s, %s, %s, 'created')
            RETURNING id
            """,
            (product["id"], body.qty, body.warehouse_id),
        ).fetchone()
        return _shipment_response(conn, {"id": row["id"], "product_id": product["id"], "from_location_id": body.warehouse_id, "qty": body.qty, "status": "created"}, replayed=False)


@router.post("/fbo/{shipment_id}/send")
def send_fbo(shipment_id: int):
    with connect() as conn:
        shipment = conn.execute(
            "SELECT * FROM fbo_shipments WHERE id = %s FOR UPDATE",
            (shipment_id,),
        ).fetchone()
        if not shipment:
            raise HTTPException(404, "Отправка FBO не найдена")
        if shipment["status"] in ("sent", "accepted"):
            return _shipment_response(conn, shipment, replayed=True)
        if shipment["status"] != "created":
            raise HTTPException(409, f"Недопустимый переход: статус «{shipment['status']}»")
        lock_product(conn, shipment["product_id"])
        free = available(conn, shipment["product_id"], shipment["from_location_id"])
        if free < shipment["qty"]:
            product = conn.execute("SELECT sku FROM products WHERE id = %s", (shipment["product_id"],)).fetchone()
            wh = conn.execute("SELECT name FROM locations WHERE id = %s", (shipment["from_location_id"],)).fetchone()
            raise insufficient_stock(
                conn, shipment["product_id"], shipment["from_location_id"],
                wh["name"], shipment["qty"], free,
            )
        transit = location_id_by_kind(conn, "transit_fbo")
        conn.execute(
            """
            INSERT INTO movements (product_id, qty, from_location_id, to_location_id, op_type, op_id)
            VALUES (%s, %s, %s, %s, 'fbo_send', %s)
            """,
            (shipment["product_id"], shipment["qty"], shipment["from_location_id"], transit, shipment_id),
        )
        shipment = dict(shipment)
        shipment["status"] = "sent"
        conn.execute("UPDATE fbo_shipments SET status = 'sent' WHERE id = %s", (shipment_id,))
        return _shipment_response(conn, shipment, replayed=False)


@router.post("/fbo/{shipment_id}/accept")
def accept_fbo(shipment_id: int):
    with connect() as conn:
        shipment = conn.execute(
            "SELECT * FROM fbo_shipments WHERE id = %s FOR UPDATE",
            (shipment_id,),
        ).fetchone()
        if not shipment:
            raise HTTPException(404, "Отправка FBO не найдена")
        if shipment["status"] == "accepted":
            return _shipment_response(conn, shipment, replayed=True)
        if shipment["status"] != "sent":
            raise HTTPException(409, f"Недопустимый переход: статус «{shipment['status']}»")
        transit = location_id_by_kind(conn, "transit_fbo")
        marketplace = location_id_by_kind(conn, "marketplace")
        conn.execute(
            """
            INSERT INTO movements (product_id, qty, from_location_id, to_location_id, op_type, op_id)
            VALUES (%s, %s, %s, %s, 'fbo_accept', %s)
            """,
            (shipment["product_id"], shipment["qty"], transit, marketplace, shipment_id),
        )
        shipment = dict(shipment)
        shipment["status"] = "accepted"
        conn.execute("UPDATE fbo_shipments SET status = 'accepted' WHERE id = %s", (shipment_id,))
        return _shipment_response(conn, shipment, replayed=False)