from __future__ import annotations
from .cmdline import Command
import subprocess


class Player(Command):
    """
    Himblick media player
    """

    def run(self):
        while True:
            subprocess.run(["okular"])
