from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .db import connect
from .stock import available, insufficient_stock, lock_product, location_id_by_kind

router = APIRouter()


class TransferIn(BaseModel):
    sku: str
    from_warehouse_id: int
    to_warehouse_id: int
    qty: int = Field(gt=0)


def _transfer_response(conn, transfer, replayed: bool):
    product = conn.execute("SELECT sku FROM products WHERE id = %s", (transfer["product_id"],)).fetchone()
    from_wh = conn.execute("SELECT name FROM locations WHERE id = %s", (transfer["from_location_id"],)).fetchone()
    to_wh = conn.execute("SELECT name FROM locations WHERE id = %s", (transfer["to_location_id"],)).fetchone()
    return {
        "id": transfer["id"],
        "sku": product["sku"],
        "qty": transfer["qty"],
        "status": transfer["status"],
        "from_warehouse_id": transfer["from_location_id"],
        "from_warehouse": from_wh["name"],
        "to_warehouse_id": transfer["to_location_id"],
        "to_warehouse": to_wh["name"],
        "replayed": replayed,
    }


@router.get("/transfers")
def list_transfers():
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.product_id, t.qty, t.from_location_id, t.to_location_id,
                   t.status, t.created_at,
                   p.sku,
                   fl.name AS from_warehouse, tl.name AS to_warehouse
            FROM transfers t
            JOIN products p ON p.id = t.product_id
            JOIN locations fl ON fl.id = t.from_location_id
            JOIN locations tl ON tl.id = t.to_location_id
            ORDER BY t.created_at DESC
            """
        ).fetchall()
        return [
            {
                "id": r["id"],
                "sku": r["sku"],
                "qty": r["qty"],
                "status": r["status"],
                "from_warehouse_id": r["from_location_id"],
                "from_warehouse": r["from_warehouse"],
                "to_warehouse_id": r["to_location_id"],
                "to_warehouse": r["to_warehouse"],
                "replayed": False,
            }
            for r in rows
        ]


@router.post("/transfers", status_code=201)
def create_transfer(body: TransferIn):
    with connect() as conn:
        product = conn.execute("SELECT id FROM products WHERE sku = %s", (body.sku,)).fetchone()
        if not product:
            raise HTTPException(404, f"Товар {body.sku} не найден")
        from_wh = conn.execute(
            "SELECT id, is_archived FROM locations WHERE id = %s AND kind = 'ff' FOR SHARE",
            (body.from_warehouse_id,),
        ).fetchone()
        if not from_wh:
            raise HTTPException(404, "Склад-отправитель не найден")
        to_wh = conn.execute(
            "SELECT id, is_archived FROM locations WHERE id = %s AND kind = 'ff' FOR SHARE",
            (body.to_warehouse_id,),
        ).fetchone()
        if not to_wh:
            raise HTTPException(404, "Склад-получатель не найден")
        if body.from_warehouse_id == body.to_warehouse_id:
            raise HTTPException(409, "Отправитель и получатель — один и тот же склад")
        if to_wh["is_archived"]:
            raise HTTPException(409, "Склад-получатель в архиве, перемещение на него запрещено")
        row = conn.execute(
            """
            INSERT INTO transfers (product_id, qty, from_location_id, to_location_id, status)
            VALUES (%s, %s, %s, %s, 'created')
            RETURNING id
            """,
            (product["id"], body.qty, body.from_warehouse_id, body.to_warehouse_id),
        ).fetchone()
        return _transfer_response(conn, {"id": row["id"], "product_id": product["id"], "from_location_id": body.from_warehouse_id, "to_location_id": body.to_warehouse_id, "qty": body.qty, "status": "created"}, replayed=False)


@router.post("/transfers/{transfer_id}/send")
def send_transfer(transfer_id: int):
    with connect() as conn:
        transfer = conn.execute(
            "SELECT * FROM transfers WHERE id = %s FOR UPDATE",
            (transfer_id,),
        ).fetchone()
        if not transfer:
            raise HTTPException(404, "Перемещение не найдено")
        if transfer["status"] in ("sent", "received"):
            return _transfer_response(conn, transfer, replayed=True)
        if transfer["status"] != "created":
            raise HTTPException(409, f"Недопустимый переход: статус «{transfer['status']}»")
        lock_product(conn, transfer["product_id"])
        to_wh = conn.execute(
            "SELECT id, is_archived FROM locations WHERE id = %s FOR SHARE",
            (transfer["to_location_id"],),
        ).fetchone()
        if to_wh["is_archived"]:
            raise HTTPException(409, "Склад-получатель в архиве, приёмка на него запрещена")
        free = available(conn, transfer["product_id"], transfer["from_location_id"])
        if free < transfer["qty"]:
            product = conn.execute("SELECT sku FROM products WHERE id = %s", (transfer["product_id"],)).fetchone()
            from_wh = conn.execute("SELECT name FROM locations WHERE id = %s", (transfer["from_location_id"],)).fetchone()
            raise insufficient_stock(
                conn, transfer["product_id"], transfer["from_location_id"],
                from_wh["name"], transfer["qty"], free,
            )
        transit = location_id_by_kind(conn, "transit_transfer")
        conn.execute(
            """
            INSERT INTO movements (product_id, qty, from_location_id, to_location_id, op_type, op_id)
            VALUES (%s, %s, %s, %s, 'transfer_send', %s)
            """,
            (transfer["product_id"], transfer["qty"], transfer["from_location_id"], transit, transfer_id),
        )
        conn.execute("UPDATE transfers SET status = 'sent' WHERE id = %s", (transfer_id,))
        transfer = dict(transfer)
        transfer["status"] = "sent"
        return _transfer_response(conn, transfer, replayed=False)


@router.post("/transfers/{transfer_id}/receive")
def receive_transfer(transfer_id: int):
    with connect() as conn:
        transfer = conn.execute(
            "SELECT * FROM transfers WHERE id = %s FOR UPDATE",
            (transfer_id,),
        ).fetchone()
        if not transfer:
            raise HTTPException(404, "Перемещение не найдено")
        if transfer["status"] == "received":
            return _transfer_response(conn, transfer, replayed=True)
        if transfer["status"] != "sent":
            raise HTTPException(409, f"Недопустимый переход: статус «{transfer['status']}»")
        lock_product(conn, transfer["product_id"])
        to_wh = conn.execute(
            "SELECT id, is_archived FROM locations WHERE id = %s FOR SHARE",
            (transfer["to_location_id"],),
        ).fetchone()
        if to_wh["is_archived"]:
            raise HTTPException(409, "Склад-получатель в архиве, приёмка на него запрещена")
        transit = location_id_by_kind(conn, "transit_transfer")
        conn.execute(
            """
            INSERT INTO movements (product_id, qty, from_location_id, to_location_id, op_type, op_id)
            VALUES (%s, %s, %s, %s, 'transfer_receive', %s)
            """,
            (transfer["product_id"], transfer["qty"], transit, transfer["to_location_id"], transfer_id),
        )
        conn.execute("UPDATE transfers SET status = 'received' WHERE id = %s", (transfer_id,))
        transfer = dict(transfer)
        transfer["status"] = "received"
        return _transfer_response(conn, transfer, replayed=False)