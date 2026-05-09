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
#  - status    : HTTP 상태 코드 상수 (HTTP_400_BAD_REQUEST, HTTP_501_NOT_IMPLEMENTED 등).
from fastapi import APIRouter, status

# JSONResponse: 비-200 응답 본문을 공통 envelope 형식으로 직접 만들 때 사용.
# (HTTPException 은 {"detail": "..."} 형식이라 envelope 와 충돌)
from fastapi.responses import JSONResponse

# Pydantic — POST body 검증.
#  - BaseModel : 요청/응답 모델 베이스 클래스.
#  - Field     : 필드 메타데이터(필수/기본값/설명) 지정.
from pydantic import BaseModel, Field

# 비즈니스 로직 클래스.
from app.services.storage_service import StorageService


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
@router.get("/api/storage/providers", status_code=status.HTTP_200_OK)
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
@router.get("/api/connectors", status_code=status.HTTP_200_OK)
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
@router.post("/api/connectors/mock-import", status_code=status.HTTP_200_OK)
async def mock_import(payload: MockImportRequest) -> Any:
    """샘플 문서를 임포트한다.

    provider 가 "mock" 이 아닌 경우 HTTP 400.
    document_type 이 SAMPLE_DOCUMENTS 에 없으면 서비스에서 "가정통신문" 으로 폴백.
    """
    # ---- 1) provider 검증 ---------------------------------------------------
    # 이 엔드포인트는 mock 전용. 다른 provider 가 들어오면 명시적으로 거절.
    if payload.provider != "mock":
        return _bad_request(
            message="provider 는 'mock' 이어야 합니다.",
            error=f"invalid_provider: {payload.provider!r}",
        )

    # ---- 2) 비즈니스 로직 호출 -----------------------------------------------
    # TODO: db 세션 의존성 주입 (Depends(get_db))
    #       현재는 DB 세션 팩토리가 아직 없어 None 으로 호출. 모델 PR 이후 활성화.
    result = await storage_service.import_sample_document(
        document_type=payload.document_type,
        db=None,
    )

    # ---- 3) 공통 envelope 으로 감싸 반환 -------------------------------------
    return _ok(result, message="샘플 문서가 임포트되었습니다.")


# =============================================================================
# ④ POST /api/connectors/google-drive/import — stub
# =============================================================================
# OAuth 인증 흐름 미구현. 명세상 HTTP 501 + 안내 메시지.
@router.post("/api/connectors/google-drive/import")
def google_drive_import_stub() -> JSONResponse:
    """Google Drive 임포트 stub (HTTP 501).

    실제 구현 시:
      1) OAuth 인증 콜백으로 access_token 확보.
      2) Drive API 로 file 메타/본문 다운로드.
      3) documents/document_texts INSERT 후 imported_document_id 반환.
    """
    return _not_implemented(
        "Google Drive 연동은 준비 중입니다. OAuth 인증 구현 후 활성화됩니다."
    )


# =============================================================================
# ⑤ POST /api/connectors/notion/import — stub
# =============================================================================
# Integration Token 미설정. 명세상 HTTP 501 + 안내 메시지.
@router.post("/api/connectors/notion/import")
def notion_import_stub() -> JSONResponse:
    """Notion 임포트 stub (HTTP 501).

    실제 구현 시:
      1) NOTION_API_TOKEN 으로 Notion API 호출.
      2) 페이지/블록 텍스트 추출.
      3) documents/document_texts INSERT 후 imported_document_id 반환.
    """
    return _not_implemented(
        "Notion 연동은 준비 중입니다. Integration Token 설정 후 활성화됩니다."
    )
