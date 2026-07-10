from tornado.options import define, options, parse_command_line
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

            filter_param = self.get_argument(
                "filter", default="meticulous-backend.service"
            )

            hours = self.get_argument("hours", default="24")
            since = self.get_argument("since", default=hours)
            until = self.get_argument("until", default="0")
            timeout = self.get_argument("timeout", default=None)

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
            
            try:
                timeout = int(timeout) if timeout is not None else None
            except ValueError:
                self.set_status(400)
                self.write("Invalid timeout parameter. Must be an integer.")
                return

            (logs, fetch_timed_out) = LogCollector.fetch_logs(filter_param, since, until, timeout)
            if fetch_timed_out:
                self.set_status(200)
                self.write(
                    "Warning: Log fetching timed out. The logs may be incomplete.\n\n"
                )
                self.set_header("X-Log-Timed-Out", "true")
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
