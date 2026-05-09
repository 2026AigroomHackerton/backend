"""External Storage Router.

외부 저장소(Google Drive, Notion 등) 연결 상태를 노출하는 Mock API.
실제 OAuth 연동은 포함되지 않으며, 하드코딩된 더미 데이터를 반환한다.
"""

# 타입 힌트의 지연 평가 활성화. (services/storage_service.py 와 동일한 이유)
from __future__ import annotations

# response_model 의 element 타입을 명시할 때 필요한 임의 타입 헬퍼.
from typing import Any

# FastAPI 의 라우터를 만들기 위한 클래스. main.py 에서 include_router 로 등록된다.
from fastapi import APIRouter

# 비즈니스 로직은 services 레이어로 분리한다.
# 라우터는 "HTTP 입출력 변환" 만 담당하고, 실제 데이터 가공/하드코딩은 service 가 책임진다.
from app.services import storage_service

# ─────────────────────────────────────────────────────────────────────
# APIRouter 설정.
#
# - prefix="/api/storage": 이 라우터의 모든 엔드포인트 앞에 자동으로 붙는 경로.
#   (다른 도메인 라우터들과 네임스페이스 충돌을 막기 위해 /api/<도메인> 규칙을 따름)
# - tags=["storage"]: Swagger UI(/docs) 에서 엔드포인트들을 "storage" 그룹으로 묶는다.
# ─────────────────────────────────────────────────────────────────────
router = APIRouter(prefix="/api/storage", tags=["storage"])


# GET /api/storage/providers
# - 프로젝트 공통 응답 envelope({success, data, message, error}) 으로 감싸 반환한다.
#   따라서 response_model 은 dict[str, Any] (envelope) 로 둔다.
# - data 필드 안에 provider 목록(list[dict]) 이 들어간다.
# - 추후 Pydantic 으로 envelope/항목을 강제 타입화하려면 ApiResponse + StorageProvider
#   스키마를 도입하는 것이 권장된다.
@router.get("/providers", response_model=dict[str, Any])
def get_providers() -> dict[str, Any]:
    """외부 저장소 provider 의 연결 상태 목록을 반환한다.

    공통 응답 포맷: {success, data, message, error}
    - data: provider 식별자와 status 를 담은 dict 의 리스트
            예) [{"provider": "google_drive", "status": "coming_soon"}, ...]
    """
    # service 호출 결과를 envelope 의 data 필드로 감싸서 반환한다.
    # 라우터에는 추가 가공 로직을 두지 않고, 공통 응답 형식 통일만 책임진다.
    providers = storage_service.list_providers()
    return {
        "success": True,
        "data": providers,
        "message": "",
        "error": None,
    }
