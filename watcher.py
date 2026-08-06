from tornado.options import define, options, parse_command_line
from datetime import datetime
import tornado.web
import tornado.ioloop
import traceback
import sdnotify

from log_collector import LogCollector
from status_collector import StatusCollector
from archive_collector import ArchiveCollector


class LogsHandler(tornado.web.RequestHandler):
    def get(self):
        try:
            self.set_header("Content-Type", "text/plain")
            self.set_header("Cache-Control", "no-store")

            filter_params = self.get_arguments("filter")
            if not filter_params:
                filter_params = ["meticulous-backend.service"]

            start_param = self.get_argument("start", default=None)
            end_param = self.get_argument("end", default=None)

            if start_param is not None or end_param is not None:
                if start_param is None or end_param is None:
                    self.set_status(400)
                    self.write("Both start and end parameters are required.")
                    return

                try:
                    start = datetime.fromisoformat(start_param)
                    end = datetime.fromisoformat(end_param)
                except ValueError:
                    self.set_status(400)
                    self.write("Invalid start or end parameter. Use ISO 8601.")
                    return

                if start.tzinfo is None or end.tzinfo is None:
                    self.set_status(400)
                    self.write("Start and end must include a timezone offset.")
                    return

                if start >= end:
                    self.set_status(400)
                    self.write("Start must be before end.")
                    return

                logs = LogCollector.fetch_logs(
                    filter_params, start_time=start, end_time=end
                )
            else:
                hours = self.get_argument("hours", default="24")
                since = self.get_argument("since", default=hours)
                until = self.get_argument("until", default="0")

                try:
                    since = int(since)
                except ValueError:
                    self.set_status(400)
                    self.write("Invalid since or hours parameter. Must be an integer.")
                    return
                try:
                    until = int(until)
                except ValueError:
                    self.set_status(400)
                    self.write("Invalid until parameter. Must be an integer.")
                    return

                if since < 0 or until < 0 or since < until:
                    self.set_status(400)
                    self.write("Invalid time range.")
                    return

                logs = LogCollector.fetch_logs(filter_params, since, until)
            logs_text = LogCollector.format_logs_as_text(logs)
            self.write(logs_text)
            self.finish()

        except Exception as e:
            self.set_status(500)
            self.write(f"Log fetching error: {e}")


class StatusHandler(tornado.web.RequestHandler):
    def get(self):
        try:
            self.set_header("Content-Type", "application/json")
            system_status = StatusCollector.get_system_status()
            self.write(system_status)
            self.finish()
        except Exception as e:
            self.set_status(500)
            self.write({"error": f"Status collection error: {e}"})


class ArchiveHandler(tornado.web.RequestHandler):
    async def get(self):
        try:
            # Create the archive with all data
            zip_data, zip_filename = ArchiveCollector.create_archive()

            # Set headers for file download
            self.set_header("Content-Type", "application/zip")
            self.set_header(
                "Content-Disposition", f"attachment; filename={zip_filename}"
            )
            self.set_header("Content-Length", str(len(zip_data)))

            # Stream the zip data to the client in chunks
            chunk_size = 64 * 1024  # 64KB chunks
            for i in range(0, len(zip_data), chunk_size):
                chunk = zip_data[i : i + chunk_size]
                self.write(chunk)
                await self.flush()  # Flush each chunk to the client

            self.finish()

        except Exception as e:
            self.set_status(500)
            self.write(f"Archive creation error: {e}")


def main():
    try:
        notifier = sdnotify.SystemdNotifier()

        # Notify that it is starting
        notifier.notify("STATUS=Initializing meticulous watcher...")

        parse_command_line()

        app = tornado.web.Application(
            [
                (r"/logs", LogsHandler),
                (r"/status", StatusHandler),
                (r"/archive", ArchiveHandler),
                (r"", tornado.web.RedirectHandler, {"url": "/"}),
            ],
        )

        app.listen(options.port)

        # Notify that it is ready
        notifier.notify("READY=1")
        notifier.notify(f"STATUS=Meticulous watcher running on port {options.port}")

        print(f"Listening on port {options.port}")
        tornado.ioloop.IOLoop.current().start()

    except Exception as e:
        if "notifier" in locals():
            notifier.notify(f"STATUS=Watcher initialization failed: {str(e)}")
        raise


# execution phase
define("port", default=3000, help="run on the given port", type=int)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc()
