# database_helper_pg.py
import os
from typing import Optional, Any, List, Dict, Union

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.engine import Engine

from dotenv import load_dotenv
load_dotenv()

# =========================
# 自訂例外
# =========================
class DatabaseError(Exception):
    """資料庫操作時的一般錯誤"""
    pass


class UniqueConstraintError(DatabaseError):
    """唯一約束錯誤"""
    pass


# =========================
# DATABASE_URL
# =========================
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL 未設定")


# ✅ ✅ ✅ 🔥 重點：關閉 SQLAlchemy pool（交給 Supabase）
engine: Engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=0,        # ✅ 不保留連線
    max_overflow=0,     # ✅ 不允許額外連線
    future=True,
)


# =========================
# execute_query
# =========================
def execute_query(
    sql: str,
    params: Optional[dict] = None,
    fetch_one: bool = False
) -> Optional[Union[Dict[str, Any], List[Dict[str, Any]], int]]:
    """
    統一 SQL 執行工具

    - SELECT → dict / list
    - INSERT / UPDATE / DELETE → rowcount
    """

    try:
        # ✅ ✅ ✅ 🔥 自動 transaction，且每次用完關閉（最關鍵）
        with engine.begin() as conn:

            stmt = text(sql)

            result = conn.execute(stmt, params or {})

            sql_upper = sql.strip().upper()

            # ✅ SELECT
            if sql_upper.startswith("SELECT"):
                rows = result.mappings()

                if fetch_one:
                    row = rows.first()
                    return dict(row) if row else None

                return [dict(row) for row in rows]

            # ✅ INSERT / UPDATE / DELETE
            return result.rowcount

    except IntegrityError as ex:
        # PostgreSQL unique error code: 23505
        if getattr(ex.orig, "pgcode", None) == "23505":
            raise UniqueConstraintError(f"Unique constraint violation: {ex}")
        raise DatabaseError(f"Integrity error: {ex}")

    except SQLAlchemyError as ex:
        raise DatabaseError(f"Database error: {ex}")
