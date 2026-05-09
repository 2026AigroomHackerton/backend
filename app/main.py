from fastapi import FastAPI

from app.routers import ocr as ocr_router
from app.routers import storage as storage_router
# 백엔드 2: 음성/텍스트 명령 라우터 (POST /api/voice/commands, GET /api/voice/commands).
# ocr/storage 와 동일하게 모듈을 import 한 뒤 .router 속성으로 접근.
from app.routers import voice as voice_router
# 백엔드 2: AI 문서 수정 라우터 (POST /api/ai/command-edit). 모듈 자체를
# import 한 뒤 .router 속성으로 접근하는 패턴(팀 컨벤션, 위 ocr/storage 동일).
from app.routers import ai as ai_router

app = FastAPI(
    title="AI Mobile Document Assistant API",
    version="0.1.0",
)

app.include_router(ocr_router.router)
app.include_router(storage_router.router)
# 백엔드 2: voice 라우터 등록. 호출 후 /api/voice/* 엔드포인트가 노출된다.
app.include_router(voice_router.router)
# 백엔드 2: AI 라우터 등록. 호출 후 /api/ai/* 엔드포인트가 노출된다.
app.include_router(ai_router.router)


@app.get("/")
def read_root() -> dict[str, str]:
    return {"status": "ok", "service": "AI Mobile Document Assistant API"}
