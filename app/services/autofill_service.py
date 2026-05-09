"""
양식 자동 채움 서비스.

[흐름]
    1. document_texts.extracted_text 를 읽어 양식 빈칸 라벨을 검출한다.
    2. 라벨을 프로필 키(name_ko/address/...) 로 매핑한다.
       - 1차: 규칙 기반 동의어 사전 (이 모듈 내 LABEL_RULES).
       - 2차: 미매칭 라벨에 한해 LLM 폴백 (OpenAI 키 없으면 skip).
    3. 매핑 성공한 라벨 → extracted_fields 행으로 INSERT.
       - suggestion 은 프로필에서 가져온 값.
       - confidence 는 규칙 매칭 1.0, LLM 매칭 0.6.
       - status 는 항상 'pending' (사용자 승인 전).

[사용자 승인 정책 (현재 단계)]
    재실행마다 기존 행을 일괄 DELETE 후 INSERT 한다 (REPLACE 의미).
    이미 사용자가 accepted/edited 한 행 보존은 본 PR 범위 외(TODO).

[트리거 시점]
    - document_service.upload_document : OCR 직후 1회 자동 호출.
    - document_service.reindex_document(force=True) : 재인덱싱 시 1회 자동 호출.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from app.core.config import OPENAI_MODEL_TEXT
from app.core.openai_client import get_client
from app.repositories import extracted_field_repository
from app.services import profile_service

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 라벨 → 프로필 키 매핑 사전 (규칙 기반)
# ---------------------------------------------------------------------------
# 키는 "양식에 등장할 수 있는 라벨" 의 다양한 변형. 매칭은 정규화(소문자/공백제거) 기준.
# 같은 라벨에 대한 다국어/약어/축약 표기를 한 곳에 모아 새 양식이 등장해도 사전만 늘리면 됨.
LABEL_RULES: dict[str, str] = {
    # name_ko (한글 성명)
    "성명": "name_ko",
    "이름": "name_ko",
    "한글성명": "name_ko",
    "한글이름": "name_ko",
    "성명한글": "name_ko",
    "namekorean": "name_ko",
    "name(ko)": "name_ko",
    # name_en (영문 성명)
    "영문성명": "name_en",
    "영문이름": "name_en",
    "성명영문": "name_en",
    "englishname": "name_en",
    "name(en)": "name_en",
    "name(english)": "name_en",
    # name_hanja (한자 성명)
    "한자성명": "name_hanja",
    "한자이름": "name_hanja",
    "성명한자": "name_hanja",
    "한자": "name_hanja",
    "漢字": "name_hanja",
    "name(hanja)": "name_hanja",
    # phone
    "전화번호": "phone",
    "전화": "phone",
    "휴대폰": "phone",
    "휴대폰번호": "phone",
    "휴대전화": "phone",
    "핸드폰": "phone",
    "핸드폰번호": "phone",
    "연락처": "phone",
    "phone": "phone",
    "phonenumber": "phone",
    "tel": "phone",
    "mobile": "phone",
    "cellphone": "phone",
    # email
    "이메일": "email",
    "메일": "email",
    "전자우편": "email",
    "email": "email",
    "e-mail": "email",
    "mail": "email",
    # address
    "주소": "address",
    "주소지": "address",
    "거주지": "address",
    "주거지": "address",
    "자택주소": "address",
    "현주소": "address",
    "도로명주소": "address",
    "address": "address",
    "homeaddress": "address",
    # rrn (주민등록번호)
    "주민번호": "rrn",
    "주민등록번호": "rrn",
    "주민등록번호앞자리": "rrn",
    "rrn": "rrn",
    "residentregistrationnumber": "rrn",
    # certifications
    "자격증": "certifications",
    "자격": "certifications",
    "보유자격": "certifications",
    "보유자격증": "certifications",
    "자격사항": "certifications",
    "certificate": "certifications",
    "certification": "certifications",
    "license": "certifications",
    # occupation
    "직업": "occupation",
    "직장": "occupation",
    "직위": "occupation",
    "직종": "occupation",
    "occupation": "occupation",
    "job": "occupation",
    "profession": "occupation",
    # gender
    "성별": "gender",
    "gender": "gender",
    "sex": "gender",
}


# 프로필 키별 field_type — 프런트엔드의 입력 컴포넌트 선택에 사용 가능.
FIELD_TYPE_BY_KEY: dict[str, str] = {
    "name_ko": "text",
    "name_en": "text",
    "name_hanja": "text",
    "phone": "text",
    "email": "text",
    "address": "text",
    "rrn": "text",
    "certifications": "text",
    "occupation": "text",
    "gender": "text",
}


# ---------------------------------------------------------------------------
# 라벨 추출
# ---------------------------------------------------------------------------
# 양식 텍스트 안에서 "라벨: ___" / "라벨 [   ]" / "라벨 (    )" / "라벨\n____"
# 형태의 빈칸 표시를 찾는다. OCR 결과는 노이즈가 많아 너무 엄격하면 0건이 되므로
# `라벨이 등장하면 빈칸으로 간주` 하는 관대한 모드를 기본으로 한다.
#
# 한국어 라벨은 보통 짧으므로 라벨 길이를 1~10자로 제한해 본문 단락이 라벨로 잡히지 않게 한다.
_LABEL_TOKEN = r"[가-힣A-Za-z][가-힣A-Za-z0-9 \(\)\-]{0,12}"
_BLANK_MARKER = r"(?:[:：]|\[|\(|_{2,}|\.{2,}|\s{4,}|$)"
_BLANK_LABEL_PATTERN = re.compile(
    rf"({_LABEL_TOKEN})\s*{_BLANK_MARKER}",
    re.MULTILINE,
)


def _normalize(label: str) -> str:
    """매칭 비교를 위한 라벨 정규화 — 소문자 + 공백 제거."""
    return re.sub(r"\s+", "", label).lower()


def _extract_candidate_labels(text: str) -> list[str]:
    """본문 텍스트에서 빈칸 후보 라벨을 추출한다.

    중복 라벨은 등장 순서를 보존한 채 1회만 남긴다.
    너무 짧거나(1글자) 숫자만 있는 후보는 노이즈이므로 제외.
    """
    if not text:
        return []

    seen: set[str] = set()
    ordered: list[str] = []
    for match in _BLANK_LABEL_PATTERN.finditer(text):
        raw = match.group(1).strip()
        if len(raw) < 2:
            continue
        if raw.isdigit():
            continue
        normalized = _normalize(raw)
        if normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(raw)
    return ordered


# ---------------------------------------------------------------------------
# 매핑 (규칙 + LLM 폴백)
# ---------------------------------------------------------------------------
def _rule_match(label: str) -> Optional[str]:
    """라벨 → 프로필 키 (규칙 사전 기반).

    정규화한 라벨이 LABEL_RULES 키와 정확히 일치하거나, 라벨 정규화 문자열에 사전 키가 substring 으로
    포함되면 매칭으로 간주한다(예: "한자성명" 안에 "성명" 이 들어 있어도 한자 우선 적용 위해 긴 키 우선).
    """
    normalized = _normalize(label)
    if not normalized:
        return None

    # 정확 일치 우선.
    if normalized in LABEL_RULES:
        return LABEL_RULES[normalized]

    # substring 매칭은 긴 키부터 시도해 더 구체적인 라벨이 우선되도록 한다.
    for rule_key in sorted(LABEL_RULES.keys(), key=len, reverse=True):
        if rule_key in normalized:
            return LABEL_RULES[rule_key]

    return None


def _llm_fallback_match(unmapped_labels: list[str]) -> dict[str, Optional[str]]:
    """규칙으로 못 매칭한 라벨을 LLM 으로 분류.

    OpenAI 키가 없거나 호출 실패 시 빈 dict 반환 → 그 라벨은 자동 채움에서 제외.
    LLM 응답은 JSON 형식 강제: {"<라벨>": "<프로필키>" | null, ...}.
    """
    if not unmapped_labels:
        return {}

    client = get_client()
    if client is None:
        logger.info("LLM 폴백 skip — OpenAI 키 미설정")
        return {}

    profile_keys = [
        "name_ko", "name_en", "name_hanja", "phone", "email",
        "address", "rrn", "certifications", "occupation", "gender",
    ]

    system_prompt = (
        "당신은 한국어 양식의 라벨을 정해진 프로필 키로 분류하는 도구입니다. "
        "각 라벨에 대해 가장 잘 맞는 프로필 키 하나를 선택하세요. "
        "어느 키와도 분명히 매칭되지 않으면 null 을 반환하세요. "
        "응답은 반드시 JSON 객체 한 개만 출력하세요."
    )
    user_prompt = (
        f"프로필 키 후보: {profile_keys}\n"
        f"라벨 목록: {unmapped_labels}\n"
        '응답 형식 예: {"성명": "name_ko", "기타": null}'
    )

    try:
        completion = client.chat.completions.create(
            model=OPENAI_MODEL_TEXT,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        content = completion.choices[0].message.content or "{}"
        parsed = json.loads(content)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM 폴백 매칭 실패: %s", exc)
        return {}

    if not isinstance(parsed, dict):
        return {}

    # 응답 검증: 값이 허용된 프로필 키 또는 null 인 항목만 유지.
    cleaned: dict[str, Optional[str]] = {}
    for label, value in parsed.items():
        if value is None:
            cleaned[label] = None
        elif isinstance(value, str) and value in profile_keys:
            cleaned[label] = value
    return cleaned


# ---------------------------------------------------------------------------
# 메인 진입점 — 트리거 측에서 호출
# ---------------------------------------------------------------------------
def autofill_document(
    document_id: int,
    user_id: int,
    extracted_text: Optional[str],
) -> list[dict]:
    """문서 본문에서 양식 빈칸을 검출해 프로필 값으로 자동 채운다.

    Args:
        document_id: 대상 문서 PK.
        user_id: 프로필을 가져올 사용자 PK (현재 MVP 는 항상 1).
        extracted_text: document_texts.extracted_text 본문. None/빈 문자열이면 NO-OP.

    Returns:
        INSERT 된 extracted_fields 행 dict 리스트 (id 포함).
        라벨이 0건이거나 프로필이 비어 있으면 빈 리스트.

    Side effects:
        해당 document_id 의 기존 extracted_fields 행을 모두 삭제한 뒤 새 행으로 교체.
    """
    if not extracted_text or not extracted_text.strip():
        # 본문 자체가 없으면 자동 채움 의미 없음. 기존 stale 데이터만 정리.
        extracted_field_repository.delete_fields_by_document(document_id=document_id)
        return []

    profile = profile_service.get_profile_for_autofill(user_id=user_id)

    # ---- 1) 라벨 후보 추출 ----
    candidate_labels = _extract_candidate_labels(extracted_text)
    if not candidate_labels:
        extracted_field_repository.delete_fields_by_document(document_id=document_id)
        return []

    # ---- 2) 규칙 매칭 ----
    matched: list[tuple[str, str, float]] = []  # (label, profile_key, confidence)
    unmapped: list[str] = []
    for label in candidate_labels:
        key = _rule_match(label)
        if key is not None:
            matched.append((label, key, 1.0))
        else:
            unmapped.append(label)

    # ---- 3) LLM 폴백 매칭 (미매칭만) ----
    if unmapped:
        llm_map = _llm_fallback_match(unmapped)
        for label in unmapped:
            key = llm_map.get(label)
            if key is not None:
                matched.append((label, key, 0.6))

    # ---- 4) suggestion 채움 — 프로필에 값이 있는 키만 살림 ----
    rows_to_insert: list[dict] = []
    used_keys: set[str] = set()
    for label, key, confidence in matched:
        # 같은 프로필 키가 여러 라벨에 매칭됐으면 첫 번째만 채택 (응답 노이즈 방지).
        if key in used_keys:
            continue
        suggestion = profile.get(key)
        if suggestion is None or suggestion == "":
            continue
        used_keys.add(key)
        rows_to_insert.append(
            {
                "document_id": document_id,
                "label": label,
                "field_type": FIELD_TYPE_BY_KEY.get(key, "text"),
                "suggestion": suggestion,
                "confidence": confidence,
                "status": "pending",
            }
        )

    # ---- 5) DB 반영 (REPLACE 의미: 기존 행 전부 삭제 후 새 행 INSERT) ----
    extracted_field_repository.delete_fields_by_document(document_id=document_id)
    extracted_field_repository.insert_fields(rows_to_insert)

    # 응답에는 INSERT 후 id 까지 포함된 형태가 필요하므로 다시 SELECT.
    return extracted_field_repository.list_fields_by_document(document_id=document_id)
