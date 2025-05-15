# meticulous watcher script for the service
# this Script will handle emergency updates for the backend

from tornado.options import define, options, parse_command_line
import tornado.web
import tornado.ioloop
import subprocess
import traceback
import shutil
from systemd import journal
import psutil
import time
import sdnotify
from datetime import datetime, timedelta

from math import floor, log


class LogsHandler(tornado.web.RequestHandler):
    def get(self):
        try:
            self.set_header("Content-Type", "text/plain")
            j = journal.Reader()
            j.log_level(journal.LOG_INFO)

            filter_param = self.get_argument(
                "filter", default="meticulous-backend.service"
            )

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

            if filter_param != "*":
                if not filter_param.endswith(".service"):
                    filter_param += ".service"
                j.add_match(_SYSTEMD_UNIT=filter_param)

            j.seek_realtime(datetime.now() - timedelta(hours=since))

            if until != 0:
                until = datetime.now() - timedelta(hours=until)
            else
                until = datetime.now()

            for entry in j:
                if until != 0 and entry['__REALTIME_TIMESTAMP'] > until:
                    break
                time = entry.get("__REALTIME_TIMESTAMP", "Unknown Timestamp")
                unit = entry.get("_SYSTEMD_UNIT", "")
                if unit != "":
                    unit = " : " + unit
                transport = entry.get("_TRANSPORT", "")
                message = entry.get("MESSAGE", "")
                self.write(f"{time} : {transport.ljust(7)}{unit} - {message}\n")
            self.finish()
        except Exception as e:
            self.set_status(500)
            self.write(f"Log fetching error: {e}")


def checkServiceRunning(service):
    try:
        """Return True if service is running"""
        if not service.endswith(".service"):
            service += ".service"
        cmd = f"/bin/systemctl status {service}"
        proc = subprocess.Popen(
            cmd,
            shell=True,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            encoding="utf8",
        )
        output = proc.communicate()[0]
        if output == "":
            output = proc.communicate()[1]
        stdout_list = output.split("\n")

        exited = False
        for line in stdout_list:
            if "Active:" in line.strip():
                if "(running)" in line:
                    return {"status": "running"}
                elif "active (exited)" in line:
                    exited = True
            if "Process:" in line.strip() and exited:
                if "status=0/SUCCESS" in line:
                    return {"status": "exited"}
        return {"status": "error", "message": output}

    except Exception as e:
        print(f"Error checking if service is running: {e}")
        return {"status": "unknown", "exception": e}


def format_bytes(size):
    power = 0 if size <= 0 else floor(log(size, 1024))
    return (
        f"{round(size / 1024 ** power, 2)} {['B', 'KB', 'MB', 'GB', 'TB'][int(power)]}"
    )


def getDiskUsage(path="/"):
    try:
        total, used, free = shutil.disk_usage(path=path)
        return {
            "total": format_bytes(total),
            "used": format_bytes(used),
            "free": format_bytes(free),
        }
    except Exception as e:
        return {"error": str(e)}


def getDiskUsages():
    discUsage = []
    try:
        discs = psutil.disk_partitions()
        discUsage = [
            {
                "mountpoint": disc.mountpoint,
                "device": disc.device,
                "usage": getDiskUsage(disc.mountpoint),
            }
            for disc in discs
        ]
    except Exception:
        pass
    return discUsage


def getUptime():
    try:
        seconds = int(time.time() - psutil.boot_time())
        minutes = seconds // 60
        hours = minutes // 60

        days = hours // 24

        hours %= 24
        minutes %= 60
        seconds %= 60
        uptime = f"{days} days, {hours} hours {minutes} minutes {seconds} seconds"

        return uptime
    except Exception as e:
        return str(e)


def getMemoryUsage():
    try:
        mem = psutil.virtual_memory()
        return {
            "total": format_bytes(mem.total),
            "used": format_bytes(mem.used),
            "free": format_bytes(mem.free),
            "shared": format_bytes(mem.shared),
        }
    except Exception as e:
        return {"error": str(e)}


class StatusHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "application/json")

        systemStatus = {
            "services": {
                "backend": checkServiceRunning("meticulous-backend"),
                "dial": checkServiceRunning("meticulous-dial"),
                "rauc": checkServiceRunning("rauc"),
                "meticulous-rauc": checkServiceRunning("meticulous-rauc"),
                "rauc-hawkbit-updater": checkServiceRunning("rauc-hawkbit-updater"),
                "nginx": checkServiceRunning("nginx"),
                "systemd-journald": checkServiceRunning("systemd-journald"),
            },
            "uptime": getUptime(),
            "memoryUsage": getMemoryUsage(),
            "discs": getDiskUsages(),
        }

        systemStatus["status"] = (
            "ok"
            if all(
                status.get("status") != "error"
                for status in systemStatus.get("services").values()
            )
            else "error"
        )
        self.write(systemStatus)
        self.finish()


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
