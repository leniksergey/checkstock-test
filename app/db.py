import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ["DATABASE_URL"]

VIRTUAL_LOCATIONS = [
    ("external", "EXTERNAL"),
    ("transit_transfer", "TRANSIT_TRANSFER"),
    ("transit_fbo", "TRANSIT_FBO"),
    ("transit_client", "TRANSIT_CLIENT"),
    ("marketplace", "MARKETPLACE"),
]
PRODUCTS = [("SKU-101", "Органайзер"), ("SKU-102", "Контейнер"), ("SKU-103", "Корзина")]
FULFILLMENTS = ["Север", "Юг"]
# (sku, ФФ, количество) - тестовый приход из задания
RECEIPTS = [
    ("SKU-101", "Север", 10), ("SKU-101", "Юг", 6),
    ("SKU-102", "Север", 5),
    ("SKU-103", "Юг", 4),
]


def connect():
    """Новое соединение на запрос; транзакция коммитится при выходе из with."""
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db():
    with connect() as conn:
        conn.execute((Path(__file__).parent / "schema.sql").read_text(encoding="utf-8"))
        # seed целиком в одной транзакции и только на пустой базе: повторный запуск ничего не дублирует
        conn.execute("LOCK TABLE products IN EXCLUSIVE MODE")
        if conn.execute("SELECT count(*) AS n FROM products").fetchone()["n"]:
            return
        for kind, name in VIRTUAL_LOCATIONS:
            conn.execute("INSERT INTO locations (kind, name) VALUES (%s, %s)", (kind, name))
        for name in FULFILLMENTS:
            conn.execute("INSERT INTO locations (kind, name) VALUES ('ff', %s)", (name,))
        for sku, name in PRODUCTS:
            conn.execute("INSERT INTO products (sku, name) VALUES (%s, %s)", (sku, name))
        for sku, ff, qty in RECEIPTS:
            conn.execute(
                """INSERT INTO movements (product_id, qty, from_location_id, to_location_id, op_type)
                   SELECT p.id, %s, ext.id, ff.id, 'receipt'
                   FROM products p, locations ext, locations ff
                   WHERE p.sku = %s AND ext.kind = 'external' AND ff.name = %s""",
                (qty, sku, ff),
            )
