import unittest
import zipfile
from io import BytesIO
from unittest import mock

import tornado.web
from tornado.testing import AsyncHTTPTestCase

from archive_collector import ArchiveCollector
from log_collector import LogCollector
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
