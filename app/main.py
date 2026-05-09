"""
FastAPI 앱 진입점.

서버 부팅 시 이 모듈이 가장 먼저 실행되어 라우터들을 한곳에 모은다.
실제 비즈니스 로직은 각 라우터/서비스/리포지토리 모듈에 분리되어 있고,
본 파일은 다음만 책임진다.
    - FastAPI 앱 인스턴스 생성 (title/version)
    - 헬스체크용 루트 엔드포인트
    - 라우터 등록 (include_router)

라우터 등록 정책:
    [백엔드 1 도메인]
      - documents 라우터: 문서 업로드/조회/수정 API (`/api/documents/...`)
      - archive   라우터: 보관함 통합 조회 API (`/api/archive`)
    [백엔드 2 도메인]
      - ocr     라우터: 이미지 OCR API (`/api/ocr/...`)
      - voice   라우터: 음성/텍스트 명령 API (`/api/voice/...`)
      - storage 라우터: 외부 저장소/임포트 API (`/api/storage/...`, `/api/connectors/...`)
      - ai      라우터: AI 문서 수정 API (`/api/ai/...`)
"""

from fastapi import FastAPI

# 백엔드 2 라우터 — 모듈을 alias 로 import 한 뒤 .router 속성으로 접근.
from app.routers import ocr as ocr_router
from app.routers import storage as storage_router
from app.routers import voice as voice_router
from app.routers import ai as ai_router

# 백엔드 1 라우터 — 모듈을 직접 import (alias 없이) .router 로 접근.
from app.routers import archive, documents, profile


app = FastAPI(
    title="AI Mobile Document Assistant API",
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# 라우터 등록
# ---------------------------------------------------------------------------
# 각 라우터 모듈은 자체 prefix(/api/documents, /api/archive, /api/ocr 등) 를
# 갖고 있으므로 여기서는 prefix 를 따로 부여하지 않고 그대로 include 한다.

# 백엔드 2: OCR / 외부저장소 / 음성 / AI
app.include_router(ocr_router.router)
app.include_router(storage_router.router)
app.include_router(voice_router.router)
app.include_router(ai_router.router)

# 백엔드 1: 문서 / 보관함 / 프로필
app.include_router(documents.router)
app.include_router(archive.router)
app.include_router(profile.router)


@app.get("/")
def read_root() -> dict[str, str]:
    """헬스체크용 루트 엔드포인트 — 서버 기동 여부 확인용."""
    return {"status": "ok", "service": "AI Mobile Document Assistant API"}
