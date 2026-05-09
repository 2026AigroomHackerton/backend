# [諛깆뿏?? ?대떦] ?섏젙 ?덉슜 ?뚯씪 - feature/backend-ocr-voice-storage 釉뚮옖移?
"""External Storage Service.

[梨낆엫]
    ?몃? ??μ냼(Google Drive, Notion) ? 濡쒖뺄/?섑뵆 ?꾪룷???꾨찓?몄쓽 鍮꾩쫰?덉뒪 濡쒖쭅 怨꾩링.

[??PR 踰붿쐞 (?댁빱??MVP)]
    - 濡쒖뺄 ?뚯씪 媛?몄삤湲?: `save_uploaded_file()` ?쇰줈 ?ㅼ젣 ?붿뒪?????
    - ?섑뵆 臾몄꽌 ?꾪룷??  : `import_sample_document()` ?쇰줈 ?섑뵆 ?띿뒪?????ㅼ젣 DB INSERT.
    - Google Drive       : stub (OAuth 誘멸뎄??. ?쇱슦?곗뿉??HTTP 501 ?묐떟 泥섎━.
    - Notion             : stub (Integration Token 誘몄꽕??. ?쇱슦?곗뿉??HTTP 501.

[DB ?곕룞 ?뺤콉 ??湲곕뒫 4]
    documents / document_texts 紐⑤뜽? "?덈? ?섏젙 湲덉?" ?곸뿭?대씪 ORM ?대옒?ㅻ?
    ?좎뼵?섏? ?딅뒗?? ???SQLAlchemy `text()` 濡?raw SQL INSERT 瑜??섑뻾?쒕떎.
    紐낆꽭???뺥빐吏?而щ읆/媛?
        documents       : title, source_type='mock', file_type='txt',
                          parse_status='done', owner_type='user', owner_id=1,
                          created_at=datetime.utcnow().isoformat()
        document_texts  : document_id, extracted_text, text_version=1,
                          updated_at=datetime.utcnow().isoformat()
    INSERT 媛 ?ㅽ뙣(?뚯씠釉??놁쓬/而щ읆 遺덉씪移??? ?섎㈃ imported_document_id 瑜?
    None ?쇰줈 ?먭퀬 ?묐떟? 洹몃?濡?諛섑솚 (?쒕쾭 ?ㅼ슫 湲덉?).
"""

# ????뚰듃 吏???됯?. ?⑥닔 ?쒓렇?덉쿂???곕뒗 ??낆쓣 誘몃━ import ?섏? ?딆븘???섍쾶 ??以??
from __future__ import annotations

# 吏꾨떒??濡쒓굅. ?덉쇅 ?대갚 ??臾댁뾿???ㅽ뙣?덈뒗吏 ?④릿??
import json
import logging

# ?붾젆?곕━ ?먮룞 ?앹꽦???ъ슜 (os.makedirs).
import os
import urllib.error
import urllib.parse
import urllib.request

# created_at / updated_at ISO8601 臾몄옄???앹꽦??
from datetime import datetime

# ?묐떟/?대? ?먮즺援ъ“??媛???낆쓣 ?좎뿰?섍쾶 ?먭린 ?꾪븳 Any.
from typing import Any

# ?낅줈?쒕맂 ?뚯씪 媛앹껜 ??multipart/form-data ???뚯씪 ?뚮씪誘명꽣瑜??ㅻ０ ???ъ슜.
from fastapi import UploadFile
from app.core import config as _config  # noqa: F401 - ensure .env is loaded
from app.services.hwpx_template_service import BACKEND_DIR, create_generated_hwpx

# 紐⑤뱢 ?⑥쐞 濡쒓굅. logger.warning(...) ?깆쑝濡??ъ슜.
logger = logging.getLogger(__name__)


class ExternalImportError(Exception):
    """Pass external import failures to the router as envelope errors."""

    def __init__(self, message: str, code: str = "EXTERNAL_IMPORT_FAILED") -> None:
        super().__init__(message)
        self.code = code


# =============================================================================
# StorageService
# =============================================================================
class StorageService:
    """?몃? ??μ냼/?꾪룷???듯빀 ?쒕퉬??

    ?쒓났 硫붿꽌??
        - get_providers()                    : 紐낆꽭 PROVIDERS ?뺤쟻 紐⑸줉 諛섑솚.
        - import_sample_document(...)        : ?섑뵆 臾몄꽌 ?띿뒪????DB INSERT (or TODO).
        - get_connectors_status()            : google_drive/notion ?곌껐 ?곹깭.
        - save_uploaded_file(file, dest_dir) : ?낅줈???뚯씪???붿뒪?ъ뿉 ???
    """

    # -------------------------------------------------------------------------
    # PROVIDERS ???대씪?댁뼵??Front) ???뺤쟻 紐⑸줉.
    # -------------------------------------------------------------------------
    # ??ぉ ?섎?:
    #   provider     : 肄붾뱶/?앸퀎????(?곷Ц snake_case)
    #   display_name : UI ?쒖떆紐?(?쒓뎅??媛??
    #   status       : "available" | "coming_soon"
    #                  - available  : 利됱떆 ?ъ슜 媛??(local, mock)
    #                  - coming_soon: OAuth/Integration 誘멸뎄?꾩씠??怨?吏???덉젙
    #   description  : ?ъ슜???붾㈃??蹂댁뿬 以???以??ㅻ챸
    # ?대옒???띿꽦?쇰줈 ?먮뒗 ?댁쑀:
    #   - ?몄뒪?댁뒪留덈떎 蹂듭궗???꾩슂 ?녿뒗 遺덈? 硫뷀??곗씠??
    #   - ?뚯뒪?몄뿉??StorageService.PROVIDERS 濡?吏곸젒 ?묎렐 媛??
    PROVIDERS: list[dict[str, Any]] = [
        {
            "provider": "google_drive",
            "display_name": "Google Drive",
            "status": "available" if os.getenv("GOOGLE_DRIVE_ACCESS_TOKEN") else "disconnected",
            "description": "Import the configured Google Drive file.",
        },
        {
            "provider": "notion",
            "display_name": "Notion",
            "status": "available" if os.getenv("NOTION_API_TOKEN") else "disconnected",
            "description": "Import the configured Notion page.",
        },
        {
            "provider": "local",
            "display_name": "濡쒖뺄 ?뚯씪",
            "status": "available",
            "description": "湲곌린?먯꽌 ?뚯씪??吏곸젒 ?낅줈?쒗빀?덈떎.",
        },
        {
            "provider": "mock",
            "display_name": "?섑뵆 臾몄꽌",
            "status": "available",
            "description": "?곕え???섑뵆 臾몄꽌瑜?遺덈윭?듬땲??",
        },
    ]

    # -------------------------------------------------------------------------
    # SAMPLE_DOCUMENTS ???섑뵆 ?꾪룷?몄슜 ?붾? ?띿뒪??
    # -------------------------------------------------------------------------
    # ??document_type) ???쒓뎅?댁씠硫? ?쇱슦?곌? 諛쏆? 媛믪쑝濡?洹몃?濡?lookup ?쒕떎.
    # ?ㅺ? ?놁쑝硫?"媛?뺥넻?좊Ц" ?쇰줈 ?대갚?쒕떎 (import_sample_document 李멸퀬).
    SAMPLE_DOCUMENTS: dict[str, dict[str, str]] = {
        "가정통신문": {
            "title": "2026학년도 5월 가정통신문",
            "text": (
                "2026학년도 5월 가정통신문\n\n"
                "안녕하십니까. 학부모님 가정에 건강과 평안을 기원합니다.\n\n"
                "이번 주 활동 안내\n"
                "- 활동명: 환경정화 활동\n"
                "- 일시: 2026년 5월 20일 오전 10시\n"
                "- 장소: 학교 주변 공원\n"
                "- 준비물: 편한 복장, 물병\n\n"
                "참가 여부를 5월 15일까지 담임 선생님께 알려주시기 바랍니다.\n\n"
                "담당 교사: 홍길동\n"
                "연락처: 010-1234-5678"
            ),
        },
        "Google Drive 샘플 문서": {
            "title": "Google Drive 가져온 안내문",
            "text": (
                "Google Drive 가져온 안내문\n\n"
                "외부 저장소에서 가져온 문서 예시입니다.\n"
                "HWPX 파일로 변환되어 편집기에서 바로 열 수 있습니다.\n\n"
                "- 출처: Google Drive\n"
                "- 상태: 가져오기 완료"
            ),
        },
        "Notion 샘플 문서": {
            "title": "Notion 회의록 샘플",
            "text": (
                "Notion 회의록 샘플\n\n"
                "Notion 페이지에서 가져온 문서 예시입니다.\n\n"
                "회의 안건\n"
                "1. 문서 촬영 흐름 점검\n"
                "2. AI 수정 승인 UX 확인\n"
                "3. 외부 저장소 연동 계획 정리"
            ),
        },
        "지원서": {
            "title": "프로그램 지원서",
            "text": "프로그램 지원서\n\n성명:\n연락처:\n이메일:\n지원 동기:\n자기소개:",
        },
        "회의록": {
            "title": "팀 회의록",
            "text": "팀 회의록\n\n일시: 2026년 5월 9일\n장소: 회의실 A\n참석자:\n\n안건:\n1.\n2.\n\n결정 사항:",
        },
    }

    # =========================================================================
    # Public: get_providers
    # =========================================================================
    def get_providers(self) -> list[dict[str, Any]]:
        """PROVIDERS ?뺤쟻 紐⑸줉??諛섑솚?쒕떎.

        ?몄텧遺(?쇱슦??????寃곌낵瑜?怨듯넻 ?묐떟 envelope ??`data.providers` ?꾨뱶???대뒗??
        諛섑솚媛믪? ?몄텧?먭? 蹂?뺥빐???대옒???곸닔媛 ?곹뼢??諛쏆? ?딅룄濡??뺤? 蹂듭궗蹂몄쓣 以??
        """
        # list(...) 濡???由ъ뒪?몃? 留뚮뱾??諛섑솚 ???몄텧?먭? append/pop ?대룄 PROVIDERS ?먯껜???덉쟾.
        return list(self.PROVIDERS)

    def _json_request(self, url: str, headers: dict[str, str], timeout: int = 20) -> dict[str, Any]:
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ExternalImportError(
                f"External API request failed. HTTP {exc.code}: {detail[:300]}",
                "EXTERNAL_API_ERROR",
            ) from exc
        except urllib.error.URLError as exc:
            raise ExternalImportError(
                f"External API is unreachable. {exc.reason}",
                "EXTERNAL_API_ERROR",
            ) from exc

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise ExternalImportError("External API response was not valid JSON.", "INVALID_EXTERNAL_RESPONSE") from exc

    def _download_text(self, url: str, headers: dict[str, str], timeout: int = 30) -> str:
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content_type = response.headers.get("content-type", "")
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ExternalImportError(
                f"?? ?? ????? ??????. HTTP {exc.code}: {detail[:300]}",
                "EXTERNAL_API_ERROR",
            ) from exc
        except urllib.error.URLError as exc:
            raise ExternalImportError(
                f"?? ?? ????? ??????. {exc.reason}",
                "EXTERNAL_API_ERROR",
            ) from exc

        charset = "utf-8"
        for part in content_type.split(";"):
            part = part.strip()
            if part.lower().startswith("charset="):
                charset = part.split("=", 1)[1].strip() or "utf-8"
        return raw.decode(charset, errors="replace").strip()

    def _plain_text_from_notion_rich_text(self, rich_text: list[dict[str, Any]] | None) -> str:
        return "".join(item.get("plain_text", "") for item in rich_text or []).strip()

    def _notion_block_text(self, block: dict[str, Any]) -> str:
        block_type = block.get("type")
        data = block.get(block_type, {}) if isinstance(block_type, str) else {}
        text = self._plain_text_from_notion_rich_text(data.get("rich_text"))
        if block_type == "to_do" and text:
            return f"[{'x' if data.get('checked') else ' '}] {text}"
        if block_type in {"bulleted_list_item", "numbered_list_item"} and text:
            return f"- {text}"
        return text

    def _fetch_notion_blocks(self, block_id: str, headers: dict[str, str], depth: int = 0) -> list[str]:
        if depth > 3:
            return []

        lines: list[str] = []
        cursor: str | None = None
        while True:
            query = {"page_size": "100"}
            if cursor:
                query["start_cursor"] = cursor
            url = (
                "https://api.notion.com/v1/blocks/"
                f"{urllib.parse.quote(block_id)}/children?{urllib.parse.urlencode(query)}"
            )
            payload = self._json_request(url, headers)
            for block in payload.get("results", []):
                line = self._notion_block_text(block)
                if line:
                    lines.append(line)
                if block.get("has_children"):
                    lines.extend(self._fetch_notion_blocks(block["id"], headers, depth + 1))
            if not payload.get("has_more"):
                break
            cursor = payload.get("next_cursor")
            if not cursor:
                break
        return lines

    def _extract_notion_title(self, page: dict[str, Any]) -> str:
        for prop in page.get("properties", {}).values():
            if prop.get("type") == "title":
                title = self._plain_text_from_notion_rich_text(prop.get("title"))
                if title:
                    return title
        return "Notion ??? ??"

    def _insert_imported_document(
        self,
        db: Any,
        title: str,
        text: str,
        source_type: str,
        external_id: str | None = None,
        mime_type: str | None = None,
        raw_meta: dict[str, Any] | None = None,
    ) -> str | None:
        if db is None:
            return None

        try:
            from sqlalchemy import text as _sql_text  # type: ignore
            import uuid as _uuid

            now_iso = datetime.utcnow().isoformat()
            hwpx_path = create_generated_hwpx(title, text, f'external_{source_type}_{_uuid.uuid4().hex[:12]}')
            stored = hwpx_path.name
            relative_hwpx_path = hwpx_path.relative_to(BACKEND_DIR).as_posix()
            file_size_bytes = hwpx_path.stat().st_size

            doc_result = db.execute(
                _sql_text(
                    "INSERT INTO documents ("
                    " user_id, original_filename, stored_filename, file_path,"
                    " file_extension, file_size, content_type,"
                    " title, source_type, file_type, parse_status,"
                    " owner_type, owner_id, created_at"
                    ") VALUES ("
                    " 1, :original_filename, :stored_filename, :file_path,"
                    " '.hwpx', :file_size, 'application/haansofthwpx',"
                    " :title, :source_type, 'hwpx', 'done',"
                    " 'user', 1, :created_at"
                    ")"
                ),
                {
                    "original_filename": f"{title}.hwpx",
                    "stored_filename": stored,
                    "file_path": relative_hwpx_path,
                    "file_size": file_size_bytes,
                    "title": title,
                    "source_type": source_type,
                    "created_at": now_iso,
                },
            )
            document_id = doc_result.lastrowid
            if document_id is None:
                raise ExternalImportError("?? ?? ?? ID? ???? ?????.", "DB_INSERT_FAILED")

            db.execute(
                _sql_text(
                    "INSERT INTO document_texts "
                    "(document_id, extracted_text, text_version, updated_at) "
                    "VALUES (:document_id, :extracted_text, 1, :updated_at)"
                ),
                {"document_id": document_id, "extracted_text": text, "updated_at": now_iso},
            )

            try:
                db.execute(
                    _sql_text(
                        "INSERT INTO external_documents "
                        "(provider, external_id, title, mime_type, imported_document_id, raw_meta_json, created_at) "
                        "VALUES (:provider, :external_id, :title, :mime_type, :document_id, :raw_meta_json, :created_at)"
                    ),
                    {
                        "provider": source_type,
                        "external_id": external_id,
                        "title": title,
                        "mime_type": mime_type,
                        "document_id": document_id,
                        "raw_meta_json": json.dumps(raw_meta or {}, ensure_ascii=False),
                        "created_at": now_iso,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("external_documents ?? ??: %s", exc)

            db.commit()
            return str(document_id)
        except ExternalImportError:
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            raise
        except Exception as exc:  # noqa: BLE001
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            logger.warning("?? ?? DB INSERT ??: %s", exc)
            raise ExternalImportError("Could not save imported document.", "DB_INSERT_FAILED") from exc

    async def import_external_document(self, provider: str, db: Any = None, external_id: str | None = None) -> dict[str, Any]:
        if provider == "google_drive":
            return self._import_google_drive_document(db=db, external_id=external_id)
        if provider == "notion":
            return self._import_notion_document(db=db, external_id=external_id)
        raise ExternalImportError("Unsupported external provider.", "INVALID_PROVIDER")

    def _import_google_drive_document(self, db: Any, external_id: str | None = None) -> dict[str, Any]:
        token = (os.getenv("GOOGLE_DRIVE_ACCESS_TOKEN") or "").strip().strip('"').strip("'")
        api_key = (os.getenv("GOOGLE_DRIVE_API_KEY") or "").strip().strip('"').strip("'")
        if token.startswith("AIza"):
            api_key = api_key or token
            token = ""
        file_id = external_id or os.getenv("GOOGLE_DRIVE_FILE_ID")
        if not token and not api_key:
            raise ExternalImportError(
                "GOOGLE_DRIVE_ACCESS_TOKEN or GOOGLE_DRIVE_API_KEY is not configured.",
                "MISSING_GOOGLE_CREDENTIAL",
            )
        if not file_id:
            raise ExternalImportError("GOOGLE_DRIVE_FILE_ID is not configured.", "MISSING_EXTERNAL_ID")

        headers = {"Authorization": f"Bearer {token}"} if token else {}
        auth_query = {"key": api_key} if api_key else {}
        meta_query = {
            "fields": "id,name,mimeType,modifiedTime,size",
            "supportsAllDrives": "true",
            **auth_query,
        }
        meta_url = (
            "https://www.googleapis.com/drive/v3/files/"
            f"{urllib.parse.quote(file_id)}?{urllib.parse.urlencode(meta_query)}"
        )
        meta = self._json_request(meta_url, headers)
        title = meta.get("name") or "Google Drive imported document"
        mime_type = meta.get("mimeType") or ""

        if mime_type.startswith("application/vnd.google-apps"):
            export_query = {"mimeType": "text/plain", **auth_query}
            download_url = (
                "https://www.googleapis.com/drive/v3/files/"
                f"{urllib.parse.quote(file_id)}/export?{urllib.parse.urlencode(export_query)}"
            )
        else:
            media_query = {"alt": "media", "supportsAllDrives": "true", **auth_query}
            download_url = (
                "https://www.googleapis.com/drive/v3/files/"
                f"{urllib.parse.quote(file_id)}?{urllib.parse.urlencode(media_query)}"
            )
        text = self._download_text(download_url, headers)
        if not text:
            raise ExternalImportError("No text could be extracted from the Google Drive document.", "EMPTY_EXTERNAL_DOCUMENT")

        imported_document_id = self._insert_imported_document(
            db=db,
            title=title,
            text=text,
            source_type="google_drive",
            external_id=file_id,
            mime_type=mime_type,
            raw_meta=meta,
        )
        return {
            "imported_document_id": imported_document_id,
            "title": title,
            "source_type": "google_drive",
            "extracted_text": text,
            "status": "imported",
        }

    def _import_notion_document(self, db: Any, external_id: str | None = None) -> dict[str, Any]:
        token = os.getenv("NOTION_API_TOKEN")
        page_id = external_id or os.getenv("NOTION_PAGE_ID")
        if not token:
            raise ExternalImportError("NOTION_API_TOKEN is not configured.", "MISSING_NOTION_TOKEN")
        if not page_id:
            raise ExternalImportError("NOTION_PAGE_ID is not configured.", "MISSING_EXTERNAL_ID")

        headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": os.getenv("NOTION_VERSION", "2022-06-28"),
        }
        page = self._json_request(f"https://api.notion.com/v1/pages/{urllib.parse.quote(page_id)}", headers)
        title = self._extract_notion_title(page)
        text = "\n".join(self._fetch_notion_blocks(page_id, headers)).strip()
        if not text:
            raise ExternalImportError("No text could be extracted from the Notion page.", "EMPTY_EXTERNAL_DOCUMENT")

        imported_document_id = self._insert_imported_document(
            db=db,
            title=title,
            text=text,
            source_type="notion",
            external_id=page_id,
            mime_type="notion/page",
            raw_meta={"page": page},
        )
        return {
            "imported_document_id": imported_document_id,
            "title": title,
            "source_type": "notion",
            "extracted_text": text,
            "status": "imported",
        }

    # =========================================================================
    # Public: import_sample_document
    # =========================================================================
    async def import_sample_document(
        self,
        document_type: str,
        db: Any = None,
    ) -> dict[str, Any]:
        """?섑뵆 臾몄꽌瑜??꾪룷?명븳??

        ?먮쫫:
            1) SAMPLE_DOCUMENTS ?먯꽌 document_type ?쇰줈 ?섑뵆 lookup.
               議댁옱?섏? ?딆쑝硫?"媛?뺥넻?좊Ц" ?쇰줈 ?대갚.
            2) db ?몄뀡???덉쑝硫?documents + document_texts ?뚯씠釉붿뿉 INSERT.
               (?꾩옱 PR ?먯꽌??紐⑤뜽???섏젙 湲덉? ?곸뿭?대씪 TODO 留??④?)
            3) db 媛 ?놁쑝硫?INSERT 瑜?嫄대꼫?곌퀬 imported_document_id 瑜?None ?쇰줈 ?붾떎.
            4) ?묐떟 dict 諛섑솚.

        Args:
            document_type : "媛?뺥넻?좊Ц" | "吏?먯꽌" | "?뚯쓽濡? ??
            db            : SQLAlchemy ?몄뀡 (?먮뒗 ?숇벑??DB handle). 誘몄＜????None.

        Returns:
            {imported_document_id, title, source_type, extracted_text, status}
        """
        # TODO: models.py???꾨옒 ?뚯씠釉?異붽? ?꾩슂 (??μ뿉寃??붿껌):
        # - ocr_sources: id, document_id, image_path, raw_text, cleaned_text, confidence, created_at
        # - voice_commands: id, document_id, transcript, input_type, audio_path, status, created_at
        # - documents: id, owner_type, owner_id, title, source_type, file_type, parse_status, created_at
        # - document_texts: id, document_id, extracted_text, text_version, updated_at
        # ???뚯씠釉붿씠 ORM 紐⑤뜽濡?異붽??섎㈃ raw SQL ????ㅼ쓬怨?媛숈씠 援먯껜:
        #     doc = Document(title=title, source_type="mock", file_type="txt",
        #                    parse_status="done", owner_type="user", owner_id=1)
        #     db.add(doc); db.commit(); db.refresh(doc)
        #     doc_text = DocumentText(document_id=doc.id, extracted_text=text,
        #                             text_version=1)
        #     db.add(doc_text); db.commit()
        #     imported_document_id = doc.id
        # ---- 1) ?섑뵆 lookup (?놁쑝硫?湲곕낯媛?"媛?뺥넻?좊Ц") ------------------------
        sample = self.SAMPLE_DOCUMENTS.get(document_type) or self.SAMPLE_DOCUMENTS["媛?뺥넻?좊Ц"]
        title: str = sample["title"]
        text: str = sample["text"]

        # imported_document_id: ?ㅼ젣 DB INSERT 媛 ?쇱뼱?ъ쓣 ?뚯쓽 PK. 湲곕낯 None.
        imported_document_id: Any = None

        # ---- 2) DB ?몄뀡???덉쑝硫??ㅼ젣 INSERT ------------------------------------
        if db is not None:
            try:
                # SQLAlchemy text() 濡?raw SQL ?ㅽ뻾.
                # ORM 紐⑤뜽 ???raw SQL ???곕뒗 ?댁쑀: 蹂??쒕퉬?ㅻ뒗 BE2 ?곸뿭?닿퀬
                # documents 紐⑤뜽 ?뺤쓽??BE1/?듯빀 ?④퀎?먯꽌 愿由ы븯誘濡?寃고빀????텣??
                from sqlalchemy import text as _sql_text  # type: ignore
                import uuid as _uuid  # stored_filename 異⑸룎 ?뚰뵾??

                # ISO8601 臾몄옄?대줈 ?쒓컙 ?쇨????좎?.
                now_iso = datetime.utcnow().isoformat()

                # ----- documents INSERT -----------------------------------------
                # documents ?뚯씠釉붿쓽 NOT NULL 而щ읆:
                #   user_id, original_filename, stored_filename, file_path,
                #   file_extension, file_size, created_at, source_type, parse_status
                # mock ?꾪룷?몃뒗 ?ㅼ젣 ?뚯씪???놁쑝誘濡??붾? 硫뷀??곗씠?곕? 梨꾩슫??
                # stored_filename ? UNIQUE ?쒖빟?대씪 uuid ?⑦렪?쇰줈 異⑸룎 ?뚰뵾.
                hwpx_path = create_generated_hwpx(title, text, f'external_{_uuid.uuid4().hex[:12]}')
                stored = hwpx_path.name
                relative_hwpx_path = hwpx_path.relative_to(BACKEND_DIR).as_posix()
                file_size_bytes = hwpx_path.stat().st_size

                doc_result = db.execute(
                    _sql_text(
                        "INSERT INTO documents ("
                        " user_id, original_filename, stored_filename, file_path,"
                        " file_extension, file_size, content_type,"
                        " title, source_type, file_type, parse_status,"
                        " owner_type, owner_id, created_at"
                        ") VALUES ("
                        " 1, :original_filename, :stored_filename, :file_path,"
                        " '.hwpx', :file_size, 'application/haansofthwpx',"
                        " :title, 'mock', 'hwpx', 'done',"
                        " 'user', 1, :created_at"
                        ")"
                    ),
                    {
                        "original_filename": f"{title}.hwpx",
                        "stored_filename": stored,
                        "file_path": relative_hwpx_path,
                        "file_size": file_size_bytes,
                        "title": title,
                        "created_at": now_iso,
                    },
                )
                # SQLAlchemy ??CursorResult ??lastrowid ?띿꽦???쒓났.
                imported_document_id = doc_result.lastrowid

                # ----- document_texts INSERT -------------------------------------
                # text_version=1 ? 紐낆꽭 怨좎젙媛? updated_at ? documents ? 媛숈? ?쒖젏.
                db.execute(
                    _sql_text(
                        "INSERT INTO document_texts "
                        "(document_id, extracted_text, text_version, updated_at) "
                        "VALUES (:document_id, :extracted_text, 1, :updated_at)"
                    ),
                    {
                        "document_id": imported_document_id,
                        "extracted_text": text,
                        "updated_at": now_iso,
                    },
                )

                # ??INSERT 媛 紐⑤몢 ?깃났???쒖젏????踰덈쭔 commit.
                # (?ㅽ뙣?섎㈃ except ?덉뿉??rollback 泥섎━.)
                db.commit()

            except Exception as exc:  # noqa: BLE001 ??INSERT ?ㅽ뙣?대룄 ?꾪룷???먯껜???묐떟
                # TODO: ??μ뿉寃?documents, document_texts ?뚯씠釉?紐⑤뜽 ?뺤씤 ?붿껌
                #   ?꾩옱 SQL ? 紐낆꽭???뺤쓽??而щ읆紐??쒖빟??媛?뺥븯怨??묒꽦?덉쑝??
                #   ?ㅼ젣 紐⑤뜽怨?而щ읆/??낆씠 ?ㅻ? ???덈떎. OperationalError 媛 ?⑤㈃
                #   蹂?except 媛 ?≪븘 rollback ??mock ?묐떟?쇰줈 ?대갚?쒕떎.
                logger.warning("?섑뵆 臾몄꽌 DB INSERT ?ㅽ뙣 ??mock ?대갚: %s", exc)
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001 ??rollback ?먯껜 ?ㅽ뙣??臾댁떆
                    pass
                imported_document_id = None
        else:
            # TODO: ??μ뿉寃?database.py ? get_db ?섏〈??異붽? ?붿껌.
            #   ?꾩옱 ?쇱슦?곌? get_db 瑜?import ?섏? 紐삵븯硫?db=None ?쇰줈 ?몄텧?쒕떎.
            #   ??寃쎌슦 ?꾪룷?몃뒗 "?깃났?쇰줈 ?묐떟?섎릺 PK ?놁쓬" ?뺤콉 (mock ?대갚).
            imported_document_id = None

        # ---- 3) ?묐떟 ?섏씠濡쒕뱶 ----------------------------------------------------
        # imported_document_id ??紐낆꽭??string ?대?濡?int ??str 蹂??(None ? ?좎?).
        return {
            "imported_document_id": (
                str(imported_document_id) if imported_document_id is not None else None
            ),
            "title": title,
            "source_type": "mock",
            "extracted_text": text,
            "status": "imported",
        }

    # =========================================================================
    # Public: get_connectors_status
    # =========================================================================
    def get_connectors_status(self) -> list[dict[str, Any]]:
        """OAuth/Integration 湲곕컲 ?몃? 而ㅻ꽖?곗쓽 ?곌껐 ?곹깭瑜?諛섑솚?쒕떎.

        紐낆꽭???꾩옱 ?④퀎?먯꽌??google_drive, notion 紐⑤몢 "disconnected" 濡?怨좎젙.
        ?ㅼ젣 ?곕룞 ?쒖뿉???좏겙 留뚮즺/由ы봽?덉떆 ?곹깭瑜?寃?ы빐 status 瑜??숈쟻?쇰줈 寃곗젙.

        Returns:
            [{provider, display_name, status, connected_at}, ...]
        """
        # connected_at ? ISO8601 datetime 臾몄옄???먮뒗 None.
        # ?꾩옱???곌껐???곸씠 ?놁쑝誘濡?None ?쇰줈 ?듭씪.
        return [
            {
                "provider": "google_drive",
                "display_name": "Google Drive",
                "status": "connected" if os.getenv("GOOGLE_DRIVE_ACCESS_TOKEN") else "disconnected",
                "connected_at": None,
            },
            {
                "provider": "notion",
                "display_name": "Notion",
                "status": "connected" if os.getenv("NOTION_API_TOKEN") else "disconnected",
                "connected_at": None,
            },
        ]

    # =========================================================================
    # Public: save_uploaded_file
    # =========================================================================
    async def save_uploaded_file(self, file: UploadFile, dest_dir: str) -> str:
        """?낅줈?쒕맂 ?뚯씪??dest_dir ????ν븯怨????寃쎈줈瑜?諛섑솚?쒕떎.

        - dest_dir ???놁쑝硫?os.makedirs 濡??먮룞 ?앹꽦 (exist_ok=True).
        - filename ??鍮꾩뼱 ?덉쓣 寃쎌슦 'uploaded_file' 濡??대갚.
        - bytes ?⑥쐞 ?곌린 (binary-safe).

        Args:
            file     : FastAPI UploadFile (multipart ?뚯씪).
            dest_dir : ????붾젆?곕━ (?곷?/?덈? 寃쎈줈 紐⑤몢 媛??.

        Returns:
            ??λ맂 ?뚯씪???꾩껜 寃쎈줈 臾몄옄??
        """
        # ---- 1) ????붾젆?곕━ 蹂댁옣 ----------------------------------------------
        # exist_ok=True : ?붾젆?곕━媛 ?대? ?덉뼱???먮윭 ?놁씠 ?듦낵.
        os.makedirs(dest_dir, exist_ok=True)

        # ---- 2) ????뚯씪紐?寃곗젙 -------------------------------------------------
        # ?대씪?댁뼵?멸? filename ??鍮꾩썙 蹂대궡??寃쎌슦(?? ?섎せ??multipart) ?鍮??대갚.
        filename = file.filename or "uploaded_file"
        # os.path.join ? OS 蹂?援щ텇?먮? ?뚯븘??泥섎━?쒕떎.
        dest_path = os.path.join(dest_dir, filename)

        # ---- 3) ?뚯씪 蹂몃Ц ?쎄퀬 ?붿뒪?ъ뿉 ?곌린 -------------------------------------
        # await 媛 ?꾩슂???댁쑀: UploadFile.read() ??鍮꾨룞湲?肄붾（??
        content = await file.read()
        # "wb" : 諛붿씠?덈━ ?곌린 紐⑤뱶. ?띿뒪?몃줈 ?대㈃ ?몄퐫???ㅻ쪟 諛쒖깮 媛??
        with open(dest_path, "wb") as f:
            f.write(content)

        # ---- 4) ???寃쎈줈 諛섑솚 ---------------------------------------------------
        return dest_path

