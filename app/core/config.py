"""환경 설정 로더.

[역할]
    - 프로젝트 루트의 .env 파일을 1회 로드하여 OS 환경변수에 주입.
    - 코드 다른 모듈은 os.getenv 대신 본 모듈의 상수를 import 해서 쓴다
      (어디서 어떤 키를 쓰는지 한 눈에 보이고, 기본값/검증을 한 곳에 모음).

[보안]
    - 비밀값(OPENAI_API_KEY 등) 은 절대 본 파일에 하드코딩하지 않는다.
    - .env 는 .gitignore 처리되어 있어 깃에 커밋되지 않는다.
    - 새 팀원은 .env.example 을 복사해서 사용한다.
"""

# 지연 평가 타입 힌트 (프로젝트 다른 모듈과 동일한 이유).
from __future__ import annotations

# os.getenv 로 환경변수를 읽기 위해 import.
import os

# python-dotenv : .env 파일을 읽어 os.environ 에 채워주는 라이브러리.
# load_dotenv() 는 멱등(idempotent) 이라 여러 번 호출되어도 안전하지만,
# 본 모듈이 import 될 때 1회만 실행되도록 모듈 최상단에 둔다.
from dotenv import load_dotenv

# .env 로드. find_dotenv 동작:
#   - 호출한 프로세스의 CWD 부터 부모 디렉토리로 거슬러 올라가며 .env 를 찾는다.
#   - 보통 프로젝트 루트(C:\backPython)에서 uvicorn 을 띄우므로 자동 탐색됨.
# override=False (기본): 이미 OS 에 설정된 환경변수가 우선. 운영 환경에서
#   실제 OS 환경변수가 .env 보다 우선되도록 하기 위함.
load_dotenv()


# ─────────────────────────────────────────────────────────────────────
# OpenAI 설정
# ─────────────────────────────────────────────────────────────────────
#
# OPENAI_API_KEY 가 비어 있으면 None 으로 둔다.
# 호출 측(ai_service / ocr_service) 에서 None 이면 mock 으로 폴백한다.
# (해커톤 데모 환경이 아닌 CI/테스트 환경에서도 코드가 import 자체는 되도록.)
OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY") or None

# 텍스트 생성 모델. 기본값은 비용/품질 균형이 좋은 gpt-4o-mini.
OPENAI_MODEL_TEXT: str = os.getenv("OPENAI_MODEL_TEXT", "gpt-4o-mini")

# Vision(OCR) 모델. gpt-4o-mini 도 이미지 입력을 지원.
OPENAI_MODEL_VISION: str = os.getenv("OPENAI_MODEL_VISION", "gpt-4o-mini")

# Comma-separated frontend origins allowed to call this API from browsers.
# Example: https://frontend-pnyn.onrender.com,http://localhost:5173
CORS_ORIGINS: list[str] = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "https://frontend-pnyn.onrender.com",
    ).split(",")
    if origin.strip()
]


def has_openai_key() -> bool:
    """OpenAI 호출이 실제 가능한지 여부.

    서비스 계층에서 "키가 있으면 real, 없으면 mock" 분기를 깔끔히 쓰기 위해
    별도 헬퍼로 노출. 단순 truthy 검사이지만 추후 키 형식 검증
    (sk- 로 시작하는지, length 등) 을 추가할 자리이기도 하다.
    """
    return bool(OPENAI_API_KEY)
