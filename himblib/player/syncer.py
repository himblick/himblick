from __future__ import annotations
from typing import TYPE_CHECKING
import os
import logging

if TYPE_CHECKING:
    from .mediadir import MediaDir

log = logging.getLogger(__name__)


class Syncer:
    def __init__(self, hostname: str, media_dir: MediaDir):
        self.hostname = hostname
        self.media_dir = media_dir
        self.media_key = os.path.expanduser("~/.ssh/id_media")

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
