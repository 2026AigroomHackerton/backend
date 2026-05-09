# [백엔드2 담당] 수정 허용 파일 - feature/backend-ocr-voice-storage 브랜치
"""OCR Service (명세 정렬 버전 — Vision 엔진 + Mock 폴백 유지).

[책임]
    이미지 → 텍스트 추출 도메인의 비즈니스 로직 계층.
    OpenAI Vision 으로 실제 OCR 을 시도하고, 키/SDK/네트워크 사정으로 실패하면
    명세 더미 텍스트로 자동 폴백한다.

[명세 정렬 사항(2번 옵션)]
    - 응답 스키마 / 에러 코드 / 헬퍼 위치를 [기능 2] 명세에 맞춤.
    - 메서드를 명세 4단(validate_image / save_image / extract_text_from_image /
      generate_ocr_id) 으로 분해하여 라우터가 명세 순서 그대로 호출 가능.
    - ALLOWED_TYPES / OCR_IMAGE_DIR 을 클래스 속성으로 노출 (테스트/외부 참조용).
    - 공통 응답 헬퍼 success_response / error_response 를 모듈 상단에 둠.

[엔진 정책]
    - 명세는 pytesseract 기반이지만, 본 PR 은 Vision API + mock 폴백을 유지.
    - 따라서 명세상 OCR_ENGINE_NOT_FOUND 는 "Vision 도 안 되고 mock 폴백도 막힌"
      극단적 케이스에 대한 표준 코드로만 정의해 둔다 (현재 흐름에선 발생 안 함).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import UploadFile

# core/* 는 본 PR 의 수정 허용 범위가 아니므로 import 만 한다.
# get_client(): OpenAI 클라이언트 싱글톤 또는 None.
# OPENAI_MODEL_VISION: 사용할 vision 모델명.
from app.core.openai_client import get_client
from app.core.config import OPENAI_MODEL_VISION

logger = logging.getLogger(__name__)


# =============================================================================
# 공통 응답 헬퍼 (명세 [공통 응답 헬퍼])
# =============================================================================
# 명세는 ocr_service.py "내부 또는 상단" 위치를 요구. 라우터/다른 서비스가
# 동일 envelope 을 재사용할 수 있도록 모듈 최상단에 둔다.
def success_response(data: dict[str, Any], message: str = "") -> dict[str, Any]:
    """공통 성공 응답 envelope.

    스키마:
        {"success": True, "data": ..., "message": ..., "error": None}
    """
    return {"success": True, "data": data, "message": message, "error": None}


def error_response(
    error: str,
    message: str = "",
    data: Any = None,
) -> dict[str, Any]:
    """공통 실패 응답 envelope.

    스키마:
        {"success": False, "data": ..., "message": ..., "error": "<CODE>"}
    """
    return {"success": False, "data": data, "message": message, "error": error}


# =============================================================================
# 도메인 예외 — 라우터에서 HTTP 코드 + envelope 으로 매핑
# =============================================================================
class OcrServiceError(Exception):
    """OCR 도메인 공통 베이스 예외."""

    code: str = "OCR_SERVICE_ERROR"
    http_status: int = 500


class InvalidFileTypeError(OcrServiceError):
    """이미지가 아닌 파일 또는 허용되지 않은 MIME 타입."""

    code = "INVALID_FILE_TYPE"
    http_status = 400


class FileSaveFailedError(OcrServiceError):
    """업로드 이미지를 디스크에 저장하지 못함 (권한/디스크 등)."""

    code = "FILE_SAVE_FAILED"
    http_status = 500


class OcrEngineNotFoundError(OcrServiceError):
    """OCR 엔진을 찾을 수 없음 (명세상 Tesseract 미설치 케이스)."""

    code = "OCR_ENGINE_NOT_FOUND"
    http_status = 500


class OcrSourceNotFoundError(OcrServiceError):
    """ocr_source_id 에 해당하는 레코드를 찾을 수 없음."""

    code = "OCR_SOURCE_NOT_FOUND"
    http_status = 404


# =============================================================================
# 상수
# =============================================================================
# 명세 [기능 2] 더미 텍스트. mock 폴백/get_result 에서 사용.
DUMMY_OCR_TEXT = (
    "2026학년도 가정통신문\n"
    "\n"
    "안녕하십니까. 학부모님의 가정에 건강과 행복이 가득하기를 바랍니다.\n"
    "\n"
    "이번 주 활동 안내\n"
    "- 활동명: 환경정화 활동\n"
    "- 일시: 2026년 5월 20일 (화) 오전 10시\n"
    "- 장소: 학교 주변 공원\n"
    "- 준비물: 편한 복장, 장갑\n"
    "\n"
    "참가 여부를 5월 15일까지 담임 선생님께 알려주시기 바랍니다.\n"
    "\n"
    "담당 교사: 홍길동\n"
    "연락처: 010-1234-5678"
)

# 라우터 호환을 위해 모듈 레벨에서도 노출 (기존 import 경로 유지).
# 실 진실은 OcrService.ALLOWED_TYPES (클래스 속성).
ALLOWED_IMAGE_CONTENT_TYPES: set[str] = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}

# content-type → 파일 확장자 매핑.
_CONTENT_TYPE_TO_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

# Vision 모델 시스템 프롬프트.
_OCR_SYSTEM_PROMPT = (
    "너는 이미지 OCR 어시스턴트다. 사용자가 보낸 이미지에서 보이는 한국어/영문 "
    "텍스트만 그대로 추출해 평문으로 반환해라. 추측·요약·번역하지 말고, "
    "줄바꿈은 가능한 원본 레이아웃을 유지해라. 텍스트 외 설명·마크다운 금지."
)

# 파일명 sanitize 용. path traversal / 공백 / 특수문자 제거.
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


# =============================================================================
# OcrService
# =============================================================================
class OcrService:
    """OCR 텍스트 추출/조회/확정 책임 서비스.

    명세 정렬 메서드:
        - validate_image(file)              : MIME 검증 (실패 시 InvalidFileTypeError).
        - save_image(file)                  : 디스크 저장 (실패 시 FileSaveFailedError).
        - extract_text_from_image(path)     : Vision 호출 → 텍스트 추출 + confidence.
        - generate_ocr_id()                 : "ocr_<uuid8>" 식별자 발급.
        - get_result(ocr_source_id)         : in-memory store 조회 (없으면 404 raise).
        - confirm_result(ocr_source_id, ed) : confirmed_text 갱신 (없으면 404 raise).
    """

    # 명세 [OcrService 클래스] 클래스 속성.
    ALLOWED_TYPES: list[str] = [
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
    ]
    OCR_IMAGE_DIR: str = "uploads/ocr-images"

    # 인스턴스별 in-memory store. DB 연동 전 GET/confirm 동작을 시뮬레이션.
    # main.py 에서 싱글톤으로 사용되므로 프로세스 생존 동안 유지된다.
    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    # =========================================================================
    # 1) validate_image — 명세 시그니처 그대로
    # =========================================================================
    def validate_image(self, file: UploadFile) -> None:
        """업로드 파일의 content_type 이 ALLOWED_TYPES 에 있는지 검증.

        Raises:
            InvalidFileTypeError: 허용되지 않은 MIME 타입.
        """
        if file.content_type not in self.ALLOWED_TYPES:
            raise InvalidFileTypeError(
                f"지원하지 않는 이미지 형식입니다: {file.content_type!r}"
            )

    # =========================================================================
    # 2) save_image — 명세 시그니처 그대로 (uploads/ocr-images/<ts>_<원본명>)
    # =========================================================================
    async def save_image(self, file: UploadFile) -> str:
        """업로드 이미지를 디스크에 저장하고 저장 경로를 반환한다.

        파일명 정책:
            - 명세: "uploads/ocr-images/<timestamp>_<원본파일명>".
            - 보안상 원본 파일명은 sanitize 한다 (path traversal 차단).
            - sanitize 결과가 비면 uuid 단편 + content-type 기반 확장자로 대체.

        Raises:
            FileSaveFailedError: 디스크 쓰기 실패.

        Returns:
            저장된 파일의 경로 문자열 (POSIX 슬래시).
            예) "uploads/ocr-images/20260509_153012_invoice.jpg"
        """
        # 디렉토리 보장 (명세: os.makedirs(exist_ok=True)).
        target_dir = Path(self.OCR_IMAGE_DIR)
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise FileSaveFailedError(f"업로드 디렉토리 생성 실패: {exc}") from exc

        # 원본 파일명 sanitize.
        safe_name = self._sanitize_filename(file.filename, file.content_type)

        # timestamp prefix.
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved_filename = f"{timestamp}_{safe_name}"
        target_path = target_dir / saved_filename

        # 파일 본문 읽기 (UploadFile.read 는 비동기).
        try:
            content = await file.read()
        except Exception as exc:  # noqa: BLE001
            raise FileSaveFailedError(f"업로드 본문 읽기 실패: {exc}") from exc

        # 디스크 쓰기는 별도 스레드에서 (이벤트 루프 보호).
        try:
            await asyncio.to_thread(target_path.write_bytes, content)
        except OSError as exc:
            raise FileSaveFailedError(f"파일 저장 실패: {exc}") from exc

        # 명세 응답 키 image_path 에 들어갈 값 (POSIX 슬래시).
        return target_path.as_posix()

    # =========================================================================
    # 3) extract_text_from_image — 명세 시그니처. 실제 Vision 호출 + mock 폴백.
    # =========================================================================
    def extract_text_from_image(self, image_path: str) -> dict[str, Any]:
        """저장된 이미지에서 텍스트를 추출.

        명세상 반환 dict:
            {"text": "<extracted>", "confidence": <0~1 float>}
            엔진 미설치 시:
            {"text": "", "confidence": 0.0, "error": "tesseract_not_found"}

        본 구현은 Vision 을 사용하되, 키/SDK 부재 또는 호출 실패 시
        명세 더미 텍스트로 폴백한다 (사용자 옵션 2번 정책).
        """
        # 클라이언트 부재 = 명세상 엔진 미설치와 의미가 가장 가깝다.
        # 다만 정책상 mock 폴백을 유지하므로 에러로 raise 하지 않고 더미 반환.
        client = get_client()
        if client is None:
            logger.info("OpenAI client 부재 → mock 텍스트 폴백")
            return {
                "text": DUMMY_OCR_TEXT,
                "confidence": 0.91,
                "_source": "mock",
            }

        # 디스크에서 bytes 로드.
        try:
            image_bytes = Path(image_path).read_bytes()
        except OSError as exc:
            # 저장은 성공했는데 곧장 못 읽는 비정상 상태 → 폴백.
            logger.warning("저장 이미지 재로딩 실패 → mock 폴백: %s", exc)
            return {
                "text": DUMMY_OCR_TEXT,
                "confidence": 0.91,
                "_source": "mock_fallback",
                "_error": str(exc),
            }

        # content-type 은 확장자로 역추정 (없으면 image/png 기본).
        ext = Path(image_path).suffix.lower()
        content_type = next(
            (ct for ct, e in _CONTENT_TYPE_TO_EXT.items() if e == ext),
            "image/png",
        )

        # Vision 호출.
        try:
            text, model_used = self._call_openai_vision(image_bytes, content_type)
            stripped = (text or "").strip()
            return {
                "text": stripped or DUMMY_OCR_TEXT,
                # OpenAI 는 글자별 score 를 주지 않으므로 1.0 으로 표기.
                "confidence": 1.0 if stripped else 0.91,
                "_source": "openai" if stripped else "mock_fallback",
                "_model": model_used,
            }
        except Exception as exc:  # noqa: BLE001 — 폴백 대상
            logger.warning("OpenAI Vision OCR 실패 → mock 폴백: %s", exc)
            return {
                "text": DUMMY_OCR_TEXT,
                "confidence": 0.91,
                "_source": "mock_fallback",
                "_error": str(exc),
            }

    # =========================================================================
    # 4) generate_ocr_id — 명세 ("ocr_" + uuid4 앞 8자리)
    # =========================================================================
    def generate_ocr_id(self) -> str:
        return f"ocr_{uuid.uuid4().hex[:8]}"

    # =========================================================================
    # 5) create_document_from_ocr — OCR 결과로 documents + document_texts INSERT
    # =========================================================================
    def create_document_from_ocr(
        self,
        db: Any,
        image_path: str,
        extracted_text: str,
        original_filename: str | None,
        content_type: str | None,
    ) -> int | None:
        """OCR 결과를 documents + document_texts 두 테이블에 INSERT.

        라우터에서 `create_document=True` 일 때만 호출된다.

        Args:
            db: SQLAlchemy Session (라우터의 Depends(get_db) 로 주입).
                None 이면 INSERT 를 건너뛰고 None 반환 (graceful 폴백).
            image_path: save_image 가 반환한 디스크 경로 (file_path 컬럼에 저장).
            extracted_text: extract_text_from_image 결과 본문.
            original_filename: 클라이언트가 보낸 파일명 (원본).
            content_type: image/png 등 MIME.

        Returns:
            생성된 documents.id. db=None 이거나 INSERT 실패 시 None.

        Notes:
            - documents 테이블의 NOT NULL 컬럼을 모두 채워야 IntegrityError 회피.
            - source_type='ocr' 로 표시해 storage mock-import 와 구분.
            - document_texts.text_version=1 로 시작.
            - 어느 단계에서든 실패하면 rollback 후 None 반환 (응답은 OCR 결과만 줌).
        """
        if db is None:
            return None

        try:
            # raw SQL 로 INSERT (storage_service 의 동일 패턴과 일관). ORM 모델을
            # 직접 import 하지 않는 이유: 통합 단계에서 모델 구조가 바뀌면 다른
            # 서비스도 영향받을 수 있어, 명세 컬럼명에 직접 의존시키는 게 안전.
            from sqlalchemy import text as _sql_text  # type: ignore
            import uuid as _uuid

            # 디스크 메타데이터 (가능하면 실제 값, 안 되면 폴백).
            image_p = Path(image_path)
            try:
                file_size_bytes = image_p.stat().st_size
            except OSError:
                file_size_bytes = len(extracted_text.encode("utf-8"))

            ext = image_p.suffix.lower() or ".bin"
            stored = image_p.name or f"ocr_{_uuid.uuid4().hex[:12]}{ext}"
            now_iso = datetime.utcnow().isoformat()
            title = (original_filename or "OCR 추출 문서").rsplit(".", 1)[0]

            # ----- documents INSERT -----------------------------------------
            doc_result = db.execute(
                _sql_text(
                    "INSERT INTO documents ("
                    " user_id, original_filename, stored_filename, file_path,"
                    " file_extension, file_size, content_type,"
                    " title, source_type, file_type, parse_status,"
                    " owner_type, owner_id, created_at"
                    ") VALUES ("
                    " 1, :original_filename, :stored_filename, :file_path,"
                    " :ext, :file_size, :content_type,"
                    " :title, 'ocr', :file_type, 'done',"
                    " 'user', 1, :created_at"
                    ")"
                ),
                {
                    "original_filename": original_filename or "ocr_image",
                    "stored_filename": stored,
                    "file_path": image_path,
                    "ext": ext,
                    "file_size": file_size_bytes,
                    "content_type": content_type,
                    "title": title,
                    # file_type 은 명세 컬럼. 확장자 앞의 점 제거해 'png' 형태로 저장.
                    "file_type": ext.lstrip("."),
                    "created_at": now_iso,
                },
            )
            new_document_id = doc_result.lastrowid
            if new_document_id is None:
                raise RuntimeError("documents INSERT 후 lastrowid 가 None")

            # ----- document_texts INSERT ------------------------------------
            db.execute(
                _sql_text(
                    "INSERT INTO document_texts "
                    "(document_id, extracted_text, text_version, updated_at) "
                    "VALUES (:document_id, :extracted_text, 1, :updated_at)"
                ),
                {
                    "document_id": new_document_id,
                    "extracted_text": extracted_text,
                    "updated_at": now_iso,
                },
            )

            db.commit()

            # ----- 자동 채움 트리거 -----------------------------------------
            # OCR 로 본문이 막 들어왔으니 양식 빈칸 검출 → extracted_fields 생성.
            # 실패해도 OCR 응답 자체에는 영향이 없도록 광범위 catch.
            try:
                from app.services import autofill_service  # 지연 import (순환 방지)
                autofill_service.autofill_document(
                    document_id=int(new_document_id),
                    user_id=1,
                    extracted_text=extracted_text,
                )
            except Exception as autofill_exc:  # noqa: BLE001
                logger.warning(
                    "자동 채움 트리거 실패 (document_id=%s): %s",
                    new_document_id, autofill_exc,
                )

            return int(new_document_id)

        except Exception as exc:  # noqa: BLE001 — INSERT 실패해도 OCR 응답은 살림
            logger.warning("OCR → documents INSERT 실패 → document_id=None 폴백: %s", exc)
            try:
                db.rollback()
            except Exception:  # noqa: BLE001 — rollback 자체 실패도 무시
                pass
            return None

    # =========================================================================
    # 보조: extract 결과를 in-memory store 에 등록 (GET/confirm 동작용)
    # =========================================================================
    def remember(
        self,
        ocr_source_id: str,
        extracted_text: str,
        image_path: str | None,
    ) -> None:
        """라우터가 extract 종료 시 호출. 이후 GET/confirm 에서 조회 가능.

        역할: save_ocr_result() 에 해당. ocr_sources 테이블 INSERT 자리.
        """
        # TODO: models.py에 아래 테이블 추가 필요 (팀장에게 요청):
        # - ocr_sources: id, document_id, image_path, raw_text, cleaned_text, confidence, created_at
        # - voice_commands: id, document_id, transcript, input_type, audio_path, status, created_at
        # - documents: id, owner_type, owner_id, title, source_type, file_type, parse_status, created_at
        # - document_texts: id, document_id, extracted_text, text_version, updated_at
        # 위 테이블이 추가되면 본 메서드 본문을 다음과 같이 교체:
        #     src = OcrSource(document_id=None, image_path=image_path,
        #                     raw_text=extracted_text, cleaned_text=extracted_text,
        #                     confidence=...)
        #     db.add(src); db.commit(); db.refresh(src)
        self._store[ocr_source_id] = {
            "ocr_source_id": ocr_source_id,
            "raw_text": extracted_text,
            "cleaned_text": extracted_text,
            "image_path": image_path,
            "status": "extracted",
        }

    # =========================================================================
    # GET /api/ocr/{ocr_source_id}
    # =========================================================================
    def get_result(self, ocr_source_id: str) -> dict[str, Any]:
        """OCR 결과를 단건 조회 (= get_ocr_result()).

        Raises:
            OcrSourceNotFoundError: 미등록 ID.
        """
        # TODO: models.py에 아래 테이블 추가 필요 (팀장에게 요청):
        # - ocr_sources: id, document_id, image_path, raw_text, cleaned_text, confidence, created_at
        # - voice_commands: id, document_id, transcript, input_type, audio_path, status, created_at
        # - documents: id, owner_type, owner_id, title, source_type, file_type, parse_status, created_at
        # - document_texts: id, document_id, extracted_text, text_version, updated_at
        # 위 테이블이 추가되면 본 메서드 본문을 다음과 같이 교체:
        #     return (db.query(OcrSource)
        #               .filter(OcrSource.id == ocr_source_id)
        #               .first())
        record = self._store.get(ocr_source_id)
        if record is None:
            raise OcrSourceNotFoundError(
                f"존재하지 않는 ocr_source_id: {ocr_source_id}"
            )
        # 명세 응답 키 4종만 노출 (status 등은 제외).
        return {
            "ocr_source_id": record["ocr_source_id"],
            "raw_text": record["raw_text"],
            "cleaned_text": record["cleaned_text"],
            "image_path": record["image_path"],
        }

    # =========================================================================
    # POST /api/ocr/{ocr_source_id}/confirm
    # =========================================================================
    def confirm_result(
        self, ocr_source_id: str, edited_text: str
    ) -> dict[str, Any]:
        """사용자 수정본을 확정 상태로 저장 (= update_cleaned_text()).

        Raises:
            OcrSourceNotFoundError: 미등록 ID.
        """
        # TODO: models.py에 아래 테이블 추가 필요 (팀장에게 요청):
        # - ocr_sources: id, document_id, image_path, raw_text, cleaned_text, confidence, created_at
        # - voice_commands: id, document_id, transcript, input_type, audio_path, status, created_at
        # - documents: id, owner_type, owner_id, title, source_type, file_type, parse_status, created_at
        # - document_texts: id, document_id, extracted_text, text_version, updated_at
        # 위 테이블이 추가되면 본 메서드 본문을 다음과 같이 교체:
        #     src = (db.query(OcrSource)
        #              .filter(OcrSource.id == ocr_source_id).first())
        #     if not src: raise OcrSourceNotFoundError(...)
        #     src.cleaned_text = edited_text
        #     db.commit()
        record = self._store.get(ocr_source_id)
        if record is None:
            raise OcrSourceNotFoundError(
                f"존재하지 않는 ocr_source_id: {ocr_source_id}"
            )
        record["cleaned_text"] = edited_text
        record["status"] = "confirmed"
        return {
            "ocr_source_id": ocr_source_id,
            "confirmed_text": edited_text,
            "status": "confirmed",
        }

    # =========================================================================
    # 내부 헬퍼들
    # =========================================================================
    @classmethod
    def _sanitize_filename(
        cls,
        original_filename: str | None,
        content_type: str | None,
    ) -> str:
        """원본 파일명을 안전하게 정제.

        - path traversal 방지를 위해 디렉토리 구분자 제거.
        - 영숫자/._- 만 허용. 그 외는 _ 로 치환.
        - 비어버리면 uuid + content-type 기반 확장자로 대체.
        """
        if original_filename:
            # 디렉토리 부분 제거 (Windows / POSIX 양쪽).
            base = original_filename.replace("\\", "/").rsplit("/", 1)[-1]
            sanitized = _UNSAFE_FILENAME_CHARS.sub("_", base).strip("._-")
            if sanitized:
                return sanitized

        # fallback — uuid + 확장자.
        ext = _CONTENT_TYPE_TO_EXT.get(content_type or "", ".bin")
        return f"upload_{uuid.uuid4().hex[:8]}{ext}"

    @staticmethod
    def _image_bytes_to_data_url(
        image_bytes: bytes, content_type: str | None
    ) -> str:
        """이미지 bytes 를 OpenAI Vision 의 image_url(data URL) 입력으로 변환."""
        mime = content_type or "image/png"
        b64 = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{mime};base64,{b64}"

    def _call_openai_vision(
        self, image_bytes: bytes, content_type: str | None
    ) -> tuple[str, str]:
        """OpenAI Vision 으로 이미지 → 텍스트.

        Returns:
            (extracted_text, model_used) 튜플.
        """
        client = get_client()
        if client is None:
            # extract_text_from_image 에서 이미 가드되어 있음 — 방어적 raise.
            raise OcrEngineNotFoundError("OpenAI client is not configured")

        data_url = self._image_bytes_to_data_url(image_bytes, content_type)
        completion = client.chat.completions.create(
            model=OPENAI_MODEL_VISION,
            messages=[
                {"role": "system", "content": _OCR_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "이 이미지의 텍스트를 추출해줘."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            temperature=0,
        )
        text = completion.choices[0].message.content or ""
        return text, OPENAI_MODEL_VISION
