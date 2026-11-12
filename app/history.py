from fastapi import APIRouter, Query

from .db import connect

router = APIRouter()


@router.get("/history")
def history(
    sku: str | None = Query(None),
    warehouse_id: int | None = Query(None),
    limit: int = Query(200, le=1000),
):
    with connect() as conn:
        conditions = []
        params = []
        if sku:
            conditions.append("p.sku = %s")
            params.append(sku)
        if warehouse_id is not None:
            conditions.append("(m.from_location_id = %s OR m.to_location_id = %s)")
            params.extend([warehouse_id, warehouse_id])
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = conn.execute(
            f"""
            SELECT m.created_at, p.sku, m.qty,
                   fl.name AS from_location, tl.name AS to_location, m.op_type
            FROM movements m
            JOIN products p ON p.id = m.product_id
            LEFT JOIN locations fl ON fl.id = m.from_location_id
            LEFT JOIN locations tl ON tl.id = m.to_location_id
            {where}
            ORDER BY m.created_at DESC
            LIMIT %s
            """,
            (*params, limit),
        ).fetchall()
        return [
            {
                "created_at": r["created_at"].isoformat(),
                "sku": r["sku"],
                "qty": r["qty"],
                "from": r["from_location"],
                "to": r["to_location"],
                "op_type": r["op_type"],
            }
            for r in rows
        ]