"""
프로필 도메인 비즈니스 로직.

라우터(`/api/profile`) 와 자동 채움 서비스(`autofill_service`) 가 공통으로 사용한다.
책임:
    - DB 행(raw dict) → 응답 dict 직렬화 (certifications JSON 파싱 포함)
    - 입력 dict → DB 저장용 dict 정규화 (certifications 직렬화)
    - 자동 채움 매칭에 쓰일 평탄한(flat) dict 제공
"""

from __future__ import annotations

import json
from typing import Any, Optional

from app.repositories import profile_repository

# 자동 채움이 다룰 10개 키 (라우터 응답/요청 본문의 표준 키 셋).
PROFILE_KEYS: tuple[str, ...] = (
    "name_ko",
    "name_en",
    "name_hanja",
    "phone",
    "email",
    "address",
    "rrn",
    "certifications",
    "occupation",
    "gender",
)


def _parse_certifications(raw: Optional[str]) -> list[dict]:
    """DB 의 certifications TEXT 를 list[dict] 로 역직렬화.

    잘못된 JSON 이면 빈 리스트로 fallback — 시연 환경에서 응답 500 을 막기 위함.
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _serialize_certifications(value: Any) -> Optional[str]:
    """입력값을 DB 저장용 JSON 문자열로 직렬화.

    - None 또는 빈 리스트 → None (NULL 저장)
    - list[dict] → json.dumps (한국어 보존을 위해 ensure_ascii=False)
    - 이미 문자열로 오는 경우(클라이언트가 직접 직렬화) → 그대로 보관 (단 빈 문자열은 None)
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, list):
        if not value:
            return None
        return json.dumps(value, ensure_ascii=False)
    # 그 외 타입은 받지 않음 — 라우터의 Pydantic 검증에서 막아주지만 보수적으로 None.
    return None


def get_profile(user_id: int) -> dict:
    """프로필을 라우터 응답용 dict 로 반환.

    행이 없으면(시드 누락 등) 모든 키를 None 으로 채워 반환 → 프런트가 '빈 프로필' 처리 가능.
    certifications 는 list[dict] 로 펼쳐서 응답 (DB 의 JSON TEXT 가 그대로 노출되지 않게).
    """
    row = profile_repository.get_profile(user_id=user_id)
    if row is None:
        return {key: None for key in PROFILE_KEYS} | {
            "user_id": user_id,
            "certifications": [],
        }

    return {
        "user_id": row["user_id"],
        "name_ko": row["name_ko"],
        "name_en": row["name_en"],
        "name_hanja": row["name_hanja"],
        "phone": row["phone"],
        "email": row["email"],
        "address": row["address"],
        "rrn": row["rrn"],
        "certifications": _parse_certifications(row["certifications"]),
        "occupation": row["occupation"],
        "gender": row["gender"],
    }


def update_profile(user_id: int, payload: dict) -> dict:
    """프로필을 전체 교체(UPSERT) 후 갱신된 상태를 반환한다.

    PUT semantics — payload 에 없는 키는 NULL 로 비워진다.
    certifications 는 list[dict] 로 들어와 DB 저장용 JSON 문자열로 변환.
    """
    db_fields: dict = {}
    for key in PROFILE_KEYS:
        if key == "certifications":
            db_fields[key] = _serialize_certifications(payload.get(key))
        else:
            value = payload.get(key)
            # 빈 문자열도 사용자가 의도적으로 비웠을 수 있어 그대로 보존하지 않고 None.
            if isinstance(value, str) and value.strip() == "":
                db_fields[key] = None
            else:
                db_fields[key] = value

    profile_repository.upsert_profile(user_id=user_id, fields=db_fields)
    return get_profile(user_id=user_id)


def get_profile_for_autofill(user_id: int) -> dict[str, Optional[str]]:
    """자동 채움 매칭용 평탄 dict.

    각 키에 '양식 빈칸을 채울 한 줄 문자열' 만 담는다.
    certifications 는 첫 자격증 이름만 노출 (양식의 '자격증' 빈칸은 보통 단일 항목 가정).
    필요하면 여기서 추가 가공(주소 첫 줄만 자르기 등) 가능.
    """
    profile = get_profile(user_id=user_id)
    certifications = profile.get("certifications") or []
    cert_summary = certifications[0]["name"] if certifications else None

    return {
        "name_ko": profile["name_ko"],
        "name_en": profile["name_en"],
        "name_hanja": profile["name_hanja"],
        "phone": profile["phone"],
        "email": profile["email"],
        "address": profile["address"],
        "rrn": profile["rrn"],
        "certifications": cert_summary,
        "occupation": profile["occupation"],
        "gender": profile["gender"],
    }
