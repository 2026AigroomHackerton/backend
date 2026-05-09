"""
FastAPI 앱 진입점.

서버 부팅 시 이 모듈이 가장 먼저 실행되어 라우터들을 한곳에 모은다.
실제 비즈니스 로직은 각 라우터/서비스/리포지토리 모듈에 분리되어 있고,
본 파일은 다음만 책임진다.
    - FastAPI 앱 인스턴스 생성 (title/version)
    - 헬스체크용 루트 엔드포인트
    - 라우터 등록 (include_router)
    - 전역 예외 핸들러 등록 (Pydantic 검증 실패 응답을 명세 형식으로 통일)

라우터 등록 정책:
    - documents 라우터: 문서 업로드/조회/수정 API (`/api/documents/...`)
    - archive 라우터:   보관함 통합 조회 API (`/api/archive`)
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.routers import archive, documents

app = FastAPI(
    title="AI Mobile Document Assistant API",
    version="0.1.0",
)


@app.get("/")
def read_root() -> dict[str, str]:
    """헬스체크용 루트 엔드포인트 — 서버 기동 여부 확인용."""
    return {"status": "ok", "service": "AI Mobile Document Assistant API"}


# ---------------------------------------------------------------------------
# 전역 예외 핸들러: Pydantic 검증 실패 → 명세 형식 응답
# ---------------------------------------------------------------------------
# FastAPI 의 기본 동작은 RequestValidationError 발생 시
#   422 Unprocessable Entity + {"detail": [...]}
# 형태로 응답하는데, 본 프로젝트 명세는 모든 응답이
#   {"success": true/false, "data": ...}
# 구조여야 한다.
# 따라서 전역 핸들러를 등록해 422 응답도 명세 형식으로 감싼다.
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Pydantic / Form / Query 등 모든 요청 검증 실패를 단일 포맷으로 통일."""
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "data": {
                "code": "VALIDATION_ERROR",
                "message": "요청 형식이 올바르지 않습니다.",
                # detail 은 디버깅·개발 편의를 위해 원본 검증 오류를 그대로 노출.
                # 운영 단계에서 민감 정보 노출이 우려되면 마스킹 고려.
                "detail": exc.errors(),
            },
        },
    )


# ---------------------------------------------------------------------------
# 라우터 등록
# ---------------------------------------------------------------------------
# 각 라우터 모듈은 자체 prefix(/api/documents, /api/archive 등)를 갖고 있으므로
# 여기서는 prefix 를 따로 부여하지 않고 그대로 include 한다.
app.include_router(documents.router)
app.include_router(archive.router)
