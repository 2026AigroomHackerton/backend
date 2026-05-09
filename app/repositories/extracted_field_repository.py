"""
extracted_fields 테이블 접근 계층.

자동 채움 서비스가 한 문서에 대한 빈칸 후보를 INSERT/REPLACE 하고,
응답 빌더(`document_service.get_document` 등) 가 SELECT 해서 fields[] 로 노출한다.

스키마 (app/models.py:ExtractedField, 9.2.12):
    id           INTEGER PK autoincrement
    document_id  INTEGER FK documents.id
    label        TEXT     양식에서 검출한 라벨 (예: '거주지', '성명(한자)')
    field_type   TEXT     'text' | 'date' | 'number' | 'select' 등
    suggestion   TEXT     자동 채워질 후보 값 (프로필에서 가져온 값)
    confidence   REAL     매칭 신뢰도 0.0 ~ 1.0
    status       TEXT     'pending' | 'accepted' | 'rejected' | 'edited'
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Final, Iterator

BACKEND_DIR: Final[Path] = Path(__file__).resolve().parents[2]
DB_PATH: Final[Path] = BACKEND_DIR / "data" / "app.db"


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def list_fields_by_document(document_id: int) -> list[dict]:
    """문서별 추출 필드 목록을 id 오름차순으로 반환."""
    sql = (
        "SELECT id, document_id, label, field_type, suggestion, confidence, status "
        "FROM extracted_fields WHERE document_id = ? ORDER BY id ASC"
    )
    with _connect() as conn:
        rows = conn.execute(sql, (document_id,)).fetchall()
        return [dict(row) for row in rows]


def delete_fields_by_document(document_id: int) -> None:
    """자동 채움을 다시 실행하기 전 기존 행을 일괄 정리.

    재인덱싱 / 재업로드 시 stale 한 suggestion 이 누적되지 않도록 REPLACE 의미로 사용.
    사용자가 이미 accepted/edited 한 필드를 보존하는 정책은 후속 PR 에서 고려.
    """
    sql = "DELETE FROM extracted_fields WHERE document_id = ?"
    with _connect() as conn:
        conn.execute(sql, (document_id,))
        conn.commit()


def insert_fields(rows: list[dict]) -> None:
    """여러 행을 한 번에 INSERT.

    각 dict 는 다음 키를 가져야 한다:
        document_id, label, field_type, suggestion, confidence, status
    """
    if not rows:
        return
    sql = (
        "INSERT INTO extracted_fields "
        "(document_id, label, field_type, suggestion, confidence, status) "
        "VALUES (:document_id, :label, :field_type, :suggestion, :confidence, :status)"
    )
    with _connect() as conn:
        conn.executemany(sql, rows)
        conn.commit()
