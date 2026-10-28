"""
Aplica un script SQL de database/scripts/ con psycopg2.

Alembic no funciona contra Supabase (la contraseña contiene '%' y configparser
la interpreta como interpolación), así que las migraciones se aplican así.
Los scripts son idempotentes (IF NOT EXISTS / CREATE OR REPLACE).

Usage:
    cd backend
    python scripts/apply_sql_migration.py ../database/scripts/016_invoices.sql
"""
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402


def connect():
    url = urlparse(settings.DATABASE_URL)
    return psycopg2.connect(
        host=url.hostname,
        port=url.port or 5432,
        dbname=url.path.lstrip("/"),
        user=unquote(url.username or ""),
        password=unquote(url.password or ""),
    )


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sql_path = Path(sys.argv[1])
    sql = sql_path.read_text(encoding="utf-8")

    conn = connect()
    try:
        with conn, conn.cursor() as cur:  # una sola transacción: todo o nada
            cur.execute(sql)
        print(f"OK: {sql_path.name} aplicado en {urlparse(settings.DATABASE_URL).hostname}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
