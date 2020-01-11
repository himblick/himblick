from __future__ import annotations
from typing import TYPE_CHECKING, Optional
import os
import mimetypes
import shutil
import logging
from . import presentation

if TYPE_CHECKING:
    from ..settings import PlayerSettings

log = logging.getLogger(__name__)


class MediaDir:
    def __init__(self, settings: PlayerSettings, path, backup_to: Optional["MediaDir"] = None):
        self.settings = settings
        self.path = os.path.abspath(path)
        # MediaDir to which we backup files if requested
        self.backup_media_dir = backup_to
        self.pdf = None
        self.videos = None
        self.images = None
        self.odp = None
        self.all = []
        self.pres = None

    def __str__(self):
        return self.path

    def clear(self):
        self.pdf = presentation.PDFPresentation(self.settings, root=self.path)
        self.videos = presentation.VideoPresentation(self.settings, root=self.path)
        self.images = presentation.ImagePresentation(self.settings, root=self.path)
        self.odp = presentation.ODPPresentation(self.settings, root=self.path)
        self.all = [self.pdf, self.videos, self.images, self.odp]
        self.pres = None

    def scan(self):
        self.clear()

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
            log.info("%s: %s: mime type unknown", self, fn)
            return False
        log.info("%s: %s: mime type %s", self, fn, mimetype)

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

    def move_assets_to(self, other: "MediaDir"):
        """
        If we contain assets, move them to other and empty this MediaDir
        """
        log.info("%s: moving assets to %s", self, other)
        # Let other backup its assets if configured
        other.backup_assets()

        # Clean the other's media directory
        if os.path.exists(other.path):
            shutil.rmtree(other.path)
        os.makedirs(other.path, exist_ok=True)

        # Move all our assets to other
        for p in self.all:
            p.move_assets_to(other.path)
        other.pdf = self.pdf
        other.videos = self.videos
        other.images = self.images
        other.odp = self.odp
        other.all = self.all
        other.pres = self.pres

        # Empty our record of media files
        self.clear()

    def backup_assets(self):
        """
        If we have a backup_media_dir configured and we contain assets, move
        them to the backup_media_dir
        """
        if self.backup_media_dir is None:
            return
        if not self.scan():
            return
        self.move_assets_to(self.backup_media_dir)
        self.clear()
