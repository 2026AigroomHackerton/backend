"""OCR API Router.

엔드포인트:
    POST /api/ocr/extract
        모바일에서 촬영한 이미지를 받아 텍스트를 추출하는 Mock API.
        (실제 OCR 엔진 연동은 ocr_service 측에서 추후 교체)

이 모듈의 책임은 "HTTP 요청을 받아서 서비스 레이어에 위임하고,
서비스가 돌려준 결과를 공통 응답 포맷으로 감싸 반환"하는 것까지로 한정한다.
비즈니스 로직(텍스트 추출 등)은 services/ocr_service.py 에 둔다.
"""

# 타입 힌트와 향후 호환성을 위한 future import (서비스 모듈과 동일한 이유).
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

# 비즈니스 로직 모듈을 모듈 단위로 import.
# (함수 단위로 import 하지 않는 이유: 추후 함수가 늘어나도 import 줄을 건드릴 필요가 없고,
#  ocr_service.extract_text_from_image 처럼 호출 시 어느 모듈 함수인지가 분명해진다.)
from app.services import ocr_service


# -----------------------------------------------------------------------------
# 라우터 정의
# -----------------------------------------------------------------------------
# prefix="/api/ocr": 이 라우터에 속한 모든 엔드포인트가 /api/ocr 로 시작.
# tags=["ocr"]    : Swagger 문서(/docs)에서 "ocr" 그룹으로 묶여 표시됨.
router = APIRouter(prefix="/api/ocr", tags=["ocr"])


# -----------------------------------------------------------------------------
# POST /api/ocr/extract
# -----------------------------------------------------------------------------
# status_code=HTTP_200_OK: 정상 응답 시 기본 상태 코드를 200 으로 명시.
# (POST 라서 기본은 201 인 경우도 있는데, 자원 생성이 아니라 "추출 결과" 반환이므로 200.)
@router.post("/extract", status_code=status.HTTP_200_OK)
async def extract_ocr(
    # File(...) : 필수 파일 필드. (...) 는 "필수"를 의미하는 FastAPI 관용.
    # description 은 Swagger 에 표시되는 설명.
    image: UploadFile = File(..., description="모바일에서 촬영한 이미지 파일"),
    # Form(False): 일반 form 필드, 기본값 False.
    # bool 타입이지만 multipart 본문에서는 문자열로 오기 때문에
    # FastAPI 가 "true"/"false" 를 알아서 bool 로 변환해 준다.
    create_document: bool = Form(False, description="OCR 결과로 Document 생성 여부"),
) -> dict[str, Any]:
    """이미지에서 텍스트를 추출한다 (Mock).

    공통 응답 포맷: {success, data, message, error}
    - success: 요청 처리 성공 여부 (boolean)
    - data   : 실제 응답 페이로드 (서비스가 반환한 dict 그대로)
    - message: 사용자/개발자에게 보여줄 한국어 메시지
    - error  : 실패 시 에러 정보. 정상 응답에서는 None 으로 둔다.
    """

    # ---- 1) 비즈니스 로직 호출 -----------------------------------------------
    # 라우터는 "어떻게 추출할지"는 모르고, 서비스에 위임만 한다.
    # extract_text_with_ai: 키가 있으면 실제 OpenAI Vision OCR, 없거나 실패 시
    # mock 으로 자동 폴백하는 새 진입점.
    data = await ocr_service.extract_text_with_ai(
        image=image,
        create_document=create_document,
    )

    # ---- 2) 공통 응답 포맷으로 감싸서 반환 ------------------------------------
    # FastAPI 는 dict 를 반환하면 자동으로 JSON 으로 직렬화해서 응답한다.
    # 모든 API 가 동일한 envelope 구조(success/data/message/error)를 갖도록 통일한다.
    return {
        "success": True,
        "data": data,
        "message": "OCR 텍스트 추출이 완료되었습니다.",
        "error": None,
    }
