import pytest
from concurrent.futures import ThreadPoolExecutor

from app.db import connect as app_connect

NORTH = 6  # Север
SOUTH = 7  # Юг


def assert_invariant(conn):
    """Σ балансов всех мест кроме EXTERNAL = Σ приходов для каждого товара."""
    rows = conn.execute("""
        WITH flows AS (
            SELECT product_id, to_location_id AS location_id, qty FROM movements
            UNION ALL
            SELECT product_id, from_location_id, -qty FROM movements
        ), bal AS (
            SELECT product_id, location_id, sum(qty) AS qty FROM flows GROUP BY 1, 2
        )
        SELECT p.id AS product_id,
               coalesce(sum(CASE WHEN l.kind <> 'external' THEN b.qty END), 0) AS total_balance,
               coalesce((SELECT sum(qty) FROM movements m2 WHERE m2.product_id = p.id AND m2.op_type = 'receipt'), 0) AS total_receipts
        FROM products p
        CROSS JOIN locations l
        LEFT JOIN bal b ON b.product_id = p.id AND b.location_id = l.id
        GROUP BY p.id
    """).fetchall()
    for r in rows:
        assert r["total_balance"] == r["total_receipts"], (
            f"Инвариант нарушен для товара {r['product_id']}: "
            f"баланс {r['total_balance']} ≠ приходы {r['total_receipts']}"
        )
    rows2 = conn.execute("""
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
        SELECT l.id, coalesce(b.qty, 0) AS on_hand, coalesce(r.qty, 0) AS reserved
        FROM locations l
        LEFT JOIN bal b ON b.location_id = l.id
        LEFT JOIN res r ON r.location_id = l.id
        WHERE l.kind = 'ff'
    """).fetchall()
    for r2 in rows2:
        assert r2["on_hand"] >= r2["reserved"], f"Склад {r2['id']}: на складе < резерв"


def _on_hand(conn, product_id, location_id):
    row = conn.execute(
        """
        SELECT coalesce(sum(CASE WHEN to_location_id = %s THEN qty ELSE -qty END), 0) AS qty
        FROM movements
        WHERE product_id = %s AND %s IN (from_location_id, to_location_id)
        """,
        (location_id, product_id, location_id),
    ).fetchone()
    return row["qty"]


def _get_product_id(conn, sku):
    return conn.execute("SELECT id FROM products WHERE sku = %s", (sku,)).fetchone()["id"]


def test_receipt_changes_stock(client):
    """1. приход → остатки"""
    with app_connect() as conn:
        before = _on_hand(conn, _get_product_id(conn, "SKU-101"), NORTH)
    r = client.post("/receipts", json={"sku": "SKU-101", "warehouse_id": NORTH, "qty": 5})
    assert r.status_code == 201, r.text
    assert r.json()["on_hand"] == before + 5
    with app_connect() as conn:
        assert_invariant(conn)


def test_reserve_changes_available_not_on_hand(client):
    """2. резерв → доступно уменьшилось, на складе то же"""
    with app_connect() as conn:
        pid = _get_product_id(conn, "SKU-101")
        before_hand = _on_hand(conn, pid, NORTH)
    r = client.post("/fbs/orders", json={"sku": "SKU-101", "warehouse_id": NORTH, "qty": 3})
    assert r.status_code == 201, r.text
    with app_connect() as conn:
        after_hand = _on_hand(conn, pid, NORTH)
        assert after_hand == before_hand, "на складе не должно измениться"
        stock = client.get("/stock").json()
        sku_stock = next(s for s in stock if s["sku"] == "SKU-101")
        wh = next(w for w in sku_stock["warehouses"] if w["warehouse_id"] == NORTH)
        assert wh["available"] == before_hand - 3
        assert_invariant(conn)


def test_transfer_send_receive(client):
    """3. перемещение send / receive"""
    with app_connect() as conn:
        pid = _get_product_id(conn, "SKU-101")
        before_north = _on_hand(conn, pid, NORTH)
        before_south = _on_hand(conn, pid, SOUTH)
    r = client.post("/transfers", json={"sku": "SKU-101", "from_warehouse_id": NORTH, "to_warehouse_id": SOUTH, "qty": 4})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    assert r.json()["status"] == "created"
    with app_connect() as conn:
        assert_invariant(conn)
    r = client.post(f"/transfers/{tid}/send")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "sent"
    assert r.json()["replayed"] is False
    with app_connect() as conn:
        assert _on_hand(conn, pid, NORTH) == before_north - 4
        assert _on_hand(conn, pid, SOUTH) == before_south
        assert_invariant(conn)
    r = client.post(f"/transfers/{tid}/receive")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "received"
    with app_connect() as conn:
        assert _on_hand(conn, pid, NORTH) == before_north - 4
        assert _on_hand(conn, pid, SOUTH) == before_south + 4
        assert_invariant(conn)


def test_fbs_ship(client):
    """4. отправка FBS"""
    r = client.post("/fbs/orders", json={"sku": "SKU-101", "warehouse_id": NORTH, "qty": 2})
    assert r.status_code == 201, r.text
    oid = r.json()["id"]
    with app_connect() as conn:
        pid = _get_product_id(conn, "SKU-101")
        before_hand = _on_hand(conn, pid, NORTH)
    r = client.post(f"/fbs/orders/{oid}/ship")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "shipped"
    with app_connect() as conn:
        assert _on_hand(conn, pid, NORTH) == before_hand - 2
        assert_invariant(conn)


def test_fbo_send_accept(client):
    """5. FBO send / accept"""
    r = client.post("/fbo", json={"sku": "SKU-101", "warehouse_id": NORTH, "qty": 2})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    r = client.post(f"/fbo/{sid}/send")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "sent"
    r = client.post(f"/fbo/{sid}/accept")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted"
    with app_connect() as conn:
        assert_invariant(conn)


def test_insufficient_stock_409(client):
    """6. нехватка на одном ФФ при достаточном общем остатке → 409"""
    client.post("/fbs/orders", json={"sku": "SKU-101", "warehouse_id": NORTH, "qty": 20})
    with app_connect() as conn:
        pid = _get_product_id(conn, "SKU-101")
        free_before = _on_hand(conn, pid, NORTH)
    r = client.post("/transfers", json={"sku": "SKU-101", "from_warehouse_id": NORTH, "to_warehouse_id": SOUTH, "qty": 999})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    r = client.post(f"/transfers/{tid}/send")
    assert r.status_code == 409, r.text
    assert "insufficient_stock" in r.json()["detail"]["error"]
    with app_connect() as conn:
        assert _on_hand(conn, pid, NORTH) == free_before
        assert_invariant(conn)


def test_archive_warehouse(client):
    """7. удаление ФФ с остатком → остатки и история на месте, входящее запрещено"""
    r = client.delete(f"/warehouses/{SOUTH}")
    assert r.status_code == 200, r.text
    assert r.json()["archived"] is True
    r = client.post("/receipts", json={"sku": "SKU-103", "warehouse_id": SOUTH, "qty": 1})
    assert r.status_code == 409, r.text
    r = client.post("/transfers", json={"sku": "SKU-101", "from_warehouse_id": NORTH, "to_warehouse_id": SOUTH, "qty": 1})
    assert r.status_code == 409, r.text
    with app_connect() as conn:
        assert_invariant(conn)


def test_replayed_no_double(client):
    """8. повторные receive / ship / accept / cancel → replayed, без двойного списания"""
    r = client.post("/transfers", json={"sku": "SKU-101", "from_warehouse_id": NORTH, "to_warehouse_id": SOUTH, "qty": 3})
    assert r.status_code == 201
    tid = r.json()["id"]
    client.post(f"/transfers/{tid}/send")
    r1 = client.post(f"/transfers/{tid}/receive")
    assert r1.status_code == 200 and r1.json()["replayed"] is False
    r2 = client.post(f"/transfers/{tid}/receive")
    assert r2.status_code == 200 and r2.json()["replayed"] is True
    r = client.post("/fbs/orders", json={"sku": "SKU-101", "warehouse_id": NORTH, "qty": 2})
    oid = r.json()["id"]
    r1 = client.post(f"/fbs/orders/{oid}/ship")
    assert r1.json()["replayed"] is False
    r2 = client.post(f"/fbs/orders/{oid}/ship")
    assert r2.json()["replayed"] is True
    r = client.post("/fbo", json={"sku": "SKU-101", "warehouse_id": NORTH, "qty": 1})
    sid = r.json()["id"]
    client.post(f"/fbo/{sid}/send")
    r1 = client.post(f"/fbo/{sid}/accept")
    assert r1.json()["replayed"] is False
    r2 = client.post(f"/fbo/{sid}/accept")
    assert r2.json()["replayed"] is True
    r = client.post(f"/fbs/orders/{oid}/cancel")
    assert r.status_code == 409
    with app_connect() as conn:
        assert_invariant(conn)


def test_parallel_receive(client):
    """9a. 8 потоков на один receive → ровно одно движение"""
    r = client.post("/transfers", json={"sku": "SKU-101", "from_warehouse_id": NORTH, "to_warehouse_id": SOUTH, "qty": 5})
    assert r.status_code == 201
    tid = r.json()["id"]
    client.post(f"/transfers/{tid}/send")

    def do_receive():
        return client.post(f"/transfers/{tid}/receive")

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda _: do_receive(), range(8)))
    statuses = [x.status_code for x in results]
    assert statuses.count(200) == 8
    replayed = [x.json()["replayed"] for x in results if x.status_code == 200]
    assert replayed.count(True) == 7
    with app_connect() as conn:
        movements = conn.execute(
            "SELECT count(*) AS n FROM movements WHERE op_type = 'transfer_receive' AND op_id = %s", (tid,)
        ).fetchone()["n"]
        assert movements == 1, f"Ожидалось 1 движение, получено {movements}"
        assert_invariant(conn)


def test_parallel_transfer_full_stock(client):
    """9b. два перемещения на весь остаток → одно проходит, второе 409"""
    with app_connect() as conn:
        pid = _get_product_id(conn, "SKU-101")
        total = _on_hand(conn, pid, NORTH)
    r1 = client.post("/transfers", json={"sku": "SKU-101", "from_warehouse_id": NORTH, "to_warehouse_id": SOUTH, "qty": total})
    r2 = client.post("/transfers", json={"sku": "SKU-101", "from_warehouse_id": NORTH, "to_warehouse_id": SOUTH, "qty": total})
    assert r1.status_code == 201 and r2.status_code == 201
    s1 = client.post(f"/transfers/{r1.json()['id']}/send")
    s2 = client.post(f"/transfers/{r2.json()['id']}/send")
    assert {s1.status_code, s2.status_code} == {200, 409}
    with app_connect() as conn:
        assert_invariant(conn)