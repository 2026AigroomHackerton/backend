"""AI 문서 수정 서비스 모듈.

[역할]
    - 음성/텍스트 명령을 받아 "어떻게 수정할지"의 계획(EditOperation 리스트)을
      만들어 라우터 계층으로 돌려주는 비즈니스 로직 계층.
    - 라우터(routers/ai.py)는 HTTP 입출력만 담당하고, 실제 "무엇을 반환할지"는
      이 서비스 계층이 결정한다 (관심사 분리).

[협업 주의]
    - 본 파일은 팀장과 공유한다. 기존 함수를 수정/삭제하지 말고, 새 기능은
      함수 추가 형태로만 확장한다.
    - 백엔드 2(나)는 Mock(가짜) 응답 함수를 담당한다. 실제 LLM 연동(OpenAI/
      Anthropic API 호출)은 추후 별도 함수에서 진행하며, 이 함수는 그대로
      두거나 폴백/테스트 용도로 유지한다.
"""

# from __future__ import annotations
# - Python 3.10 미만 호환을 위해 타입 힌트(dict[str, Any] 등)를 문자열로 지연
#   평가(lazy evaluation)하게 한다. 3.13 환경에서는 사실상 no-op이지만
#   팀원의 로컬 파이썬 버전이 다양할 수 있으므로 안전하게 켜 둔다.
from __future__ import annotations

# typing.Any
# - dict[str, Any]에서 "값의 타입은 자유"임을 표시할 때 사용.
# - EditOperation의 value들은 문자열/불리언/리스트/딕셔너리가 섞여 있어서
#   엄격한 타입을 못 박지 않고 Any로 둔다 (Mock 단계라 스키마가 자주 바뀜).
from typing import Any

# json: OpenAI 의 응답 텍스트(JSON 문자열)를 dict 로 파싱할 때 사용.
import json
# logging: OpenAI 호출 실패 시 디버깅 로그를 남기기 위해 사용.
import logging

# 클라이언트 싱글톤 헬퍼. 키가 없을 때 None 을 돌려준다.
from app.core.openai_client import get_client
# 모델명 상수. 환경변수에서 주입된 값(.env 의 OPENAI_MODEL_TEXT).
from app.core.config import OPENAI_MODEL_TEXT

# 모듈 전용 로거. main.py 에서 logging.basicConfig 를 따로 안 했어도
# uvicorn 이 기본 핸들러를 붙여주므로 stderr 에 출력된다.
logger = logging.getLogger(__name__)


def generate_mock_edit_plan(
    document_id: str,
    command_text: str,
    scope: str,
) -> dict[str, Any]:
    """음성/텍스트 명령을 받아 EditOperation 형식의 가짜 수정 계획을 반환한다.

    [매개변수]
        document_id: 수정 대상 문서의 식별자. DB의 documents.id 또는 외부 문서
                     서비스의 ID. 현재 Mock 단계에서는 단순히 응답에 echo만
                     하고 별도 검증/조회는 하지 않는다.
        command_text: 사용자가 음성/텍스트로 입력한 자연어 명령.
                      예) "체육 활동 안내문을 환경정화로 바꿔줘"
        scope: 수정 범위. "document"(전체) | "section"(섹션) | "paragraph"
               (문단) 등을 상정하지만, Mock 단계에서는 자유 문자열로 받는다.

    [반환값]
        명세서(EditOperation)에 정의된 구조를 따르는 dict.
        - summary           : 수정 의도를 한 줄로 요약 (UI 토스트/확인 모달용)
        - edit_operations   : 실제 수정 단위 배열. 한 명령이 여러 수정을
                              유발할 수 있으므로 리스트.
        - preview_text      : 프런트엔드에서 미리보기용으로 보여줄 결과 텍스트
        - _echo             : 디버깅용. 입력값을 그대로 돌려보내 프런트/팀장이
                              요청이 잘 전달됐는지 빠르게 확인 가능. 실제
                              LLM 연동 시점에는 제거 예정.

    [Mock 정책]
        - LLM API를 호출하지 않는다. 외부 키 없이도 프런트엔드 통합 테스트가
          가능하도록 명세서 예시를 하드코딩하여 반환한다.
        - 입력 인자에 따라 결과가 달라지지 않는다 (의도된 단순화).
        - 실제 LLM 연동 시 동일 시그니처의 별도 함수(예: generate_edit_plan)
          로 교체하거나, 본 함수를 폴백으로 둔다.
    """
    # 명세서 예시 그대로의 EditOperation 페이로드.
    # 키 이름/순서는 프런트엔드와 합의된 스키마를 따른다.
    return {
        # summary: 사용자에게 "AI가 이렇게 이해했다"를 한 줄로 보여주는 요약문.
        "summary": "체육 활동 안내문을 환경정화 활동 안내문으로 변경",

        # edit_operations: 실제 적용할 수정 작업 배열.
        # 각 op는 독립적으로 사용자 확인(승인/거부)이 가능해야 하므로 리스트.
        "edit_operations": [
            {
                # operation_id: 프런트가 "어떤 op를 승인했는지" 추적하기 위한 ID.
                # Mock에서는 op_001 고정. 실제 LLM 연동 시 UUID 권장.
                "operation_id": "op_001",

                # type: 수정 종류. replace_section 외에 insert_paragraph,
                # delete_section, append_text 등 추가될 예정.
                "type": "replace_section",

                # target: 수정 대상 위치(섹션 제목/문단 식별자 등).
                "target": "활동 내용",

                # before_text / after_text: diff UI 렌더링용 원문/수정문.
                "before_text": "지난주 체육 활동 안내",
                "after_text": "이번 주 환경정화 활동 안내",

                # reason: AI가 왜 이렇게 수정했는지에 대한 자연어 근거.
                # 사용자 신뢰도 향상 + 잘못 이해한 경우 디버깅에 사용.
                "reason": "사용자가 활동 주제 변경을 요청함",

                # requires_user_confirm: 자동 적용 vs 사용자 확인 후 적용.
                # 민감한 수정(삭제/대체)은 True로 강제, 단순 오타 수정은
                # False로 두어 즉시 적용도 가능.
                "requires_user_confirm": True,
            }
        ],

        # preview_text: 모든 edit_operations를 적용했을 때의 최종 미리보기.
        # 프런트는 이 텍스트를 그대로 화면에 띄워 사용자 컨펌을 받는다.
        "preview_text": "수정된 전체 미리보기 텍스트...",

        # _echo: 디버깅 전용 필드. 언더스코어 prefix는 "공식 스키마가 아님"을
        # 의미하는 관례. 프런트는 무시하고, 우리만 콘솔/Network 탭에서
        # 입력이 제대로 도착했는지 확인하는 용도로 사용한다.
        "_echo": {
            "document_id": document_id,
            "command_text": command_text,
            "scope": scope,
        },
    }


# =============================================================================
# 실제 OpenAI 연동
# =============================================================================
# 위의 generate_mock_edit_plan 은 그대로 보존(폴백/테스트 용도).
# 아래 함수들은 실제 LLM 호출 경로를 새로 추가한 것.
#
# 호출 진입점은 generate_edit_plan() 이며, 키가 있으면 OpenAI 를 부르고
# 없거나 호출 실패 시 mock 으로 자동 폴백한다.
# 라우터는 generate_edit_plan() 한 함수만 알면 된다.
# =============================================================================


# LLM 에 보낼 시스템 프롬프트. 결과를 정해진 JSON 스키마로만 반환하도록 강제한다.
# - JSON 외 텍스트가 섞이지 않도록 "오직 JSON 만" 을 명시.
# - response_format={"type": "json_object"} 와 함께 쓰여 모델이 JSON 으로
#   확실히 응답하도록 한다.
_SYSTEM_PROMPT = """\
너는 한국어 학교 문서를 편집하는 어시스턴트다.
사용자의 자연어 명령(command_text)과 수정 범위(scope)를 받아
EditOperation JSON 객체를 만든다.

반드시 다음 스키마의 JSON 만 반환한다 (다른 설명/마크다운 금지):
{
  "summary": "한 줄 요약(한국어)",
  "edit_operations": [
    {
      "operation_id": "op_001 같은 짧은 식별자",
      "type": "replace_section | insert_paragraph | delete_section | append_text 중 하나",
      "target": "수정 대상 섹션/문단의 제목 또는 식별자",
      "before_text": "원문(추정)",
      "after_text": "수정 후 텍스트",
      "reason": "왜 이렇게 수정하는지 짧은 한국어 설명",
      "requires_user_confirm": true
    }
  ],
  "preview_text": "모든 수정을 적용한 후의 미리보기 텍스트"
}
"""


def _call_openai_for_edit(
    document_id: str,
    command_text: str,
    scope: str,
) -> dict[str, Any]:
    """OpenAI Chat Completions 로 EditOperation dict 를 생성한다.

    [내부 함수]
        generate_edit_plan() 에서만 호출. 외부에서 직접 부르지 말 것.

    Raises:
        Exception: OpenAI 호출 실패, JSON 파싱 실패 등. 호출자가 폴백 처리.
    """
    # 클라이언트 획득. None 일 가능성은 generate_edit_plan 에서 이미 걸러졌지만
    # 방어적으로 한 번 더 체크.
    client = get_client()
    if client is None:
        raise RuntimeError("OpenAI client is not configured")

    # 사용자 입력은 그대로 user 메시지로 전달.
    # f-string 으로 합치되, command_text 에 줄바꿈/특수문자가 들어와도
    # JSON 응답을 깨뜨리지 않는다 (모델이 JSON 출력만 하도록 시스템 프롬프트로 강제).
    user_msg = (
        f"document_id: {document_id}\n"
        f"scope: {scope}\n"
        f"command_text: {command_text}"
    )

    # Chat Completions 호출.
    # - response_format={"type": "json_object"} : 모델이 반드시 JSON 으로만 응답.
    # - temperature=0.2 : 편집은 결정론적 결과가 바람직하므로 낮게.
    completion = client.chat.completions.create(
        model=OPENAI_MODEL_TEXT,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )

    # 모델 응답에서 텍스트 추출. choices[0].message.content 가 JSON 문자열.
    raw = completion.choices[0].message.content or "{}"

    # JSON 문자열 → dict.
    # response_format 강제로 거의 항상 valid JSON 이지만, 만약 깨지면
    # JSONDecodeError 가 올라가 generate_edit_plan 의 except 에서 폴백된다.
    parsed: dict[str, Any] = json.loads(raw)

    # 디버깅용 echo 필드 추가 (mock 함수와 동일한 모양 유지).
    parsed["_echo"] = {
        "document_id": document_id,
        "command_text": command_text,
        "scope": scope,
        "_source": "openai",
        "_model": OPENAI_MODEL_TEXT,
    }
    return parsed


def generate_edit_plan(
    document_id: str,
    command_text: str,
    scope: str,
) -> dict[str, Any]:
    """공개 진입점: 실제 LLM 호출(가능하면) 또는 mock 응답을 반환.

    [폴백 정책]
        - OPENAI_API_KEY 가 없으면 mock 으로 즉시 폴백.
        - OpenAI 호출 중 예외가 나면 로그를 남기고 mock 으로 폴백.
          (해커톤 데모 중 네트워크/쿼터 문제로 데모가 멈추는 것을 방지)

    Args/Returns:
        generate_mock_edit_plan 과 동일한 시그니처.
    """
    # 키가 없으면 굳이 try 안 들어가고 바로 mock.
    if get_client() is None:
        return generate_mock_edit_plan(document_id, command_text, scope)

    # 키가 있으면 실제 호출 시도. 실패 시 mock 폴백.
    try:
        return _call_openai_for_edit(document_id, command_text, scope)
    except Exception as exc:  # noqa: BLE001 — 외부 호출은 모두 폴백 대상
        logger.warning("OpenAI 호출 실패 → mock 폴백: %s", exc)
        return generate_mock_edit_plan(document_id, command_text, scope)
