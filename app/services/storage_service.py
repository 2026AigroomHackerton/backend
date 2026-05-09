"""External Storage Mock Service.

Google Drive, Notion 등 외부 저장소 연동의 뼈대 단계.
실제 OAuth/연동 코드는 포함하지 않으며, 하드코딩된 provider 목록만 반환한다.
추후 실제 연동 시 이 모듈에서 각 provider 의 status 와 메타데이터를 갱신한다.
"""

# Python 3.10+ 의 PEP 604 스타일 타입 힌트(`list[dict]` 등)를 런타임 평가가 아닌
# 문자열로 다루도록 한다. 호환성과 순환 import 방지를 위해 프로젝트 전반에서 사용.
from __future__ import annotations

# dict 의 value 타입을 임의 타입으로 허용하기 위해 typing.Any 를 import.
# (provider 항목에 향후 문자열/숫자/리스트 등 다양한 메타데이터가 들어갈 수 있음)
from typing import Any

# ─────────────────────────────────────────────────────────────────────
# 외부 저장소 provider 더미 목록.
#
# - 모듈 private 상수(`_` prefix)로 두어 외부에서는 `list_providers()` 함수만 쓰도록 강제.
# - status 값은 현재 단계에서는 모두 "coming_soon" 으로 고정.
#   향후 실제 OAuth 연동이 붙으면 "connected" / "disconnected" / "error" 등으로 확장 예정.
# - provider 식별자(snake_case) 는 프론트엔드/문서와 합의된 키로 사용한다.
# ─────────────────────────────────────────────────────────────────────
_PROVIDERS: list[dict[str, Any]] = [
    {"provider": "google_drive", "status": "coming_soon"},
    {"provider": "notion", "status": "coming_soon"},
]


def list_providers() -> list[dict[str, Any]]:
    """외부 저장소 provider 의 연결 상태 목록을 반환한다.

    Returns:
        provider 식별자와 status 를 담은 dict 의 리스트.
    """
    # 호출자가 반환된 리스트/딕셔너리를 변경해도 모듈 내부 상수(_PROVIDERS) 가
    # 오염되지 않도록 각 dict 를 얕게 복사해서 새 리스트를 만든다.
    # (현재는 1-depth dict 라 dict(item) 만으로 충분하지만, 추후 nested 구조가 생기면
    # copy.deepcopy 로 교체해야 함.)
    return [dict(item) for item in _PROVIDERS]
