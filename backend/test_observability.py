import os
import unittest
from unittest.mock import patch

import sentry_sdk

from observability import (
    initialize_sentry,
    sanitize_error_event,
    sanitize_transaction_event,
    sentry_debug_enabled,
)


class SentryConfigurationTests(unittest.TestCase):
    def test_sentry_is_disabled_without_a_dsn(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch.object(sentry_sdk, "init") as init:
            self.assertFalse(initialize_sentry("test-build"))
        init.assert_not_called()

    def test_sentry_uses_privacy_safe_configurable_defaults(self) -> None:
        environment = {
            "SENTRY_DSN": "https://public@example.invalid/1",
            "SENTRY_ENVIRONMENT": "production",
            "SENTRY_TRACES_SAMPLE_RATE": "0.25",
        }
        with patch.dict(os.environ, environment, clear=True), patch.object(sentry_sdk, "init") as init:
            self.assertTrue(initialize_sentry("test-build"))

        init.assert_called_once_with(
            dsn=environment["SENTRY_DSN"],
            environment="production",
            release="test-build",
            send_default_pii=False,
            include_local_variables=False,
            max_request_body_size="never",
            before_send=sanitize_error_event,
            before_send_transaction=sanitize_transaction_event,
            traces_sample_rate=0.25,
        )

    def test_invalid_trace_sample_rate_falls_back_to_off(self) -> None:
        environment = {
            "SENTRY_DSN": "https://public@example.invalid/1",
            "SENTRY_TRACES_SAMPLE_RATE": "2",
        }
        with patch.dict(os.environ, environment, clear=True), patch.object(sentry_sdk, "init") as init:
            initialize_sentry("test-build")

        options = init.call_args.kwargs
        self.assertEqual(0.0, options["traces_sample_rate"])
        self.assertNotIn("profiles_sample_rate", options)
        self.assertNotIn("profile_session_sample_rate", options)

    def test_debug_route_requires_dsn_and_explicit_opt_in(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(sentry_debug_enabled())
        with patch.dict(os.environ, {"SENTRY_DEBUG_ROUTE": "true"}, clear=True):
            self.assertFalse(sentry_debug_enabled())
        with patch.dict(os.environ, {
            "SENTRY_DSN": "https://public@example.invalid/1",
            "SENTRY_DEBUG_ROUTE": "true",
        }, clear=True):
            self.assertTrue(sentry_debug_enabled())

    def test_initialization_failure_does_not_prevent_startup(self) -> None:
        with (
            patch.dict(os.environ, {"SENTRY_DSN": "invalid"}, clear=True),
            patch.object(sentry_sdk, "init", side_effect=ValueError("invalid private DSN")),
        ):
            self.assertFalse(initialize_sentry("test-build"))

    def test_error_filter_removes_all_user_and_request_content(self) -> None:
        private_values = (
            "hunter@example.test",
            "https://jobs.example.test/private-role?token=secret",
            "private resume content",
            "api-secret",
            "C:/Users/private/resume.pdf",
        )
        event = {
            "event_id": "safe-event-id",
            "timestamp": "2026-08-08T20:00:00Z",
            "level": "error",
            "message": private_values[2],
            "transaction": "/api/jobs/42/tailor",
            "request": {
                "url": private_values[1],
                "data": {"resume": private_values[2]},
                "headers": {"Authorization": private_values[3]},
            },
            "user": {"email": private_values[0]},
            "breadcrumbs": {"values": [{"message": private_values[1]}]},
            "extra": {"output_path": private_values[4]},
            "contexts": {"response": {"data": private_values[2]}},
            "exception": {"values": [{
                "type": "RuntimeError",
                "module": "builtins",
                "value": private_values[2],
                "mechanism": {"description": private_values[3]},
                "stacktrace": {"frames": [{
                    "module": "app",
                    "function": "tailor_resume",
                    "lineno": 42,
                    "in_app": True,
                    "abs_path": private_values[4],
                    "context_line": private_values[2],
                    "vars": {"api_key": private_values[3]},
                }]},
            }]},
        }

        filtered = sanitize_error_event(event)
        self.assertIsNotNone(filtered)
        filtered_text = repr(filtered)
        for private_value in private_values:
            self.assertNotIn(private_value, filtered_text)
        self.assertEqual("RuntimeError", filtered["exception"]["values"][0]["type"])
        self.assertEqual(
            "tailor_resume",
            filtered["exception"]["values"][0]["stacktrace"]["frames"][0]["function"],
        )

    def test_non_exception_events_are_dropped(self) -> None:
        self.assertIsNone(sanitize_error_event({"message": "private profile content"}))

    def test_transaction_filter_keeps_only_anonymous_timing_and_trace_fields(self) -> None:
        event = {
            "event_id": "safe-event-id",
            "type": "transaction",
            "start_timestamp": 1.0,
            "timestamp": 2.0,
            "transaction": "/api/jobs/42/tailor",
            "request": {"url": "https://private.example/jobs/42"},
            "spans": [{"description": "SELECT resume_text FROM profile"}],
            "contexts": {"trace": {
                "trace_id": "0123456789abcdef0123456789abcdef",
                "span_id": "0123456789abcdef",
                "op": "http.server",
                "status": "ok",
                "data": {"http.query": "api_key=secret"},
            }},
        }

        filtered = sanitize_transaction_event(event)
        self.assertEqual("http.server", filtered["transaction"])
        self.assertEqual("0123456789abcdef0123456789abcdef", filtered["contexts"]["trace"]["trace_id"])
        filtered_text = repr(filtered)
        for private_value in ("/api/jobs/42/tailor", "private.example", "resume_text", "api_key"):
            self.assertNotIn(private_value, filtered_text)

    def test_dynamic_user_text_is_not_accepted_as_code_or_trace_metadata(self) -> None:
        private_text = "private resume content hunter@example.test"
        error = sanitize_error_event({"exception": {"values": [{
            "type": private_text,
            "module": "app",
            "stacktrace": {"frames": [{"function": private_text, "lineno": -1}]},
        }]}})
        self.assertNotIn(private_text, repr(error))

        transaction = sanitize_transaction_event({
            "contexts": {"trace": {
                "trace_id": private_text,
                "op": private_text,
            }},
        })
        self.assertNotIn(private_text, repr(transaction))


if __name__ == "__main__":
    unittest.main()
