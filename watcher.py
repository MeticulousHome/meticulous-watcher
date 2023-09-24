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
import tornado.web
import tornado.ioloop
import hashlib
import os
import subprocess
import time
import threading
import traceback
import stat
import socketio
import asyncio

user_path=os.path.expanduser("~/")

##################################################################################################################
##################################################################################################################
##################################################################################################################
##################################################################################################################
##################################################################################################################
# HTTP SERVER HANDLING
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
            with open(os.path.expanduser("~/update/updtPckg.tar.gz"), 'wb') as file:
                file.write(received_file)
            updating = True
            tr = threading.Thread(target=startUpdate)
            tr.start()
        else:
            self.set_status(400)
            self.write("sha checksum mismatch!")
        
    def options(self):
        global stopESPcomm
        # no body
        createUpdateDir()
        self.set_status(204)
        self.finish()
        stopESPcomm = True



##################################################################################################################
# IPC HANDLERS
IPC_path = f'{user_path}/ipc'                              # directory for the InterProcess Communication pipes
pipe1 = None
pipe2 = None
pipe2_path = f'{IPC_path}/pipe2'
pipe1_path = f'{IPC_path}/pipe1'
backAlive = False
IPC_message = bytes()
backend_time_off = 5   #seconds the backend is allowed to be offline in one occurrence
read_back_thread = None

#this function creates the pipes to allow communication between backend and the watcher
def checkPipes():
    #validates if the pipes directory exists
    if os.path.exists(IPC_path):

        #validates the file repersenting pipe1 exists
        if os.path.exists(pipe1_path):

            #checks if it is indeed a pipe
            pipe1_stat = os.stat(pipe1_path)

            #if its not a pipe, it deletes the file and creates a pipe
            if not stat.S_ISFIFO(pipe1_stat.st_mode):
                os.remove(pipe1_path)
                os.mkfifo(pipe1_path)
        #if the file representing a pipe does not exist
        else:
            #it's created
            os.mkfifo()

        #repeats with pipe 2
        if os.path.exists(pipe2_path):

            pipe2_stat = os.stat(pipe2_path)
            
            if not stat.S_ISFIFO(pipe2_stat.st_mode):
                os.remove(pipe2_path)
                os.mkfifo(pipe2_path)
        else:
            os.mkfifo()

    #if not even the directory exists
    else:
        #create the base directory
        os.mkdir(IPC_path)
        #create the pipes
        os.mkfifo(pipe1_path)
        os.mkfifo(pipe2_path)


##### BACKEND CHECKER

# This function kepps track that the backend is still alive or not and
# updates the flag accordignly. It contains no logic to handle any case
def readBackend():
    global IPC_message
    global backAlive
    global continueUpdate
    global pipe2

    try:
        pipe2 = os.open(pipe2_path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as e:
        print(f'an error occurred oppening pipe2: {e}')
        pipe2 = None

    while True:
        if pipe2 != None:
            IPC_message = os.read(pipe2, 1024)
            if IPC_message:
                backAlive = True
                if len(IPC_message) > 3:
                    if IPC_message.decode() == "released":
                        continueUpdate = True

#This functions checks if the backend has been offline more than it should
def backendFail():
    global updating
    global backAlive

    _backAlive = backAlive
    backAlive = False
    return (not _backAlive) and (not updating)

# This function checks if the failure was not isolated (this must be a task in a thread)
def backendDead():
    failure_count = 0
    restarts_done = 0

    while True:
        time.sleep(backend_time_off)
        if backendFail():
            failure_count = failure_count + 1
        else:
            failure_count = 0

        if failure_count > 4:
            failure_count = 0
            # We restart the backend up to 3 times
            if restarts_done < 3:
                restarts_done = restarts_done + 1
                subprocess.run("systemctl stop back",shell=True,capture_output=True,text=True,cwd=user_path)
                time.sleep(1)
                subprocess.run("systemctl start back",shell=True,capture_output=True,text=True,cwd=user_path)
            #if we have tried to restart the back 3 times and still dead
            else:

                #stop the back as we will need the por 8080 to be free, just making sure of it
                subprocess.run("systemctl stop back",shell=True,capture_output=True,text=True,cwd=user_path)

                #launch the little back to notify the need to restart / upgrade the machine
                lil_back("fail")

            #Notify the user to restart the Meticulous or upload software
            #START LIL BACKEND
            #NOTIFY USER TO RESTART OR UPLOAD

# This function boots the lil back to advise the user a problem has occured and ask for a restart or update
def lil_back(notify:str):
    # create a new HTTP aplication in the server the back should have been
    lil_sio = socketio.AsyncServer(cors_allowed_origins='*', async_mode='tornado')

    #serves the app for socket communication 
    lil_app = tornado.web.Application(
        [
            (r"/socket.io/", socketio.get_tornado_handler(lil_sio)),
        ],
    )

    time.sleep(2)
    
    #on the port that the backend was using
    lil_app.listen(8080)

    #notifies the frontend that the backend failed
    if notify == "fail":
        asyncio.run(lil_sio.emit("BACKEND_FAIL"))
    if notify == "update":
        asyncio.run(lil_sio.emit("MANUAL-REBOOT"))

##################################################################################################################
# UPDATE HANDLERS
autoupdate_path = "./update/Script" 
updating = False
continueUpdate = False

#This function creates the update directory where the file will be stored and extracted
def createUpdateDir():
    # Specify the directory path you want to create
    directory_path = os.path.expanduser("~/update")

    # Check if the directory already exists
    if not os.path.exists(directory_path):
    # Create the directory if it does not exist
        os.makedirs(directory_path)
        #print(f"Directory '{directory_path}' created successfully.")

#This function starts the update process
def startUpdate():

    global reboot_flag
    global continueUpdate
    global updating

    updating = True  # affects: 

    path = "./update/updtPckg.tar.gz"

    #extract the directory of the update 
    command = f'sudo tar -xzf {path} -C ./update'
    subprocess.run(command, shell=True,cwd=user_path)

    #delete the compressed file
    command = f'sudo rm {path}'
    subprocess.run(command, shell=True,cwd=user_path)

    # ASK BACKEND TO FREE RESOURCES
    with open(pipe1_path, 'w') as pipe:
        pipe.write("FREE")
    # WAIT FOR THE BACKEND CONFIRMATION THAT RESOURCES ARE FREED (and its dead)
    while not continueUpdate:
        pass

    #call the update script that will be provided in the update pckg
    command = f'python3 {autoupdate_path}/update_protocol.py'
    update_success = subprocess.run(command, shell=True, capture_output=True, text=True,cwd=user_path).stdout

    print(update_success)

    #We restart the backend
    subprocess.run("systemctl stop back",shell=True,capture_output=True,text=True,cwd=user_path)
    time.sleep(1)
    subprocess.run("systemctl start back",shell=True,capture_output=True,text=True,cwd=user_path)

    updating = False
    continueUpdate = False


def main():
    global read_back_thread

    parse_command_line()

    #opens the pipes to chat with backend
    checkPipes()

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
