# [백엔드2 담당] 수정 허용 파일 - feature/backend-ocr-voice-storage 브랜치
"""External Storage Service (Class-based, real integration capable).

[책임]
    Google Drive, Notion 등 외부 저장소 연동 비즈니스 로직 계층.
    각 provider 의 연결 상태(status) 를 동적으로 감지하고, 업로드/내보내기
    같은 실제 호출도 real path / mock fallback 양쪽으로 지원한다.

[real path]
    - Google Drive : googleapiclient + google-auth (서비스 계정 JSON).
    - Notion       : Notion REST API 직접 호출 (httpx 우선, 없으면 stdlib urllib).

[mock path — fallback]
    - 라이브러리 미설치, env 미설정, API 호출 실패 → 더미 응답 반환.
    - status 는 다음 3가지 중 하나로 응답한다:
        - "connected"    : real 호출이 가능한 상태 (lib + creds 모두 OK)
        - "disconnected" : 라이브러리는 있는데 credentials 가 없음
        - "coming_soon"  : 라이브러리 자체가 미설치 (진짜 "추후 지원" 의미)

[필요 환경변수 — .env 에 추가]
    # Google Drive (서비스 계정 JSON 경로 OR 인라인 JSON)
    GOOGLE_SERVICE_ACCOUNT_JSON=/abs/path/to/service-account.json
    GOOGLE_DRIVE_FOLDER_ID=optional-target-folder-id

    # Notion (https://www.notion.so/my-integrations 에서 발급)
    NOTION_API_TOKEN=secret_xxxxxxxx
    NOTION_PARENT_PAGE_ID=hyphenated-or-non-hyphenated-page-id

[필요 라이브러리 — requirements.txt 에 추가 (현 PR 범위 외)]
    google-api-python-client>=2.0.0
    google-auth>=2.0.0
    httpx>=0.27.0   # Notion 호출용. 미설치시 stdlib urllib 폴백.
"""

from __future__ import annotations

import json as _json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Mock 더미 데이터 (lib/credentials 미설정 시 응답에 사용)
# ─────────────────────────────────────────────────────────────────────
_MOCK_DRIVE_FILE_ID = "mock_drive_file_001"
_MOCK_NOTION_PAGE_ID = "mock_notion_page_001"

# Notion API 버전 (https://developers.notion.com/reference/versioning).
_NOTION_API_VERSION = "2022-06-28"
_NOTION_API_BASE = "https://api.notion.com/v1"


# =============================================================================
# Provider 가용성 감지 — 라이브러리 + credentials 동시 체크
# =============================================================================
def _detect_google_drive() -> tuple[str, str | None]:
    """Google Drive 의 (status, reason) 을 반환.

    status:
        - "coming_soon"  : googleapiclient / google-auth 미설치
        - "disconnected" : 라이브러리는 있는데 credentials env 미설정/파일 없음
        - "connected"    : 호출 가능한 상태
    """
    # 1) 라이브러리 import 시도 (lazy — 본 함수가 불릴 때만).
    try:
        import googleapiclient  # noqa: F401
        from google.oauth2 import service_account  # noqa: F401
    except ImportError as exc:
        return "coming_soon", f"library_missing: {exc.name or exc}"

    # 2) 서비스 계정 JSON 의 경로 또는 인라인 JSON 확인.
    creds_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    creds_inline = os.getenv("GOOGLE_SERVICE_ACCOUNT_INFO")
    if not (creds_path or creds_inline):
        return "disconnected", "credentials_missing"

    # 3) 파일 경로로 받은 경우 실제 존재 확인.
    if creds_path and not os.path.isfile(creds_path):
        return "disconnected", f"credentials_file_not_found: {creds_path}"

    return "connected", None


def _detect_notion() -> tuple[str, str | None]:
    """Notion 의 (status, reason) 을 반환.

    Notion 은 httpx 가 없어도 stdlib urllib 으로 호출 가능하므로
    "coming_soon" 으로 떨어지는 케이스가 거의 없다.
    """
    if not os.getenv("NOTION_API_TOKEN"):
        return "disconnected", "credentials_missing"
    return "connected", None


# =============================================================================
# StorageService
# =============================================================================
class StorageService:
    """외부 저장소 통합 서비스.

    제공 메서드:
        - list_providers()                       : provider 별 동적 status 목록.
        - upload_to_google_drive(name, content)  : GDrive 업로드 (real or mock).
        - export_to_notion(title, content)       : Notion 페이지 생성 (real or mock).
    """

    # =========================================================================
    # Public: list_providers — 동적 status
    # =========================================================================
    def list_providers(self) -> list[dict[str, Any]]:
        """provider 별 연결 상태 목록을 반환한다.

        각 항목 키:
            provider : "google_drive" | "notion"
            status   : "connected" | "disconnected" | "coming_soon"
            reason   : status != "connected" 일 때 진단 문자열 (선택)
        """
        gdrive_status, gdrive_reason = _detect_google_drive()
        notion_status, notion_reason = _detect_notion()

        items: list[dict[str, Any]] = [
            {"provider": "google_drive", "status": gdrive_status},
            {"provider": "notion", "status": notion_status},
        ]
        # 디버깅 편의를 위해 reason 이 있을 때만 노출.
        if gdrive_reason:
            items[0]["reason"] = gdrive_reason
        if notion_reason:
            items[1]["reason"] = notion_reason
        return items

    # =========================================================================
    # Public: Google Drive 업로드 (real or mock)
    # =========================================================================
    def upload_to_google_drive(
        self,
        file_name: str,
        content: bytes,
        mime_type: str = "text/plain",
    ) -> dict[str, Any]:
        """파일을 Google Drive 에 업로드한다.

        실제 라이브러리 + credentials 가 있으면 진짜 업로드, 그렇지 않으면 mock.
        Returns:
            {provider, file_id, file_name, size_bytes, _source[, _error]}
        """
        try:
            return self._upload_to_google_drive_real(file_name, content, mime_type)
        except Exception as exc:  # noqa: BLE001 — 폴백 대상
            logger.warning("Google Drive 업로드 실패 → mock 폴백: %s", exc)
            return {
                "provider": "google_drive",
                "file_id": _MOCK_DRIVE_FILE_ID,
                "file_name": file_name,
                "size_bytes": len(content),
                "_source": "mock_fallback",
                "_error": str(exc),
            }

    def _upload_to_google_drive_real(
        self,
        file_name: str,
        content: bytes,
        mime_type: str,
    ) -> dict[str, Any]:
        """실제 GDrive 업로드. 모든 실패 케이스(미설치/미설정/네트워크) 는
        예외를 발생시켜 상위에서 mock 폴백되도록 한다."""
        # lazy import — 라이브러리 없으면 ImportError → 폴백.
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaInMemoryUpload
        from google.oauth2 import service_account

        creds_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
        creds_inline = os.getenv("GOOGLE_SERVICE_ACCOUNT_INFO")
        scopes = ["https://www.googleapis.com/auth/drive.file"]

        # credentials 객체 생성: 파일 경로 우선, 인라인 JSON 차선.
        if creds_path:
            creds = service_account.Credentials.from_service_account_file(
                creds_path, scopes=scopes
            )
        elif creds_inline:
            info = _json.loads(creds_inline)
            creds = service_account.Credentials.from_service_account_info(
                info, scopes=scopes
            )
        else:
            raise RuntimeError(
                "Google Drive credentials missing "
                "(GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_INFO)"
            )

        # cache_discovery=False : 디스크 캐시 미사용 (운영 환경 권장).
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        media = MediaInMemoryUpload(content, mimetype=mime_type)

        body: dict[str, Any] = {"name": file_name}
        # 폴더 지정이 있으면 그 안에 업로드.
        folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
        if folder_id:
            body["parents"] = [folder_id]

        result = (
            service.files()
            .create(body=body, media_body=media, fields="id, name, size")
            .execute()
        )
        # size 는 응답에 항상 있는 게 아니라서 안전하게 fallback.
        size = result.get("size")
        return {
            "provider": "google_drive",
            "file_id": result.get("id"),
            "file_name": result.get("name"),
            "size_bytes": int(size) if size else len(content),
            "_source": "google_drive",
        }

    # =========================================================================
    # Public: Notion 페이지 생성 (real or mock)
    # =========================================================================
    def export_to_notion(
        self,
        page_title: str,
        content: str,
        parent_page_id: str | None = None,
    ) -> dict[str, Any]:
        """문서를 Notion 페이지로 내보낸다.

        Args:
            page_title     : 새 페이지 제목.
            content        : 본문 텍스트 (단일 paragraph 블록으로 등록).
            parent_page_id : 부모 페이지 ID. 누락 시 NOTION_PARENT_PAGE_ID env 사용.

        Returns:
            {provider, page_id, page_title, _source[, _error]}
        """
        try:
            return self._export_to_notion_real(page_title, content, parent_page_id)
        except Exception as exc:  # noqa: BLE001 — 폴백 대상
            logger.warning("Notion export 실패 → mock 폴백: %s", exc)
            return {
                "provider": "notion",
                "page_id": _MOCK_NOTION_PAGE_ID,
                "page_title": page_title,
                "_source": "mock_fallback",
                "_error": str(exc),
            }

    def _export_to_notion_real(
        self,
        page_title: str,
        content: str,
        parent_page_id: str | None,
    ) -> dict[str, Any]:
        """실제 Notion API 호출. httpx 우선, 없으면 stdlib urllib."""
        token = os.getenv("NOTION_API_TOKEN")
        parent = parent_page_id or os.getenv("NOTION_PARENT_PAGE_ID")
        if not token:
            raise RuntimeError("NOTION_API_TOKEN not set")
        if not parent:
            raise RuntimeError(
                "NOTION_PARENT_PAGE_ID not set (or pass parent_page_id arg)"
            )

        url = f"{_NOTION_API_BASE}/pages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": _NOTION_API_VERSION,
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "parent": {"page_id": parent},
            "properties": {
                "title": [{"text": {"content": page_title}}],
            },
            "children": [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {"type": "text", "text": {"content": content}}
                        ]
                    },
                }
            ],
        }

        # 1) httpx 가 있으면 그걸로 (더 견고한 timeout/에러 처리).
        try:
            import httpx  # type: ignore

            with httpx.Client(timeout=20.0) as cli:
                resp = cli.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except ImportError:
            # 2) httpx 없으면 stdlib urllib.
            import urllib.request as _ur
            import urllib.error as _ue

            req = _ur.Request(
                url,
                data=_json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with _ur.urlopen(req, timeout=20) as resp:
                    data = _json.loads(resp.read().decode("utf-8"))
            except _ue.HTTPError as exc:
                # Notion 의 에러 응답 본문을 그대로 전달해 디버깅 편의 ↑
                err_body = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Notion HTTP {exc.code}: {err_body}") from exc

        return {
            "provider": "notion",
            "page_id": data.get("id"),
            "page_title": page_title,
            "_source": "notion",
        }
