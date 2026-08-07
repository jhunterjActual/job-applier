import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import app as app_module
import materials as materials_module
import searcher as searcher_module
from fastapi.testclient import TestClient
from lifecycle import undo_latest_lifecycle_change, update_lifecycle
from materials import material_download_name, persist_cover_letter, resolve_output_file
from database import get_db_connection
from job_cleanup import apply_cleanup, cleanup_preview
from searcher import (
    _job_location_from_json_ld,
    _protect_browser_network,
    _structured_job_metadata,
    _metadata_from_text,
    canonicalize_job_url,
    is_specific_job_url,
    is_public_http_url,
    is_useful_job_details,
    provider_alerts_from_health,
    provider_for_url,
)
from tailor import (
    RESUME_MODE_GUIDANCE,
    RESUME_SECTION_TEMPLATES,
    TailoringResponse,
    apply_resume_section_template,
    finalize_cover_letter,
    tailor_resume_and_cover_letter,
)
from utils import _headquarters_query, _looks_like_us_address, find_us_headquarters, markdown_to_html


class ApplicationMaterialsSafetyTests(unittest.TestCase):
    def test_cover_letter_is_persisted_separately_as_utf8_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cover_path = Path(persist_cover_letter(12, "Dear Hiring Team,\n\nHello.", Path(temp_dir)))
            self.assertEqual("cover_letter_12.txt", cover_path.name)
            self.assertEqual("Dear Hiring Team,\n\nHello.\n", cover_path.read_text(encoding="utf-8"))
            self.assertEqual(cover_path, resolve_output_file(cover_path, ".txt", Path(temp_dir)))

    def test_material_resolution_rejects_files_outside_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as other_dir:
            outside = Path(other_dir) / "resume.pdf"
            outside.write_bytes(b"pdf")
            with self.assertRaisesRegex(ValueError, "invalid"):
                resolve_output_file(outside, ".pdf", Path(temp_dir))

    def test_download_names_are_safe_and_readable(self) -> None:
        filename = material_download_name("Example / Corp", "VP: Data & AI", "cover-letter", ".txt")
        self.assertEqual("example-corp-vp-data-ai-cover-letter.txt", filename)
        self.assertNotIn("/", filename)

    def test_cover_letter_date_placeholders_are_resolved(self) -> None:
        expected = "August 3, 2026"
        for placeholder in ("[Date]", "{DATE}", "<date>"):
            with self.subTest(placeholder=placeholder):
                result = finalize_cover_letter(
                    f"Candidate Address\n\n{placeholder}\n\nDear Hiring Team,",
                    date(2026, 8, 3),
                )
                self.assertIn(expected, result)
                self.assertNotIn(placeholder, result)

    def test_downloads_and_manual_open_do_not_mark_job_applied(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database_path = root / "materials.db"
            resume_path = root / "tailored_resume_1.pdf"
            resume_path.write_bytes(b"%PDF-test")
            cover_path = persist_cover_letter(1, "Dear Hiring Team,", root)

            connection = sqlite3.connect(database_path)
            connection.executescript("""
                CREATE TABLE jobs (
                    id INTEGER PRIMARY KEY, company TEXT, title TEXT, url TEXT,
                    source TEXT, status TEXT
                );
                CREATE TABLE applications (
                    id INTEGER PRIMARY KEY, job_id INTEGER, company TEXT, position TEXT,
                    tailored_resume_path TEXT, cover_letter_path TEXT, cover_letter TEXT,
                    status TEXT
                );
            """)
            connection.execute(
                "INSERT INTO jobs VALUES (1, ?, ?, ?, ?, ?)",
                ("Example Corp", "VP Data", "https://jobs.example.test/1", "lever", "tailored"),
            )
            connection.execute(
                "INSERT INTO applications VALUES (1, 1, ?, ?, ?, ?, ?, ?)",
                ("Example Corp", "VP Data", str(resume_path), cover_path, "Dear Hiring Team,", "tailored"),
            )
            connection.commit()
            connection.close()

            def connection_factory():
                test_connection = sqlite3.connect(database_path)
                test_connection.row_factory = sqlite3.Row
                return test_connection

            with (
                patch.object(app_module, "get_db_connection", connection_factory),
                patch.object(materials_module.config, "OUTPUT_DIR", root),
            ):
                resume_response = app_module.download_tailored_resume(1)
                cover_response = app_module.download_cover_letter(1)
                redirect_response = app_module.open_manual_application(1)

            self.assertIn("example-corp-vp-data-resume.pdf", resume_response.headers["content-disposition"])
            self.assertIn("example-corp-vp-data-cover-letter.txt", cover_response.headers["content-disposition"])
            self.assertEqual("https://jobs.example.test/1", redirect_response.headers["location"])
            connection = connection_factory()
            self.assertEqual("tailored", connection.execute("SELECT status FROM jobs WHERE id = 1").fetchone()[0])
            self.assertEqual("tailored", connection.execute("SELECT status FROM applications WHERE job_id = 1").fetchone()[0])
            connection.close()

class ResumeRenderingTests(unittest.TestCase):
    def test_tailoring_uses_validated_structured_response(self) -> None:
        class FakeResponse:
            text = '{"tailored_resume": "unterminated'
            parsed = {
                "tailored_resume": "# Candidate\n\n## Professional Summary\n\nEvidence-based summary.",
                "cover_letter": "August 4, 2026\n\nDear Hiring Team,\n\nI am interested in this role.",
            }

        class FakeModels:
            config = None

            def generate_content(self, **kwargs):
                self.config = kwargs["config"]
                return FakeResponse()

        class FakeClient:
            models = FakeModels()

        client = FakeClient()
        with patch("tailor.get_client", return_value=client):
            result = tailor_resume_and_cover_letter(
                "# Candidate\n\n## Experience\n\nEvidence.",
                "Director",
                "Example Company",
                "Lead an evidence-based program.",
            )

        self.assertTrue(result["success"])
        self.assertIn("## Professional Summary", result["tailored_resume"])
        self.assertIs(client.models.config.response_schema, TailoringResponse)

    def test_markdown_is_escaped_and_supported_tokens_are_rendered(self) -> None:
        rendered = markdown_to_html(
            "# Candidate\n\n---\n\n## Experience\n\n### Role\n\n"
            "- Delivered **measurable** results with *care*.\n\n<script>alert(1)</script>"
        )
        self.assertIn("<hr>", rendered)
        self.assertIn("<strong>measurable</strong>", rendered)
        self.assertIn("<em>care</em>", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn('<section class="resume-section">', rendered)


class FrontendStartupTests(unittest.TestCase):
    def test_required_startup_controls_exist_in_dashboard_html(self) -> None:
        static_dir = Path(__file__).parent / "static"
        html_source = (static_dir / "index.html").read_text(encoding="utf-8")
        for element_id in (
            "profile-form", "resume-file-upload", "search-form", "refresh-jobs-btn",
            "cleanup-jobs-btn", "refresh-logs-btn", "open-manual-application-btn",
            "archive-untouched-btn", "delete-untouched-btn", "restore-archived-btn",
            "lifecycle-form", "undo-lifecycle-btn", "save-materials-btn",
            "download-resume-btn", "download-cover-letter-btn",
            "saved-search-select", "p-resume-mode",
            "p-prefer-us-headquarters",
            "p-gemini-key-status", "p-gemini-key-help",
            "p-google-key-status", "p-google-key-help",
            "lifecycle-applied-calendar-btn", "saved-search-frequency", "provider-alerts",
            "open-job-import-btn", "job-import-modal", "job-import-form",
            "job-import-url", "preview-job-import-btn", "job-import-fields",
            "job-import-company", "job-import-title", "job-import-description",
            "save-job-import-btn",
        ):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', html_source)
        self.assertIn("Checking Gemini API Key", html_source)
        self.assertIn("app.js?v=20260807-3", html_source)
        self.assertIn("index.css?v=20260807-3", html_source)

    def test_launchers_require_the_current_backend_build(self) -> None:
        project_dir = Path(__file__).parent.parent
        for launcher in ("run.bat", "run.ps1"):
            with self.subTest(launcher=launcher):
                source = (project_dir / launcher).read_text(encoding="utf-8")
                self.assertIn("/api/version", source)
                self.assertIn("20260807.3", source)

    def test_manual_application_flow_replaces_browser_submission(self) -> None:
        project_dir = Path(__file__).parent.parent
        app_source = (project_dir / "backend" / "app.py").read_text(encoding="utf-8")
        html_source = (project_dir / "backend" / "static" / "index.html").read_text(encoding="utf-8")
        script_source = (project_dir / "backend" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertFalse((project_dir / "backend" / "applier.py").exists())
        self.assertNotIn('@app.post("/api/jobs/{job_id}/apply")', app_source)
        self.assertIn('@app.get("/api/jobs/{job_id}/apply-manually")', app_source)
        self.assertNotIn("Submit Application Now", html_source)
        self.assertNotIn("triggerApplicationSubmission", script_source)
        self.assertIn("Apply Manually", script_source)

    def test_launchers_use_the_configurable_default_port(self) -> None:
        project_dir = Path(__file__).parent.parent
        powershell_source = (project_dir / "run.ps1").read_text(encoding="utf-8")
        batch_source = (project_dir / "run.bat").read_text(encoding="utf-8")

        self.assertIn("[int]$Port = 8001", powershell_source)
        self.assertIn("--port $Port", powershell_source)
        self.assertIn('set "PORT=8001"', batch_source)
        self.assertIn("--port %PORT%", batch_source)
        self.assertNotIn("8000", powershell_source)
        self.assertNotIn("8000", batch_source)

    def test_job_applier_brand_assets_and_manifest_are_wired(self) -> None:
        static_dir = Path(__file__).parent / "static"
        html_source = (static_dir / "index.html").read_text(encoding="utf-8")
        manifest_source = (static_dir / "site.webmanifest").read_text(encoding="utf-8")
        for asset in (
            "icons/favicon.ico", "icons/favicon.svg", "icons/apple-touch-icon.png",
            "icons/safari-pinned-tab.svg", "icons/icon-192.png", "icons/icon-512.png",
        ):
            with self.subTest(asset=asset):
                self.assertTrue((static_dir / asset).is_file())
        self.assertIn('rel="manifest" href="/static/site.webmanifest"', html_source)
        self.assertIn('src="/static/icons/favicon.svg"', html_source)
        self.assertIn('"name": "Job Applier Agent"', manifest_source)

    def test_cleanup_footer_has_scoped_responsive_actions(self) -> None:
        static_dir = Path(__file__).parent / "static"
        html_source = (static_dir / "index.html").read_text(encoding="utf-8")
        css_source = (static_dir / "index.css").read_text(encoding="utf-8")
        self.assertIn('class="modal-footer cleanup-footer"', html_source)
        self.assertIn('class="cleanup-count-badge"', html_source)
        self.assertIn('btn btn-primary cleanup-action-btn" id="archive-untouched-btn"', html_source)
        self.assertIn(".cleanup-footer .modal-actions", css_source)
        self.assertIn("@media (max-width: 620px)", css_source)

    def test_cleanup_actions_have_click_through_guard(self) -> None:
        script_source = (Path(__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("cleanupActionsReady", script_source)
        self.assertIn("}, 500);", script_source)

    def test_job_and_log_rows_do_not_interpolate_untrusted_html(self) -> None:
        script_source = (Path(__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("tr.innerHTML", script_source)
        self.assertIn("safeHttpUrl", script_source)

    def test_all_resume_modes_have_prompt_guidance(self) -> None:
        expected = {"it", "technical_executive", "general_professional", "federal", "healthcare", "education", "sales", "trades_operations", "academic_cv", "cover_letter"}
        self.assertEqual(expected, set(RESUME_MODE_GUIDANCE))
        self.assertEqual(expected, set(RESUME_SECTION_TEMPLATES))

    def test_dual_mode_date_entry_is_validated(self) -> None:
        script_source = (Path(__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("parseDateEntry", script_source)
        self.assertIn("showPicker", script_source)

    def test_profile_api_source_does_not_return_plaintext_secrets_or_enable_cors(self) -> None:
        app_source = (Path(__file__).parent / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("CORSMiddleware", app_source)
        self.assertIn('result.pop("gemini_api_key"', app_source)
        self.assertIn('result.pop("google_maps_api_key"', app_source)

    def test_profile_ui_exposes_privacy_safe_key_status_and_replacement_guidance(self) -> None:
        static_dir = Path(__file__).parent / "static"
        html_source = (static_dir / "index.html").read_text(encoding="utf-8")
        script_source = (static_dir / "app.js").read_text(encoding="utf-8")
        self.assertIn('role="status" aria-live="polite"', html_source)
        self.assertIn("updateSecretStatuses", script_source)
        self.assertIn('status.textContent = configured ? "Saved" : "Not saved"', script_source)
        self.assertIn("Leave this field blank to keep the current key", script_source)
        self.assertNotRegex(script_source, r"profile\.google_maps_api_key(?!_configured)")
        self.assertNotRegex(script_source, r"profile\.gemini_api_key(?!_configured)")

    def test_manual_import_ui_preserves_unscored_jobs(self) -> None:
        script_source = (Path(__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("/api/jobs/import/preview", script_source)
        self.assertIn("/api/jobs/import", script_source)
        self.assertIn('job.match_score === null', script_source)
        self.assertIn('"Unscored"', script_source)


class ProfileSecretPresenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "profile-secrets.db"
        connection = sqlite3.connect(self.db_path)
        connection.executescript("""
            CREATE TABLE profile (
                id INTEGER PRIMARY KEY,
                name TEXT,
                gemini_api_key TEXT,
                google_maps_api_key TEXT
            );
            INSERT INTO profile VALUES (1, 'Test Candidate', 'gemini-secret', '');
        """)
        connection.commit()
        connection.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def connection_factory(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def test_profile_returns_presence_flags_without_secret_values(self) -> None:
        with patch.object(app_module, "get_db_connection", side_effect=self.connection_factory):
            profile = app_module.get_profile()

        self.assertTrue(profile["gemini_api_key_configured"])
        self.assertFalse(profile["google_maps_api_key_configured"])
        self.assertNotIn("gemini_api_key", profile)
        self.assertNotIn("google_maps_api_key", profile)


class LocalBrowserBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app_module.app, base_url="http://127.0.0.1:8001")

    def tearDown(self) -> None:
        self.client.close()

    def test_non_loopback_host_is_rejected_before_routing(self) -> None:
        response = self.client.get("/api/version", headers={"Host": "attacker.example"})
        self.assertEqual(400, response.status_code)
        self.assertEqual("Invalid local Host header.", response.json()["detail"])

    def test_cross_site_writes_are_rejected_but_local_clients_reach_routing(self) -> None:
        hostile_origin = self.client.post(
            "/api/not-a-real-route",
            headers={"Origin": "https://attacker.example"},
        )
        hostile_referer = self.client.post(
            "/api/not-a-real-route",
            headers={"Referer": "https://attacker.example/form"},
        )
        cross_site_fetch = self.client.post(
            "/api/not-a-real-route",
            headers={"Sec-Fetch-Site": "same-site"},
        )
        same_origin = self.client.post(
            "/api/not-a-real-route",
            headers={"Origin": "http://127.0.0.1:8001", "Sec-Fetch-Site": "same-origin"},
        )
        local_cli = self.client.post("/api/not-a-real-route")
        self.assertEqual(403, hostile_origin.status_code)
        self.assertEqual(403, hostile_referer.status_code)
        self.assertEqual(403, cross_site_fetch.status_code)
        self.assertEqual(404, same_origin.status_code)
        self.assertEqual(404, local_cli.status_code)

    def test_oversized_resume_is_rejected_without_changing_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "profile.db"
            connection = sqlite3.connect(database_path)
            connection.executescript("""
                CREATE TABLE profile (
                    id INTEGER PRIMARY KEY, base_resume_text TEXT, suggested_keywords TEXT
                );
                INSERT INTO profile VALUES (1, 'Original resume', '');
            """)
            connection.close()

            def connection_factory():
                test_connection = sqlite3.connect(database_path)
                test_connection.row_factory = sqlite3.Row
                return test_connection

            with patch.object(app_module, "get_db_connection", connection_factory):
                response = self.client.post(
                    "/api/profile/upload-resume",
                    headers={"Origin": "http://127.0.0.1:8001"},
                    files={
                        "file": (
                            "resume.txt",
                            b"a" * (app_module.MAX_RESUME_UPLOAD_BYTES + 1),
                            "text/plain",
                        )
                    },
                )

            self.assertEqual(413, response.status_code)
            connection = sqlite3.connect(database_path)
            stored = connection.execute("SELECT base_resume_text FROM profile WHERE id = 1").fetchone()[0]
            connection.close()
            self.assertEqual("Original resume", stored)


class ManualJobImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "manual-import.db"
        connection = sqlite3.connect(self.database_path)
        connection.executescript("""
            CREATE TABLE profile (
                id INTEGER PRIMARY KEY, base_resume_text TEXT, gemini_api_key TEXT
            );
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, company TEXT,
                description TEXT, url TEXT UNIQUE, match_score INTEGER,
                match_analysis TEXT, date_found TEXT, status TEXT, location TEXT,
                work_arrangement TEXT, employment_type TEXT, compensation TEXT,
                source TEXT, last_checked_at TEXT, is_expired INTEGER,
                expiration_reason TEXT
            );
            CREATE TABLE applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, status TEXT
            );
            INSERT INTO profile VALUES (1, 'Experienced data leader', 'test-key');
        """)
        connection.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def connection_factory(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def test_preview_rejects_private_network_url_before_database_or_browser(self) -> None:
        database = unittest.mock.Mock()
        scraper = unittest.mock.Mock()
        with (
            patch.object(app_module, "get_db_connection", database),
            patch.object(app_module, "inspect_job_posting", scraper),
            self.assertRaisesRegex(app_module.HTTPException, "Local, private-network"),
        ):
            app_module.preview_manual_job(
                app_module.ManualJobPreviewRequest(url="http://127.0.0.1/jobs/private")
            )
        database.assert_not_called()
        scraper.assert_not_called()

    def test_preview_extracts_editable_fields_without_saving(self) -> None:
        extracted = {
            "title": "Data Director", "company": "Example Health",
            "description": "Lead enterprise data strategy and analytics. " * 3,
            "location": "Remote", "work_arrangement": "remote",
            "employment_type": "full_time", "compensation": "$150,000 per year",
        }
        with (
            patch.object(app_module, "get_db_connection", side_effect=self.connection_factory),
            patch.object(app_module, "validate_public_http_url", return_value=(True, "")),
            patch.object(app_module, "inspect_job_posting", return_value={
                "status": "ok", "reason": "", "details": extracted,
            }),
        ):
            result = app_module.preview_manual_job(
                app_module.ManualJobPreviewRequest(url="https://example.test/jobs/123?utm_source=email")
            )

        self.assertFalse(result["duplicate"])
        self.assertTrue(result["extraction_succeeded"])
        self.assertEqual("https://example.test/jobs/123", result["job"]["url"])
        self.assertEqual("Data Director", result["job"]["title"])
        connection = self.connection_factory()
        self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
        connection.close()

    def test_save_scores_and_inserts_reviewed_job(self) -> None:
        request = app_module.ManualJobSaveRequest(
            url="https://example.test/jobs/123", title="Data Director",
            company="Example Health",
            description="Lead enterprise data strategy and analytics. " * 3,
            location="Remote", work_arrangement="remote", employment_type="full_time",
            compensation="$150,000 per year",
        )
        with (
            patch.object(app_module, "get_db_connection", side_effect=self.connection_factory),
            patch.object(app_module, "validate_public_http_url", return_value=(True, "")),
            patch.object(app_module, "analyze_job_match", return_value={
                "success": True, "match_score": 88, "match_analysis": "Strong match."
            }),
        ):
            result = app_module.save_manual_job(request)

        self.assertEqual(88, result["match_score"])
        connection = self.connection_factory()
        stored = connection.execute("SELECT * FROM jobs WHERE id = ?", (result["job_id"],)).fetchone()
        connection.close()
        self.assertEqual("https://example.test/jobs/123", stored["url"])
        self.assertEqual("example.test", stored["source"])
        self.assertEqual("matched", stored["status"])

    def test_duplicate_is_reported_before_scraping(self) -> None:
        connection = self.connection_factory()
        connection.execute(
            "INSERT INTO jobs (title, company, description, url, status) VALUES (?, ?, ?, ?, ?)",
            ("Existing", "Example", "Existing description", "https://example.test/jobs/123", "matched"),
        )
        connection.commit()
        connection.close()
        scraper = unittest.mock.Mock()
        with (
            patch.object(app_module, "get_db_connection", side_effect=self.connection_factory),
            patch.object(app_module, "validate_public_http_url", return_value=(True, "")),
            patch.object(app_module, "inspect_job_posting", scraper),
        ):
            result = app_module.preview_manual_job(
                app_module.ManualJobPreviewRequest(url="https://example.test/jobs/123?utm_campaign=test")
            )
        self.assertTrue(result["duplicate"])
        scraper.assert_not_called()

    def test_preview_uses_resolved_url_and_detects_redirect_duplicate(self) -> None:
        connection = self.connection_factory()
        connection.execute(
            "INSERT INTO jobs (title, company, description, url, status) VALUES (?, ?, ?, ?, ?)",
            ("Existing", "Example", "Existing description", "https://careers.example.test/jobs/123", "matched"),
        )
        connection.commit()
        connection.close()
        with (
            patch.object(app_module, "get_db_connection", side_effect=self.connection_factory),
            patch.object(app_module, "validate_public_http_url", return_value=(True, "")),
            patch.object(app_module, "inspect_job_posting", return_value={
                "status": "partial", "reason": "", "details": {
                    "url": "https://careers.example.test/jobs/123",
                    "title": "Data Director",
                },
            }),
        ):
            result = app_module.preview_manual_job(
                app_module.ManualJobPreviewRequest(url="https://example.test/apply/123")
            )

        self.assertTrue(result["duplicate"])
        self.assertEqual("https://careers.example.test/jobs/123", result["job"]["url"])

    def test_preview_explains_access_challenge_and_preserves_manual_entry(self) -> None:
        with (
            patch.object(app_module, "get_db_connection", side_effect=self.connection_factory),
            patch.object(app_module, "validate_public_http_url", return_value=(True, "")),
            patch.object(app_module, "inspect_job_posting", return_value={
                "status": "access_challenge", "reason": "Automated access blocked.", "details": {},
            }),
        ):
            result = app_module.preview_manual_job(
                app_module.ManualJobPreviewRequest(url="https://jobs.example.test/opening/123")
            )

        self.assertFalse(result["extraction_succeeded"])
        self.assertEqual("access_challenge", result["extraction_status"])
        self.assertIn("Enter the posting details manually", result["message"])

    def test_save_without_ai_key_keeps_job_visible_as_unscored(self) -> None:
        connection = self.connection_factory()
        connection.execute("UPDATE profile SET gemini_api_key = '' WHERE id = 1")
        connection.commit()
        connection.close()
        matcher = unittest.mock.Mock()
        request = app_module.ManualJobSaveRequest(
            url="https://example.test/jobs/unscored", title="Operations Director",
            company="Example Manufacturing",
            description="Lead operations, safety, quality, and continuous improvement. " * 2,
        )
        with (
            patch.object(app_module, "get_db_connection", side_effect=self.connection_factory),
            patch.object(app_module, "validate_public_http_url", return_value=(True, "")),
            patch.object(app_module, "analyze_job_match", matcher),
        ):
            result = app_module.save_manual_job(request)

        self.assertIsNone(result["match_score"])
        matcher.assert_not_called()
        connection = self.connection_factory()
        stored_score = connection.execute(
            "SELECT match_score FROM jobs WHERE id = ?", (result["job_id"],)
        ).fetchone()[0]
        connection.close()
        self.assertIsNone(stored_score)

    def test_malformed_ai_result_does_not_prevent_save(self) -> None:
        request = app_module.ManualJobSaveRequest(
            url="https://example.test/jobs/malformed-ai", title="Operations Director",
            company="Example Manufacturing",
            description="Lead operations, safety, quality, and continuous improvement. " * 2,
        )
        with (
            patch.object(app_module, "get_db_connection", side_effect=self.connection_factory),
            patch.object(app_module, "validate_public_http_url", return_value=(True, "")),
            patch.object(app_module, "analyze_job_match", return_value=None),
        ):
            result = app_module.save_manual_job(request)

        self.assertIsNone(result["match_score"])
        connection = self.connection_factory()
        stored = connection.execute(
            "SELECT id, match_score FROM jobs WHERE id = ?", (result["job_id"],)
        ).fetchone()
        connection.close()
        self.assertIsNotNone(stored)
        self.assertIsNone(stored["match_score"])

    def test_verification_error_does_not_close_active_job(self) -> None:
        connection = self.connection_factory()
        cursor = connection.execute(
            "INSERT INTO jobs (title, company, description, url, status, is_expired) VALUES (?, ?, ?, ?, ?, 0)",
            ("Data Director", "Example", "Complete description", "https://example.test/jobs/verify", "matched"),
        )
        connection.commit()
        job_id = cursor.lastrowid
        connection.close()
        with (
            patch.object(app_module, "get_db_connection", side_effect=self.connection_factory),
            patch.object(app_module, "inspect_job_posting", return_value={
                "status": "format_drift", "details": {}, "reason": "Unexpected format.",
            }),
            self.assertRaisesRegex(app_module.HTTPException, "No job status was changed"),
        ):
            app_module.verify_job_posting(job_id)

        connection = self.connection_factory()
        stored = connection.execute(
            "SELECT status, is_expired FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        connection.close()
        self.assertEqual("matched", stored["status"])
        self.assertEqual(0, stored["is_expired"])


class HeadquartersPreferenceTests(unittest.TestCase):
    def test_profile_save_persists_disabled_us_preference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "profile.db"
            connection = sqlite3.connect(db_path)
            connection.executescript("""
                CREATE TABLE profile (
                    id INTEGER PRIMARY KEY, name TEXT, email TEXT, phone TEXT,
                    github TEXT, linkedin TEXT, website TEXT, base_resume_text TEXT,
                    resume_mode TEXT, prefer_us_headquarters INTEGER,
                    suggested_keywords TEXT
                );
                INSERT INTO profile VALUES (1, '', '', '', '', '', '', '', '', 1, '');
            """)
            connection.close()

            def open_test_database():
                return sqlite3.connect(db_path)

            profile = app_module.ProfileUpdate(
                name="Candidate", email="candidate@example.test", phone="",
                github="", linkedin="", website="", base_resume_text="Resume",
                resume_mode="general_professional", prefer_us_headquarters=False,
            )
            with patch.object(app_module, "get_db_connection", side_effect=open_test_database):
                app_module.update_profile(profile)

            connection = sqlite3.connect(db_path)
            stored = connection.execute(
                "SELECT prefer_us_headquarters FROM profile WHERE id = 1"
            ).fetchone()[0]
            connection.close()
            self.assertEqual(0, stored)

    def test_us_preference_changes_places_query(self) -> None:
        self.assertEqual("Example Co United States headquarters", _headquarters_query("Example Co", True))
        self.assertEqual("Example Co global headquarters", _headquarters_query("Example Co", False))

    def test_us_address_requires_explicit_country(self) -> None:
        self.assertTrue(_looks_like_us_address("New York, NY 10001, USA"))
        self.assertTrue(_looks_like_us_address("Chicago, IL, United States"))
        self.assertFalse(_looks_like_us_address("Chennai, Tamil Nadu 600100, India"))

    @patch("urllib.request.urlopen")
    def test_us_preference_rejects_non_us_places_result(self, urlopen) -> None:
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = (
            b'{"candidates":[{"formatted_address":"Chennai, Tamil Nadu 600100, India"}]}'
        )
        urlopen.return_value = response
        with patch("utils.get_gemini_api_key", return_value=""):
            self.assertEqual(
                "Unknown",
                find_us_headquarters("Future Works", api_key="", google_maps_key="maps-key", prefer_us=True),
            )

    @patch("urllib.request.urlopen")
    def test_global_preference_accepts_formatted_global_result(self, urlopen) -> None:
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = (
            b'{"candidates":[{"formatted_address":"London EC1V 3AG, United Kingdom"}]}'
        )
        urlopen.return_value = response
        self.assertEqual(
            "London EC1V 3AG, United Kingdom",
            find_us_headquarters("Example Co", api_key="", google_maps_key="maps-key", prefer_us=False),
        )


class SearchQualityTests(unittest.TestCase):
    class FakeHttpResponse:
        def __init__(self, payload: bytes):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return self.payload

    def test_generic_smartrecruiters_career_pages_are_not_job_postings(self) -> None:
        self.assertFalse(is_specific_job_url("https://careers.smartrecruiters.com/QADInc/corporate-careers"))
        self.assertTrue(is_specific_job_url("https://jobs.smartrecruiters.com/Example/12345-data-analyst"))

    def test_tracking_variants_share_one_canonical_url(self) -> None:
        base = "https://jobs.lever.co/example/abc-123"
        self.assertEqual(base, canonicalize_job_url(base + "/?source=linkedin#apply"))

    def test_unknown_site_keeps_functional_query_but_removes_tracking(self) -> None:
        self.assertEqual(
            "https://careers.example.test/opening?job=123",
            canonicalize_job_url(
                "https://careers.example.test/opening?utm_source=email&job=123#apply"
            ),
        )

    def test_public_url_validation_blocks_local_and_private_addresses(self) -> None:
        for url in (
            "http://127.0.0.1/job", "http://localhost/job",
            "http://10.10.1.4/job", "http://169.254.169.254/latest/meta-data",
            "file:///etc/passwd",
        ):
            with self.subTest(url=url):
                self.assertFalse(is_public_http_url(url))

    @patch("searcher.socket.getaddrinfo")
    def test_public_url_validation_requires_only_global_dns_results(self, getaddrinfo) -> None:
        getaddrinfo.return_value = [
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ]
        self.assertTrue(is_public_http_url("https://jobs.example.test/opening"))
        getaddrinfo.return_value = [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]
        self.assertFalse(is_public_http_url("https://mixed.example.test/opening"))

    @patch("searcher.socket.getaddrinfo")
    def test_browser_route_guard_blocks_private_redirects_and_subresources(self, getaddrinfo) -> None:
        getaddrinfo.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]

        class FakeContext:
            handler = None

            def route(self, pattern, handler):
                self.handler = handler

        class FakeRoute:
            continued = False
            aborted = False

            def continue_(self):
                self.continued = True

            def abort(self, reason):
                self.aborted = reason == "blockedbyclient"

        class FakeRequest:
            def __init__(self, url):
                self.url = url

        context = FakeContext()
        _protect_browser_network(context)
        public_route = FakeRoute()
        private_route = FakeRoute()
        context.handler(public_route, FakeRequest("https://assets.example.test/app.js"))
        context.handler(private_route, FakeRequest("http://127.0.0.1/private"))
        self.assertTrue(public_route.continued)
        self.assertTrue(private_route.aborted)

    def test_browser_error_page_is_not_useful_job_content(self) -> None:
        self.assertFalse(is_useful_job_details({
            "title": "Sorry, Internet Explorer 11 is no longer supported by SmartRecruiters",
            "company": "LegalAndGeneral Enterprise Data Architect",
            "description": "Please update your browser. " * 10,
        }))

    def test_real_job_content_is_useful(self) -> None:
        self.assertTrue(is_useful_job_details({
            "title": "Chief Data Officer",
            "company": "Example Health",
            "description": "Lead enterprise data strategy, governance, analytics, and a multidisciplinary team. " * 3,
        }))

    def test_provider_detection_and_metadata_normalization(self) -> None:
        self.assertEqual("greenhouse", provider_for_url("https://job-boards.greenhouse.io/acme/jobs/123"))
        self.assertEqual("ashby", provider_for_url("https://jobs.ashbyhq.com/acme/123"))
        self.assertEqual("lever", provider_for_url("https://jobs.eu.lever.co/acme/123"))
        metadata = _metadata_from_text("Remote full time role. Compensation $120,000 - $150,000 per year.")
        self.assertEqual("remote", metadata["work_arrangement"])
        self.assertEqual("full_time", metadata["employment_type"])
        self.assertIn("$120,000", metadata["compensation"])

    def test_json_ld_location_normalizes_structured_country_values(self) -> None:
        posting = {
            "jobLocation": [
                {
                    "@type": "Place",
                    "address": {
                        "@type": "PostalAddress",
                        "addressLocality": "Los Angeles, CA,US",
                        "addressRegion": "CA",
                        "addressCountry": {"@type": "Country", "name": "US"},
                    },
                },
                {
                    "@type": "Place",
                    "address": {
                        "addressLocality": "Dayton",
                        "addressRegion": "OH",
                        "addressCountry": {"@type": "Country", "name": "US"},
                    },
                },
            ]
        }

        self.assertEqual(
            "Los Angeles, CA, US; Dayton, OH, US",
            _job_location_from_json_ld(posting),
        )

    def test_popular_job_board_structured_metadata_shapes_are_normalized(self) -> None:
        samples = {
            "ziprecruiter": ({
                "employmentType": "PART_TIME",
                "baseSalary": {
                    "@type": "MonetaryAmount", "currency": "USD",
                    "value": {"@type": "QuantitativeValue", "minValue": 18, "maxValue": 20, "unitText": "HOUR"},
                },
            }, {"work_arrangement": "", "employment_type": "part_time", "compensation": "USD 18–20 per hour"}),
            "glassdoor": ({
                "employmentType": ["FULL_TIME"], "jobLocationType": "TELECOMMUTE",
                "estimatedSalary": {
                    "@type": "MonetaryAmount", "currency": "USD",
                    "value": {"@type": "QuantitativeValue", "minValue": 77447, "maxValue": 113374, "unitText": "YEAR"},
                },
            }, {"work_arrangement": "remote", "employment_type": "full_time", "compensation": "USD 77,447–113,374 per year"}),
            "dice": ({
                "employmentType": "CONTRACTOR", "jobLocationType": "TELECOMMUTE",
                "baseSalary": {"@type": "MonetaryAmount", "currency": "USD", "minValue": 70, "maxValue": 85},
            }, {"work_arrangement": "remote", "employment_type": "contract", "compensation": "USD 70–85"}),
        }

        for platform, (posting, expected) in samples.items():
            with self.subTest(platform=platform):
                self.assertEqual(expected, _structured_job_metadata(posting))

    def test_remote_applicant_location_is_used_when_job_location_is_absent(self) -> None:
        posting = {
            "jobLocationType": "TELECOMMUTE",
            "applicantLocationRequirements": [
                {"@type": "Country", "name": "USA"},
                {"@type": "AdministrativeArea", "name": "District of Columbia"},
            ],
        }

        self.assertEqual("USA; District of Columbia", _job_location_from_json_ld(posting))

    def test_provider_format_drift_creates_user_alert(self) -> None:
        health = {"ashby": {"raw_candidates": 4, "valid_discovered": 4, "new_candidates": 3, "accepted": 0, "errors": []}}
        alerts = provider_alerts_from_health(health)
        self.assertEqual("content_format_drift", alerts[0]["code"])

    def test_lever_api_response_is_normalized_without_browser_scraping(self) -> None:
        payload = json.dumps({
            "id": "posting-123",
            "text": "Director of Data",
            "descriptionPlain": "Lead enterprise data strategy, governance, analytics, architecture, and delivery across the organization. " * 2,
            "categories": {
                "location": "Dayton, OH", "allLocations": ["Dayton, OH", "Remote"],
                "commitment": "Full-time",
            },
            "workplaceType": "hybrid",
            "salaryRange": {"currency": "USD", "interval": "year", "min": 150000, "max": 190000},
        }).encode("utf-8")
        with patch.object(
            searcher_module.urllib.request,
            "urlopen",
            return_value=self.FakeHttpResponse(payload),
        ) as urlopen:
            outcome = searcher_module._lever_posting_from_api(
                "https://jobs.lever.co/acme-corp/posting-123"
            )

        self.assertEqual("ok", outcome["status"])
        self.assertEqual("Director of Data", outcome["details"]["title"])
        self.assertEqual("Acme Corp", outcome["details"]["company"])
        self.assertEqual("Dayton, OH; Remote", outcome["details"]["location"])
        self.assertEqual("hybrid", outcome["details"]["work_arrangement"])
        self.assertEqual("full_time", outcome["details"]["employment_type"])
        self.assertEqual("USD 150,000–190,000 per year", outcome["details"]["compensation"])
        request = urlopen.call_args.args[0]
        self.assertEqual(
            "https://api.lever.co/v0/postings/acme-corp/posting-123",
            request.full_url,
        )

    def test_lever_removed_posting_is_stale_without_browser_fallback(self) -> None:
        stale = {"status": "stale", "details": {}, "reason": "No longer published."}
        browser = unittest.mock.Mock()
        with (
            patch.object(searcher_module, "validate_public_http_url", return_value=(True, "")),
            patch.object(searcher_module, "_lever_posting_from_api", return_value=stale),
            patch.object(searcher_module, "_scrape_job_details_browser", browser),
        ):
            outcome = searcher_module.inspect_job_posting(
                "https://jobs.lever.co/acme/posting-123"
            )

        self.assertEqual("stale", outcome["status"])
        browser.assert_not_called()

    def test_lever_invalid_successful_response_is_format_drift(self) -> None:
        payload = json.dumps({"id": "posting-123", "categories": []}).encode("utf-8")
        with patch.object(
            searcher_module.urllib.request,
            "urlopen",
            return_value=self.FakeHttpResponse(payload),
        ):
            outcome = searcher_module._lever_posting_from_api(
                "https://jobs.lever.co/acme/posting-123"
            )

        self.assertEqual("format_drift", outcome["status"])

    def test_lever_api_not_found_is_classified_as_stale(self) -> None:
        error = searcher_module.urllib.error.HTTPError(
            "https://api.lever.co/v0/postings/acme/posting-123",
            404,
            "Not Found",
            hdrs=None,
            fp=None,
        )
        with patch.object(searcher_module.urllib.request, "urlopen", side_effect=error):
            outcome = searcher_module._lever_posting_from_api(
                "https://jobs.lever.co/acme/posting-123"
            )

        self.assertEqual("stale", outcome["status"])

    def test_lever_api_unavailable_uses_browser_fallback(self) -> None:
        def browser_fallback(_url, _allow_partial, diagnostic):
            diagnostic.update({"status": "ok", "reason": ""})
            return {"title": "Fallback role", "company": "Acme", "description": "Complete description"}

        with (
            patch.object(searcher_module, "validate_public_http_url", return_value=(True, "")),
            patch.object(searcher_module, "_lever_posting_from_api", return_value={
                "status": "api_unavailable", "details": {}, "reason": "Timed out.",
            }),
            patch.object(searcher_module, "_scrape_job_details_browser", side_effect=browser_fallback),
        ):
            outcome = searcher_module.inspect_job_posting(
                "https://jobs.lever.co/acme/posting-123"
            )

        self.assertEqual("ok", outcome["status"])
        self.assertTrue(outcome["used_browser_fallback"])

    def test_lever_stale_postings_do_not_create_format_drift_alert(self) -> None:
        health = {
            "lever": {
                "raw_candidates": 3, "valid_discovered": 3, "new_candidates": 3,
                "accepted": 0, "errors": [],
                "rejection_reasons": {"stale": 3, "format_drift": 0},
            }
        }

        alerts = provider_alerts_from_health(health)

        self.assertEqual(["stale_postings"], [alert["code"] for alert in alerts])

    def test_generic_extraction_prefers_structured_job_data_in_child_frame(self) -> None:
        class Scope:
            def __init__(self, url):
                self.url = url

        page = Scope("https://careers.example.test/openings/123")
        frame = Scope("https://embedded.example.test/jobs/123")
        page.main_frame = page
        page.frames = [page, frame]
        framed_posting = {"@type": "JobPosting", "title": "Framed role"}

        with (
            patch.object(
                searcher_module,
                "_job_posting_json_ld",
                side_effect=lambda scope: framed_posting if scope is frame else {},
            ),
            patch.object(searcher_module, "_largest_text", return_value=""),
        ):
            scope, posting, has_job_frame = searcher_module._select_job_content_scope(page)

        self.assertIs(frame, scope)
        self.assertEqual(framed_posting, posting)
        self.assertTrue(has_job_frame)


class ResumeTemplateTests(unittest.TestCase):
    def test_sections_are_normalized_into_mode_order(self) -> None:
        source = "# Candidate\n\n## Experience\nRole details\n\n## Skills\nPython\n\n## Summary\nLeader"
        result = apply_resume_section_template(source, "it")
        self.assertLess(result.index("## Summary"), result.index("## Technical Skills"))
        self.assertLess(result.index("## Technical Skills"), result.index("## Professional Experience"))

    def test_unknown_sections_are_rejected_instead_of_silently_dropped(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported section"):
            apply_resume_section_template("# Candidate\n\n## Mystery Material\nText", "it")


class LifecycleSchemaTests(unittest.TestCase):
    def test_lifecycle_columns_and_foreign_keys_are_enabled(self) -> None:
        connection = get_db_connection()
        lifecycle_columns = {
            "created_at", "tailored_at", "form_filled_at", "submitted_at",
            "confirmed_at", "application_method", "submission_evidence", "notes", "follow_up_date", "tailored_resume_text",
            "cover_letter_path",
        }
        actual = {row[1] for row in connection.execute("PRAGMA table_info(applications)")}
        self.assertTrue(lifecycle_columns.issubset(actual))
        job_columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        self.assertTrue({"last_checked_at", "is_expired", "expiration_reason", "location", "work_arrangement", "employment_type", "compensation", "source"}.issubset(job_columns))
        profile_columns = {row[1] for row in connection.execute("PRAGMA table_info(profile)")}
        self.assertIn("resume_mode", profile_columns)
        self.assertIn("prefer_us_headquarters", profile_columns)
        self.assertEqual(1, connection.execute("PRAGMA foreign_keys").fetchone()[0])
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"application_status_history", "saved_searches"}.issubset(tables))
        saved_search_columns = {row[1] for row in connection.execute("PRAGMA table_info(saved_searches)")}
        self.assertTrue({"schedule_frequency", "next_alert_at"}.issubset(saved_search_columns))
        connection.close()


class ManualLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY, company TEXT, title TEXT, status TEXT
            );
            CREATE TABLE applications (
                id INTEGER PRIMARY KEY, job_id INTEGER UNIQUE, company TEXT, position TEXT,
                date_applied TEXT, status TEXT, created_at TEXT, submitted_at TEXT,
                confirmed_at TEXT, application_method TEXT, submission_evidence TEXT,
                notes TEXT, follow_up_date TEXT, tailored_resume_path TEXT, cover_letter TEXT
            );
            CREATE TABLE application_status_history (
                id INTEGER PRIMARY KEY, job_id INTEGER, from_status TEXT, to_status TEXT,
                changed_at TEXT, source TEXT, notes TEXT, undone_at TEXT
            );
            INSERT INTO jobs VALUES (1, 'Example Co', 'Data Director', 'matched');
        """)

    def tearDown(self) -> None:
        self.connection.close()

    def test_manual_application_records_date_method_and_evidence(self) -> None:
        result = update_lifecycle(
            self.connection, 1, "applied", applied_on=date(2026, 8, 3),
            method="referral", notes="Referred by Alex", source="manual",
        )
        self.assertEqual("applied", result["status"])
        application = self.connection.execute("SELECT * FROM applications WHERE job_id = 1").fetchone()
        self.assertEqual("2026-08-03", application["date_applied"])
        self.assertEqual("manual:referral", application["application_method"])
        self.assertIn('"confirmed_by_user": true', application["submission_evidence"])
        self.assertEqual("applied", self.connection.execute("SELECT status FROM jobs WHERE id=1").fetchone()[0])

    def test_latest_manual_change_can_be_undone(self) -> None:
        update_lifecycle(self.connection, 1, "applied", applied_on=date(2026, 8, 3), source="manual")
        result = undo_latest_lifecycle_change(self.connection, 1)
        self.assertEqual("matched", result["status"])
        self.assertEqual("matched", self.connection.execute("SELECT status FROM jobs WHERE id=1").fetchone()[0])
        self.assertIsNone(self.connection.execute("SELECT id FROM applications WHERE job_id=1").fetchone())


class BulkCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY,
                company TEXT,
                title TEXT,
                date_found TEXT,
                match_score INTEGER,
                status TEXT,
                archived_at TEXT,
                archived_from_status TEXT
            );
            CREATE TABLE applications (id INTEGER PRIMARY KEY, job_id INTEGER);
            INSERT INTO jobs VALUES (1, 'A', 'Untouched', '2026-01-01', 90, 'matched', NULL, NULL);
            INSERT INTO jobs VALUES (2, 'B', 'Has History', '2026-01-01', 80, 'matched', NULL, NULL);
            INSERT INTO jobs VALUES (3, 'C', 'Tailored', '2026-01-01', 70, 'tailored', NULL, NULL);
            INSERT INTO jobs VALUES (4, 'D', 'Archived', '2026-01-01', 60, 'archived', '2026-01-02', 'matched');
            INSERT INTO applications VALUES (1, 2);
        """)

    def tearDown(self) -> None:
        self.connection.close()

    def test_preview_protects_any_job_with_history_or_progress(self) -> None:
        preview = cleanup_preview(self.connection)
        self.assertEqual(1, preview["actions"]["archive"]["count"])
        self.assertEqual(2, preview["actions"]["delete"]["count"])
        self.assertEqual(1, preview["actions"]["restore"]["count"])
        self.assertEqual(2, preview["protected_count"])

    def test_preview_token_prevents_changed_set_from_being_mutated(self) -> None:
        preview = cleanup_preview(self.connection)
        token = preview["actions"]["archive"]["preview_token"]
        self.connection.execute(
            "INSERT INTO jobs VALUES (5, 'E', 'New', '2026-01-03', 50, 'matched', NULL, NULL)"
        )
        with self.assertRaisesRegex(ValueError, "changed after preview"):
            apply_cleanup(self.connection, "archive", token, "2026-01-04T00:00:00")
        status = self.connection.execute("SELECT status FROM jobs WHERE id = 1").fetchone()[0]
        self.assertEqual("matched", status)

    def test_archive_and_restore_are_reversible(self) -> None:
        preview = cleanup_preview(self.connection)
        affected = apply_cleanup(
            self.connection, "archive", preview["actions"]["archive"]["preview_token"], "2026-01-04T00:00:00"
        )
        self.assertEqual(1, affected)
        self.assertEqual("archived", self.connection.execute("SELECT status FROM jobs WHERE id = 1").fetchone()[0])

        preview = cleanup_preview(self.connection)
        affected = apply_cleanup(
            self.connection, "restore", preview["actions"]["restore"]["preview_token"], "2026-01-04T00:00:00"
        )
        self.assertEqual(2, affected)
        restored = self.connection.execute("SELECT status FROM jobs WHERE id IN (1, 4) ORDER BY id").fetchall()
        self.assertEqual(["matched", "matched"], [row[0] for row in restored])

    def test_delete_removes_only_previewed_untouched_jobs(self) -> None:
        preview = cleanup_preview(self.connection)
        affected = apply_cleanup(
            self.connection, "delete", preview["actions"]["delete"]["preview_token"], "2026-01-04T00:00:00"
        )
        self.assertEqual(2, affected)
        remaining = self.connection.execute("SELECT id FROM jobs ORDER BY id").fetchall()
        self.assertEqual([2, 3], [row[0] for row in remaining])


if __name__ == "__main__":
    unittest.main()
