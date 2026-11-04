import asyncio
import io
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch
import subprocess

import pymupdf as fitz
import pikepdf
from PIL import Image
from pptx import Presentation
from pptx.util import Inches
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
from fastapi.testclient import TestClient

from app.catalog import BROWSER_ONLY_TOOL_IDS, READY_TOOL_IDS, TOOL_CATALOG
from app.main import MAX_CONCURRENT_JOBS, MAX_INPUT_FILES, app
from app.pdf_tools import (
    ToolError,
    ToolResult,
    add_headers_footers_pdf,
    accessible_text_export,
    run_tool,
    sign_pdf,
    compress_pdf,
    compare_page_renderings,
    compare_text_pdfs,
    compress_images_pdf,
    crop_pages_pdf,
    deduplicate_pages_pdf,
    delete_pages_pdf,
    deskew_pdf,
    duplicate_pages_pdf,
    extract_attachments_zip,
    extract_pages_pdf,
    enhance_scan_pdf,
    fill_form_pdf,
    flatten_forms_pdf,
    grayscale_pdf,
    html_to_pdf,
    highlight_text_pdf,
    insert_blank_pages_pdf,
    linearize_pdf,
    list_ocr_languages,
    images_to_pdf,
    merge_pdfs,
    page_numbers_pdf,
    ocr_to_text,
    ocr_searchable_pdf,
    parse_page_groups,
    parse_page_order,
    pdf_to_images,
    pdf_to_slides,
    pdf_to_markdown,
    pdf_to_word_docx,
    pdf_to_excel_xlsx,
    pdfua_accessibility_report,
    pdf_to_text,
    plain_text_from_markdown,
    remove_attachments_pdf,
    remove_hidden_content_pdf,
    remove_metadata_pdf,
    resize_pages_pdf,
    reverse_pages_pdf,
    redact_text_pdf,
    reorder_pdf,
    repair_pdf,
    rotate_pdf,
    set_page_labels_pdf,
    split_pdf,
    stamp_pdf,
    text_to_pdf,
    update_metadata_pdf,
    validate_pdf_report,
    verify_signature_pdf,
    watermark_pdf,
    word_to_pdf,
    svg_to_pdf,
    slides_to_pdf,
    encrypt_pdf,
    excel_to_pdf,
    decrypt_pdf,
    epub_to_pdf,
    convert_pdfa,
    pdfa_setup_available,
    reading_order_entry_count,
    set_reading_order,
    tag_pdf_structure,
)


def make_pdf(*labels: str) -> bytes:
    document = fitz.open()
    for label in labels:
        page = document.new_page()
        page.insert_text((72, 72), label)
    data = document.tobytes()
    document.close()
    return data


def make_pptx(*labels: str) -> bytes:
    presentation = Presentation()
    for label in labels:
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(2))
        box.text = label
    data = io.BytesIO()
    presentation.save(data)
    return data.getvalue()


def make_form_pdf() -> bytes:
    document = fitz.open()
    page = document.new_page()
    widget = fitz.Widget()
    widget.field_name = "full_name"
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.rect = fitz.Rect(50, 50, 250, 80)
    page.add_widget(widget)
    data = document.tobytes()
    document.close()
    return data


def make_pkcs12_certificate() -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "Test PDF Signer")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        b"test signer",
        key,
        certificate,
        None,
        BestAvailableEncryption(b"certificate-secret"),
    )


class PDFToolsTests(unittest.TestCase):
    def test_catalog_has_expected_tool_count(self):
        self.assertEqual(len(TOOL_CATALOG), 69)

    def test_catalog_reports_available_tools_as_ready(self):
        from app.catalog import get_tool_catalog

        catalog = get_tool_catalog()
        statuses = {tool["id"]: tool["status"] for tool in catalog}
        excluded_tools = {"ocr-text", "ocr-searchable", "ocr-languages", "pdfa"}
        self.assertTrue(excluded_tools.isdisjoint(statuses))
        self.assertTrue(excluded_tools.isdisjoint(READY_TOOL_IDS))
        self.assertTrue(all(statuses[tool_id] == "ready" for tool_id in READY_TOOL_IDS))
        self.assertEqual(
            BROWSER_ONLY_TOOL_IDS,
            {"watch-folder", "history", "presets", "workflow-builder"},
        )
        self.assertTrue(all(statuses[tool_id] == "ready" for tool_id in BROWSER_ONLY_TOOL_IDS))

    def test_processing_endpoint_dispatches_every_server_tool(self):
        client = TestClient(app)
        server_tools = READY_TOOL_IDS - BROWSER_ONLY_TOOL_IDS
        sample_pdf = make_pdf("sample")
        with patch("app.main.run_tool", return_value=ToolResult(b"processed", "result.pdf")) as process:
            for tool in sorted(server_tools):
                with self.subTest(tool=tool):
                    response = client.post(
                        f"/api/process/{tool}",
                        headers={"origin": "http://localhost:8000"},
                        files={"files": ("document.pdf", sample_pdf, "application/pdf")},
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(process.call_args.args[0], tool)

    def test_processing_capacity_returns_retryable_status(self):
        with patch("app.main.processing_slots", asyncio.Semaphore(0)), patch(
            "app.main.PROCESSING_QUEUE_TIMEOUT_SECONDS", 0.01
        ), TestClient(app) as client:
            for path, field in (("/api/process/merge", "files"), ("/api/inspect", "file")):
                with self.subTest(path=path):
                    response = client.post(
                        path,
                        headers={"origin": "http://localhost:8000"},
                        files={field: ("document.pdf", make_pdf("sample"), "application/pdf")},
                    )
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.headers["retry-after"], "5")

    def test_api_limits_number_of_uploaded_files(self):
        client = TestClient(app)
        files = [
            ("files", (f"document-{index}.pdf", b"pdf", "application/pdf"))
            for index in range(MAX_INPUT_FILES + 1)
        ]
        response = client.post(
            "/api/process/merge",
            headers={"origin": "http://localhost:8000"},
            files=files,
        )
        self.assertEqual(response.status_code, 413)

    def test_pdfa_setup_rejects_malformed_icc_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "invalid.icc"
            profile.write_bytes(b"not an ICC profile")
            environ = {
                "PAPERTRAIL_GHOSTSCRIPT": "ghostscript",
                "PAPERTRAIL_VERAPDF": "verapdf",
                "PAPERTRAIL_PDFA_ICC_PROFILE": str(profile),
                "PAPERTRAIL_PDFA_OUTPUT_CONDITION": "test",
            }
            with patch.dict(os.environ, environ, clear=True), patch(
                "app.pdf_tools.shutil.which", side_effect=lambda name: name
            ):
                with self.assertRaisesRegex(ToolError, "readable RGB ICC profile"):
                    convert_pdfa("source.pdf", make_pdf("sample"))

    def test_browser_only_tools_cannot_be_submitted_to_processing_endpoint(self):
        client = TestClient(app)
        for tool in BROWSER_ONLY_TOOL_IDS:
            with self.subTest(tool=tool):
                response = client.post(
                    f"/api/process/{tool}",
                    headers={"origin": "http://localhost:8000"},
                    files={"files": ("document.pdf", make_pdf("sample"), "application/pdf")},
                )
                self.assertEqual(response.status_code, 404)

    def test_api_tagging_inspection_and_reading_order(self):
        client = TestClient(app)
        source = make_pdf("first page", "second page")
        tagged = client.post(
            "/api/process/tag-pdf",
            headers={"origin": "http://localhost:8000"},
            files={"files": ("source.pdf", source, "application/pdf")},
            data={"document_language": "en-GB"},
        )
        self.assertEqual(tagged.status_code, 200, tagged.text)

        inspected = client.post(
            "/api/inspect",
            headers={"origin": "http://localhost:8000"},
            files={"file": ("tagged.pdf", tagged.content, "application/pdf")},
        )
        self.assertEqual(inspected.status_code, 200, inspected.text)
        self.assertEqual(inspected.json()["reading_order_count"], 2)

        reordered = client.post(
            "/api/process/reading-order",
            headers={"origin": "http://localhost:8000"},
            files={"files": ("tagged.pdf", tagged.content, "application/pdf")},
            data={"structure_order": "2, 1"},
        )
        self.assertEqual(reordered.status_code, 200, reordered.text)
        with pikepdf.Pdf.open(io.BytesIO(reordered.content)) as document:
            page_objects = [page.obj.objgen for page in document.pages]
            children = list(document.Root.StructTreeRoot.K.K)
            self.assertEqual([child.Pg.objgen for child in children], [page_objects[1], page_objects[0]])

    def test_unincluded_tools_are_not_advertised_or_available(self):
        client = TestClient(app)
        excluded_tools = {"ocr-text", "ocr-searchable", "ocr-languages", "pdfa"}
        catalog = client.get("/api/tools")
        status = client.get("/api/status")
        self.assertEqual(catalog.status_code, 200)
        self.assertEqual(status.status_code, 200)
        catalog_ids = {tool["id"] for tool in catalog.json()}
        ready_ids = set(status.json()["ready_tools"])
        self.assertTrue(excluded_tools.isdisjoint(catalog_ids))
        self.assertTrue(excluded_tools.isdisjoint(ready_ids))
        self.assertNotIn("ocr_available", status.json())
        self.assertNotIn("pdfa_available", status.json())

        for tool in excluded_tools:
            with self.subTest(tool=tool):
                response = client.post(
                    f"/api/process/{tool}",
                    headers={"origin": "http://localhost:8000"},
                    files={"files": ("source.pdf", make_pdf("sample"), "application/pdf")},
                )
                self.assertEqual(response.status_code, 404)

        self.assertEqual(client.get("/api/ocr-languages").status_code, 404)

    def test_basic_structure_tags_preserve_pages_and_support_page_reading_order(self):
        source = make_pdf("first page", "second page")
        self.assertEqual(reading_order_entry_count("source.pdf", source), 0)
        tagged = tag_pdf_structure("source.pdf", source, "en-US")
        self.assertEqual(reading_order_entry_count("tagged.pdf", tagged.data), 2)

        with pikepdf.Pdf.open(io.BytesIO(tagged.data)) as document:
            root = document.Root.StructTreeRoot
            self.assertTrue(document.Root.MarkInfo.Marked)
            self.assertEqual(str(document.Root.Lang), "en-US")
            self.assertEqual(root.K.S, pikepdf.Name.Document)
            page_elements = list(root.K.K)
            self.assertEqual(len(page_elements), 2)
            self.assertTrue(all(element.S == pikepdf.Name.P for element in page_elements))
            self.assertTrue(all(element.P.S == pikepdf.Name.Document for element in page_elements))

        reordered = set_reading_order("tagged.pdf", tagged.data, "2, 1")
        self.assertEqual(reading_order_entry_count("reordered.pdf", reordered.data), 2)
        with pikepdf.Pdf.open(io.BytesIO(reordered.data)) as document:
            children = list(document.Root.StructTreeRoot.K.K)
            page_objects = [page.obj.objgen for page in document.pages]
            self.assertEqual([child.Pg.objgen for child in children], [page_objects[1], page_objects[0]])
        visual_document = fitz.open(stream=reordered.data, filetype="pdf")
        self.assertEqual(visual_document[0].get_text().strip(), "first page")
        self.assertEqual(visual_document[1].get_text().strip(), "second page")
        visual_document.close()

    def test_basic_structure_tags_reject_existing_or_unreadable_structure(self):
        with self.assertRaisesRegex(ToolError, "valid document language"):
            tag_pdf_structure("source.pdf", make_pdf("text"), "not a language tag")
        blank = fitz.open()
        blank.new_page()
        blank_data = blank.tobytes()
        blank.close()
        with self.assertRaisesRegex(ToolError, "selectable text on every page"):
            tag_pdf_structure("blank.pdf", blank_data, "en")
        tagged = tag_pdf_structure("source.pdf", make_pdf("text"), "en")
        with self.assertRaisesRegex(ToolError, "does not replace existing"):
            tag_pdf_structure("tagged.pdf", tagged.data, "en")
        with self.assertRaisesRegex(ToolError, "requires a PDF tagged"):
            set_reading_order("source.pdf", make_pdf("text"), "1")
        with self.assertRaisesRegex(ToolError, "each page"):
            set_reading_order("tagged.pdf", tagged.data, "1, 1")

    def test_pdfa_setup_requires_ghostscript_verapdf_and_icc_profile(self):
        with patch.dict(os.environ, {}, clear=True), patch("app.pdf_tools.shutil.which", return_value=None):
            with self.assertRaisesRegex(ToolError, "Ghostscript.*veraPDF.*ICC profile") as error:
                convert_pdfa("source.pdf", make_pdf("sample"))
        self.assertEqual(error.exception.status_code, 503)

    def test_pdfa_conversion_requires_a_passing_verapdf_report(self):
        source = make_pdf("PDF/A conversion")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ghostscript = root / "gs.exe"
            ghostscript.touch()
            icc_profile = root / "srgb.icc"
            profile_bytes = bytearray(128)
            profile_bytes[0:4] = (128).to_bytes(4, "big")
            profile_bytes[16:20] = b"RGB "
            profile_bytes[36:40] = b"acsp"
            icc_profile.write_bytes(profile_bytes)
            environ = {
                "PAPERTRAIL_GHOSTSCRIPT": str(ghostscript),
                "PAPERTRAIL_VERAPDF": "verapdf",
                "PAPERTRAIL_PDFA_ICC_PROFILE": str(icc_profile),
                "PAPERTRAIL_PDFA_OUTPUT_CONDITION": "sRGB (test)",
            }
            definition_text = {}

            def fake_run(command, **kwargs):
                if command[0] == str(ghostscript):
                    output_path = Path(next(argument.split("=", 1)[1] for argument in command if argument.startswith("-sOutputFile=")))
                    output_path.write_bytes(source)
                    definition_path = Path(next(argument for argument in command if argument.endswith("PDFA_def.ps")))
                    definition_text["value"] = definition_path.read_text(encoding="ascii")
                    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout='<report><jobs><job><validationReport isCompliant="true"/></job></jobs></report>',
                    stderr="",
                )

            with patch.dict(os.environ, environ, clear=True), patch(
                "app.pdf_tools.shutil.which", side_effect=lambda name: str(root / "verapdf.exe") if name == "verapdf" else None
            ), patch("app.pdf_tools.subprocess.run", side_effect=fake_run) as run:
                result = convert_pdfa("source.pdf", source)
                self.assertEqual(result.filename, "source-pdfa-2b.pdf")
                self.assertTrue(pdfa_setup_available())
                self.assertEqual(run.call_count, 2)
                gs_arguments = run.call_args_list[0].args[0]
                self.assertIn("-dPDFA=2", gs_arguments)
                self.assertIn("-dPDFACompatibilityPolicy=2", gs_arguments)
                self.assertIn("/OutputConditionIdentifier (sRGB \\(test\\))", definition_text["value"])
                self.assertIn("-f", run.call_args_list[1].args[0])

            def fake_noncompliant_run(command, **kwargs):
                if command[0] == str(ghostscript):
                    output_path = Path(next(argument.split("=", 1)[1] for argument in command if argument.startswith("-sOutputFile=")))
                    output_path.write_bytes(source)
                    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout='<report><jobs><job><validationReport isCompliant="false"/></job></jobs></report>',
                    stderr="",
                )

            with patch.dict(os.environ, environ, clear=True), patch(
                "app.pdf_tools.shutil.which", side_effect=lambda name: str(root / "verapdf.exe") if name == "verapdf" else None
            ), patch("app.pdf_tools.subprocess.run", side_effect=fake_noncompliant_run):
                with self.assertRaisesRegex(ToolError, "did not pass veraPDF"):
                    convert_pdfa("source.pdf", source)

    def test_api_exposes_exact_batch_workflow_options(self):
        from app.pdf_tools import BATCH_OPTION_KEYS, BATCH_TOOLS

        response = TestClient(app).get("/api/status")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["max_concurrent_jobs"], MAX_CONCURRENT_JOBS)
        self.assertEqual(set(payload["workflow_tools"]), BATCH_TOOLS)
        self.assertEqual(
            {tool: set(options) for tool, options in payload["workflow_options"].items()},
            BATCH_OPTION_KEYS,
        )

    def test_parse_page_groups_accepts_multiple_outputs(self):
        self.assertEqual(parse_page_groups("1-2, 4; 5", 5), [[0, 1, 3], [4]])

    def test_parse_page_groups_rejects_pages_outside_document(self):
        with self.assertRaisesRegex(ToolError, "between 1 and 3"):
            parse_page_groups("1-4", 3)

    def test_parse_page_order_requires_every_page_once(self):
        self.assertEqual(parse_page_order("3, 1, 2", 3), [2, 0, 1])
        with self.assertRaisesRegex(ToolError, "each page"):
            parse_page_order("3, 1, 1", 3)

    def test_merge_combines_documents_in_input_order(self):
        result = merge_pdfs([("first.pdf", make_pdf("first")), ("second.pdf", make_pdf("second"))])
        document = fitz.open(stream=result.data, filetype="pdf")
        self.assertEqual(document.page_count, 2)
        self.assertIn("first", document[0].get_text())
        self.assertIn("second", document[1].get_text())
        document.close()

    def test_split_creates_one_pdf_per_page_group(self):
        result = split_pdf("report.pdf", make_pdf("one", "two", "three"), "1-2; 3")
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            self.assertEqual(archive.namelist(), ["report-part-1.pdf", "report-part-2.pdf"])
            first = fitz.open(stream=archive.read(archive.namelist()[0]), filetype="pdf")
            second = fitz.open(stream=archive.read(archive.namelist()[1]), filetype="pdf")
            self.assertEqual(first.page_count, 2)
            self.assertEqual(second.page_count, 1)
            first.close()
            second.close()

    def test_rotate_changes_each_page_rotation(self):
        result = rotate_pdf("report.pdf", make_pdf("one", "two"), 90)
        document = fitz.open(stream=result.data, filetype="pdf")
        self.assertEqual([page.rotation for page in document], [90, 90])
        document.close()

    def test_reorder_changes_page_order(self):
        result = reorder_pdf("report.pdf", make_pdf("one", "two", "three"), "3,1,2")
        document = fitz.open(stream=result.data, filetype="pdf")
        self.assertEqual(
            [page.get_text().strip() for page in document],
            ["three", "one", "two"],
        )
        document.close()

    def test_extract_keeps_selected_pages_in_entered_order(self):
        result = extract_pages_pdf("report.pdf", make_pdf("one", "two", "three"), "3, 1")
        document = fitz.open(stream=result.data, filetype="pdf")
        self.assertEqual([page.get_text().strip() for page in document], ["three", "one"])
        document.close()

    def test_delete_pages_refuses_to_empty_document(self):
        with self.assertRaisesRegex(ToolError, "leave the PDF empty"):
            delete_pages_pdf("report.pdf", make_pdf("one"), "1")

    def test_delete_pages_removes_only_selected_pages(self):
        result = delete_pages_pdf("report.pdf", make_pdf("one", "two", "three"), "2")
        document = fitz.open(stream=result.data, filetype="pdf")
        self.assertEqual([page.get_text().strip() for page in document], ["one", "three"])
        document.close()

    def test_duplicate_pages_inserts_copy_after_original(self):
        result = duplicate_pages_pdf("report.pdf", make_pdf("one", "two"), "1")
        document = fitz.open(stream=result.data, filetype="pdf")
        self.assertEqual([page.get_text().strip() for page in document], ["one", "one", "two"])
        document.close()

    def test_reverse_pages_reverses_order(self):
        result = reverse_pages_pdf("report.pdf", make_pdf("one", "two", "three"))
        document = fitz.open(stream=result.data, filetype="pdf")
        self.assertEqual([page.get_text().strip() for page in document], ["three", "two", "one"])
        document.close()

    def test_insert_blank_adds_pages_after_selections(self):
        result = insert_blank_pages_pdf("report.pdf", make_pdf("one", "two"), "1")
        document = fitz.open(stream=result.data, filetype="pdf")
        self.assertEqual(document.page_count, 3)
        self.assertEqual([page.get_text().strip() for page in document], ["one", "", "two"])
        document.close()

    def test_page_labels_are_saved_as_pdf_metadata(self):
        result = set_page_labels_pdf("report.pdf", make_pdf("one", "two"), "Appendix ", "R", 1)
        document = fitz.open(stream=result.data, filetype="pdf")
        self.assertEqual([item["prefix"] for item in document.get_page_labels()], ["Appendix "])
        self.assertEqual([document[i].get_label() for i in range(2)], ["Appendix I", "Appendix II"])
        document.close()

    def test_crop_and_resize_preserve_pages(self):
        source = make_pdf("one")
        cropped = crop_pages_pdf("report.pdf", source, 12)
        crop_doc = fitz.open(stream=cropped.data, filetype="pdf")
        original = fitz.open(stream=source, filetype="pdf")
        self.assertAlmostEqual(crop_doc[0].rect.width, original[0].rect.width - 24, places=1)
        crop_doc.close()
        original.close()
        resized = resize_pages_pdf("report.pdf", source, "letter")
        resize_doc = fitz.open(stream=resized.data, filetype="pdf")
        self.assertEqual(round(resize_doc[0].rect.width), 612)
        resize_doc.close()

    def test_pdf_to_images_returns_png_zip(self):
        result = pdf_to_images("report.pdf", make_pdf("one"), 72)
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            self.assertEqual(archive.namelist(), ["report-page-0001.png"])
            self.assertTrue(archive.read(archive.namelist()[0]).startswith(b"\x89PNG"))

    def test_raster_operations_reject_excessive_pixel_work_before_rendering(self):
        document = fitz.open()
        document.new_page(width=20_000, height=20_000)
        oversized_pdf = document.tobytes()
        document.close()

        with patch("app.pdf_tools.fitz.Page.get_pixmap") as render_page:
            operations = (
                lambda: pdf_to_images("large.pdf", oversized_pdf, 120),
                lambda: grayscale_pdf("large.pdf", oversized_pdf, 120),
                lambda: deskew_pdf("large.pdf", oversized_pdf),
                lambda: deduplicate_pages_pdf("large.pdf", oversized_pdf),
            )
            for operation in operations:
                with self.subTest(operation=operation):
                    with self.assertRaisesRegex(ToolError, "8 million rendered pixels per page"):
                        operation()
            render_page.assert_not_called()

        many_pages = make_pdf(*(["sample"] * 11))
        with self.assertRaisesRegex(ToolError, "40 million rendered pixels per operation"):
            pdf_to_images("many-pages.pdf", many_pages, 200)

    def test_pdf_to_text_extracts_page_text(self):
        result = pdf_to_text("report.pdf", make_pdf("sample words"))
        self.assertIn(b"sample words", result.data)

    def test_pdf_to_markdown_preserves_page_boundaries(self):
        result = pdf_to_markdown("report.pdf", make_pdf("sample words", "another page"))
        self.assertIn(b"## Page 1", result.data)
        self.assertIn(b"sample words", result.data)
        self.assertIn(b"## Page 2", result.data)

    def test_accessible_text_export_includes_page_headings_and_text(self):
        result = accessible_text_export("report.pdf", make_pdf("reading order text"))
        self.assertIn(b"Page 1", result.data)
        self.assertIn(b"reading order text", result.data)

    def test_compress_embedded_images_keeps_pdf_readable(self):
        document = fitz.open()
        page = document.new_page(width=72, height=72)
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 500, 500), False)
        pixmap.clear_with(180)
        page.insert_image(page.rect, pixmap=pixmap)
        source = document.tobytes()
        document.close()
        result = compress_images_pdf("scanned.pdf", source, 96, 60)
        optimized = fitz.open(stream=result.data, filetype="pdf")
        self.assertEqual(optimized.page_count, 1)
        self.assertTrue(optimized[0].get_images())
        optimized.close()

    def test_stamp_adds_selected_text_and_rejects_bad_position(self):
        result = stamp_pdf("report.pdf", make_pdf("sample"), "APPROVED", "top-right")
        document = fitz.open(stream=result.data, filetype="pdf")
        self.assertIn("APPROVED", document[0].get_text())
        document.close()
        with self.assertRaisesRegex(ToolError, "stamp position"):
            stamp_pdf("report.pdf", make_pdf("sample"), "APPROVED", "outside")

    def test_text_and_markdown_conversion_create_pdfs(self):
        plain = text_to_pdf("notes.txt", b"first line\nsecond line")
        markdown = text_to_pdf("notes.md", b"# Heading\n\n**bold** and [link](https://example.test)", markdown=True)
        for result in (plain, markdown):
            document = fitz.open(stream=result.data, filetype="pdf")
            self.assertEqual(document.page_count, 1)
            self.assertTrue(document[0].get_text().strip())
            document.close()
        self.assertEqual(plain_text_from_markdown("# Heading\n- one\n\n**bold**"), "Heading\n• one\n\nbold")

    def test_html_conversion_keeps_text_and_excludes_active_content(self):
        source = b"<h1>Report</h1><p>Hello <b>world</b></p><script>do not include</script>"
        result = html_to_pdf("report.html", source)
        document = fitz.open(stream=result.data, filetype="pdf")
        text = "\n".join(page.get_text() for page in document)
        self.assertIn("Report", text)
        self.assertIn("Hello", text)
        self.assertNotIn("do not include", text)
        document.close()
        with self.assertRaisesRegex(ToolError, "10 MiB"):
            html_to_pdf("large.html", b" " * (10 * 1024 * 1024 + 1))

    def test_epub_conversion_follows_spine_and_limits_archive_size(self):
        source = io.BytesIO()
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
            archive.writestr(
                "OPS/book.opf",
                '<package><manifest><item id="chapter" href="chapter.xhtml"/></manifest><spine><itemref idref="chapter"/></spine></package>',
            )
            archive.writestr("OPS/chapter.xhtml", "<h1>Chapter One</h1><p>Book contents</p>")
        result = epub_to_pdf("book.epub", source.getvalue())
        document = fitz.open(stream=result.data, filetype="pdf")
        self.assertIn("Chapter One", document[0].get_text())
        self.assertIn("Book contents", document[0].get_text())
        document.close()
        with self.assertRaisesRegex(ToolError, "could not be read"):
            epub_to_pdf("broken.epub", b"not a zip")

    def test_pdf_and_docx_text_conversion_round_trip(self):
        source_pdf = make_pdf("Editable Word text")
        docx = pdf_to_word_docx("report.pdf", source_pdf)
        with zipfile.ZipFile(io.BytesIO(docx.data)) as archive:
            xml = archive.read("word/document.xml")
            self.assertIn(b"Editable Word text", xml)
        word_output = word_to_pdf("report.docx", docx.data)
        document = fitz.open(stream=word_output.data, filetype="pdf")
        self.assertIn("Editable Word text", document[0].get_text())
        document.close()

    def test_pdf_and_xlsx_text_conversion_round_trip(self):
        workbook = pdf_to_excel_xlsx("report.pdf", make_pdf("First spreadsheet row", "Second row"))
        with zipfile.ZipFile(io.BytesIO(workbook.data)) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml")
            self.assertIn(b"First spreadsheet row", sheet)
            self.assertIn(b"Second row", sheet)
        converted = excel_to_pdf("report.xlsx", workbook.data)
        document = fitz.open(stream=converted.data, filetype="pdf")
        text = "\n".join(page.get_text() for page in document)
        self.assertIn("First spreadsheet row", text)
        self.assertIn("Second row", text)
        document.close()

    def test_linearized_and_validated_outputs(self):
        source = make_pdf("sample")
        optimized = linearize_pdf("report.pdf", source)
        doc = fitz.open(stream=optimized.data, filetype="pdf")
        self.assertEqual(doc.page_count, 1)
        doc.close()
        report = validate_pdf_report("report.pdf", source)
        self.assertIn(b"opened successfully", report.data)

    def test_pdfua_report_discloses_basic_check_scope(self):
        report = pdfua_accessibility_report("report.pdf", make_pdf("visible text"))
        self.assertIn(b"not a PDF/UA conformance validation", report.data)
        self.assertIn(b"REVIEW: Tagged structure tree", report.data)
        self.assertIn(b"PASS: Selectable text on every page", report.data)

    def test_batch_runs_validated_pdf_operations_in_order(self):
        source = make_pdf("batch content")
        result = run_tool(
            "batch",
            [("report.pdf", source)],
            batch_steps='[{"tool":"rotate","options":{"rotation":90}},{"tool":"watermark","options":{"watermark":"DRAFT"}}]',
        )
        document = fitz.open(stream=result.data, filetype="pdf")
        self.assertEqual(document[0].rotation, 90)
        self.assertIn("DRAFT", document[0].get_text())
        document.close()
        with self.assertRaisesRegex(ToolError, "unsupported PDF-to-PDF"):
            run_tool("batch", [("report.pdf", source)], batch_steps='[{"tool":"merge"}]')
        with self.assertRaisesRegex(ToolError, "wrong type"):
            run_tool("batch", [("report.pdf", source)], batch_steps='[{"tool":"rotate","options":{"rotation":"90"}}]')

    def test_api_batch_endpoint_runs_workflow_steps(self):
        response = TestClient(app).post(
            "/api/process/batch",
            headers={"origin": "http://localhost:8000"},
            files={"files": ("report.pdf", make_pdf("workflow content"), "application/pdf")},
            data={"batch_steps": '[{"tool":"watermark","options":{"watermark":"DRAFT"}},{"tool":"rotate","options":{"rotation":90}}]'},
        )
        self.assertEqual(response.status_code, 200, response.text)
        document = fitz.open(stream=response.content, filetype="pdf")
        self.assertIn("workflow content", document[0].get_text())
        self.assertIn("DRAFT", document[0].get_text())
        self.assertEqual(round(document[0].rect.width), 842)
        document.close()

    def test_grayscale_conversion_is_raster_pdf(self):
        result = grayscale_pdf("report.pdf", make_pdf("sample"), 72)
        document = fitz.open(stream=result.data, filetype="pdf")
        self.assertEqual(document.page_count, 1)
        self.assertTrue(document[0].get_images())
        self.assertFalse(document[0].get_text().strip())
        document.close()

    def test_scan_cleanup_tools_return_rasterized_pdfs(self):
        source = make_pdf("scanned page")
        for result in (deskew_pdf("scan.pdf", source), enhance_scan_pdf("scan.pdf", source)):
            document = fitz.open(stream=result.data, filetype="pdf")
            self.assertEqual(document.page_count, 1)
            self.assertTrue(document[0].get_images())
            self.assertFalse(document[0].get_text().strip())
            document.close()

    def test_deduplicate_removes_visually_identical_pages(self):
        result = deduplicate_pages_pdf("report.pdf", make_pdf("same", "same", "other"))
        document = fitz.open(stream=result.data, filetype="pdf")
        self.assertEqual(document.page_count, 2)
        document.close()

    def test_watermark_and_footer_add_visible_text(self):
        source = make_pdf("sample")
        watermarked = watermark_pdf("report.pdf", source, "DRAFT")
        doc = fitz.open(stream=watermarked.data, filetype="pdf")
        self.assertIn("DRAFT", doc[0].get_text())
        doc.close()
        numbered = page_numbers_pdf("report.pdf", source, 4)
        doc = fitz.open(stream=numbered.data, filetype="pdf")
        self.assertIn("4 / 4", doc[0].get_text())
        doc.close()
        header_footer = add_headers_footers_pdf("report.pdf", source, "Header", "Footer", False)
        doc = fitz.open(stream=header_footer.data, filetype="pdf")
        self.assertIn("Header", doc[0].get_text())
        self.assertIn("Footer", doc[0].get_text())
        doc.close()

    def test_edit_and_remove_metadata(self):
        source = make_pdf("sample")
        edited = update_metadata_pdf("report.pdf", source, {"title": "Quarterly report", "author": "Alex"})
        doc = fitz.open(stream=edited.data, filetype="pdf")
        self.assertEqual(doc.metadata["title"], "Quarterly report")
        self.assertEqual(doc.metadata["author"], "Alex")
        doc.close()
        stripped = remove_metadata_pdf("report.pdf", edited.data)
        doc = fitz.open(stream=stripped.data, filetype="pdf")
        self.assertFalse(doc.metadata["title"])
        doc.close()

    def test_fill_and_flatten_text_form_fields(self):
        source = make_form_pdf()
        filled = fill_form_pdf("form.pdf", source, "full_name=Ada Lovelace")
        document = fitz.open(stream=filled.data, filetype="pdf")
        widget = next(document[0].widgets())
        self.assertEqual(widget.field_value, "Ada Lovelace")
        document.close()
        flattened = flatten_forms_pdf("form.pdf", filled.data)
        document = fitz.open(stream=flattened.data, filetype="pdf")
        self.assertEqual(list(document[0].widgets() or ()), [])
        self.assertIn("Ada Lovelace", document[0].get_text())
        document.close()
        with self.assertRaisesRegex(ToolError, "Unknown form field"):
            fill_form_pdf("form.pdf", source, "unknown=value")

    def test_inspect_api_reports_form_field_names(self):
        client = TestClient(app)
        response = client.post(
            "/api/inspect",
            headers={"origin": "http://localhost:8000"},
            files={"file": ("form.pdf", make_form_pdf(), "application/pdf")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["fields"][0]["name"], "full_name")

    def test_extract_and_remove_embedded_attachments(self):
        document = fitz.open(stream=make_pdf("sample"), filetype="pdf")
        document.embfile_add("notes.txt", b"attachment contents")
        source = document.tobytes()
        document.close()
        extracted = extract_attachments_zip("report.pdf", source)
        with zipfile.ZipFile(io.BytesIO(extracted.data)) as archive:
            self.assertEqual(archive.read("notes.txt"), b"attachment contents")
        stripped = remove_attachments_pdf("report.pdf", source)
        document = fitz.open(stream=stripped.data, filetype="pdf")
        self.assertEqual(document.embfile_names(), [])
        document.close()

    def test_sanitization_removes_metadata_and_embedded_files(self):
        source_doc = fitz.open(stream=make_pdf("keep visible content"), filetype="pdf")
        source_doc.set_metadata({"title": "private title"})
        source_doc.embfile_add("secret.txt", b"private attachment")
        source = source_doc.tobytes()
        source_doc.close()
        result = remove_hidden_content_pdf("report.pdf", source)
        document = fitz.open(stream=result.data, filetype="pdf")
        self.assertIn("keep visible content", document[0].get_text())
        self.assertFalse(document.metadata["title"])
        self.assertEqual(document.embfile_names(), [])
        document.close()

    def test_image_conversion_creates_a_pdf(self):
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 32, 24), False)
        pixmap.clear_with(240)
        image_bytes = pixmap.tobytes("png")
        result = images_to_pdf([("scan.png", image_bytes)])
        document = fitz.open(stream=result.data, filetype="pdf")
        self.assertEqual(document.page_count, 1)
        self.assertEqual(round(document[0].rect.width), 24)
        self.assertEqual(round(document[0].rect.height), 18)
        document.close()

    def test_image_conversion_rejects_excessive_pixel_work(self):
        image_buffer = io.BytesIO()
        Image.new("RGB", (3_000, 3_000)).save(image_buffer, format="PNG")
        with self.assertRaisesRegex(ToolError, "8 million pixels per page"):
            images_to_pdf([("large.png", image_buffer.getvalue())])

    def test_compression_returns_a_valid_pdf_and_size_metadata(self):
        source = make_pdf("sample")
        result = compress_pdf("sample.pdf", source)
        document = fitz.open(stream=result.data, filetype="pdf")
        self.assertEqual(document.page_count, 1)
        self.assertEqual(result.input_bytes, len(source))
        document.close()

    def test_ocr_reports_missing_server_engine(self):
        with patch("app.pdf_tools.shutil.which", return_value=None):
            with self.assertRaisesRegex(ToolError, "Tesseract installed on the processing server") as error:
                ocr_to_text("scan.pdf", make_pdf("sample"), "eng")
        self.assertEqual(error.exception.status_code, 503)

    def test_searchable_ocr_uses_tesseract_pdf_output(self):
        source = make_pdf("source page")
        tesseract_pdf = make_pdf("recognized words")
        with patch("app.pdf_tools.shutil.which", return_value="tesseract.exe"), patch(
            "app.pdf_tools.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout=tesseract_pdf, stderr=b""),
        ) as run:
            result = ocr_searchable_pdf("scan.pdf", source, "eng")
        self.assertIn("pdf", run.call_args.args[0])
        document = fitz.open(stream=result.data, filetype="pdf")
        self.assertIn("recognized words", document[0].get_text())
        document.close()

    def test_searchable_ocr_enforces_page_limit_and_setup(self):
        document = fitz.open()
        for _ in range(101):
            document.new_page()
        source = document.tobytes()
        document.close()
        with patch("app.pdf_tools.shutil.which", return_value=None):
            with self.assertRaisesRegex(ToolError, "Tesseract installed"):
                ocr_searchable_pdf("scan.pdf", source, "eng")
        with patch("app.pdf_tools.shutil.which", return_value="tesseract.exe"):
            with self.assertRaisesRegex(ToolError, "limited to 100 pages"):
                ocr_searchable_pdf("scan.pdf", source, "eng")

    def test_installed_ocr_languages_are_reported(self):
        result = subprocess.CompletedProcess(
            [],
            0,
            stdout="List of available languages in C:\\Tesseract:\neng\nhin\n",
            stderr="",
        )
        with patch("app.pdf_tools.shutil.which", return_value="tesseract.exe"), patch(
            "app.pdf_tools.subprocess.run", return_value=result
        ):
            self.assertEqual(list_ocr_languages(), ["eng", "hin"])

    def test_ocr_language_api_is_not_exposed(self):
        client = TestClient(app)
        self.assertEqual(client.get("/api/ocr-languages").status_code, 404)

    def test_svg_conversion_and_pdf_repair(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"><rect width="100" height="50" fill="blue"/></svg>'
        converted = svg_to_pdf("graphic.svg", svg)
        document = fitz.open(stream=converted.data, filetype="pdf")
        self.assertEqual(document.page_count, 1)
        document.close()
        repaired = repair_pdf("sample.pdf", make_pdf("repair me"))
        document = fitz.open(stream=repaired.data, filetype="pdf")
        self.assertIn("repair me", document[0].get_text())
        document.close()

    def test_pdf_comparisons_produce_reports(self):
        files = [("original.pdf", make_pdf("old text")), ("revised.pdf", make_pdf("new text"))]
        text_report = compare_text_pdfs(files)
        self.assertIn(b"-old text", text_report.data)
        self.assertIn(b"+new text", text_report.data)
        side_by_side = compare_page_renderings(files, visual_diff=False)
        visual = compare_page_renderings(files, visual_diff=True)
        for result in (side_by_side, visual):
            document = fitz.open(stream=result.data, filetype="pdf")
            self.assertEqual(document.page_count, 1)
            document.close()

    def test_highlight_and_redaction_apply_matching_text(self):
        source = make_pdf("secret marker")
        highlighted = highlight_text_pdf("sample.pdf", source, "secret")
        document = fitz.open(stream=highlighted.data, filetype="pdf")
        self.assertIsNotNone(document[0].first_annot)
        document.close()
        redacted = redact_text_pdf("sample.pdf", source, "secret")
        document = fitz.open(stream=redacted.data, filetype="pdf")
        self.assertNotIn("secret", document[0].get_text())
        document.close()
        with self.assertRaisesRegex(ToolError, "No matching text"):
            redact_text_pdf("sample.pdf", source, "not present")

    def test_encryption_and_decryption_round_trip(self):
        protected = encrypt_pdf("sample.pdf", make_pdf("protected content"), "user-pass-1", "owner-pass-1", "copy")
        document = fitz.open(stream=protected.data, filetype="pdf")
        self.assertTrue(document.is_encrypted)
        self.assertTrue(document.authenticate("user-pass-1"))
        document.close()
        unlocked = decrypt_pdf("sample-protected.pdf", protected.data, "user-pass-1")
        document = fitz.open(stream=unlocked.data, filetype="pdf")
        self.assertFalse(document.is_encrypted)
        self.assertIn("protected content", document[0].get_text())
        document.close()

    def test_api_comparison_accepts_two_uploaded_pdfs(self):
        client = TestClient(app)
        response = client.post(
            "/api/process/compare-text",
            headers={"origin": "http://localhost:8000"},
            files=[
                ("files", ("original.pdf", make_pdf("old text"), "application/pdf")),
                ("files", ("revised.pdf", make_pdf("new text"), "application/pdf")),
            ],
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(b"-old text", response.content)
        self.assertIn(b"+new text", response.content)

    def test_slide_conversions_process_uploads(self):
        presentation = make_pptx("Slide one content", "Second slide")
        converted = slides_to_pdf("deck.pptx", presentation)
        document = fitz.open(stream=converted.data, filetype="pdf")
        extracted = "\n".join(page.get_text() for page in document)
        document.close()
        self.assertIn("Slide one content", extracted)
        self.assertIn("Second slide", extracted)

        slides = pdf_to_slides("report.pdf", make_pdf("PDF page one", "PDF page two"))
        parsed_presentation = Presentation(io.BytesIO(slides.data))
        self.assertEqual(len(parsed_presentation.slides), 2)
        self.assertTrue(all(any(shape.shape_type == 13 for shape in slide.shapes) for slide in parsed_presentation.slides))

    def test_slide_conversions_reject_unsupported_input(self):
        with self.assertRaisesRegex(ToolError, r"\.pptx"):
            slides_to_pdf("deck.ppt", b"not a presentation")
        with self.assertRaisesRegex(ToolError, r"\.pdf"):
            run_tool("pdf-to-slides", [("image.png", b"image")])

    def test_api_pdf_to_slides_returns_presentation(self):
        client = TestClient(app)
        response = client.post(
            "/api/process/pdf-to-slides",
            headers={"origin": "http://localhost:8000"},
            files={"files": ("report.pdf", make_pdf("first page"), "application/pdf")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.headers["content-type"],
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
        self.assertEqual(len(Presentation(io.BytesIO(response.content)).slides), 1)
        converted = client.post(
            "/api/process/slides-to-pdf",
            headers={"origin": "http://localhost:8000"},
            files={"files": ("deck.pptx", make_pptx("slide text"), "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
        )
        self.assertEqual(converted.status_code, 200, converted.text)
        self.assertIn("slide text", fitz.open(stream=converted.content, filetype="pdf")[0].get_text())

    def test_api_encrypt_and_decrypt_password_fields(self):
        client = TestClient(app)
        encrypted = client.post(
            "/api/process/encrypt",
            headers={"origin": "http://localhost:8000"},
            files={"files": ("original.pdf", make_pdf("secret text"), "application/pdf")},
            data={"user_password": "user-pass-1", "owner_password": "owner-pass-1", "profile": "copy"},
        )
        self.assertEqual(encrypted.status_code, 200, encrypted.text)
        decrypted = client.post(
            "/api/process/decrypt",
            headers={"origin": "http://localhost:8000"},
            files={"files": ("protected.pdf", encrypted.content, "application/pdf")},
            data={"password": "user-pass-1"},
        )
        self.assertEqual(decrypted.status_code, 200, decrypted.text)
        document = fitz.open(stream=decrypted.content, filetype="pdf")
        self.assertIn("secret text", document[0].get_text())
        document.close()

    def test_api_sign_certify_and_verify_with_per_request_certificate(self):
        client = TestClient(app)
        certificate = make_pkcs12_certificate()
        invalid_certificate = client.post(
            "/api/process/sign",
            headers={"origin": "http://localhost:8000"},
            files=[
                ("files", ("original.pdf", make_pdf("signed content"), "application/pdf")),
                ("files", ("invalid.p12", b"not a certificate", "application/x-pkcs12")),
            ],
        )
        self.assertEqual(invalid_certificate.status_code, 400, invalid_certificate.text)
        self.assertIn("certificate or password is invalid", invalid_certificate.text)

        signed = client.post(
            "/api/process/sign",
            headers={"origin": "http://localhost:8000"},
            files=[
                ("files", ("original.pdf", make_pdf("signed content"), "application/pdf")),
                ("files", ("signer.p12", certificate, "application/x-pkcs12")),
            ],
            data={
                "certificate_password": "certificate-secret",
                "signature_field": "Signature1",
                "signature_reason": "Test signing",
            },
        )
        self.assertEqual(signed.status_code, 200, signed.text)
        verified = client.post(
            "/api/process/verify-signature",
            headers={"origin": "http://localhost:8000"},
            files={"files": ("signed.pdf", signed.content, "application/pdf")},
        )
        self.assertEqual(verified.status_code, 200, verified.text)
        self.assertIn(b"Cryptographic integrity: valid", verified.content)
        self.assertIn(b"Certificate trusted by configured roots: no or unknown", verified.content)

        certified = sign_pdf(
            [("original.pdf", make_pdf("certified content")), ("signer.pfx", certificate)],
            "certificate-secret",
            "Certification1",
            "Certified",
            certify=True,
        )
        self.assertIn(b"Signatures found: 1", verify_signature_pdf("certified.pdf", certified.data).data)


if __name__ == "__main__":
    unittest.main()
