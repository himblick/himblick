from __future__ import annotations
from typing import List
from .cmdline import Command
from .settings import Settings
import re
import subprocess
import mimetypes
import os
import shutil
import shlex
import tempfile
import time
import logging

log = logging.getLogger(__name__)


def run(cmd: List[str], check: bool = True, **kw) -> subprocess.CompletedProcess:
    """
    Logging wrapper to subprocess.run.

    Also, default check to True.
    """
    log.info("Run %s", " ".join(shlex.quote(x) for x in cmd))
    return subprocess.run(cmd, check=check, **kw)


class Presentation:
    """
    Base class for all presentation types
    """

    def run_player(self, cmd, **kw):
        """
        Run a media player command line, performing other common actions if
        needed
        """
        # Run things under caffeinate
        #
        # If it is not sufficient, others do:
        #   disable screensavers
        #   xset s noblank
        #   xset s off
        #   xset -dpms
        #
        # See also: https://stackoverflow.com/questions/10885337/inhibit-screensaver-with-python
        cmd = ["caffeinate", "--"] + cmd
        run(cmd, **kw)


class SingleFileMixin:
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.fname = None
        self.mtime = 0

    def __bool__(self):
        return self.fname is not None

    def add(self, fname):
        mtime = os.path.getmtime(fname)
        if self.fname is None or mtime > self.mtime:
            self.fname = fname
            self.mtime = mtime


class FileGroupMixin:
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.files = []
        self.mtime = 0

    def __bool__(self):
        return bool(self.files)

    def add(self, fname):
        mtime = os.path.getmtime(fname)
        self.files.append(fname)
        if self.mtime is None or mtime > self.mtime:
            self.mtime = mtime


class EmptyPresentation(Presentation):
    """
    Presentation doing nothing forever
    """
    def run(self):
        log.warn("Nothing to do: sleeping forever")
        while True:
            time.sleep(3600)


class PDFPresentation(SingleFileMixin, Presentation):
    def run(self):
        log.info("%s: PDF presentation", self.fname)

        confdir = os.path.expanduser("~/.config")
        os.makedirs(confdir, exist_ok=True)

        # TODO: configure slide advance time

        # Configure okular
        with open(os.path.expanduser(os.path.join(confdir, "okularpartrc")), "wt") as fd:
            print("[Core Presentation]", file=fd)
            print("SlidesAdvance=true", file=fd)
            print("SlidesAdvanceTime=2", file=fd)
            print("SlidesLoop=true", file=fd)
            print("[Dlg Presentation]", file=fd)
            print("SlidesShowProgress=false", file=fd)
            # print("SlidesTransition=GlitterRight", file=fd)

        # Silence a too-helpful first-time-run informational message
        with open(os.path.expanduser(os.path.join(confdir, "okular.kmessagebox")), "wt") as fd:
            print("[General]", file=fd)
            print("presentationInfo=4", file=fd)

        # Remove state of previous okular runs, so presentations begin at the
        # beginning
        docdata = os.path.expanduser("~/.local/share/okular/docdata/")
        if os.path.isdir(docdata):
            shutil.rmtree(docdata)

        self.run_player(["okular", "--presentation", "--", self.fname])


class VideoPresentation(FileGroupMixin, Presentation):
    def run(self):
        self.files.sort()
        log.info("Video presentation of %d videos", len(self.files))
        with tempfile.NamedTemporaryFile("wt", suffix=".vlc") as tf:
            for fname in self.files:
                print(fname, file=tf)
            tf.flush()

            self.run_player(
                    ["cvlc", "--no-audio", "--loop", "--fullscreen",
                        "--video-on-top", "--no-video-title-show", tf.name])


class ImagePresentation(FileGroupMixin, Presentation):
    def run(self):
        self.files.sort()
        log.info("Image presentation of %d images", len(self.files))
        with tempfile.NamedTemporaryFile("wt") as tf:
            for fname in self.files:
                print(fname, file=tf)
            tf.flush()

            # TODO: adjust slide advance time
            self.run_player(["feh", "-f", tf.name, "-F", "-Y", "-D", "1.5"])


class ODPPresentation(SingleFileMixin, Presentation):
    def run(self):
        log.info("%s: ODP presentation", self.fname)
        self.run_player(["loimpress", "--nodefault", "--norestore", "--nologo", "--nolockcheck", "--show", self.fname])


class LogoPresentation(Presentation):
    """
    Presentation shown when there are no media to show
    """
    def __init__(self, fname):
        self.fname = fname

    def run(self):
        self.run_player(["feh", "-F", "-Y", self.fname])


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

    def find_presentation(self, path):
        """
        Find the presentation to play from a given media directory
        """
        if not os.path.isdir(path):
            return None
        pdf = PDFPresentation()
        videos = VideoPresentation()
        images = ImagePresentation()
        odp = ODPPresentation()
        all_players = [pdf, videos, images, odp]

        for fn in os.listdir(path):
            abspath = os.path.abspath(os.path.join(path, fn))
            base, ext = os.path.splitext(fn)
            mimetype = mimetypes.types_map.get(ext)
            if mimetype is None:
                log.info("%s: mime type unknown", fn)
                continue
            else:
                log.info("%s: mime type %s", fn, mimetype)
            if mimetype == "application/pdf":
                pdf.add(abspath)
            elif mimetype.startswith("image/"):
                images.add(abspath)
            elif mimetype.startswith("video/"):
                videos.add(abspath)
            elif mimetype == "application/vnd.oasis.opendocument.presentation":
                odp.add(abspath)

        player = max(all_players, key=lambda x: x.mtime)
        if not player:
            return None
        return player

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

        # TODO: monitor media directory for changes

        # current_dir = os.path.join(self.args.media, "current")
        current_dir = self.args.media
        logo_dir = os.path.join(self.args.media, "logo")

        # First look into the 'current' directory
        pres = self.find_presentation(current_dir)
        if pres is None:
            # If there is no media to play there, look into the 'logo' directory
            log.warn("%s: no media found, trying logo", current_dir)
            pres = self.find_presentation(logo_dir)
        if pres is None:
            # Else, do nothing
            log.warn("%s: no media found, doing nothing", logo_dir)
            pres = EmptyPresentation()

        pres.run()

#        - or configure lightdm to start X with nocursor option
