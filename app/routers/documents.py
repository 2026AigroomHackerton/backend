"""
臾몄꽌 愿??API ?쇱슦??

??紐⑤뱢? ?대씪?댁뼵??紐⑤컮???? 媛 ?몄텧?섎뒗 HTTP ?붾뱶?ъ씤?몄쓽 吏꾩엯?먯씠??
?ㅼ젣 鍮꾩쫰?덉뒪 濡쒖쭅(?뚯씪 ??Β텱B 湲곕줉 ??? `app.services.document_service` ???꾩엫?섍퀬,
蹂?紐⑤뱢? ?ㅼ쓬 梨낆엫留?吏꾨떎.
    - URL 寃쎈줈 諛?HTTP 硫붿꽌???뺤쓽
    - ?붿껌 ?뚮씪誘명꽣(?뚯씪 ?? ?섏떊
    - ?쒕퉬???몄텧 寃곌낵瑜?怨듯넻 ?묐떟 ?щ㎎?쇰줈 媛먯떥 諛섑솚
    - ?꾨찓???덉쇅瑜??곸젅??HTTP ?곹깭 肄붾뱶濡?蹂??

API 怨듯넻 洹쒖튃(紐낆꽭??湲곗?):
    - 紐⑤뱺 寃쎈줈??`/api` ?묐몢???ъ슜 ??蹂??쇱슦?곕뒗 `/api/documents` 源뚯? prefix 遺??
    - ?묐떟 蹂몃Ц? ??긽 `{"success": true/false, "data": ...}` 援ъ“
        - ?깃났 ??data: ?꾨찓??寃곌낵 (?? 臾몄꽌 硫뷀??곗씠??dict)
        - ?ㅽ뙣 ??data: {"code": "<?먮윭 肄붾뱶>", "message": "<?щ엺???쎈뒗 硫붿떆吏>"}
    - ?몄쬆? ?댁빱??MVP ?④퀎?먯꽌 ?앸왂, `user_id=1` ?곕え ?ъ슜??怨좎젙
"""

from __future__ import annotations

import uuid

from pathlib import Path
from fastapi import APIRouter, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.services import document_service
from app.services import openai_document_service
from app.services.hwpx_template_service import HwpxTemplateError, create_generated_hwpx
from app.services.document_service import (
    DocumentNotFoundError,
    EmptyFileError,
    FileTooLargeError,
    UnsupportedFileTypeError,
)

# ---------------------------------------------------------------------------
# ?곕え ?ъ슜??ID
# ---------------------------------------------------------------------------
# ?몄쬆/?몄뀡???꾩엯?섍린 ???꾩떆濡??ъ슜?섎뒗 怨좎젙 ?ъ슜???앸퀎??
# ?ㅼ꽌鍮꾩뒪 ?꾪솚 ???좏겙 ?붿퐫????二쇱엯?섎뒗 媛믪쑝濡?援먯껜???덉젙.
DEMO_USER_ID = 1

# ---------------------------------------------------------------------------
# ?쇱슦???뺤쓽
# ---------------------------------------------------------------------------
# prefix:
#     "/api/documents" ??紐낆꽭?쒖쓽 `/api` 怨듯넻 ?묐몢??+ 臾몄꽌 ?꾨찓??寃쎈줈
# tags:
#     ["documents"] ??Swagger UI ?먯꽌 ?숈씪 洹몃９?쇰줈 臾띠씠?꾨줉 ?쒓린
router = APIRouter(prefix="/api/documents", tags=["documents"])


# ---------------------------------------------------------------------------
# ?묐떟 鍮뚮뜑 (怨듯넻 ?щ㎎ 蹂댁옣)
# ---------------------------------------------------------------------------
# FastAPI ??湲곕낯 HTTPException ? 蹂몃Ц??`{"detail": "..."}` ?쇰줈 ?먮룞 吏곷젹?뷀븳??
# 洹몃윭??紐낆꽭?쒕뒗 紐⑤뱺 ?묐떟??`{"success": bool, "data": ...}` 援ъ“?ъ빞 ?쒕떎怨??뺥븳??
# ?곕씪???쇱슦???대??먯꽌 吏곸젒 JSONResponse 瑜?諛섑솚?섏뿬 ?щ㎎??媛뺤젣?쒕떎.
def _success_response(data, status_code: int = status.HTTP_200_OK) -> JSONResponse:
    """
    ?깃났 ?묐떟 鍮뚮뜑 ???듯빀 怨듯넻 envelope 4-key.

    怨듯넻 ?묐떟 ?ㅽ럺:
        {"success": bool, "data": dict|null, "message": str, "error": str|null}

    Args:
        data: ?묐떟 蹂몃Ц `data` ?꾨뱶???ㅼ뼱媛?媛? dict / list / None 紐⑤몢 ?덉슜.
        status_code: HTTP ?곹깭 肄붾뱶. 湲곕낯 200, 由ъ냼???앹꽦 ??201 ??
    """
    return JSONResponse(
        status_code=status_code,
        content={
            "success": True,
            "data": data,
            "message": "",
            "error": None,
        },
    )


def _error_response(message: str, code: str, status_code: int) -> JSONResponse:
    """
    ?먮윭 ?묐떟 鍮뚮뜑 ???듯빀 怨듯넻 envelope 4-key.

    ?댁쟾 ?뺤떇(`data` ?덉뿉 code/message ?⑦궧) ?먯꽌 ?듯빀 envelope ?쇰줈 ?댁쟾:
        {"success": False, "data": null, "message": <?ㅻ챸>, "error": <CODE>}

    Args:
        message: ?ъ슜??媛쒕컻?먯뿉寃?蹂댁씪 ?쒓뎅??硫붿떆吏.
        code: ?대씪?댁뼵??遺꾧린???곷Ц ?먮윭 肄붾뱶 (?? "UNSUPPORTED_FILE_TYPE").
        status_code: HTTP ?곹깭 肄붾뱶 (400, 413 ??.
    """
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "data": None,
            "message": message,
            "error": code,
        },
    )


# ---------------------------------------------------------------------------
# ?붾뱶?ъ씤?? 臾몄꽌 ?낅줈??
# ---------------------------------------------------------------------------

@router.post("/image-to-hwpx")
async def image_to_hwpx(file: UploadFile | None = File(default=None)):
    """Create a valid HWPX file from an uploaded image.

    Flow:
    image -> OpenAI Vision JSON(title/body) -> blank_template.hwpx ->
    Contents/section0.xml placeholder replacement -> validate -> FileResponse.
    """
    if file is None:
        return _error_response(
            message="image file is required.",
            code="MISSING_FILE",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        document = await openai_document_service.image_to_document_json(file)
        stem_source = Path(file.filename or document["title"] or "captured_document").stem
        output_path = create_generated_hwpx(
            title=document["title"],
            body=document["body"],
            filename_stem=stem_source,
        )
        return FileResponse(
            path=output_path,
            filename=output_path.name,
            media_type="application/haansofthwpx",
            headers={"X-Document-Title": document["title"].encode("utf-8").hex()},
        )
    except openai_document_service.OpenAiDocumentError as exc:
        return _error_response(
            message=str(exc),
            code="OPENAI_DOCUMENT_FAILED",
            status_code=status.HTTP_502_BAD_GATEWAY,
        )
    except HwpxTemplateError as exc:
        return _error_response(
            message=str(exc),
            code="HWPX_TEMPLATE_INVALID",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    except Exception as exc:  # noqa: BLE001
        return _error_response(
            message=f"failed to create HWPX from image: {exc}",
            code="IMAGE_TO_HWPX_FAILED",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
@router.post("/upload")
async def upload_document(
    file: UploadFile | None = File(default=None),
    # 紐낆꽭 [BE1 - Documents API] ??異붽? multipart ?꾨뱶.
    # ?꾨씫 媛??optional) ?대씪 湲곕낯媛믪쓣 None / 'upload' 濡??붾떎.
    title: str | None = Form(default=None, description="?ъ슜??吏??臾몄꽌 ?쒕ぉ"),
    source_type: str = Form(default="upload", description="異쒖쿂 ?좏삎 (upload/mock/...)"),
    folder_id: int | None = Form(default=None, description="?랁븷 ?대뜑 ID"),
    category: str | None = Form(default=None, description="(?덉빟) 移댄뀒怨좊━"),
) -> JSONResponse:
    """
    臾몄꽌 ?낅줈???붾뱶?ъ씤??

    紐낆꽭 multipart ?꾨뱶: file(?꾩닔), title?, source_type?, folder_id?, category?
    `category` ???꾩옱 documents ?뚯씠釉붿뿉 而щ읆???놁뼱 蹂?PR ?먯꽌??諛쏄린留??섍퀬 臾댁떆?쒕떎.
        TODO: documents ?뚯씠釉붿뿉 category 而щ읆 異붽? ??service ???꾨떖.

    ?묐떟: ?듯빀 envelope 4-key (success, data, message, error).
        ?깃났 ??data: ?앹꽦??臾몄꽌 硫뷀??곗씠??dict.
        ?ㅽ뙣 ??data=null, error=<CODE>, message=<?ㅻ챸>.
    """

    # ---- ?뚯씪 ?꾨씫 諛⑹뼱 ----
    # FastAPI 媛 ?먮룞 422 寃利??묐떟???꾩슦硫?紐낆꽭 ?뺤떇??源⑥?誘濡?
    # `default=None` ?쇰줈 諛쏆븘 吏곸젒 紐낆꽭 ?뺤떇?쇰줈 蹂?섑븳??
    if file is None:
        return _error_response(
            message="?뚯씪??泥⑤??섏? ?딆븯?듬땲?? multipart/form-data ??'file' ?꾨뱶瑜??뺤씤??二쇱꽭??",
            code="MISSING_FILE",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # ---- 鍮꾩쫰?덉뒪 濡쒖쭅 ?꾩엫 + ?꾨찓???덉쇅 蹂??----
    try:
        document = await document_service.upload_document(
            file=file,
            user_id=DEMO_USER_ID,
            title=title,
            source_type=source_type,
            folder_id=folder_id,
            category=category,
        )
    except UnsupportedFileTypeError as exc:
        # ?대씪?댁뼵?몄쓽 ?섎せ???낅젰 ??400 Bad Request
        return _error_response(
            message=str(exc),
            code="UNSUPPORTED_FILE_TYPE",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except EmptyFileError as exc:
        # 鍮??뚯씪???섎せ???낅젰?쇰줈 媛꾩＜ ??400 Bad Request
        return _error_response(
            message=str(exc),
            code="EMPTY_FILE",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except FileTooLargeError as exc:
        # ?ш린 珥덇낵??RFC 9110 ???섎???413 Payload Too Large 媛 ?곹빀.
        return _error_response(
            message=str(exc),
            code="FILE_TOO_LARGE",
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )
    except Exception as exc:  # noqa: BLE001
        # ?덇린移?紐삵븳 ?쒕쾭 ?ㅻ쪟??紐낆꽭 ?뺤떇?쇰줈 ?묐떟?댁빞 ?섎?濡?愿묐쾾??catch.
        # ?댁쁺 ?섍꼍?먯꽌???ш린??濡쒓퉭/紐⑤땲?곕쭅 ?꾧뎄 ?곕룞 ?꾩슂.
        return _error_response(
            message=f"?쒕쾭 ?대? ?ㅻ쪟媛 諛쒖깮?덉뒿?덈떎: {exc}",
            code="INTERNAL_SERVER_ERROR",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # ---- ?깃났 ?묐떟 ----
    # ??由ъ냼???앹꽦?대?濡?201 Created.
    return _success_response(document, status_code=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# ?붾뱶?ъ씤?? 臾몄꽌 紐⑸줉 議고쉶
# ---------------------------------------------------------------------------
@router.get("")
async def list_documents(
    folder_id: int | None = Query(None, description="?대뜑 ID ?꾪꽣"),
    category: str | None = Query(None, description="(?덉빟) 移댄뀒怨좊━ ?꾪꽣"),
    source_type: str | None = Query(None, description="異쒖쿂 ?좏삎 ?꾪꽣"),
) -> JSONResponse:
    """
    ?곕え ?ъ슜??`user_id=1`)???쒖꽦 臾몄꽌 紐⑸줉??諛섑솚?쒕떎.

    "?쒖꽦" = soft-delete ?섏? ?딆? (deleted_at IS NULL) 臾몄꽌.

    紐낆꽭 [BE1 - Documents API] 荑쇰━ ?뚮씪誘명꽣:
        folder_id?    : documents.folder_id ?쇱튂 ?꾪꽣.
        category?     : (?덉빟) ?꾩옱 schema ??而щ읆 ?놁쓬 ??諛쏄린留??섍퀬 臾댁떆. TODO.
        source_type?  : documents.source_type ?쇱튂 ?꾪꽣.

    ?묐떟 data ??媛?臾몄꽌蹂?dict ?ㅼ쓽 由ъ뒪??
    """
    try:
        documents = document_service.list_documents(
            user_id=DEMO_USER_ID,
            folder_id=folder_id,
            category=category,
            source_type=source_type,
        )
    except Exception as exc:  # noqa: BLE001
        return _error_response(
            message=f"臾몄꽌 紐⑸줉 議고쉶 以??ㅻ쪟媛 諛쒖깮?덉뒿?덈떎: {exc}",
            code="INTERNAL_SERVER_ERROR",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # 紐낆꽭??留욎떠 data 瑜?{documents: [...]} 濡?nest ?섏? ?딄퀬 list 吏곸젒 諛섑솚 ?좎?.
    # (???쇱슦?몄쓽 ?묐떟 ?먮즺?뺤씠 documents[] ?먯껜?대?濡?紐낆꽭 "data: documents[]" ? ?쇱튂.)
    return _success_response(documents, status_code=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# ?붾뱶?ъ씤?? ?⑥씪 臾몄꽌 議고쉶
# ---------------------------------------------------------------------------

SHARED_EXPORT_DIR = Path(document_service.BACKEND_DIR) / "shared_exports"


def _safe_download_name(filename: str | None) -> str:
    name = Path(filename or "shared-document.hwpx").name
    return name or "shared-document.hwpx"


@router.post("/share-link")
async def create_share_link(file: UploadFile | None = File(default=None)) -> JSONResponse:
    """Store a temporary exported document and return an API URL that can be shared."""
    if file is None:
        return _error_response(
            message="공유할 파일이 없습니다.",
            code="MISSING_FILE",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        content = await file.read()
        if not content:
            return _error_response(
                message="빈 파일은 공유할 수 없습니다.",
                code="EMPTY_FILE",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        SHARED_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        filename = _safe_download_name(file.filename)
        stored_path = SHARED_EXPORT_DIR / f"{token}_{filename}"
        stored_path.write_bytes(content)

        return _success_response(
            {
                "token": token,
                "filename": filename,
                "url": f"/api/documents/shared/{token}",
            },
            status_code=status.HTTP_201_CREATED,
        )
    except Exception as exc:  # noqa: BLE001
        return _error_response(
            message=f"공유 링크를 만들지 못했습니다: {exc}",
            code="SHARE_LINK_FAILED",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.get("/shared/{token}")
async def get_shared_export(token: str):
    """Download a temporary shared export by token."""
    try:
        if not token or not all(ch in "0123456789abcdef" for ch in token.lower()):
            return _error_response(
                message="공유 링크가 올바르지 않습니다.",
                code="INVALID_SHARE_TOKEN",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        matches = list(SHARED_EXPORT_DIR.glob(f"{token}_*"))
        if not matches:
            return _error_response(
                message="공유 파일을 찾을 수 없습니다.",
                code="SHARED_FILE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        path = matches[0]
        filename = path.name.split("_", 1)[1] if "_" in path.name else path.name
        return FileResponse(
            path=path,
            filename=filename,
            media_type="application/octet-stream",
        )
    except Exception as exc:  # noqa: BLE001
        return _error_response(
            message=f"공유 파일을 불러오지 못했습니다: {exc}",
            code="SHARED_FILE_LOAD_FAILED",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

@router.get("/{document_id}/file")
async def get_document_file(document_id: int):
    """Return the stored original file for the editor viewer."""
    try:
        info = document_service.get_document_file_info(
            document_id=document_id, user_id=DEMO_USER_ID
        )
        backend_dir = Path(document_service.BACKEND_DIR)
        file_path = backend_dir / info["file_path"]
        if not file_path.exists() or not file_path.is_file():
            return _error_response(
                message="??? ?? ??? ?? ? ????.",
                code="FILE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return FileResponse(
            path=file_path,
            filename=info["original_filename"] or f"document-{document_id}",
            media_type=info["content_type"],
        )
    except DocumentNotFoundError as exc:
        return _error_response(
            message=str(exc),
            code="NOT_FOUND",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except Exception as exc:  # noqa: BLE001
        return _error_response(
            message=f"?? ?? ?? ? ??? ??????: {exc}",
            code="INTERNAL_SERVER_ERROR",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.put("/{document_id}/file")
async def replace_document_file(document_id: int, file: UploadFile = File(...)):
    """Replace the stored HWPX file after AI editing."""
    try:
        result = await document_service.replace_document_file(
            document_id=document_id,
            user_id=DEMO_USER_ID,
            file=file,
        )
        return _success_response(result, status_code=status.HTTP_200_OK)
    except DocumentNotFoundError as exc:
        return _error_response(
            message=str(exc),
            code="NOT_FOUND",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except EmptyFileError as exc:
        return _error_response(
            message=str(exc),
            code="EMPTY_FILE",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:  # noqa: BLE001
        return _error_response(
            message=f"??? ?? ?? ??? ??????: {exc}",
            code="INTERNAL_SERVER_ERROR",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.get("/{document_id}")
async def get_document(document_id: int) -> JSONResponse:
    """
    ?곕え ?ъ슜?먯쓽 ?⑥씪 臾몄꽌瑜?議고쉶?쒕떎.

    document_texts ???대떦 臾몄꽌???띿뒪?멸? ?덉쑝硫?extracted_text 瑜??④퍡 諛섑솚?섍퀬,
    ?놁쑝硫?null 濡?梨꾩썙 紐낆꽭瑜?留뚯”?쒗궓??

    Args:
        document_id: 寃쎈줈 ?뚮씪誘명꽣. FastAPI 媛 int 濡??먮룞 罹먯뒪?낇븯硫??ㅽ뙣 ??422 媛 ?⑥?留?
            ?꾩옱 ?쇱슦?곗뿉??422 瑜?紐낆꽭 ?뺤떇?쇰줈 蹂?섑븯??濡쒖쭅? ?녿떎.
            (TODO: main.py ??RequestValidationError exception_handler ?깅줉 ?꾩슂)

    Returns:
        - 200 + {"success": true, "data": {...}}: ?뺤긽
        - 404 + {"success": false, "data": {"code":"DOCUMENT_NOT_FOUND", ...}}: 誘몄〈???뚯쑀沅?遺덉씪移???젣??
        - 500 + 紐낆꽭 ?뺤떇 ?먮윭: ?덇린移?紐삵븳 ?ㅻ쪟
    """
    try:
        document = document_service.get_document(
            document_id=document_id, user_id=DEMO_USER_ID
        )
    except DocumentNotFoundError as exc:
        return _error_response(
            message=str(exc),
            code="NOT_FOUND",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except Exception as exc:  # noqa: BLE001
        return _error_response(
            message=f"臾몄꽌 議고쉶 以??ㅻ쪟媛 諛쒖깮?덉뒿?덈떎: {exc}",
            code="INTERNAL_SERVER_ERROR",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return _success_response(document, status_code=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# ?붿껌 蹂몃Ц ?ㅽ궎留?(Pydantic)
# ---------------------------------------------------------------------------
# PUT /api/documents/{document_id}/text ???붿껌 蹂몃Ц 紐⑤뜽.
# Pydantic ???먮룞?쇰줈 JSON ?뚯떛쨌?꾨뱶 議댁옱쨌???寃利앹쓣 泥섎━?댁???
# ?ㅻ쭔 ?ㅽ뙣 ??FastAPI ??422 + {"detail": [...]} ?뺤떇??湲곕낯 ?묐떟??諛섑솚?섎?濡?
# 蹂??묐떟??紐낆꽭("{success, data}")? ?닿툔?섎뒗 ?먯? ?뚮젮吏??몃젅?대뱶?ㅽ봽.
# (TODO: main.py ??RequestValidationError ?몃뱾?щ? ?깅줉?섎㈃ 紐낆꽭 ?뺤떇?쇰줈 ?듭씪 媛??)
class UpdateDocumentTextRequest(BaseModel):
    """PUT /api/documents/{document_id}/text ?붿껌 諛붾뵒."""

    edited_text: str


class ReindexDocumentRequest(BaseModel):
    """POST /api/documents/{document_id}/reindex ?붿껌 諛붾뵒.

    紐낆꽭 [BE1 - Documents API] reindex ?붿껌: {force: boolean}.
    force=False 硫??대? ?띿뒪?멸? ?덉쓣 ???ъ쿂由щ? ?앸왂?섍퀬 ?꾩옱 ?곹깭瑜?諛섑솚,
    force=True 硫?媛뺤젣濡?text_version ??+1 ?섍퀬 updated_at ??媛깆떊??"?ъ씤?깆떛"
    ?④낵瑜??쒕??덉씠?섑븳??
    """

    force: bool = False


# ---------------------------------------------------------------------------
# ?붾뱶?ъ씤?? 臾몄꽌 ?띿뒪???섏젙 (踰꾩쟾 ?대젰 湲곕줉)
# ---------------------------------------------------------------------------
@router.put("/{document_id}/text")
async def update_document_text(
    document_id: int,
    body: UpdateDocumentTextRequest,
) -> JSONResponse:
    """
    臾몄꽌 蹂몃Ц ?띿뒪?몃? ?섏젙?쒕떎.

    ?붿껌:
        PUT /api/documents/{document_id}/text
        Body: {"edited_text": "?섏젙??臾몄꽌 ?띿뒪??}  ??UpdateDocumentTextRequest 濡?寃利?

    泥섎━:
        1) Pydantic ??蹂몃Ц??UpdateDocumentTextRequest ?몄뒪?댁뒪濡??먮룞 ?뚯떛쨌寃利?
        2) service.update_document_text ?몄텧 ??DB 4?④퀎 ?묒뾽 ?꾩엫
        3) 寃곌낵瑜?紐낆꽭 ?뺤떇?쇰줈 媛먯떥 ?묐떟

    ?묐떟 data:
        - document_text_id, version_id, version_no, updated_at

    ?곹깭 肄붾뱶:
        - 200: ?깃났
        - 422: Pydantic 蹂몃Ц 寃利??ㅽ뙣 (FastAPI 湲곕낯 ?묐떟 ??紐낆꽭 ?뺤떇 ?꾨떂)
        - 404: 臾몄꽌 ?놁쓬/?뚯쑀沅?遺덉씪移???젣??
        - 500: ?덇린移?紐삵븳 ?쒕쾭 ?ㅻ쪟
    """

    # ---- 鍮꾩쫰?덉뒪 濡쒖쭅 ?꾩엫 ----
    try:
        result = document_service.update_document_text(
            document_id=document_id,
            edited_text=body.edited_text,
            user_id=DEMO_USER_ID,
            created_by=DEMO_USER_ID,
        )
    except DocumentNotFoundError as exc:
        return _error_response(
            message=str(exc),
            code="NOT_FOUND",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except Exception as exc:  # noqa: BLE001
        return _error_response(
            message=f"臾몄꽌 ?띿뒪???섏젙 以??ㅻ쪟媛 諛쒖깮?덉뒿?덈떎: {exc}",
            code="INTERNAL_SERVER_ERROR",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # ---- ?깃났 ?묐떟 ----
    return _success_response(result, status_code=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# ?붾뱶?ъ씤?? 臾몄꽌 ?ъ씤?깆떛
# ---------------------------------------------------------------------------
@router.post("/{document_id}/reindex")
async def reindex_document(
    document_id: int,
    body: ReindexDocumentRequest,
) -> JSONResponse:
    """
    臾몄꽌 ?띿뒪???꾨뱶瑜??ъ씤?깆떛?쒕떎.

    紐낆꽭 [BE1 - Documents API] POST /api/documents/{id}/reindex:
        ?붿껌: {force: boolean}
        ?묐떟: data = {document_texts: <媛깆떊 ??dict>, fields: [...]}.

    ?숈옉:
        - force=False: document_texts 媛 ?덉쑝硫??꾩옱 ?곹깭 洹몃?濡?諛섑솚 (?ъ쿂由??앸왂).
        - force=True : document_texts.text_version ??+1 ?섍퀬 updated_at ??媛깆떊??
                       ?ъ씤?깆떛 ?④낵瑜??쒕??덉씠??(?ㅼ젣 OCR ?ъ떎?됱? 蹂?PR 踰붿쐞 ??.

    fields ??schema 遺?щ줈 鍮?諛곗뿴 諛섑솚 (TODO).
    """
    try:
        result = document_service.reindex_document(
            document_id=document_id,
            user_id=DEMO_USER_ID,
            force=body.force,
        )
    except DocumentNotFoundError as exc:
        return _error_response(
            message=str(exc),
            code="NOT_FOUND",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except Exception as exc:  # noqa: BLE001
        return _error_response(
            message=f"臾몄꽌 ?ъ씤?깆떛 以??ㅻ쪟媛 諛쒖깮?덉뒿?덈떎: {exc}",
            code="INTERNAL_SERVER_ERROR",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return _success_response(result, status_code=status.HTTP_200_OK)


