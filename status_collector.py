import subprocess
import shutil
import psutil
import time
from math import floor, log


class StatusCollector:

    @staticmethod
    def check_service_running(service):
        try:
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
            return {"status": "unknown", "exception": str(e)}

    @staticmethod
    def format_bytes(size):
        power = 0 if size <= 0 else floor(log(size, 1024))
        return f"{round(size / 1024 ** power, 2)} {['B', 'KB', 'MB', 'GB', 'TB'][int(power)]}"

    @staticmethod
    def get_disk_usage(path="/"):
        try:
            total, used, free = shutil.disk_usage(path=path)
            return {
                "total": StatusCollector.format_bytes(total),
                "used": StatusCollector.format_bytes(used),
                "free": StatusCollector.format_bytes(free),
            }
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def get_disk_usages():
        disc_usage = []
        try:
            discs = psutil.disk_partitions()
            disc_usage = [
                {
                    "mountpoint": disc.mountpoint,
                    "device": disc.device,
                    "usage": StatusCollector.get_disk_usage(disc.mountpoint),
                }
                for disc in discs
            ]
        except Exception:
            pass
        return disc_usage

    @staticmethod
    def get_uptime():
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

    @staticmethod
    def get_memory_usage():
        try:
            mem = psutil.virtual_memory()
            return {
                "total": StatusCollector.format_bytes(mem.total),
                "used": StatusCollector.format_bytes(mem.used),
                "free": StatusCollector.format_bytes(mem.free),
                "shared": StatusCollector.format_bytes(mem.shared),
            }
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def get_system_status():
        system_status = {
            "services": {
                "backend": StatusCollector.check_service_running("meticulous-backend"),
                "dial": StatusCollector.check_service_running("meticulous-dial"),
                "rauc": StatusCollector.check_service_running("rauc"),
                "meticulous-rauc": StatusCollector.check_service_running(
                    "meticulous-rauc"
                ),
                "rauc-hawkbit-updater": StatusCollector.check_service_running(
                    "rauc-hawkbit-updater"
                ),
                "nginx": StatusCollector.check_service_running("nginx"),
                "systemd-journald": StatusCollector.check_service_running(
                    "systemd-journald"
                ),
            },
            "uptime": StatusCollector.get_uptime(),
            "memoryUsage": StatusCollector.get_memory_usage(),
            "discs": StatusCollector.get_disk_usages(),
        }

        system_status["status"] = (
            "ok"
            if all(
                status.get("status") != "error"
                for status in system_status.get("services").values()
            )
            else "error"
        )

        return system_status
