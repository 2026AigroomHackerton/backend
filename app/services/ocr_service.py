# [백엔드2 담당] 수정 허용 파일 - feature/backend-ocr-voice-storage 브랜치
"""OCR Service (Class-based).

해커톤 MVP 단계에서는 실제 OCR 엔진(Tesseract, Google Cloud Vision, Naver CLOVA OCR 등)을
호출하지 않고, 미리 정해 둔 더미 텍스트를 그대로 반환한다.
이렇게 해 두면 프론트엔드/다른 백엔드 담당자가 실제 OCR 연동 전에도
API 스펙에 맞춰 화면과 로직을 먼저 만들어 둘 수 있다.

추후 실제 OCR 연동 시에는 OCRService 의 메서드만 교체하면 되며,
라우터나 응답 포맷은 변경하지 않아도 되도록 인터페이스를 단순하게 유지한다.

[설계 메모]
- 클래스 기반(OCRService)로 작성. 라우터는 모듈-수준 싱글톤 인스턴스를 사용한다.
- 싱글톤은 해커톤 MVP 단계의 단순화 선택. 추후 FastAPI Depends 로 주입 형태로
  바꾸기 쉽도록 메서드 시그니처에 self 외 의존성을 두지 않았다.
"""

# from __future__ import annotations 는 타입 힌트를 "문자열"로 평가하도록 해 준다.
# Python 3.10 미만 호환성, 그리고 순환 import 회피에 유용하다.
from __future__ import annotations

# uuid: OCR 결과마다 고유 식별자(ocr_source_id)를 부여하기 위해 사용.
import uuid

# asyncio: 동기 SDK(OpenAI) 호출을 별도 스레드로 돌려 이벤트 루프를 안 막기 위함.
import asyncio
# base64: 이미지 bytes 를 OpenAI Vision API 가 요구하는 data URL 형태로 인코딩.
import base64
# logging: OpenAI 호출 실패 시 디버깅 로그 출력.
import logging

# 타입 힌트용 모듈.
#  - Any: 어떤 타입이든 허용 (응답 dict 의 값이 다양하므로)
#  - Optional: None 도 가질 수 있는 타입(예: document_id 는 int 이거나 None)
from typing import Any, Optional

# FastAPI 의 UploadFile: multipart/form-data 로 업로드된 파일을 다루는 객체.
# .read() / .filename / .content_type 등의 비동기 API 를 제공한다.
from fastapi import UploadFile

# 클라이언트 싱글톤(키 없으면 None) 과 vision 모델명.
from app.core.openai_client import get_client
from app.core.config import OPENAI_MODEL_VISION

# 모듈 전용 로거.
logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# 더미 데이터 / 프롬프트 상수 (모듈 수준 — 클래스 인스턴스 간 공유)
# -----------------------------------------------------------------------------
# Mock OCR 이 항상 반환할 가정통신문 더미 텍스트.
# 실제 OCR 결과처럼 문장 단위로 끊어진 한국어 문서로 작성한다.
# (해커톤 데모 시나리오: 학부모가 가정통신문을 폰으로 촬영해서 업로드한다고 가정)
DUMMY_RAW_TEXT = (
    "2026학년도 가정통신문입니다. 지난주 체육 활동 안내 및 "
    "다음 주 현장학습 일정 변경에 대해 안내드립니다. "
    "학부모님의 협조 부탁드립니다."
)

# OCR 신뢰도 더미 값. 0.0 ~ 1.0 사이의 실수.
# 실제 OCR 엔진은 글자별/문장별로 신뢰도를 다르게 주지만,
# Mock 단계에서는 단일 값으로 단순화한다.
DUMMY_CONFIDENCE = 0.95

# Vision 모델에게 줄 시스템 프롬프트.
# - "텍스트만" 추출하라는 명확한 지시.
# - 추측/요약/번역 금지.
_OCR_SYSTEM_PROMPT = (
    "너는 이미지 OCR 어시스턴트다. 사용자가 보낸 이미지에서 보이는 한국어/영문 "
    "텍스트만 그대로 추출해 평문으로 반환해라. 추측·요약·번역하지 말고, "
    "줄바꿈은 가능한 원본 레이아웃을 유지해라. 텍스트 외 설명·마크다운 금지."
)


# =============================================================================
# OCRService — 클래스 기반 비즈니스 로직 계층
# =============================================================================
class OCRService:
    """이미지 → 텍스트 추출 책임을 가진 서비스 클래스.

    제공 메서드:
        - extract_text_from_image: 순수 Mock 경로 (외부 API 호출 없음).
        - extract_text_with_ai   : 키 있으면 OpenAI Vision, 없거나 실패 시 mock 폴백.

    추후 실제 OCR 엔진으로 교체할 때:
        같은 시그니처를 가진 다른 클래스(예: NaverClovaOCRService)를 만들고
        라우터의 인스턴스 생성 라인만 바꿔 주면 되도록 책임 경계를 좁게 유지한다.
    """

    # -------------------------------------------------------------------------
    # 메서드 1) 순수 Mock 경로
    # -------------------------------------------------------------------------
    async def extract_text_from_image(
        self,
        image: UploadFile,
        create_document: bool = False,
    ) -> dict[str, Any]:
        """업로드된 이미지에서 텍스트를 추출하는 Mock 메서드.

        실제 OCR 처리는 하지 않으며, 파일을 읽기만 하고 하드코딩된 결과를 반환한다.

        Args:
            image: 모바일에서 업로드된 이미지 파일.
                FastAPI 가 multipart/form-data 요청을 파싱해서 넘겨 준다.
            create_document: True 인 경우 추출 결과로 Document 레코드를 생성해야 함.
                현재 단계에서는 documents 도메인과 연동하지 않으므로 document_id 는 None.

        Returns:
            ocr_source_id, extracted_text, confidence, document_id 를 포함한 dict.
            라우터에서 이 dict 를 공통 응답 포맷의 `data` 필드에 그대로 넣어 응답한다.
        """

        # ---- 1) 업로드 파일 스트림을 읽어 둔다 -------------------------------
        # 실제 OCR 연동 시에는 여기서 받은 bytes 를 OCR 엔진에 그대로 넘기면 된다.
        # Mock 단계에서는 결과에 사용하지 않지만,
        # "파일이 정상적으로 도착했는지" 확인하는 의미로 한 번 읽어 둔다.
        _ = await image.read()

        # ---- 2) OCR 결과의 고유 식별자(ocr_source_id) 생성 -------------------
        # uuid4 는 무작위 기반 UUID. .hex 는 하이픈 없는 32자리 문자열.
        # 그중 앞 12자리만 잘라서 짧은 식별자로 사용한다 (가독성을 위함).
        # prefix "ocr_" 를 붙여 다른 도메인의 id 와 시각적으로 구분되게 한다.
        ocr_source_id = f"ocr_{uuid.uuid4().hex[:12]}"

        # ---- 3) Document 생성 여부 분기 --------------------------------------
        # document_id 는 기본적으로 None.
        # create_document=True 면 원래 documents 서비스에 OCR 결과로 새 Document 를
        # 만들어 달라고 요청해야 하지만, 지금은 Mock 단계라 실제 연동을 하지 않는다.
        document_id: Optional[int] = None
        if create_document:
            # TODO: documents.service 와 연동하여 OCR 결과로 Document 를 생성하고
            #       그 id 를 document_id 에 대입하도록 변경할 것.
            #       (현재는 models 수정 금지 / Mock 단계이므로 None 유지)
            document_id = None

        # ---- 4) 응답 데이터 dict 반환 ----------------------------------------
        # 키 이름은 프론트엔드/다른 팀과 합의된 스펙을 따른다.
        return {
            "ocr_source_id": ocr_source_id,
            "extracted_text": DUMMY_RAW_TEXT,
            "confidence": DUMMY_CONFIDENCE,
            "document_id": document_id,
        }

    # -------------------------------------------------------------------------
    # 내부 헬퍼: 이미지 → data URL
    # -------------------------------------------------------------------------
    @staticmethod
    def _image_bytes_to_data_url(image_bytes: bytes, content_type: str | None) -> str:
        """이미지 bytes 를 OpenAI Vision API 의 image_url 입력 포맷(data URL)으로 변환.

        OpenAI 는 외부 URL 또는 data:base64 형태를 받는다.
        파일을 다시 어디 업로드 안 하고 바로 base64 로 인라인 전달한다.
        """
        # content_type 이 비어있으면 image/png 로 가정 (대부분 OS 가 기본으로 잡아 줌).
        mime = content_type or "image/png"
        # base64 인코딩 후 ASCII 문자열로 디코드 (data URL 은 ASCII 문자열만 가능).
        b64 = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{mime};base64,{b64}"

    # -------------------------------------------------------------------------
    # 내부 헬퍼: OpenAI Vision 동기 호출
    # -------------------------------------------------------------------------
    def _call_openai_vision(self, image_bytes: bytes, content_type: str | None) -> str:
        """OpenAI Vision 으로 이미지에서 텍스트를 추출해 문자열로 반환.

        [동기 메서드]
            OpenAI SDK v1 의 동기 클라이언트를 사용. 호출자(extract_text_with_ai)가
            asyncio.to_thread 로 감싸서 이벤트 루프를 막지 않게 한다.
        """
        client = get_client()
        if client is None:
            raise RuntimeError("OpenAI client is not configured")

        # data URL 변환.
        data_url = self._image_bytes_to_data_url(image_bytes, content_type)

        # Vision Chat Completions 호출.
        # messages.content 가 멀티모달일 때는 list 안에 type=text / type=image_url
        # 항목들을 섞을 수 있다.
        completion = client.chat.completions.create(
            model=OPENAI_MODEL_VISION,
            messages=[
                {"role": "system", "content": _OCR_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "이 이미지의 텍스트를 추출해줘."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            # OCR 은 결정론적이어야 하므로 temperature 0.
            temperature=0,
        )
        # 응답 텍스트 추출. content 가 비어 있으면 빈 문자열.
        return completion.choices[0].message.content or ""

    # -------------------------------------------------------------------------
    # 메서드 2) 공개 진입점: 키 있으면 OpenAI, 없거나 실패 시 mock 폴백
    # -------------------------------------------------------------------------
    async def extract_text_with_ai(
        self,
        image: UploadFile,
        create_document: bool = False,
    ) -> dict[str, Any]:
        """공개 진입점: 키가 있으면 OpenAI Vision, 없거나 실패 시 mock.

        extract_text_from_image (mock) 와 동일한 응답 스키마를 반환하므로
        라우터/프론트는 동일하게 처리하면 된다.
        """
        # 파일 bytes 를 읽어 둔다 (mock/real 양쪽에서 공통으로 필요).
        image_bytes = await image.read()
        content_type = image.content_type

        # 공통 식별자.
        ocr_source_id = f"ocr_{uuid.uuid4().hex[:12]}"

        # document 연동은 아직 미구현. mock 과 동일하게 None.
        document_id: Optional[int] = None

        # 키가 없으면 mock 텍스트로 폴백.
        if get_client() is None:
            return {
                "ocr_source_id": ocr_source_id,
                "extracted_text": DUMMY_RAW_TEXT,
                "confidence": DUMMY_CONFIDENCE,
                "document_id": document_id,
                "_source": "mock",
            }

        # 실제 호출. 동기 SDK 를 별도 스레드로 보내 이벤트 루프 보호.
        try:
            text = await asyncio.to_thread(
                self._call_openai_vision, image_bytes, content_type
            )
        except Exception as exc:  # noqa: BLE001 — 폴백 대상
            logger.warning("OpenAI Vision OCR 실패 → mock 폴백: %s", exc)
            return {
                "ocr_source_id": ocr_source_id,
                "extracted_text": DUMMY_RAW_TEXT,
                "confidence": DUMMY_CONFIDENCE,
                "document_id": document_id,
                "_source": "mock_fallback",
                "_error": str(exc),
            }

        # 실제 결과 반환.
        # confidence 는 OpenAI 가 score 를 주지 않으므로 임시로 1.0 으로 둔다.
        # (추후 별도 신뢰도 추정 모델 도입 시 교체)
        return {
            "ocr_source_id": ocr_source_id,
            "extracted_text": text.strip(),
            "confidence": 1.0,
            "document_id": document_id,
            "_source": "openai",
            "_model": OPENAI_MODEL_VISION,
        }
