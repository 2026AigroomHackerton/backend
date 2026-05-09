# [백엔드2 담당] 수정 허용 파일 - feature/backend-ocr-voice-storage 브랜치
# =============================================================================
# voice.py  (app/routers/voice.py)
# -----------------------------------------------------------------------------
# "음성/텍스트 명령" 관련 HTTP 엔드포인트를 정의하는 라우터 모듈.
#
# 책임 범위:
#   - 요청 바디/쿼리 파라미터를 Pydantic 스키마로 검증
#   - 서비스 계층(voice_service)을 호출해 비즈니스 로직 위임
#   - 결과를 프로젝트 공통 응답 포맷으로 감싸서 반환
#
# 책임이 아닌 것:
#   - DB 접근, 외부 API 호출 등 "어떻게 할지"의 구체 로직 → 서비스 계층 담당
# =============================================================================

# 미래 어노테이션. 타입 힌트 평가 지연으로 호환성과 가독성을 동시에 챙긴다.
from __future__ import annotations

# typing.Any   : 공통 응답의 data/error 필드처럼 "무엇이든 들어올 수 있는" 자리에 사용
# typing.Literal: input_type 같이 "정해진 문자열 집합"만 허용하고 싶을 때 사용
from typing import Any, Literal

# FastAPI 라우터 객체 + 파라미터 선언용 헬퍼.
# APIRouter는 main.py에서 include_router()로 메인 앱에 부착될 수 있도록 분리해 둔다.
# File/UploadFile : multipart 오디오 업로드(/transcribe) 처리에 사용.
# status / JSONResponse : 400 응답을 공통 envelope 으로 직접 반환하기 위함.
from fastapi import APIRouter, File, Query, UploadFile, status
from fastapi.responses import JSONResponse

# Pydantic의 BaseModel/Field는 요청·응답 스키마 정의와 검증·문서화를 담당한다.
from pydantic import BaseModel, Field

# 동일 패키지의 서비스 계층을 import. 라우터는 직접 로직을 짜지 않고
# VoiceService 인스턴스의 메서드를 호출만 한다 (얇은 라우터 + 두꺼운 서비스 패턴).
# ALLOWED_AUDIO_CONTENT_TYPES : transcribe 라우트의 콘텐츠 타입 화이트리스트.
from app.services.voice_service import ALLOWED_AUDIO_CONTENT_TYPES, VoiceService

# 모듈-수준 싱글톤 인스턴스. stateless Mock 서비스이므로 한 번 만들어 재사용.
# 변수명을 `voice_service` 로 두어 호출 사이트(`voice_service.create_voice_command(...)`)는
# 함수 기반 시절과 동일하게 유지되어 라우터 로직 수정 폭을 최소화한다.
voice_service = VoiceService()


# 이 라우터의 모든 엔드포인트는 `/api/voice` 접두사 아래에 모인다.
# tags=["voice"]는 자동 생성되는 OpenAPI 문서(/docs)에서 그룹 이름으로 표시된다.
router = APIRouter(prefix="/api/voice", tags=["Voice"])


# -----------------------------------------------------------------------------
# 요청 스키마
# -----------------------------------------------------------------------------
class VoiceCommandCreate(BaseModel):
    """
    POST /api/voice/commands 의 요청 바디 형태.

    Pydantic이 자동으로:
      - 누락 필드 → 422 응답
      - 타입 불일치 → 422 응답
      - input_type 이 "text"/"voice" 외 값 → 422 응답
    을 처리해 주므로 라우터 본문에서 별도 검증 코드를 쓰지 않아도 된다.
    """

    # `...` 는 "기본값 없음 = 필수"를 뜻하는 Pydantic 관용 표기.
    document_id: str = Field(..., description="대상 문서 ID")
    transcript: str = Field(..., description="텍스트/음성 인식 결과 텍스트")

    # Literal 로 허용 값을 화이트리스트화. OpenAPI 스키마에도 enum으로 노출된다.
    input_type: Literal["text", "voice"] = Field(..., description="입력 유형")


# -----------------------------------------------------------------------------
# 응답 스키마 (공통 포맷)
# -----------------------------------------------------------------------------
class ApiResponse(BaseModel):
    """
    프로젝트 전역 공통 응답 포맷.

    모든 엔드포인트가 다음 구조로 응답한다:
        {"success": bool, "data": ..., "message": str, "error": ...}

    여기서 BaseModel로 정의해 두면 FastAPI가 자동으로:
      - /docs 에 응답 형태 표시
      - 응답 직렬화 시 필드 누락 방지
    를 해 준다.
    """

    success: bool                    # 처리 성공 여부
    data: Any | None = None          # 정상 응답 시 페이로드 (실패 시 None)
    message: str = ""                # 사람이 읽을 수 있는 부가 메시지
    error: Any | None = None         # 실패 시 에러 정보 (정상 시 None)


def _ok(data: Any, message: str = "") -> dict:
    """
    공통 응답 포맷의 "성공" 케이스를 한 줄로 만들어 주는 헬퍼.

    엔드포인트가 늘어나도 같은 모양의 응답이 나오도록 강제해
    프론트엔드 파서가 분기를 안 해도 되게 한다.
    """
    return {"success": True, "data": data, "message": message, "error": None}


def _bad_request(message: str, error: str) -> JSONResponse:
    """
    공통 envelope 형식의 400 응답.

    HTTPException 은 기본적으로 {"detail": ...} 라 envelope 와 어긋나므로
    JSONResponse 로 직접 status_code + body 를 지정한다.
    """
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "success": False,
            "data": None,
            "message": message,
            "error": error,
        },
    )


# -----------------------------------------------------------------------------
# 엔드포인트: 명령 생성
# -----------------------------------------------------------------------------
@router.post("/commands", response_model=ApiResponse)
def create_command(payload: VoiceCommandCreate) -> dict:
    """
    POST /api/voice/commands

    클라이언트가 보낸 JSON 바디는 FastAPI가 자동으로 VoiceCommandCreate 인스턴스로
    역직렬화·검증한다. 검증 통과 후에만 이 함수가 실행된다.

    실제 저장 로직은 서비스 계층에 위임한다. (지금은 Mock)
    """
    # 서비스 호출 — 키워드 인자로 넘겨 가독성을 높이고, 인자 순서가 바뀌어도
    # 실수로 값이 뒤섞이지 않게 한다.
    result = voice_service.create_voice_command(
        document_id=payload.document_id,
        transcript=payload.transcript,
        input_type=payload.input_type,
    )
    # 공통 포맷으로 감싸서 반환.
    return _ok(result, message="voice command accepted")


# -----------------------------------------------------------------------------
# 엔드포인트: 명령 이력 조회
# -----------------------------------------------------------------------------
@router.get("/commands", response_model=ApiResponse)
def list_commands(
    # GET 요청은 보통 바디가 없으므로, 조회 키는 쿼리 파라미터로 받는다.
    # `Query(...)`의 `...`는 "필수"를 의미. 빠지면 FastAPI가 422를 돌려준다.
    document_id: str = Query(..., description="조회할 문서 ID"),
) -> dict:
    """
    GET /api/voice/commands?document_id=...

    특정 문서에 연결된 명령 이력을 반환한다. 지금은 더미 한 건이 항상 나오지만,
    서비스 계층이 실 DB 연동으로 바뀌면 이 라우터는 손대지 않아도 된다.
    """
    items = voice_service.list_voice_commands(document_id=document_id)

    # 응답 data 에는 어떤 문서를 조회했는지(document_id)도 함께 실어 준다.
    # 클라이언트가 다중 요청을 병렬 전송할 때 응답 매칭이 쉬워진다.
    return _ok({"document_id": document_id, "items": items})


# -----------------------------------------------------------------------------
# 엔드포인트: 음성 → 텍스트 (STT)
# -----------------------------------------------------------------------------
# POST /api/voice/transcribe
# - multipart/form-data 로 오디오 파일을 받아 텍스트로 변환.
# - real path: OpenAI Whisper API. mock fallback: 더미 transcript.
# - 콘텐츠 타입이 ALLOWED_AUDIO_CONTENT_TYPES 에 없으면 400 + envelope 에러.
@router.post("/transcribe", status_code=status.HTTP_200_OK)
async def transcribe_voice(
    audio: UploadFile = File(..., description="음성 파일 (mp3/m4a/wav/webm/ogg/flac)"),
) -> Any:
    """음성 파일을 텍스트로 변환한다 (STT).

    공통 응답 포맷: {success, data, message, error}.
    - data.transcript     : 변환된 텍스트
    - data.audio_filename : 업로드된 파일명
    - data._source        : "openai_whisper" | "mock" | "mock_fallback"
    """
    # 콘텐츠 타입 화이트리스트 검증.
    # UploadFile.content_type 은 클라이언트가 보낸 MIME (스푸핑 가능하지만 1차 방어선).
    if audio.content_type not in ALLOWED_AUDIO_CONTENT_TYPES:
        return _bad_request(
            message="지원하지 않는 오디오 형식입니다.",
            error=f"unsupported_content_type: {audio.content_type!r}",
        )

    data = await voice_service.transcribe_audio(audio_file=audio)
    return _ok(data, message="음성 인식이 완료되었습니다.")
