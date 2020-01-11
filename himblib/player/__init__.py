from __future__ import annotations
from ..cmdline import Command
from ..settings import Settings, PlayerSettings
from ..utils import run
from . import presentation
from .changemonitor import ChangeMonitor
from .mediadir import MediaDir
from .server import WebUI
import re
import mimetypes
import os
import signal
import asyncio
import logging

log = logging.getLogger(__name__)


class Player(Command):
    """
    Himblick media player
    """
    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("--media", action="store", metavar="dir", default="/srv/media",
                            help="media directory")
        parser.add_argument("--config", "-C", action="store", metavar="file.conf",
                            default="/boot/himblick.conf",
                            help="configuration file to read (default: /boot/himblick.conf)")
        return parser

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.settings = Settings(self.args.config)
        self.player_settings = PlayerSettings(os.path.join(self.args.media, "himblick.conf"))
        self.media_dir = MediaDir(self.player_settings, self.args.media)
        self.previous_dir = MediaDir(self.player_settings, os.path.join(self.args.media, "previous"))
        self.current_dir = MediaDir(
                self.player_settings, os.path.join(self.args.media, "current"), backup_to=self.previous_dir)
        self.logo_dir = MediaDir(self.player_settings, os.path.join(self.args.media, "logo"))
        self.web_ui = WebUI(self)

    def configure_screen(self):
        """
        Configure the screen based on himblick.conf
        """
        # Set screen orientation
        orientation = self.settings.general("screen orientation")
        if orientation:
            run(["xrandr", "--orientation", orientation])

        mode = self.settings.general("screen mode")
        if mode:
            res = run(["xrandr", "--query"], capture_output=True, text=True)
            re_output = re.compile(r"^(\S+) connected ")
            for line in res.stdout.splitlines():
                mo = re_output.match(line)
                if mo:
                    output_name = mo.group(1)
                    break
            else:
                output_name = None
            run(["xrandr", "--output", output_name, "--mode", mode])

    def run(self):
        # Errors go to the logs, which go to stderr, which is saved in
        # ~/.xsession-errors
        mimetypes.init()

        self.configure_screen()

        self.web_ui.start_server()

        asyncio.get_event_loop().run_until_complete(self.main_loop())

    async def make_player(self):
        # Reload configuration
        self.player_settings.reload()

        # Look in the media directory
        if self.media_dir.scan():
            self.media_dir.move_assets_to(self.current_dir)
            return self.current_dir.pres

        log.warn("%s: no media found, trying an old current dir", self.media_dir)
        if self.current_dir.scan():
            return self.current_dir.pres

        # If there is no media to play there, look into the 'logo' directory
        log.warn("%s: no media found, trying logo", self.current_dir)
        if self.logo_dir.scan():
            return self.logo_dir.pres

        # Else, do nothing
        log.warn("%s: no media found, doing nothing", self.logo_dir)
        return presentation.EmptyPresentation()

    async def main_loop(self):
        loop = asyncio.get_event_loop()
        queue = asyncio.Queue()
        monitor = ChangeMonitor(queue, self.args.media)  # noqa

        def do_terminate():
            queue.put_nowait("quit")

        loop.add_signal_handler(signal.SIGINT, do_terminate)
        loop.add_signal_handler(signal.SIGTERM, do_terminate)

        while True:
            player = await self.make_player()
            asyncio.create_task(player.run(queue))
            self.web_ui.trigger_reload()
            cmd = await queue.get()
            log.info("Queue command: %s", cmd)
            if cmd == "rescan":
                if player.is_running():
                    await player.stop()
            elif cmd == "player_exited":
                pass
            elif cmd == "quit":
                if player.is_running():
                    await player.stop()
                break
