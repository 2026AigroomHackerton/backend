"""OpenAI 클라이언트 싱글톤.

[역할]
    - openai.OpenAI 인스턴스를 프로세스 전역에서 1개만 생성/재사용.
    - 키가 없을 때(=mock 모드) 는 None 을 돌려주어 호출자가 폴백 처리하도록.

[왜 분리했는가]
    - 클라이언트 생성 로직(api_key 주입, timeout, base_url 등)을 한 곳에 모으면
      추후 retry/proxy/Azure OpenAI 등으로 교체할 때 한 파일만 바꾸면 된다.
    - 서비스 모듈마다 OpenAI(...) 를 직접 호출하면 키 검증/연결풀이 분산된다.
"""

from __future__ import annotations

# typing.Optional : "None 이거나 OpenAI 인스턴스" 를 표현하기 위함.
#                   3.10+ 에서는 `OpenAI | None` 으로도 쓸 수 있지만 호환성을 위해 Optional 사용.
from typing import Optional

# openai SDK v1.x 의 동기 클라이언트. 비동기는 AsyncOpenAI 가 별도로 있다.
# 본 프로젝트의 라우터는 일부 async 지만, OpenAI 호출은 짧고 단순하므로 동기로 두고
# FastAPI 가 worker thread 에서 처리하도록 한다 (def 엔드포인트 / asyncio.to_thread).
from openai import OpenAI

# config 에서 키와 모델명을 읽어온다. (config 가 .env 를 이미 로드함)
from app.core import config


# ─────────────────────────────────────────────────────────────────────
# 모듈 전역 캐시.
# - 첫 호출 시에만 실제 OpenAI(...) 가 생성되고, 이후엔 동일 인스턴스를 재사용.
# - underscore(_) prefix 는 "외부에서 직접 건들지 말라" 는 관용.
# ─────────────────────────────────────────────────────────────────────
_client: Optional[OpenAI] = None


def get_client() -> Optional[OpenAI]:
    """OpenAI 클라이언트를 반환. 키가 없으면 None.

    Returns:
        OpenAI 인스턴스 또는 None.
        호출자는 None 이면 mock 폴백 또는 503 응답을 선택해야 한다.
    """
    global _client

    # 키가 없으면 클라이언트 생성 자체를 시도하지 않는다.
    # (openai SDK 는 키 없이도 인스턴스를 만들 수는 있지만, 호출 시 401 이 떠서
    #  에러 메시지가 모호해진다. 사전 차단으로 진단을 명확히 한다.)
    if not config.has_openai_key():
        return None

    # 최초 1회만 생성. (간단한 lazy singleton)
    if _client is None:
        _client = OpenAI(api_key=config.OPENAI_API_KEY)

    return _client
