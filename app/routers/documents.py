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

from fastapi import APIRouter, File, UploadFile, status
from fastapi.responses import JSONResponse

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
    성공 응답 빌더.

    Args:
        data: 응답 본문 `data` 필드에 들어갈 값. dict 또는 list 등 JSON 직렬화 가능 타입.
            (목록 조회는 list, 단일 조회/생성은 dict 가 들어온다.)
        status_code: HTTP 상태 코드. 기본 200, 리소스 생성 시 201 등으로 호출자가 지정.

    Returns:
        `{"success": True, "data": data}` 형태의 JSONResponse.
    """
    return JSONResponse(
        status_code=status_code,
        content={"success": True, "data": data},
    )


def _error_response(message: str, code: str, status_code: int) -> JSONResponse:
    """
    에러 응답 빌더.

    명세 형식: `{"success": False, "data": {"code": "<에러 코드>", "message": "<설명>"}}`

    Args:
        message: 사용자/개발자에게 보일 한국어 메시지.
        code: 클라이언트가 분기 처리하기 좋은 영문 에러 코드 (예: "UNSUPPORTED_FILE_TYPE").
        status_code: HTTP 상태 코드 (400, 413 등).

    Returns:
        명세 형식의 JSONResponse.
    """
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "data": {"code": code, "message": message},
        },
    )


# ---------------------------------------------------------------------------
# 엔드포인트: 문서 업로드
# ---------------------------------------------------------------------------
@router.post("/upload")
async def upload_document(
    file: UploadFile | None = File(default=None),
) -> JSONResponse:
    """
    문서 업로드 엔드포인트.

    클라이언트는 multipart/form-data 형식으로 `file` 필드에 파일을 담아 전송한다.
    서버는 다음 작업을 수행한다.
        1. 파일이 첨부되었는지 확인
        2. 서비스 계층에 위임하여 파일 저장 + DB 메타데이터 기록
        3. 결과를 명세 형식으로 감싸 응답
            - 성공: 201 Created + {"success": true, "data": <메타데이터>}
            - 실패: 4xx/5xx + {"success": false, "data": {"code": ..., "message": ...}}

    Args:
        file: 업로드된 파일.
            `File(default=None)` 으로 두어 누락 시 FastAPI 의 기본 422 검증 응답이 아니라
            라우터 내부에서 명세 형식으로 400 에러를 반환할 수 있게 한다.

    Returns:
        명세 형식의 JSONResponse.
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
            file=file, user_id=DEMO_USER_ID
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
async def list_documents() -> JSONResponse:
    """
    데모 사용자(`user_id=1`)의 활성 문서 목록을 반환한다.

    "활성" = soft-delete 되지 않은 (deleted_at IS NULL) 문서.

    응답 data 는 각 문서별 dict 들의 리스트이며, 각 dict 는 다음 필드를 포함한다.
        id, title, source_type, file_type, parse_status, created_at, updated_at

    Returns:
        성공 시 200 + {"success": true, "data": [...]}
        예기치 못한 오류 시 500 + 명세 형식 에러
    """
    try:
        documents = document_service.list_documents(user_id=DEMO_USER_ID)
    except Exception as exc:  # noqa: BLE001
        # 운영에서는 여기서 로깅. MVP 라 메시지만 회피적으로 노출.
        return _error_response(
            message=f"문서 목록 조회 중 오류가 발생했습니다: {exc}",
            code="INTERNAL_SERVER_ERROR",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

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
            code="DOCUMENT_NOT_FOUND",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except Exception as exc:  # noqa: BLE001
        return _error_response(
            message=f"문서 조회 중 오류가 발생했습니다: {exc}",
            code="INTERNAL_SERVER_ERROR",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return _success_response(document, status_code=status.HTTP_200_OK)
