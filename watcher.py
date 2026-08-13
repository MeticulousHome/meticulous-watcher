from tornado.options import define, options, parse_command_line
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import tornado.web
import tornado.ioloop
import traceback
import sdnotify

from log_collector import LogCollector
from status_collector import StatusCollector
from archive_collector import ArchiveCollector

# One worker on purpose. Collection is almost entirely GIL-holding work --
# regex passes in the redactor, per-field entry conversion in systemd-python --
# so extra threads buy no parallelism, they only multiply peak memory: a 24h
# "filter=*" fetch already holds the journal entries, the joined text and the
# redacted copy at once. IOLoop.run_in_executor's default pool is
# cpu_count() * 5 threads, which is exactly the wrong shape for this workload.
COLLECTOR_POOL = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="watcher-collector"
)


class ClientGone(Exception):
    """The client vanished before its work reached the front of the queue."""


class CollectorHandler(tornado.web.RequestHandler):
    """Base for handlers whose work is too slow to run on the IOLoop.

    Collection blocks for a long time: a 24h ``filter=*`` fetch walks the whole
    journal and then redacts megabytes of text. Run inline it froze the single
    IOLoop, so Tornado could not accept connections, could not notice that a
    client had given up, and could not cancel anything -- every request queued
    behind it still ran to completion for a client that was long gone, which is
    why CPU stayed pinned for minutes after a request "finished".

    Moving the work to a thread keeps the loop free, which is what makes
    on_connection_close fire at all: it is delivered by the same loop the work
    used to block.
    """

    def initialize(self):
        self.client_disconnected = False

    def on_connection_close(self):
        super().on_connection_close()
        self.client_disconnected = True

    async def run_off_loop(self, func, *args):
        """Run ``func`` in the collector pool, skipping it if the client left.

        A thread already running cannot be cancelled, but anything still queued
        behind it can be dropped. That is what stops a client that times out and
        retries from stacking up several full collections, each of which would
        otherwise run to completion for nobody.
        """

        def guarded():
            if self.client_disconnected:
                raise ClientGone()
            return func(*args)

        return await tornado.ioloop.IOLoop.current().run_in_executor(
            COLLECTOR_POOL, guarded
        )


class LogsHandler(CollectorHandler):
    async def get(self):
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

                fetch_kwargs = {"start_time": start, "end_time": end}
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

                fetch_kwargs = {"since_hours": since, "until_hours": until}

            def collect():
                # Fetch and format in the same worker call: the entry list is
                # the largest object in flight and there is no reason to hand
                # it back to the IOLoop thread between the two steps.
                return LogCollector.format_logs_as_text(
                    LogCollector.fetch_logs(filter_params, **fetch_kwargs)
                )

            logs_text = await self.run_off_loop(collect)
            self.write(logs_text)
            self.finish()

        except ClientGone:
            return
        except Exception as e:
            self.set_status(500)
            self.write(f"Log fetching error: {e}")


class StatusHandler(CollectorHandler):
    async def get(self):
        try:
            self.set_header("Content-Type", "application/json")
            system_status = await self.run_off_loop(StatusCollector.get_system_status)
            self.write(system_status)
            self.finish()
        except ClientGone:
            return
        except Exception as e:
            self.set_status(500)
            self.write({"error": f"Status collection error: {e}"})


class ArchiveHandler(CollectorHandler):
    async def get(self):
        try:
            # Create the archive with all data
            zip_data, zip_filename = await self.run_off_loop(
                ArchiveCollector.create_archive
            )

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

        except ClientGone:
            return
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
