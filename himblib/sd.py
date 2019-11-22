from .cmdline import Command, Fail
import subprocess
import json
import logging

log = logging.getLogger(__name__)


class SD(Command):
    """
    Set up a SD image
    """
    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("--locate", action="store_true",
                            help="locate the device where the SD image is present")
        return parser

    def locate(self):
        res = subprocess.run(["lsblk", "-JOb"], text=True, capture_output=True, check=True)
        res = json.loads(res.stdout)
        devs = []
        for dev in res["blockdevices"]:
            if dev["rm"] and not dev["ro"] and dev["type"] == "disk" and dev["tran"] == "usb":
                devs.append(dev["path"])
                log.info("Found %s: %s %s %s %.3fGB",
                         dev["path"], dev["vendor"], dev["model"], dev["serial"],
                         int(dev["size"]) / (1024**3))
        if not devs:
            raise Fail("No candidate SD cards found")
        if len(devs) > 1:
            raise Fail(f"{len(devs)} SD cards found")
        return devs[0]

    def run(self):
        """
        Set up an imblick private image
        """
        if self.args.locate:
            print(self.locate())
        else:
            raise Fail("No command given: try --help")

