import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from applier import (
    _confirmation_markers,
    build_cover_letter_upload_path,
    detect_submission_confirmation,
    field_requires_review,
)
from lifecycle import status_from_automation, undo_latest_lifecycle_change, update_lifecycle
from database import get_db_connection
from job_cleanup import apply_cleanup, cleanup_preview
from searcher import (
    _metadata_from_text,
    canonicalize_job_url,
    is_specific_job_url,
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
from utils import markdown_to_html


class ApplicationSafetyTests(unittest.TestCase):
    class _FakeLocator:
        def __init__(self, page) -> None:
            self.page = page

        def inner_text(self, timeout: int = 0) -> str:
            return self.page.body

    class _FakePage:
        def __init__(self, url: str, body: str) -> None:
            self.url = url
            self.body = body

        def locator(self, selector: str):
            return ApplicationSafetyTests._FakeLocator(self)

    def test_cover_letter_path_never_aliases_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            resume = Path(temp_dir) / "tailored_resume_12.pdf"
            cover = build_cover_letter_upload_path(str(resume))
            self.assertNotEqual(resume.resolve(), cover.resolve())
            self.assertEqual("tailored_resume_12_cover_letter.txt", cover.name)

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

    def test_sensitive_fields_always_require_review(self) -> None:
        for label in (
            "Will you require visa sponsorship?",
            "Desired salary",
            "Veteran status",
            "I certify that this information is true",
        ):
            with self.subTest(label=label):
                self.assertTrue(field_requires_review({"label": label}))
        self.assertFalse(field_requires_review({"label": "First name"}))

    def test_unconfirmed_submission_is_not_applied(self) -> None:
        self.assertEqual("form_filled", status_from_automation({}))
        self.assertEqual("submitted", status_from_automation({"submission_attempted": True}))
        self.assertEqual(
            "applied",
            status_from_automation({"submission_attempted": True, "submission_confirmed": True}),
        )

    def test_confirmation_text_must_be_new_after_submit(self) -> None:
        page = self._FakePage("https://example.test/apply", "Application submitted means you have completed all steps")
        baseline = _confirmation_markers(page)
        confirmed, _ = detect_submission_confirmation(page, page.url, baseline)
        self.assertFalse(confirmed)
        page.body = "Thank you for applying"
        confirmed, evidence = detect_submission_confirmation(page, page.url, baseline)
        self.assertTrue(confirmed)
        self.assertTrue(evidence.startswith("confirmation_text:"))


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
            "cleanup-jobs-btn", "refresh-logs-btn", "apply-job-confirm-btn",
            "archive-untouched-btn", "delete-untouched-btn", "restore-archived-btn",
            "lifecycle-form", "undo-lifecycle-btn", "save-materials-btn",
            "saved-search-select", "p-resume-mode",
            "lifecycle-applied-calendar-btn", "saved-search-frequency", "provider-alerts",
        ):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', html_source)
        self.assertIn("Checking Gemini API Key", html_source)
        self.assertIn("app.js?v=", html_source)
        self.assertIn("index.css?v=", html_source)

    def test_launchers_require_the_current_backend_build(self) -> None:
        project_dir = Path(__file__).parent.parent
        for launcher in ("run.bat", "run.ps1"):
            with self.subTest(launcher=launcher):
                source = (project_dir / launcher).read_text(encoding="utf-8")
                self.assertIn("/api/version", source)
                self.assertIn("20260804.1", source)

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


class SearchQualityTests(unittest.TestCase):
    def test_generic_smartrecruiters_career_pages_are_not_job_postings(self) -> None:
        self.assertFalse(is_specific_job_url("https://careers.smartrecruiters.com/QADInc/corporate-careers"))
        self.assertTrue(is_specific_job_url("https://jobs.smartrecruiters.com/Example/12345-data-analyst"))

    def test_tracking_variants_share_one_canonical_url(self) -> None:
        base = "https://jobs.lever.co/example/abc-123"
        self.assertEqual(base, canonicalize_job_url(base + "/?source=linkedin#apply"))

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
        metadata = _metadata_from_text("Remote full time role. Compensation $120,000 - $150,000 per year.")
        self.assertEqual("remote", metadata["work_arrangement"])
        self.assertEqual("full_time", metadata["employment_type"])
        self.assertIn("$120,000", metadata["compensation"])

    def test_provider_format_drift_creates_user_alert(self) -> None:
        health = {"ashby": {"raw_candidates": 4, "valid_discovered": 4, "new_candidates": 3, "accepted": 0, "errors": []}}
        alerts = provider_alerts_from_health(health)
        self.assertEqual("content_format_drift", alerts[0]["code"])


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
        }
        actual = {row[1] for row in connection.execute("PRAGMA table_info(applications)")}
        self.assertTrue(lifecycle_columns.issubset(actual))
        job_columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        self.assertTrue({"last_checked_at", "is_expired", "expiration_reason", "location", "work_arrangement", "employment_type", "compensation", "source"}.issubset(job_columns))
        profile_columns = {row[1] for row in connection.execute("PRAGMA table_info(profile)")}
        self.assertIn("resume_mode", profile_columns)
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
