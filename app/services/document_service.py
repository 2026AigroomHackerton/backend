"""
문서 업로드 서비스 모듈.

이 모듈은 라우터로부터 전달받은 업로드 파일을 처리하는 핵심 비즈니스 로직을 담당한다.
구체적으로 다음 작업을 수행한다.
    1. 업로드된 파일의 형식/크기 검증
    2. 원본 파일을 로컬 디스크(`backend/uploads/originals/`)에 안전한 이름으로 저장
    3. SQLite DB(`backend/data/app.db`)의 `documents` 테이블에 메타데이터 레코드 생성
    4. 생성된 문서 정보를 dict 형태로 반환

해커톤 MVP 단계이므로 SQLAlchemy 등 ORM 없이 표준 라이브러리 sqlite3만 사용한다.
또한 OCR/텍스트 추출 등 실제 문서 처리 로직은 본 모듈에서 다루지 않는다.
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Iterator

from fastapi import UploadFile

# ---------------------------------------------------------------------------
# 경로 상수 정의
# ---------------------------------------------------------------------------
# `__file__` = backend/app/services/document_service.py
# parents[0] = services/, parents[1] = app/, parents[2] = backend/
# 즉 BACKEND_DIR 은 프로젝트 루트(`backend/`)를 가리킨다.
BACKEND_DIR: Final[Path] = Path(__file__).resolve().parents[2]

# 사용자가 업로드한 "원본" 파일이 저장될 디렉토리.
# 명세서에 따라 `/uploads/originals` 경로를 사용한다.
UPLOAD_DIR: Final[Path] = BACKEND_DIR / "uploads" / "originals"

# SQLite DB 파일이 위치할 디렉토리 및 파일 경로.
DATA_DIR: Final[Path] = BACKEND_DIR / "data"
DB_PATH: Final[Path] = DATA_DIR / "app.db"

# ---------------------------------------------------------------------------
# 업로드 정책 상수
# ---------------------------------------------------------------------------
# 허용 확장자 목록.
#   - `.hwpx`, `.hwp`: 한글 문서 (명세서 "한글 파일" 요구사항)
#   - `.png`, `.jpg`, `.jpeg`: 이미지 문서 (OCR 대상이 될 사진/스캔본)
# frozenset 으로 선언하여 실수로 변경되지 않도록 보호한다.
ALLOWED_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {".hwpx", ".hwp", ".png", ".jpg", ".jpeg"}
)

# 업로드 가능한 최대 파일 크기 (바이트). 100MB.
# 모바일에서 찍은 고해상도 이미지가 들어올 수 있으므로 여유 있게 잡는다.
MAX_FILE_SIZE: Final[int] = 100 * 1024 * 1024

# ---------------------------------------------------------------------------
# 모듈 import 시점에 디렉토리 사전 생성
# ---------------------------------------------------------------------------
# 첫 업로드 요청 때 디렉토리가 없어 실패하는 일을 방지한다.
# `parents=True` 로 중간 디렉토리도 함께 생성하고,
# `exist_ok=True` 로 이미 존재해도 오류를 내지 않는다.
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 도메인 예외 정의
# ---------------------------------------------------------------------------
# 라우터 계층에서 이 예외들을 잡아 적절한 HTTP 상태 코드로 변환한다.
# 비즈니스 의미를 분리하기 위해 ValueError 를 상속한 별도 클래스로 둔다.
class UnsupportedFileTypeError(ValueError):
    """허용되지 않은 파일 확장자가 업로드되었을 때 발생."""


class EmptyFileError(ValueError):
    """파일이 비어있을 때 발생 (네트워크 오류나 잘못된 요청 방어용)."""


class FileTooLargeError(ValueError):
    """파일 크기가 MAX_FILE_SIZE 를 초과했을 때 발생."""


# ---------------------------------------------------------------------------
# DB 초기화
# ---------------------------------------------------------------------------
def _init_db() -> None:
    """
    프로젝트에서 사용하는 모든 테이블이 없으면 생성하고, 기존 테이블에는 누락된 컬럼을 보강한다.

    동작 흐름:
        1. `documents` 테이블을 신규 풀스키마로 생성 (이미 있으면 NO-OP).
        2. 이미 존재하는 `documents` 의 컬럼 목록을 조회해, 누락된 컬럼만 ALTER 로 추가.
           (구버전 DB 와 호환성 유지를 위한 idempotent 마이그레이션)
        3. `document_texts` 테이블을 신규 생성 (없을 때만).

    SQLite 의 ALTER TABLE ADD COLUMN 은 NOT NULL 컬럼일 경우 DEFAULT 가 필수다.
    따라서 NOT NULL 로 추가하는 컬럼(source_type, parse_status)에는 DEFAULT 값을 명시한다.
    그 외(title, folder_id, updated_at, deleted_at)는 nullable 이라 DEFAULT 없이도 안전하다.

    이 함수는 모듈 import 시점에 1회 호출되며, 여러 번 호출되어도 부작용이 없도록 설계되었다.
    """
    with sqlite3.connect(DB_PATH) as conn:
        # ---- 1) documents 풀스키마 (신규 환경용) ----
        # 이미 테이블이 있으면 IF NOT EXISTS 가 NO-OP 이므로,
        # 구버전 스키마는 아래 ALTER 단계에서 보강된다.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                original_filename TEXT NOT NULL,
                stored_filename TEXT NOT NULL UNIQUE,
                file_path TEXT NOT NULL,
                file_extension TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                content_type TEXT,
                title TEXT,
                source_type TEXT NOT NULL DEFAULT 'upload',
                parse_status TEXT NOT NULL DEFAULT 'pending',
                folder_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                deleted_at TEXT
            )
            """
        )

        # ---- 2) 구버전 documents 에 컬럼 보강 ----
        # PRAGMA table_info 로 현재 컬럼 목록을 가져와 누락된 항목만 추가한다.
        existing_columns = {
            row[1]  # PRAGMA table_info 결과의 두 번째 필드가 컬럼 이름
            for row in conn.execute("PRAGMA table_info(documents)").fetchall()
        }
        column_specs = (
            ("title", "TEXT"),
            ("source_type", "TEXT NOT NULL DEFAULT 'upload'"),
            ("parse_status", "TEXT NOT NULL DEFAULT 'pending'"),
            ("folder_id", "INTEGER"),
            ("updated_at", "TEXT"),
            ("deleted_at", "TEXT"),
        )
        for column_name, column_definition in column_specs:
            if column_name not in existing_columns:
                # f-string 사용은 컬럼 이름/정의가 모두 코드 내부 상수라 SQL 인젝션 위험 없음.
                conn.execute(
                    f"ALTER TABLE documents ADD COLUMN {column_name} {column_definition}"
                )

        # ---- 3) document_texts 신규 테이블 ----
        # OCR/AI 처리 결과를 분리해서 저장하는 테이블.
        # documents 와 1:N 관계 (text_version 으로 버전 관리)
        # FOREIGN KEY 는 SQLite 기본 enforcement 가 OFF 이지만 향후 활성화 대비 명시.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS document_texts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                extracted_text TEXT,
                cleaned_text TEXT,
                summary TEXT,
                keywords TEXT,
                text_version INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT,
                FOREIGN KEY (document_id) REFERENCES documents(id)
            )
            """
        )

        # `document_id` 로 자주 조회될 가능성이 높으므로 인덱스 생성.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_document_texts_document_id "
            "ON document_texts(document_id)"
        )

        # ---- 4) document_versions 신규 테이블 ----
        # 문서 텍스트가 변경될 때마다 한 행씩 누적되는 버전 이력 테이블.
        # version_no 는 해당 document_id 안에서만 의미 있는 단조 증가 정수.
        # text_snapshot 은 그 시점의 텍스트 전문(스냅샷)을 보관해 롤백/diff 용도로 쓴다.
        # created_by 는 누가 수정했는지 (현재는 데모 사용자 1).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS document_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                version_no INTEGER NOT NULL,
                text_snapshot TEXT,
                created_by INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY (document_id) REFERENCES documents(id)
            )
            """
        )

        # 가장 최근 version_no 를 빠르게 찾기 위해 (document_id, version_no) 복합 인덱스 추가.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_document_versions_doc_ver "
            "ON document_versions(document_id, version_no)"
        )

        conn.commit()


# 모듈 import 시점에 즉시 테이블 생성을 시도한다.
_init_db()


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """
    DB 연결을 컨텍스트 매니저로 감싸는 헬퍼.

    `with _connect() as conn:` 형식으로 사용하면
    블록을 빠져나갈 때 항상 close() 가 호출되므로 누수가 방지된다.
    `row_factory = sqlite3.Row` 설정으로, 결과 행을 dict 처럼 다룰 수 있다.

    반환 타입은 `Iterator[sqlite3.Connection]` 으로 명시한다.
    `@contextmanager` 가 붙은 함수는 내부적으로 yield 하는 제너레이터이며,
    데코레이터가 이를 컨텍스트 매니저로 변환해준다.
    따라서 함수 자체의 시그니처는 "Connection 을 yield 하는 제너레이터"여야 하고,
    `with` 블록 안에서 받는 값(`as conn`)이 sqlite3.Connection 으로 올바르게 추론된다.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 메인 비즈니스 함수
# ---------------------------------------------------------------------------
async def upload_document(file: UploadFile, user_id: int) -> dict:
    """
    업로드된 파일을 검증·저장하고 DB 레코드를 만든 뒤 메타데이터를 반환한다.

    Args:
        file: FastAPI 가 멀티파트 폼에서 추출한 UploadFile.
        user_id: 데모 사용자 식별자 (해커톤 MVP 에서는 라우터에서 1 을 고정 주입).

    Returns:
        생성된 문서 메타데이터 dict. 라우터는 이를 그대로 응답 `data` 필드에 넣는다.

    Raises:
        UnsupportedFileTypeError: 확장자가 허용 목록에 없을 때.
        EmptyFileError: 파일 크기가 0 일 때.
        FileTooLargeError: 파일 크기가 MAX_FILE_SIZE 초과일 때.
    """

    # ---- 1단계: 파일명/확장자 검증 ----
    # `file.filename` 은 클라이언트가 보낸 원본 이름. 누락 시 'unnamed' 로 대체.
    original_filename = file.filename or "unnamed"
    # `Path(...).suffix` 는 ".hwpx" 처럼 점을 포함한 확장자를 반환.
    # 대소문자 무시 비교를 위해 lower() 적용.
    extension = Path(original_filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"지원하지 않는 파일 형식입니다: {extension or '확장자 없음'}"
        )

    # ---- 2단계: 파일 본문 읽기 + 크기 검증 ----
    # UploadFile 은 비동기 파일 인터페이스이므로 await 로 읽는다.
    # MVP 라 메모리에 통째로 올린다. 파일 크기가 커질 미래에는 스트리밍 저장이 필요할 수 있다.
    contents = await file.read()
    file_size = len(contents)
    if file_size == 0:
        raise EmptyFileError("빈 파일은 업로드할 수 없습니다.")
    if file_size > MAX_FILE_SIZE:
        raise FileTooLargeError(
            f"파일 크기({file_size} 바이트)가 허용 한도({MAX_FILE_SIZE} 바이트)를 초과했습니다."
        )

    # ---- 3단계: 안전한 저장 파일명 생성 ----
    # 클라이언트가 보낸 원본 이름을 그대로 디스크에 쓰면 다음 위험이 있다.
    #   - 동일 파일명 충돌 → 덮어쓰기로 데이터 유실
    #   - 경로 조작 (e.g. "../etc/passwd") 으로 디렉토리 탈출
    # 따라서 UUID4 의 hex 표현(32자리) + 원본 확장자로 새 파일명을 만든다.
    # 원본 파일명은 DB 의 original_filename 컬럼에 별도로 보존된다.
    stored_filename = f"{uuid.uuid4().hex}{extension}"
    save_path = UPLOAD_DIR / stored_filename
    # write_bytes 는 파일 핸들을 자동으로 열고 닫아주므로 컴팩트하다.
    save_path.write_bytes(contents)

    # ---- 4단계: DB 레코드 생성 ----
    # file_path 는 BACKEND_DIR 기준 상대 경로 문자열로 저장.
    # 절대 경로를 저장하면 머신 이전 시 깨지므로, 이식성 위해 상대 경로 채택.
    # `as_posix()` 로 OS 무관하게 슬래시 형식으로 통일.
    relative_path = save_path.relative_to(BACKEND_DIR).as_posix()

    # ISO 8601 + UTC 타임존으로 생성 시각 기록 → 클라이언트에서 파싱 용이.
    created_at = datetime.now(timezone.utc).isoformat()

    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO documents (
                user_id, original_filename, stored_filename, file_path,
                file_extension, file_size, content_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                original_filename,
                stored_filename,
                relative_path,
                extension,
                file_size,
                file.content_type,
                created_at,
            ),
        )
        conn.commit()
        # AUTOINCREMENT 로 발급된 새 PK.
        # sqlite3 타입 스텁상 lastrowid 는 `int | None` 이라 그대로 쓰면
        # Pylance 가 응답 dict 의 id 필드 타입을 'int | None' 으로 추론해 경고한다.
        # INSERT 직후엔 None 이 나올 수 없으므로 assert 로 타입을 좁힌다.
        assert cursor.lastrowid is not None, "INSERT 직후 lastrowid 는 항상 존재해야 합니다."
        document_id = cursor.lastrowid

    # ---- 5단계: 응답용 메타데이터 반환 ----
    # 라우터는 이 dict 를 `{"success": True, "data": <dict>}` 로 감싸 응답한다.
    return {
        "id": document_id,
        "user_id": user_id,
        "original_filename": original_filename,
        "stored_filename": stored_filename,
        "file_path": relative_path,
        "file_extension": extension,
        "file_size": file_size,
        "content_type": file.content_type,
        "created_at": created_at,
    }


# ---------------------------------------------------------------------------
# 조회 관련 도메인 예외
# ---------------------------------------------------------------------------
class DocumentNotFoundError(LookupError):
    """요청한 문서가 존재하지 않거나 다른 사용자의 것이거나 soft-delete 된 경우 발생."""


# ---------------------------------------------------------------------------
# 조회 서비스 함수
# ---------------------------------------------------------------------------
# 라우터는 아래 함수를 호출하고, 함수는 다시 repository 계층(`document_repository`) 을 호출한다.
# DB 쿼리 자체는 본 모듈이 아닌 repository 에서만 수행된다 (아키텍처 규칙).
# 본 함수들은 DB 결과(raw row dict)를 명세서가 요구하는 응답 필드로 매핑·정제한다.
#
# TODO(repository import): 순환 import 방지를 위해 함수 내부에서 지연 import 한다.
# 모듈 최상단에서 import 하면 repository 가 service 의 다른 심볼을 참조할 때 충돌 가능.


def list_documents(user_id: int) -> list[dict]:
    """
    특정 사용자의 활성(soft-delete 되지 않은) 문서 목록을 명세 응답 필드로 정제하여 반환한다.

    Args:
        user_id: 데모 사용자 식별자.

    Returns:
        명세서가 요구하는 필드로만 구성된 dict 들의 리스트.
        필드: id, title, source_type, file_type, parse_status, created_at, updated_at
    """
    from app.repositories import document_repository  # 지연 import

    rows = document_repository.list_active_documents_by_user(user_id=user_id)
    # DB 컬럼명(`file_extension`)을 응답 필드명(`file_type`)으로 매핑한다.
    # 응답 스펙은 추후 file_type 의 의미가 확장될 수 있으나(MIME 등),
    # 현재 단계에선 확장자 문자열을 그대로 노출.
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "source_type": row["source_type"],
            "file_type": row["file_extension"],
            "parse_status": row["parse_status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def get_document(document_id: int, user_id: int) -> dict:
    """
    단일 문서를 조회하여, document_texts 의 extracted_text 와 함께 반환한다.

    Args:
        document_id: 조회 대상 문서의 PK.
        user_id: 데모 사용자 식별자 (소유권 검증용).

    Returns:
        명세 응답 필드 + extracted_text(없으면 None) 가 포함된 dict.

    Raises:
        DocumentNotFoundError: 해당 id 의 문서가 없거나, 다른 사용자의 것이거나, soft-delete 된 경우.
    """
    from app.repositories import document_repository  # 지연 import

    row = document_repository.get_document_with_latest_text(
        document_id=document_id, user_id=user_id
    )
    if row is None:
        raise DocumentNotFoundError(
            f"문서 #{document_id} 를 찾을 수 없습니다."
        )

    # repository 가 documents + document_texts 조인 결과를 평탄화한 dict 를 돌려주므로,
    # 여기서는 응답 스키마에 맞게 필요한 필드만 골라낸다.
    return {
        "id": row["id"],
        "title": row["title"],
        "source_type": row["source_type"],
        "file_type": row["file_extension"],
        "parse_status": row["parse_status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        # document_texts 가 없으면 None 으로 통일 (명세 요구사항).
        "extracted_text": row.get("extracted_text"),
    }


# ---------------------------------------------------------------------------
# 텍스트 수정 서비스 함수 (PUT /api/documents/{id}/text)
# ---------------------------------------------------------------------------
def update_document_text(
    document_id: int,
    edited_text: str,
    user_id: int,
    created_by: int,
) -> dict:
    """
    문서 본문 텍스트를 수정하고, 새 버전 이력을 기록한 뒤 결과를 반환한다.

    처리 순서 (명세 정의):
        1) document_id + user_id 로 문서 존재 검증, 없으면 DocumentNotFoundError.
        2) document_versions 의 max(version_no) + 1 로 새 version_no 계산.
        3) document_texts 가 이미 있으면 UPDATE, 없으면 INSERT (text_version 도 함께 동기화).
        4) document_versions 에 새 버전 이력 한 행 INSERT.
        5) documents.updated_at 갱신.

    각 단계는 repository 함수 한 번 호출에 대응하여, 본 함수는 순서·분기 로직만 담당한다.
    SQLite 단일 프로세스 사용 환경이므로 단계 간 분리된 트랜잭션이라도 race 위험이 거의 없다.
    (TODO: 멀티 프로세스/병행 쓰기 환경 대비 시 단일 트랜잭션으로 묶어야 함.)

    Args:
        document_id: 수정할 문서 PK.
        edited_text: 새 본문 (빈 문자열도 허용 — 의도적 비우기).
        user_id: 소유권 검증용 사용자 식별자.
        created_by: 버전 이력에 기록할 수정자 식별자.

    Returns:
        명세 응답 필드 dict:
            - document_text_id: document_texts 행의 PK
            - version_id: document_versions 행의 PK
            - version_no: 새로 부여된 버전 번호
            - updated_at: 갱신 시각 (ISO-8601 UTC)

    Raises:
        DocumentNotFoundError: 문서가 없거나, 소유권 불일치, 또는 soft-delete 상태.
    """
    from app.repositories import document_repository  # 지연 import (순환 방지)

    # ---- 1) 문서 존재/소유권 검증 ----
    if not document_repository.document_exists_for_user(
        document_id=document_id, user_id=user_id
    ):
        raise DocumentNotFoundError(
            f"문서 #{document_id} 를 찾을 수 없습니다."
        )

    # ---- 2) 새 version_no 계산 ----
    next_version_no = (
        document_repository.get_max_version_no(document_id=document_id) + 1
    )

    # ---- 공통 시각 ----
    # 모든 쓰기에 같은 시각을 사용하면 후속 디버깅이 쉽다 (한 번에 묶인 작업임이 자명).
    now_iso = datetime.now(timezone.utc).isoformat()

    # ---- 3) document_texts UPSERT ----
    existing_text_id = document_repository.find_document_text_id(
        document_id=document_id
    )
    if existing_text_id is None:
        # 텍스트 행이 아직 없으면 새로 INSERT.
        document_text_id = document_repository.insert_document_text(
            document_id=document_id,
            extracted_text=edited_text,
            text_version=next_version_no,
            updated_at=now_iso,
        )
    else:
        # 이미 있으면 UPDATE 만 수행하고 동일 PK 를 그대로 응답에 사용.
        document_repository.update_document_text(
            text_id=existing_text_id,
            extracted_text=edited_text,
            text_version=next_version_no,
            updated_at=now_iso,
        )
        document_text_id = existing_text_id

    # ---- 4) 버전 이력 추가 ----
    version_id = document_repository.insert_document_version(
        document_id=document_id,
        version_no=next_version_no,
        text_snapshot=edited_text,
        created_by=created_by,
        created_at=now_iso,
    )

    # ---- 5) documents.updated_at 갱신 ----
    document_repository.update_document_updated_at(
        document_id=document_id, updated_at=now_iso
    )

    # ---- 6) 응답 데이터 구성 ----
    return {
        "document_text_id": document_text_id,
        "version_id": version_id,
        "version_no": next_version_no,
        "updated_at": now_iso,
    }
