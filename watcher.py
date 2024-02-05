# meticulous watcher script for the service
# this Script will handle emergency updates for the backend

from tornado.options import define, options, parse_command_line
import tornado.web
import tornado.ioloop
import hashlib
import os
import subprocess
import threading
import traceback
import socketio
import zipfile
import shutil

WORK_DIR="/opt"
TMP_DIR="/tmp"
UPDATE_FILE = os.path.join(TMP_DIR, "meticulous-backend-update.zip")
BACKEND_FOLDER = "meticulous-backend"

# HTTP SERVER HANDLING
class UploadHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")     #allows requests from the dashboard
        self.set_header("Access-Control-Allow-Headers", "x-requested-with, Content-MD5, Content-Length")
        self.set_header('Access-Control-Allow-Methods', 'POST')

    def post(self):
        if 'file' not in self.request.files:
            self.set_status(400)
            self.finish("No file uploaded.")
            return
        self.set_status(200)
        self.write("File received!")
        uploaded_file = self.request.files['file'][0]['body']
        with open(os.path.expanduser(UPDATE_FILE), 'wb') as file:
            file.write(uploaded_file)

        self.startUpdate()

    def startUpdate(self):

        subprocess.run("systemctl stop meticulous-backend", shell=True)

        #extract the directory of the update 
        success = self.unzip_update(UPDATE_FILE, TMP_DIR)

        os.remove(UPDATE_FILE)
        if success:
            print("Update succeeded, replacing backend")
            try:
                shutil.rmtree(os.path.join(WORK_DIR, BACKEND_FOLDER))
            except FileNotFoundError:
                self.write("Existing backend folder not found. Please check your path configs")
                print("Existing backend folder not found. Please check your path configs")
            shutil.move(os.path.join(TMP_DIR, BACKEND_FOLDER), os.path.join(WORK_DIR, BACKEND_FOLDER))

        # restart
        subprocess.run("systemctl start meticulous-backend", shell=True)
        print("Backend restarted")


    def unzip_update(self, zip_path, output_folder):
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
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


def main():

    parse_command_line()

    sio = socketio.AsyncServer(cors_allowed_origins='*', async_mode='tornado')

    app = tornado.web.Application(
        [
            (r"/update", UploadHandler),
            (r"/socket.io/", socketio.get_tornado_handler(sio)),
            (r'/(.*)', tornado.web.StaticFileHandler, {"default_filename": "index.html","path": os.path.join(WORK_DIR, "meticulous-dashboard")}),
            (r'', tornado.web.RedirectHandler, {"url":"/"}),
        ],
    )

    app.listen(options.port)
    print(f"Listening on port {options.port}")
    tornado.ioloop.IOLoop.current().start()


#execution phase
define("port", default=3000, help="run on the given port", type=int)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except:
        traceback.print_exc()
