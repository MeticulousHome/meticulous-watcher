import os
import zipfile
import json
from io import BytesIO
from datetime import datetime
from log_collector import LogCollector
from status_collector import StatusCollector


class ArchiveCollector:

    @staticmethod
    def collect_history_files(history_path="/meticulous-user/history"):
        files = []
        if os.path.exists(history_path) and os.path.isdir(history_path):
            for root, dirs, filenames in os.walk(history_path):
                # Add directories first
                for dirname in dirs:
                    dir_path = os.path.join(root, dirname)
                    archive_path = os.path.relpath(dir_path, history_path)
                    files.append((dir_path, f"history/{archive_path}/"))

                # Add files
                for filename in filenames:
                    file_path = os.path.join(root, filename)
                    # Create relative path for archive
                    archive_path = os.path.relpath(file_path, history_path)
                    files.append((file_path, f"history/{archive_path}"))
        return files

    @staticmethod
    def create_archive():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Create in-memory zip file
        zip_buffer = BytesIO()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:

            # Add logs for meticulous-backend service
            try:
                backend_logs, fetch_timed_out = LogCollector.fetch_logs(
                    "meticulous-backend", 24, 0, timeout=180
                )
            except Exception as e:
                zipf.writestr(
                    f"logs_meticulous-backend_error_{timestamp}.txt",
                    f"Error fetching backend logs: {str(e)}",
                )
            else:
                # Keep redaction outside the collection exception handler:
                # a redaction failure must abort archive creation, never fall
                # back to an archive without safe logs.
                backend_logs_text = LogCollector.format_logs_as_text(backend_logs)
                timeout_suffix = (
                    "_timed_out_possible_incomplete_logs" if fetch_timed_out else ""
                )
                zipf.writestr(
                    f"logs_meticulous-backend_{timestamp}{timeout_suffix}.txt",
                    backend_logs_text,
                )

            # Add logs for meticulous-dial service
            try:
                dial_logs, fetch_timed_out = LogCollector.fetch_logs(
                    "meticulous-dial", 24, 0, timeout=180
                )
            except Exception as e:
                zipf.writestr(
                    f"logs_meticulous-dial_error_{timestamp}.txt",
                    f"Error fetching dial logs: {str(e)}",
                )
            else:
                dial_logs_text = LogCollector.format_logs_as_text(dial_logs)
                timeout_suffix = (
                    "_timed_out_possible_incomplete_logs" if fetch_timed_out else ""
                )
                zipf.writestr(
                    f"logs_meticulous-dial_{timestamp}{timeout_suffix}.txt",
                    dial_logs_text,
                )

            # Add status
            try:
                status = StatusCollector.get_system_status()
                status_json = json.dumps(status, indent=2, default=str)
                zipf.writestr(f"status_{timestamp}.json", status_json)
            except Exception as e:
                error_status = {"error": f"Error collecting status: {str(e)}"}
                zipf.writestr(
                    f"status_error_{timestamp}.json", json.dumps(error_status, indent=2)
                )

            # Add history files
            try:
                history_files = ArchiveCollector.collect_history_files(
                    "/meticulous-user/history"
                )
                for file_path, archive_path in history_files:
                    try:
                        if archive_path.endswith("/"):
                            # This is a directory, create it in the archive
                            zipf.writestr(archive_path, "")
                        else:
                            # This is a file, add it normally
                            zipf.write(file_path, archive_path)
                    except Exception as e:
                        # Create error file for each file that fails
                        error_msg = f"Error adding {file_path}: {str(e)}"
                        zipf.writestr(
                            f"history_errors/{archive_path}_error.txt", error_msg
                        )
            except Exception as e:
                zipf.writestr(
                    "history_error.txt", f"Error collecting history files: {str(e)}"
                )

        zip_buffer.seek(0)
        return zip_buffer.getvalue(), f"meticulous_archive_{timestamp}.zip"
