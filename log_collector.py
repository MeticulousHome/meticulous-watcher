from datetime import datetime, timedelta

from systemd import journal

from log_redactor import DEFAULT_KEY_PATH, load_key, redact


class LogCollector:

    @staticmethod
    def fetch_logs(
        filter_units="meticulous-backend.service",
        since_hours=24,
        until_hours=0,
        start_time=None,
        end_time=None,
    ):

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

            logs = []
            for entry in j:
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

            return logs

        except Exception as e:
            raise Exception(f"Log fetching error: {e}")

    @staticmethod
    def format_logs_as_text(
        logs, redaction_key=None, redaction_key_path=DEFAULT_KEY_PATH
    ):
        """Format and redact logs before they leave the watcher.

        ``redaction_key`` is injectable for tests. Production callers load the
        persistent per-device key from ``redaction_key_path``. Any key-loading
        or redaction failure is deliberately allowed to propagate so request
        and archive handlers fail closed.
        """

        logs_text = "\n".join(log["formatted"] for log in logs)
        key = (
            redaction_key if redaction_key is not None else load_key(redaction_key_path)
        )
        redacted_text, _ = redact(logs_text, key)
        return redacted_text
