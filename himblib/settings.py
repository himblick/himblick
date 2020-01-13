from __future__ import annotations
from typing import Generator, Tuple, Dict
import os
import logging
import configparser

log = logging.getLogger()


class Settings:
    def __init__(self, pathname):
        self.cfg = configparser.ConfigParser()
        # Default settings
        self.cfg.read_dict({
            "general": {
                # Host name
                "name": "himblick",
            },
            "provision": {
                # Base raspbian image
                "base image": "images/raspbian-buster-lite.img",

                # Set this to a directory used to cache intermediate bits
                "cache dir": "",

                # Tarball with ssh host keys to reuse
                # If None, generate random ones
                "ssh host keys": "",

                # Public key to copy in the pi user's authorized_keys
                "ssh authorized key": "",

                # Himblick Debian package to install in the raspbian system
                "himblick package": "../himblick_1.0-1_all.deb",
            }
        })
        log.info("Reading configuration from %s", pathname)
        self.cfg.read([pathname])

        # Keep a copy of the config file without the [provision] section
        # Filter it manually because manipulating it with ConfigParser would
        # throw away comments
        non_provision_settings_lines = []
        skip_lines = False
        with open(pathname, "rt") as fd:
            for line in fd:
                if skip_lines:
                    if line.strip().startswith("["):
                        skip_lines = False
                elif line.strip().lower() == "[provision]":
                    skip_lines = True
                if not skip_lines:
                    non_provision_settings_lines.append(line)
        self.non_provision_settings = "".join(non_provision_settings_lines)

    def general(self, key: str) -> str:
        return self.cfg["general"].get(key, "")

    def provision(self, key: str) -> str:
        return self.cfg["provision"].get(key, "")

    def wifis(self) -> Generator[Tuple[str, Dict[str, str]]]:
        """
        Iterate essid, {key: val} for each
        """
        for section in self.cfg.sections():
            if not section.startswith("wifi "):
                continue
            essid = section[5:].strip()
            values = self.cfg[section]
            yield essid, values

    # Compatibility accessors

    @property
    def BASE_IMAGE(self):
        return self.provision("base image")

    @property
    def SSH_AUTHORIZED_KEY(self):
        return self.provision("ssh authorized key")

    @property
    def HIMBLICK_PACKAGE(self):
        return self.provision("himblick package")

    @property
    def CACHE_DIR(self):
        return self.provision("cache dir")


class PlayerSettings:
    def __init__(self, pathname):
        self.pathname = pathname

    def reload(self):
        self.cfg = configparser.ConfigParser()
        # Default settings
        self.cfg.read_dict({
            "player": {
                # Transition time for photo slideshows
                "photo transition time": "5",

                # Transition time for PDF presentations
                "pdf transition time": "5",
            }
        })
        log.info("Reading configuration from %s", self.pathname)
        self.cfg.read([self.pathname])

        # Create player config file if missing
        if not os.path.exists(self.pathname):
            with open(self.pathname, "wt") as out:
                self.cfg.write(out)

    @property
    def photo_transition_time(self):
        return int(self.cfg["player"].get("photo transition time", "5"))

    @property
    def pdf_transition_time(self):
        return int(self.cfg["player"].get("pdf transition time", "5"))
