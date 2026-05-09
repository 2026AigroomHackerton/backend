"""AI 관련 HTTP 라우터.

[역할]
    - 외부 클라이언트(프런트엔드/모바일)로부터 들어오는 HTTP 요청을 받아
      적절한 서비스 함수를 호출하고, 그 결과를 JSON으로 직렬화해 응답한다.
    - 비즈니스 로직은 services/ai_service.py에 두고, 본 파일은 "입력 검증 +
      서비스 호출 + 응답 포맷팅"만 담당한다 (얇은 라우터 원칙).

[엔드포인트]
    - POST /api/ai/command-edit : 음성/텍스트 명령 → AI 수정 계획 반환
        OPENAI_API_KEY 가 설정되어 있으면 실제 OpenAI 호출, 없거나 실패 시 mock 폴백

[담당]
    - 백엔드 2 (AI 문서 수정 기능 Mock 뼈대)
"""

# 지연 평가된 타입 힌트. ai_service.py와 동일한 이유로 사용.
from __future__ import annotations

# Any: 응답 data 필드의 타입을 자유롭게 두기 위함. 실제 OpenAI 응답 구조가
# 모델/프롬프트에 따라 변동될 수 있어 엄격한 모델로 못 박지 않는다.
from typing import Any

# APIRouter: FastAPI에서 라우트를 모듈별로 분리할 때 쓰는 미니 라우터.
# Depends: DB 세션 등 의존성을 주입받기 위한 헬퍼.
# main.py에서 include_router(...)로 본 라우터를 앱에 합친다.
from fastapi import APIRouter, Depends

# BaseModel: Pydantic의 데이터 검증 클래스. 요청/응답 스키마를 선언하면
# FastAPI가 자동으로 JSON 파싱/검증/OpenAPI 문서 생성을 처리한다.
# Field: 각 필드에 description, 기본값, 제약(min_length 등)을 부여.
from pydantic import BaseModel, Field

# 서비스 계층의 함수. 라우터는 직접 응답 데이터를 만들지 않고, 항상 서비스
# 함수를 거쳐 데이터를 받아온다 (관심사 분리).
# generate_edit_plan: 공개 진입점.
#   - OPENAI_API_KEY 가 있으면 실제 OpenAI 호출 (db 주입 시 본문도 함께 prompt 에 포함)
#   - 키가 없거나 호출 실패 시 자동으로 generate_mock_edit_plan 으로 폴백
from app.services.ai_service import generate_edit_plan

# DB 의존성 — storage.py / ocr.py 와 동일한 lazy import 폴백.
try:
    from app.database import get_db  # type: ignore
except ImportError:  # pragma: no cover
    def get_db():  # noqa: D401 — 더미 generator
        """더미 get_db. None 을 yield 해 라우터가 db=None 으로 호출되게 한다."""
        yield None


# 라우터 인스턴스.
# - prefix="/api/ai": 본 라우터의 모든 엔드포인트 경로 앞에 자동 부착.
#   따라서 아래 @router.post("/command-edit")의 최종 경로는
#   POST /api/ai/command-edit 가 된다.
# - tags=["ai"]: /docs (Swagger UI)에서 그룹핑되는 라벨.
router = APIRouter(prefix="/api/ai", tags=["ai"])


class CommandEditRequest(BaseModel):
    """POST /api/ai/command-edit 요청 바디 스키마.

    Pydantic이 본 클래스를 보고:
      1) 들어온 JSON을 dict가 아닌 본 클래스 인스턴스로 자동 변환
      2) 필드가 없거나 타입이 다르면 422 Unprocessable Entity 반환
      3) /docs에 요청 예시(JSON Schema)를 자동 생성
    """

    # ...(Ellipsis)는 "필수 필드"를 의미. 빠지면 422로 거절된다.
    document_id: str = Field(..., description="대상 문서 ID")
    command_text: str = Field(..., description="음성/텍스트로 입력된 사용자 명령")
    scope: str = Field(..., description="수정 범위 (예: section, paragraph, document)")


class CommandEditResponse(BaseModel):
    """POST /api/ai/command-edit 응답 바디 스키마.

    프로젝트 공통 응답 포맷({success, data})을 따른다.
    - success: 처리 성공 여부 (Mock에서는 항상 True)
    - data   : 실제 페이로드. EditOperation 구조의 dict가 들어온다.
    """

    success: bool
    data: dict[str, Any]


# @router.post: HTTP POST 메서드 등록 데코레이터.
# - 첫 인자 "/command-edit": prefix와 합쳐져 /api/ai/command-edit가 됨.
# - response_model=CommandEditResponse:
#     1) 반환값이 본 모델로 직렬화됨 (선언되지 않은 필드는 잘려나감 → 보안)
#     2) /docs에 응답 스키마가 자동 생성됨
# - summary / description:
#     /docs (Swagger UI) 에 노출되는 설명. 함수 docstring 보다 우선.
@router.post(
    "/command-edit",
    response_model=CommandEditResponse,
    summary="AI 명령 기반 문서 수정 계획 생성",
    description=(
        "사용자의 음성/텍스트 명령을 받아 AI 가 생성한 수정 계획(EditOperation) 을 "
        "반환합니다. OPENAI_API_KEY 가 설정되어 있으면 실제 OpenAI 호출을 수행하고, "
        "키가 없거나 호출이 실패하면 동일한 응답 스키마의 mock 결과로 자동 폴백합니다."
    ),
)
def command_edit(
    req: CommandEditRequest,
    db=Depends(get_db),
) -> CommandEditResponse:
    """사용자 명령을 받아 AI 가 만든 수정 계획을 돌려준다.

    [흐름]
      1) FastAPI 가 요청 바디 JSON 을 자동으로 CommandEditRequest 로 변환.
      2) Depends(get_db) 로 SQLAlchemy 세션 주입.
      3) 서비스 계층(generate_edit_plan) 에 위임:
         - 서비스가 db 와 document_id 로 document_texts 본문 조회.
         - 본문이 prompt 에 포함되어 OpenAI 가 실제 텍스트를 보고 응답.
         - 키 부재 / 호출 실패 / 본문 부재 시 mock 폴백.
      4) 공통 응답 포맷({success, data}) 으로 감싸서 반환.

    [예외 처리]
      - generate_edit_plan 내부에서 모든 외부 호출 예외를 잡아 폴백 처리.
      - 라우터 단에서 별도 try/except 두지 않는다.
    """
    # 서비스 함수 호출. db 까지 전달하여 본문 조회까지 수행하게 한다.
    # 키 / 세션 / 본문 어느 하나라도 누락되면 서비스가 graceful 폴백.
    plan = generate_edit_plan(
        document_id=req.document_id,
        command_text=req.command_text,
        scope=req.scope,
        db=db,
    )

    # 서비스가 돌려준 dict를 공통 응답 포맷으로 래핑하여 반환.
    # response_model에 의해 FastAPI가 자동으로 JSON 직렬화한다.
    return CommandEditResponse(success=True, data=plan)
