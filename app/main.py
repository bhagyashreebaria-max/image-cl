from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import pymupdf as fitz
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.engine import (
    MAX_PREVIEW_PAGES,
    MAX_PIPELINE_STEPS,
    PRIMITIVE_OPERATIONS,
    execute_engine,
    execute_pipeline,
    inspect_document,
    parse_steps,
)
from app.pdf_tools import MAX_INPUT_FILES, MAX_UPLOAD_BYTES, ToolError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("prism-document-engine")

STATIC_DIR = Path(__file__).parent / "static"
app = FastAPI(title="Prism Composable Document Engine", docs_url=None, redoc_url=None)
try:
    MAX_CONCURRENT_JOBS = int(os.environ.get("PAPERTRAIL_MAX_CONCURRENT_JOBS", "2"))
except ValueError as error:
    raise RuntimeError("PAPERTRAIL_MAX_CONCURRENT_JOBS must be an integer from 1 to 64.") from error
if not 1 <= MAX_CONCURRENT_JOBS <= 64:
    raise RuntimeError("PAPERTRAIL_MAX_CONCURRENT_JOBS must be an integer from 1 to 64.")
PROCESSING_QUEUE_TIMEOUT_SECONDS = 30
processing_slots = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
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


@app.middleware("http")
async def limit_processing_concurrency(request, call_next):
    if not request.url.path.startswith("/api/v1/engine/"):
        return await call_next(request)
    try:
        await asyncio.wait_for(processing_slots.acquire(), timeout=PROCESSING_QUEUE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return JSONResponse(
            {"detail": "The processing service is at capacity. Please retry shortly."},
            status_code=503,
            headers={"Retry-After": "5"},
        )
    try:
        return await call_next(request)
    finally:
        processing_slots.release()


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/api/v1/engine")
async def engine_capabilities():
    return {
        "name": "Prism Composable Document Engine",
        "api_version": "v1",
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "max_concurrent_jobs": MAX_CONCURRENT_JOBS,
        "max_pipeline_steps": MAX_PIPELINE_STEPS,
        "page_preview_limit": MAX_PREVIEW_PAGES,
        "primitives": {
            primitive: {
                operation: sorted(options)
                for operation, options in operations.items()
            }
            for primitive, operations in PRIMITIVE_OPERATIONS.items()
        },
        "pipeline": {"path": "/api/v1/engine/pipeline", "max_steps": MAX_PIPELINE_STEPS},
        "input_storage": "Request parsing may use temporary storage; this service does not retain jobs or outputs.",
    }


def parse_recipe(value: str) -> list[dict[str, Any]]:
    try:
        recipe = json.loads(value)
    except json.JSONDecodeError as error:
        raise ToolError("'recipe' must be a valid JSON array.") from error
    if not isinstance(recipe, list) or not 1 <= len(recipe) <= MAX_PIPELINE_STEPS:
        raise ToolError(f"'recipe' must contain between 1 and {MAX_PIPELINE_STEPS} steps.")
    return parse_steps(json.dumps(recipe), field_name="recipe")


async def read_uploads(uploads: list[UploadFile]) -> list[tuple[str, bytes]]:
    if not uploads:
        raise HTTPException(status_code=400, detail="Provide at least one PDF document.")
    if len(uploads) > MAX_INPUT_FILES:
        raise HTTPException(status_code=413, detail=f"An operation can include at most {MAX_INPUT_FILES} files.")
    payloads: list[tuple[str, bytes]] = []
    total_size = 0
    try:
        for upload in uploads:
            content = await upload.read(MAX_UPLOAD_BYTES - total_size + 1)
            total_size += len(content)
            if total_size > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="Combined input files exceed the 80 MiB server limit.")
            payloads.append((upload.filename or "document.pdf", content))
    finally:
        for upload in uploads:
            await upload.close()
    return payloads


def file_response(result) -> Response:
    return Response(
        content=result.data,
        media_type=result.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{result.filename}"',
            "X-Input-Bytes": str(result.input_bytes),
            "X-Output-Bytes": str(len(result.data)),
        },
    )


async def run_engine_request(primitive: str, uploads: list[UploadFile], raw_steps: str, *, pipeline: bool = False):
    try:
        try:
            steps = parse_recipe(raw_steps) if pipeline else parse_steps(raw_steps)
        except ToolError:
            for upload in uploads:
                await upload.close()
            raise
        files = await read_uploads(uploads)
        runner = execute_pipeline if pipeline else execute_engine
        arguments = (files, steps) if pipeline else (primitive, files, steps)
        result = await run_in_threadpool(runner, *arguments)
        return file_response(result)
    except HTTPException:
        raise
    except ToolError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    except (fitz.FileDataError, fitz.EmptyFileError, ValueError) as error:
        logger.info("Rejected document input for %s: %s", primitive, error)
        raise HTTPException(status_code=400, detail="The document could not be processed. Check that it is a valid, unencrypted PDF.") from error
    except Exception as error:
        logger.exception("Unexpected error while running the %s engine", primitive)
        raise HTTPException(status_code=500, detail="Processing failed. Check the processing server logs for details.") from error


@app.post("/api/v1/engine/compose")
async def compose_engine(files: list[UploadFile] = File(...), steps: str = Form("[]")):
    return await run_engine_request("compose", files, steps)


@app.post("/api/v1/engine/transform")
async def transform_engine(files: list[UploadFile] = File(...), steps: str = Form(...)):
    return await run_engine_request("transform", files, steps)


@app.post("/api/v1/engine/protect")
async def protect_engine(files: list[UploadFile] = File(...), steps: str = Form(...)):
    return await run_engine_request("protect", files, steps)


@app.post("/api/v1/engine/pipeline")
async def pipeline_engine(files: list[UploadFile] = File(...), recipe: str = Form(...)):
    return await run_engine_request("pipeline", files, recipe, pipeline=True)


@app.post("/api/v1/engine/inspect")
async def inspect_engine(file: UploadFile = File(...)):
    try:
        data = await file.read(MAX_UPLOAD_BYTES + 1)
    finally:
        await file.close()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is larger than the 80 MiB server limit.")
    try:
        return await run_in_threadpool(inspect_document, file.filename or "document.pdf", data)
    except ToolError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    except (fitz.FileDataError, fitz.EmptyFileError, ValueError) as error:
        logger.info("Rejected document for inspection: %s", error)
        raise HTTPException(status_code=400, detail="The document could not be inspected. Check that it is a valid, unencrypted PDF.") from error
    except Exception as error:
        logger.exception("Unexpected error while inspecting a document")
        raise HTTPException(status_code=500, detail="Inspection failed. Check the processing server logs for details.") from error


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
