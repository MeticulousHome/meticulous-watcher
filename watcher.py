from tornado.options import define, options, parse_command_line
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import threading
import tornado.web
import tornado.ioloop
import traceback
import sdnotify

from auth import AuthMixin
from log_collector import ClientGone, LogCollector
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


class CollectorHandler(AuthMixin, tornado.web.RequestHandler):
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
        # Purpose-built cross-thread signal for the deep, in-collection
        # cancellation checks (LogCollector.fetch_logs, redact). Those run in
        # COLLECTOR_POOL, not on the IOLoop that delivers on_connection_close,
        # so they read this Event rather than the client_disconnected
        # attribute above: Event.set()/is_set() is the documented primitive
        # for that cross-thread handoff, not an attribute read relying on GIL
        # happenstance.
        self._client_gone = threading.Event()

    def set_default_headers(self):
        # Diagnostic responses can contain sensitive machine state. Apply the
        # policy centrally so success, validation errors and authorization
        # failures from every collector route are all non-cacheable.
        self.set_header("Cache-Control", "no-store")

    def on_connection_close(self):
        super().on_connection_close()
        self.client_disconnected = True
        self._client_gone.set()

    async def run_off_loop(self, func, *args, cancellable=False):
        """Run ``func`` in the collector pool, skipping it if the client left.

        A thread already running cannot be cancelled, but anything still queued
        behind it can be dropped. That is what stops a client that times out and
        retries from stacking up several full collections, each of which would
        otherwise run to completion for nobody.

        When ``cancellable`` is True, ``func`` is additionally called with a
        ``cancelled`` keyword argument: a zero-argument callable reporting True
        once this handler's client has disconnected. Work that opts in is
        expected to check it periodically and abandon by raising ClientGone;
        handlers whose work is a single opaque call (StatusHandler,
        ArchiveHandler) do not opt in and are unaffected.
        """

        def guarded():
            if self.client_disconnected:
                raise ClientGone()
            if cancellable:
                return func(*args, cancelled=self._client_gone.is_set)
            return func(*args)

        return await tornado.ioloop.IOLoop.current().run_in_executor(
            COLLECTOR_POOL, guarded
        )


def _parse_timeout(handler):
    timeout_param = handler.get_argument("timeout", default=None)
    try:
        timeout = int(timeout_param) if timeout_param is not None else None
    except ValueError as error:
        raise ValueError("Invalid timeout parameter. Must be an integer.") from error
    if timeout is not None and timeout <= 0:
        raise ValueError("Invalid timeout parameter. Must be greater than zero.")
    return timeout


def _parse_log_range(handler):
    start_param = handler.get_argument("start", default=None)
    end_param = handler.get_argument("end", default=None)

    if start_param is not None or end_param is not None:
        if start_param is None or end_param is None:
            raise ValueError("Both start and end parameters are required.")
        try:
            start = datetime.fromisoformat(start_param)
            end = datetime.fromisoformat(end_param)
        except ValueError as error:
            raise ValueError("Invalid start or end parameter. Use ISO 8601.") from error
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Start and end must include a timezone offset.")
        if start >= end:
            raise ValueError("Start must be before end.")
        return {"start_time": start, "end_time": end}

    hours = handler.get_argument("hours", default="24")
    since_param = handler.get_argument("since", default=hours)
    until_param = handler.get_argument("until", default="0")
    try:
        since = int(since_param)
    except ValueError as error:
        raise ValueError(
            "Invalid since or hours parameter. Must be an integer."
        ) from error
    try:
        until = int(until_param)
    except ValueError as error:
        raise ValueError("Invalid until parameter. Must be an integer.") from error
    if since < 0 or until < 0 or since < until:
        raise ValueError("Invalid time range.")
    return {"since_hours": since, "until_hours": until}


class LogsHandler(CollectorHandler):
    async def get(self):
        try:
            self.set_header("Content-Type", "text/plain")

            filter_params = self.get_arguments("filter")
            if not filter_params:
                filter_params = ["meticulous-backend.service"]

            try:
                timeout = _parse_timeout(self)
                fetch_kwargs = _parse_log_range(self)
            except ValueError as error:
                self.set_status(400)
                self.write(str(error))
                return

            def collect(cancelled):
                # Fetch and format in the same worker call: the entry list is
                # the largest object in flight and there is no reason to hand
                # it back to the IOLoop thread between the two steps.
                logs, fetch_timed_out = LogCollector.fetch_logs(
                    filter_params,
                    cancelled=cancelled,
                    timeout=timeout,
                    **fetch_kwargs,
                )
                logs_text = LogCollector.format_logs_as_text(
                    logs,
                    cancelled=cancelled,
                )
                return logs_text, fetch_timed_out

            logs_text, fetch_timed_out = await self.run_off_loop(
                collect, cancellable=True
            )
            if fetch_timed_out:
                self.set_status(200)
                self.write(
                    "Warning: Log fetching timed out. The logs may be incomplete.\n\n"
                )
                self.set_header("X-Log-Timed-Out", "true")
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
