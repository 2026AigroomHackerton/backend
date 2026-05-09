# [백엔드2 담당] 수정 허용 파일 - feature/backend-ocr-voice-storage 브랜치
# =============================================================================
# voice_service.py — 음성/텍스트 명령 도메인 비즈니스 로직 계층.
#
# [책임]
#   - 오디오 파일 검증 (validate_audio)
#   - 오디오 파일 디스크 저장 (save_audio)
#   - OpenAI Whisper STT 호출 (transcribe_audio) — graceful fallback
#   - voice_commands 저장 / 조회 (DB 미연동 — 메모리 dict 폴백)
#
# [real path]
#   - OPENAI_API_KEY 가 .env 에 있으면 openai.AsyncOpenAI 로 Whisper 호출.
#
# [graceful fallback — 명세 [전제 조건] ]
#   - 키 없음     : {"transcript": "", "error": "openai_key_missing", "language": "ko"}
#   - API 호출 실패: {"transcript": "", "error": "<메시지>",       "language": "ko"}
#   - 둘 다 서버를 절대 죽이지 않는다 (라우터에서 500 응답으로 변환).
#
# [DB 미연동]
#   - DB 모델 절대 수정 금지 영역이라, save_command_to_db / get_commands_from_db
#     는 클래스 속성 메모리 리스트(_IN_MEMORY_COMMANDS) 에 보관한다.
#   - 추후 실제 DB 연동 시 본 메서드들 본문만 교체하면 라우터는 영향 없음.
# =============================================================================

# 타입 힌트의 지연 평가. 팀 표준.
from __future__ import annotations

# 표준 라이브러리.
import logging          # Whisper 실패 등 비치명적 오류 로깅
import os               # 환경변수 / 디렉토리 생성 / 경로 결합
import uuid             # voice_command_id 생성용 (uuid4)
from datetime import datetime, timezone  # 파일명 timestamp / created_at ISO
from pathlib import Path                  # filename sanitize (디렉토리 트래버설 차단)
from typing import Any

# FastAPI: UploadFile 타입만 import (라우터가 넘겨주는 객체).
from fastapi import UploadFile

# .env 로드를 위한 부수 효과 import.
# config 모듈이 import 시점에 dotenv.load_dotenv() 를 호출하므로,
# 본 모듈이 처음 import 되는 순간 OPENAI_API_KEY 가 os.environ 에 들어와 있다.
# (config 자체의 상수는 직접 안 쓰므로 noqa: F401 로 lint 경고 억제.)
from app.core import config  # noqa: F401  — env 로딩 보장 목적

# 모듈 전용 로거.
logger = logging.getLogger(__name__)


# =============================================================================
# VoiceService
# =============================================================================
class VoiceService:
    """음성/텍스트 명령 비즈니스 로직 클래스.

    제공 메서드:
        - validate_audio(file)                              : 오디오 MIME 검증.
        - save_audio(file)            -> str                : 디스크 저장 후 경로 반환.
        - transcribe_audio(audio_path)-> dict               : Whisper STT (graceful).
        - generate_command_id()       -> str                : "vc_" + uuid4[:8].
        - save_command_to_db(...)     -> dict               : 저장 (메모리 폴백).
        - get_commands_from_db(doc_id)-> list[dict]         : 조회 (메모리 폴백).
    """

    # =========================================================================
    # 클래스 속성 — 명세에 명시된 화이트리스트와 디렉토리 상수
    # =========================================================================
    # 허용 오디오 콘텐츠 타입 (명세 [전제 조건] 그대로 7종).
    # 라우터에서 INVALID_FILE_TYPE 검증에 사용.
    # list 로 두는 이유: 명세가 list 로 못 박았고, 외부에서 문서화/직렬화할 때
    # 안정적인 출력 순서를 갖게 한다.
    ALLOWED_AUDIO_TYPES: list[str] = [
        "audio/mpeg",
        "audio/mp4",
        "audio/wav",
        "audio/webm",
        "audio/ogg",
        "audio/flac",
        "audio/x-m4a",
    ]

    # 오디오 저장 루트. 프로세스 CWD(보통 프로젝트 루트) 기준 상대 경로.
    # save_audio() 가 첫 호출 시 os.makedirs(exist_ok=True) 로 자동 생성.
    VOICE_DIR: str = "uploads/voice"

    # =========================================================================
    # 메모리 fallback 저장소.
    # - DB 미연동 환경에서 commands 이력을 임시 보관.
    # - 클래스 속성으로 두어 모든 인스턴스가 동일 리스트를 공유.
    #   (라우터에서 모듈 싱글톤 인스턴스를 쓰므로 사실상 1개의 리스트)
    # =========================================================================
    _IN_MEMORY_COMMANDS: list[dict[str, Any]] = []

    # =========================================================================
    # validate_audio
    # =========================================================================
    def validate_audio(self, file: UploadFile) -> None:
        """오디오 콘텐츠 타입 화이트리스트 검증.

        명세에 따라 검증 실패 시 ValueError 를 발생시키고,
        라우터(voice.py) 가 이를 잡아 INVALID_FILE_TYPE 400 으로 변환한다.

        Raises:
            ValueError: content_type 이 ALLOWED_AUDIO_TYPES 에 없을 때.
        """
        if file.content_type not in self.ALLOWED_AUDIO_TYPES:
            raise ValueError(
                f"unsupported audio content_type: {file.content_type!r}"
            )

    # =========================================================================
    # save_audio
    # =========================================================================
    async def save_audio(self, file: UploadFile) -> str:
        """업로드된 오디오 파일을 디스크에 저장하고 경로를 돌려준다.

        저장 위치: VOICE_DIR/<timestamp>_<원본파일명>
            - timestamp 는 마이크로초까지 포함하여 동시 업로드의 충돌을 줄인다.
            - 원본 파일명은 Path(name).name 으로 디렉토리 부분을 제거 (트래버설 방어).

        Returns:
            저장된 파일의 상대 경로 문자열.
        """
        # 디렉토리 자동 생성. 이미 있으면 무시.
        os.makedirs(self.VOICE_DIR, exist_ok=True)

        # 원본 파일명 안전화.
        # - filename 이 None 이면 기본 "audio.mp3" 사용.
        # - 슬래시/백슬래시가 들어와도 Path(...).name 으로 마지막 컴포넌트만 추출.
        original_name = file.filename or "audio.mp3"
        safe_name = Path(original_name).name or "audio.mp3"

        # 파일명 충돌 회피용 timestamp prefix.
        # 마이크로초까지 포함해 같은 초에 여러 요청이 들어와도 거의 충돌 없음.
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        save_name = f"{timestamp}_{safe_name}"

        # os.path.join 은 OS 별 separator 를 자동 처리.
        save_path = os.path.join(self.VOICE_DIR, save_name)

        # 비동기 read() 로 전체 바이트를 읽어 디스크에 기록.
        # 큰 파일의 경우 chunk 단위 스트리밍이 이상적이나, 해커톤 MVP 단계에서는
        # 단순한 일괄 저장으로 충분.
        content = await file.read()
        with open(save_path, "wb") as f:
            f.write(content)
        return save_path

    # =========================================================================
    # transcribe_audio
    # =========================================================================
    async def transcribe_audio(self, audio_path: str) -> dict[str, Any]:
        """OpenAI Whisper API 로 STT 수행.

        명세 응답 스키마:
            성공     : {"transcript": "...", "language": "ko"}
            키 없음  : {"transcript": "", "error": "openai_key_missing", "language": "ko"}
            API 실패 : {"transcript": "", "error": "<예외 메시지>",     "language": "ko"}

        Returns:
            위 스키마를 따르는 dict. 라우터가 "error" 키 유무로 분기한다.
        """
        # 1) 키 부재 graceful fallback.
        #    config 모듈이 .env 를 이미 로드했으므로 os.getenv 면 충분.
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return {
                "transcript": "",
                "error": "openai_key_missing",
                "language": "ko",
            }

        # 2) 실제 Whisper 호출.
        try:
            # AsyncOpenAI 는 가벼운 래퍼라 호출마다 인스턴스화해도 무방.
            # (싱글톤화는 app.core.openai_client 의 동기 클라이언트와 분리하여
            #  운영. 본 서비스는 async 라우트라 AsyncOpenAI 를 쓴다.)
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=api_key)

            # 명세 그대로의 호출 형태. 파일을 binary 로 열어 SDK 에 그대로 전달.
            # SDK 가 multipart 업로드 + 확장자 감지를 처리.
            with open(audio_path, "rb") as f:
                result = await client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                    language="ko",
                )

            # result.text 가 None 일 가능성에 대비해 빈 문자열로 보정.
            return {
                "transcript": result.text or "",
                "language": "ko",
            }
        except Exception as exc:  # noqa: BLE001 — 명세상 graceful fallback
            # 네트워크/쿼터/만료 등 모든 실패를 dict 응답으로 변환.
            # logger.warning 으로 진단은 남기되, 호출자(라우터)가 500 으로 변환.
            logger.warning("Whisper STT 호출 실패: %s", exc)
            return {
                "transcript": "",
                "error": str(exc),
                "language": "ko",
            }

    # =========================================================================
    # generate_command_id
    # =========================================================================
    def generate_command_id(self) -> str:
        """voice_command_id 를 생성한다.

        명세: "vc_" + uuid4 의 hex 앞 8자리.
        예) "vc_3f9a82c1"
        """
        # uuid4().hex 는 32자리 16진수 문자열. 앞 8자리만 사용해도
        # 데모 규모(요청 수십~수백 건)에서는 충돌 확률이 극히 낮음.
        return f"vc_{uuid.uuid4().hex[:8]}"

    # =========================================================================
    # save_command_to_db
    # =========================================================================
    async def save_command_to_db(
        self,
        document_id: str,
        transcript: str,
        input_type: str,
        audio_path: str | None = None,
    ) -> dict[str, Any]:
        """음성/텍스트 명령 레코드를 저장한다.

        TODO(DB 연동):
            DB 모델(voice_commands 테이블) 이 도입되면 다음 절차로 교체:
                INSERT INTO voice_commands (...) VALUES (...)
            현재는 DB 모델 절대 수정 금지 영역이므로 클래스 속성 메모리 리스트로 보관.

        Returns:
            {voice_command_id, document_id, transcript, input_type,
             audio_path, status, created_at} 의 dict.
        """
        # TODO: models.py에 아래 테이블 추가 필요 (팀장에게 요청):
        # - ocr_sources: id, document_id, image_path, raw_text, cleaned_text, confidence, created_at
        # - voice_commands: id, document_id, transcript, input_type, audio_path, status, created_at
        # - documents: id, owner_type, owner_id, title, source_type, file_type, parse_status, created_at
        # - document_texts: id, document_id, extracted_text, text_version, updated_at
        # 위 테이블이 추가되면 본 메서드 본문을 다음과 같이 교체:
        #     vc = VoiceCommand(document_id=..., transcript=..., input_type=...,
        #                       audio_path=..., status="received")
        #     db.add(vc); db.commit(); db.refresh(vc)
        #     return {... vc.id ...}
        record: dict[str, Any] = {
            "voice_command_id": self.generate_command_id(),
            "document_id": document_id,
            "transcript": transcript,
            "input_type": input_type,
            "audio_path": audio_path,
            # 저장 직후 상태. /transcribe 라우트는 응답에서 "transcribed" 로
            # 덮어쓰지만, /commands POST 는 "received" 그대로 사용.
            "status": "received",
            # ISO 8601 형식 (UTC). 프론트가 toLocaleString 등으로 변환하기 쉬움.
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        # 메모리 리스트에 append. (실제 DB INSERT 의 자리)
        self._IN_MEMORY_COMMANDS.append(record)
        return record

    # =========================================================================
    # get_commands_from_db
    # =========================================================================
    async def get_commands_from_db(self, document_id: str) -> list[dict[str, Any]]:
        """document_id 기준 명령 이력을 조회한다.

        TODO(DB 연동):
            SELECT * FROM voice_commands
             WHERE document_id = :document_id
             ORDER BY created_at DESC
            현재는 메모리 리스트에서 동일 document_id 항목만 필터링.

        Returns:
            저장된 record dict 의 리스트. 없으면 빈 리스트.
        """
        # TODO: models.py에 아래 테이블 추가 필요 (팀장에게 요청):
        # - ocr_sources: id, document_id, image_path, raw_text, cleaned_text, confidence, created_at
        # - voice_commands: id, document_id, transcript, input_type, audio_path, status, created_at
        # - documents: id, owner_type, owner_id, title, source_type, file_type, parse_status, created_at
        # - document_texts: id, document_id, extracted_text, text_version, updated_at
        # 위 테이블이 추가되면 본 메서드 본문을 다음과 같이 교체:
        #     return (db.query(VoiceCommand)
        #               .filter(VoiceCommand.document_id == document_id)
        #               .order_by(VoiceCommand.created_at.desc())
        #               .all())
        # 동일 document_id 인 레코드만 필터.
        # 정렬은 created_at 내림차순 (최신순).
        items = [
            cmd
            for cmd in self._IN_MEMORY_COMMANDS
            if cmd.get("document_id") == document_id
        ]
        items.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return items
