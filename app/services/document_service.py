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
    `documents` 테이블이 없으면 생성한다.

    모듈이 처음 import 될 때 한 번 호출되어, 첫 요청 이전에 스키마를 보장한다.
    `IF NOT EXISTS` 덕분에 여러 번 호출되어도 안전하다.
    """
    with sqlite3.connect(DB_PATH) as conn:
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
                created_at TEXT NOT NULL
            )
            """
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
