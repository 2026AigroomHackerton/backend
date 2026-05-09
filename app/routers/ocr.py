# [백엔드2 담당] 수정 허용 파일 - feature/backend-ocr-voice-storage 브랜치
"""OCR API Router (명세 흐름 정렬 버전).

엔드포인트:
    POST /api/ocr/extract
        이미지 업로드 → 검증 → 저장 → 텍스트 추출 → ID 발급 → store 등록 → 응답.
    GET  /api/ocr/{ocr_source_id}
        ocr_source_id 로 OCR 추출 결과 단건 조회 (없으면 404).
    POST /api/ocr/{ocr_source_id}/confirm
        사용자 수정 텍스트로 확정 (없으면 404).

본 모듈은 HTTP 입출력만 담당한다. 비즈니스 로직은 OcrService 가 담당하며,
명세 [라우터 구현] 흐름에 맞춰 4단계 메서드를 순서대로 호출한다.
공통 응답 envelope 은 services.ocr_service 의 success_response/error_response 를 사용.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.ocr_service import (
    FileSaveFailedError,
    InvalidFileTypeError,
    OcrEngineNotFoundError,
    OcrService,
    OcrSourceNotFoundError,
    error_response,
    success_response,
)

# DB 의존성 주입 — storage.py 와 동일한 lazy import 폴백 패턴.
# get_db 가 없으면 None 을 yield 하는 더미로 떨어져 service 가 INSERT 를 건너뜀.
try:
    from app.database import get_db  # type: ignore
except ImportError:  # pragma: no cover - 모델 PR 머지 전 단계
    def get_db():  # noqa: D401 — 더미 generator
        """더미 get_db. None 을 yield 해 라우터가 db=None 으로 호출되게 한다."""
        yield None


# -----------------------------------------------------------------------------
# 라우터 정의
# -----------------------------------------------------------------------------
router = APIRouter(prefix="/api/ocr", tags=["OCR"])

# stateless Mock 서비스. 단일 인스턴스로 in-memory store 를 공유한다.
ocr_service = OcrService()


# -----------------------------------------------------------------------------
# 요청 스키마
# -----------------------------------------------------------------------------
class OcrConfirmRequest(BaseModel):
    """POST /api/ocr/{ocr_source_id}/confirm body."""

    edited_text: str = Field(..., description="사용자가 수정한 최종 OCR 텍스트")


# -----------------------------------------------------------------------------
# 라우터 전용 헬퍼
# -----------------------------------------------------------------------------
def _envelope_response(payload: dict, http_status: int) -> JSONResponse:
    """success/error envelope dict 을 JSONResponse 로 감싼다.

    HTTPException 을 쓰면 {"detail": ...} 가 되어 envelope 가 깨지므로
    JSONResponse 로 직접 status_code 를 지정한다.
    """
    return JSONResponse(status_code=http_status, content=payload)


# -----------------------------------------------------------------------------
# ① POST /api/ocr/extract — 명세 5단계 흐름
# -----------------------------------------------------------------------------
@router.post(
    "/extract",
    status_code=status.HTTP_200_OK,
    summary="이미지 OCR 텍스트 추출",
    description=(
        "이미지 파일을 업로드하면 pytesseract로 실제 OCR을 수행합니다. "
        "한국어/영어 동시 인식(kor+eng). 서버에 Tesseract 설치 필요."
    ),
)
async def extract_ocr(
    image: UploadFile = File(..., description="모바일에서 촬영한 이미지 파일"),
    create_document: bool = Form(False, description="OCR 결과로 Document 생성 여부"),
    db: Any = Depends(get_db),
):
    """이미지에서 텍스트를 추출한다 (실제 Vision + mock 폴백).

    명세 처리 순서:
        1) validate_image            → 실패 시 HTTP 400
        2) save_image                → image_path 확보 (실패 시 HTTP 500)
        3) extract_text_from_image   → 텍스트/신뢰도 (엔진 미설치 시 HTTP 500)
        4) generate_ocr_id
        5) in-memory store 등록 (GET/{id}, /confirm 동작용)
        6) create_document=True 면 documents + document_texts INSERT
           → 응답 data.document_id 에 실제 PK 반환
    """
    # 1) MIME 검증
    try:
        ocr_service.validate_image(image)
    except InvalidFileTypeError as exc:
        return _envelope_response(
            error_response(error=exc.code, message=str(exc)),
            http_status=exc.http_status,
        )

    # 원본 메타데이터 보존 — save_image 가 file.read() 를 소비하므로 사전 캡처.
    original_filename = image.filename
    content_type = image.content_type

    # 2) 디스크 저장 (실패 시 FILE_SAVE_FAILED)
    try:
        image_path = await ocr_service.save_image(image)
    except FileSaveFailedError as exc:
        return _envelope_response(
            error_response(error=exc.code, message=str(exc)),
            http_status=exc.http_status,
        )

    # 3) OCR 추출 (현 정책상 엔진 미설치도 mock 폴백되므로 raise 없음.
    #    명세 호환을 위해 OcrEngineNotFoundError 처리 코드만 남겨 둠)
    try:
        ocr_result = ocr_service.extract_text_from_image(image_path)
    except OcrEngineNotFoundError as exc:
        return _envelope_response(
            error_response(
                error=exc.code,
                message="서버에 Tesseract가 설치되어 있지 않습니다.",
            ),
            http_status=exc.http_status,
        )

    # 4) ID 발급
    ocr_source_id = ocr_service.generate_ocr_id()

    extracted_text = ocr_result.get("text", "")
    confidence = float(ocr_result.get("confidence", 0.0))

    # 5) in-memory store 등록
    ocr_service.remember(
        ocr_source_id=ocr_source_id,
        extracted_text=extracted_text,
        image_path=image_path,
    )

    # 6) create_document=True → documents + document_texts INSERT.
    #    db=None 이거나 INSERT 실패 시 graceful 폴백 (document_id=None).
    document_id: int | None = None
    if create_document:
        document_id = ocr_service.create_document_from_ocr(
            db=db,
            image_path=image_path,
            extracted_text=extracted_text,
            original_filename=original_filename,
            content_type=content_type,
        )

    # 명세 응답 data 키 5종 + image_path. _source/_model 등 디버깅 메타는 _ 접두.
    data = {
        "ocr_source_id": ocr_source_id,
        "extracted_text": extracted_text,
        "confidence": confidence,
        "document_id": document_id,
        "image_path": image_path,
    }
    if "_source" in ocr_result:
        data["_source"] = ocr_result["_source"]
    if "_model" in ocr_result:
        data["_model"] = ocr_result["_model"]

    return success_response(data, message="OCR 텍스트 추출이 완료되었습니다.")


# -----------------------------------------------------------------------------
# ② GET /api/ocr/{ocr_source_id}
# -----------------------------------------------------------------------------
@router.get(
    "/{ocr_source_id}",
    status_code=status.HTTP_200_OK,
    summary="OCR 결과 조회",
    description="ocr_source_id로 저장된 OCR 결과를 조회합니다.",
)
def get_ocr_result(ocr_source_id: str):
    """OCR 결과 단건 조회. 미등록 ID 는 404 envelope 로 응답."""
    try:
        data = ocr_service.get_result(ocr_source_id=ocr_source_id)
    except OcrSourceNotFoundError as exc:
        return _envelope_response(
            error_response(error=exc.code, message=str(exc)),
            http_status=exc.http_status,
        )
    return success_response(data)


# -----------------------------------------------------------------------------
# ③ POST /api/ocr/{ocr_source_id}/confirm
# -----------------------------------------------------------------------------
@router.post(
    "/{ocr_source_id}/confirm",
    status_code=status.HTTP_200_OK,
    summary="OCR 결과 사용자 확정",
    description="사용자가 수동 수정한 텍스트를 최종 확정합니다.",
)
def confirm_ocr_result(ocr_source_id: str, payload: OcrConfirmRequest):
    """사용자 수정본으로 확정. 미등록 ID 는 404 envelope 로 응답."""
    try:
        data = ocr_service.confirm_result(
            ocr_source_id=ocr_source_id,
            edited_text=payload.edited_text,
        )
    except OcrSourceNotFoundError as exc:
        return _envelope_response(
            error_response(error=exc.code, message=str(exc)),
            http_status=exc.http_status,
        )
    return success_response(data, message="OCR 결과가 확정되었습니다.")
