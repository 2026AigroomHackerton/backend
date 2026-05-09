"""
프로필 API 라우터.

엔드포인트:
    - GET  /api/profile : 데모 사용자(user_id=1) 프로필 조회
    - PUT  /api/profile : 프로필 전체 교체(보내지 않은 필드는 NULL 로 비워짐)

응답 envelope 은 documents 라우터와 동일한 4-key 형식
({"success", "data", "message", "error"}) 을 따른다.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services import profile_service

DEMO_USER_ID = 1

router = APIRouter(prefix="/api/profile", tags=["profile"])


# ---------------------------------------------------------------------------
# 요청/응답 envelope 헬퍼 (documents 라우터와 동일 포맷)
# ---------------------------------------------------------------------------
def _success_response(data, status_code: int = status.HTTP_200_OK) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": True, "data": data, "message": "", "error": None},
    )


def _error_response(message: str, code: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "data": None, "message": message, "error": code},
    )


# ---------------------------------------------------------------------------
# Pydantic 요청 본문 스키마
# ---------------------------------------------------------------------------
class CertificationItem(BaseModel):
    """자격증 한 건. 4개 필드 모두 optional — 부분 입력 허용."""

    name: str | None = Field(default=None, description="자격증명")
    acquired_date: str | None = Field(default=None, description="취득일 (ISO YYYY-MM-DD)")
    cert_number: str | None = Field(default=None, description="자격증 번호")
    issuer: str | None = Field(default=None, description="발급기관")


class UpdateProfileRequest(BaseModel):
    """PUT /api/profile 요청 바디.

    모든 필드는 optional 이며, 누락된 필드는 DB 에서 NULL 로 덮인다(전체 교체 의미).
    """

    name_ko: str | None = Field(default=None, description="성명(한글)")
    name_en: str | None = Field(default=None, description="성명(영문)")
    name_hanja: str | None = Field(default=None, description="성명(한자)")
    phone: str | None = Field(default=None, description="전화번호")
    email: str | None = Field(default=None, description="이메일")
    address: str | None = Field(default=None, description="주소")
    rrn: str | None = Field(default=None, description="주민등록번호 (시연용 평문)")
    certifications: list[CertificationItem] | None = Field(
        default=None, description="자격증 목록"
    )
    occupation: str | None = Field(default=None, description="직업")
    gender: str | None = Field(default=None, description="성별")


# ---------------------------------------------------------------------------
# 엔드포인트
# ---------------------------------------------------------------------------
@router.get("")
async def get_profile() -> JSONResponse:
    """데모 사용자(user_id=1) 의 프로필을 조회한다.

    응답 data 형태:
        {
            "user_id": 1,
            "name_ko": "...", "name_en": "...", "name_hanja": "...",
            "phone": "...", "email": "...", "address": "...",
            "rrn": "...",
            "certifications": [{"name", "acquired_date", "cert_number", "issuer"}, ...],
            "occupation": "...", "gender": "..."
        }
    """
    try:
        profile = profile_service.get_profile(user_id=DEMO_USER_ID)
    except Exception as exc:  # noqa: BLE001
        return _error_response(
            message=f"프로필 조회 중 오류가 발생했습니다: {exc}",
            code="INTERNAL_SERVER_ERROR",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return _success_response(profile, status_code=status.HTTP_200_OK)


@router.put("")
async def update_profile(body: UpdateProfileRequest) -> JSONResponse:
    """프로필을 전체 교체(UPSERT) 한다.

    PUT 의미상 요청 본문에 없는 키는 NULL 로 비워진다.
    부분 갱신을 원하면 GET 으로 현재 값을 가져와 머지 후 PUT 으로 보내는 방식 권장.
    """
    try:
        # Pydantic v2 의 model_dump() — list[BaseModel] 도 dict 리스트로 평탄화.
        # exclude_unset 을 쓰지 않는 이유: PUT 은 누락 = 비움 의미를 가져야 하기 때문.
        payload = body.model_dump()
        updated = profile_service.update_profile(
            user_id=DEMO_USER_ID, payload=payload
        )
    except Exception as exc:  # noqa: BLE001
        return _error_response(
            message=f"프로필 갱신 중 오류가 발생했습니다: {exc}",
            code="INTERNAL_SERVER_ERROR",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return _success_response(updated, status_code=status.HTTP_200_OK)
