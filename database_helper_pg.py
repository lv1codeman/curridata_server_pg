# database_helper_pg.py
import os
from typing import Optional, Any, List, Dict, Union
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.engine import Engine, Connection

# =========================
# 自訂例外（保留你原本設計）
# =========================
class DatabaseError(Exception):
    """資料庫操作時的一般錯誤。"""
    pass


class UniqueConstraintError(DatabaseError):
    """資料庫唯一約束條件衝突錯誤。"""
    pass


# =========================
# PostgreSQL / Supabase 連線
# =========================

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL 環境變數未設定，"
        "請在本機 .env 或 Render Environment 中設定 PostgreSQL 連線字串"
    )

engine: Engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,    # ✅ Supabase 強烈建議
    pool_size=5,
    max_overflow=10,
    future=True,
)

# =========================
# Connection Context Manager
# =========================
@contextmanager
def DatabaseCursor() -> Connection:
    """
    提供一個 PostgreSQL Connection，模擬你原本的 DatabaseCursor 行為。
    在 with 區塊結束時會自動 commit / rollback。
    """
    conn: Connection = engine.connect()
    trans = conn.begin()
    try:
        yield conn
        trans.commit()
    except IntegrityError as ex:
        trans.rollback()
        # PostgreSQL 唯一約束違反 SQLSTATE = 23505
        if getattr(ex.orig, "pgcode", None) == "23505":
            raise UniqueConstraintError(f"Unique constraint violation: {ex}") from ex
        raise DatabaseError(f"Integrity error: {ex}") from ex
    except SQLAlchemyError as ex:
        trans.rollback()
        raise DatabaseError(f"Database operation failed: {ex}") from ex
    finally:
        conn.close()


# =========================
# execute_query（保留原有行為）
# =========================
def execute_query(
    sql: str,
    params: Optional[tuple] = None,
    fetch_one: bool = False
) -> Optional[Union[Dict[str, Any], List[Dict[str, Any]], int]]:
    """
    執行 SQL 查詢或命令，行為與你原本 MSSQL 版本一致：

    - SELECT（fetch_one=True） → dict 或 None
    - SELECT（fetch_one=False） → List[dict]
    - INSERT / UPDATE / DELETE → 影響筆數 (int)

    ⚠️ 注意：PostgreSQL 參數請使用 %s，不是 ?
    """
    with DatabaseCursor() as conn:
        stmt = text(sql)

        if params:
            result = conn.execute(stmt, params)
        else:
            result = conn.execute(stmt)

        sql_upper = sql.strip().upper()

        # ===== SELECT 查詢 =====
        if sql_upper.startswith("SELECT"):
            rows = result.mappings()

            if fetch_one:
                row = rows.first()
                return dict(row) if row else None

            return [dict(row) for row in rows]

        # ===== INSERT / UPDATE / DELETE =====
        return result.rowcount