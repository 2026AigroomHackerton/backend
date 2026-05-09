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
    - 응답 본문은 항상 `{"success": true, "data": ...}` 구조
    - 인증은 해커톤 MVP 단계에서 생략, `user_id=1` 데모 사용자 고정
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.services import document_service
from app.services.document_service import (
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


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_document(file: UploadFile = File(...)) -> dict:
    """
    문서 업로드 엔드포인트.

    클라이언트는 multipart/form-data 형식으로 `file` 필드에 파일을 담아 전송한다.
    서버는 다음 작업을 수행한다.
        1. 서비스 계층에 위임하여 파일 저장 + DB 메타데이터 기록
        2. 결과를 `{"success": true, "data": <메타데이터>}` 로 감싸 201 Created 로 응답

    Args:
        file: 업로드된 파일. `File(...)` 로 명시하여 FastAPI 가 멀티파트로 파싱하게 한다.

    Returns:
        성공 시 표준 응답 구조 dict.

    Raises:
        HTTPException 400: 미지원 파일 형식 또는 빈 파일.
        HTTPException 413: 허용 크기 초과.
    """

    # 도메인 로직은 모두 서비스 계층에 위임한다.
    # 라우터는 "어떻게 처리할지" 가 아닌 "어떻게 노출할지" 만 책임진다.
    try:
        document = await document_service.upload_document(
            file=file, user_id=DEMO_USER_ID
        )
    except UnsupportedFileTypeError as exc:
        # 클라이언트의 잘못된 입력 → 400 Bad Request
        # `from exc` 로 예외 체이닝을 보존하여 디버깅 시 원인 추적이 쉽도록 한다.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except EmptyFileError as exc:
        # 빈 파일도 잘못된 입력으로 간주 → 400 Bad Request
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except FileTooLargeError as exc:
        # 크기 초과는 RFC 9110 의 의미상 413 Payload Too Large 가 적합.
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc

    # 명세서가 정한 공통 응답 구조로 감싼 뒤 그대로 반환.
    # FastAPI 가 dict 를 자동으로 JSON 으로 직렬화해 응답한다.
    return {"success": True, "data": document}
