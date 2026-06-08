from systemd import journal
from datetime import datetime, timedelta


class LogCollector:

    @staticmethod
    def fetch_logs(
        filter_unit="meticulous-backend.service", since_hours=24, until_hours=0
    ):

        try:
            j = journal.Reader()
            j.log_level(journal.LOG_INFO)

            # Add filter for specific unit
            if filter_unit != "*":
                if not filter_unit.endswith(".service"):
                    filter_unit += ".service"
                j.add_match(_SYSTEMD_UNIT=filter_unit)

            # Set time range
            j.seek_realtime(datetime.now() - timedelta(hours=since_hours))

            if until_hours != 0:
                until_ts = datetime.now() - timedelta(hours=until_hours)
            else:
                until_ts = datetime.now()

            logs = []
            for entry in j:
                if (
                    until_hours != 0
                    and entry["__REALTIME_TIMESTAMP"].timestamp() > until_ts.timestamp()
                ):
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
    def format_logs_as_text(logs):
        return "\n".join([log["formatted"] for log in logs])
