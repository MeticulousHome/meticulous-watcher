#meticulous watcher script for the service

from tornado.options import define, options, parse_command_line
import socketio
import tornado.web
import tornado.ioloop
import hashlib
import base64