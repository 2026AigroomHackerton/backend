# [백엔드2 담당] 수정 허용 파일 - feature/backend-ocr-voice-storage 브랜치
"""External Storage Router.

엔드포인트:
    GET  /api/storage/providers           : 임포트 가능한 provider 목록.
    GET  /api/connectors                  : OAuth 커넥터 연결 상태.
    POST /api/connectors/mock-import      : 샘플 문서 임포트 (실제 DB INSERT 자리).
    POST /api/connectors/google-drive/import : OAuth 미구현 stub (HTTP 501).
    POST /api/connectors/notion/import    : OAuth 미구현 stub (HTTP 501).

이 모듈의 책임은 "HTTP 요청을 받아 서비스에 위임하고, 결과를 공통 envelope
({success, data, message, error}) 로 감싸 반환" 까지로 한정한다. 비즈니스 로직은
모두 services/storage_service.py 의 StorageService 클래스가 담당한다.

[라우팅 설계 메모]
    main.py 가 `app.include_router(storage_router.router)` 한 번만 호출하므로,
    `/api/storage/*` 와 `/api/connectors/*` 두 경로 prefix 를 동일 라우터에서
    제공해야 한다. 따라서 APIRouter 에 prefix 를 두지 않고, 각 핸들러에 전체
    경로를 직접 명시한다.
"""

# 타입 힌트 지연 평가.
from __future__ import annotations

# 응답 dict 의 값 타입 유연화.
from typing import Any

# FastAPI 핵심.
#  - APIRouter : 엔드포인트 묶음.
#  - Depends   : 의존성 주입 (DB 세션 전달용).
#  - status    : HTTP 상태 코드 상수 (HTTP_400_BAD_REQUEST, HTTP_501_NOT_IMPLEMENTED 등).
from fastapi import APIRouter, Depends, status

# JSONResponse: 비-200 응답 본문을 공통 envelope 형식으로 직접 만들 때 사용.
# (HTTPException 은 {"detail": "..."} 형식이라 envelope 와 충돌)
from fastapi.responses import JSONResponse

# Pydantic — POST body 검증.
#  - BaseModel : 요청/응답 모델 베이스 클래스.
#  - Field     : 필드 메타데이터(필수/기본값/설명) 지정.
from pydantic import BaseModel, Field

# 비즈니스 로직 클래스.
from app.services.storage_service import ExternalImportError, StorageService


# -----------------------------------------------------------------------------
# DB 의존성 주입
# -----------------------------------------------------------------------------
# 명세 [DB 의존성 주입] : "db: Session = Depends(get_db) 를 mock-import 에 주입.
#   get_db 없으면 TODO 주석만 남기고 db 파라미터 없이 임시 구현".
#
# 본 PR 은 storage.py / storage_service.py 외 파일을 못 건드리므로
# 다음 전략을 취한다:
#   1) `app.database.get_db` 를 lazy import 시도.
#   2) ImportError 면 동일 시그니처의 더미 generator 함수로 폴백 → None 을 yield.
#   3) Depends(get_db) 는 그대로 쓰되, 더미 환경에서는 db=None 이 주입된다.
#   4) 서비스 레이어가 db=None 분기를 이미 처리한다.
try:
    # 백엔드 1 이 작성할 가능성이 큰 표준 위치들. 모두 ImportError 면 더미 사용.
    from app.database import get_db  # type: ignore  # noqa: F401
except ImportError:  # pragma: no cover - 모델 PR 머지 전 단계
    # TODO: 팀장에게 app/database.py + get_db (SQLAlchemy SessionLocal) 추가 요청.
    #   get_db 가 추가되면 위 import 가 성공하므로 본 더미 분기는 자동 비활성화.
    def get_db():  # noqa: D401 — 더미 generator
        """더미 get_db. None 을 yield 해 라우터가 db=None 으로 호출되게 한다."""
        yield None


# -----------------------------------------------------------------------------
# 라우터 정의
# -----------------------------------------------------------------------------
# tags 는 Swagger 그룹핑용. prefix 는 위 메모대로 두 경로를 모두 다루기 위해 비워 둔다.
router = APIRouter(tags=["Storage"])

# 모듈-수준 싱글톤. stateless 서비스이므로 한 번 만들어 재사용한다.
# 추후 의존성 주입(Depends) 으로 교체할 수 있게 변수명을 단순하게 둔다.
storage_service = StorageService()


# -----------------------------------------------------------------------------
# 요청 스키마 — POST /api/connectors/mock-import
# -----------------------------------------------------------------------------
class ExternalImportRequest(BaseModel):
    """Request body for external storage import.

    If external_id is omitted, GOOGLE_DRIVE_FILE_ID / NOTION_PAGE_ID from .env is used.
    """

    external_id: str | None = Field(None, description="provider document id")


class MockImportRequest(BaseModel):
    """mock-import 의 request body 스키마.

    Pydantic 이 자동으로:
      - 필드 누락 → 422 응답
      - 타입 불일치 → 422 응답
    을 처리해 주므로 핸들러에서 별도 검증 코드는 최소화된다.
    """

    # `...` 는 "기본값 없음 = 필수". provider 는 mock 만 허용 (라우터에서 검증).
    provider: str = Field(..., description="저장소 provider 식별자 ('mock' 만 허용)")
    # 샘플 문서 종류. 누락 시 명세 기본값 "가정통신문".
    document_type: str = Field("가정통신문", description="샘플 문서 종류")


# -----------------------------------------------------------------------------
# 공통 응답 헬퍼 — 모든 엔드포인트가 동일한 envelope 형태를 사용하도록 통일
# -----------------------------------------------------------------------------
def _ok(data: Any, message: str = "") -> dict[str, Any]:
    """성공 응답 envelope 생성.

    구조: {"success": True, "data": ..., "message": ..., "error": None}
    """
    return {"success": True, "data": data, "message": message, "error": None}


def _bad_request(message: str, error: str) -> JSONResponse:
    """400 Bad Request 응답 envelope 생성.

    HTTPException 대신 JSONResponse 로 직접 만들어 envelope 형식을 유지한다.
    """
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "success": False,
            "data": None,
            "message": message,
            "error": error,
        },
    )


def _not_implemented(message: str) -> JSONResponse:
    """501 Not Implemented 응답 envelope 생성.

    OAuth 미구현 커넥터(google_drive/notion) 의 import stub 에서 사용한다.
    error 필드는 명세에 따라 고정 상수 "NOT_IMPLEMENTED".
    """
    return JSONResponse(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        content={
            "success": False,
            "data": None,
            "message": message,
            "error": "NOT_IMPLEMENTED",
        },
    )


# =============================================================================
# ① GET /api/storage/providers
# =============================================================================
# 클라이언트가 "어떤 임포트 옵션이 있는가" 를 표시할 때 호출.
# response 의 data 는 {"providers": [...]} 형태로 nest 한다 (명세).
@router.get(
    "/api/storage/providers",
    status_code=status.HTTP_200_OK,
    summary="연결 가능한 외부 저장소 목록 조회",
    description=(
        "Google Drive, Notion, 로컬, 샘플 문서 등 연결 가능한 저장소 목록을 반환합니다."
    ),
)
def get_providers() -> dict[str, Any]:
    """임포트 가능한 provider 목록을 반환한다.

    공통 envelope 의 data 는 {"providers": [...]}.
    """
    providers = storage_service.get_providers()
    return _ok({"providers": providers})


# =============================================================================
# ② GET /api/connectors
# =============================================================================
# OAuth 기반 외부 커넥터(google_drive/notion) 의 연결 상태를 반환한다.
# 현재는 명세상 모두 disconnected.
@router.get(
    "/api/connectors",
    status_code=status.HTTP_200_OK,
    summary="현재 연결된 저장소 상태 조회",
    description="각 외부 저장소의 현재 연결/해제 상태를 반환합니다.",
)
def get_connectors() -> dict[str, Any]:
    """OAuth 커넥터 연결 상태를 반환한다.

    공통 envelope 의 data 는 {"connectors": [...]}.
    """
    connectors = storage_service.get_connectors_status()
    return _ok({"connectors": connectors})


# =============================================================================
# ③ POST /api/connectors/mock-import
# =============================================================================
# 샘플 문서를 documents/document_texts 에 삽입(또는 TODO 분기) 한다.
# provider 가 "mock" 이 아니면 400 으로 거절.
@router.post(
    "/api/connectors/mock-import",
    status_code=status.HTTP_200_OK,
    summary="샘플 문서 임포트",
    description=(
        "데모용 샘플 문서(가정통신문/지원서/회의록)를 실제 DB에 생성합니다. "
        "provider는 반드시 'mock'이어야 합니다."
    ),
)
async def mock_import(
    payload: MockImportRequest,
    # Depends(get_db) — 실제 get_db 가 import 되면 SQLAlchemy Session 이,
    # 더미 폴백이면 None 이 주입된다. 서비스가 두 케이스를 모두 처리한다.
    db: Any = Depends(get_db),
) -> Any:
    """샘플 문서를 임포트한다.

    명세 [라우터 ③]:
        - request.provider != "mock" → HTTP 400 INVALID_PROVIDER.
        - 그 외에는 storage_service.import_sample_document(db, document_type) 호출.
        - 응답 data : {imported_document_id, title, source_type, extracted_text, status}
    """
    # ---- 1) provider 검증 ---------------------------------------------------
    # 명세 에러 코드 INVALID_PROVIDER + 한국어 메시지 그대로.
    if payload.provider != "mock":
        return _bad_request(
            message="mock provider만 지원합니다.",
            error="INVALID_PROVIDER",
        )

    # ---- 2) 비즈니스 로직 호출 -----------------------------------------------
    # db 가 None 이면 서비스가 INSERT 를 건너뛰고 imported_document_id=None 으로
    # 응답한다. (TODO: 모델 PR 머지 후 자동으로 실 INSERT 경로 활성화)
    result = await storage_service.import_sample_document(
        db=db,
        document_type=payload.document_type,
    )

    # ---- 3) 공통 envelope 으로 감싸 반환 -------------------------------------
    return _ok(result, message="샘플 문서가 임포트되었습니다.")


# =============================================================================
# ④ POST /api/connectors/google-drive/import — stub
# =============================================================================
# OAuth 인증 흐름 미구현. 명세상 HTTP 501 + 안내 메시지.
@router.post(
    "/api/connectors/google-drive/import",
    status_code=status.HTTP_200_OK,
    summary="Import Google Drive document",
    description="Import a Drive document with GOOGLE_DRIVE_ACCESS_TOKEN and save it as HWPX.",
)
async def google_drive_import(
    payload: ExternalImportRequest | None = None,
    db: Any = Depends(get_db),
) -> Any:
    """Import a Google Drive file into documents/document_texts."""
    try:
        result = await storage_service.import_external_document(
            provider="google_drive",
            db=db,
            external_id=payload.external_id if payload else None,
        )
    except ExternalImportError as exc:
        return _bad_request(message=str(exc), error=exc.code)
    return _ok(result, message="Google Drive document imported.")


# =============================================================================
# ? POST /api/connectors/notion/import
# =============================================================================
# Notion integration token based import.
@router.post(
    "/api/connectors/notion/import",
    status_code=status.HTTP_200_OK,
    summary="Import Notion page",
    description="Import a Notion page with NOTION_API_TOKEN and save it as HWPX.",
)
async def notion_import(
    payload: ExternalImportRequest | None = None,
    db: Any = Depends(get_db),
) -> Any:
    """Import a Notion page into documents/document_texts."""
    try:
        result = await storage_service.import_external_document(
            provider="notion",
            db=db,
            external_id=payload.external_id if payload else None,
        )
    except ExternalImportError as exc:
        return _bad_request(message=str(exc), error=exc.code)
    return _ok(result, message="Notion page imported.")
