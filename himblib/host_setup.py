from __future__ import annotations
from typing import List
from .cmdline import Command
import logging
import configparser
import subprocess
import shlex
import io
import os
import sys
import textwrap
from .utils import atomic_writer

log = logging.getLogger(__name__)


class HostSetup(Command):
    """
    Configure host at boot, with settings from /boot/himblick.conf
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

    def write_file(self, abspath: str, content: str, chmod=0o644):
        """
        Write the given content to a file
        """
        if self.args.dry_run:
            print(f"{abspath}:")
            sys.stdout.write(textwrap.indent(content, "  "))
        else:
            with atomic_writer(abspath, "wt", chmod=chmod) as fd:
                fd.write(content)

    def write_symlink(self, abspath: str, target: str):
        """
        Create the given symlink pointing to ``target``
        """
        if self.args.dry_run:
            print(f"{abspath} -> {target}")
        else:
            if os.path.lexists(abspath):
                os.unlink(abspath)
            os.symlink(target, abspath)

    def cmd(self, cmd: List[str], check: bool = True, **kw) -> subprocess.CompletedProcess:
        """
        Run a command
        """
        if self.args.dry_run:
            print("run: " + " ".join(shlex.quote(x) for x in cmd))
        else:
            return subprocess.run(cmd, check=check, **kw)

    def configure_wpasupplicant(self):
        def print_section(essid, psk, file=None):
            print("network={", file=file)
            print(f'    ssid="{essid}"', file=file)
            print(f"    psk={psk}", file=file)
            print("}", file=file)

        wifi_country = self.config["general"].get("wifi country")
        if wifi_country is None:
            return

        with io.StringIO() as fd:
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

        if self.args.dry_run:
            print(" * wifi")
        self.write_file("/etc/wpa_supplicant/wpa_supplicant-wlan0.conf", wpa_config, chmod=0o600)

    def configure_keyboard(self):
        layout = self.config["general"].get("keyboard layout")
        if layout is None:
            return

        conffile = "/etc/default/keyboard"

        try:
            lines = []
            replaced = False
            with open(conffile, "rt") as fd:
                for line in fd:
                    line = line.rstrip()
                    if line.startswith("XKBLAYOUT="):
                        new_line = "XKBLAYOUT=" + shlex.quote(layout)
                        if line.strip != new_line:
                            lines.append(new_line)
                            replaced = True
                        else:
                            lines.append(line)
                    else:
                        lines.append(line)
        except FileNotFoundError:
            return

        if replaced:
            if self.args.dry_run:
                print(" * keyboard")
            self.write_file(conffile, "\n".join(lines) + "\n")
            self.cmd(["/usr/sbin/dpkg-reconfigure", "-f", "noninteractive", "keyboard-configuration"])

    def configure_timezone(self):
        timezone = self.config["general"].get("timezone")
        if timezone is None:
            return

        if self.args.dry_run:
            print(" * timezone")

        self.write_symlink(
                "/etc/localtime",
                os.path.join("/usr/share/zoneinfo/", timezone))
        self.write_file(
                "/etc/timezone",
                timezone + "\n")
        self.cmd(["timedatectl", "set-timezone", timezone])

    def configure_hostname(self):
        hostname = self.config["general"].get("name")
        if hostname is None:
            return
        if self.args.dry_run:
            print(" * hostname")
        self.write_file("/etc/hostname", hostname + "\n")
        self.cmd(["hostname", hostname])
        self.cmd(["hostnamectl", "set-hostname", hostname])
        self.cmd(["hostnamectl", "set-chassis", "embedded"])

    def run(self):
        self.configure_wpasupplicant()
        self.configure_keyboard()
        self.configure_timezone()
        self.configure_hostname()
        os.sync()
