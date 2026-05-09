# [백엔드2 담당] 수정 허용 파일 - feature/backend-ocr-voice-storage 브랜치
"""OCR Service (Class-based, real integration capable).

[책임]
    이미지 → 텍스트 추출 도메인의 비즈니스 로직 계층.
    실제 OpenAI Vision OCR 경로와, 키/라이브러리/네트워크 사정으로 호출이 불가능할
    때의 Mock 폴백 경로를 함께 제공한다.

[real path]
    - OPENAI_API_KEY 가 .env 에 있고 openai SDK 가 설치되어 있을 때 자동 활성화.
    - 모델은 OPENAI_MODEL_VISION (기본 gpt-4o-mini) 을 사용.

[mock path — fallback]
    - 키가 없거나 SDK 호출이 실패한 경우, 명세 더미 텍스트(DUMMY_OCR_TEXT) 와
      고정 ocr_source_id "ocr_mock_001" 을 그대로 반환.
    - 응답 스키마(ocr_source_id, extracted_text, confidence, document_id,
      image_filename) 는 real/mock 동일하므로 라우터/프론트는 분기를 둘 필요 없다.
    - 디버깅을 위해 응답에 _source 필드를 부여한다 ("openai"|"mock"|"mock_fallback").

[유지된 Mock 영역]
    get_result(), confirm_result() 는 OCR 결과 저장소(DB) 가 필요한 메서드인데,
    DB 모델은 절대 수정 금지 영역이라 본 PR 에서는 Mock 으로 둔다.
    실제 구현 시 본 메서드 본문만 교체하면 라우터는 영향 받지 않는다.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from typing import Any

from fastapi import UploadFile

# 기존 인프라 사용. core/* 는 본 PR 의 수정 허용 범위가 아니므로 import 만.
# get_client(): OpenAI 클라이언트 싱글톤 또는 None.
# OPENAI_MODEL_VISION: 사용할 vision 모델명.
from app.core.openai_client import get_client
from app.core.config import OPENAI_MODEL_VISION

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# 더미 텍스트 / 콘텐츠 타입 화이트리스트 / Vision 시스템 프롬프트
# -----------------------------------------------------------------------------
# 명세 [기능 2] 에서 정의된 가정통신문 더미. mock 폴백에서 그대로 반환.
DUMMY_OCR_TEXT = (
    "2026학년도 가정통신문\n"
    "\n"
    "안녕하십니까. 학부모님의 가정에 건강과 행복이 가득하기를 바랍니다.\n"
    "\n"
    "이번 주 활동 안내\n"
    "- 활동명: 환경정화 활동\n"
    "- 일시: 2026년 5월 20일 (화) 오전 10시\n"
    "- 장소: 학교 주변 공원\n"
    "- 준비물: 편한 복장, 장갑\n"
    "\n"
    "참가 여부를 5월 15일까지 담임 선생님께 알려주시기 바랍니다.\n"
    "\n"
    "담당 교사: 홍길동\n"
    "연락처: 010-1234-5678"
)

# 라우터에서 화이트리스트 검증에 쓰는 set. 명세상 4종만 허용.
ALLOWED_IMAGE_CONTENT_TYPES: set[str] = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}

# Vision 모델용 시스템 프롬프트.
# - "텍스트만" 추출하라고 명시.
# - 추측/요약/번역 금지.
# - 줄바꿈을 가능한 한 원본대로 유지.
_OCR_SYSTEM_PROMPT = (
    "너는 이미지 OCR 어시스턴트다. 사용자가 보낸 이미지에서 보이는 한국어/영문 "
    "텍스트만 그대로 추출해 평문으로 반환해라. 추측·요약·번역하지 말고, "
    "줄바꿈은 가능한 원본 레이아웃을 유지해라. 텍스트 외 설명·마크다운 금지."
)


# =============================================================================
# OcrService
# =============================================================================
class OcrService:
    """OCR 텍스트 추출/조회/확정 책임 서비스.

    제공 메서드:
        - extract_text(image_file)              : 이미지 → 텍스트 (real or mock).
        - get_result(ocr_source_id)             : 결과 단건 조회 (Mock — DB TODO).
        - confirm_result(ocr_source_id, edited) : 사용자 수정본 확정 (Mock — DB TODO).
    """

    # =========================================================================
    # Public: extract_text — real Vision 우선, 실패 시 mock 폴백
    # =========================================================================
    async def extract_text(self, image_file: UploadFile) -> dict[str, Any]:
        """이미지에서 텍스트를 추출한다.

        키가 있으면 OpenAI Vision 으로 실제 OCR 을 수행하고, 키가 없거나 호출이
        실패하면 명세 더미 텍스트로 폴백한다. 응답 스키마는 두 경로가 동일.
        """
        # 업로드 파일 bytes 와 content_type, filename 을 한 번에 확보.
        # (real/mock 양쪽에서 공통으로 필요)
        image_bytes = await image_file.read()
        content_type = image_file.content_type
        filename = image_file.filename

        # 1) Real path 시도 — 클라이언트가 None 이 아니면 키가 살아 있는 상태.
        client = get_client()
        if client is not None:
            try:
                # 동기 SDK 를 별도 스레드로 보내 이벤트 루프 보호.
                text, model_used = await asyncio.to_thread(
                    self._call_openai_vision, image_bytes, content_type
                )
                # OpenAI 가 빈 텍스트를 돌려준 케이스도 가끔 있음 → 더미로 보정.
                stripped = (text or "").strip()
                return {
                    "ocr_source_id": f"ocr_{uuid.uuid4().hex[:12]}",
                    "extracted_text": stripped or DUMMY_OCR_TEXT,
                    # OpenAI 는 글자별 score 를 주지 않으므로 1.0 으로 표기.
                    # (추후 ensemble/heuristic 으로 추정값 도입 가능)
                    "confidence": 1.0,
                    "document_id": None,  # documents 도메인 미연동
                    "image_filename": filename,
                    "_source": "openai",
                    "_model": model_used,
                }
            except Exception as exc:  # noqa: BLE001 — 폴백 대상
                # API 호출이 망가진 경우(키 만료, 쿼터 초과, 네트워크 등) 로그 + mock.
                logger.warning("OpenAI Vision OCR 실패 → mock 폴백: %s", exc)
                return self._mock_response(filename, error=str(exc))

        # 2) Mock path — 키 없음 또는 SDK 미설치.
        return self._mock_response(filename)

    # =========================================================================
    # Public: get_result / confirm_result (DB 연동 전이라 Mock 유지)
    # =========================================================================
    def get_result(self, ocr_source_id: str) -> dict[str, Any]:
        """OCR 결과를 ocr_source_id 로 단건 조회 (Mock).

        TODO(실제 DB 연동):
            - DB 또는 캐시에서 ocr_source_id 로 raw_text/cleaned_text/image_path 조회.
            - 없으면 도메인 에러 → 라우터에서 404 처리.
            - documents 도메인이 절대 수정 금지라 본 PR 에서는 Mock 으로만 둔다.
        """
        return {
            "ocr_source_id": ocr_source_id,
            "raw_text": DUMMY_OCR_TEXT,
            "cleaned_text": DUMMY_OCR_TEXT,
            "image_path": "/uploads/ocr-images/mock_image.jpg",
        }

    def confirm_result(self, ocr_source_id: str, edited_text: str) -> dict[str, Any]:
        """사용자 수정본을 확정 상태로 저장 (Mock).

        TODO(실제 DB 연동):
            - OCR 레코드(ocr_source_id) 의 confirmed_text=edited_text,
              status='confirmed' 로 UPDATE.
            - 레코드 없으면 도메인 에러 → 라우터에서 404 처리.
        """
        return {
            "ocr_source_id": ocr_source_id,
            "confirmed_text": edited_text,
            "status": "confirmed",
        }

    # =========================================================================
    # Internal helpers
    # =========================================================================
    def _mock_response(
        self,
        filename: str | None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """mock 응답 빌더 — 키 없음/호출 실패 양쪽에서 재사용."""
        result: dict[str, Any] = {
            "ocr_source_id": "ocr_mock_001",
            "extracted_text": DUMMY_OCR_TEXT,
            "confidence": 0.91,
            "document_id": None,
            "image_filename": filename,
            "_source": "mock_fallback" if error else "mock",
        }
        if error:
            # 디버깅용 — 실제 운영에서는 sanitize 가 필요할 수 있음.
            result["_error"] = error
        return result

    @staticmethod
    def _image_bytes_to_data_url(image_bytes: bytes, content_type: str | None) -> str:
        """이미지 bytes 를 OpenAI Vision API 의 image_url 입력(data URL) 으로 변환.

        OpenAI 는 외부 URL 또는 data:base64 형태를 받는다. 별도 업로드 없이
        bytes 를 직접 인라인 전달.
        """
        mime = content_type or "image/png"
        b64 = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{mime};base64,{b64}"

    def _call_openai_vision(
        self,
        image_bytes: bytes,
        content_type: str | None,
    ) -> tuple[str, str]:
        """OpenAI Vision 으로 이미지 → 텍스트.

        [동기 메서드]
            openai SDK v1 의 동기 클라이언트 사용. extract_text() 가
            asyncio.to_thread 로 감싸서 호출하므로 이벤트 루프는 막히지 않는다.

        Returns:
            (extracted_text, model_used) 튜플.
        """
        client = get_client()
        if client is None:
            raise RuntimeError("OpenAI client is not configured")

        data_url = self._image_bytes_to_data_url(image_bytes, content_type)

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
        text = completion.choices[0].message.content or ""
        return text, OPENAI_MODEL_VISION
