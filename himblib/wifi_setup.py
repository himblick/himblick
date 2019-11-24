from __future__ import annotations
from .cmdline import Command
import logging
import configparser
import io
import sys
from .utils import atomic_writer

log = logging.getLogger(__name__)


class WifiSetup(Command):
    """
    Configure Wifi at boot
    """
    NAME = "wifi-setup"

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("--config", "-C", action="store", metavar="file.ini",
                            default="/boot/wifi.ini",
                            help="configuration file to read (default: /boot/wifi.ini)")
        parser.add_argument("--output", "-o", action="store", metavar="wpa_supplicant.conf",
                            default="/etc/wpa_supplicant/wpa_supplicant.conf",
                            help="wpa_supplicant configuration file to write"
                                 " (default: /etc/wpa_supplicant/wpa_supplicant.conf)")
        parser.add_argument("--stdout", action="store_true",
                            help="print configuration to standard output")
        return parser

    def print_section(self, essid, psk, file=None):
        print("network={", file=file)
        print(f'    ssid="{essid}"', file=file)
        print(f"    psk={psk}", file=file)
        print("}", file=file)

    def run(self):
        config = configparser.ConfigParser()
        config.read(self.args.config)

        with io.StringIO() as fd:
            country = config["general"]["country"]

            print("ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev", file=fd)
            print("update_config=1", file=fd)
            print(f"country={country}", file=fd)

            for section in config.sections():
                values = config[section]
                if 'hash' in values:
                    self.print_section(section, values["hash"], file=fd)
                elif 'password' in values:
                    self.print_section(section, '"' + values["password"] + '"', file=fd)

            wpa_config = fd.getvalue()

        if self.args.stdout:
            sys.stdout.write(wpa_config)
        else:
            with atomic_writer(self.args.output, "wt", chmod=0o600) as fd:
                fd.write(wpa_config)
