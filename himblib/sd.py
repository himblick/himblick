from .cmdline import Command, Fail
import subprocess
import json
import logging
import os
from .utils import make_progressbar

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
        parser.add_argument("--umount", action="store_true",
                            help="unmount all partitions for the SD device")
        parser.add_argument("--write-image", action="store_true",
                            help="write the filesystem image to the SD device")
        return parser

    def locate(self):
        res = subprocess.run(["lsblk", "-JOb"], text=True, capture_output=True, check=True)
        res = json.loads(res.stdout)
        devs = []
        for dev in res["blockdevices"]:
            if dev["rm"] and not dev["ro"] and dev["type"] == "disk" and dev["tran"] == "usb":
                devs.append(dev)
                log.info("Found %s: %s %s %s %.3fGB",
                         dev["path"], dev["vendor"], dev["model"], dev["serial"],
                         int(dev["size"]) / (1024**3))
        if not devs:
            raise Fail("No candidate SD cards found")
        if len(devs) > 1:
            raise Fail(f"{len(devs)} SD cards found")
        return devs[0]

    def umount(self, dev):
        for part in dev["children"]:
            mp = part["mountpoint"]
            if not mp:
                continue
            subprocess.run(["umount", mp], check=True)

    def write_image(self, dev):
        chunksize = 16 * 1024 * 1024
        pbar = make_progressbar(maxval=os.path.getsize(self.settings.BASE_IMAGE))
        total_read = 0
        with open(self.settings.BASE_IMAGE, "rb") as fdin:
            with open(dev["path"], "wb") as fdout:
                pbar.start()
                while True:
                    buf = fdin.read(chunksize)
                    if not buf:
                        break
                    total_read += len(buf)
                    pbar.update(total_read)
                    fdout.write(buf)
                    fdout.flush()
                    os.fdatasync(fdout.fileno())
                pbar.finish()

    def run(self):
        """
        Set up an imblick private image
        """
        if self.args.locate:
            print(self.locate()["path"])
        elif self.args.umount:
            dev = self.locate()
            self.umount(dev)
        elif self.args.write_image:
            dev = self.locate()
            self.umount(dev)
            self.write_image(dev)
        else:
            raise Fail("No command given: try --help")
