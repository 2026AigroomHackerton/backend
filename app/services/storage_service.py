# [백엔드2 담당] 수정 허용 파일 - feature/backend-ocr-voice-storage 브랜치
"""External Storage Service.

[책임]
    외부 저장소(Google Drive, Notion) 와 로컬/샘플 임포트 도메인의 비즈니스 로직 계층.

[현 PR 범위 (해커톤 MVP)]
    - 로컬 파일 가져오기 : `save_uploaded_file()` 으로 실제 디스크 저장.
    - 샘플 문서 임포트   : `import_sample_document()` 으로 샘플 텍스트 → DB INSERT.
    - Google Drive       : stub (OAuth 미구현). 라우터에서 HTTP 501 응답 처리.
    - Notion             : stub (Integration Token 미설정). 라우터에서 HTTP 501.

[DB 연동 정책]
    documents / document_texts 모델은 "절대 수정 금지" 영역이라 실제 INSERT 코드를
    이 PR 에서 작성하지 않는다. 대신 호출부에서 db 세션이 들어왔을 때를 대비한
    분기와 TODO 주석을 남겨, 모델 PR 이 머지된 뒤 본 메서드 본문만 교체하면
    바로 동작하도록 인터페이스를 안정화한다.
"""

# 타입 힌트 지연 평가. 함수 시그니처에 쓰는 타입을 미리 import 하지 않아도 되게 해 준다.
from __future__ import annotations

# 진단용 로거. 예외 폴백 시 무엇이 실패했는지 남긴다.
import logging

# 디렉터리 자동 생성에 사용 (os.makedirs).
import os

# 응답/내부 자료구조의 값 타입을 유연하게 두기 위한 Any.
from typing import Any

# 업로드된 파일 객체 — multipart/form-data 의 파일 파라미터를 다룰 때 사용.
from fastapi import UploadFile

# 모듈 단위 로거. logger.warning(...) 등으로 사용.
logger = logging.getLogger(__name__)


# =============================================================================
# StorageService
# =============================================================================
class StorageService:
    """외부 저장소/임포트 통합 서비스.

    제공 메서드:
        - get_providers()                    : 명세 PROVIDERS 정적 목록 반환.
        - import_sample_document(...)        : 샘플 문서 텍스트 → DB INSERT (or TODO).
        - get_connectors_status()            : google_drive/notion 연결 상태.
        - save_uploaded_file(file, dest_dir) : 업로드 파일을 디스크에 저장.
    """

    # -------------------------------------------------------------------------
    # PROVIDERS — 클라이언트(Front) 용 정적 목록.
    # -------------------------------------------------------------------------
    # 항목 의미:
    #   provider     : 코드/식별용 키 (영문 snake_case)
    #   display_name : UI 표시명 (한국어 가능)
    #   status       : "available" | "coming_soon"
    #                  - available  : 즉시 사용 가능 (local, mock)
    #                  - coming_soon: OAuth/Integration 미구현이라 곧 지원 예정
    #   description  : 사용자 화면에 보여 줄 한 줄 설명
    # 클래스 속성으로 두는 이유:
    #   - 인스턴스마다 복사할 필요 없는 불변 메타데이터.
    #   - 테스트에서 StorageService.PROVIDERS 로 직접 접근 가능.
    PROVIDERS: list[dict[str, Any]] = [
        {
            "provider": "google_drive",
            "display_name": "Google Drive",
            "status": "coming_soon",
            "description": "Google Drive 문서를 가져옵니다. (준비 중)",
        },
        {
            "provider": "notion",
            "display_name": "Notion",
            "status": "coming_soon",
            "description": "Notion 페이지를 가져옵니다. (준비 중)",
        },
        {
            "provider": "local",
            "display_name": "로컬 파일",
            "status": "available",
            "description": "기기에서 파일을 직접 업로드합니다.",
        },
        {
            "provider": "mock",
            "display_name": "샘플 문서",
            "status": "available",
            "description": "데모용 샘플 문서를 불러옵니다.",
        },
    ]

    # -------------------------------------------------------------------------
    # SAMPLE_DOCUMENTS — 샘플 임포트용 더미 텍스트.
    # -------------------------------------------------------------------------
    # 키(document_type) 는 한국어이며, 라우터가 받은 값으로 그대로 lookup 한다.
    # 키가 없으면 "가정통신문" 으로 폴백한다 (import_sample_document 참고).
    SAMPLE_DOCUMENTS: dict[str, dict[str, str]] = {
        "가정통신문": {
            "title": "2026학년도 5월 가정통신문",
            "text": (
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
            ),
        },
        "지원서": {
            "title": "프로그램 지원서",
            "text": (
                "프로그램 지원서\n"
                "\n"
                "성명: \n"
                "연락처: \n"
                "이메일: \n"
                "주소: \n"
                "\n"
                "지원 동기:\n"
                "\n"
                "자기소개:\n"
                "\n"
                "희망 직무:\n"
            ),
        },
        "회의록": {
            "title": "팀 회의록",
            "text": (
                "회의록\n"
                "\n"
                "일시: 2026년 5월 9일\n"
                "장소: 회의실 A\n"
                "참석자: \n"
                "\n"
                "안건:\n"
                "1. \n"
                "2. \n"
                "\n"
                "결정 사항:\n"
                "\n"
                "다음 회의 일정: "
            ),
        },
    }

    # =========================================================================
    # Public: get_providers
    # =========================================================================
    def get_providers(self) -> list[dict[str, Any]]:
        """PROVIDERS 정적 목록을 반환한다.

        호출부(라우터)는 이 결과를 공통 응답 envelope 의 `data.providers` 필드에 담는다.
        반환값은 호출자가 변형해도 클래스 상수가 영향을 받지 않도록 얕은 복사본을 준다.
        """
        # list(...) 로 새 리스트를 만들어 반환 — 호출자가 append/pop 해도 PROVIDERS 자체는 안전.
        return list(self.PROVIDERS)

    # =========================================================================
    # Public: import_sample_document
    # =========================================================================
    async def import_sample_document(
        self,
        document_type: str,
        db: Any = None,
    ) -> dict[str, Any]:
        """샘플 문서를 임포트한다.

        흐름:
            1) SAMPLE_DOCUMENTS 에서 document_type 으로 샘플 lookup.
               존재하지 않으면 "가정통신문" 으로 폴백.
            2) db 세션이 있으면 documents + document_texts 테이블에 INSERT.
               (현재 PR 에서는 모델이 수정 금지 영역이라 TODO 만 남김)
            3) db 가 없으면 INSERT 를 건너뛰고 imported_document_id 를 None 으로 둔다.
            4) 응답 dict 반환.

        Args:
            document_type : "가정통신문" | "지원서" | "회의록" 등.
            db            : SQLAlchemy 세션 (또는 동등한 DB handle). 미주입 시 None.

        Returns:
            {imported_document_id, title, source_type, extracted_text, status}
        """
        # ---- 1) 샘플 lookup (없으면 기본값 "가정통신문") ------------------------
        sample = self.SAMPLE_DOCUMENTS.get(document_type) or self.SAMPLE_DOCUMENTS["가정통신문"]
        title: str = sample["title"]
        text: str = sample["text"]

        # imported_document_id: 실제 DB INSERT 가 일어났을 때의 PK. 기본 None.
        imported_document_id: Any = None

        # ---- 2) DB 세션이 있으면 INSERT 시도 ------------------------------------
        if db is not None:
            try:
                # TODO(실제 DB 연동):
                #   documents 테이블 INSERT:
                #       title=title, source_type="mock",
                #       file_type="txt", parse_status="done"
                #   document_texts 테이블 INSERT:
                #       document_id=<위에서 생성된 id>, extracted_text=text
                #   commit 후 생성된 document.id 를 imported_document_id 에 대입.
                #
                # documents 모델은 "절대 수정 금지" 영역이라 본 PR 에서 직접 작성하지 않는다.
                # 모델 PR 이 머지된 뒤 이 블록 본문만 채우면 동작.
                imported_document_id = None
            except Exception as exc:  # noqa: BLE001 — INSERT 실패해도 임포트 자체는 응답
                logger.warning("샘플 문서 DB INSERT 실패: %s", exc)
                imported_document_id = None
        else:
            # TODO(실제 DB 연동):
            #   라우터에서 db 의존성을 주입하도록 변경되면 위 블록이 활성화된다.
            #   현재는 DB 세션 팩토리가 아직 없어 None 으로 호출됨.
            imported_document_id = None

        # ---- 3) 응답 페이로드 ----------------------------------------------------
        return {
            "imported_document_id": imported_document_id,
            "title": title,
            "source_type": "mock",
            "extracted_text": text,
            "status": "imported",
        }

    # =========================================================================
    # Public: get_connectors_status
    # =========================================================================
    def get_connectors_status(self) -> list[dict[str, Any]]:
        """OAuth/Integration 기반 외부 커넥터의 연결 상태를 반환한다.

        명세상 현재 단계에서는 google_drive, notion 모두 "disconnected" 로 고정.
        실제 연동 시에는 토큰 만료/리프레시 상태를 검사해 status 를 동적으로 결정.

        Returns:
            [{provider, display_name, status, connected_at}, ...]
        """
        # connected_at 은 ISO8601 datetime 문자열 또는 None.
        # 현재는 연결된 적이 없으므로 None 으로 통일.
        return [
            {
                "provider": "google_drive",
                "display_name": "Google Drive",
                "status": "disconnected",
                "connected_at": None,
            },
            {
                "provider": "notion",
                "display_name": "Notion",
                "status": "disconnected",
                "connected_at": None,
            },
        ]

    # =========================================================================
    # Public: save_uploaded_file
    # =========================================================================
    async def save_uploaded_file(self, file: UploadFile, dest_dir: str) -> str:
        """업로드된 파일을 dest_dir 에 저장하고 저장 경로를 반환한다.

        - dest_dir 이 없으면 os.makedirs 로 자동 생성 (exist_ok=True).
        - filename 이 비어 있을 경우 'uploaded_file' 로 폴백.
        - bytes 단위 쓰기 (binary-safe).

        Args:
            file     : FastAPI UploadFile (multipart 파일).
            dest_dir : 저장 디렉터리 (상대/절대 경로 모두 가능).

        Returns:
            저장된 파일의 전체 경로 문자열.
        """
        # ---- 1) 대상 디렉터리 보장 ----------------------------------------------
        # exist_ok=True : 디렉터리가 이미 있어도 에러 없이 통과.
        os.makedirs(dest_dir, exist_ok=True)

        # ---- 2) 저장 파일명 결정 -------------------------------------------------
        # 클라이언트가 filename 을 비워 보내는 경우(예: 잘못된 multipart) 대비 폴백.
        filename = file.filename or "uploaded_file"
        # os.path.join 은 OS 별 구분자를 알아서 처리한다.
        dest_path = os.path.join(dest_dir, filename)

        # ---- 3) 파일 본문 읽고 디스크에 쓰기 -------------------------------------
        # await 가 필요한 이유: UploadFile.read() 는 비동기 코루틴.
        content = await file.read()
        # "wb" : 바이너리 쓰기 모드. 텍스트로 열면 인코딩 오류 발생 가능.
        with open(dest_path, "wb") as f:
            f.write(content)

        # ---- 4) 저장 경로 반환 ---------------------------------------------------
        return dest_path
