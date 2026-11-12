from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .db import connect
from .stock import available, insufficient_stock, lock_product, location_id_by_kind

router = APIRouter()


class FBSOrderIn(BaseModel):
    sku: str
    warehouse_id: int
    qty: int = Field(gt=0)


def _order_response(conn, order, replayed: bool):
    product = conn.execute("SELECT sku FROM products WHERE id = %s", (order["product_id"],)).fetchone()
    wh = conn.execute("SELECT name FROM locations WHERE id = %s", (order["location_id"],)).fetchone()
    return {
        "id": order["id"],
        "sku": product["sku"],
        "qty": order["qty"],
        "status": order["status"],
        "warehouse_id": order["location_id"],
        "warehouse": wh["name"],
        "replayed": replayed,
    }


@router.get("/fbs/orders")
def list_fbs_orders():
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.product_id, r.qty, r.status, r.created_at, r.location_id,
                   p.sku, l.name AS warehouse
            FROM reservations r
            JOIN products p ON p.id = r.product_id
            JOIN locations l ON l.id = r.location_id
            ORDER BY r.created_at DESC
            """
        ).fetchall()
        return [
            {
                "id": r["id"],
                "sku": r["sku"],
                "qty": r["qty"],
                "status": r["status"],
                "warehouse_id": r["location_id"],
                "warehouse": r["warehouse"],
                "replayed": False,
            }
            for r in rows
        ]


@router.post("/fbs/orders", status_code=201)
def create_fbs_order(body: FBSOrderIn):
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
            raise HTTPException(409, "Склад в архиве, reserva на него запрещён")
        lock_product(conn, product["id"])
        free = available(conn, product["id"], body.warehouse_id)
        if free < body.qty:
            wh_name = conn.execute("SELECT name FROM locations WHERE id = %s", (body.warehouse_id,)).fetchone()["name"]
            raise insufficient_stock(
                conn, product["id"], body.warehouse_id, wh_name, body.qty, free,
            )
        row = conn.execute(
            """
            INSERT INTO reservations (product_id, location_id, qty, status)
            VALUES (%s, %s, %s, 'active')
            RETURNING id
            """,
            (product["id"], body.warehouse_id, body.qty),
        ).fetchone()
        return _order_response(conn, {"id": row["id"], "product_id": product["id"], "location_id": body.warehouse_id, "qty": body.qty, "status": "active"}, replayed=False)


@router.post("/fbs/orders/{order_id}/ship")
def ship_fbs_order(order_id: int):
    with connect() as conn:
        order = conn.execute(
            "SELECT * FROM reservations WHERE id = %s FOR UPDATE",
            (order_id,),
        ).fetchone()
        if not order:
            raise HTTPException(404, "Заказ FBS не найден")
        if order["status"] == "shipped":
            return _order_response(conn, order, replayed=True)
        if order["status"] == "cancelled":
            raise HTTPException(409, "Нельзя отправить отменённый заказ")
        if order["status"] != "active":
            raise HTTPException(409, f"Недопустимый переход: статус «{order['status']}»")
        lock_product(conn, order["product_id"])
        transit = location_id_by_kind(conn, "transit_client")
        conn.execute(
            """
            INSERT INTO movements (product_id, qty, from_location_id, to_location_id, op_type, op_id)
            VALUES (%s, %s, %s, %s, 'fbs_ship', %s)
            """,
            (order["product_id"], order["qty"], order["location_id"], transit, order_id),
        )
        order = dict(order)
        order["status"] = "shipped"
        conn.execute("UPDATE reservations SET status = 'shipped' WHERE id = %s", (order_id,))
        return _order_response(conn, order, replayed=False)


@router.post("/fbs/orders/{order_id}/cancel")
def cancel_fbs_order(order_id: int):
    with connect() as conn:
        order = conn.execute(
            "SELECT * FROM reservations WHERE id = %s FOR UPDATE",
            (order_id,),
        ).fetchone()
        if not order:
            raise HTTPException(404, "Заказ FBS не найден")
        if order["status"] == "cancelled":
            return _order_response(conn, order, replayed=True)
        if order["status"] == "shipped":
            raise HTTPException(409, "Нельзя отменить отправленный заказ")
        if order["status"] != "active":
            raise HTTPException(409, f"Недопустимый переход: статус «{order['status']}»")
        order = dict(order)
        order["status"] = "cancelled"
        conn.execute("UPDATE reservations SET status = 'cancelled' WHERE id = %s", (order_id,))
        return _order_response(conn, order, replayed=False)