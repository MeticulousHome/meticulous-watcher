import threading
import unittest
import zipfile
from datetime import datetime
from io import BytesIO
from unittest import mock

import tornado.web
from tornado.testing import AsyncHTTPTestCase

from archive_collector import ArchiveCollector
from log_collector import ClientGone, LogCollector
from watcher import LogsHandler


TEST_KEY = bytes(range(32))


def synthetic_logs():
    return [
        {
            "formatted": (
                "2026-07-28 01:42:05 : service - "
                "SSID='HomeNet' root_password: synthetic-password"
            )
        }
    ]


class LogsHandlerTests(AsyncHTTPTestCase):
    def get_app(self):
        return tornado.web.Application([(r"/logs", LogsHandler)])

    @mock.patch.object(LogCollector, "fetch_logs", return_value=synthetic_logs())
    @mock.patch("log_collector.load_key", return_value=TEST_KEY)
    def test_logs_endpoint_only_returns_redacted_text(self, _load_key, _fetch_logs):
        response = self.fetch("/logs")

        self.assertEqual(response.code, 200)
        body = response.body.decode()
        self.assertNotIn("HomeNet", body)
        self.assertNotIn("synthetic-password", body)
        self.assertIn("[SSID_", body)
        self.assertIn("[REDACTED]", body)

    @mock.patch.object(LogCollector, "fetch_logs", return_value=synthetic_logs())
    @mock.patch.object(
        LogCollector,
        "format_logs_as_text",
        side_effect=RuntimeError("synthetic redaction failure"),
    )
    def test_logs_endpoint_fails_closed(self, _format_logs, _fetch_logs):
        response = self.fetch("/logs")

        self.assertEqual(response.code, 500)
        self.assertNotIn(b"HomeNet", response.body)
        self.assertNotIn(b"synthetic-password", response.body)

    @mock.patch.object(LogCollector, "fetch_logs", return_value=[])
    @mock.patch("log_collector.load_key", return_value=TEST_KEY)
    def test_logs_endpoint_accepts_multiple_units_and_absolute_range(
        self, _load_key, fetch_logs
    ):
        response = self.fetch(
            "/logs?filter=meticulous-backend&filter=nginx"
            "&start=2026-07-25T00%3A00%3A00-06%3A00"
            "&end=2026-07-26T00%3A00%3A00-06%3A00"
        )

        self.assertEqual(response.code, 200)
        fetch_logs.assert_called_once_with(
            ["meticulous-backend", "nginx"],
            # A live cancellation predicate accompanies every fetch now --
            # see LogsHandlerCancellationTests below for what it is and how
            # it is used.
            cancelled=mock.ANY,
            start_time=datetime.fromisoformat("2026-07-25T00:00:00-06:00"),
            end_time=datetime.fromisoformat("2026-07-26T00:00:00-06:00"),
        )

    def test_logs_endpoint_rejects_incomplete_absolute_range(self):
        response = self.fetch("/logs?start=2026-07-25T00%3A00%3A00-06%3A00")

        self.assertEqual(response.code, 400)

    def test_logs_endpoint_rejects_invalid_relative_range(self):
        response = self.fetch("/logs?since=1&until=2")

        self.assertEqual(response.code, 400)


class LogsHandlerCancellationTests(AsyncHTTPTestCase):
    def get_app(self):
        return tornado.web.Application([(r"/logs", LogsHandler)])

    @mock.patch.object(LogCollector, "fetch_logs")
    def test_disconnect_mid_collection_completes_without_writing_body(self, fetch_logs):
        """LogsHandler wires run_off_loop(cancellable=True) through to
        LogCollector.fetch_logs. If the collection observes the client is
        gone and abandons via ClientGone, the request must complete quietly:
        no response body, no error surfaced to Tornado."""

        def observe_predicate_then_abandon(*_args, cancelled=None, **_kwargs):
            # run_off_loop must supply a live predicate backed by the
            # purpose-built Event -- not a plain attribute read -- and it
            # must read "not disconnected" before anything happens.
            self.assertIsNotNone(cancelled)
            self.assertIsInstance(cancelled.__self__, threading.Event)
            self.assertFalse(cancelled())
            raise ClientGone()

        fetch_logs.side_effect = observe_predicate_then_abandon

        response = self.fetch("/logs")

        self.assertEqual(response.code, 200)
        self.assertEqual(response.body, b"")


class ArchiveRedactionTests(unittest.TestCase):
    @mock.patch.object(LogCollector, "fetch_logs", return_value=synthetic_logs())
    @mock.patch("log_collector.load_key", return_value=TEST_KEY)
    def test_archive_log_entries_are_redacted(self, _load_key, _fetch_logs):
        archive_data, _ = ArchiveCollector.create_archive()

        with zipfile.ZipFile(BytesIO(archive_data)) as archive:
            log_entries = [
                name
                for name in archive.namelist()
                if name.startswith("logs_") and "_error_" not in name
            ]
            self.assertEqual(len(log_entries), 2)
            for name in log_entries:
                body = archive.read(name).decode()
                self.assertNotIn("HomeNet", body)
                self.assertNotIn("synthetic-password", body)

    @mock.patch.object(LogCollector, "fetch_logs", return_value=synthetic_logs())
    @mock.patch.object(
        LogCollector,
        "format_logs_as_text",
        side_effect=RuntimeError("synthetic redaction failure"),
    )
    def test_redaction_failure_aborts_archive(self, _format_logs, _fetch_logs):
        with self.assertRaisesRegex(RuntimeError, "redaction failure"):
            ArchiveCollector.create_archive()


if __name__ == "__main__":
    unittest.main()
