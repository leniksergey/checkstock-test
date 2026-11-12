from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .db import connect

router = APIRouter()

VIRTUAL_NAMES = {"EXTERNAL", "TRANSIT_TRANSFER", "TRANSIT_FBO", "TRANSIT_CLIENT", "MARKETPLACE"}


class WarehouseIn(BaseModel):
    name: str = Field(min_length=1)


@router.get("/warehouses")
def list_warehouses():
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT l.id, l.name, l.is_archived,
                   coalesce(sum(CASE WHEN m.to_location_id = l.id THEN m.qty ELSE -m.qty END), 0) AS on_hand
            FROM locations l
            LEFT JOIN movements m ON m.from_location_id = l.id OR m.to_location_id = l.id
            WHERE l.kind = 'ff'
            GROUP BY l.id, l.name, l.is_archived
            ORDER BY l.id
            """
        ).fetchall()
        return [
            {
                "warehouse_id": r["id"],
                "name": r["name"],
                "archived": r["is_archived"],
                "on_hand": r["on_hand"],
            }
            for r in rows
        ]


@router.post("/warehouses", status_code=201)
def create_warehouse(body: WarehouseIn):
    with connect() as conn:
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "Имя склада не может быть пустым")
        if name.upper() in VIRTUAL_NAMES:
            raise HTTPException(409, f"Имя «{name}» зарезервировано для виртуального места")
        existing = conn.execute("SELECT id FROM locations WHERE kind = 'ff' AND name = %s", (name,)).fetchone()
        if existing:
            raise HTTPException(409, f"Склад «{name}» уже существует")
        row = conn.execute(
            "INSERT INTO locations (kind, name) VALUES ('ff', %s) RETURNING id",
            (name,),
        ).fetchone()
        return {"warehouse_id": row["id"], "name": name, "archived": False}


@router.patch("/warehouses/{warehouse_id}")
def rename_warehouse(warehouse_id: int, body: WarehouseIn):
    with connect() as conn:
        wh = conn.execute(
            "SELECT id, name FROM locations WHERE id = %s AND kind = 'ff' FOR UPDATE",
            (warehouse_id,),
        ).fetchone()
        if not wh:
            raise HTTPException(404, "Склад не найден")
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "Имя склада не может быть пустым")
        if name.upper() in VIRTUAL_NAMES:
            raise HTTPException(409, f"Имя «{name}» зарезервировано для виртуального места")
        existing = conn.execute(
            "SELECT id FROM locations WHERE kind = 'ff' AND name = %s AND id <> %s",
            (name, warehouse_id),
        ).fetchone()
        if existing:
            raise HTTPException(409, f"Склад «{name}» уже существует")
        conn.execute("UPDATE locations SET name = %s WHERE id = %s", (name, warehouse_id))
        return {"warehouse_id": warehouse_id, "name": name}


@router.delete("/warehouses/{warehouse_id}")
def archive_warehouse(warehouse_id: int):
    with connect() as conn:
        wh = conn.execute(
            "SELECT id, is_archived FROM locations WHERE id = %s AND kind = 'ff' FOR UPDATE",
            (warehouse_id,),
        ).fetchone()
        if not wh:
            raise HTTPException(404, "Склад не найден")
        if wh["is_archived"]:
            return {"warehouse_id": warehouse_id, "archived": True, "replayed": True}
        sent = conn.execute(
            "SELECT id FROM transfers WHERE to_location_id = %s AND status = 'sent'",
            (warehouse_id,),
        ).fetchone()
        if sent:
            raise HTTPException(409, "На этот склад есть перемещения в пути, архивирование невозможно")
        conn.execute("UPDATE locations SET is_archived = true WHERE id = %s", (warehouse_id,))
        return {"warehouse_id": warehouse_id, "archived": True}


@router.get("/stock/{sku}/locations")
def stock_locations(sku: str):
    with connect() as conn:
        product = conn.execute("SELECT id FROM products WHERE sku = %s", (sku,)).fetchone()
        if not product:
            raise HTTPException(404, f"Товар {sku} не найден")
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
            SELECT l.id AS location_id, l.kind, l.name, l.is_archived,
                   coalesce(b.qty, 0) AS qty, coalesce(r.qty, 0) AS reserved
            FROM locations l
            LEFT JOIN bal b ON b.product_id = %s AND b.location_id = l.id
            LEFT JOIN res r ON r.product_id = %s AND r.location_id = l.id
            WHERE l.kind IN ('ff', 'transit_transfer', 'transit_fbo', 'transit_client', 'marketplace')
              AND coalesce(b.qty, 0) <> 0
            ORDER BY l.id
            """,
            (product["id"], product["id"]),
        ).fetchall()
        return {
            "sku": sku,
            "locations": [
                {
                    "location_id": r["location_id"],
                    "kind": r["kind"],
                    "name": r["name"],
                    "archived": r["is_archived"],
                    "qty": r["qty"],
                    "reserved": r["reserved"],
                }
                for r in rows
            ],
        }