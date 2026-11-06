from __future__ import annotations

import json
import base64
from collections.abc import Mapping, Sequence
from typing import Any

import pymupdf as fitz

from app.pdf_tools import (
    MAX_UPLOAD_BYTES,
    ToolError,
    ToolResult,
    compress_images_pdf,
    compress_pdf,
    crop_pages_pdf,
    decrypt_pdf,
    delete_pages_pdf,
    duplicate_pages_pdf,
    encrypt_pdf,
    extract_pages_pdf,
    form_fields,
    grayscale_pdf,
    insert_blank_pages_pdf,
    linearize_pdf,
    merge_pdfs,
    open_pdf,
    redact_text_pdf,
    remove_hidden_content_pdf,
    remove_metadata_pdf,
    reorder_pdf,
    resize_pages_pdf,
    reverse_pages_pdf,
    rotate_pdf,
    safe_stem,
    watermark_pdf,
)

MAX_PIPELINE_STEPS = 10
MAX_PREVIEW_PAGES = 60

PRIMITIVE_OPERATIONS: dict[str, dict[str, set[str]]] = {
    "compose": {
        "rotate": {"rotation"},
        "rotate-pages": {"pages", "rotation"},
        "reorder": {"order"},
        "reverse": set(),
        "delete": {"ranges"},
        "extract": {"ranges"},
        "duplicate": {"ranges"},
        "blank": {"ranges"},
        "crop": {"margin"},
        "resize": {"page_size"},
    },
    "transform": {
        "compress": set(),
        "compress-images": {"dpi", "quality"},
        "grayscale": {"dpi"},
        "linearize": set(),
        "watermark": {"watermark"},
    },
    "protect": {
        "encrypt": {"user_password", "owner_password", "profile"},
        "decrypt": {"password"},
        "redact": {"phrase"},
        "scrub-metadata": set(),
        "sanitize": set(),
    },
}

_OPTION_TYPES: dict[str, type | tuple[type, ...]] = {
    "rotation": int,
    "pages": list,
    "order": str,
    "ranges": str,
    "margin": (int, float),
    "page_size": str,
    "dpi": int,
    "quality": int,
    "watermark": str,
    "user_password": str,
    "owner_password": str,
    "profile": str,
    "password": str,
    "phrase": str,
}


def parse_steps(value: str, *, field_name: str = "steps") -> list[dict[str, Any]]:
    try:
        steps = json.loads(value)
    except json.JSONDecodeError as error:
        raise ToolError(f"'{field_name}' must be a valid JSON array.") from error
    if not isinstance(steps, list) or len(steps) > MAX_PIPELINE_STEPS:
        raise ToolError(f"'{field_name}' must contain at most {MAX_PIPELINE_STEPS} steps.")
    parsed: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict) or set(step) - {"primitive", "operation", "options"}:
            raise ToolError(f"Step {index} must contain only primitive, operation, and options.")
        primitive = step.get("primitive")
        operation = step.get("operation")
        options = step.get("options", {})
        if not isinstance(primitive, str) or primitive not in PRIMITIVE_OPERATIONS:
            raise ToolError(f"Step {index} uses an unsupported engine primitive.")
        if not isinstance(operation, str) or operation not in PRIMITIVE_OPERATIONS[primitive]:
            raise ToolError(f"Step {index} uses an unsupported {primitive} operation.")
        allowed_options = PRIMITIVE_OPERATIONS[primitive][operation]
        if not isinstance(options, dict) or set(options) - allowed_options:
            raise ToolError(f"Step {index} contains unsupported options.")
        for key, value in options.items():
            expected_type = _OPTION_TYPES[key]
            if type(value) not in (expected_type if isinstance(expected_type, tuple) else (expected_type,)):
                raise ToolError(f"Step {index} option '{key}' has the wrong type.")
        parsed.append({"primitive": primitive, "operation": operation, "options": options})
    return parsed


def _run_step(filename: str, data: bytes, step: Mapping[str, Any]) -> ToolResult:
    primitive = step["primitive"]
    operation = step["operation"]
    options = dict(step.get("options", {}))
    if primitive == "compose" and operation == "rotate-pages":
        pages = options.get("pages")
        rotation = options.get("rotation")
        if not isinstance(pages, list) or not pages or any(type(page) is not int for page in pages):
            raise ToolError("Rotate pages requires a non-empty list of page numbers.")
        if rotation not in {90, 180, 270}:
            raise ToolError("Choose a rotation of 90, 180, or 270 degrees.")
        document = open_pdf(data, filename)
        try:
            if any(page < 1 or page > document.page_count for page in pages):
                raise ToolError(f"Page numbers must be between 1 and {document.page_count}.")
            for page_number in pages:
                page = document[page_number - 1]
                page.set_rotation((page.rotation + rotation) % 360)
            result_data = document.tobytes(garbage=3, deflate=True)
        finally:
            document.close()
        if len(result_data) > MAX_UPLOAD_BYTES:
            raise ToolError("Rotate pages produced an output larger than 80 MiB.", 413)
        return ToolResult(result_data, f"{safe_stem(filename)}-rotated.pdf", input_bytes=len(data))
    if primitive == "compose":
        if operation == "rotate":
            result = rotate_pdf(filename, data, options.get("rotation", 90))
        elif operation == "reorder":
            result = reorder_pdf(filename, data, options.get("order", ""))
        elif operation == "reverse":
            result = reverse_pages_pdf(filename, data)
        elif operation == "delete":
            result = delete_pages_pdf(filename, data, options.get("ranges", ""))
        elif operation == "extract":
            result = extract_pages_pdf(filename, data, options.get("ranges", ""))
        elif operation == "duplicate":
            result = duplicate_pages_pdf(filename, data, options.get("ranges", ""))
        elif operation == "blank":
            result = insert_blank_pages_pdf(filename, data, options.get("ranges", ""))
        elif operation == "crop":
            result = crop_pages_pdf(filename, data, options.get("margin", 24))
        else:
            result = resize_pages_pdf(filename, data, options.get("page_size", "a4"))
    elif primitive == "transform":
        if operation == "compress":
            result = compress_pdf(filename, data)
        elif operation == "compress-images":
            result = compress_images_pdf(filename, data, options.get("dpi", 120), options.get("quality", 70))
        elif operation == "grayscale":
            result = grayscale_pdf(filename, data, options.get("dpi", 120))
        elif operation == "linearize":
            result = linearize_pdf(filename, data)
        else:
            result = watermark_pdf(filename, data, options.get("watermark", ""))
    elif operation == "encrypt":
        result = encrypt_pdf(
            filename,
            data,
            options.get("user_password", ""),
            options.get("owner_password", ""),
            options.get("profile", "copy"),
        )
    elif operation == "decrypt":
        result = decrypt_pdf(filename, data, options.get("password", ""))
    elif operation == "redact":
        result = redact_text_pdf(filename, data, options.get("phrase", ""))
    elif operation == "scrub-metadata":
        result = remove_metadata_pdf(filename, data)
    else:
        result = remove_hidden_content_pdf(filename, data)
    if result.media_type != "application/pdf":
        raise ToolError(f"{primitive.title()} operation '{operation}' did not produce a PDF.")
    if len(result.data) > MAX_UPLOAD_BYTES:
        raise ToolError(f"{primitive.title()} operation '{operation}' produced an output larger than 80 MiB.", 413)
    return result


def execute_engine(
    primitive: str,
    files: Sequence[tuple[str, bytes]],
    steps: Sequence[Mapping[str, Any]],
) -> ToolResult:
    if primitive not in PRIMITIVE_OPERATIONS:
        raise ToolError("That engine primitive is not available.", 404)
    if any(step["primitive"] != primitive for step in steps):
        raise ToolError(f"Every step must use the '{primitive}' primitive.")
    return _execute_steps(files, steps)


def _execute_steps(files: Sequence[tuple[str, bytes]], steps: Sequence[Mapping[str, Any]]) -> ToolResult:
    if not files:
        raise ToolError("Provide at least one PDF document.")
    if len(files) > 100:
        raise ToolError("An operation can include at most 100 files.", 413)
    if sum(len(data) for _, data in files) > MAX_UPLOAD_BYTES:
        raise ToolError("Combined input files exceed the 80 MiB server limit.", 413)

    if len(files) == 1:
        filename, data = files[0]
    else:
        merged = merge_pdfs(files)
        if len(merged.data) > MAX_UPLOAD_BYTES:
            raise ToolError("Composed output exceeded the 80 MiB server limit.", 413)
        filename, data = merged.filename, merged.data

    original_size = sum(len(payload) for _, payload in files)
    for step in steps:
        result = _run_step(filename, data, step)
        filename, data = result.filename, result.data
    return ToolResult(data, f"{safe_stem(filename)}-prism.pdf", input_bytes=original_size)


def execute_pipeline(files: Sequence[tuple[str, bytes]], steps: Sequence[Mapping[str, Any]]) -> ToolResult:
    if not steps:
        raise ToolError("A pipeline must contain at least one step.")
    if any(step["primitive"] not in PRIMITIVE_OPERATIONS for step in steps):
        raise ToolError("A pipeline contains an unsupported engine primitive.")
    if len({step["primitive"] for step in steps}) > len(PRIMITIVE_OPERATIONS):
        raise ToolError("A pipeline can use Compose, Transform, and Protect steps.")
    return _execute_steps(files, steps)


def inspect_document(filename: str, data: bytes) -> dict[str, Any]:
    document = open_pdf(data, filename)
    try:
        fonts: set[str] = set()
        images: set[int] = set()
        pages = []
        for index, page in enumerate(document, start=1):
            if index > MAX_PREVIEW_PAGES:
                break
            page_fonts = page.get_fonts(full=True)
            for font in page_fonts:
                if len(font) > 3:
                    fonts.add(str(font[3]))
            for image in page.get_images(full=True):
                if image:
                    images.add(int(image[0]))
            pages.append({
                "number": index,
                "width": round(page.rect.width, 2),
                "height": round(page.rect.height, 2),
                "rotation": page.rotation,
                "text_characters": len(page.get_text("text")),
                "image_count": len(page.get_images(full=True)),
                "thumbnail": _page_thumbnail(page),
            })
        metadata = document.metadata or {}
        return {
            "filename": safe_stem(filename) + ".pdf",
            "page_count": document.page_count,
            "pages_truncated": document.page_count > MAX_PREVIEW_PAGES,
            "encrypted": document.is_encrypted,
            "pdf_version": metadata.get("format") or "PDF",
            "fields": [
                {"name": widget.field_name, "type": widget.field_type_string}
                for widget in form_fields(document)
            ],
            "metadata": {
                key: metadata.get(key, "")
                for key in ("title", "author", "subject", "keywords", "creator", "producer", "creationDate", "modDate")
            },
            "fonts": sorted(fonts),
            "image_count": len(images),
            "pages": pages,
        }
    except (fitz.FileDataError, fitz.EmptyFileError, ValueError) as error:
        raise ToolError("The PDF could not be inspected. Check that it is valid and unencrypted.") from error
    finally:
        document.close()


def _page_thumbnail(page: fitz.Page) -> str:
    scale = min(96 / max(page.rect.width, 1), 128 / max(page.rect.height, 1), 1.0)
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return "data:image/png;base64," + base64.b64encode(pixmap.tobytes("png")).decode("ascii")
