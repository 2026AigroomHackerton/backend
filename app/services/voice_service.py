# [백엔드2 담당] 수정 허용 파일 - feature/backend-ocr-voice-storage 브랜치
# =============================================================================
# voice_service.py — 음성/텍스트 명령 도메인 비즈니스 로직 계층.
#
# [책임]
#   - 음성 파일을 텍스트로 변환 (STT, OpenAI Whisper)
#   - 음성/텍스트 명령 저장 / 이력 조회 (Mock — DB 미연동)
#
# [real path]
#   - transcribe_audio(): OPENAI_API_KEY 가 있으면 Whisper(`whisper-1`) 호출.
#
# [mock path — fallback]
#   - 키가 없거나 Whisper 호출이 실패하면 명세 더미 transcript 로 폴백.
#   - create_voice_command / list_voice_commands 는 DB 미연동이라 Mock 유지.
#     실제 구현 시 메서드 본문만 교체하면 라우터는 영향 없음.
# =============================================================================

from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

from fastapi import UploadFile

# OpenAI 클라이언트 싱글톤. 키 없으면 None.
from app.core.openai_client import get_client

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# 더미 transcript / 콘텐츠 타입 화이트리스트 / Whisper 모델명
# -----------------------------------------------------------------------------
# Mock 폴백에서 반환할 더미 transcript. 사양서 list_voice_commands 예시와 동일.
DUMMY_TRANSCRIPT = "환경정화 활동으로 바꿔줘"

# Whisper 가 받을 수 있는 일반적인 오디오 MIME 타입.
# - mp3, m4a, mp4, wav, webm, ogg, flac
# - 라우터에서 화이트리스트 검증.
ALLOWED_AUDIO_CONTENT_TYPES: set[str] = {
    "audio/mpeg",       # mp3
    "audio/mp3",        # 비표준이지만 일부 클라이언트가 보냄
    "audio/mp4",        # mp4 컨테이너
    "audio/m4a",        # 비표준 별칭
    "audio/x-m4a",
    "audio/wav",
    "audio/x-wav",
    "audio/webm",
    "audio/ogg",
    "audio/flac",
}

# 사용할 Whisper 모델. 현재는 단일 모델만 노출.
OPENAI_MODEL_STT = "whisper-1"


# =============================================================================
# VoiceService
# =============================================================================
class VoiceService:
    """음성/텍스트 명령 비즈니스 로직 클래스.

    제공 메서드:
        - transcribe_audio(audio_file)             : 음성 → 텍스트 (real or mock).
        - create_voice_command(...)                : 새 명령 저장 (Mock — DB TODO).
        - list_voice_commands(document_id)         : 명령 이력 조회 (Mock — DB TODO).
    """

    # =========================================================================
    # Public: transcribe_audio — Whisper 우선, 실패 시 mock 폴백
    # =========================================================================
    async def transcribe_audio(self, audio_file: UploadFile) -> dict[str, Any]:
        """음성 파일을 텍스트로 변환한다.

        키가 있으면 Whisper STT, 없거나 실패 시 더미 transcript 폴백.
        응답 스키마는 두 경로 동일 ({transcript, audio_filename, _source, ...}).
        """
        audio_bytes = await audio_file.read()
        # OpenAI SDK 가 파일 확장자로 포맷을 추정하므로 filename 보존이 중요.
        filename = audio_file.filename or "audio.mp3"

        client = get_client()
        if client is not None:
            try:
                text = await asyncio.to_thread(
                    self._call_openai_whisper, audio_bytes, filename
                )
                return {
                    "transcript": text or DUMMY_TRANSCRIPT,
                    "audio_filename": filename,
                    "_source": "openai_whisper",
                    "_model": OPENAI_MODEL_STT,
                }
            except Exception as exc:  # noqa: BLE001 — 폴백 대상
                logger.warning("Whisper STT 실패 → mock 폴백: %s", exc)
                return {
                    "transcript": DUMMY_TRANSCRIPT,
                    "audio_filename": filename,
                    "_source": "mock_fallback",
                    "_error": str(exc),
                }

        # 키 없음 → 순수 mock.
        return {
            "transcript": DUMMY_TRANSCRIPT,
            "audio_filename": filename,
            "_source": "mock",
        }

    # =========================================================================
    # Public: create_voice_command / list_voice_commands (DB TODO — Mock 유지)
    # =========================================================================
    def create_voice_command(
        self,
        document_id: str,
        transcript: str,
        input_type: str,
    ) -> dict[str, Any]:
        """새 음성/텍스트 명령을 "저장한 척" 하고 결과를 반환 (Mock).

        TODO(DB 연동):
            1) DB 세션을 받아 VoiceCommand 레코드 INSERT
            2) 생성된 PK(voice_command_id) 반환
            현재 단계에서는 DB 모델 절대 수정 금지 영역이라 Mock.
        """
        return {
            "voice_command_id": "vc_001",
            "document_id": document_id,
            "transcript": transcript,
            "input_type": input_type,
            "status": "accepted",
        }

    def list_voice_commands(self, document_id: str) -> list[dict[str, Any]]:
        """특정 문서의 명령 이력을 조회 (Mock).

        TODO(DB 연동):
            SELECT * FROM voice_commands WHERE document_id=:document_id
            ORDER BY created_at DESC
        """
        return [
            {
                "command_id": "vc_001",
                "transcript": DUMMY_TRANSCRIPT,
            }
        ]

    # =========================================================================
    # Internal: Whisper 동기 호출
    # =========================================================================
    def _call_openai_whisper(self, audio_bytes: bytes, filename: str) -> str:
        """OpenAI Whisper 로 audio bytes → text.

        [동기 메서드]
            transcribe_audio() 가 asyncio.to_thread 로 감싸 호출.

        [파일 객체 요건]
            OpenAI SDK 는 file-like 객체를 받으며, 확장자로 포맷을 추정한다.
            BytesIO 에 .name 을 직접 부여해 SDK 가 ".mp3"/".wav" 등을 알 수 있게 한다.
        """
        client = get_client()
        if client is None:
            raise RuntimeError("OpenAI client is not configured")

        bio = io.BytesIO(audio_bytes)
        # SDK 의 multipart 업로더가 .name 을 읽어 확장자 기반으로 포맷 결정.
        bio.name = filename

        result = client.audio.transcriptions.create(
            model=OPENAI_MODEL_STT,
            file=bio,
        )
        # SDK 응답은 .text 속성을 가진 객체.
        return getattr(result, "text", "") or ""
