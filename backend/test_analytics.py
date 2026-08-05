import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import FastAPI
from fastapi.testclient import TestClient

from analytics import AnalyticsService, EVENT_SCHEMAS, _filter_final_posthog_message


class RecordingClient:
    def __init__(self, capture_action=None, flush_action=None) -> None:
        self.capture_action = capture_action
        self.flush_action = flush_action
        self.captures = []
        self.flush_calls = 0
        self.shutdown_calls = 0
        self.exception_calls = 0

    def capture(self, event, **kwargs):
        if self.capture_action:
            self.capture_action()
        self.captures.append((event, kwargs))
        return "event-uuid"

    def capture_exception(self, *args, **kwargs):
        self.exception_calls += 1
        raise AssertionError("Exception capture must remain disabled")

    def flush(self, **kwargs):
        self.flush_calls += 1
        if self.flush_action:
            self.flush_action()

    def shutdown(self):
        self.shutdown_calls += 1


class ClientFactory:
    def __init__(self, client=None) -> None:
        self.client = client or RecordingClient()
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.client


def valid_properties(event: str) -> dict:
    properties = {
        "result": "success",
        "source_category": "manual",
    }
    if "from_status" in EVENT_SCHEMAS[event]:
        properties["from_status"] = "matched"
    if "to_status" in EVENT_SCHEMAS[event]:
        properties["to_status"] = "tailored"
    if "duration_bucket" in EVENT_SCHEMAS[event]:
        properties["duration_bucket"] = "under_1s"
    return properties


class AnalyticsPrivacyTests(unittest.TestCase):
    def make_service(self, directory: str, factory: ClientFactory, token: str = "test-token"):
        return AnalyticsService(
            token=token,
            host="https://posthog.invalid",
            application_version="20260804.1",
            identity_path=Path(directory) / "installation-id",
            client_factory=factory,
        )

    def test_no_client_or_identity_is_created_without_token(self) -> None:
        with TemporaryDirectory() as directory:
            factory = ClientFactory()
            identity_path = Path(directory) / "installation-id"
            service = self.make_service(directory, factory, token="")
            self.assertFalse(service.enabled)
            self.assertEqual([], factory.calls)
            self.assertFalse(identity_path.exists())

    def test_all_four_events_use_the_persisted_random_identity(self) -> None:
        with TemporaryDirectory() as directory:
            factory = ClientFactory()
            service = self.make_service(directory, factory)
            persisted = (Path(directory) / "installation-id").read_text(encoding="ascii").strip()
            for event in EVENT_SCHEMAS:
                self.assertTrue(service.capture(event, valid_properties(event)))
            self.assertTrue(service.shutdown(1.0))

            self.assertEqual(4, len(factory.client.captures))
            self.assertEqual({persisted}, {call[1]["distinct_id"] for call in factory.client.captures})
            self.assertEqual(persisted, service.installation_id)

            second_factory = ClientFactory()
            second_service = self.make_service(directory, second_factory)
            self.assertEqual(persisted, second_service.installation_id)
            second_service.shutdown(1.0)

    def test_separate_installations_never_default_to_the_same_identity(self) -> None:
        with TemporaryDirectory() as first, TemporaryDirectory() as second:
            first_service = self.make_service(first, ClientFactory())
            second_service = self.make_service(second, ClientFactory())
            self.assertNotEqual(first_service.installation_id, second_service.installation_id)
            first_service.shutdown(1.0)
            second_service.shutdown(1.0)

    def test_existing_invalid_identity_is_not_overwritten_or_reset(self) -> None:
        with TemporaryDirectory() as directory:
            identity_path = Path(directory) / "installation-id"
            identity_path.write_text("existing-user-content\n", encoding="ascii")
            factory = ClientFactory()
            service = self.make_service(directory, factory)
            self.assertFalse(service.enabled)
            self.assertEqual("existing-user-content\n", identity_path.read_text(encoding="ascii"))
            self.assertEqual([], factory.calls)

    def test_properties_reject_pii_and_final_filter_removes_sdk_context(self) -> None:
        prohibited_keys = {
            "name", "email", "phone", "resume", "company", "employer", "job_title",
            "url", "search_terms", "application", "api_key", "database_path", "filename",
        }
        with TemporaryDirectory() as directory:
            factory = ClientFactory()
            service = self.make_service(directory, factory)
            self.assertFalse(
                service.capture(
                    "job_search_started",
                    {**valid_properties("job_search_started"), "email": "person@example.test"},
                )
            )
            for event in EVENT_SCHEMAS:
                self.assertTrue(service.capture(event, valid_properties(event)))
            service.shutdown(1.0)

            for _, kwargs in factory.client.captures:
                properties = kwargs["properties"]
                self.assertTrue(prohibited_keys.isdisjoint(properties))
                serialized = repr(properties).lower()
                for fragment in ("person@example", "example company", "https://", "secret-key", ".pdf"):
                    self.assertNotIn(fragment, serialized)

            message = {
                "event": "job_search_started",
                "distinct_id": service.installation_id,
                "properties": {
                    **valid_properties("job_search_started"),
                    "application_version": "20260804.1",
                    "$current_url": "https://private.example/jobs/secret",
                    "$ip": "192.0.2.1",
                    "$lib": "posthog-python",
                    "$geoip_disable": True,
                    "$process_person_profile": False,
                },
            }
            filtered = _filter_final_posthog_message(message)
            self.assertNotIn("$current_url", filtered["properties"])
            self.assertNotIn("$ip", filtered["properties"])

    def test_capture_failure_does_not_change_api_response(self) -> None:
        with TemporaryDirectory() as directory:
            factory = ClientFactory(RecordingClient(capture_action=lambda: (_ for _ in ()).throw(RuntimeError("offline"))))
            service = self.make_service(directory, factory)
            app = FastAPI()

            @app.get("/test")
            def test_route():
                service.capture("job_search_started", valid_properties("job_search_started"))
                return {"success": True}

            response = TestClient(app).get("/test")
            self.assertEqual(200, response.status_code)
            self.assertEqual({"success": True}, response.json())
            service.shutdown(1.0)

    def test_unreachable_client_never_blocks_an_api_response(self) -> None:
        release_capture = threading.Event()
        with TemporaryDirectory() as directory:
            factory = ClientFactory(RecordingClient(capture_action=lambda: release_capture.wait(1.0)))
            service = self.make_service(directory, factory)
            app = FastAPI()

            @app.get("/test")
            def test_route():
                service.capture("job_search_started", valid_properties("job_search_started"))
                return {"success": True}

            started_at = time.monotonic()
            response = TestClient(app).get("/test")
            elapsed = time.monotonic() - started_at
            self.assertEqual(200, response.status_code)
            self.assertLess(elapsed, 0.25)
            release_capture.set()
            service.shutdown(1.0)

    def test_shutdown_flush_is_bounded_and_safe(self) -> None:
        release_flush = threading.Event()
        with TemporaryDirectory() as directory:
            client = RecordingClient(flush_action=lambda: release_flush.wait(2.0))
            service = self.make_service(directory, ClientFactory(client))
            started_at = time.monotonic()
            completed = service.shutdown(0.05)
            elapsed = time.monotonic() - started_at
            self.assertFalse(completed)
            self.assertLess(elapsed, 0.25)
            self.assertEqual(1, client.flush_calls)
            release_flush.set()

    def test_exception_and_session_capture_are_not_enabled(self) -> None:
        with TemporaryDirectory() as directory:
            factory = ClientFactory()
            service = self.make_service(directory, factory)
            options = factory.calls[0]
            self.assertFalse(options["enable_exception_autocapture"])
            self.assertFalse(options["capture_exception_code_variables"])
            self.assertNotIn("session_recording", options)
            service.shutdown(1.0)
            self.assertEqual(0, factory.client.exception_calls)


if __name__ == "__main__":
    unittest.main()
