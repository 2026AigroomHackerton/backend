# =============================================================================
# voice_service.py
# -----------------------------------------------------------------------------
# "음성/텍스트 명령" 도메인의 비즈니스 로직 계층(Service Layer).
#
# 라우터(voice.py)는 HTTP 요청/응답 형태만 책임지고, 실제 "무엇을 할지"의
# 로직은 이 파일이 담당한다. 해커톤 MVP 단계라 DB 연결 없이 더미 값을
# 반환하는 Mock 구현으로 작성되어 있으며, 나중에 DB 연동이 추가될 때
# 이 파일의 함수 본문만 교체하면 라우터는 영향을 받지 않는다.
# =============================================================================

# `from __future__ import annotations` 는 타입 힌트를 "문자열"로 지연 평가하게
# 만들어, Python 3.9 이전 스타일의 제네릭(list[dict] 등)을 일관되게 쓸 수
# 있게 해 준다. 또 import 순환 문제를 줄이는 데 도움이 된다.
from __future__ import annotations


def create_voice_command(
    document_id: str,
    transcript: str,
    input_type: str,
) -> dict:
    """
    새 음성/텍스트 명령을 "저장한 척"하고 결과를 반환하는 Mock 함수.

    실제 구현 시에는 이 자리에서:
      1) DB 세션을 받아 VoiceCommand 레코드를 INSERT
      2) 생성된 PK(voice_command_id)를 돌려받음
    의 절차가 들어가야 한다. MVP에서는 DB 모델을 건드리지 않기 위해
    고정된 더미 ID("vc_001")를 반환한다.

    Parameters
    ----------
    document_id : str
        명령이 적용될 대상 문서의 ID.
    transcript : str
        사용자가 말하거나 입력한 명령 텍스트.
    input_type : str
        "text" 또는 "voice". 라우터 단의 Pydantic 스키마가 이미 검증한다.

    Returns
    -------
    dict
        라우터가 그대로 공통 응답 포맷의 `data` 자리에 끼워 넣는 dict.
    """
    # 더미 응답. 실제 DB 연동 전까지는 항상 같은 값을 돌려 주어도
    # 프론트엔드/다른 백엔드 팀원이 인터페이스에 맞춰 통합 테스트가 가능하다.
    return {
        "voice_command_id": "vc_001",  # 고정 더미 PK
        "document_id": document_id,    # 요청을 그대로 echo (디버깅 편의)
        "transcript": transcript,
        "input_type": input_type,
        "status": "accepted",          # 처리 상태 — 추후 "pending"/"done" 등으로 확장 가능
    }


def list_voice_commands(document_id: str) -> list[dict]:
    """
    특정 문서에 대한 음성/텍스트 명령 이력을 조회하는 Mock 함수.

    실제 구현 시:
      SELECT * FROM voice_commands WHERE document_id = :document_id ORDER BY created_at DESC
    의 결과를 dict 리스트로 변환해 반환해야 한다. 지금은 요청 사양에
    명시된 더미 이력 한 건만 돌려준다.

    Parameters
    ----------
    document_id : str
        조회 대상 문서 ID. 현재 Mock에서는 사용되지 않지만, 시그니처는
        실제 구현과 동일하게 유지해 라우터를 두 번 손대지 않게 한다.

    Returns
    -------
    list[dict]
        명령 이력 리스트. 한 항목은 {command_id, transcript} 키를 가진다.
    """
    # 사양서 예시를 그대로 사용 — 프론트가 화면 모킹할 때 바로 쓸 수 있는 값.
    return [
        {
            "command_id": "vc_001",
            "transcript": "환경정화 활동으로 바꿔줘",
        }
    ]
