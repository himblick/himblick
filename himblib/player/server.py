from __future__ import annotations
from typing import TYPE_CHECKING, Set
import logging
import json
import os
import time
import tornado.web
from tornado.web import url
import tornado.httpserver
import tornado.netutil
import tornado.websocket
import tornado.ioloop
from .static import StaticFileHandler


if TYPE_CHECKING:
    from . import Player


log = logging.getLogger("serve")


class Socket(tornado.websocket.WebSocketHandler):
    def open(self):
        log.debug("WebSocket connection opened")
        self.application.add_socket(self)

    def on_message(self, message):
        log.debug("WebSocket message received: %r", message)

    def on_close(self):
        log.debug("WebSocket connection closed")
        self.application.remove_socket(self)


class StatusPage(tornado.web.RequestHandler):
    def get(self):
        _ = self.locale.translate

        if self.request.protocol == "http":
            self.ws_url = "ws://" + self.request.host + \
                          self.application.reverse_url("socket")
        else:
            self.ws_url = "wss://" + self.request.host + \
                          self.application.reverse_url("socket")

        self.render("status.html", title=_("Himblick status"), now=time.time())


class WebUI(tornado.web.Application):
    def __init__(self, player: Player):
        urls = [
            url(r"/_server/websocket", Socket, name="socket"),
            url(r"^/$", StatusPage, name="status"),
        ]

        settings = {
            "template_path": os.path.join(os.path.dirname(__file__), "templates"),
            "static_handler_class": StaticFileHandler,
            "static_path": os.path.join(os.path.dirname(__file__), "static"),
            "xsrf_cookies": True,
            # "cookie_secret":
        }

        super().__init__(
            urls,
            **settings,
        )

        self.player: Player = player
        self.sockets: Set[Socket] = set()

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