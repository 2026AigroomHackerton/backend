"""
문서 도메인의 DB 접근 계층 (Repository).

이 모듈은 SQLite DB 와 직접 통신하는 SQL 쿼리를 모아둔 곳이다.
프로젝트의 아키텍처 규칙은 다음과 같다.
    router  →  service  →  repository  →  DB
즉, DB 쿼리는 본 repository 모듈 안에서만 작성하며, 상위 레이어(service/router)는
파이썬 dict 형태의 결과만 받아 가공한다.

본 모듈의 책임:
    - documents / document_texts 테이블 SELECT 쿼리
    - 결과 행을 sqlite3.Row → dict 로 변환
    - 도메인 비즈니스 로직(검증/예외 변환 등) 은 service 계층에 위임 (여기서는 안 한다)

스키마 정의(`CREATE TABLE`) 자체는 `services/document_service.py::_init_db()` 가 담당한다.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Final, Iterator, Optional

# ---------------------------------------------------------------------------
# DB 경로 상수
# ---------------------------------------------------------------------------
# 본 파일: backend/app/repositories/document_repository.py
# parents[0] = repositories/, parents[1] = app/, parents[2] = backend/
# 즉 BACKEND_DIR 은 프로젝트 루트(`backend/`)이며, DB 는 그 하위 `data/app.db`.
#
# service 모듈에도 동일 상수가 있지만, 양방향 import 를 피하기 위해 의도적으로 중복 정의한다.
# (둘 다 같은 SQLite 파일을 가리키므로 충돌 없음.)
BACKEND_DIR: Final[Path] = Path(__file__).resolve().parents[2]
DB_PATH: Final[Path] = BACKEND_DIR / "data" / "app.db"


# ---------------------------------------------------------------------------
# DB 연결 헬퍼
# ---------------------------------------------------------------------------
@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """
    SQLite 연결을 컨텍스트 매니저로 제공한다.

    `row_factory = sqlite3.Row` 설정 덕분에 fetch 결과를 인덱스 + 컬럼명 양쪽으로 접근 가능.
    `with _connect() as conn:` 종료 시 자동으로 close() 호출.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 조회 쿼리
# ---------------------------------------------------------------------------
def list_active_documents_by_user(user_id: int) -> list[dict]:
    """
    특정 사용자의 활성(soft-delete 되지 않은) 문서 목록을 created_at 내림차순으로 반환한다.

    "활성" = `deleted_at IS NULL` (soft-delete 되지 않은 행)

    Args:
        user_id: documents.user_id 와 매칭할 사용자 식별자.

    Returns:
        sqlite3.Row 를 dict 로 변환한 리스트.
        각 dict 는 documents 테이블의 모든 컬럼을 그대로 담고 있다.
        (응답 필드 매핑/필터링은 service 계층의 책임)
    """
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                id, user_id, original_filename, stored_filename,
                file_path, file_extension, file_size, content_type,
                title, source_type, parse_status, folder_id,
                created_at, updated_at, deleted_at
            FROM documents
            WHERE user_id = ?
              AND deleted_at IS NULL
            ORDER BY created_at DESC, id DESC
            """,
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_document_with_latest_text(
    document_id: int, user_id: int
) -> Optional[dict]:
    """
    단일 문서를 조회하고, 가장 최근 버전의 extracted_text 를 함께 가져와 평탄화된 dict 로 반환한다.

    매칭 조건:
        - documents.id = document_id
        - documents.user_id = user_id  (소유권 검증)
        - documents.deleted_at IS NULL (soft-delete 되지 않음)

    document_texts 가 다중 버전을 보관할 수 있으므로 `text_version` 이 가장 큰 행을 선택한다.
    (없으면 LEFT JOIN 결과의 extracted_text 가 NULL.)

    Args:
        document_id: documents.id
        user_id: documents.user_id

    Returns:
        해당 문서가 있으면 dict (documents 모든 컬럼 + extracted_text),
        없거나 다른 사용자/삭제된 경우 None.
    """
    with _connect() as conn:
        # 1) documents 단일 행 조회 (필요 컬럼 모두 가져온다).
        document_row = conn.execute(
            """
            SELECT
                id, user_id, original_filename, stored_filename,
                file_path, file_extension, file_size, content_type,
                title, source_type, parse_status, folder_id,
                created_at, updated_at, deleted_at
            FROM documents
            WHERE id = ?
              AND user_id = ?
              AND deleted_at IS NULL
            """,
            (document_id, user_id),
        ).fetchone()

        if document_row is None:
            # 명세상 service 가 None 을 받아 NotFound 예외로 변환한다.
            return None

        # 2) document_texts 의 가장 최신(text_version 큰) extracted_text 조회.
        # 한 문서에 여러 버전이 있을 수 있으므로 정렬 후 LIMIT 1.
        # JOIN 대신 별도 쿼리로 분리한 이유: SQLite 가 GROUP BY 없는 윈도우 함수에 약하고,
        # 두 쿼리로 분리하는 편이 가독성·유지보수 측면에서 더 명확하기 때문.
        text_row = conn.execute(
            """
            SELECT extracted_text
            FROM document_texts
            WHERE document_id = ?
            ORDER BY text_version DESC, id DESC
            LIMIT 1
            """,
            (document_id,),
        ).fetchone()

        # 3) documents 결과 dict 에 extracted_text 를 추가하여 평탄화.
        result = dict(document_row)
        # text_row 가 None 이면 None 을 담는다 (명세: "없으면 null").
        result["extracted_text"] = (
            text_row["extracted_text"] if text_row is not None else None
        )
        return result
