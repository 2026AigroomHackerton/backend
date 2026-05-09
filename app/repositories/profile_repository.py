"""
프로필 도메인의 DB 접근 계층 (Repository).

`demo_profiles` 테이블 한 행(`user_id` 단위 1:1) 을 읽고/덮어쓴다.
나머지 도메인 규칙(직렬화, 검증, 자동 채움 트리거 등)은 service 계층의 책임이며,
본 모듈은 raw SQL 만 다룬다.

`document_repository` 의 sqlite3 + dict 패턴을 그대로 답습해 일관성을 유지한다.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Final, Iterator, Optional

# ---------------------------------------------------------------------------
# DB 경로 상수
# ---------------------------------------------------------------------------
# document_repository 와 동일 패턴 — 양방향 import 회피를 위해 의도적으로 중복 정의.
BACKEND_DIR: Final[Path] = Path(__file__).resolve().parents[2]
DB_PATH: Final[Path] = BACKEND_DIR / "data" / "app.db"


# 테이블에 실제로 존재하는 컬럼 (database.py 의 ALTER 와 동기).
# UPDATE 문 작성 시 값을 채우는 컬럼 화이트리스트로도 사용된다.
PROFILE_COLUMNS: Final[tuple[str, ...]] = (
    "name_ko",
    "name_en",
    "name_hanja",
    "phone",
    "email",
    "address",
    "rrn",
    "certifications",
    "occupation",
    "gender",
)


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """sqlite3 연결을 dict-row 모드로 yield."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def get_profile(user_id: int) -> Optional[dict]:
    """user_id 기준 프로필 단일 행을 dict 로 반환. 없으면 None."""
    sql = (
        "SELECT user_id, name_ko, name_en, name_hanja, phone, email, "
        "       address, rrn, certifications, occupation, gender "
        "FROM demo_profiles WHERE user_id = ?"
    )
    with _connect() as conn:
        row = conn.execute(sql, (user_id,)).fetchone()
        return dict(row) if row is not None else None


def upsert_profile(user_id: int, fields: dict) -> None:
    """프로필 행을 전체 덮어쓰기(UPSERT) 한다.

    `fields` 는 PROFILE_COLUMNS 의 부분 집합. 누락된 키는 NULL 로 들어간다(전체 교체 의미).
    PUT semantics 라서 호출자가 넘기지 않은 컬럼은 의도적으로 비워지는 게 맞다.
    """
    # ---- 컬럼/값 분리 (SQL 인젝션 방지를 위해 컬럼명은 화이트리스트로 검증) ----
    columns: list[str] = ["user_id"]
    values: list = [user_id]
    for col in PROFILE_COLUMNS:
        columns.append(col)
        values.append(fields.get(col))

    placeholders = ", ".join(["?"] * len(columns))
    column_list = ", ".join(columns)
    # SQLite 의 ON CONFLICT 구문으로 PK 충돌 시 동일 컬럼들을 새 값으로 덮어씀.
    update_clause = ", ".join(f"{col} = excluded.{col}" for col in PROFILE_COLUMNS)

    sql = (
        f"INSERT INTO demo_profiles ({column_list}) VALUES ({placeholders}) "
        f"ON CONFLICT(user_id) DO UPDATE SET {update_clause}"
    )

    with _connect() as conn:
        conn.execute(sql, tuple(values))
        conn.commit()
