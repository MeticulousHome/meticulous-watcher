# meticulous watcher script for the service
# this Script will handle the following tasks
#
# Update the firmware
# Check if the  backend is live
#
# HOW
#
# an implementation of Pipes will be done to communicate
# backend process and watcher process
# 
# there will be:
#
# pipe1: Watcher >=====> backend
#
# pipe2: Watcher <=====< backend
#
# The watcher will be listening to pipe2 to see if the 
# backend is live and update a flag accordingly
#
# if the backend is not live for more than a certain
# ammount of time, X attempts to start the service will be done
# if after that still dead. The following will happen:
# 
# 1. a minor version of backend that will only be able
#    to communicate with the frontend will "spawn" and
#    send a message to reboot the machine and if the 
#    problem persist to update the software
# 
# To update the firmware:
#
# 1. It will receive and validate the data sent by the 
#    dashboard.
# 2. After data validation ~/update directory is created
#    to store the file
# 3. The file is decompressed and extracted
# 4. It will make use of the pipe1 to ask the backend to 
#    release GPIO pins and cut comms with arduino (or to
#    just kill itself after freeing its resoources). Will
#    ask for a response after freeing the resources in order
#    to continue (for the case the backend kills itself,
#    a flag will be set to override the "failure state" of
#    the backend)
# 5. After confirmation of resources freed, it will call the
#    update_protocol script to update the firmware
# 6. a minor version of backend that will only be able
#    to communicate with the frontend will "spawn" and
#    send a message to reboot the machine when the update is done


from tornado.options import define, options, parse_command_line
import socketio
import tornado.web
import tornado.ioloop
import hashlib
import base64
import os
import subprocess
import time
import threading
import traceback

# CLASS DEFINITION
class UploadHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "http://192.168.50.10:3000")     #allows requests from the dashboard
        self.set_header("Access-Control-Allow-Headers", "x-requested-with, Content-MD5, Content-Length")
        self.set_header('Access-Control-Allow-Methods', 'OPTIONS, PUT')

    def put(self):
        global updating
        received_file = self.request.body
        received_sha = self.request.headers.get('Content-MD5')

        computed_sha = hashlib.sha256(received_file).hexdigest()

        if computed_sha == received_sha:
            self.set_status(200)
            self.write("File received and verified successfully!")
            add_to_buffer("Update File received")
            with open(os.path.expanduser("~/update/updtPckg.tar.gz"), 'wb') as file:
                file.write(received_file)
            add_to_buffer("File saved, starting update process")
            updating = True
            tr = threading.Thread(target=startUpdate)
            tr.start()
        else:
            self.set_status(400)
            self.write("sha checksum mismatch!")
            add_to_buffer("File received erroneusly")
        
    def options(self):
        global stopESPcomm
        # no body
        createUpdateDir()
        self.set_status(204)
        self.finish()
        stopESPcomm = True

# GLOBAL VARIABLES
autoupdate_path = "./meticulous-raspberry-setup/meticulous-autoupdate"
user_path=os.path.expanduser("~/")

updating = False

pipe1 = None
pipe2 = None

pipe1_path = f''
pipe2_path = f''

message = bytes()
backAlive = False
continueUpdate = False

backend_time_off = 5   #seconds the backend is allowed to be offline in one occurrence


# THREAD VARIABLES
read_back_thread = None

# FUNCTION DEFINITIONS


def createUpdateDir():
    # Specify the directory path you want to create
    directory_path = os.path.expanduser("~/update")

    # Check if the directory already exists
    if not os.path.exists(directory_path):
    # Create the directory if it does not exist
        os.makedirs(directory_path)
        #print(f"Directory '{directory_path}' created successfully.")

#this function opens a pipe to allow communication between backend and the watcher
def openPipes():
    try:
        pipe2 = os.open(pipe2_path, os.O_RDONLY | os.O_NONBLOCK)
        pipe1 = os.open(pipe1_path, os.O_WRONLY)
    except OSError as e:
        print(f'an error occurred oppening pipes: {e}')

# This function kepps track that the backend is still alive or not and
# updates the flag accordignly. It contains no logic to handle any case
def readBackend():
    global message
    global backAlive
    global continueUpdate
    global first_backend_fail_time

    while True:
        if pipe1 != None:
            message = os.read(pipe1, 1024)
            if message:
                backAlive = True
                if len(message) > 3:
                    continueUpdate = message.decode() == "released"
            else:
                backAlive = False


#This functions checks if the backend has been offline more than it should
def backendFail():
    global updating
    if backAlive:
        return False
    else:
        time.sleep(backend_time_off)
        return (not backAlive) and (not updating)

# This function checks if the failure was not isolated
def backendDead():
    failure_count = 0
    while True:
        if backendFail():
            failure_count = failure_count + 1
        else:
            failure_count = 0

        if failure_count > 4:
            #Se make sure the backend procecss is dead
            subprocess.run("systemctl stop back.service ",shell=True,capture_output=True,text=True,cwd=user_path)
            #Notify the user to restart the Meticulous or upload software
            #START LIL BACKEND
            #NOTIFY USER TO RESTART OR UPLOAD

def startUpdate():

    global stopESPcomm
    global reboot_flag
    global continueUpdate
    global updating

    stopESPcomm = True

    path = "./update/updtPckg.tar.gz"

    #extract the directory of the update 
    command = f'sudo tar -xzf {path} -C ./update'
    subprocess.run(command, shell=True,cwd=user_path)

    #delete the compressed file
    command = f'sudo rm {path}'
    subprocess.run(command, shell=True,cwd=user_path)

    # ASK BACKEND TO FREE RESOURCES

    # WAIT FOR THE BACKEND CONFIRMATION THAT RESOURCES ARE FREED (and its dead)

    #call the update script (will use the script as a module)
    command = f'python {autoupdate_path}/update_protocol.py'
    update_success = subprocess.run(command, shell=True, capture_output=True, text=True,cwd=user_path).stdout

    print(update_success)

    reboot_flag = True
    time.sleep(2)
    PID = subprocess.run("systemctl status back.service | grep -oP 'Main PID: \K\d+'",shell=True,capture_output=True,text=True,cwd=user_path).stdout

    #y lo matamos alv _(~o _ o~)_/\_(0 _ 0)_

    subprocess.run(f'sudo kill -9 {PID}',shell=True,cwd=user_path)
    updating = False

def main():
    global read_back_thread

    parse_command_line()

    #opens the pipes to chat with backend
    openPipes()

    #starts the thread that reads the pipe2
    read_back_thread = threading.Thread(target=readBackend)
    read_back_thread.start()


    app = tornado.web.Application(
        [
            (r"/update", UploadHandler),
        ],
    )

    app.listen(options.port)
    tornado.ioloop.IOLoop.current().start()


#execution phase
define("port", default=8081, help="run on the given port", type=int)

if __name__ == "__main__":
    try:
        main()
    except:
        traceback.print_exc()
