from .cmdline import Command, Fail
import subprocess
import json
import logging
import os
import tempfile
from .utils import make_progressbar

log = logging.getLogger(__name__)


def format_gb(size):
    return "{:.3f}GB".format(size / (1024**3))


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
        parser.add_argument("--partition", action="store_true",
                            help="update the partition layout")
        parser.add_argument("--setup-system", action="store_true",
                            help="set up the system partition")
        return parser

    def locate(self):
        res = subprocess.run(["lsblk", "-JOb"], text=True, capture_output=True, check=True)
        res = json.loads(res.stdout)
        devs = []
        for dev in res["blockdevices"]:
            if dev["rm"] and not dev["ro"] and dev["type"] == "disk" and dev["tran"] == "usb":
                devs.append(dev)
                log.info("Found %s: %s %s %s %s",
                         dev["path"], dev["vendor"], dev["model"], dev["serial"],
                         format_gb(int(dev["size"])))
        if not devs:
            raise Fail("No candidate SD cards found")
        if len(devs) > 1:
            raise Fail(f"{len(devs)} SD cards found")
        return devs[0]

    def locate_partition(self, label):
        dev = self.locate()
        for part in dev["children"]:
            if part["label"] != label:
                continue
            return part

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

    def partition(self, dev):
        try:
            import parted
        except ModuleNotFoundError:
            raise Fail("please install python3-parted")
        device = parted.getDevice(dev["path"])
        disk = parted.newDisk(device)

        if not disk.check():
            raise Fail("Parted disk check failed (TODO: find out how to get details about what check failed)")

        partitions = list(disk.partitions)
        if len(partitions) > 3:
            raise Fail(f"SD card has too many ({len(partitions)}) partitions: reset it with --write-image")

        fs = partitions[0].fileSystem
        if not fs:
            raise Fail("SD boot partition has no file system: reset it with --write-image")
        if fs.type != "fat32":
            raise Fail("SD boot partition is not a fat32 partition: reset it with --write-image")

        fs = partitions[1].fileSystem
        if not fs:
            raise Fail("SD system partition has no file system: reset it with --write-image")
        if fs.type != "ext4":
            raise Fail("SD boot partition is not an ext4 partition: reset it with --write-image")

        if len(partitions) == 2:
            # Media partition to be created

            # Get the last free space
            free_space = disk.getFreeSpaceRegions()[-1]
            partition = parted.Partition(
                    disk=disk,
                    type=parted.PARTITION_NORMAL,
                    geometry=free_space)
            disk.addPartition(partition=partition, constraint=device.optimalAlignedConstraint)
            disk.commit()
            log.info("%s media partition created", format_gb(free_space.length))

            # Create exFAT file system
            subprocess.run(["mkexfatfs", "-n", "media", partition.path], check=True)
            log.info("%s media partition formatted", format_gb(free_space.length))
        else:
            # Current parted cannot seem to deal with exfat, let's use exfatfsck instead
            res = subprocess.run(["exfatfsck", "-n", partitions[2].path], capture_output=True, check=False)
            if res.returncode != 0:
                raise Fail("SD media partition exFAT file system failed checks:"
                           " reset it with --write-image and rerun --partition")

    def setup_system(self):
        part = self.locate_partition("rootfs")
        if part["mountpoint"] is None:
            log.info("Mounting rootfs partition %s", part["path"])
            subprocess.run(["udisksctl", "mount", "-b", part["path"]], stdout=subprocess.DEVNULL, check=True)
            part = self.locate_partition("rootfs")

        root = part["mountpoint"]
        # Vars to pass to the ansible playbook
        playbook_vars = {}
        print(root)

        with tempfile.TemporaryDirectory() as workdir:
            ansible_inventory = os.path.join(workdir, "inventory.ini")
            with open(ansible_inventory, "wt") as fd:
                print("[rootfs]", file=fd)
                print("{} ansible_connection=chroot {}".format(
                        os.path.abspath(root),
                        " ".join("{}={}".format(k, v) for k, v in playbook_vars.items())),
                      file=fd)

            ansible_cfg = os.path.join(workdir, "ansible.cfg")
            with open(ansible_cfg, "wt") as fd:
                print("[defaults]", file=fd)
                print("nocows = 1", file=fd)
                print("inventory = {}".format(os.path.abspath(ansible_inventory)), file=fd)

            env = dict(os.environ)
            env["ANSIBLE_CONFIG"] = ansible_cfg
            subprocess.run(["ansible", "all", "-m", "setup"], env=env, check=True)
            # args = ["ansible-playbook", "-v", os.path.abspath(sysdesc.playbook)]
            # ansible_sh = os.path.join(workdir, "ansible.sh")
            # with open(ansible_sh, "wt") as fd:
            #     print("#!/bin/sh", file=fd)
            #     print("set -xue", file=fd)
            #     print("export ANSIBLE_CONFIG={}".format(shlex.quote(ansible_cfg)), file=fd)
            #     print(" ".join(shlex.quote(x) for x in args), file=fd)
            # os.chmod(ansible_sh, 0o755)
#
#            res = self._run_ansible([ansible_sh])
#            if res.result != 0:
#                self.log.warn("Rerunning ansible to check what fails")
#                self._run_ansible([ansible_sh])
#                raise RuntimeError("ansible exited with result {}".format(res.result))
#            else:
#                return res.changed == 0 and res.unreachable == 0 and self.failed == 0

        subprocess.run(["udisksctl", "unmount", "-b", part["path"]], stdout=subprocess.DEVNULL, check=True)

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
        elif self.args.partition:
            dev = self.locate()
            self.umount(dev)
            self.partition(dev)
        elif self.args.setup_system:
            self.setup_system()
        else:
            raise Fail("No command given: try --help")
