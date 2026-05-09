"""
아카이브 라우터.

이 모듈은 모바일 앱의 "보관함/아카이브" 화면에 노출할 데이터를 한 번에 묶어 반환한다.
구체적으로 다음 두 영역을 합쳐 응답한다.
    - local:    데모 사용자(`user_id=1`) 가 본 서비스에 직접 업로드한 문서들
                (deleted_at IS NULL 활성 문서, folder_id 별로 그룹핑하여 폴더/카테고리 단위 노출)
    - external: 외부 클라우드(예: Google Drive, Notion) 연동 자리표시 더미.
                실제 연동은 후속 단계에서 작업 예정이며, 현재는 status="coming_soon" 으로 표시.

API 공통 규칙:
    - 모든 응답은 `{"success": true/false, "data": ...}` 구조 유지
    - 인증은 해커톤 MVP 단계에서 생략, `user_id=1` 데모 사용자 고정

아키텍처 메모:
    - 본래 router → service → repository 순으로 호출이 흘러야 하지만,
      `document_service.list_documents` 응답은 folder_id 를 누락한 채 매핑된 카드만 돌려주므로
      그룹핑을 위해 본 라우터에서는 예외적으로 `document_repository` 를 직접 호출한다.
      service / repository 파일 수정이 금지된 본 작업의 제약 하에서 가장 깔끔한 절충안.
      (TODO: 추후 service 에 `list_documents_with_folder` 같은 함수가 생기면 거기로 옮길 것)
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.repositories import document_repository

# ---------------------------------------------------------------------------
# 데모 사용자 ID
# ---------------------------------------------------------------------------
# 인증/세션 전 단계에서 모든 요청은 동일한 데모 사용자로 처리한다.
DEMO_USER_ID: Final[int] = 1


# ---------------------------------------------------------------------------
# 라우터 정의
# ---------------------------------------------------------------------------
# prefix: "/api/archive" — 아카이브 도메인 단일 엔드포인트
# tags: Swagger UI 그룹 표기
router = APIRouter(prefix="/api/archive", tags=["archive"])


# ---------------------------------------------------------------------------
# 외부 연동 자리표시 더미
# ---------------------------------------------------------------------------
# 실제 Google Drive / Notion 연동은 후속 작업으로 분리되어 있다.
# 클라이언트는 이 더미를 받아 "준비 중" 카드로 노출하면 된다.
# 각 항목의 source_type/status 는 명세서 요구사항을 그대로 따른다.
# id/title 은 클라이언트 카드 컴포넌트가 동일 스키마로 렌더링할 수 있도록 함께 제공.
EXTERNAL_PLACEHOLDERS: Final[list[dict[str, Any]]] = [
    {
        "id": "google_drive_placeholder",
        "title": "Google Drive 연동 준비 중",
        "source_type": "google_drive",
        "status": "coming_soon",
    },
    {
        "id": "notion_placeholder",
        "title": "Notion 연동 준비 중",
        "source_type": "notion",
        "status": "coming_soon",
    },
]


# ---------------------------------------------------------------------------
# 응답 빌더 (공통 포맷 보장)
# ---------------------------------------------------------------------------
# `routers/documents.py` 의 동일 빌더와 별개로 본 모듈에서 자체 정의한다.
# documents.py 수정이 금지된 본 작업의 제약상 import 해서 재사용하면 결합이 생기므로,
# 라우터 단위로 가벼운 중복을 감수한다.
def _success_response(data: Any, status_code: int = status.HTTP_200_OK) -> JSONResponse:
    """`{"success": True, "data": data}` 형태의 JSONResponse 빌더."""
    return JSONResponse(
        status_code=status_code,
        content={"success": True, "data": data},
    )


def _error_response(message: str, code: str, status_code: int) -> JSONResponse:
    """`{"success": False, "data": {"code": code, "message": message}}` 빌더."""
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "data": {"code": code, "message": message},
        },
    )


# ---------------------------------------------------------------------------
# 그룹핑 헬퍼
# ---------------------------------------------------------------------------
def _group_local_documents_by_folder(rows: list[dict]) -> list[dict]:
    """
    documents 행 목록을 folder_id 별로 묶고, 각 그룹마다 카드 목록을 만든다.

    그룹 정렬 규칙:
        - folder_id IS NULL 그룹을 먼저(최상단) 노출 → "미분류" 영역 의도.
        - 그 외 folder_id 는 오름차순.

    그룹 내부 카드 정렬 규칙:
        - 호출자(repository)가 created_at DESC 로 이미 정렬해 주므로 그 순서 유지.

    Args:
        rows: documents 테이블의 행을 dict 로 변환한 리스트.
            각 row 는 최소 다음 컬럼을 가져야 함:
            id, title, source_type, created_at, updated_at, folder_id

    Returns:
        [
            {
                "folder_id": <int 또는 None>,
                "documents": [
                    {"id", "title", "source_type", "created_at", "updated_at"},
                    ...
                ]
            },
            ...
        ]
    """
    # 1) folder_id → 카드 리스트 누적용 dict
    # 키가 None 일 수 있으므로 일반 dict 사용 (Python 은 None 도 dict 키로 허용).
    groups: dict[Any, list[dict]] = {}

    for row in rows:
        folder_id = row.get("folder_id")
        # 명세 요구 카드 필드만 추려 노출.
        card = {
            "id": row["id"],
            "title": row["title"],
            "source_type": row["source_type"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        groups.setdefault(folder_id, []).append(card)

    # 2) folder_id 정렬:
    # None 그룹을 가장 앞에 두고, 그 외는 정수 오름차순.
    # 정렬 키 함수: None 이면 (-inf 대신) 0번째 키, 정수면 1번째 키로 분기.
    def _sort_key(folder_id) -> tuple[int, int]:
        if folder_id is None:
            # None 그룹은 가장 위
            return (0, 0)
        return (1, int(folder_id))

    sorted_folder_ids = sorted(groups.keys(), key=_sort_key)

    # 3) 최종 응답 형태로 매핑
    return [
        {
            "folder_id": folder_id,
            "documents": groups[folder_id],
        }
        for folder_id in sorted_folder_ids
    ]


# ---------------------------------------------------------------------------
# 엔드포인트: 아카이브 조회
# ---------------------------------------------------------------------------
@router.get("")
async def get_archive() -> JSONResponse:
    """
    데모 사용자의 활성 문서를 폴더별로 그룹핑한 local 영역과,
    외부 연동 자리표시(Google Drive/Notion) external 영역을 한 번에 반환한다.

    Returns:
        - 200 + {"success": true, "data": {"local": [...], "external": [...]}}: 정상
        - 500 + 명세 형식 에러: 예기치 못한 오류
    """
    # ---- 1) DB 조회 ----
    # repository 가 deleted_at IS NULL 필터를 이미 적용해 활성 문서만 돌려준다.
    try:
        rows = document_repository.list_active_documents_by_user(
            user_id=DEMO_USER_ID
        )
    except Exception as exc:  # noqa: BLE001
        # 운영에서는 여기서 로깅. MVP 라 메시지만 회피적으로 노출.
        return _error_response(
            message=f"아카이브 조회 중 오류가 발생했습니다: {exc}",
            code="INTERNAL_SERVER_ERROR",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # ---- 2) folder_id 별로 그룹핑 ----
    local_groups = _group_local_documents_by_folder(rows)

    # ---- 3) 응답 ----
    # external 은 매 요청 동일한 더미 리스트를 깊은 복사 없이 그대로 노출.
    # 클라이언트 측 변형 위험은 없다(JSON 직렬화 후 전달이라 원본 보호).
    return _success_response(
        {
            "local": local_groups,
            "external": EXTERNAL_PLACEHOLDERS,
        },
        status_code=status.HTTP_200_OK,
    )
