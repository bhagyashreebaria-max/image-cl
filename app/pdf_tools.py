from __future__ import annotations

from dataclasses import dataclass
import hashlib
import difflib
import json
import os
from html import escape
from html.parser import HTMLParser
from math import ceil
import posixpath
from io import BytesIO
import re
import shutil
import subprocess
import textwrap
import tempfile
import zipfile
from xml.etree import ElementTree
from pathlib import Path, PurePath
from typing import Sequence
from urllib.parse import unquote, urlsplit

import pymupdf as fitz
import pikepdf
from pptx import Presentation
from pptx.util import Inches
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.misc import PdfReadError
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import signers
from pyhanko.sign.fields import MDPPerm, SigFieldSpec
from pyhanko.sign.general import SigningError
from pyhanko.sign.validation import validate_pdf_signature
from pyhanko.sign.validation.errors import SignatureValidationError

MAX_UPLOAD_BYTES = 80 * 1024 * 1024
IMAGE_TYPES = {"png", "jpg", "jpeg", "bmp", "tif", "tiff"}
OCR_LANGUAGES = {"eng", "hin", "eng+hin"}
PAGE_LABEL_STYLES = {"": "No numbering", "D": "1, 2, 3", "R": "I, II, III", "r": "i, ii, iii", "A": "A, B, C", "a": "a, b, c"}
PAGE_SIZES = {
    "a4": (595.28, 841.89),
    "letter": (612, 792),
    "legal": (612, 1008),
}
MAX_DIFF_PIXELS = 6_000_000
MAX_HTML_BYTES = 10 * 1024 * 1024
MAX_OCR_PAGES = 100
MAX_SCAN_PAGES = 100
MAX_SCAN_PIXELS = 8_000_000
MAX_SLIDE_PAGES = 100
MAX_SLIDE_TOTAL_PIXELS = 40_000_000
MAX_RENDERED_PAGE_PIXELS = 8_000_000
MAX_RENDERED_TOTAL_PIXELS = 40_000_000
MAX_IMAGE_PAGES = 100
MAX_INPUT_FILES = 100
BATCH_TOOLS = {
    "compress", "compress-images", "crop-pages", "deduplicate", "delete-pages", "draw",
    "duplicate-pages", "edit-metadata", "encrypt", "extract", "flatten-forms",
    "grayscale", "headers-footers", "highlight", "insert-blank", "linearize",
    "page-labels", "page-numbers", "permissions", "redact", "remove-attachments",
    "remove-hidden", "remove-metadata", "repair", "reorder", "resize-pages",
    "reverse-pages", "rotate", "stamp", "watermark",
}
BATCH_OPTION_KEYS = {
    "compress": set(),
    "compress-images": {"dpi", "quality"},
    "crop-pages": {"margin"},
    "deduplicate": set(),
    "delete-pages": {"ranges"},
    "draw": {"x", "y", "width", "height"},
    "duplicate-pages": {"ranges"},
    "edit-metadata": {"title", "author", "subject", "keywords"},
    "encrypt": {"profile", "user_password", "owner_password"},
    "extract": {"ranges"},
    "flatten-forms": set(),
    "grayscale": {"dpi"},
    "headers-footers": {"header", "footer", "page_number"},
    "highlight": {"phrase"},
    "insert-blank": {"ranges"},
    "linearize": set(),
    "page-labels": {"prefix", "label_style", "first_number"},
    "page-numbers": {"first_number"},
    "permissions": {"profile", "user_password", "owner_password"},
    "redact": {"phrase"},
    "remove-attachments": set(),
    "remove-hidden": set(),
    "remove-metadata": set(),
    "repair": set(),
    "reorder": {"order"},
    "resize-pages": {"page_size"},
    "reverse-pages": set(),
    "rotate": {"rotation"},
    "stamp": {"watermark", "stamp_position"},
    "watermark": {"watermark"},
}


class ToolError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ToolResult:
    data: bytes
    filename: str
    media_type: str = "application/pdf"
    input_bytes: int = 0


def safe_stem(filename: str) -> str:
    stem = PurePath(filename).stem
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-_")
    return cleaned[:80] or "document"


def validate_pdf(data: bytes, filename: str) -> None:
    if not filename.lower().endswith(".pdf"):
        raise ToolError(f"{filename}: choose a file with a .pdf extension.")
    if b"%PDF-" not in data[:1024]:
        raise ToolError(f"{filename}: this file does not have a valid PDF header.")


def open_pdf(data: bytes, filename: str, password: str = "") -> fitz.Document:
    validate_pdf(data, filename)
    try:
        document = fitz.open(stream=data, filetype="pdf")
    except (fitz.FileDataError, fitz.EmptyFileError) as error:
        raise ToolError(f"{filename}: the PDF could not be opened. It may be damaged.") from error
    if document.is_encrypted and (not password or not document.authenticate(password)):
        document.close()
        raise ToolError(f"{filename}: this PDF is password-protected or the password is incorrect.")
    if document.page_count == 0:
        document.close()
        raise ToolError(f"{filename}: the PDF has no pages.")
    return document


def validate_rendered_pixels(document: fitz.Document, dpi: int, operation: str) -> None:
    total_pixels = 0
    for page in document:
        width = max(1, ceil(page.rect.width * dpi / 72))
        height = max(1, ceil(page.rect.height * dpi / 72))
        page_pixels = width * height
        if page_pixels > MAX_RENDERED_PAGE_PIXELS:
            raise ToolError(f"{operation} is limited to 8 million rendered pixels per page.")
        total_pixels += page_pixels
        if total_pixels > MAX_RENDERED_TOTAL_PIXELS:
            raise ToolError(f"{operation} is limited to 40 million rendered pixels per operation.")


def image_frame_pixels(data: bytes, filename: str) -> tuple[int, int]:
    try:
        with Image.open(BytesIO(data)) as image:
            frame_count = getattr(image, "n_frames", 1)
            if frame_count > MAX_IMAGE_PAGES:
                raise ToolError(f"Image conversion is limited to {MAX_IMAGE_PAGES} pages per operation.")
            total_pixels = 0
            for frame in range(frame_count):
                image.seek(frame)
                pixels = image.width * image.height
                if pixels > MAX_RENDERED_PAGE_PIXELS:
                    raise ToolError("Image conversion is limited to 8 million pixels per page.")
                total_pixels += pixels
                if total_pixels > MAX_RENDERED_TOTAL_PIXELS:
                    raise ToolError("Image conversion is limited to 40 million pixels per operation.")
            return frame_count, total_pixels
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise ToolError(f"{filename}: the image could not be decoded.") from error


def parse_page_groups(value: str, page_count: int) -> list[list[int]]:
    groups: list[list[int]] = []
    for raw_group in value.split(";"):
        group_text = raw_group.strip()
        if not group_text:
            raise ToolError("Enter page groups separated by semicolons, for example 1-3; 5; 7-8.")
        pages: list[int] = []
        for token in group_text.split(","):
            part = token.strip()
            match = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", part)
            if not match:
                raise ToolError(f"'{part}' is not a valid page number or range.")
            start = int(match.group(1))
            end = int(match.group(2) or start)
            if start < 1 or end < start or end > page_count:
                raise ToolError(f"Page range '{part}' must be between 1 and {page_count}.")
            pages.extend(range(start - 1, end))
        if len(set(pages)) != len(pages):
            raise ToolError(f"Page group '{group_text}' contains a page more than once.")
        groups.append(pages)
    return groups


def parse_page_order(value: str, page_count: int) -> list[int]:
    try:
        pages = [int(part.strip()) for part in value.split(",")]
    except ValueError as error:
        raise ToolError("Enter every page number once, separated by commas.") from error
    expected = set(range(1, page_count + 1))
    if len(pages) != page_count or set(pages) != expected:
        raise ToolError(f"Order must contain each page from 1 to {page_count} exactly once.")
    return [page - 1 for page in pages]


def merge_pdfs(files: Sequence[tuple[str, bytes]]) -> ToolResult:
    if len(files) < 2:
        raise ToolError("Choose at least two PDFs to merge.")
    output = fitz.open()
    total_bytes = sum(len(data) for _, data in files)
    try:
        for name, data in files:
            source = open_pdf(data, name)
            try:
                output.insert_pdf(source)
            finally:
                source.close()
        result = output.tobytes(garbage=3, deflate=True)
    finally:
        output.close()
    return ToolResult(result, "merged-document.pdf", input_bytes=total_bytes)


def split_pdf(filename: str, data: bytes, ranges: str) -> ToolResult:
    source = open_pdf(data, filename)
    try:
        groups = parse_page_groups(ranges, source.page_count)
        memory_file = BytesIO()
        stem = safe_stem(filename)
        with zipfile.ZipFile(memory_file, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for index, pages in enumerate(groups, start=1):
                part = fitz.open()
                try:
                    part.insert_pdf(source, from_page=pages[0], to_page=pages[0])
                    for page_index in pages[1:]:
                        single_page = fitz.open()
                        try:
                            single_page.insert_pdf(source, from_page=page_index, to_page=page_index)
                            part.insert_pdf(single_page)
                        finally:
                            single_page.close()
                    archive.writestr(f"{stem}-part-{index}.pdf", part.tobytes(garbage=3, deflate=True))
                finally:
                    part.close()
        return ToolResult(memory_file.getvalue(), f"{stem}-split.zip", "application/zip", len(data))
    finally:
        source.close()


def rotate_pdf(filename: str, data: bytes, degrees: int) -> ToolResult:
    if degrees not in {90, 180, 270}:
        raise ToolError("Choose a rotation of 90, 180, or 270 degrees.")
    document = open_pdf(data, filename)
    try:
        for page in document:
            page.set_rotation((page.rotation + degrees) % 360)
        result = document.tobytes(garbage=3, deflate=True)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-rotated.pdf", input_bytes=len(data))


def reorder_pdf(filename: str, data: bytes, order: str) -> ToolResult:
    document = open_pdf(data, filename)
    try:
        indexes = parse_page_order(order, document.page_count)
        document.select(indexes)
        result = document.tobytes(garbage=3, deflate=True)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-reordered.pdf", input_bytes=len(data))


def copy_pages(document: fitz.Document, page_indexes: Sequence[int]) -> bytes:
    output = fitz.open()
    try:
        for page_index in page_indexes:
            output.insert_pdf(document, from_page=page_index, to_page=page_index)
        return output.tobytes(garbage=3, deflate=True)
    finally:
        output.close()


def extract_pages_pdf(filename: str, data: bytes, ranges: str) -> ToolResult:
    document = open_pdf(data, filename)
    try:
        groups = parse_page_groups(ranges, document.page_count)
        indexes = [page for group in groups for page in group]
        result = copy_pages(document, indexes)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-extracted.pdf", input_bytes=len(data))


def delete_pages_pdf(filename: str, data: bytes, ranges: str) -> ToolResult:
    document = open_pdf(data, filename)
    try:
        removed = {page for group in parse_page_groups(ranges, document.page_count) for page in group}
        remaining = [index for index in range(document.page_count) if index not in removed]
        if not remaining:
            raise ToolError("Deleting those pages would leave the PDF empty.")
        result = copy_pages(document, remaining)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-pages-removed.pdf", input_bytes=len(data))


def duplicate_pages_pdf(filename: str, data: bytes, ranges: str) -> ToolResult:
    document = open_pdf(data, filename)
    try:
        selected = {page for group in parse_page_groups(ranges, document.page_count) for page in group}
        indexes = []
        for index in range(document.page_count):
            indexes.append(index)
            if index in selected:
                indexes.append(index)
        result = copy_pages(document, indexes)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-pages-duplicated.pdf", input_bytes=len(data))


def reverse_pages_pdf(filename: str, data: bytes) -> ToolResult:
    document = open_pdf(data, filename)
    try:
        result = copy_pages(document, list(reversed(range(document.page_count))))
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-reversed.pdf", input_bytes=len(data))


def insert_blank_pages_pdf(filename: str, data: bytes, after_pages: str) -> ToolResult:
    document = open_pdf(data, filename)
    try:
        selected = {page for group in parse_page_groups(after_pages, document.page_count) for page in group}
        for page_index in sorted(selected, reverse=True):
            page = document.load_page(page_index)
            rect = page.rect
            document.new_page(pno=page_index + 1, width=rect.width, height=rect.height)
        result = document.tobytes(garbage=3, deflate=True)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-with-blank-pages.pdf", input_bytes=len(data))


def set_page_labels_pdf(
    filename: str,
    data: bytes,
    prefix: str,
    style: str,
    first_number: int,
) -> ToolResult:
    if style not in PAGE_LABEL_STYLES:
        raise ToolError("Choose a supported page-numbering style.")
    if first_number < 1 or first_number > 9999:
        raise ToolError("The first page number must be between 1 and 9999.")
    if len(prefix) > 24 or any(ord(char) < 32 for char in prefix):
        raise ToolError("Page-label prefixes must be 24 characters or fewer and contain no control characters.")
    document = open_pdf(data, filename)
    try:
        labels = []
        if prefix or style:
            labels.append({"startpage": 0, "prefix": prefix, "style": style, "firstpagenum": first_number})
            document.set_page_labels(labels)
        else:
            document.set_page_labels([])
        result = document.tobytes(garbage=3, deflate=True)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-labeled.pdf", input_bytes=len(data))


def crop_pages_pdf(filename: str, data: bytes, margin: float) -> ToolResult:
    if not 0 <= margin <= 500:
        raise ToolError("Enter a crop inset from 0 to 500 points.")
    document = open_pdf(data, filename)
    try:
        for page in document:
            box = page.cropbox
            if margin * 2 >= min(box.width, box.height):
                raise ToolError("The crop inset is too large for at least one page.")
            page.set_cropbox(fitz.Rect(box.x0 + margin, box.y0 + margin, box.x1 - margin, box.y1 - margin))
        result = document.tobytes(garbage=3, deflate=True)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-cropped.pdf", input_bytes=len(data))


def resize_pages_pdf(filename: str, data: bytes, page_size: str) -> ToolResult:
    if page_size not in PAGE_SIZES:
        raise ToolError("Choose A4, Letter, or Legal page size.")
    width, height = PAGE_SIZES[page_size]
    source = open_pdf(data, filename)
    output = fitz.open()
    try:
        for page in source:
            target = output.new_page(width=width, height=height)
            target.show_pdf_page(target.rect, source, page.number, keep_proportion=True)
        result = output.tobytes(garbage=3, deflate=True)
    finally:
        source.close()
        output.close()
    return ToolResult(result, f"{safe_stem(filename)}-{page_size}.pdf", input_bytes=len(data))


def images_to_pdf(files: Sequence[tuple[str, bytes]]) -> ToolResult:
    if not files:
        raise ToolError("Choose one or more images.")
    output = fitz.open()
    total_bytes = sum(len(data) for _, data in files)
    total_pages = 0
    total_pixels = 0
    try:
        for name, data in files:
            extension = PurePath(name).suffix.lower().lstrip(".")
            if extension not in IMAGE_TYPES:
                raise ToolError(f"{name}: use PNG, JPEG, BMP, or TIFF images.")
            frame_count, image_pixels = image_frame_pixels(data, name)
            total_pages += frame_count
            total_pixels += image_pixels
            if total_pages > MAX_IMAGE_PAGES:
                raise ToolError(f"Image conversion is limited to {MAX_IMAGE_PAGES} pages per operation.")
            if total_pixels > MAX_RENDERED_TOTAL_PIXELS:
                raise ToolError("Image conversion is limited to 40 million pixels per operation.")
            try:
                image = fitz.open(stream=data, filetype=extension)
                try:
                    converted = fitz.open(stream=image.convert_to_pdf(), filetype="pdf")
                    try:
                        output.insert_pdf(converted)
                    finally:
                        converted.close()
                finally:
                    image.close()
            except (fitz.FileDataError, fitz.EmptyFileError) as error:
                raise ToolError(f"{name}: the image could not be decoded.") from error
        result = output.tobytes(garbage=3, deflate=True)
    finally:
        output.close()
    return ToolResult(result, "images.pdf", input_bytes=total_bytes)


def compress_pdf(filename: str, data: bytes) -> ToolResult:
    document = open_pdf(data, filename)
    try:
        result = document.tobytes(
            garbage=4,
            clean=True,
            deflate=True,
            deflate_images=True,
            deflate_fonts=True,
            use_objstms=1,
        )
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-optimized.pdf", input_bytes=len(data))


def compress_images_pdf(filename: str, data: bytes, dpi: int, quality: int) -> ToolResult:
    if dpi not in {96, 120, 150, 200}:
        raise ToolError("Choose an image resolution of 96, 120, 150, or 200 DPI.")
    if quality not in {50, 60, 70, 80, 90}:
        raise ToolError("Choose an image quality of 50, 60, 70, 80, or 90.")
    document = open_pdf(data, filename)
    try:
        document.rewrite_images(
            dpi_threshold=dpi + 1,
            dpi_target=dpi,
            quality=quality,
            lossy=True,
            lossless=True,
        )
        result = document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-images-compressed.pdf", input_bytes=len(data))


def pdf_to_markdown(filename: str, data: bytes) -> ToolResult:
    document = open_pdf(data, filename)
    try:
        pages = [
            f"## Page {index + 1}\n\n{page.get_text('text').strip()}"
            for index, page in enumerate(document)
        ]
        result = ("\n\n".join(pages) + "\n").encode("utf-8")
    finally:
        document.close()
    return ToolResult(
        result,
        f"{safe_stem(filename)}.md",
        "text/markdown; charset=utf-8",
        len(data),
    )


def pdf_to_images(filename: str, data: bytes, dpi: int) -> ToolResult:
    if dpi not in {72, 120, 150, 200}:
        raise ToolError("Choose an image resolution of 72, 120, 150, or 200 DPI.")
    document = open_pdf(data, filename)
    archive_buffer = BytesIO()
    try:
        validate_rendered_pixels(document, dpi, "PDF-to-images")
        with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for page_number, page in enumerate(document, start=1):
                pixmap = page.get_pixmap(dpi=dpi, alpha=False)
                archive.writestr(f"{safe_stem(filename)}-page-{page_number:04}.png", pixmap.tobytes("png"))
    finally:
        document.close()
    return ToolResult(
        archive_buffer.getvalue(),
        f"{safe_stem(filename)}-pages.zip",
        "application/zip",
        len(data),
    )


def pdf_to_slides(filename: str, data: bytes) -> ToolResult:
    document = open_pdf(data, filename)
    if document.page_count > MAX_SLIDE_PAGES:
        document.close()
        raise ToolError(f"PDF-to-slides is limited to {MAX_SLIDE_PAGES} pages per operation.")
    presentation = Presentation()
    first_page = document[0]
    if first_page.rect.width > 56 * 72 or first_page.rect.height > 56 * 72:
        document.close()
        raise ToolError("A PDF page exceeds the 56-inch slide dimension limit.")
    presentation.slide_width = Inches(first_page.rect.width / 72)
    presentation.slide_height = Inches(first_page.rect.height / 72)
    blank_layout = presentation.slide_layouts[6]
    total_pixels = 0
    try:
        for page in document:
            width_px = page.rect.width / 72 * 96
            height_px = page.rect.height / 72 * 96
            if width_px * height_px > MAX_SCAN_PIXELS:
                raise ToolError("A PDF page exceeds the 8-million-pixel slide rendering limit.")
            total_pixels += width_px * height_px
            if total_pixels > MAX_SLIDE_TOTAL_PIXELS:
                raise ToolError("PDF-to-slides is limited to 40 million rendered pixels per operation.")
            image = page.get_pixmap(dpi=96, alpha=False).tobytes("png")
            slide = presentation.slides.add_slide(blank_layout)
            scale = min(
                presentation.slide_width / Inches(page.rect.width / 72),
                presentation.slide_height / Inches(page.rect.height / 72),
            )
            picture_width = int(Inches(page.rect.width / 72) * scale)
            picture_height = int(Inches(page.rect.height / 72) * scale)
            left = (presentation.slide_width - picture_width) // 2
            top = (presentation.slide_height - picture_height) // 2
            slide.shapes.add_picture(BytesIO(image), left, top, width=picture_width, height=picture_height)
        output = BytesIO()
        presentation.save(output)
    finally:
        document.close()
    return ToolResult(
        output.getvalue(),
        f"{safe_stem(filename)}.pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        len(data),
    )


def slides_to_pdf(filename: str, data: bytes) -> ToolResult:
    if PurePath(filename).suffix.lower() != ".pptx":
        raise ToolError("Choose a modern PowerPoint presentation with a .pptx extension.")
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > 4000 or sum(item.file_size for item in members) > MAX_UPLOAD_BYTES:
                raise ToolError("The PowerPoint file expands beyond the supported size limit.")
            if "ppt/presentation.xml" not in {item.filename for item in members}:
                raise ToolError(f"{filename}: this is not a valid PowerPoint presentation.")
        presentation = Presentation(BytesIO(data))
        if not presentation.slides:
            raise ToolError("The PowerPoint presentation has no slides.")
        if len(presentation.slides) > MAX_SLIDE_PAGES:
            raise ToolError(f"Slides-to-PDF is limited to {MAX_SLIDE_PAGES} slides per operation.")
        slide_text = []
        for index, slide in enumerate(presentation.slides, start=1):
            lines = [f"Slide {index}"]
            for shape in slide.shapes:
                if shape.has_text_frame:
                    text = shape.text.strip()
                    if text:
                        lines.extend(text.splitlines())
                if shape.has_table:
                    for row in shape.table.rows:
                        lines.append("    ".join(cell.text.strip() for cell in row.cells))
            slide_text.extend(lines)
            slide_text.append("")
    except ToolError:
        raise
    except (zipfile.BadZipFile, KeyError, ValueError, ElementTree.ParseError, RuntimeError) as error:
        raise ToolError(f"{filename}: the PowerPoint presentation could not be read.") from error
    content = "\n".join(slide_text).encode("utf-8")
    converted = text_to_pdf(f"{safe_stem(filename)}.txt", content)
    return ToolResult(converted.data, f"{safe_stem(filename)}.pdf", input_bytes=len(data))


def pdf_to_text(filename: str, data: bytes) -> ToolResult:
    document = open_pdf(data, filename)
    try:
        pages = [f"--- Page {index + 1} ---\n{page.get_text('text').strip()}" for index, page in enumerate(document)]
        result = ("\n\n".join(pages) + "\n").encode("utf-8")
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}.txt", "text/plain; charset=utf-8", len(data))


def accessible_text_export(filename: str, data: bytes) -> ToolResult:
    document = open_pdf(data, filename)
    try:
        pages = []
        for page_number, page in enumerate(document, start=1):
            blocks = [
                block[4].strip()
                for block in page.get_text("blocks", sort=True)
                if len(block) > 6 and block[6] == 0 and block[4].strip()
            ]
            pages.append(f"Page {page_number}\n" + "\n\n".join(blocks))
        content = "\n\n".join(pages) + "\n"
    finally:
        document.close()
    return ToolResult(
        content.encode("utf-8"),
        f"{safe_stem(filename)}-accessible.txt",
        "text/plain; charset=utf-8",
        len(data),
    )


def plain_text_from_markdown(text: str) -> str:
    lines = []
    in_fence = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("```") or line.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            lines.append("    " + raw_line)
            continue
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"^\s*[-+*]\s+", "• ", line)
        line = re.sub(r"^\s*\d+[.)]\s+", "• ", line)
        line = re.sub(r"^\s*>\s?", "", line)
        line = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", line)
        line = re.sub(r"(\*\*|__|\*|_|~~|`)", "", line)
        lines.append(line)
    return "\n".join(lines)


def text_to_pdf(filename: str, data: bytes, markdown: bool = False) -> ToolResult:
    extension = PurePath(filename).suffix.lower()
    allowed = {".md", ".markdown"} if markdown else {".txt"}
    if extension not in allowed:
        expected = "Markdown" if markdown else "text"
        raise ToolError(f"Choose a {expected} file ({', '.join(sorted(allowed))}).")
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ToolError(f"{filename}: use a UTF-8 encoded text file.") from error
    if markdown:
        content = plain_text_from_markdown(content)
    output = fitz.open()
    try:
        font = fitz.Font("helv")
        lines = []
        for paragraph in content.splitlines() or [""]:
            wrapped = textwrap.wrap(paragraph, width=92, break_long_words=True, replace_whitespace=False)
            lines.extend(wrapped or [""])
        lines_per_page = 48
        for offset in range(0, max(1, len(lines)), lines_per_page):
            page = output.new_page(width=595.28, height=841.89)
            chunk = lines[offset : offset + lines_per_page]
            page.insert_textbox(
                fitz.Rect(50, 50, 545, 790),
                "\n".join(chunk),
                fontname="helv",
                fontsize=10,
                lineheight=1.35,
                color=(0.12, 0.15, 0.2),
            )
        result = output.tobytes(garbage=3, deflate=True)
    finally:
        output.close()
    suffix = "markdown" if markdown else "text"
    return ToolResult(result, f"{safe_stem(filename)}-{suffix}.pdf", input_bytes=len(data))


class _HTMLTextParser(HTMLParser):
    BLOCK_TAGS = {
        "article", "blockquote", "br", "dd", "div", "dl", "dt", "footer", "h1", "h2",
        "h3", "h4", "h5", "h6", "header", "li", "main", "ol", "p", "section", "table",
        "tr", "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_tag: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.ignored_tag = tag
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.ignored_tag == tag:
            self.ignored_tag = None
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.ignored_tag is None:
            self.parts.append(data)


def html_to_pdf(filename: str, data: bytes) -> ToolResult:
    if PurePath(filename).suffix.lower() not in {".html", ".htm"}:
        raise ToolError("Choose an HTML file with an .html or .htm extension.")
    if len(data) > MAX_HTML_BYTES:
        raise ToolError("HTML input must be 10 MiB or smaller.")
    try:
        source = data.decode("utf-8-sig")
        parser = _HTMLTextParser()
        parser.feed(source)
        parser.close()
    except (UnicodeDecodeError, ValueError) as error:
        raise ToolError(f"{filename}: use a valid UTF-8 HTML file.") from error
    content = escape("".join(parser.parts).strip())
    story = fitz.Story(
        html=f'<html><body><pre style="font-family: sans-serif; white-space: pre-wrap">{content}</pre></body></html>'
    )
    buffer = BytesIO()
    writer = fitz.DocumentWriter(buffer)
    page_rect = fitz.Rect(0, 0, *PAGE_SIZES["a4"])
    content_rect = fitz.Rect(48, 48, page_rect.width - 48, page_rect.height - 48)

    def next_page(page_number: int, filled: fitz.Rect) -> tuple[fitz.Rect, fitz.Rect, fitz.Matrix]:
        return page_rect, content_rect, fitz.Identity

    try:
        story.write(writer, next_page)
    finally:
        writer.close()
    return ToolResult(buffer.getvalue(), f"{safe_stem(filename)}.pdf", input_bytes=len(data))


def epub_to_pdf(filename: str, data: bytes) -> ToolResult:
    if PurePath(filename).suffix.lower() != ".epub":
        raise ToolError("Choose an EPUB file.")
    if not data:
        raise ToolError("The EPUB file is empty.")
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > 2000 or sum(item.file_size for item in members) > MAX_UPLOAD_BYTES:
                raise ToolError("The EPUB expands beyond the supported size limit.")
            names = {item.filename for item in members}
            container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
            rootfile = container.find(".//{*}rootfile")
            package_path = rootfile.get("full-path", "") if rootfile is not None else ""
            if not package_path or package_path not in names:
                raise ToolError("The EPUB package manifest is missing.")
            package = ElementTree.fromstring(archive.read(package_path))
            package_dir = posixpath.dirname(package_path)
            manifest = {
                item.get("id"): item.get("href")
                for item in package.findall(".//{*}manifest/{*}item")
                if item.get("id") and item.get("href")
            }
            chapters = []
            for itemref in package.findall(".//{*}spine/{*}itemref"):
                href = manifest.get(itemref.get("idref"))
                parsed = urlsplit(href or "")
                member_path = posixpath.normpath(posixpath.join(package_dir, unquote(parsed.path)))
                if parsed.scheme or parsed.netloc or member_path.startswith("../") or member_path not in names:
                    raise ToolError("The EPUB contains an invalid chapter path.")
                parser = _HTMLTextParser()
                parser.feed(archive.read(member_path).decode("utf-8-sig"))
                parser.close()
                chapter = "".join(parser.parts).strip()
                if chapter:
                    chapters.append(chapter)
    except ToolError:
        raise
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, UnicodeDecodeError, RuntimeError) as error:
        raise ToolError(f"{filename}: the EPUB package could not be read.") from error
    if not chapters:
        raise ToolError("The EPUB does not contain readable chapters.")
    text = "\n\n".join(chapters).encode("utf-8")
    converted = text_to_pdf(f"{safe_stem(filename)}.txt", text)
    return ToolResult(converted.data, f"{safe_stem(filename)}.pdf", input_bytes=len(data))


def docx_text(data: bytes, filename: str) -> str:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > 2000 or sum(item.file_size for item in members) > MAX_UPLOAD_BYTES:
                raise ToolError("The Word document expands beyond the supported size limit.")
            root = ElementTree.fromstring(archive.read("word/document.xml"))
    except ToolError:
        raise
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, RuntimeError) as error:
        raise ToolError(f"{filename}: the Word document could not be read.") from error
    paragraphs = []
    for paragraph in (item for item in root.iter() if item.tag.endswith("}p")):
        text = "".join(
            "\t" if item.tag.endswith("}tab") else item.text or ""
            for item in paragraph.iter()
            if item.tag.endswith("}t") or item.tag.endswith("}tab")
        )
        if text.strip():
            paragraphs.append(text)
    if not paragraphs:
        raise ToolError(f"{filename}: no readable text was found.")
    return "\n".join(paragraphs)


def word_to_pdf(filename: str, data: bytes) -> ToolResult:
    if PurePath(filename).suffix.lower() != ".docx":
        raise ToolError("Choose a modern Word document with a .docx extension.")
    text = docx_text(data, filename)
    converted = text_to_pdf(f"{safe_stem(filename)}.txt", text.encode("utf-8"))
    return ToolResult(converted.data, f"{safe_stem(filename)}.pdf", input_bytes=len(data))


def pdf_to_word_docx(filename: str, data: bytes) -> ToolResult:
    document = open_pdf(data, filename)
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ElementTree.register_namespace("w", namespace)
    word = f"{{{namespace}}}"
    root = ElementTree.Element(f"{word}document")
    body = ElementTree.SubElement(root, f"{word}body")
    try:
        for page_index, page in enumerate(document):
            page_text = page.get_text("text").strip()
            for line in page_text.splitlines():
                paragraph = ElementTree.SubElement(body, f"{word}p")
                run = ElementTree.SubElement(paragraph, f"{word}r")
                text = ElementTree.SubElement(run, f"{word}t")
                text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                text.text = line
            if page_index < document.page_count - 1:
                paragraph = ElementTree.SubElement(body, f"{word}p")
                run = ElementTree.SubElement(paragraph, f"{word}r")
                ElementTree.SubElement(run, f"{word}br").set(f"{word}type", "page")
        ElementTree.SubElement(body, f"{word}sectPr")
        document_xml = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    finally:
        document.close()
    content_types = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        b'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        b'<Default Extension="xml" ContentType="application/xml"/>'
        b'<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        b"</Types>"
    )
    relationships = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        b"</Relationships>"
    )
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document_xml)
    return ToolResult(
        output.getvalue(),
        f"{safe_stem(filename)}.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        len(data),
    )


def pdf_to_excel_xlsx(filename: str, data: bytes) -> ToolResult:
    document = open_pdf(data, filename)
    spreadsheet_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    relationship_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package_relationship_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    content_types_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
    ElementTree.register_namespace("", spreadsheet_ns)
    ElementTree.register_namespace("r", relationship_ns)
    rows = []
    try:
        rows.append((0, "Page", "Extracted text"))
        for page_number, page in enumerate(document, start=1):
            for line in page.get_text("text").splitlines():
                if line.strip():
                    rows.append((page_number, str(page_number), line))
        if len(rows) == 1:
            raise ToolError("No text was found to export.")
    finally:
        document.close()
    word = f"{{{spreadsheet_ns}}}"
    sheet_data = ElementTree.Element(f"{word}worksheet")
    data_element = ElementTree.SubElement(sheet_data, f"{word}sheetData")
    for row_number, (page_number, page_value, text_value) in enumerate(rows, start=1):
        row_element = ElementTree.SubElement(data_element, f"{word}row", {"r": str(row_number)})
        for column, value in enumerate((page_value, text_value), start=1):
            reference = f"{'A' if column == 1 else 'B'}{row_number}"
            cell = ElementTree.SubElement(row_element, f"{word}c", {"r": reference, "t": "inlineStr"})
            inline = ElementTree.SubElement(cell, f"{word}is")
            text = ElementTree.SubElement(inline, f"{word}t")
            text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            text.text = value
    workbook = ElementTree.Element(f"{word}workbook")
    sheets = ElementTree.SubElement(workbook, f"{word}sheets")
    ElementTree.SubElement(sheets, f"{word}sheet", {
        "name": "Extracted text",
        "sheetId": "1",
        f"{{{relationship_ns}}}id": "rId1",
    })
    workbook_rels = ElementTree.Element(f"{{{package_relationship_ns}}}Relationships")
    ElementTree.SubElement(workbook_rels, f"{{{package_relationship_ns}}}Relationship", {
        "Id": "rId1",
        "Type": f"{relationship_ns}/worksheet",
        "Target": "worksheets/sheet1.xml",
    })
    root_rels = ElementTree.Element(f"{{{package_relationship_ns}}}Relationships")
    ElementTree.SubElement(root_rels, f"{{{package_relationship_ns}}}Relationship", {
        "Id": "rId1",
        "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
        "Target": "xl/workbook.xml",
    })
    content_types = ElementTree.Element(f"{{{content_types_ns}}}Types")
    ElementTree.SubElement(content_types, f"{{{content_types_ns}}}Default", {
        "Extension": "rels",
        "ContentType": "application/vnd.openxmlformats-package.relationships+xml",
    })
    ElementTree.SubElement(content_types, f"{{{content_types_ns}}}Default", {
        "Extension": "xml",
        "ContentType": "application/xml",
    })
    ElementTree.SubElement(content_types, f"{{{content_types_ns}}}Override", {
        "PartName": "/xl/workbook.xml",
        "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    })
    ElementTree.SubElement(content_types, f"{{{content_types_ns}}}Override", {
        "PartName": "/xl/worksheets/sheet1.xml",
        "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
    })
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", ElementTree.tostring(content_types, encoding="utf-8", xml_declaration=True))
        archive.writestr("_rels/.rels", ElementTree.tostring(root_rels, encoding="utf-8", xml_declaration=True))
        archive.writestr("xl/workbook.xml", ElementTree.tostring(workbook, encoding="utf-8", xml_declaration=True))
        archive.writestr("xl/_rels/workbook.xml.rels", ElementTree.tostring(workbook_rels, encoding="utf-8", xml_declaration=True))
        archive.writestr("xl/worksheets/sheet1.xml", ElementTree.tostring(sheet_data, encoding="utf-8", xml_declaration=True))
    return ToolResult(
        output.getvalue(),
        f"{safe_stem(filename)}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        len(data),
    )


def excel_to_pdf(filename: str, data: bytes) -> ToolResult:
    if PurePath(filename).suffix.lower() != ".xlsx":
        raise ToolError("Choose a modern Excel workbook with a .xlsx extension.")
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > 2000 or sum(item.file_size for item in members) > MAX_UPLOAD_BYTES:
                raise ToolError("The Excel workbook expands beyond the supported size limit.")
            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            sheet = next((item for item in workbook.iter() if item.tag.endswith("}sheet")), None)
            if sheet is None:
                raise ToolError("The Excel workbook has no worksheets.")
            relationship_id = next((value for key, value in sheet.attrib.items() if key.endswith("}id")), "")
            relation = next((item for item in relationships.iter() if item.get("Id") == relationship_id), None)
            target = relation.get("Target", "") if relation is not None else ""
            parsed = urlsplit(target)
            target_path = unquote(parsed.path).lstrip("/")
            sheet_path = posixpath.normpath(
                target_path if target_path.startswith("xl/") else posixpath.join("xl", target_path)
            )
            if parsed.scheme or parsed.netloc or sheet_path.startswith("../") or sheet_path not in {item.filename for item in members}:
                raise ToolError("The Excel worksheet path is invalid.")
            shared_strings = []
            if "xl/sharedStrings.xml" in {item.filename for item in members}:
                strings_root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                for entry in (item for item in strings_root.iter() if item.tag.endswith("}si")):
                    shared_strings.append("".join(child.text or "" for child in entry.iter() if child.tag.endswith("}t")))
            worksheet = ElementTree.fromstring(archive.read(sheet_path))
            rows = []
            for row in (item for item in worksheet.iter() if item.tag.endswith("}row")):
                values = []
                for cell in (item for item in row if item.tag.endswith("}c")):
                    kind = cell.get("t", "")
                    value_node = next((item for item in cell if item.tag.endswith("}v")), None)
                    value = value_node.text if value_node is not None else ""
                    if kind == "s" and value:
                        value = shared_strings[int(value)]
                    elif kind == "inlineStr":
                        value = "".join(child.text or "" for child in cell.iter() if child.tag.endswith("}t"))
                    values.append(value or "")
                if values:
                    rows.append("\t".join(values))
    except ToolError:
        raise
    except (zipfile.BadZipFile, KeyError, ValueError, IndexError, ElementTree.ParseError, RuntimeError) as error:
        raise ToolError(f"{filename}: the Excel workbook could not be read.") from error
    if not rows:
        raise ToolError("The first worksheet is empty.")
    converted = text_to_pdf(f"{safe_stem(filename)}.txt", "\n".join(rows).encode("utf-8"))
    return ToolResult(converted.data, f"{safe_stem(filename)}.pdf", input_bytes=len(data))


def linearize_pdf(filename: str, data: bytes) -> ToolResult:
    document = open_pdf(data, filename)
    try:
        source_bytes = document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()
    linearized = BytesIO()
    with pikepdf.open(BytesIO(source_bytes)) as pdf:
        pdf.save(linearized, linearize=True)
    result = linearized.getvalue()
    return ToolResult(result, f"{safe_stem(filename)}-linearized.pdf", input_bytes=len(data))


def validate_pdf_report(filename: str, data: bytes) -> ToolResult:
    document = open_pdf(data, filename)
    try:
        lines = [
            f"File: {safe_stem(filename)}.pdf",
            "Result: opened successfully by PyMuPDF.",
            f"Pages: {document.page_count}",
            f"PDF version: {document.metadata.get('format') or 'PDF'}",
            f"Encrypted: {'yes' if document.is_encrypted else 'no'}",
        ]
        for index, page in enumerate(document, start=1):
            rect = page.rect
            lines.append(f"Page {index}: {rect.width:.1f} x {rect.height:.1f} pt; rotation {page.rotation} degrees")
        return ToolResult(("\n".join(lines) + "\n").encode(), f"{safe_stem(filename)}-validation.txt", "text/plain; charset=utf-8", len(data))
    finally:
        document.close()


def pdfua_accessibility_report(filename: str, data: bytes) -> ToolResult:
    document = open_pdf(data, filename)
    try:
        catalog = document.xref_object(document.pdf_catalog())
        text_pages = sum(bool(page.get_text("text").strip()) for page in document)
        checks = [
            ("Document title", bool(document.metadata.get("title"))),
            ("Document language", bool(document.metadata.get("language"))),
            ("Tagged structure tree", "/StructTreeRoot" in catalog),
            ("Selectable text on every page", text_pages == document.page_count),
        ]
        lines = [
            f"File: {safe_stem(filename)}.pdf",
            "Scope: basic automated indicators only; this is not a PDF/UA conformance validation.",
            "",
        ]
        lines.extend(f"{'PASS' if passed else 'REVIEW'}: {label}" for label, passed in checks)
        lines.append("")
        lines.append(f"Pages with selectable text: {text_pages} / {document.page_count}")
        report = "\n".join(lines) + "\n"
    finally:
        document.close()
    return ToolResult(
        report.encode("utf-8"),
        f"{safe_stem(filename)}-accessibility-report.txt",
        "text/plain; charset=utf-8",
        len(data),
    )


def _pdfa_configuration() -> tuple[str | None, str | None, Path | None, str | None]:
    configured_ghostscript = os.environ.get("PAPERTRAIL_GHOSTSCRIPT", "").strip()
    if configured_ghostscript:
        ghostscript = str(Path(configured_ghostscript)) if Path(configured_ghostscript).is_file() else shutil.which(configured_ghostscript)
    else:
        ghostscript = next(
            (path for command in ("gswin64c", "gswin32c", "gs") if (path := shutil.which(command))),
            None,
        )
    configured_verapdf = os.environ.get("PAPERTRAIL_VERAPDF", "").strip()
    verapdf = shutil.which(configured_verapdf or "verapdf")
    profile_value = os.environ.get("PAPERTRAIL_PDFA_ICC_PROFILE", "").strip()
    profile = Path(profile_value).expanduser() if profile_value else None
    condition = os.environ.get("PAPERTRAIL_PDFA_OUTPUT_CONDITION", "").strip() or None
    if profile is not None and not profile.is_file():
        profile = None
    if profile is not None:
        try:
            with profile.open("rb") as stream:
                header = stream.read(128)
                profile_size = int.from_bytes(header[:4], "big") if len(header) >= 4 else 0
                if (
                    len(header) < 128
                    or profile_size < 128
                    or profile_size > profile.stat().st_size
                    or header[16:20] != b"RGB "
                    or header[36:40] != b"acsp"
                ):
                    profile = None
        except OSError:
            profile = None
    return ghostscript, verapdf, profile, condition


def pdfa_setup_error() -> str | None:
    ghostscript, verapdf, profile, condition = _pdfa_configuration()
    missing = []
    if not ghostscript:
        missing.append("Ghostscript")
    if not verapdf:
        missing.append("veraPDF")
    if profile is None:
        missing.append("a readable RGB ICC profile configured with PAPERTRAIL_PDFA_ICC_PROFILE")
    if not condition:
        missing.append("PAPERTRAIL_PDFA_OUTPUT_CONDITION")
    if missing:
        return "PDF/A-2b requires " + ", ".join(missing) + " on the processing server."
    return None


def pdfa_setup_available() -> bool:
    return pdfa_setup_error() is None


def _postscript_literal(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ToolError("The PDF/A output condition must be a single line.")
    return "(" + value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


def convert_pdfa(filename: str, data: bytes) -> ToolResult:
    setup_error = pdfa_setup_error()
    if setup_error:
        raise ToolError(setup_error, status_code=503)
    ghostscript, verapdf, profile, condition = _pdfa_configuration()
    if not ghostscript or not verapdf or profile is None or condition is None:
        raise ToolError("PDF/A conversion setup is incomplete on the processing server.", status_code=503)

    try:
        with tempfile.TemporaryDirectory(prefix="papertrail-pdfa-") as directory:
            work_dir = Path(directory)
            input_path = work_dir / "input.pdf"
            output_path = work_dir / "output.pdf"
            definition_path = work_dir / "PDFA_def.ps"
            input_path.write_bytes(data)
            profile_path = profile.resolve().as_posix()
            definition = (
                "%!\n"
                f"/ICCProfile {_postscript_literal(profile_path)} def\n"
                "[/_objdef {icc_PDFA} /type /stream /OBJ pdfmark\n"
                "[{icc_PDFA} << /N 3 >> /PUT pdfmark\n"
                "[{icc_PDFA} ICCProfile (r) file /PUT pdfmark\n"
                "[/_objdef {OutputIntent_PDFA} /type /dict /OBJ pdfmark\n"
                "[{OutputIntent_PDFA} << /Type /OutputIntent /S /GTS_PDFA1 "
                f"/DestOutputProfile {{icc_PDFA}} /OutputConditionIdentifier {_postscript_literal(condition)} "
                f"/Info {_postscript_literal(condition)} >> /PUT pdfmark\n"
                "[{Catalog} << /OutputIntents [ {OutputIntent_PDFA} ] >> /PUT pdfmark\n"
            )
            definition_path.write_text(definition, encoding="ascii")
            try:
                conversion = subprocess.run(
                    [
                        ghostscript,
                        "-dSAFER",
                        "-dBATCH",
                        "-dNOPAUSE",
                        "-dPDFA=2",
                        "-dPDFACompatibilityPolicy=2",
                        "-sColorConversionStrategy=RGB",
                        "-sDEVICE=pdfwrite",
                        f"-sOutputFile={output_path}",
                        f"--permit-file-read={profile.resolve()}",
                        str(definition_path),
                        str(input_path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                raise ToolError("Ghostscript took too long to convert this PDF to PDF/A-2b.", status_code=504) from error
            if conversion.returncode or not output_path.is_file():
                raise ToolError("Ghostscript could not convert this document to PDF/A-2b with the configured profile.")
            if output_path.stat().st_size > MAX_UPLOAD_BYTES:
                raise ToolError("The PDF/A output exceeded the 80 MiB server limit.", status_code=413)

            try:
                validation = subprocess.run(
                    [
                        verapdf,
                        "-f",
                        "2b",
                        "--format",
                        "xml",
                        "--maxfailures",
                        "100",
                        "--maxfailuresdisplayed",
                        "10",
                        str(output_path),
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                raise ToolError("veraPDF took too long to validate this document.", status_code=504) from error
            try:
                report = ElementTree.fromstring(validation.stdout)
            except ElementTree.ParseError as error:
                raise ToolError("veraPDF did not return a readable PDF/A validation report.") from error
            reports = report.findall(".//validationReport")
            if validation.returncode or len(reports) != 1 or reports[0].get("isCompliant") != "true":
                raise ToolError("The converted output did not pass veraPDF's PDF/A-2b conformance validation.")
            result = output_path.read_bytes()
    except OSError as error:
        raise ToolError(f"PDF/A conversion could not use its temporary processing files: {error}") from error
    return ToolResult(result, f"{safe_stem(filename)}-pdfa-2b.pdf", input_bytes=len(data))


def _page_structure_elements(pdf: pikepdf.Pdf) -> list[pikepdf.Object] | None:
    structure_root = pdf.Root.get("/StructTreeRoot")
    if not isinstance(structure_root, pikepdf.Dictionary):
        return None
    document_element = structure_root.get("/K")
    if not isinstance(document_element, pikepdf.Dictionary) or document_element.get("/S") != pikepdf.Name.Document:
        return None
    children = document_element.get("/K")
    if not isinstance(children, pikepdf.Array) or not children:
        return None
    if any(
        not isinstance(child, pikepdf.Dictionary)
        or child.get("/S") != pikepdf.Name.P
        or child.get("/K") != 0
        or child.get("/Pg") is None
        for child in children
    ):
        return None
    return list(children)


def reading_order_entry_count(filename: str, data: bytes) -> int:
    try:
        with pikepdf.Pdf.open(BytesIO(data)) as pdf:
            elements = _page_structure_elements(pdf)
            return len(elements) if elements is not None else 0
    except pikepdf.PdfError as error:
        raise ToolError("The PDF structure could not be inspected.") from error


def tag_pdf_structure(filename: str, data: bytes, language: str) -> ToolResult:
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", language):
        raise ToolError("Enter a valid document language tag such as en-US or hi.")
    document = open_pdf(data, filename)
    try:
        if not document.page_count:
            raise ToolError("The PDF has no pages to tag.")
        page_text = [page.get_text("text").strip() for page in document]
        if any(not text for text in page_text):
            raise ToolError("Basic tagging needs selectable text on every page. OCR scanned pages before tagging.")
    finally:
        document.close()

    try:
        with pikepdf.Pdf.open(BytesIO(data)) as pdf:
            if pdf.Root.get("/StructTreeRoot") is not None or any(
                page.obj.get("/StructParents") is not None for page in pdf.pages
            ):
                raise ToolError("This basic tagging tool does not replace existing document structure tags.")
            structure_root = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.StructTreeRoot))
            pdf.Root["/StructTreeRoot"] = structure_root
            document_element = pdf.make_indirect(pikepdf.Dictionary(
                Type=pikepdf.Name.StructElem,
                S=pikepdf.Name.Document,
                P=structure_root,
                K=pikepdf.Array(),
            ))
            structure_root["/K"] = document_element
            page_elements = pikepdf.Array()
            parent_numbers = pikepdf.Array()
            for index, (page, text) in enumerate(zip(pdf.pages, page_text, strict=True)):
                page_object = page.obj
                contents = page_object.get("/Contents")
                streams = list(contents) if isinstance(contents, pikepdf.Array) else [contents]
                content_bytes = b"\n".join(stream.read_bytes() for stream in streams if stream is not None)
                actual_text = text.encode("utf-16-be").hex().upper()
                marked_content = (
                    f"/P << /MCID 0 /ActualText <FEFF{actual_text}> >> BDC\n".encode("ascii")
                    + content_bytes
                    + b"\nEMC\n"
                )
                page_object["/Contents"] = pdf.make_stream(marked_content)
                page_object["/StructParents"] = index
                element = pdf.make_indirect(pikepdf.Dictionary(
                    Type=pikepdf.Name.StructElem,
                    S=pikepdf.Name.P,
                    P=document_element,
                    Pg=page_object,
                    K=0,
                ))
                page_elements.append(element)
                parent_numbers.extend([index, pikepdf.Array([element])])
            document_element["/K"] = page_elements
            structure_root["/ParentTree"] = pdf.make_indirect(pikepdf.Dictionary(Nums=parent_numbers))
            structure_root["/ParentTreeNextKey"] = len(page_elements)
            pdf.Root["/MarkInfo"] = pikepdf.Dictionary(Marked=True)
            pdf.Root["/Lang"] = pikepdf.String(language)
            output = BytesIO()
            pdf.save(output, min_version="1.7")
    except pikepdf.PdfError as error:
        raise ToolError("The PDF could not be given basic page-level structure tags.") from error
    return ToolResult(output.getvalue(), f"{safe_stem(filename)}-tagged.pdf", input_bytes=len(data))


def set_reading_order(filename: str, data: bytes, order: str) -> ToolResult:
    try:
        with pikepdf.Pdf.open(BytesIO(data)) as pdf:
            elements = _page_structure_elements(pdf)
            if elements is None:
                raise ToolError("Reading-order editing requires a PDF tagged by the basic page-level structure tool.")
            indexes = parse_page_order(order, len(elements))
            document_element = pdf.Root.StructTreeRoot.K
            document_element["/K"] = pikepdf.Array([elements[index] for index in indexes])
            output = BytesIO()
            pdf.save(output, min_version="1.7")
    except pikepdf.PdfError as error:
        raise ToolError("The PDF reading order could not be updated.") from error
    return ToolResult(output.getvalue(), f"{safe_stem(filename)}-reading-order.pdf", input_bytes=len(data))


def grayscale_pdf(filename: str, data: bytes, dpi: int) -> ToolResult:
    if dpi not in {72, 100, 120, 150}:
        raise ToolError("Choose a grayscale rendering resolution of 72, 100, 120, or 150 DPI.")
    source = open_pdf(data, filename)
    output = fitz.open()
    try:
        validate_rendered_pixels(source, dpi, "Grayscale conversion")
        for page in source:
            pixmap = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, alpha=False)
            target = output.new_page(width=page.rect.width, height=page.rect.height)
            target.insert_image(target.rect, pixmap=pixmap)
        result = output.tobytes(garbage=3, deflate=True)
    finally:
        source.close()
        output.close()
    return ToolResult(result, f"{safe_stem(filename)}-grayscale.pdf", input_bytes=len(data))


def estimate_skew_angle(image: Image.Image) -> float:
    preview = image.convert("L")
    preview.thumbnail((700, 900), Image.Resampling.LANCZOS)
    best_angle, best_score = 0.0, -1.0
    for step in range(-10, 11):
        angle = step * 0.5
        rotated = preview.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=255)
        rows = rotated.point(lambda pixel: 1 if pixel < 180 else 0).getprojection()[1]
        mean = sum(rows) / max(1, len(rows))
        score = sum((count - mean) ** 2 for count in rows)
        if score > best_score:
            best_angle, best_score = angle, score
    return best_angle


def scan_processing_pdf(filename: str, data: bytes, *, deskew: bool) -> ToolResult:
    source = open_pdf(data, filename)
    output = fitz.open()
    try:
        if source.page_count > MAX_SCAN_PAGES:
            raise ToolError(f"Scan processing is limited to {MAX_SCAN_PAGES} pages per operation.")
        operation = "Deskew" if deskew else "Scan enhancement"
        validate_rendered_pixels(source, 120, operation)
        for page in source:
            pixmap = page.get_pixmap(dpi=120, colorspace=fitz.csGRAY, alpha=False)
            image = Image.open(BytesIO(pixmap.tobytes("png"))).convert("L")
            if deskew:
                angle = estimate_skew_angle(image)
                if abs(angle) >= 0.5:
                    image = image.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=255)
            else:
                image = ImageOps.autocontrast(image)
                image = ImageEnhance.Contrast(image).enhance(1.12)
                image = image.filter(ImageFilter.UnsharpMask(radius=1.2, percent=120, threshold=3))
            image_bytes = BytesIO()
            image.save(image_bytes, format="PNG", optimize=True)
            target = output.new_page(width=page.rect.width, height=page.rect.height)
            target.insert_image(target.rect, stream=image_bytes.getvalue())
        result = output.tobytes(garbage=3, deflate=True)
    finally:
        source.close()
        output.close()
    suffix = "deskewed" if deskew else "enhanced"
    return ToolResult(result, f"{safe_stem(filename)}-{suffix}.pdf", input_bytes=len(data))


def deskew_pdf(filename: str, data: bytes) -> ToolResult:
    return scan_processing_pdf(filename, data, deskew=True)


def enhance_scan_pdf(filename: str, data: bytes) -> ToolResult:
    return scan_processing_pdf(filename, data, deskew=False)


def deduplicate_pages_pdf(filename: str, data: bytes) -> ToolResult:
    document = open_pdf(data, filename)
    try:
        validate_rendered_pixels(document, 72, "Duplicate-page detection")
        seen: set[tuple[int, int, str]] = set()
        unique_indexes = []
        for index, page in enumerate(document):
            pixmap = page.get_pixmap(dpi=72, alpha=False)
            fingerprint = (
                round(page.rect.width),
                round(page.rect.height),
                hashlib.sha256(pixmap.samples).hexdigest(),
            )
            if fingerprint not in seen:
                seen.add(fingerprint)
                unique_indexes.append(index)
        if not unique_indexes:
            raise ToolError("No pages were left after duplicate detection.")
        result = copy_pages(document, unique_indexes)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-deduplicated.pdf", input_bytes=len(data))


def watermark_pdf(filename: str, data: bytes, text: str) -> ToolResult:
    text = text.strip()
    if not text or len(text) > 100 or any(ord(char) < 32 for char in text):
        raise ToolError("Enter watermark text of 1–100 characters without control characters.")
    document = open_pdf(data, filename)
    try:
        for page in document:
            rect = page.rect
            box = fitz.Rect(rect.width * 0.1, rect.height * 0.44, rect.width * 0.9, rect.height * 0.56)
            page.insert_textbox(
                box,
                text,
                fontname="helv",
                fontsize=min(36, max(14, rect.width / max(len(text), 12))),
                color=(0.45, 0.49, 0.55),
                fill_opacity=0.28,
                align=fitz.TEXT_ALIGN_CENTER,
                overlay=True,
            )
        result = document.tobytes(garbage=3, deflate=True)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-watermarked.pdf", input_bytes=len(data))


def stamp_pdf(filename: str, data: bytes, text: str, position: str) -> ToolResult:
    text = text.strip()
    positions = {"top-left", "top-right", "bottom-left", "bottom-right", "center"}
    if not text or len(text) > 40 or any(ord(char) < 32 for char in text):
        raise ToolError("Enter stamp text of 1–40 characters without control characters.")
    if position not in positions:
        raise ToolError("Choose a supported stamp position.")
    document = open_pdf(data, filename)
    try:
        for page in document:
            rect = page.rect
            width = min(rect.width - 48, max(110, len(text) * 9))
            height = 30
            x = 24 if position.endswith("left") else rect.width - width - 24
            y = 24 if position.startswith("top") else rect.height - height - 24
            if position == "center":
                x = (rect.width - width) / 2
                y = (rect.height - height) / 2
            box = fitz.Rect(x, y, x + width, y + height)
            page.draw_rect(box, color=(0.7, 0.12, 0.16), fill=(1, 1, 1), fill_opacity=0.82, width=1.5, overlay=True)
            page.insert_textbox(
                box + (4, 4, -4, -4),
                text.upper(),
                fontsize=11,
                fontname="hebo",
                color=(0.7, 0.12, 0.16),
                align=fitz.TEXT_ALIGN_CENTER,
                overlay=True,
            )
        result = document.tobytes(garbage=3, deflate=True)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-stamped.pdf", input_bytes=len(data))


def add_headers_footers_pdf(
    filename: str,
    data: bytes,
    header: str,
    footer: str,
    include_page_number: bool,
) -> ToolResult:
    if len(header) > 120 or len(footer) > 120:
        raise ToolError("Header and footer text must be 120 characters or fewer.")
    if not header.strip() and not footer.strip() and not include_page_number:
        raise ToolError("Enter header or footer text, or enable page numbering.")
    document = open_pdf(data, filename)
    try:
        total = document.page_count
        for index, page in enumerate(document, start=1):
            rect = page.rect
            if header.strip():
                page.insert_text((36, 24), header, fontsize=9, color=(0.25, 0.3, 0.38))
            footer_text = footer.strip()
            if include_page_number:
                footer_text = f"{footer_text}    " if footer_text else ""
                footer_text += f"{index} / {total}"
            if footer_text:
                page.insert_textbox(
                    fitz.Rect(36, rect.height - 32, rect.width - 36, rect.height - 12),
                    footer_text,
                    fontsize=9,
                    color=(0.25, 0.3, 0.38),
                    align=fitz.TEXT_ALIGN_CENTER,
                )
        result = document.tobytes(garbage=3, deflate=True)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-header-footer.pdf", input_bytes=len(data))


def page_numbers_pdf(filename: str, data: bytes, start_number: int) -> ToolResult:
    if start_number < 1 or start_number > 9999:
        raise ToolError("Page numbering must start between 1 and 9999.")
    return _page_numbers_with_start(filename, data, start_number)


def _page_numbers_with_start(filename: str, data: bytes, start_number: int) -> ToolResult:
    document = open_pdf(data, filename)
    try:
        total = document.page_count
        for index, page in enumerate(document):
            rect = page.rect
            page.insert_textbox(
                fitz.Rect(36, rect.height - 32, rect.width - 36, rect.height - 12),
                f"{index + start_number} / {total + start_number - 1}",
                fontsize=9,
                color=(0.25, 0.3, 0.38),
                align=fitz.TEXT_ALIGN_CENTER,
            )
        result = document.tobytes(garbage=3, deflate=True)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-numbered.pdf", input_bytes=len(data))


def update_metadata_pdf(filename: str, data: bytes, metadata: dict[str, str]) -> ToolResult:
    allowed = {"title", "author", "subject", "keywords"}
    if any(key not in allowed for key in metadata):
        raise ToolError("Only title, author, subject, and keywords can be edited.")
    if any(len(value) > 500 for value in metadata.values()):
        raise ToolError("Metadata fields must be 500 characters or fewer.")
    document = open_pdf(data, filename)
    try:
        current = document.metadata
        for key in allowed:
            if key in metadata:
                current[key] = metadata[key]
        document.set_metadata(current)
        result = document.tobytes(garbage=3, deflate=True)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-metadata.pdf", input_bytes=len(data))


def remove_metadata_pdf(filename: str, data: bytes) -> ToolResult:
    document = open_pdf(data, filename)
    try:
        document.set_metadata({})
        document.set_xml_metadata("")
        result = document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-metadata-removed.pdf", input_bytes=len(data))


def remove_hidden_content_pdf(filename: str, data: bytes) -> ToolResult:
    document = open_pdf(data, filename)
    try:
        document.scrub(
            attached_files=True,
            clean_pages=True,
            embedded_files=True,
            hidden_text=True,
            javascript=True,
            metadata=True,
            redactions=True,
            remove_links=True,
            reset_fields=True,
            reset_responses=True,
            thumbnails=True,
            xml_metadata=True,
        )
        result = document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-sanitized.pdf", input_bytes=len(data))


def form_fields(document: fitz.Document) -> list[fitz.Widget]:
    return [
        widget
        for page in document
        for widget in (page.widgets() or ())
    ]


def parse_form_values(value: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in value.splitlines():
        if not line.strip():
            continue
        name, separator, field_value = line.partition("=")
        name = name.strip()
        if not separator or not name or len(name) > 200 or len(field_value) > 2000:
            raise ToolError("Enter form values as one field name=value pair per line (max 2000 characters per value).")
        if name in values:
            raise ToolError(f"Field '{name}' is listed more than once.")
        values[name] = field_value
    if not values:
        raise ToolError("Enter at least one PDF field name=value pair.")
    return values


def fill_form_pdf(filename: str, data: bytes, field_values: str) -> ToolResult:
    values = parse_form_values(field_values)
    document = open_pdf(data, filename)
    try:
        available_names = {widget.field_name for widget in form_fields(document)}
        missing = values.keys() - available_names
        if missing:
            raise ToolError(f"Unknown form field: {', '.join(sorted(missing))}.")
        supported_types = {
            fitz.PDF_WIDGET_TYPE_TEXT,
            fitz.PDF_WIDGET_TYPE_COMBOBOX,
            fitz.PDF_WIDGET_TYPE_LISTBOX,
            fitz.PDF_WIDGET_TYPE_CHECKBOX,
            fitz.PDF_WIDGET_TYPE_RADIOBUTTON,
        }
        for page in document:
            for widget in list(page.widgets() or ()):
                if widget.field_name not in values:
                    continue
                if widget.field_type not in supported_types:
                    raise ToolError(f"Form field '{widget.field_name}' uses an unsupported field type.")
                widget.field_value = values[widget.field_name]
                widget.update()
        result = document.tobytes(garbage=3, deflate=True)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-filled.pdf", input_bytes=len(data))


def flatten_forms_pdf(filename: str, data: bytes) -> ToolResult:
    document = open_pdf(data, filename)
    field_count = 0
    supported_types = {
        fitz.PDF_WIDGET_TYPE_TEXT,
        fitz.PDF_WIDGET_TYPE_COMBOBOX,
        fitz.PDF_WIDGET_TYPE_LISTBOX,
    }
    try:
        for page in document:
            for widget in list(page.widgets() or ()):
                if widget.field_type not in supported_types:
                    raise ToolError(
                        f"Cannot flatten '{widget.field_name}': only text and choice fields are supported."
                    )
                page.draw_rect(widget.rect, color=(0.45, 0.49, 0.55), fill=(1, 1, 1), width=0.5, overlay=True)
                page.insert_textbox(
                    widget.rect + (4, 3, -4, -3),
                    widget.field_value or "",
                    fontsize=10,
                    color=(0.12, 0.15, 0.2),
                    overlay=True,
                )
                page.delete_widget(widget)
                field_count += 1
        if field_count == 0:
            raise ToolError("This PDF has no supported form fields to flatten.")
        result = document.tobytes(garbage=3, deflate=True)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-forms-flattened.pdf", input_bytes=len(data))


def svg_to_pdf(filename: str, data: bytes) -> ToolResult:
    if PurePath(filename).suffix.lower() != ".svg":
        raise ToolError("Choose an SVG file.")
    try:
        image = fitz.open(stream=data, filetype="svg")
        try:
            result = image.convert_to_pdf()
        finally:
            image.close()
    except (fitz.FileDataError, fitz.EmptyFileError, RuntimeError) as error:
        raise ToolError(f"{filename}: the SVG could not be converted.") from error
    return ToolResult(result, f"{safe_stem(filename)}.pdf", input_bytes=len(data))


def repair_pdf(filename: str, data: bytes) -> ToolResult:
    validate_pdf(data, filename)
    repaired = BytesIO()
    try:
        with pikepdf.open(BytesIO(data), attempt_recovery=True) as document:
            document.save(repaired, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    except pikepdf.PasswordError as error:
        raise ToolError(f"{filename}: password-protected PDFs are not supported by repair.") from error
    except pikepdf.PdfError as error:
        raise ToolError(f"{filename}: the server PDF engine could not repair this file.") from error
    result = repaired.getvalue()
    validated = open_pdf(result, filename)
    validated.close()
    return ToolResult(result, f"{safe_stem(filename)}-repaired.pdf", input_bytes=len(data))


def compare_text_pdfs(files: Sequence[tuple[str, bytes]]) -> ToolResult:
    if len(files) != 2:
        raise ToolError("Choose exactly two PDFs to compare text.")
    documents = [open_pdf(data, name) for name, data in files]
    try:
        old_lines = []
        new_lines = []
        for page_number in range(max(doc.page_count for doc in documents)):
            old_lines.append(f"--- Page {page_number + 1} ---")
            new_lines.append(f"--- Page {page_number + 1} ---")
            if page_number < documents[0].page_count:
                old_lines.extend(documents[0][page_number].get_text("text").splitlines())
            if page_number < documents[1].page_count:
                new_lines.extend(documents[1][page_number].get_text("text").splitlines())
        diff = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=safe_stem(files[0][0]),
            tofile=safe_stem(files[1][0]),
            lineterm="",
        )
        report = "\n".join(diff)
        if not report:
            report = "The extracted text is identical.\n"
        total_size = sum(len(data) for _, data in files)
        return ToolResult(report.encode("utf-8"), "text-comparison.diff", "text/plain; charset=utf-8", total_size)
    finally:
        for document in documents:
            document.close()


def compare_page_renderings(files: Sequence[tuple[str, bytes]], visual_diff: bool) -> ToolResult:
    if len(files) != 2:
        raise ToolError("Choose exactly two PDFs to compare pages.")
    documents = [open_pdf(data, name) for name, data in files]
    output = fitz.open()
    changed_summary = []
    try:
        page_total = max(doc.page_count for doc in documents)
        total_pixels = 0
        for index in range(page_total):
            page_rects = [
                doc[index].rect if index < doc.page_count else fitz.Rect(0, 0, 612, 792)
                for doc in documents
            ]
            width = max(rect.width for rect in page_rects)
            height = max(rect.height for rect in page_rects)
            if width * height > MAX_DIFF_PIXELS:
                raise ToolError(
                    f"Page {index + 1} is too large for a visual comparison. Use the text comparison or split the document."
                )
            total_pixels += ceil(width) * ceil(height)
            if total_pixels > MAX_RENDERED_TOTAL_PIXELS:
                raise ToolError("Page comparison is limited to 40 million rendered pixels per operation.")
            if visual_diff:
                left_doc, right_doc = fitz.open(), fitz.open()
                try:
                    for target_doc, source_doc, rect in zip(
                        [left_doc, right_doc], documents, page_rects
                    ):
                        target = target_doc.new_page(width=width, height=height)
                        if index < source_doc.page_count:
                            target.show_pdf_page(target.rect, source_doc, index, keep_proportion=True)
                    pixmaps = [doc[0].get_pixmap(dpi=72, colorspace=fitz.csRGB, alpha=False) for doc in (left_doc, right_doc)]
                    if pixmaps[0].width != pixmaps[1].width or pixmaps[0].height != pixmaps[1].height:
                        raise ToolError("The pages could not be aligned for visual comparison.")
                    left_samples = memoryview(pixmaps[0].samples)
                    right_samples = memoryview(pixmaps[1].samples)
                    pixels = bytearray(len(left_samples))
                    changed = 0
                    for offset in range(0, len(pixels), 3):
                        if (
                            abs(left_samples[offset] - right_samples[offset]) > 24
                            or abs(left_samples[offset + 1] - right_samples[offset + 1]) > 24
                            or abs(left_samples[offset + 2] - right_samples[offset + 2]) > 24
                        ):
                            pixels[offset : offset + 3] = bytes((220, 48, 57))
                            changed += 1
                        else:
                            pixels[offset : offset + 3] = bytes((235, 238, 242))
                    diff_pixmap = fitz.Pixmap(
                        fitz.csRGB,
                        pixmaps[0].width,
                        pixmaps[0].height,
                        bytes(pixels),
                        False,
                    )
                    page = output.new_page(width=width, height=height + 38)
                    page.insert_text((24, 24), f"Page {index + 1}: {changed:,} changed pixels (72 DPI)", fontsize=12)
                    page.insert_image(fitz.Rect(0, 38, width, height + 38), pixmap=diff_pixmap)
                    changed_summary.append(f"Page {index + 1}: {changed:,} changed pixels")
                finally:
                    left_doc.close()
                    right_doc.close()
            else:
                page = output.new_page(width=width * 2, height=height + 30)
                page.insert_text((24, 20), f"Page {index + 1}", fontsize=12)
                for side, (document, rect) in enumerate(zip(documents, page_rects)):
                    if index < document.page_count:
                        target = fitz.Rect(side * width, 30, (side + 1) * width, height + 30)
                        page.show_pdf_page(target, document, index, keep_proportion=True)
        result = output.tobytes(garbage=3, deflate=True)
    finally:
        for document in documents:
            document.close()
        output.close()
    filename = "visual-difference.pdf" if visual_diff else "side-by-side-comparison.pdf"
    return ToolResult(result, filename, input_bytes=sum(len(data) for _, data in files))


def redact_text_pdf(filename: str, data: bytes, phrase: str) -> ToolResult:
    phrase = phrase.strip()
    if not phrase or len(phrase) > 200:
        raise ToolError("Enter text of 1–200 characters to redact.")
    document = open_pdf(data, filename)
    matches = 0
    try:
        for page in document:
            rectangles = page.search_for(phrase)
            matches += len(rectangles)
            for rect in rectangles:
                page.add_redact_annot(rect, fill=(0, 0, 0))
            if rectangles:
                page.apply_redactions()
        if not matches:
            raise ToolError("No matching text was found, so no redactions were applied.")
        result = document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-redacted.pdf", input_bytes=len(data))


def highlight_text_pdf(filename: str, data: bytes, phrase: str) -> ToolResult:
    phrase = phrase.strip()
    if not phrase or len(phrase) > 200:
        raise ToolError("Enter text of 1–200 characters to highlight.")
    document = open_pdf(data, filename)
    matches = 0
    try:
        for page in document:
            rectangles = page.search_for(phrase)
            matches += len(rectangles)
            for rect in rectangles:
                page.add_highlight_annot(rect)
        if not matches:
            raise ToolError("No matching text was found, so no highlights were added.")
        result = document.tobytes(garbage=3, deflate=True)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-highlighted.pdf", input_bytes=len(data))


def comment_pdf(filename: str, data: bytes, comment: str) -> ToolResult:
    comment = comment.strip()
    if not comment or len(comment) > 1000:
        raise ToolError("Enter a comment of 1–1000 characters.")
    document = open_pdf(data, filename)
    try:
        page = document[0]
        page.add_text_annot(fitz.Point(40, 40), comment)
        result = document.tobytes(garbage=3, deflate=True)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-commented.pdf", input_bytes=len(data))


def draw_rectangle_pdf(filename: str, data: bytes, x: float, y: float, width: float, height: float) -> ToolResult:
    if min(x, y) < 0 or min(width, height) <= 0 or max(x, y, width, height) > 5000:
        raise ToolError("Enter positive rectangle coordinates and dimensions up to 5000 points.")
    document = open_pdf(data, filename)
    try:
        for index, page in enumerate(document, start=1):
            rect = fitz.Rect(x, y, x + width, y + height)
            if not page.rect.contains(rect):
                raise ToolError(f"The rectangle does not fit within page {index}.")
            page.draw_rect(rect, color=(0.16, 0.34, 0.86), width=1.5, overlay=True)
        result = document.tobytes(garbage=3, deflate=True)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-marked.pdf", input_bytes=len(data))


def encrypt_pdf(filename: str, data: bytes, user_password: str, owner_password: str, profile: str) -> ToolResult:
    validate_pdf(data, filename)
    if len(user_password) < 8 or len(owner_password) < 8 or user_password == owner_password:
        raise ToolError("Use different user and owner passwords, each at least 8 characters long.")
    profiles = {
        "view": pikepdf.Permissions(accessibility=True, extract=False, modify_annotation=False, modify_assembly=False, modify_form=False, modify_other=False, print_lowres=False, print_highres=False),
        "print": pikepdf.Permissions(accessibility=True, extract=False, modify_annotation=False, modify_assembly=False, modify_form=False, modify_other=False, print_lowres=True, print_highres=False),
        "copy": pikepdf.Permissions(accessibility=True, extract=True, modify_annotation=False, modify_assembly=False, modify_form=False, modify_other=False, print_lowres=True, print_highres=True),
        "edit": pikepdf.Permissions(),
    }
    if profile not in profiles:
        raise ToolError("Choose a supported permissions profile.")
    buffer = BytesIO()
    try:
        with pikepdf.open(BytesIO(data)) as document:
            document.save(
                buffer,
                encryption=pikepdf.Encryption(
                    owner=owner_password,
                    user=user_password,
                    R=6,
                    allow=profiles[profile],
                ),
            )
    except pikepdf.PasswordError as error:
        raise ToolError("This PDF is already encrypted. Decrypt it before changing its security settings.") from error
    return ToolResult(buffer.getvalue(), f"{safe_stem(filename)}-protected.pdf", input_bytes=len(data))


def sign_pdf(
    files: Sequence[tuple[str, bytes]],
    certificate_password: str,
    field_name: str,
    reason: str,
    *,
    certify: bool = False,
) -> ToolResult:
    if len(files) != 2:
        raise ToolError("Choose one PDF and one PKCS#12 (.p12 or .pfx) certificate.")
    pdf_files = [(name, content) for name, content in files if name.lower().endswith(".pdf")]
    certificate_files = [(name, content) for name, content in files if name.lower().endswith((".p12", ".pfx"))]
    if len(pdf_files) != 1 or len(certificate_files) != 1:
        raise ToolError("Choose exactly one PDF and one PKCS#12 (.p12 or .pfx) certificate.")
    filename, data = pdf_files[0]
    certificate_name, certificate_data = certificate_files[0]
    if not field_name or len(field_name) > 50 or not re.fullmatch(r"[A-Za-z0-9_.-]+", field_name):
        raise ToolError("Use a signature field name of 1–50 letters, numbers, dots, underscores, or hyphens.")
    if len(reason) > 200 or len(certificate_password) > 500:
        raise ToolError("The signature reason or certificate password is too long.")
    validate_pdf(data, filename)
    try:
        signer = signers.SimpleSigner.load_pkcs12_data(
            certificate_data,
            other_certs=(),
            passphrase=certificate_password.encode("utf-8"),
        )
    except (TypeError, ValueError) as error:
        raise ToolError(f"{certificate_name}: the certificate or password is invalid.") from error
    if signer is None:
        raise ToolError(f"{certificate_name}: the certificate or password is invalid.")
    pdf_stream = BytesIO(data)
    output = BytesIO()
    try:
        writer = IncrementalPdfFileWriter(pdf_stream)
        metadata = signers.PdfSignatureMetadata(
            field_name=field_name,
            reason=reason or None,
            certify=certify,
            docmdp_permissions=MDPPerm.NO_CHANGES if certify else MDPPerm.FILL_FORMS,
        )
        engine = signers.PdfSigner(
            metadata,
            signer=signer,
            new_field_spec=SigFieldSpec(sig_field_name=field_name),
        )
        engine.sign_pdf(writer, output=output)
    except (ValueError, OSError, PdfReadError, SigningError) as error:
        raise ToolError("The PDF could not be signed. Check the certificate and input PDF.") from error
    suffix = "certified" if certify else "signed"
    return ToolResult(
        output.getvalue(),
        f"{safe_stem(filename)}-{suffix}.pdf",
        input_bytes=len(data) + len(certificate_data),
    )


def verify_signature_pdf(filename: str, data: bytes) -> ToolResult:
    validate_pdf(data, filename)
    try:
        reader = PdfFileReader(BytesIO(data))
        signatures = reader.embedded_signatures
        lines = [f"File: {safe_stem(filename)}.pdf", f"Signatures found: {len(signatures)}"]
        if not signatures:
            lines.append("Result: no digital signatures were found.")
        for index, signature in enumerate(signatures, start=1):
            status = validate_pdf_signature(signature, skip_diff=False)
            trusted = "yes" if status.trusted is True else "no or unknown"
            lines.extend([
                f"Signature {index}: {signature.field_name}",
                f"  Cryptographic integrity: {'valid' if status.intact else 'invalid'}",
                f"  Signature validity: {'valid' if status.valid else 'invalid'}",
                f"  Certificate trusted by configured roots: {trusted}",
            ])
        lines.append("Trust status depends on the processing server's configured trust roots and revocation data.")
        report = "\n".join(lines) + "\n"
    except (PdfReadError, SignatureValidationError, ValueError, SigningError) as error:
        raise ToolError("The PDF signatures could not be verified.") from error
    return ToolResult(
        report.encode("utf-8"),
        f"{safe_stem(filename)}-signature-report.txt",
        "text/plain; charset=utf-8",
        len(data),
    )


def decrypt_pdf(filename: str, data: bytes, password: str) -> ToolResult:
    validate_pdf(data, filename)
    buffer = BytesIO()
    try:
        password_options = {"password": password}
        with pikepdf.open(BytesIO(data), **password_options) as document:
            document.save(buffer, encryption=False)
    except pikepdf.PasswordError as error:
        raise ToolError("The PDF password is incorrect.") from error
    return ToolResult(buffer.getvalue(), f"{safe_stem(filename)}-unlocked.pdf", input_bytes=len(data))


def extract_attachments_zip(filename: str, data: bytes) -> ToolResult:
    document = open_pdf(data, filename)
    buffer = BytesIO()
    try:
        names = document.embfile_names()
        if not names:
            raise ToolError("This PDF has no embedded attachments.")
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in names:
                safe_name = re.sub(r"[/\\\x00-\x1f]+", "-", PurePath(name).name).strip(". ") or "attachment"
                archive.writestr(safe_name, document.embfile_get(name))
    finally:
        document.close()
    return ToolResult(buffer.getvalue(), f"{safe_stem(filename)}-attachments.zip", "application/zip", len(data))


def remove_attachments_pdf(filename: str, data: bytes) -> ToolResult:
    document = open_pdf(data, filename)
    try:
        for name in document.embfile_names():
            document.embfile_del(name)
        result = document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()
    return ToolResult(result, f"{safe_stem(filename)}-attachments-removed.pdf", input_bytes=len(data))


def ocr_to_text(filename: str, data: bytes, language: str) -> ToolResult:
    if language not in OCR_LANGUAGES:
        raise ToolError("Choose a supported OCR language: English, Hindi, or English + Hindi.")
    if not shutil.which("tesseract"):
        raise ToolError(
            "OCR needs Tesseract installed on the processing server. Install Tesseract and the selected language data, then restart the toolkit.",
            status_code=503,
        )
    document = open_pdf(data, filename)
    try:
        if document.page_count > MAX_OCR_PAGES:
            raise ToolError(f"OCR is limited to {MAX_OCR_PAGES} pages per operation.")
        validate_rendered_pixels(document, 200, "OCR")
        extracted = []
        for page_number, page in enumerate(document, start=1):
            try:
                text_page = page.get_textpage_ocr(language=language, dpi=200, full=True)
            except RuntimeError as error:
                raise ToolError(
                    "Tesseract could not start. Check that the selected language data is installed."
                ) from error
            text = page.get_text("text", textpage=text_page).strip()
            extracted.append(f"--- Page {page_number} ---\n{text}")
        result = ("\n\n".join(extracted) + "\n").encode("utf-8")
    finally:
        document.close()
    return ToolResult(
        result,
        f"{safe_stem(filename)}-ocr.txt",
        "text/plain; charset=utf-8",
        len(data),
    )


def ocr_searchable_pdf(filename: str, data: bytes, language: str) -> ToolResult:
    if language not in OCR_LANGUAGES:
        raise ToolError("Choose a supported OCR language: English, Hindi, or English + Hindi.")
    executable = shutil.which("tesseract")
    if not executable:
        raise ToolError(
            "OCR needs Tesseract installed on the processing server. Install Tesseract and the selected language data, then restart the toolkit.",
            status_code=503,
        )
    source = open_pdf(data, filename)
    output = fitz.open()
    try:
        if source.page_count > MAX_OCR_PAGES:
            raise ToolError(f"Searchable OCR is limited to {MAX_OCR_PAGES} pages per operation.")
        validate_rendered_pixels(source, 200, "Searchable OCR")
        for page in source:
            image = page.get_pixmap(dpi=200, alpha=False).tobytes("png")
            try:
                result = subprocess.run(
                    [executable, "stdin", "stdout", "-l", language, "pdf"],
                    input=image,
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                raise ToolError("Tesseract took too long on a page. Try a smaller document.") from error
            if result.returncode:
                raise ToolError("Tesseract could not process a page. Check the selected language data on the server.")
            try:
                recognized = fitz.open(stream=result.stdout, filetype="pdf")
                try:
                    output.insert_pdf(recognized)
                finally:
                    recognized.close()
            except (fitz.FileDataError, fitz.EmptyFileError) as error:
                raise ToolError("Tesseract returned an invalid searchable PDF.") from error
        result_data = output.tobytes(garbage=3, deflate=True)
    finally:
        source.close()
        output.close()
    return ToolResult(result_data, f"{safe_stem(filename)}-searchable.pdf", input_bytes=len(data))


def list_ocr_languages() -> list[str]:
    executable = shutil.which("tesseract")
    if not executable:
        raise ToolError(
            "OCR needs Tesseract installed on the processing server.",
            status_code=503,
        )
    try:
        result = subprocess.run(
            [executable, "--list-langs"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ToolError("Tesseract did not respond to the language query.", status_code=503) from error
    if result.returncode:
        raise ToolError("Tesseract could not list its installed language data.", status_code=503)
    return [
        line.strip()
        for line in result.stdout.splitlines()
        if re.fullmatch(r"[A-Za-z0-9_+-]+", line.strip())
    ]


def run_tool(
    tool: str,
    files: Sequence[tuple[str, bytes]],
    *,
    ranges: str = "",
    rotation: int = 90,
    order: str = "",
    language: str = "eng",
    document_language: str = "en-US",
    structure_order: str = "",
    prefix: str = "",
    label_style: str = "D",
    first_number: int = 1,
    margin: float = 24,
    page_size: str = "a4",
    dpi: int = 120,
    quality: int = 70,
    watermark: str = "",
    stamp_position: str = "center",
    header: str = "",
    footer: str = "",
    page_number: bool = True,
    title: str = "",
    author: str = "",
    subject: str = "",
    keywords: str = "",
    profile: str = "copy",
    user_password: str = "",
    owner_password: str = "",
    password: str = "",
    phrase: str = "",
    comment: str = "",
    x: float = 50,
    y: float = 50,
    width: float = 100,
    height: float = 60,
    field_values: str = "",
    batch_steps: str = "",
    certificate_password: str = "",
    signature_field: str = "Signature1",
    signature_reason: str = "",
) -> ToolResult:
    if tool == "batch":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF for batch processing.")
        return run_batch(files[0][0], files[0][1], batch_steps)
    if tool == "merge":
        return merge_pdfs(files)
    if tool == "split":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to split.")
        return split_pdf(files[0][0], files[0][1], ranges)
    if tool == "rotate":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to rotate.")
        return rotate_pdf(files[0][0], files[0][1], rotation)
    if tool == "reorder":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to reorder.")
        return reorder_pdf(files[0][0], files[0][1], order)
    if tool in {"extract", "delete-pages", "duplicate-pages"}:
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF for this page operation.")
        operations = {
            "extract": extract_pages_pdf,
            "delete-pages": delete_pages_pdf,
            "duplicate-pages": duplicate_pages_pdf,
        }
        return operations[tool](files[0][0], files[0][1], ranges)
    if tool == "reverse-pages":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to reverse.")
        return reverse_pages_pdf(files[0][0], files[0][1])
    if tool == "insert-blank":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF for blank-page insertion.")
        return insert_blank_pages_pdf(files[0][0], files[0][1], ranges)
    if tool == "page-labels":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to label.")
        return set_page_labels_pdf(files[0][0], files[0][1], prefix, label_style, first_number)
    if tool == "crop-pages":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to crop.")
        return crop_pages_pdf(files[0][0], files[0][1], margin)
    if tool == "resize-pages":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to resize.")
        return resize_pages_pdf(files[0][0], files[0][1], page_size)
    if tool == "image-to-pdf":
        return images_to_pdf(files)
    if tool == "compress":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to optimize.")
        return compress_pdf(files[0][0], files[0][1])
    if tool == "compress-images":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to compress images in.")
        return compress_images_pdf(files[0][0], files[0][1], dpi, quality)
    if tool == "pdf-to-images":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to convert to images.")
        return pdf_to_images(files[0][0], files[0][1], dpi)
    if tool == "pdf-to-slides":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to convert to slides.")
        return pdf_to_slides(files[0][0], files[0][1])
    if tool == "pdf-to-text":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to extract text from.")
        return pdf_to_text(files[0][0], files[0][1])
    if tool == "pdf-to-markdown":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to export as Markdown.")
        return pdf_to_markdown(files[0][0], files[0][1])
    if tool == "accessible-text":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to export as accessible text.")
        return accessible_text_export(files[0][0], files[0][1])
    if tool in {"text-to-pdf", "markdown-to-pdf"}:
        if len(files) != 1:
            raise ToolError("Choose exactly one text file to convert.")
        return text_to_pdf(files[0][0], files[0][1], markdown=tool == "markdown-to-pdf")
    if tool == "html-to-pdf":
        if len(files) != 1:
            raise ToolError("Choose exactly one HTML file to convert.")
        return html_to_pdf(files[0][0], files[0][1])
    if tool == "epub-to-pdf":
        if len(files) != 1:
            raise ToolError("Choose exactly one EPUB file to convert.")
        return epub_to_pdf(files[0][0], files[0][1])
    if tool == "word-to-pdf":
        if len(files) != 1:
            raise ToolError("Choose exactly one Word document to convert.")
        return word_to_pdf(files[0][0], files[0][1])
    if tool == "pdf-to-word":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to convert to Word.")
        return pdf_to_word_docx(files[0][0], files[0][1])
    if tool == "excel-to-pdf":
        if len(files) != 1:
            raise ToolError("Choose exactly one Excel workbook to convert.")
        return excel_to_pdf(files[0][0], files[0][1])
    if tool == "slides-to-pdf":
        if len(files) != 1:
            raise ToolError("Choose exactly one PowerPoint presentation to convert.")
        return slides_to_pdf(files[0][0], files[0][1])
    if tool == "pdf-to-excel":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to convert to Excel.")
        return pdf_to_excel_xlsx(files[0][0], files[0][1])
    if tool == "linearize":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to optimize for web viewing.")
        return linearize_pdf(files[0][0], files[0][1])
    if tool == "validate":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to validate.")
        return validate_pdf_report(files[0][0], files[0][1])
    if tool == "pdfua":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF for the accessibility check.")
        return pdfua_accessibility_report(files[0][0], files[0][1])
    if tool == "pdfa":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to convert to PDF/A.")
        return convert_pdfa(files[0][0], files[0][1])
    if tool == "tag-pdf":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to add basic structure tags to.")
        return tag_pdf_structure(files[0][0], files[0][1], document_language)
    if tool == "reading-order":
        if len(files) != 1:
            raise ToolError("Choose exactly one tagged PDF to set the reading order for.")
        return set_reading_order(files[0][0], files[0][1], structure_order)
    if tool == "grayscale":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to convert to grayscale.")
        return grayscale_pdf(files[0][0], files[0][1], dpi)
    if tool == "deskew":
        if len(files) != 1:
            raise ToolError("Choose exactly one scanned PDF to deskew.")
        return deskew_pdf(files[0][0], files[0][1])
    if tool == "enhance-scan":
        if len(files) != 1:
            raise ToolError("Choose exactly one scanned PDF to enhance.")
        return enhance_scan_pdf(files[0][0], files[0][1])
    if tool == "deduplicate":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to check for duplicate pages.")
        return deduplicate_pages_pdf(files[0][0], files[0][1])
    if tool == "watermark":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to watermark.")
        return watermark_pdf(files[0][0], files[0][1], watermark)
    if tool == "stamp":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to stamp.")
        return stamp_pdf(files[0][0], files[0][1], watermark, stamp_position)
    if tool == "headers-footers":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF for headers and footers.")
        return add_headers_footers_pdf(files[0][0], files[0][1], header, footer, page_number)
    if tool == "page-numbers":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to number.")
        return page_numbers_pdf(files[0][0], files[0][1], first_number)
    if tool == "extract-attachments":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to extract attachments from.")
        return extract_attachments_zip(files[0][0], files[0][1])
    if tool == "remove-attachments":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to remove attachments from.")
        return remove_attachments_pdf(files[0][0], files[0][1])
    if tool == "edit-metadata":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to edit metadata for.")
        metadata = {key: value for key, value in {
            "title": title,
            "author": author,
            "subject": subject,
            "keywords": keywords,
        }.items() if value}
        return update_metadata_pdf(files[0][0], files[0][1], metadata)
    if tool == "remove-metadata":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to remove metadata from.")
        return remove_metadata_pdf(files[0][0], files[0][1])
    if tool == "remove-hidden":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to sanitize.")
        return remove_hidden_content_pdf(files[0][0], files[0][1])
    if tool == "fill-forms":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to fill.")
        return fill_form_pdf(files[0][0], files[0][1], field_values)
    if tool == "flatten-forms":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to flatten form fields in.")
        return flatten_forms_pdf(files[0][0], files[0][1])
    if tool == "svg-to-pdf":
        if len(files) != 1:
            raise ToolError("Choose exactly one SVG to convert.")
        return svg_to_pdf(files[0][0], files[0][1])
    if tool == "repair":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to repair.")
        return repair_pdf(files[0][0], files[0][1])
    if tool in {"compare-text", "compare-pages", "visual-diff"}:
        if tool == "compare-text":
            return compare_text_pdfs(files)
        return compare_page_renderings(files, visual_diff=tool == "visual-diff")
    if tool == "highlight":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to highlight.")
        return highlight_text_pdf(files[0][0], files[0][1], phrase)
    if tool == "redact":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to redact.")
        return redact_text_pdf(files[0][0], files[0][1], phrase)
    if tool == "annotate":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to annotate.")
        return comment_pdf(files[0][0], files[0][1], comment)
    if tool == "draw":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to draw on.")
        return draw_rectangle_pdf(files[0][0], files[0][1], x, y, width, height)
    if tool in {"encrypt", "permissions"}:
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to protect.")
        return encrypt_pdf(files[0][0], files[0][1], user_password, owner_password, profile)
    if tool in {"sign", "certify"}:
        return sign_pdf(
            files,
            certificate_password,
            signature_field,
            signature_reason,
            certify=tool == "certify",
        )
    if tool == "verify-signature":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to verify signatures in.")
        return verify_signature_pdf(files[0][0], files[0][1])
    if tool == "decrypt":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to decrypt.")
        return decrypt_pdf(files[0][0], files[0][1], password)
    if tool == "ocr-text":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF for OCR.")
        return ocr_to_text(files[0][0], files[0][1], language)
    if tool == "ocr-searchable":
        if len(files) != 1:
            raise ToolError("Choose exactly one PDF to make searchable.")
        return ocr_searchable_pdf(files[0][0], files[0][1], language)
    raise ToolError("That tool is not available yet.", status_code=404)


def run_batch(filename: str, data: bytes, steps_json: str) -> ToolResult:
    try:
        steps = json.loads(steps_json)
    except json.JSONDecodeError as error:
        raise ToolError("Enter a valid JSON array of batch steps.") from error
    if not isinstance(steps, list) or not steps or len(steps) > 10:
        raise ToolError("A batch must contain between 1 and 10 steps.")
    current_name, current_data = filename, data
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict) or set(step) - {"tool", "options"}:
            raise ToolError(f"Batch step {index} must contain only tool and options.")
        tool = step.get("tool")
        options = step.get("options", {})
        if not isinstance(tool, str) or tool not in BATCH_TOOLS:
            raise ToolError(f"Batch step {index} uses an unsupported PDF-to-PDF operation.")
        if not isinstance(options, dict) or set(options) - BATCH_OPTION_KEYS[tool]:
            raise ToolError(f"Batch step {index} has unsupported options.")
        for key, value in options.items():
            if key in {"rotation", "first_number", "dpi", "quality"}:
                valid_type = type(value) is int
            elif key in {"margin", "x", "y", "width", "height"}:
                valid_type = type(value) in {int, float}
            elif key == "page_number":
                valid_type = type(value) is bool
            else:
                valid_type = isinstance(value, str)
            if not valid_type:
                raise ToolError(f"Batch step {index} option '{key}' has the wrong type.")
        result = run_tool(tool, [(current_name, current_data)], **options)
        if result.media_type != "application/pdf":
            raise ToolError(f"Batch step {index} did not return a PDF.")
        if len(result.data) > MAX_UPLOAD_BYTES:
            raise ToolError(f"Batch step {index} produced an output larger than 80 MiB.")
        current_name, current_data = result.filename, result.data
    return ToolResult(
        current_data,
        current_name.rsplit(".", 1)[0] + "-batch.pdf",
        input_bytes=len(data),
    )
