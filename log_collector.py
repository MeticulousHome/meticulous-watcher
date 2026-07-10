from systemd import journal
from datetime import datetime, timedelta
import time as sys_time


class LogCollector:

    @staticmethod
    def fetch_logs(
        filter_unit="meticulous-backend.service", since_hours=24, until_hours=0, timeout=None
    ) -> tuple[list[dict], bool]:
        
        bound_timeout = min(timeout, 999) if timeout is not None else None

        try:
            j = journal.Reader()
            j.log_level(journal.LOG_INFO)

            # Add filter for specific unit
            if filter_unit != "*":
                if not filter_unit.endswith(".service"):
                    filter_unit += ".service"
                j.add_match(_SYSTEMD_UNIT=filter_unit)

            if until_hours != 0:
                until_ts = datetime.now() - timedelta(hours=until_hours)
            else:
                until_ts = datetime.now()

            start_monotonic_time = sys_time.monotonic()
            timed_out = False

            start_ts = datetime.now() - timedelta(hours=since_hours)
            # Set time range (going backwards in time)
            # Its more important to get the latest logs than to get the oldest ones as they are the closest to the current state of the system.
            j.seek_realtime(until_ts)

            logs = []
            while (entry := j.get_previous()):
                if bound_timeout and ( sys_time.monotonic() - start_monotonic_time > bound_timeout ):
                    timed_out = True
                    break
                
                # reached the start time, stop fetching logs
                if entry["__REALTIME_TIMESTAMP"].timestamp() < start_ts.timestamp():
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

            return list(reversed(logs)), timed_out

        except Exception as e:
            raise Exception(f"Log fetching error: {e}")

    @staticmethod
    def format_logs_as_text(logs):
        return "\n".join([log["formatted"] for log in logs])
