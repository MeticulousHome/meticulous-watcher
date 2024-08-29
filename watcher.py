# meticulous watcher script for the service
# this Script will handle emergency updates for the backend

from tornado.options import define, options, parse_command_line
import tornado.web
import tornado.ioloop
import os
import subprocess
import traceback
import zipfile
import shutil
from systemd import journal
import psutil
import time


from math import floor, log

WORK_DIR = "/opt"
TMP_DIR = "/tmp"
UPDATE_FILE = os.path.join(TMP_DIR, "meticulous-backend-update.zip")
BACKEND_FOLDER = "meticulous-backend"


# HTTP SERVER HANDLING
class UploadHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header(
            "Access-Control-Allow-Origin", "*"
        )  # allows requests from the dashboard
        self.set_header(
            "Access-Control-Allow-Headers",
            "x-requested-with, Content-MD5, Content-Length",
        )
        self.set_header("Access-Control-Allow-Methods", "POST")

    def post(self):
        if "file" not in self.request.files:
            self.set_status(400)
            self.finish("No file uploaded.")
            return
        self.set_status(200)
        self.write("File received!")
        uploaded_file = self.request.files["file"][0]["body"]
        with open(os.path.expanduser(UPDATE_FILE), "wb") as file:
            file.write(uploaded_file)

        self.startUpdate()

    def startUpdate(self):

        subprocess.run("systemctl stop meticulous-backend", shell=True)

        # extract the directory of the update
        success = self.unzip_update(UPDATE_FILE, TMP_DIR)

        os.remove(UPDATE_FILE)
        if success:
            print("Update succeeded, replacing backend")
            try:
                shutil.rmtree(os.path.join(WORK_DIR, BACKEND_FOLDER))
            except FileNotFoundError:
                self.write(
                    "Existing backend folder not found. Please check your path configs"
                )
                print(
                    "Existing backend folder not found. Please check your path configs"
                )
            shutil.move(
                os.path.join(TMP_DIR, BACKEND_FOLDER),
                os.path.join(WORK_DIR, BACKEND_FOLDER),
            )

        # restart
        subprocess.run("systemctl start meticulous-backend", shell=True)
        print("Backend restarted")

    def unzip_update(self, zip_path, output_folder):
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            # List of file paths in the zip, excluding those in the ignore_folder
            file_paths = zip_ref.namelist()
            for file in file_paths:
                if not file.startswith(BACKEND_FOLDER):
                    self.set_status(400)
                    self.finish("file contains wrong update folder")
                    return False
                if ".." in file:
                    self.set_status(400)
                    self.finish("file contains traversel path")
                    return False

            # Iterate over the file list and extract each file
            for file in file_paths:
                # Extract the file to the specified directory
                zip_ref.extract(file, output_folder)
                print(file)
            return True
        return False


class LogsHandler(tornado.web.RequestHandler):
    def get(self):
        try:
            self.set_header("Content-Type", "text/plain")
            j = journal.Reader()
            j.this_boot()
            j.log_level(journal.LOG_INFO)

            filter_param = self.get_argument(
                "filter", default="meticulous-backend.service"
            )
            if filter_param != "*":
                if not filter_param.endswith(".service"):
                    filter_param += ".service"
                j.add_match(_SYSTEMD_UNIT=filter_param)

            for entry in j:
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
        for line in stdout_list:
            if "Active:" in line.strip():
                if "(running)" in line:
                    return {"status": "running"}
                else:
                    break
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
                status.get("status") == "running"
                for status in systemStatus.get("services").values()
            )
            else "error"
        )
        self.write(systemStatus)
        self.finish()


def main():
    parse_command_line()

    app = tornado.web.Application(
        [
            (r"/update", UploadHandler),
            (r"/logs", LogsHandler),
            (r"/status", StatusHandler),
            (r"", tornado.web.RedirectHandler, {"url": "/"}),
        ],
    )

    app.listen(options.port)
    print(f"Listening on port {options.port}")
    tornado.ioloop.IOLoop.current().start()


# execution phase
define("port", default=3000, help="run on the given port", type=int)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc()
