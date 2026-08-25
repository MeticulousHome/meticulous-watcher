"""Cancellation behaviour of LogCollector.fetch_logs.

systemd.journal.Reader is mocked throughout: these tests exercise the
periodic ``cancelled`` check around the entry loop, not systemd itself.
"""

import unittest
from datetime import datetime, timedelta
from unittest import mock

from log_collector import ClientGone, LogCollector


class FakeJournalReader:
    """Stands in for systemd.journal.Reader: ignores every filter/seek call
    and just yields the entries it was built with, counting how many were
    actually pulled so a test can prove an aborted fetch stopped early."""

    def __init__(self, entries):
        self._entries = entries
        self.consumed = 0

    def log_level(self, *_args, **_kwargs):
        pass

    def add_match(self, *_args, **_kwargs):
        pass

    def add_disjunction(self):
        pass

    def seek_realtime(self, *_args, **_kwargs):
        pass

    def __iter__(self):
        for entry in self._entries:
            self.consumed += 1
            yield entry


def _entries(count):
    # All safely in the past relative to the "now" fetch_logs computes for
    # itself a moment later, so none trip the until_timestamp break this
    # module is not exercising.
    base = datetime.now().astimezone() - timedelta(hours=1)
    return [
        {
            "__REALTIME_TIMESTAMP": base + timedelta(seconds=i),
            "_SYSTEMD_UNIT": "meticulous-backend.service",
            "_TRANSPORT": "stdout",
            "MESSAGE": f"line {i}",
        }
        for i in range(count)
    ]


class FetchLogsCancellationTests(unittest.TestCase):
    def test_predicate_that_never_signals_matches_uncancelled_call(self):
        entries = _entries(1200)

        with mock.patch(
            "log_collector.journal.Reader", return_value=FakeJournalReader(entries)
        ):
            baseline = LogCollector.fetch_logs()

        with mock.patch(
            "log_collector.journal.Reader", return_value=FakeJournalReader(entries)
        ):
            with_predicate = LogCollector.fetch_logs(cancelled=lambda: False)

        self.assertEqual(baseline, with_predicate)
        self.assertEqual(len(with_predicate), 1200)

    def test_predicate_that_signals_part_way_raises_client_gone(self):
        reader = FakeJournalReader(_entries(1200))
        # Checks land at entry_index 0, 500, 1000 -- False, False, True
        # fires on the third, proving the interval check runs repeatedly
        # rather than only once at the very start.
        cancelled = mock.Mock(side_effect=[False, False, True])

        with mock.patch("log_collector.journal.Reader", return_value=reader):
            with self.assertRaises(ClientGone):
                LogCollector.fetch_logs(cancelled=cancelled)

        self.assertEqual(cancelled.call_count, 3)
        # Abandoned well short of the full 1200 -- the whole point is not
        # paying for entries after the client left.
        self.assertLess(reader.consumed, 1200)


if __name__ == "__main__":
    unittest.main()
