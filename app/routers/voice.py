# [백엔드2 담당] 수정 허용 파일 - feature/backend-ocr-voice-storage 브랜치
# =============================================================================
# voice.py  (app/routers/voice.py)
# -----------------------------------------------------------------------------
# "음성/텍스트 명령" 관련 HTTP 엔드포인트를 정의하는 라우터 모듈.
#
# [엔드포인트]
#   - POST /api/voice/transcribe : 음성 파일 → 텍스트(STT) + DB 저장
#   - POST /api/voice/commands   : 텍스트/오디오 명령 직접 저장
#   - GET  /api/voice/commands   : 특정 문서의 명령 이력 조회
#
# [책임 범위]
#   - 입력(form/body/query) 검증 위임 (Pydantic / VoiceService.validate_audio)
#   - 비즈니스 로직(STT, 저장, 조회) 은 VoiceService 에 위임
#   - 결과를 프로젝트 공통 응답 envelope({success, data, message, error}) 로 감싼다
#
# [에러 코드 — 명세 [에러 처리] 그대로]
#   - INVALID_FILE_TYPE      : 400. 오디오 화이트리스트 외 콘텐츠 타입.
#   - STT_API_KEY_MISSING    : 500. OPENAI_API_KEY 미설정.
#   - STT_FAILED             : 500. Whisper 호출 자체가 실패.
# =============================================================================

# 타입 힌트 지연 평가. 팀 표준.
from __future__ import annotations

# typing
#   - Any     : 공통 envelope 의 data/error 자리에 어떤 타입이든 허용.
#   - Literal : input_type 같이 "정해진 값만 허용" 강제용.
from typing import Any, Literal

# FastAPI
#   - APIRouter   : main.py 에서 include_router 로 부착.
#   - File/Form/UploadFile : multipart/form-data (오디오 + document_id) 처리.
#   - Query       : GET 쿼리 파라미터 선언.
#   - status      : HTTP 상태 코드 상수 모음.
from fastapi import APIRouter, File, Form, Query, UploadFile, status
# JSONResponse: 4xx/5xx 응답을 envelope 형태로 직접 반환할 때 사용.
# (HTTPException 의 기본 detail 포맷과 envelope 가 충돌하므로 직접 구성)
from fastapi.responses import JSONResponse

# Pydantic: /commands POST 의 JSON 바디 스키마 정의 / 검증 / 문서화.
from pydantic import BaseModel, Field

# VoiceService — 오디오 검증/저장/STT/저장/조회 비즈니스 로직.
from app.services.voice_service import VoiceService

# 모듈-수준 싱글톤 인스턴스.
# - VoiceService 는 stateless (메모리 리스트는 클래스 속성이라 인스턴스 무관).
# - 매 요청마다 새로 만들 필요가 없어 1회 생성 후 재사용.
voice_service = VoiceService()

# 라우터. prefix 와 tag 는 다른 도메인 라우터들과 동일한 컨벤션(/api/<도메인>).
router = APIRouter(prefix="/api/voice", tags=["Voice"])


# =============================================================================
# 공통 응답 헬퍼
# =============================================================================
class ApiResponse(BaseModel):
    """공통 응답 envelope 의 Pydantic 표현.

    실제 응답은 dict 로 직접 만들지만, OpenAPI 문서(/docs)에 노출하기 위해
    response_model 자리에 둔다.
    """

    success: bool                 # 처리 성공 여부
    data: Any | None = None       # 정상 시 페이로드 (실패 시 None)
    message: str = ""             # 사람이 읽을 메시지
    error: Any | None = None      # 실패 시 에러 코드/문자열 (정상 시 None)


def _ok(data: Any, message: str = "") -> dict[str, Any]:
    """envelope 의 성공 케이스를 만드는 헬퍼."""
    return {"success": True, "data": data, "message": message, "error": None}


def _error_response(
    *,
    status_code: int,
    error_code: str,
    message: str,
) -> JSONResponse:
    """envelope 의 실패 케이스를 status_code 와 함께 반환하는 헬퍼.

    명세 에러 코드(INVALID_FILE_TYPE / STT_API_KEY_MISSING / STT_FAILED)
    를 envelope 의 `error` 필드에 그대로 실어 내려보낸다.
    """
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "data": None,
            "message": message,
            "error": error_code,
        },
    )


# =============================================================================
# 요청 스키마
# =============================================================================
class VoiceCommandCreate(BaseModel):
    """POST /api/voice/commands 의 요청 바디.

    명세 [라우터 ② 요청] 그대로:
        {document_id, transcript, input_type: "text"|"audio" (default "text")}
    """

    # `...` = "필수 필드".
    document_id: str = Field(..., description="대상 문서 ID")
    transcript: str = Field(..., description="저장할 명령 텍스트")
    # default 가 있으므로 클라이언트가 input_type 을 생략하면 "text" 로 처리.
    # Literal 로 enum 강제 → /docs 에 자동 노출 + 422 자동 검증.
    input_type: Literal["text", "audio"] = Field(
        "text",
        description="입력 유형 (default: text)",
    )


# =============================================================================
# ① POST /api/voice/transcribe — 음성 → 텍스트 (+ DB 저장)
# =============================================================================
@router.post(
    "/transcribe",
    status_code=status.HTTP_200_OK,
    response_model=ApiResponse,
    summary="음성 파일 STT 변환",
    description=(
        "오디오 파일을 OpenAI Whisper API로 실제 음성 인식합니다. "
        "환경변수 OPENAI_API_KEY 필요. 한국어 인식(language=ko)."
    ),
)
async def transcribe_voice(
    # multipart/form-data 의 file 파트.
    audio: UploadFile = File(..., description="음성 파일 (mp3/m4a/wav/webm/ogg/flac)"),
    # multipart/form-data 의 일반 텍스트 필드. 명세에서 필수.
    # 누락 시 FastAPI 가 자동으로 422 응답.
    document_id: str = Form(..., description="명령이 적용될 대상 문서 ID"),
) -> Any:
    """음성 파일을 텍스트로 변환하고 voice_commands 에 저장한다.

    [처리 순서 — 명세 그대로]
        1) validate_audio(audio)              → 실패 시 400 INVALID_FILE_TYPE
        2) audio_path = save_audio(audio)     → 디스크 저장 (실제 파일)
        3) stt_result = transcribe_audio(audio_path) → Whisper 호출
        4) stt_result["error"] 분기:
            - "openai_key_missing" → 500 STT_API_KEY_MISSING
            - 그 외 메시지         → 500 STT_FAILED
        5) save_command_to_db(...)            → 메모리 리스트에 저장
        6) 명세 응답 스키마로 정렬해 반환
    """

    # ── 1) 콘텐츠 타입 검증 ───────────────────────────────────────────────
    # VoiceService.validate_audio 가 ValueError 를 던지면 INVALID_FILE_TYPE.
    try:
        voice_service.validate_audio(audio)
    except ValueError:
        return _error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="INVALID_FILE_TYPE",
            message="지원하지 않는 오디오 형식입니다.",
        )

    # ── 2) 디스크 저장 ────────────────────────────────────────────────────
    # 실패 시 (디스크 권한/공간 등) 500 으로 떨어뜨려 STT_FAILED 와 분리하고 싶지만
    # 명세 에러 코드 표에 별도 항목이 없으므로 STT_FAILED 로 합쳐 응답한다.
    try:
        audio_path = await voice_service.save_audio(audio)
    except Exception as exc:  # noqa: BLE001 — 디스크 쪽 모든 예외를 일괄 처리
        return _error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="STT_FAILED",
            message=f"오디오 파일 저장에 실패했습니다: {exc}",
        )

    # ── 3) Whisper STT ────────────────────────────────────────────────────
    # transcribe_audio 자체는 graceful 하게 dict 를 돌려준다. (예외 안 던짐)
    stt_result = await voice_service.transcribe_audio(audio_path)

    # ── 4) STT 실패 분기 ──────────────────────────────────────────────────
    # 명세는 "변환 실패 시 (error 키 있음)" → HTTP 500.
    if "error" in stt_result:
        if stt_result["error"] == "openai_key_missing":
            return _error_response(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="STT_API_KEY_MISSING",
                message="OpenAI API 키가 설정되지 않았습니다.",
            )
        return _error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="STT_FAILED",
            message=f"음성 변환에 실패했습니다: {stt_result['error']}",
        )

    # ── 5) DB(메모리) 저장 ────────────────────────────────────────────────
    # input_type 은 명세상 "audio" 고정.
    command = await voice_service.save_command_to_db(
        document_id=document_id,
        transcript=stt_result["transcript"],
        input_type="audio",
        audio_path=audio_path,
    )

    # ── 6) 명세 응답 스키마로 정렬 ────────────────────────────────────────
    # status 는 "received" → "transcribed" 로 덮어쓴다 (명세 [라우터 ① 응답]).
    response_data = {
        "voice_command_id": command["voice_command_id"],
        "transcript": command["transcript"],
        "input_type": command["input_type"],
        "document_id": command["document_id"],
        "audio_path": command["audio_path"],
        "status": "transcribed",
    }
    return _ok(response_data, message="음성 인식이 완료되었습니다.")


# =============================================================================
# ② POST /api/voice/commands — 텍스트/오디오 명령 직접 저장
# =============================================================================
@router.post(
    "/commands",
    response_model=ApiResponse,
    summary="텍스트 명령 저장 (Web Speech API fallback)",
    description=(
        "브라우저 Web Speech API로 이미 변환된 텍스트 명령을 서버에 저장합니다. "
        "Whisper를 거치지 않고 직접 텍스트로 입력하는 fallback 엔드포인트."
    ),
)
async def create_command(payload: VoiceCommandCreate) -> dict[str, Any]:
    """음성/텍스트 명령을 voice_commands 에 직접 저장한다.

    Whisper 호출 없이 클라이언트가 보낸 transcript 를 그대로 저장하는 경로.
    (예: 텍스트 입력 모드, 또는 클라이언트가 별도 STT 를 거친 후의 결과 저장)
    """
    # save_command_to_db 가 명세 응답 키를 모두 채워 반환하므로 그대로 사용.
    record = await voice_service.save_command_to_db(
        document_id=payload.document_id,
        transcript=payload.transcript,
        input_type=payload.input_type,
        # 텍스트 입력 경로 → audio_path 없음.
        audio_path=None,
    )

    # 명세 [라우터 ② 응답] 스키마로 키만 골라 정렬.
    response_data = {
        "voice_command_id": record["voice_command_id"],
        "document_id": record["document_id"],
        "transcript": record["transcript"],
        "input_type": record["input_type"],
        "status": record["status"],          # "received"
        "created_at": record["created_at"],
    }
    return _ok(response_data, message="voice command accepted")


# =============================================================================
# ③ GET /api/voice/commands — 특정 문서의 명령 이력 조회
# =============================================================================
@router.get(
    "/commands",
    response_model=ApiResponse,
    summary="문서별 명령 이력 조회",
    description="document_id 기준으로 해당 문서의 전체 명령 이력을 조회합니다.",
)
async def list_commands(
    document_id: str = Query(..., description="조회할 문서 ID"),
) -> dict[str, Any]:
    """document_id 에 연결된 명령 이력을 반환한다.

    DB 미연동 환경에서는 VoiceService 의 메모리 리스트에서 필터링한 결과를 돌려준다.
    DB 연동이 붙으면 라우터는 그대로 두고 서비스 메서드 본문만 교체된다.
    """
    records = await voice_service.get_commands_from_db(document_id)

    # 명세 [라우터 ③ 응답] 스키마: commands 항목당 5개 키만 노출.
    # 내부 record 는 audio_path/document_id 도 갖지만, 명세 외 필드는 잘라낸다.
    commands = [
        {
            "voice_command_id": r["voice_command_id"],
            "transcript": r["transcript"],
            "input_type": r["input_type"],
            "status": r["status"],
            "created_at": r["created_at"],
        }
        for r in records
    ]
    return _ok({"document_id": document_id, "commands": commands})
