"""
문서 관련 API 라우터.

이 모듈은 클라이언트(모바일 앱) 가 호출하는 HTTP 엔드포인트의 진입점이다.
실제 비즈니스 로직(파일 저장·DB 기록 등)은 `app.services.document_service` 에 위임하고,
본 모듈은 다음 책임만 진다.
    - URL 경로 및 HTTP 메서드 정의
    - 요청 파라미터(파일 등) 수신
    - 서비스 호출 결과를 공통 응답 포맷으로 감싸 반환
    - 도메인 예외를 적절한 HTTP 상태 코드로 변환

API 공통 규칙(명세서 기준):
    - 모든 경로는 `/api` 접두사 사용 → 본 라우터는 `/api/documents` 까지 prefix 부여
    - 응답 본문은 항상 `{"success": true/false, "data": ...}` 구조
        - 성공 시 data: 도메인 결과 (예: 문서 메타데이터 dict)
        - 실패 시 data: {"code": "<에러 코드>", "message": "<사람이 읽는 메시지>"}
    - 인증은 해커톤 MVP 단계에서 생략, `user_id=1` 데모 사용자 고정
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, Query, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.services import document_service
from app.services.document_service import (
    DocumentNotFoundError,
    EmptyFileError,
    FileTooLargeError,
    UnsupportedFileTypeError,
)

# ---------------------------------------------------------------------------
# 데모 사용자 ID
# ---------------------------------------------------------------------------
# 인증/세션을 도입하기 전 임시로 사용하는 고정 사용자 식별자.
# 실서비스 전환 시 토큰 디코딩 후 주입되는 값으로 교체될 예정.
DEMO_USER_ID = 1

# ---------------------------------------------------------------------------
# 라우터 정의
# ---------------------------------------------------------------------------
# prefix:
#     "/api/documents" — 명세서의 `/api` 공통 접두사 + 문서 도메인 경로
# tags:
#     ["documents"] — Swagger UI 에서 동일 그룹으로 묶이도록 표기
router = APIRouter(prefix="/api/documents", tags=["documents"])


# ---------------------------------------------------------------------------
# 응답 빌더 (공통 포맷 보장)
# ---------------------------------------------------------------------------
# FastAPI 의 기본 HTTPException 은 본문을 `{"detail": "..."}` 으로 자동 직렬화한다.
# 그러나 명세서는 모든 응답이 `{"success": bool, "data": ...}` 구조여야 한다고 정한다.
# 따라서 라우터 내부에서 직접 JSONResponse 를 반환하여 포맷을 강제한다.
def _success_response(data, status_code: int = status.HTTP_200_OK) -> JSONResponse:
    """
    성공 응답 빌더 — 통합 공통 envelope 4-key.

    공통 응답 스펙:
        {"success": bool, "data": dict|null, "message": str, "error": str|null}

    Args:
        data: 응답 본문 `data` 필드에 들어갈 값. dict / list / None 모두 허용.
        status_code: HTTP 상태 코드. 기본 200, 리소스 생성 시 201 등.
    """
    return JSONResponse(
        status_code=status_code,
        content={
            "success": True,
            "data": data,
            "message": "",
            "error": None,
        },
    )


def _error_response(message: str, code: str, status_code: int) -> JSONResponse:
    """
    에러 응답 빌더 — 통합 공통 envelope 4-key.

    이전 형식(`data` 안에 code/message 패킹) 에서 통합 envelope 으로 이전:
        {"success": False, "data": null, "message": <설명>, "error": <CODE>}

    Args:
        message: 사용자/개발자에게 보일 한국어 메시지.
        code: 클라이언트 분기용 영문 에러 코드 (예: "UNSUPPORTED_FILE_TYPE").
        status_code: HTTP 상태 코드 (400, 413 등).
    """
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "data": None,
            "message": message,
            "error": code,
        },
    )


# ---------------------------------------------------------------------------
# 엔드포인트: 문서 업로드
# ---------------------------------------------------------------------------
@router.post("/upload")
async def upload_document(
    file: UploadFile | None = File(default=None),
    # 명세 [BE1 - Documents API] 의 추가 multipart 필드.
    # 누락 가능(optional) 이라 기본값을 None / 'upload' 로 둔다.
    title: str | None = Form(default=None, description="사용자 지정 문서 제목"),
    source_type: str = Form(default="upload", description="출처 유형 (upload/mock/...)"),
    folder_id: int | None = Form(default=None, description="속할 폴더 ID"),
    category: str | None = Form(default=None, description="(예약) 카테고리"),
) -> JSONResponse:
    """
    문서 업로드 엔드포인트.

    명세 multipart 필드: file(필수), title?, source_type?, folder_id?, category?
    `category` 는 현재 documents 테이블에 컬럼이 없어 본 PR 에서는 받기만 하고 무시한다.
        TODO: documents 테이블에 category 컬럼 추가 후 service 에 전달.

    응답: 통합 envelope 4-key (success, data, message, error).
        성공 시 data: 생성된 문서 메타데이터 dict.
        실패 시 data=null, error=<CODE>, message=<설명>.
    """

    # ---- 파일 누락 방어 ----
    # FastAPI 가 자동 422 검증 응답을 띄우면 명세 형식이 깨지므로
    # `default=None` 으로 받아 직접 명세 형식으로 변환한다.
    if file is None:
        return _error_response(
            message="파일이 첨부되지 않았습니다. multipart/form-data 의 'file' 필드를 확인해 주세요.",
            code="MISSING_FILE",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # ---- 비즈니스 로직 위임 + 도메인 예외 변환 ----
    try:
        document = await document_service.upload_document(
            file=file,
            user_id=DEMO_USER_ID,
            title=title,
            source_type=source_type,
            folder_id=folder_id,
            category=category,
        )
    except UnsupportedFileTypeError as exc:
        # 클라이언트의 잘못된 입력 → 400 Bad Request
        return _error_response(
            message=str(exc),
            code="UNSUPPORTED_FILE_TYPE",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except EmptyFileError as exc:
        # 빈 파일도 잘못된 입력으로 간주 → 400 Bad Request
        return _error_response(
            message=str(exc),
            code="EMPTY_FILE",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except FileTooLargeError as exc:
        # 크기 초과는 RFC 9110 의 의미상 413 Payload Too Large 가 적합.
        return _error_response(
            message=str(exc),
            code="FILE_TOO_LARGE",
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )
    except Exception as exc:  # noqa: BLE001
        # 예기치 못한 서버 오류도 명세 형식으로 응답해야 하므로 광범위 catch.
        # 운영 환경에서는 여기서 로깅/모니터링 도구 연동 필요.
        return _error_response(
            message=f"서버 내부 오류가 발생했습니다: {exc}",
            code="INTERNAL_SERVER_ERROR",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # ---- 성공 응답 ----
    # 새 리소스 생성이므로 201 Created.
    return _success_response(document, status_code=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# 엔드포인트: 문서 목록 조회
# ---------------------------------------------------------------------------
@router.get("")
async def list_documents(
    folder_id: int | None = Query(None, description="폴더 ID 필터"),
    category: str | None = Query(None, description="(예약) 카테고리 필터"),
    source_type: str | None = Query(None, description="출처 유형 필터"),
) -> JSONResponse:
    """
    데모 사용자(`user_id=1`)의 활성 문서 목록을 반환한다.

    "활성" = soft-delete 되지 않은 (deleted_at IS NULL) 문서.

    명세 [BE1 - Documents API] 쿼리 파라미터:
        folder_id?    : documents.folder_id 일치 필터.
        category?     : (예약) 현재 schema 에 컬럼 없음 → 받기만 하고 무시. TODO.
        source_type?  : documents.source_type 일치 필터.

    응답 data 는 각 문서별 dict 들의 리스트.
    """
    try:
        documents = document_service.list_documents(
            user_id=DEMO_USER_ID,
            folder_id=folder_id,
            category=category,
            source_type=source_type,
        )
    except Exception as exc:  # noqa: BLE001
        return _error_response(
            message=f"문서 목록 조회 중 오류가 발생했습니다: {exc}",
            code="INTERNAL_SERVER_ERROR",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # 명세에 맞춰 data 를 {documents: [...]} 로 nest 하지 않고 list 직접 반환 유지.
    # (현 라우트의 응답 자료형이 documents[] 자체이므로 명세 "data: documents[]" 와 일치.)
    return _success_response(documents, status_code=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# 엔드포인트: 단일 문서 조회
# ---------------------------------------------------------------------------
@router.get("/{document_id}")
async def get_document(document_id: int) -> JSONResponse:
    """
    데모 사용자의 단일 문서를 조회한다.

    document_texts 에 해당 문서의 텍스트가 있으면 extracted_text 를 함께 반환하고,
    없으면 null 로 채워 명세를 만족시킨다.

    Args:
        document_id: 경로 파라미터. FastAPI 가 int 로 자동 캐스팅하며 실패 시 422 가 뜨지만,
            현재 라우터에서 422 를 명세 형식으로 변환하는 로직은 없다.
            (TODO: main.py 에 RequestValidationError exception_handler 등록 필요)

    Returns:
        - 200 + {"success": true, "data": {...}}: 정상
        - 404 + {"success": false, "data": {"code":"DOCUMENT_NOT_FOUND", ...}}: 미존재/소유권 불일치/삭제됨
        - 500 + 명세 형식 에러: 예기치 못한 오류
    """
    try:
        document = document_service.get_document(
            document_id=document_id, user_id=DEMO_USER_ID
        )
    except DocumentNotFoundError as exc:
        return _error_response(
            message=str(exc),
            code="NOT_FOUND",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except Exception as exc:  # noqa: BLE001
        return _error_response(
            message=f"문서 조회 중 오류가 발생했습니다: {exc}",
            code="INTERNAL_SERVER_ERROR",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return _success_response(document, status_code=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# 요청 본문 스키마 (Pydantic)
# ---------------------------------------------------------------------------
# PUT /api/documents/{document_id}/text 의 요청 본문 모델.
# Pydantic 이 자동으로 JSON 파싱·필드 존재·타입 검증을 처리해준다.
# 다만 실패 시 FastAPI 는 422 + {"detail": [...]} 형식의 기본 응답을 반환하므로,
# 본 응답이 명세("{success, data}")와 어긋나는 점은 알려진 트레이드오프.
# (TODO: main.py 에 RequestValidationError 핸들러를 등록하면 명세 형식으로 통일 가능.)
class UpdateDocumentTextRequest(BaseModel):
    """PUT /api/documents/{document_id}/text 요청 바디."""

    edited_text: str


class ReindexDocumentRequest(BaseModel):
    """POST /api/documents/{document_id}/reindex 요청 바디.

    명세 [BE1 - Documents API] reindex 요청: {force: boolean}.
    force=False 면 이미 텍스트가 있을 때 재처리를 생략하고 현재 상태를 반환,
    force=True 면 강제로 text_version 을 +1 하고 updated_at 을 갱신해 "재인덱싱"
    효과를 시뮬레이션한다.
    """

    force: bool = False


# ---------------------------------------------------------------------------
# 엔드포인트: 문서 텍스트 수정 (버전 이력 기록)
# ---------------------------------------------------------------------------
@router.put("/{document_id}/text")
async def update_document_text(
    document_id: int,
    body: UpdateDocumentTextRequest,
) -> JSONResponse:
    """
    문서 본문 텍스트를 수정한다.

    요청:
        PUT /api/documents/{document_id}/text
        Body: {"edited_text": "수정된 문서 텍스트"}  ← UpdateDocumentTextRequest 로 검증

    처리:
        1) Pydantic 이 본문을 UpdateDocumentTextRequest 인스턴스로 자동 파싱·검증
        2) service.update_document_text 호출 → DB 4단계 작업 위임
        3) 결과를 명세 형식으로 감싸 응답

    응답 data:
        - document_text_id, version_id, version_no, updated_at

    상태 코드:
        - 200: 성공
        - 422: Pydantic 본문 검증 실패 (FastAPI 기본 응답 — 명세 형식 아님)
        - 404: 문서 없음/소유권 불일치/삭제됨
        - 500: 예기치 못한 서버 오류
    """

    # ---- 비즈니스 로직 위임 ----
    try:
        result = document_service.update_document_text(
            document_id=document_id,
            edited_text=body.edited_text,
            user_id=DEMO_USER_ID,
            created_by=DEMO_USER_ID,
        )
    except DocumentNotFoundError as exc:
        return _error_response(
            message=str(exc),
            code="NOT_FOUND",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except Exception as exc:  # noqa: BLE001
        return _error_response(
            message=f"문서 텍스트 수정 중 오류가 발생했습니다: {exc}",
            code="INTERNAL_SERVER_ERROR",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # ---- 성공 응답 ----
    return _success_response(result, status_code=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# 엔드포인트: 문서 재인덱싱
# ---------------------------------------------------------------------------
@router.post("/{document_id}/reindex")
async def reindex_document(
    document_id: int,
    body: ReindexDocumentRequest,
) -> JSONResponse:
    """
    문서 텍스트/필드를 재인덱싱한다.

    명세 [BE1 - Documents API] POST /api/documents/{id}/reindex:
        요청: {force: boolean}
        응답: data = {document_texts: <갱신 후 dict>, fields: [...]}.

    동작:
        - force=False: document_texts 가 있으면 현재 상태 그대로 반환 (재처리 생략).
        - force=True : document_texts.text_version 을 +1 하고 updated_at 을 갱신해
                       재인덱싱 효과를 시뮬레이션 (실제 OCR 재실행은 본 PR 범위 외).

    fields 는 schema 부재로 빈 배열 반환 (TODO).
    """
    try:
        result = document_service.reindex_document(
            document_id=document_id,
            user_id=DEMO_USER_ID,
            force=body.force,
        )
    except DocumentNotFoundError as exc:
        return _error_response(
            message=str(exc),
            code="NOT_FOUND",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except Exception as exc:  # noqa: BLE001
        return _error_response(
            message=f"문서 재인덱싱 중 오류가 발생했습니다: {exc}",
            code="INTERNAL_SERVER_ERROR",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return _success_response(result, status_code=status.HTTP_200_OK)
