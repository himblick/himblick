from __future__ import annotations
from .cmdline import Command
from .settings import Settings
from .utils import run
import re
import mimetypes
import os
import shutil
import shlex
import tempfile
import asyncio
import pyinotify
import logging

log = logging.getLogger(__name__)


class Presentation:
    """
    Base class for all presentation types
    """
    def __init__(self, root):
        # Directory where the media files are found
        self.root = os.path.abspath(root)
        # Subprocess used to track the player
        self.proc = None

    def is_running(self):
        """
        Check if the presentation is still running
        """
        return self.proc is not None

    async def run_player(self, cmd, **kw):
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
        cmd = ["caffeinate", "--", "systemd-run", "--scope", "--slice=himblick-player", "--user"] + cmd
        log.info("Run %s", " ".join(shlex.quote(x) for x in cmd))
        self.proc = await asyncio.create_subprocess_exec(*cmd)
        returncode = await self.proc.wait()
        self.proc = None
        log.info("player exited with return code %d", returncode)

    async def stop(self):
        log.info("Stopping player")
        run(["systemctl", "--user", "stop", "himblick-player.slice"])
        log.info("Player stopped")


class SingleFileMixin:
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.fname = None
        self.mtime = 0

    def __bool__(self):
        return self.fname is not None

    @property
    def pathname(self):
        return os.path.join(self.root, self.fname)

    def add(self, fname):
        mtime = os.path.getmtime(os.path.join(self.root, fname))
        if self.fname is None or mtime > self.mtime:
            self.fname = fname
            self.mtime = mtime


class FileGroupMixin:
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.fnames = []
        self.mtime = 0

    def __bool__(self):
        return bool(self.fnames)

    @property
    def pathnames(self):
        for fn in self.fnames:
            yield os.path.join(self.root, fn)

    def add(self, fname):
        mtime = os.path.getmtime(os.path.join(self.root, fname))
        self.fnames.append(fname)
        if self.mtime is None or mtime > self.mtime:
            self.mtime = mtime


class EmptyPresentation(Presentation):
    """
    Presentation doing nothing forever
    """
    async def run(self):
        log.info("Starting the empty presentation, doing nothing")
        self.proc = asyncio.get_current_loop().create_future()
        await self.proc
        log.info("Empty presentation stopped")

    async def stop(self):
        log.info("Stopping the empty presentation")
        self.proc.set_result(True)
        self.proc = None
        log.info("Stopped the empty presentation")


class PDFPresentation(SingleFileMixin, Presentation):
    async def run(self):
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

        await self.run_player(["okular", "--presentation", "--", self.pathname])


class VideoPresentation(FileGroupMixin, Presentation):
    async def run(self):
        self.fnames.sort()
        log.info("Video presentation of %d videos", len(self.fnames))
        with tempfile.NamedTemporaryFile("wt", suffix=".vlc") as tf:
            for pathname in self.pathnames:
                print(pathname, file=tf)
            tf.flush()

            await self.run_player(
                    ["cvlc", "--no-audio", "--loop", "--fullscreen",
                        "--video-on-top", "--no-video-title-show", tf.name])


class ImagePresentation(FileGroupMixin, Presentation):
    async def run(self):
        self.fnames.sort()
        log.info("Image presentation of %d images", len(self.fnames))
        with tempfile.NamedTemporaryFile("wt") as tf:
            for pathname in self.pathnames:
                print(pathname, file=tf)
            tf.flush()

            # TODO: adjust slide advance time
            await self.run_player(["feh", "-f", tf.name, "-F", "-Y", "-D", "1.5"])


class ODPPresentation(SingleFileMixin, Presentation):
    async def run(self):
        log.info("%s: ODP presentation", self.fname)
        await self.run_player(
                ["loimpress", "--nodefault", "--norestore", "--nologo", "--nolockcheck", "--show",
                 os.path.join(self.root, self.pathname)])


class LogoPresentation(Presentation):
    """
    Presentation shown when there are no media to show
    """
    def __init__(self, fname):
        self.fname = fname

    def run(self):
        self.run_player(["feh", "-F", "-Y", self.fname])


class MediaDir:
    def __init__(self, path):
        self.path = path
        self.relevant_files = []
        self.pdf = None
        self.videos = None
        self.images = None
        self.odp = None
        self.all = []
        self.pres = None

    def __str__(self):
        return self.path

    def scan(self):
        self.relevant_files = None
        self.pdf = PDFPresentation(self.path)
        self.videos = VideoPresentation(self.path)
        self.images = ImagePresentation(self.path)
        self.odp = ODPPresentation(self.path)
        self.all = [self.pdf, self.videos, self.images, self.odp]
        self.pres = None

        if not os.path.isdir(self.path):
            return None

        for fn in os.listdir(self.path):
            self.add(fn)

        pres = max(self.all, key=lambda x: x.mtime)
        if not pres:
            return None
        self.pres = pres
        return self.pres

    def add(self, fn):
        base, ext = os.path.splitext(fn)
        mimetype = mimetypes.types_map.get(ext)
        if mimetype is None:
            log.info("%s: mime type unknown", fn)
            return False
        log.info("%s: mime type %s", fn, mimetype)

        if mimetype == "application/pdf":
            self.pdf.add(fn)
            return True
        elif mimetype.startswith("image/"):
            self.images.add(fn)
            return True
        elif mimetype.startswith("video/"):
            self.videos.add(fn)
            return True
        elif mimetype == "application/vnd.oasis.opendocument.presentation":
            self.odp.add(fn)
            return True
        else:
            return False


class ChangeMonitor:
    """
    Trigger an event when a file is removed, then recreate the file
    """
    def __init__(self, queue: asyncio.Queue, media_dir: str, monitor_file_name: str = "remove-when-done"):
        """
        :arg queue: queue where we send notifications
        :arg media_dir: directory where we manage the monitor file
        :arg monitor_file_name: name to use for the monitor file
        """
        self.queue = queue
        self.media_dir = os.path.abspath(media_dir)
        self.monitor_file_name = monitor_file_name
        self.monitor_file = os.path.join(self.media_dir, self.monitor_file_name)

        # Set up pyinotify.
        # See https://stackoverflow.com/questions/26414052/watch-for-a-file-with-asyncio
        self.watch_manager = pyinotify.WatchManager()
        self.watch = self.watch_manager.add_watch(self.media_dir, pyinotify.IN_DELETE)
        self.notifier = pyinotify.AsyncioNotifier(
                self.watch_manager, asyncio.get_event_loop(), default_proc_fun=self.on_event)

        # Create the monitor file if it does not exist
        if not os.path.exists(self.monitor_file):
            self.create_monitor_file()

    def create_monitor_file(self):
        """
        Create the file we use for monitoring
        """
        with open(self.monitor_file, "wt") as fd:
            print("Remove this file when you want the player to rescan the media directory", file=fd)

    def on_event(self, event):
        """
        Handle incoming asyncio events
        """
        # We can skip instantiating pyinotify.ProcessEvent, since we don't need
        # dispatching

        # Filter out spurious events
        if event.path != self.media_dir:
            log.warn("%s: event %r received for a directory we were not monitoring", event.path, event)
            return

        if event.name != self.monitor_file_name:
            return

        # We can shamelessly use put_nowait, since the queue has no size bound.
        # This is handy because pyinotify does not seem to support async
        # callbacks
        self.queue.put_nowait(None)

        # Recreate the monitor file, to be ready for the next notification
        self.create_monitor_file()


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
        self.media_dir = MediaDir(self.args.media)
        self.logo_dir = MediaDir(os.path.join(self.args.media, "logo"))

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

        asyncio.get_event_loop().run_until_complete(self.main_loop())

    async def make_player(self):
        # Look in the media directory
        if self.media_dir.scan():
            return self.media_dir.pres

        # If there is no media to play there, look into the 'logo' directory
        log.warn("%s: no media found, trying logo", self.media_dir)
        if self.logo_dir.scan():
            return self.logo_dir.pres

        # Else, do nothing
        log.warn("%s: no media found, doing nothing", self.logo_dir)
        return EmptyPresentation()

    async def main_loop(self):
        queue = asyncio.Queue()
        monitor = ChangeMonitor(queue, self.args.media)  # noqa
        current_dir = os.path.join(self.args.media, "current")
        previous_dir = os.path.join(self.args.media, "previous")

        while True:
            # Save the last playing directory as previous_dir
            if os.path.exists(current_dir):
                if os.path.exists(previous_dir):
                    shutil.rmtree(previous_dir)
                os.rename(current_dir, previous_dir)

            player = await self.make_player()
            await asyncio.wait((player.run(), queue.get()), return_when=asyncio.FIRST_COMPLETED)
            if player.is_running():
                await player.stop()
