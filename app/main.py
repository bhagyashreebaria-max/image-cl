from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import pymupdf as fitz
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.catalog import READY_TOOL_IDS, get_tool_catalog
from app.pdf_tools import (
    BATCH_OPTION_KEYS,
    BATCH_TOOLS,
    MAX_UPLOAD_BYTES,
    ToolError,
    form_fields,
    list_ocr_languages,
    open_pdf,
    run_tool,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("papertrail-pdf-toolkit")

STATIC_DIR = Path(__file__).parent / "static"
app = FastAPI(title="Papertrail PDF Toolkit", docs_url=None, redoc_url=None)
configured_origins = os.environ.get("PAPERTRAIL_ALLOWED_ORIGINS")
ALLOWED_ORIGINS = {
    origin.strip()
    for origin in (
        configured_origins.split(",")
        if configured_origins is not None
        else ["http://127.0.0.1:8000", "http://localhost:8000"]
    )
    if origin.strip()
}
configured_hosts = os.environ.get("PAPERTRAIL_ALLOWED_HOSTS")
ALLOWED_HOSTS = [
    host.strip()
    for host in (configured_hosts or "127.0.0.1,localhost,testserver").split(",")
    if host.strip()
]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


@app.middleware("http")
async def protect_api_origin(request, call_next):
    if request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not origin:
            return Response("An allowed application Origin header is required.", status_code=403)
        if origin and origin not in ALLOWED_ORIGINS:
            return Response("Requests must come from a configured application Origin.", status_code=403)
    return await call_next(request)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/api/status")
async def get_status():
    ocr_available = shutil.which("tesseract") is not None
    return {
        "server_processing": True,
        "ocr_available": ocr_available,
        "ready_tools": sorted(
            READY_TOOL_IDS if ocr_available else READY_TOOL_IDS - {"ocr-text", "ocr-searchable", "ocr-languages"}
        ),
        "workflow_tools": sorted(BATCH_TOOLS),
        "workflow_options": {tool: sorted(options) for tool, options in BATCH_OPTION_KEYS.items()},
        "max_upload_bytes": MAX_UPLOAD_BYTES,
    }


@app.get("/api/tools")
async def get_tools():
    return get_tool_catalog(ocr_available=shutil.which("tesseract") is not None)


@app.get("/api/ocr-languages")
async def get_ocr_languages():
    try:
        languages = list_ocr_languages()
    except ToolError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    return {"languages": languages}


@app.post("/api/inspect")
async def inspect_pdf(file: UploadFile = File(...)):
    try:
        data = await file.read(MAX_UPLOAD_BYTES + 1)
    finally:
        await file.close()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is larger than the 80 MiB server limit.")
    try:
        document = open_pdf(data, file.filename or "document.pdf")
    except ToolError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    try:
        fields = [
            {"name": widget.field_name, "type": widget.field_type_string}
            for widget in form_fields(document)
        ]
        return {"page_count": document.page_count, "fields": fields}
    finally:
        document.close()


@app.post("/api/process/{tool}")
async def process_pdf(
    tool: str,
    files: list[UploadFile] = File(...),
    ranges: str = Form(""),
    rotation: int = Form(90),
    order: str = Form(""),
    language: str = Form("eng"),
    prefix: str = Form(""),
    label_style: str = Form("D"),
    first_number: int = Form(1),
    margin: float = Form(24),
    page_size: str = Form("a4"),
    dpi: int = Form(120),
    quality: int = Form(70),
    watermark: str = Form(""),
    stamp_position: str = Form("center"),
    header: str = Form(""),
    footer: str = Form(""),
    page_number: bool = Form(True),
    title: str = Form(""),
    author: str = Form(""),
    subject: str = Form(""),
    keywords: str = Form(""),
    profile: str = Form("copy"),
    user_password: str = Form(""),
    owner_password: str = Form(""),
    password: str = Form(""),
    phrase: str = Form(""),
    comment: str = Form(""),
    x: float = Form(50),
    y: float = Form(50),
    width: float = Form(100),
    height: float = Form(60),
    field_values: str = Form(""),
    batch_steps: str = Form(""),
    certificate_password: str = Form(""),
    signature_field: str = Form("Signature1"),
    signature_reason: str = Form(""),
):
    if tool not in READY_TOOL_IDS:
        raise HTTPException(status_code=404, detail="That tool is not available yet.")
    payloads: list[tuple[str, bytes]] = []
    total_size = 0
    try:
        for upload in files:
            name = upload.filename or "document"
            content = await upload.read(MAX_UPLOAD_BYTES + 1)
            total_size += len(content)
            if total_size > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="Combined input files exceed the 80 MiB server limit.")
            payloads.append((name, content))
    finally:
        for upload in files:
            await upload.close()

    try:
        result = await run_in_threadpool(
            run_tool,
            tool,
            payloads,
            ranges=ranges,
            rotation=rotation,
            order=order,
            language=language,
            prefix=prefix,
            label_style=label_style,
            first_number=first_number,
            margin=margin,
            page_size=page_size,
            dpi=dpi,
            quality=quality,
            watermark=watermark,
            stamp_position=stamp_position,
            header=header,
            footer=footer,
            page_number=page_number,
            title=title,
            author=author,
            subject=subject,
            keywords=keywords,
            profile=profile,
            user_password=user_password,
            owner_password=owner_password,
            phrase=phrase,
            comment=comment,
            x=x,
            y=y,
            width=width,
            height=height,
            field_values=field_values,
            batch_steps=batch_steps,
            certificate_password=certificate_password,
            signature_field=signature_field,
            signature_reason=signature_reason,
            password=password,
        )
    except ToolError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    except (fitz.FileDataError, fitz.EmptyFileError, ValueError) as error:
        logger.info("Rejected PDF input for %s: %s", tool, error)
        raise HTTPException(status_code=400, detail="The PDF could not be processed. Check that it is valid and unencrypted.") from error
    except Exception as error:
        logger.exception("Unexpected error while running %s", tool)
        raise HTTPException(status_code=500, detail="Processing failed. Check the processing server logs for details.") from error

    headers = {
        "Content-Disposition": f'attachment; filename="{result.filename}"',
        "X-Input-Bytes": str(result.input_bytes),
        "X-Output-Bytes": str(len(result.data)),
    }
    return Response(content=result.data, media_type=result.media_type, headers=headers)


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
