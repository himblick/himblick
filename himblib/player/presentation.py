from __future__ import annotations
from typing import TYPE_CHECKING
import asyncio
import shlex
import os
import shutil
import tempfile
import logging

if TYPE_CHECKING:
    from ..settings import PlayerSettings

log = logging.getLogger(__name__)


class Presentation:
    """
    Base class for all presentation types
    """
    def __init__(self, settings: PlayerSettings):
        self.settings = settings
        self.loop = asyncio.get_event_loop()
        # Subprocess used to track the player
        self.proc = None
        # If not None, it's a future set when the player has quit
        self.quit = None

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
        cmd = ["systemd-run", "--scope", "--slice=himblick-player", "--user", "caffeinate", "--"] + cmd
        log.info("Run %s", " ".join(shlex.quote(x) for x in cmd))
        self.proc = await asyncio.create_subprocess_exec(*cmd)
        log.info("player %d started", self.proc.pid)
        returncode = await self.proc.wait()
        log.info("player %d exited with return code %d", self.proc.pid, returncode)
        self.proc = None

    async def run(self, queue):
        await self._run()
        if self.quit:
            self.quit.set_result(True)
        else:
            queue.put_nowait("player_exited")

    async def stop(self):
        log.info("Stopping player %s", self.proc.pid if self.proc is not None else None)
        self.quit = self.loop.create_future()
        quit_proc = await asyncio.create_subprocess_exec("systemctl", "--user", "stop", "himblick-player.slice")
        returncode = await quit_proc.wait()
        if returncode != 0:
            log.info("Stop process return code %d", returncode)
        else:
            log.info("Player stop requested")
        await self.quit
        log.info("Player stopped")
        self.quit = None


class EmptyPresentation(Presentation):
    """
    Presentation doing nothing forever
    """
    async def _run(self):
        log.info("Starting the empty presentation, doing nothing")
        self.proc = self.loop.create_future()
        await self.proc
        log.info("Empty presentation stopped")

    async def stop(self):
        log.info("Stopping the empty presentation")
        self.proc.set_result(True)
        self.proc = None
        log.info("Stopped the empty presentation")


class FilePresentation(Presentation):
    """
    Base class for presentations that work on media files
    """
    def __init__(self, *args, root: str, **kw):
        super().__init__(*args, **kw)
        # Directory where the media files are found
        self.root = root
        self.fnames = []
        self.most_recent_fname = None
        self.mtime = 0

    def __bool__(self):
        return bool(self.fnames)

    @property
    def pathnames(self):
        for fn in self.fnames:
            yield os.path.join(self.root, fn)

    @property
    def most_recent_pathname(self):
        return os.path.join(self.root, self.most_recent_fname)

    def add(self, fname):
        mtime = os.path.getmtime(os.path.join(self.root, fname))
        self.fnames.append(fname)
        if self.mtime is None or mtime > self.mtime:
            self.mtime = mtime
            self.most_recent_fname = fname

    def move_assets_to(self, new_root):
        for fname in self.fnames:
            os.rename(
                    os.path.join(self.root, fname),
                    os.path.join(new_root, fname))
        self.root = new_root


class PDFPresentation(FilePresentation):
    async def _run(self):
        pathname = self.most_recent_pathname
        log.info("%s: PDF presentation", pathname)

        confdir = os.path.expanduser("~/.config")
        os.makedirs(confdir, exist_ok=True)

        # Configure okular
        with open(os.path.expanduser(os.path.join(confdir, "okularpartrc")), "wt") as fd:
            print("[Core Presentation]", file=fd)
            print("SlidesAdvance=true", file=fd)
            print(f"SlidesAdvanceTime={self.settings.pdf_transition_time}", file=fd)
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

        await self.run_player(["okular", "--presentation", "--", self.most_recent_pathname])


class VideoPresentation(FilePresentation):
    async def _run(self):
        self.fnames.sort()
        log.info("Video presentation of %d videos", len(self.fnames))
        with tempfile.NamedTemporaryFile("wt", suffix=".vlc") as tf:
            for pathname in self.pathnames:
                print(pathname, file=tf)
            tf.flush()

            await self.run_player(
                    ["cvlc", "--no-audio", "--loop", "--fullscreen",
                        "--video-on-top", "--no-video-title-show", tf.name])


class ImagePresentation(FilePresentation):
    async def _run(self):
        self.fnames.sort()
        log.info("Image presentation of %d images", len(self.fnames))
        with tempfile.NamedTemporaryFile("wt") as tf:
            for pathname in self.pathnames:
                print(pathname, file=tf)
            tf.flush()

            await self.run_player(["feh", "-f", tf.name, "-F", "-Y", "-D", str(self.settings.photo_transition_time)])


class ODPPresentation(FilePresentation):
    async def _run(self):
        pathname = self.most_recent_pathname
        log.info("%s: ODP presentation", pathname)
        await self.run_player(
                ["loimpress", "--nodefault", "--norestore", "--nologo", "--nolockcheck", "--show",
                 os.path.join(self.root, pathname)])
