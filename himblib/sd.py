from __future__ import annotations
from typing import Dict, Any
from .cmdline import Command, Fail
from .chroot import Chroot
from contextlib import contextmanager
import subprocess
import json
import logging
import os
import shlex
import shutil
import time
import yaml
from .utils import make_progressbar

log = logging.getLogger(__name__)


def format_gb(size):
    return "{:.3f}GB".format(size / (1024**3))


class Cache:
    def __init__(self, root):
        self.root = root

    def get(self, name):
        res = os.path.join(self.root, name)
        os.makedirs(res, exist_ok=True)
        return res


class SD(Command):
    """
    Set up a SD image
    """
    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("--config", "-C", action="store", metavar="settings.py",
                            help="configuration file to load")
        parser.add_argument("--hostname", action="store", metavar="hostname",
                            help="hostname to use")
        parser.add_argument("--shell", action="store_true",
                            help="open a shell inside the rootfs")
        parser.add_argument("--locate", action="store_true",
                            help="locate the device where the SD image is present")
        parser.add_argument("--umount", action="store_true",
                            help="unmount all partitions for the SD device")
        parser.add_argument("--write-image", action="store_true",
                            help="write the filesystem image to the SD device")
        parser.add_argument("--partition", action="store_true",
                            help="update the partition layout")
        parser.add_argument("--setup", action="store", nargs="?", const="all",
                            help="set up the system partition")
        return parser

    def __init__(self, args):
        super().__init__(args)
        self.settings = self.load_settings()

    def load_settings(self):
        from .settings import Settings
        settings = Settings()
        if self.args.config is not None:
            settings.load(self.args.config)
        return settings

    def locate(self) -> Dict[str, Any]:
        """
        Locate the SD card to work on

        :returns: the lsblk data structure for the SD device
        """
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

    def umount(self, dev: Dict[str, Any]):
        """
        Make sure all the SD partitions are unmounted

        :arg dev: the lsblk data structure for the SD device
        """
        for part in dev["children"]:
            mp = part["mountpoint"]
            if not mp:
                continue
            subprocess.run(["umount", mp], check=True)

    def write_image(self, dev: Dict[str, Any]):
        """
        Write the base image to the SD card
        """
        backing_store = bytearray(16 * 1024 * 1024)
        copy_buffer = memoryview(backing_store)
        pbar = make_progressbar(maxval=os.path.getsize(self.settings.BASE_IMAGE))
        total_read = 0
        with open(self.settings.BASE_IMAGE, "rb") as fdin:
            with open(dev["path"], "wb") as fdout:
                pbar.start()
                while True:
                    bytes_read = fdin.readinto(copy_buffer)
                    if not bytes_read:
                        break
                    total_read += bytes_read
                    pbar.update(total_read)
                    fdout.write(copy_buffer[:bytes_read])
                    fdout.flush()
                    os.fdatasync(fdout.fileno())
                pbar.finish()

    def partition(self, dev: Dict[str, Any]):
        """
        Update partitioning on the SD card
        """
        try:
            import parted
        except ModuleNotFoundError:
            raise Fail("please install python3-parted")

        # See https://github.com/dcantrell/pyparted/tree/master/examples
        # for pyparted examples
        # See https://www.gnu.org/software/parted/api/modules.html
        # for library documentation

        device = parted.getDevice(dev["path"])
        disk = parted.newDisk(device)

        if not disk.check():
            raise Fail("Parted disk check failed (TODO: find out how to get details about what check failed)")

        partitions = list(disk.partitions)
        if len(partitions) > 3:
            raise Fail(f"SD card has too many ({len(partitions)}) partitions: reset it with --write-image")

        part_boot = partitions[0]
        fs = part_boot.fileSystem
        if not fs:
            raise Fail("SD boot partition has no file system: reset it with --write-image")
        if fs.type != "fat32":
            raise Fail("SD boot partition is not a fat32 partition: reset it with --write-image")

        part_root = partitions[1]
        fs = part_root.fileSystem
        if not fs:
            raise Fail("SD system partition has no file system: reset it with --write-image")
        if fs.type != "ext4":
            raise Fail("SD system partition is not an ext4 partition: reset it with --write-image")

        if len(partitions) == 3:
            part_media = partitions[2]
        else:
            part_media = None

        # TODO: check partition label, and error out if it exists and is not 'media'

        target_root_size = int(round(4 * 1024**3 / device.sectorSize))
        need_root_resize = part_root.geometry.end - part_root.geometry.start - 16 < target_root_size
        log.info("%s: partition is only %.1fGB and needs resizing",
                 part_root.path, target_root_size * device.sectorSize / 1024**3)

        if need_root_resize:
            if part_media:
                log.info("%s: partition needs resize: removing media partition %s", part_root.path, part_media.path)
                disk.deletePartition(part_media)
                part_media = None

            # Resize rootfs partition
            constraint = device.optimalAlignedConstraint
            constraint.minSize = target_root_size
            constraint.maxSize = target_root_size
            disk.maximizePartition(part_root, constraint)
            disk.commit()
            time.sleep(0.5)
            self.umount(self.locate())
            time.sleep(0.3)

            subprocess.run(["e2fsck", "-fy", part_root.path], check=True)
            subprocess.run(["resize2fs", part_root.path], check=True)

        if part_media is None:
            # Get the last free space
            free_space = disk.getFreeSpaceRegions()[-1]

            # Create media partition
            partition = parted.Partition(
                    disk=disk,
                    type=parted.PARTITION_NORMAL,
                    geometry=free_space)
            disk.addPartition(partition=partition, constraint=device.optimalAlignedConstraint)
            disk.commit()
            time.sleep(0.5)
            self.umount(self.locate())
            time.sleep(0.3)
            log.info("%s media partition created", format_gb(free_space.length * device.sectorSize))

            # Create exFAT file system
            subprocess.run(["mkexfatfs", "-n", "media", partition.path], check=True)
            log.info("%s media partition formatted", format_gb(free_space.length * device.sectorSize))
        else:
            # Current parted cannot seem to deal with exfat, let's use exfatfsck instead
            res = subprocess.run(["exfatfsck", "-n", partitions[2].path], capture_output=True, check=False)
            if res.returncode != 0:
                raise Fail("SD media partition exFAT file system failed checks:"
                           " reset it with --write-image and rerun --partition")

    @contextmanager
    def mounted(self, label):
        part = self.locate_partition(label)
        if part["mountpoint"] is None:
            log.info("Mounting %s partition %s", label, part["path"])
            subprocess.run(["udisksctl", "mount", "-b", part["path"]], stdout=subprocess.DEVNULL, check=True)
            part = self.locate_partition(label)

        yield Chroot(part["mountpoint"])

        subprocess.run(["udisksctl", "unmount", "-b", part["path"]], stdout=subprocess.DEVNULL, check=True)

    def setup_boot(self):
        with self.mounted("boot") as chroot:
            chroot.cleanup_raspbian_boot()

            # WiFi configuration
            # Write a wifi.ini that will be processed by `himblick wifi-setup` on boot
            wifi_config = chroot.abspath("wifi.ini")
            if self.settings.WIFI_CONFIG:
                with open(wifi_config, "wt") as fd:
                    fd.write(self.settings.WIFI_CONFIG.lstrip())
            elif os.path.exists(wifi_config):
                os.unlink(wifi_config)

    def save_apt_cache(self, chroot: Chroot):
        """
        Copy .deb files from the apt cache in the rootfs to our local cache
        """
        if not self.cache:
            return

        rootfs_apt_cache = chroot.abspath("/var/cache/apt/archives")
        apt_cache_root = self.cache.get("apt")

        for fn in os.listdir(rootfs_apt_cache):
            if not fn.endswith(".deb"):
                continue
            src = os.path.join(rootfs_apt_cache, fn)
            dest = os.path.join(apt_cache_root, fn)
            if os.path.exists(dest) and os.path.getsize(dest) == os.path.getsize(src):
                continue
            shutil.copy(src, dest)

    def restore_apt_cache(self, chroot: Chroot):
        """
        Copy .deb files from our local cache to the apt cache in the rootfs
        """
        if not self.cache:
            return

        rootfs_apt_cache = chroot.abspath("/var/cache/apt/archives")
        apt_cache_root = self.cache.get("apt")

        for fn in os.listdir(apt_cache_root):
            if not fn.endswith(".deb"):
                continue
            src = os.path.join(apt_cache_root, fn)
            dest = os.path.join(rootfs_apt_cache, fn)
            if os.path.exists(dest) and os.path.getsize(dest) == os.path.getsize(src):
                continue
            shutil.copy(src, dest)

    def setup_rootfs(self):
        with self.mounted("rootfs") as chroot:
            chroot.cleanup_raspbian_rootfs()

            self.restore_apt_cache(chroot)

            # Generate SSH host keys
            ssh_dir = chroot.abspath("/etc/ssh")
            # Remove existing host keys
            for fn in os.listdir(ssh_dir):
                if fn.startswith("ssh_host_") and fn.endswith("_key"):
                    os.unlink(os.path.join(ssh_dir, fn))
            # Install or generate new ones
            if self.settings.SSH_HOST_KEYS is None:
                # Generate new ones
                subprocess.run(["ssh-keygen", "-A", "-f", chroot.root], check=True)
            else:
                subprocess.run(["tar", "-C", ssh_dir, "-axf", self.settings.SSH_HOST_KEYS], check=True)

            # Update apt cache
            apt_cache = chroot.abspath("/var/cache/apt/pkgcache.bin")
            if not os.path.exists(apt_cache) or time.time() - os.path.getmtime(apt_cache) > 86400:
                chroot.run(["apt", "update"], check=True)

            # Install our own package
            if not os.path.exists(self.settings.HIMBLICK_PACKAGE):
                raise Fail(f"{self.settings.HIMBLICK_PACKAGE} (configured as HIMBLICK_PACKAGE) does not exist")
            debname = os.path.basename(self.settings.HIMBLICK_PACKAGE)
            dst_pkgfile = os.path.join("/srv/himblick", debname)
            if chroot.copy_if_unchanged(self.settings.HIMBLICK_PACKAGE, dst_pkgfile):
                chroot.run(["apt", "-y", "--no-install-recommends", "--reinstall", "install", dst_pkgfile])

            # Do the systemd unit manipulation here, because it does not work
            # in ansible's playbook, as systemd is not started in the chroot
            # and ansible requires it even to enable units, even if it
            # documents that it doesn't.
            #
            # See https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=895550)
            chroot.systemctl_enable("himblick_wifi_setup.service")

            # Enable ssh
            self.systemctl_enable("ssh.service")

            # Make sure ansible is installed in the chroot
            chroot.apt_install("ansible")

            # We cannot simply use ansible's chroot connector:
            #  - ansible does not mount /dev, /proc and so on in chroots, so
            #    many packages fail to install
            #
            # We work around it coping all ansible needs inside the rootfs,
            # then using systemd-nspawn to run ansible inside it using the
            # `local` connector.
            #
            #  - systemd ansible operations still don't work, so we do them
            #    here in himblick instead

            # Create an ansible environment inside the rootfs
            ansible_dir = chroot.abspath("/srv/himblick/ansible", create=True)

            # TODO: take playbook and roles names from config?

            # Copy the ansible playbook and roles
            chroot.copy_to("rootfs.yaml", "/srv/himblick/ansible")
            chroot.copy_to("roles", "/srv/himblick/ansible")

            # Vars to pass to the ansible playbook
            playbook_vars = {
                "KEYBOARD_LAYOUT": self.settings.KEYBOARD_LAYOUT,
                "TIMEZONE": self.settings.TIMEZONE,
                "HOSTNAME": self.args.hostname or self.settings.HOSTNAME,
            }
            if self.settings.SSH_AUTHORIZED_KEY:
                with open(self.settings.SSH_AUTHORIZED_KEY, "rt") as fd:
                    playbook_vars["SSH_AUTHORIZED_KEY"] = fd.read()

            vars_file = os.path.join(ansible_dir, "himblick-vars.yaml")
            with open(vars_file, "wt") as fd:
                yaml.dump(playbook_vars, fd)

            # Write ansible's inventory
            ansible_inventory = os.path.join(ansible_dir, "inventory.ini")
            with open(ansible_inventory, "wt") as fd:
                print("[rootfs]", file=fd)
                print("localhost ansible_connection=local", file=fd)

            # Write ansible's config
            ansible_cfg = os.path.join(ansible_dir, "ansible.cfg")
            with open(ansible_cfg, "wt") as fd:
                print("[defaults]", file=fd)
                print("nocows = 1", file=fd)
                print("inventory = inventory.ini", file=fd)

            # Write ansible's startup script
            args = ["exec", "ansible-playbook", "-v", "rootfs.yaml"]
            ansible_sh = os.path.join(ansible_dir, "rootfs.sh")
            with open(ansible_sh, "wt") as fd:
                print("#!/bin/sh", file=fd)
                print("set -xue", file=fd)
                print('cd $(dirname -- "$0")', file=fd)
                print("export ANSIBLE_CONFIG=ansible.cfg", file=fd)
                print(" ".join(shlex.quote(x) for x in args), file=fd)
            os.chmod(ansible_sh, 0o755)

            # Run ansible
            chroot.run(["/srv/himblick/ansible/rootfs.sh"], check=True)

            self.save_apt_cache(chroot)

    def run(self):
        """
        Set up an imblick private image
        """
        self.cache = None
        if self.settings.CACHE_DIR:
            self.cache = Cache(self.settings.CACHE_DIR)

        if self.args.shell:
            with self.mounted("rootfs") as chroot:
                chroot.run([])
        elif self.args.locate:
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
        elif self.args.setup:
            if self.args.setup in ("boot", "all"):
                self.setup_boot()
            if self.args.setup in ("rootfs", "all"):
                self.setup_rootfs()
        else:
            raise Fail("No command given: try --help")
