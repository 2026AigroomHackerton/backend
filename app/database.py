"""
SQLAlchemy 2.x DB 연결 + 세션 + 부팅 시 스키마 초기화.

[제공]
    - engine          : SQLAlchemy Engine (SQLite 단일 파일)
    - SessionLocal    : 요청 단위 Session 팩토리
    - get_db()        : FastAPI Depends 용 제너레이터 (yield Session)
    - Base            : models.py 의 DeclarativeBase 재노출 (편의)
    - init_db()       : 모든 테이블 생성 + 누락 컬럼 ALTER 보강

[부팅 정책]
    본 모듈이 import 되는 시점에 즉시 init_db() 가 1회 실행된다.
    - main.py 의 라우터 import 흐름이 본 모듈을 흡수하므로 별도 lifespan 훅 불요.
    - storage.py / 기타 라우터의 `from app.database import get_db` 가 import 시
      자동으로 스키마 보장이 끝나 있다.

[기존 raw SQL 코드와의 공존]
    - services/document_service.py::_init_db() 가 documents/document_texts/
      document_versions 를 raw CREATE TABLE 로 미리 만든다 (모듈 import 시).
    - 본 모듈의 init_db() 는 그 위에:
        1) Base.metadata.create_all(bind=engine)  → 누락된 12개 테이블 생성
        2) ALTER TABLE documents ADD COLUMN owner_type/owner_id/file_type
        3) ALTER TABLE document_versions ADD COLUMN file_path
      를 멱등하게 수행한다.
    - 그 결과 BE1/BE2 어느 경로(raw sqlite3 / SQLAlchemy ORM) 로도 동일 DB 를 읽는다.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

# ORM Base — 모든 모델 클래스가 여기에 등록된다. import 시점에 metadata 가 채워짐.
from app.models import Base

logger = logging.getLogger(__name__)


# =============================================================================
# DB 파일 경로
# =============================================================================
# 본 파일: backend/app/database.py
# parents[0] = app/, parents[1] = backend/
# => DATA_DIR = backend/data/, DB_PATH = backend/data/app.db
BACKEND_DIR: Path = Path(__file__).resolve().parents[1]
DATA_DIR: Path = BACKEND_DIR / "data"
DB_PATH: Path = DATA_DIR / "app.db"

# 디렉토리 보장 (이미 있으면 NO-OP).
DATA_DIR.mkdir(parents=True, exist_ok=True)


# =============================================================================
# Engine + SessionLocal
# =============================================================================
# - sqlite+pysqlite : Python 표준 sqlite3 드라이버.
# - check_same_thread=False : FastAPI 가 다중 스레드에서 세션을 생성/사용할 수
#                             있으므로 SQLite 의 스레드 검사를 끈다.
# - future=True : SQLAlchemy 2.x 스타일 활성화 (이미 2.x 라 사실상 default).
DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    future=True,
)

# 요청마다 한 개씩 만들어 쓰는 Session 팩토리.
# - autoflush=False : INSERT 후 자동 flush 를 끄고, commit 시점만 명시적으로.
# - autocommit=False: 트랜잭션 경계를 명시적으로 관리.
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    future=True,
)


# =============================================================================
# get_db — FastAPI Depends 용 제너레이터
# =============================================================================
def get_db() -> Iterator[Session]:
    """요청 시작에 Session 발급, 종료 시 close.

    사용 예:
        @router.post("/x")
        def handler(..., db: Session = Depends(get_db)): ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =============================================================================
# init_db — 부팅 시 1회 실행
# =============================================================================
def init_db() -> None:
    """모든 ORM 테이블을 생성하고, 기존 테이블에는 누락 컬럼만 ALTER 로 보강한다.

    멱등 동작:
        - create_all 은 이미 있는 테이블을 건너뜀.
        - ALTER 는 PRAGMA table_info 결과를 보고 누락 컬럼만 추가.
    """
    # ---- 1) 모든 ORM 테이블 생성 (없는 것만) -----------------------------------
    Base.metadata.create_all(bind=engine)

    # ---- 2) 기존 테이블 누락 컬럼 보강 (멱등 ALTER) ---------------------------
    # documents : 명세 spec 컬럼(owner_type/owner_id/file_type) 보강.
    # document_versions : 명세의 file_path 컬럼 보강.
    inspector = inspect(engine)

    def _existing_columns(table_name: str) -> set[str]:
        """테이블의 컬럼명 집합을 반환. 테이블이 없으면 빈 set."""
        if not inspector.has_table(table_name):
            return set()
        return {col["name"] for col in inspector.get_columns(table_name)}

    # documents 보강
    documents_cols = _existing_columns("documents")
    documents_alters: list[tuple[str, str]] = [
        ("owner_type", "TEXT"),
        ("owner_id", "INTEGER"),
        ("file_type", "TEXT"),
    ]

    # document_versions 보강
    document_versions_cols = _existing_columns("document_versions")
    document_versions_alters: list[tuple[str, str]] = [
        ("file_path", "TEXT"),
    ]

    # 실제 ALTER 실행. 한 트랜잭션으로 묶어 실패 시 롤백.
    with engine.begin() as conn:
        for col_name, col_type in documents_alters:
            if col_name not in documents_cols:
                logger.info("ALTER TABLE documents ADD COLUMN %s", col_name)
                conn.execute(
                    text(f"ALTER TABLE documents ADD COLUMN {col_name} {col_type}")
                )

        for col_name, col_type in document_versions_alters:
            if col_name not in document_versions_cols:
                logger.info("ALTER TABLE document_versions ADD COLUMN %s", col_name)
                conn.execute(
                    text(
                        f"ALTER TABLE document_versions ADD COLUMN {col_name} {col_type}"
                    )
                )

    logger.info("init_db 완료: %d개 테이블 등록 (DB=%s)", len(Base.metadata.tables), DB_PATH)


# =============================================================================
# 모듈 import 시점 부팅 — 1회 자동 실행.
# (services/document_service.py::_init_db() 와 동일한 패턴.)
# =============================================================================
init_db()


# =============================================================================
# 외부 export 목록.
# =============================================================================
__all__ = [
    "Base",
    "engine",
    "SessionLocal",
    "get_db",
    "init_db",
    "DATABASE_URL",
    "DB_PATH",
]
