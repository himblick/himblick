from __future__ import annotations
from typing import TYPE_CHECKING, Set
import logging
import json
import os
import time
import datetime
import subprocess
from collections import deque
import tornado.web
from tornado.web import url
import tornado.httpserver
import tornado.netutil
import tornado.websocket
import tornado.ioloop
from tornado.escape import xhtml_escape
from .static import StaticFileHandler


if TYPE_CHECKING:
    from . import Player


log = logging.getLogger("serve")


class Socket(tornado.websocket.WebSocketHandler):
    def prepare(self):
        self.is_admin = self.get_secure_cookie("admin") == b"y"

    def open(self):
        log.debug("WebSocket connection opened")
        self.application.add_socket(self)

    def on_message(self, message):
        try:
            data = json.loads(message)
        except Exception as e:
            log.warn("Cannot decode incoming ws message %r: %s", message, e)
            return

        command = data.get("command")
        if command is None:
            return

        if self.is_admin:
            if command == "reload_media":
                self.application.player.command_queue.put_nowait("rescan")

    def on_close(self):
        log.debug("WebSocket connection closed")
        self.application.remove_socket(self)


def format_timestamp(ts):
    dt = datetime.datetime.fromtimestamp(ts)
    text = xhtml_escape(dt.strftime("%Y-%m-%d %H:%M:%S"))
    return f"<span data-timestamp='{ts}'>{text}</span>"


def runcmd(*cmd):
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.stdout


class BaseHandler(tornado.web.RequestHandler):
    def prepare(self):
        self.is_admin = self.get_secure_cookie("admin") == b"y"

    def get_template_namespace(self):
        res = super().get_template_namespace()

        if self.request.protocol == "http":
            res["ws_url"] = "ws://" + self.request.host + \
                          self.application.reverse_url("socket")
        else:
            res["ws_url"] = "wss://" + self.request.host + \
                          self.application.reverse_url("socket")

        res["now"] = time.time()
        res["presentation"] = self.application.player.current_presentation
        res["format_timestamp"] = format_timestamp
        res["is_admin"] = self.is_admin

        return res


class MainPage(BaseHandler):
    def get(self):
        _ = self.locale.translate

        uploaded_media = []
        media_dir = self.application.player.media_dir.path
        for de in os.scandir(media_dir):
            if de.is_dir():
                continue
            if de.name in ("current", "previus", "logo", "himblick.conf", "remove-when-done"):
                continue
            if de.name.startswith("."):
                continue
            uploaded_media.append(de.name)
        uploaded_media.sort()

        self.render("main.html",
                    title=_("Himblick"),
                    uploaded_media=uploaded_media)

    def post(self):
        password = self.get_body_argument("password", "")
        if password and password == self.application.player.settings.general("admin password"):
            log.info("Auth succeeded")
            self.set_secure_cookie("admin", "y")
        else:
            log.info("Auth failed/logout")
            self.clear_cookie("admin")
        self.redirect("/")


class StatusPage(BaseHandler):
    def get(self):
        _ = self.locale.translate

        self.render("status.html",
                    title=_("Himblick status"),
                    uptime=runcmd("uptime"),
                    free=runcmd("free", "-h"),
                    systemctl_status=runcmd("systemctl", "--user", "status", "himblick-player.slice"))


class TopCpuPage(BaseHandler):
    def get(self):
        _ = self.locale.translate

        self.render("top.html",
                    title=_("Himblick status - CPU top"),
                    top=runcmd("top", "-b", "-n", "1", "-o", "%CPU", "-w", "512"))


class TopMemPage(BaseHandler):
    def get(self):
        _ = self.locale.translate

        self.render("top.html",
                    title=_("Himblick status - MEM top"),
                    top=runcmd("top", "-b", "-n", "1", "-o", "%MEM", "-w", "512"))


class MediaUpload(BaseHandler):
    def post(self):
        if not self.is_admin:
            self.send_error(403)
            return
        # _ = self.locale.translate
        media_dir = self.application.player.media_dir.path
        for name, files in self.request.files.items():
            for f in files:
                name = os.path.basename(f["filename"])
                with open(os.path.join(media_dir, name), "wb") as fd:
                    fd.write(f.body)
        self.finish("OK")


class MediaActivate(BaseHandler):
    def post(self):
        if not self.is_admin:
            self.send_error(403)
            return
        self.application.player.command_queue.put_nowait("rescan")
        self.redirect("/")


class WebLoggingHandler(logging.Handler):
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.queue = deque(maxlen=100)
        # TODO: add a formatter and a filter

    def emit(self, record):
        formatted = self.format(record)
        self.queue.append(formatted)
        # TODO: notify a log update via websocket


class WebUI(tornado.web.Application):
    def __init__(self, player: Player):
        urls = [
            url(r"/_server/websocket", Socket, name="socket"),
            url(r"^/$", MainPage, name="main"),
            url(r"^/status$", StatusPage, name="status"),
            url(r"^/status/top-cpu$", TopCpuPage, name="status_top_cpu"),
            url(r"^/status/top-mem$", TopMemPage, name="status_top_mem"),
            url(r"^/media/upload$", MediaUpload, name="media_upload"),
            url(r"^/media/activate$", MediaActivate, name="media_activate"),
        ]

        cookie_secret = player.settings.general("cookie secret")
        if not cookie_secret:
            import secrets
            cookie_secret = secrets.token_hex(64)

        settings = {
            "template_path": os.path.join(os.path.dirname(__file__), "templates"),
            "static_handler_class": StaticFileHandler,
            "static_path": os.path.join(os.path.dirname(__file__), "static"),
            "xsrf_cookies": True,
            "cookie_secret": cookie_secret,
        }

        super().__init__(
            urls,
            **settings,
        )

        self.player: Player = player
        self.sockets: Set[Socket] = set()

        self.logbuffer = WebLoggingHandler(level=logging.INFO)
        logging.getLogger().addHandler(self.logbuffer)

    def add_socket(self, handler):
        self.sockets.add(handler)

    def remove_socket(self, handler):
        self.sockets.discard(handler)

    def trigger_reload(self):
        log.info("Content change detected: reloading site")
        payload = json.dumps({"event": "reload"})
        for handler in self.sockets:
            handler.write_message(payload)

    def start_server(self, host="0.0.0.0", port=8018):
        sockets = tornado.netutil.bind_sockets(port, host)
        pairs = []
        for s in sockets:
            pairs.append(s.getsockname()[:2])
        pairs.sort()
        host, port = pairs[0]

        if ":" in host:
            host = f"[{host}]"
        server_url = f"http://{host}:{port}"
        log.info("Serving on %s", server_url)

        server = tornado.httpserver.HTTPServer(self)
        server.add_sockets(sockets)
