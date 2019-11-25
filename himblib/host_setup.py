from __future__ import annotations
from .cmdline import Command
import logging
import configparser
import io
import sys
import os
from .utils import atomic_writer

log = logging.getLogger(__name__)


class HostSetup(Command):
    """
    Configure host at boot
    """
    NAME = "host-setup"

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("--config", "-C", action="store", metavar="file.conf",
                            default="/boot/himblick.conf",
                            help="configuration file to read (default: /boot/wifi.ini)")
        parser.add_argument("--dry-run", "-n", action="store_true",
                            help="print configuration changes to standard output, but do not perform them")
        return parser

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.config = configparser.ConfigParser()
        self.config.read(self.args.config)

    def configure_wpasupplicant(self):
        def print_section(essid, psk, file=None):
            print("network={", file=file)
            print(f'    ssid="{essid}"', file=file)
            print(f"    psk={psk}", file=file)
            print("}", file=file)

        with io.StringIO() as fd:
            wifi_country = self.config["general"]["wifi country"]

            # print("ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev", file=fd)
            # print("update_config=1", file=fd)
            print(f"country={wifi_country}", file=fd)

            for section in self.config.sections():
                if not section.startswith("wifi "):
                    continue
                values = self.config[section]
                essid = section[5:].strip()
                if 'hash' in values:
                    print_section(essid, values["hash"], file=fd)
                elif 'password' in values:
                    print_section(essid, '"' + values["password"] + '"', file=fd)

            wpa_config = fd.getvalue()

        dest = "/etc/wpa_supplicant/wpa_supplicant-wlan0.conf"
        if self.args.dry_run:
            print(f"* {dest}")
            sys.stdout.write(wpa_config)
        else:
            with atomic_writer(dest, "wt", chmod=0o600) as fd:
                fd.write(wpa_config)

        os.sync()

    def run(self):
        self.configure_wpasupplicant()
