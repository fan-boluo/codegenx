"""Project database manager — create database and list tables."""

from __future__ import annotations

import pymysql
from pymysql.cursors import DictCursor

from shared.config.config import get_settings


settings = get_settings()


def _get_connection(database: str | None = None):
    db = database or settings.mysql_db
    return pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=db,
        charset=settings.mysql_charset,
        cursorclass=DictCursor,
    )


def create_project_database(db_name: str) -> None:
    conn = _get_connection(database=None)
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        conn.commit()
    finally:
        conn.close()


def list_database_tables(db_name: str) -> list[dict]:
    conn = _get_connection(database=db_name)
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES")
            rows = cur.fetchall()
            tables = []
            for row in rows:
                table_name = list(row.values())[0]
                tables.append({"name": table_name, "type": "table"})
            return tables
    finally:
        conn.close()
