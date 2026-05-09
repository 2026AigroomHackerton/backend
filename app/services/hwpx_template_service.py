from __future__ import annotations

import logging
import re
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = BACKEND_DIR / "templates" / "blank_template.hwpx"
GENERATED_DIR = BACKEND_DIR / "generated_hwpx"

REQUIRED_HWPX_FILES = [
    "Contents/content.hpf",
    "Contents/section0.xml",
    "Contents/header.xml",
    "Contents/version.xml",
    "META-INF/manifest.xml",
    "mimetype",
]

BODY_PLACEHOLDER = "{{DOCUMENT_BODY}}"
TITLE_PLACEHOLDER = "{{DOCUMENT_TITLE}}"
BODY_PARAGRAPH_RE = re.compile(r"<hp:p\b(?:(?!</hp:p>).)*" + re.escape(BODY_PLACEHOLDER) + r"(?:(?!</hp:p>).)*</hp:p>", re.DOTALL)
LINESEG_RE = re.compile(r'(<hp:lineseg\b[^>]*\bvertpos=")(-?\d+)("[^>]*/>)')
PARAGRAPH_ID_RE = re.compile(r'(<hp:p\b[^>]*\bid=")([^" ]+)(")', re.DOTALL)


class HwpxTemplateError(ValueError):
    pass


def list_hwpx_paths(hwpx_path: str | Path) -> list[str]:
    with zipfile.ZipFile(hwpx_path, "r") as zf:
        return zf.namelist()


def validate_hwpx(hwpx_path: str | Path) -> bool:
    names = set(list_hwpx_paths(hwpx_path))
    missing = [name for name in REQUIRED_HWPX_FILES if name not in names]
    if missing:
        raise HwpxTemplateError(f"HWPX required files are missing: {missing}")
    return True


def rebuild_hwpx(src_dir: str | Path, output_hwpx: str | Path) -> None:
    src = Path(src_dir)
    output = Path(output_hwpx)
    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        mimetype = src / "mimetype"
        if mimetype.exists():
            zf.write(mimetype, "mimetype", compress_type=zipfile.ZIP_STORED)

        for file in src.rglob("*"):
            if not file.is_file() or file == mimetype:
                continue
            arcname = file.relative_to(src).as_posix()
            zf.write(file, arcname)


def _normalize_body_lines(body: str) -> list[str]:
    lines = []
    for raw_line in (body or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if line:
            lines.append(line)
    return lines or [""]


def _extract_base_vertpos(paragraph_xml: str) -> int:
    match = LINESEG_RE.search(paragraph_xml)
    if not match:
        return 3200
    try:
        return int(match.group(2))
    except ValueError:
        return 3200


def _set_paragraph_id(paragraph_xml: str, index: int) -> str:
    return PARAGRAPH_ID_RE.sub(lambda m: f"{m.group(1)}{2147483648 + index}{m.group(3)}", paragraph_xml, count=1)


def _set_vertpos(paragraph_xml: str, base_vertpos: int, index: int) -> str:
    # HWPUNIT line spacing. A conservative gap prevents HOP from painting text on top of itself.
    next_vertpos = base_vertpos + (index * 1800)
    return LINESEG_RE.sub(lambda m: f"{m.group(1)}{next_vertpos}{m.group(3)}", paragraph_xml)


def _build_body_paragraphs(template_paragraph: str, body: str) -> str:
    base_vertpos = _extract_base_vertpos(template_paragraph)
    paragraphs: list[str] = []
    for index, line in enumerate(_normalize_body_lines(body)):
        paragraph = template_paragraph.replace(BODY_PLACEHOLDER, escape(line))
        paragraph = _set_paragraph_id(paragraph, index)
        paragraph = _set_vertpos(paragraph, base_vertpos, index)
        paragraphs.append(paragraph)
    return "".join(paragraphs)


def _replace_placeholders(xml: str, title: str, body: str) -> str:
    if TITLE_PLACEHOLDER not in xml:
        raise HwpxTemplateError(
            "Template Contents/section0.xml is missing {{DOCUMENT_TITLE}} placeholder."
        )
    if BODY_PLACEHOLDER not in xml:
        raise HwpxTemplateError(
            "Template Contents/section0.xml is missing {{DOCUMENT_BODY}} placeholder."
        )

    body_match = BODY_PARAGRAPH_RE.search(xml)
    if not body_match:
        raise HwpxTemplateError(
            "Template Contents/section0.xml body placeholder must be inside an hp:p paragraph."
        )

    body_paragraphs = _build_body_paragraphs(body_match.group(0), body)
    next_xml = xml[: body_match.start()] + body_paragraphs + xml[body_match.end() :]
    return next_xml.replace(TITLE_PLACEHOLDER, escape(title or ""))


def create_hwpx_from_template(
    output_path: str | Path,
    title: str,
    body: str,
    template_path: str | Path = TEMPLATE_PATH,
) -> Path:
    template = Path(template_path)
    output = Path(output_path)

    if not template.exists():
        raise HwpxTemplateError(f"blank_template.hwpx was not found: {template}")

    validate_hwpx(template)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir_path = Path(temp_dir)

        with zipfile.ZipFile(template, "r") as zf:
            zf.extractall(temp_dir_path)

        section_path = temp_dir_path / "Contents" / "section0.xml"
        if not section_path.exists():
            raise HwpxTemplateError(
                "Template HWPX is invalid: Contents/section0.xml was not found."
            )

        xml = section_path.read_text(encoding="utf-8")
        section_path.write_text(_replace_placeholders(xml, title, body), encoding="utf-8")

        rebuild_hwpx(temp_dir_path, output)

    validate_hwpx(output)
    paths = list_hwpx_paths(output)
    logger.info("Generated HWPX paths: %s", paths)
    print("Generated HWPX paths:", paths)
    return output


def create_generated_hwpx(title: str, body: str, filename_stem: str) -> Path:
    safe_stem = "".join(
        ch if ch.isalnum() or ch in "._-" else "_" for ch in filename_stem
    ).strip("._-")
    if not safe_stem:
        safe_stem = "generated_document"
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    return create_hwpx_from_template(GENERATED_DIR / f"{safe_stem}.hwpx", title, body)

