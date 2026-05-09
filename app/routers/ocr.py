# [백엔드2 담당] 수정 허용 파일 - feature/backend-ocr-voice-storage 브랜치
"""OCR API Router.

엔드포인트:
    POST /api/ocr/extract
        모바일에서 촬영한 이미지를 받아 텍스트를 추출하는 Mock API.
    GET  /api/ocr/{ocr_source_id}
        ocr_source_id 로 OCR 추출 결과 단건 조회 (Mock).
    POST /api/ocr/{ocr_source_id}/confirm
        사용자가 수동 수정한 OCR 텍스트를 확정 상태로 저장 (Mock).

이 모듈의 책임은 "HTTP 요청을 받아서 서비스 레이어에 위임하고,
서비스가 돌려준 결과를 공통 응답 포맷({success, data, message, error}) 으로 감싸
반환"하는 것까지로 한정한다. 비즈니스 로직(텍스트 추출/조회/확정 등) 은 모두
services/ocr_service.py 의 OcrService 클래스가 담당한다.
"""

# 타입 힌트와 향후 호환성을 위한 future import.
from __future__ import annotations

# 응답 dict 의 값 타입이 다양하므로 Any 를 사용.
from typing import Any

# FastAPI 핵심 클래스/함수 import.
#  - APIRouter: 라우터 단위로 엔드포인트 묶음을 만든다 (main.py 에서 include_router 로 등록).
#  - File: multipart/form-data 의 "파일" 파라미터임을 명시.
#  - Form: multipart/form-data 의 "일반 필드" 파라미터임을 명시.
#  - UploadFile: 업로드된 파일을 다루는 객체.
#  - status: HTTP 상태 코드 상수 모음 (예: status.HTTP_200_OK).
from fastapi import APIRouter, File, Form, UploadFile, status
# 400 응답을 공통 envelope 으로 직접 반환하기 위해 JSONResponse 를 사용한다.
# (HTTPException 은 기본적으로 {"detail": "..."} 형태라 envelope 와 어긋남)
from fastapi.responses import JSONResponse

# Pydantic — confirm 엔드포인트의 request body 검증.
from pydantic import BaseModel, Field

# 비즈니스 로직 클래스 + 콘텐츠 타입 화이트리스트 import.
from app.services.ocr_service import ALLOWED_IMAGE_CONTENT_TYPES, OcrService


# -----------------------------------------------------------------------------
# 라우터 정의
# -----------------------------------------------------------------------------
# prefix="/api/ocr": 이 라우터에 속한 모든 엔드포인트가 /api/ocr 로 시작.
# tags=["OCR"]    : Swagger 문서(/docs) 에서 "OCR" 그룹으로 묶여 표시됨.
router = APIRouter(prefix="/api/ocr", tags=["OCR"])

# 모듈-수준 싱글톤 인스턴스. stateless Mock 서비스이므로 한 번 만들어 재사용.
# 추후 의존성 주입이 필요해지면 FastAPI Depends 로 교체.
ocr_service = OcrService()


# -----------------------------------------------------------------------------
# 요청/응답 스키마
# -----------------------------------------------------------------------------
class OcrConfirmRequest(BaseModel):
    """POST /api/ocr/{ocr_source_id}/confirm 의 request body.

    Pydantic 이 자동으로:
      - 필드 누락 → 422 응답
      - 타입 불일치 → 422 응답
    을 처리해 주므로 라우터 본문에서 별도 검증 코드를 쓰지 않아도 된다.
    """

    # `...` 는 "기본값 없음 = 필수" 를 뜻하는 Pydantic 관용 표기.
    edited_text: str = Field(..., description="사용자가 수정한 최종 OCR 텍스트")


# -----------------------------------------------------------------------------
# 공통 응답 헬퍼
# -----------------------------------------------------------------------------
# 라우터 내부에서만 쓰는 작은 헬퍼라 모듈 private(_) 으로 둔다.
def _ok(data: Any, message: str = "") -> dict[str, Any]:
    """공통 응답 envelope 의 "성공" 케이스를 만들어 주는 헬퍼.

    응답 구조: {"success": True, "data": ..., "message": ..., "error": None}
    """
    return {"success": True, "data": data, "message": message, "error": None}


def _bad_request(message: str, error: str) -> JSONResponse:
    """공통 응답 envelope 형식의 400 응답을 만들어 주는 헬퍼.

    HTTPException 을 쓰면 {"detail": ...} 가 되어 envelope 가 깨지므로,
    JSONResponse 로 직접 status_code + body 를 지정한다.
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


# -----------------------------------------------------------------------------
# ① POST /api/ocr/extract
# -----------------------------------------------------------------------------
# status_code=HTTP_200_OK: 정상 응답 시 기본 상태 코드를 200 으로 명시.
# (POST 라서 기본은 201 인 경우도 있는데, 자원 생성이 아니라 "추출 결과" 반환이므로 200.)
@router.post("/extract", status_code=status.HTTP_200_OK)
async def extract_ocr(
    # File(...) : 필수 파일 필드. (...) 는 "필수"를 의미하는 FastAPI 관용.
    image: UploadFile = File(..., description="모바일에서 촬영한 이미지 파일"),
    # Form(False): 일반 form 필드, 기본값 False. multipart 본문에서 문자열로 와도
    # FastAPI 가 "true"/"false" 를 알아서 bool 로 변환해 준다.
    create_document: bool = Form(False, description="OCR 결과로 Document 생성 여부"),
) -> Any:
    """이미지에서 텍스트를 추출한다 (Mock).

    공통 응답 포맷: {success, data, message, error}.
    image 의 content_type 이 ALLOWED_IMAGE_CONTENT_TYPES 에 없으면 400 응답.
    """

    # ---- 1) 콘텐츠 타입 화이트리스트 검증 ------------------------------------
    # UploadFile.content_type 은 클라이언트가 보낸 MIME 타입.
    # 명세상 image/jpeg, image/png, image/gif, image/webp 만 허용.
    if image.content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
        return _bad_request(
            message="지원하지 않는 이미지 형식입니다.",
            error=f"unsupported_content_type: {image.content_type!r}",
        )

    # ---- 2) 비즈니스 로직 호출 -----------------------------------------------
    # 라우터는 "어떻게 추출할지" 는 모르고, 서비스에 위임만 한다.
    # create_document 는 명세상 받기만 하고, document_id 는 Mock 단계에서 None 고정.
    # (documents 도메인은 절대 수정 금지 영역이므로 Mock 단계에서 연동하지 않음)
    _ = create_document  # 명시적 미사용 표시. 추후 documents 연동 시 활용.
    data = await ocr_service.extract_text(image_file=image)

    # ---- 3) 공통 응답 포맷으로 감싸서 반환 ------------------------------------
    return _ok(data, message="OCR 텍스트 추출이 완료되었습니다.")


# -----------------------------------------------------------------------------
# ② GET /api/ocr/{ocr_source_id}
# -----------------------------------------------------------------------------
# path parameter 로 ocr_source_id 를 받는다.
# (정적 경로 /extract 는 위에서 먼저 선언했으므로 라우팅 충돌 없음)
@router.get("/{ocr_source_id}", status_code=status.HTTP_200_OK)
def get_ocr_result(ocr_source_id: str) -> dict[str, Any]:
    """OCR 결과를 ocr_source_id 로 단건 조회한다 (Mock).

    명세상 어떤 ocr_source_id 가 와도 동일한 더미를 돌려준다.
    실제 구현 시에는 서비스 레이어에서 DB 조회 후 없으면 404 처리할 것.
    """
    # 비즈니스 로직은 서비스에 위임 (라우터에 비즈니스 로직 직접 작성 금지 룰).
    data = ocr_service.get_result(ocr_source_id=ocr_source_id)
    return _ok(data)


# -----------------------------------------------------------------------------
# ③ POST /api/ocr/{ocr_source_id}/confirm
# -----------------------------------------------------------------------------
# 사용자가 OCR 결과를 직접 수정한 뒤 "이 텍스트로 확정" 을 누를 때 호출된다.
@router.post("/{ocr_source_id}/confirm", status_code=status.HTTP_200_OK)
def confirm_ocr_result(
    ocr_source_id: str,
    payload: OcrConfirmRequest,
) -> dict[str, Any]:
    """OCR 결과를 사용자 수정본으로 확정한다 (Mock).

    Pydantic 이 payload(edited_text 필수) 검증을 끝낸 뒤에 함수 본문이 실행된다.
    """
    # 서비스 호출 — 키워드 인자로 넘겨 가독성을 높이고, 인자 순서가 바뀌어도
    # 실수로 값이 뒤섞이지 않게 한다.
    data = ocr_service.confirm_result(
        ocr_source_id=ocr_source_id,
        edited_text=payload.edited_text,
    )
    return _ok(data, message="OCR 결과가 확정되었습니다.")
