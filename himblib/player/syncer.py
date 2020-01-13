from __future__ import annotations
from typing import TYPE_CHECKING, Optional
import os
import asyncio
import logging

if TYPE_CHECKING:
    from .mediadir import MediaDir

log = logging.getLogger(__name__)


class Syncer:
    def __init__(self, hostname: str, media_dir: MediaDir):
        self.hostname = hostname
        self.media_dir = media_dir
        self.media_key = os.path.expanduser("~/.ssh/id_media")
        self.sync_task: Optional[asyncio.Task] = None

    def rescan(self):
        log.info("syncer:%s: rescanning %s", self.hostname, self.media_dir.path)
        to_sync = []
        for fn in os.listdir(self.media_dir.path):
            if fn.endswith(".synced"):
                if fn == f"{self.hostname}.synced":
                    log.info("syncer:%s: already synced", self.hostname)
                    return
                else:
                    pass  # Ignore .synced files for other hosts
            else:
                log.info("syncer:%s: %s to be synced", self.hostname, fn)
                to_sync.append(fn)

        if to_sync:
            if self.sync_task is not None:
                self.sync_task.cancel()
                self.sync_task = None

            self.sync_task = asyncio.create_task(self.sync(to_sync))

    async def sync(self, fnames):
        print("SYNCING", fnames)
        # Create the .synced file
        with open(os.path.join(self.media_dir.path, f"{self.hostname}.synced"), "wt"):
            pass
        self.sync_task = None
