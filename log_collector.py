from datetime import datetime, timedelta
import time as sys_time

from systemd import journal

from log_redactor import DEFAULT_KEY_PATH, RedactionCancelled, load_key, redact


class ClientGone(Exception):
    """The client vanished before or during its collection work.

    Raised by CollectorHandler.run_off_loop's pre-flight check (in
    watcher.py) when a request was still queued when its client
    disconnected, and from here by fetch_logs / format_logs_as_text when the
    client disconnects while a collection is already running. Defined in
    this module rather than watcher.py -- which imports it from here -- so
    LogCollector can raise it directly without an import cycle: watcher.py
    already imports LogCollector, so the reverse import cannot exist too.
    """


# "A few hundred iterations" per the design this implements: frequent enough
# that an aborted fetch stops within a fraction of a second of being
# signalled, rare enough that the check itself never shows up against a
# >100k-entry journal walk.
_CANCEL_CHECK_INTERVAL = 500


class LogCollector:

    @staticmethod
    def fetch_logs(
        filter_units="meticulous-backend.service",
        since_hours=24,
        until_hours=0,
        start_time=None,
        end_time=None,
        cancelled=None,
        timeout=None,
    ) -> tuple[list[dict], bool]:
        bound_timeout = min(float(timeout), 999.0) if timeout is not None else None
        if bound_timeout is not None and bound_timeout <= 0:
            raise ValueError("timeout must be greater than zero")

        try:
            j = journal.Reader()
            # Match journalctl's default behavior: include every priority,
            # including debug entries.
            j.log_level(journal.LOG_DEBUG)

            if isinstance(filter_units, str):
                filter_units = [filter_units]

            normalized_units = []
            if "*" not in filter_units:
                for filter_unit in filter_units:
                    if not filter_unit.endswith(".service"):
                        filter_unit += ".service"
                    if filter_unit not in normalized_units:
                        normalized_units.append(filter_unit)

            for index, filter_unit in enumerate(normalized_units):
                if index > 0:
                    j.add_disjunction()
                j.add_match(_SYSTEMD_UNIT=filter_unit)

            # Set time range
            now = datetime.now().astimezone()
            start_ts = start_time or now - timedelta(hours=since_hours)
            until_ts = end_time or now - timedelta(hours=until_hours)
            j.seek_realtime(start_ts)
            until_timestamp = until_ts.timestamp()

            start_monotonic_time = sys_time.monotonic()
            timed_out = False

            logs = []
            for entry_index, entry in enumerate(j):
                if entry_index % _CANCEL_CHECK_INTERVAL == 0:
                    if cancelled is not None and cancelled():
                        raise ClientGone()
                    if (
                        bound_timeout is not None
                        and sys_time.monotonic() - start_monotonic_time
                        >= bound_timeout
                    ):
                        timed_out = True
                        break

                if entry["__REALTIME_TIMESTAMP"].timestamp() >= until_timestamp:
                    break

                time = entry.get("__REALTIME_TIMESTAMP", "Unknown Timestamp")
                unit = entry.get("_SYSTEMD_UNIT", "")
                if unit != "":
                    unit = " : " + unit
                transport = entry.get("_TRANSPORT", "")
                message = entry.get("MESSAGE", "")

                logs.append(
                    {
                        "timestamp": time,
                        "transport": transport,
                        "unit": unit,
                        "message": message,
                        "formatted": f"{time} : {transport.ljust(7)}{unit} - {message}",
                    }
                )

            return logs, timed_out

        except ClientGone:
            raise
        except Exception as e:
            raise Exception(f"Log fetching error: {e}")

    @staticmethod
    def format_logs_as_text(
        logs, redaction_key=None, redaction_key_path=DEFAULT_KEY_PATH, cancelled=None
    ):
        """Format and redact logs before they leave the watcher.

        ``redaction_key`` is injectable for tests. Production callers load the
        persistent per-device key from ``redaction_key_path``. Any key-loading
        or redaction failure is deliberately allowed to propagate so request
        and archive handlers fail closed.

        ``cancelled``, if given, is forwarded to ``redact()``. The
        ``RedactionCancelled`` it can raise is translated to ``ClientGone``
        here, so every caller of this module only ever needs to watch for one
        cancellation exception.
        """

        logs_text = "\n".join(log["formatted"] for log in logs)
        key = (
            redaction_key if redaction_key is not None else load_key(redaction_key_path)
        )
        try:
            redacted_text, _ = redact(logs_text, key, cancelled=cancelled)
        except RedactionCancelled:
            raise ClientGone() from None
        return redacted_text
