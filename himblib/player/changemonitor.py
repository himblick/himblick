from __future__ import annotations
import asyncio
import logging
import os
import pyinotify

log = logging.getLogger(__name__)


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
        self.queue.put_nowait("rescan")

        # Recreate the monitor file, to be ready for the next notification
        self.create_monitor_file()
