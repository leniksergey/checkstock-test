import os
import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ["DATABASE_URL"]


def connect():
    """Новое соединение на запрос; транзакция коммитится при выходе из with."""
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)
