from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .db import connect

router = APIRouter()

TRANSIT_KEYS = {
    "transit_transfer": "between_ff",
    "transit_fbo": "to_marketplace_fbo",
    "transit_client": "to_client_fbs",
}

# Баланс места = всё, что пришло, минус всё, что ушло. Остаток нигде не хранится числом.
BALANCES_CTE = """
WITH flows AS (
    SELECT product_id, to_location_id AS location_id, qty FROM movements
    UNION ALL
    SELECT product_id, from_location_id, -qty FROM movements
), bal AS (
    SELECT product_id, location_id, sum(qty) AS qty FROM flows GROUP BY 1, 2
), res AS (
    SELECT product_id, location_id, sum(qty) AS qty
    FROM reservations WHERE status = 'active' GROUP BY 1, 2
)
"""


def location_id_by_kind(conn, kind):
    return conn.execute("SELECT id FROM locations WHERE kind = %s", (kind,)).fetchone()["id"]


def lock_product(conn, product_id):
    """Блокировка строки товара: два параллельных списания не продадут один остаток."""
    conn.execute("SELECT 1 FROM products WHERE id = %s FOR UPDATE", (product_id,))


def available(conn, product_id, location_id):
    """Доступно = баланс места минус активные резервы."""
    row = conn.execute(
        """
        SELECT coalesce(sum(CASE WHEN to_location_id = %s THEN qty ELSE -qty END), 0) AS on_hand
        FROM movements
        WHERE product_id = %s AND %s IN (from_location_id, to_location_id)
        """,
        (location_id, product_id, location_id),
    ).fetchone()
    res = conn.execute(
        """
        SELECT coalesce(sum(qty), 0) AS reserved
        FROM reservations
        WHERE product_id = %s AND location_id = %s AND status = 'active'
        """,
        (product_id, location_id),
    ).fetchone()
    return row["on_hand"] - res["reserved"]


def insufficient_stock(conn, product_id, location_id, location_name, qty, free):
    """409 с подсказкой, где ещё есть товар. Состояние БД не меняется."""
    rows = conn.execute(
        """
        WITH flows AS (
            SELECT product_id, to_location_id AS location_id, qty FROM movements
            UNION ALL
            SELECT product_id, from_location_id, -qty FROM movements
        ), bal AS (
            SELECT product_id, location_id, sum(qty) AS qty FROM flows GROUP BY 1, 2
        ), res AS (
            SELECT product_id, location_id, sum(qty) AS qty
            FROM reservations WHERE status = 'active' GROUP BY 1, 2
        )
        SELECT l.id AS warehouse_id, l.name,
               coalesce(b.qty, 0) - coalesce(r.qty, 0) AS available
        FROM locations l
        LEFT JOIN bal b ON b.product_id = %s AND b.location_id = l.id
        LEFT JOIN res r ON r.product_id = %s AND r.location_id = l.id
        WHERE l.kind = 'ff' AND l.id <> %s
        ORDER BY l.id
        """,
        (product_id, product_id, location_id),
    ).fetchall()
    elsewhere = [
        {"warehouse_id": r["warehouse_id"], "name": r["name"], "available": r["available"]}
        for r in rows if r["available"] > 0
    ]
    if elsewhere:
        names = ", ".join(r["name"] for r in rows if r["available"] > 0)
        hint = f"Хватает на: {names}. Оформите перемещение с него."
    else:
        hint = "Товара нет нигде."
    raise HTTPException(
        409,
        {
            "error": "insufficient_stock",
            "message": f"На складе «{location_name}» доступно {free}, нужно {qty}. {hint}",
            "needed": qty,
            "available_here": free,
            "available_elsewhere": elsewhere,
        },
    )


def on_hand(conn, product_id, location_id):
    row = conn.execute(
        """SELECT coalesce(sum(CASE WHEN to_location_id = %(l)s THEN qty ELSE -qty END), 0) AS qty
           FROM movements
           WHERE product_id = %(p)s AND %(l)s IN (from_location_id, to_location_id)""",
        {"p": product_id, "l": location_id},
    ).fetchone()
    return row["qty"]


def get_stock(conn):
    rows = conn.execute(
        BALANCES_CTE
        + """
        SELECT p.sku, p.name AS product, l.id AS location_id, l.kind, l.name AS location,
               l.is_archived, coalesce(b.qty, 0) AS qty, coalesce(r.qty, 0) AS reserved
        FROM products p
        CROSS JOIN locations l
        LEFT JOIN bal b ON b.product_id = p.id AND b.location_id = l.id
        LEFT JOIN res r ON r.product_id = p.id AND r.location_id = l.id
        WHERE l.kind IN ('ff', 'transit_transfer', 'transit_fbo', 'transit_client', 'marketplace')
        ORDER BY p.sku, l.id"""
    ).fetchall()
    products = {}
    for r in rows:
        item = products.setdefault(
            r["sku"],
            {
                "sku": r["sku"],
                "name": r["product"],
                "warehouses": [],
                "in_transit": {"between_ff": 0, "to_marketplace_fbo": 0, "to_client_fbs": 0},
                "at_marketplace": 0,
            },
        )
        if r["kind"] == "ff":
            item["warehouses"].append(
                {
                    "warehouse_id": r["location_id"],
                    "name": r["location"],
                    "archived": r["is_archived"],
                    "on_hand": r["qty"],
                    "reserved": r["reserved"],
                    "available": r["qty"] - r["reserved"],
                }
            )
        elif r["kind"] == "marketplace":
            item["at_marketplace"] = r["qty"]
        else:
            item["in_transit"][TRANSIT_KEYS[r["kind"]]] = r["qty"]
    return list(products.values())


class ReceiptIn(BaseModel):
    sku: str
    warehouse_id: int
    qty: int = Field(gt=0)


@router.get("/stock")
def api_get_stock():
    """Список товаров с остатками по складам + в пути + у МП."""
    with connect() as conn:
        return get_stock(conn)


@router.post("/receipts", status_code=201)
def api_create_receipt(payload: ReceiptIn):
    """Приход: EXTERNAL → ФФ."""
    with connect() as conn:
        # 1. найти товар
        product = conn.execute(
            "SELECT id FROM products WHERE sku = %s", (payload.sku,)
        ).fetchone()
        if not product:
            raise HTTPException(404, {"error": "not_found", "message": f"Товар {payload.sku} не найден"})
        product_id = product["id"]

        # 2. найти склад + проверить архив
        wh = conn.execute(
            "SELECT id, name, is_archived FROM locations WHERE id = %s AND kind = 'ff'",
            (payload.warehouse_id,),
        ).fetchone()
        if not wh:
            raise HTTPException(404, {"error": "not_found", "message": f"Склад #{payload.warehouse_id} не найден"})
        if wh["is_archived"]:
            raise HTTPException(409, {"error": "warehouse_archived",
                                      "message": f"Склад «{wh['name']}» в архиве, приход запрещён"})

        # 3. блокировка товара
        lock_product(conn, product_id)

        # 4. движение EXTERNAL → склад
        external_id = location_id_by_kind(conn, "external")
        conn.execute(
            """INSERT INTO movements (product_id, qty, from_location_id, to_location_id, op_type, op_id)
               VALUES (%s, %s, %s, %s, 'receipt', NULL)""",
            (product_id, payload.qty, external_id, payload.warehouse_id),
        )

        # 5. ответ
        return {
            "sku": payload.sku,
            "warehouse_id": payload.warehouse_id,
            "on_hand": on_hand(conn, product_id, payload.warehouse_id),
        }
