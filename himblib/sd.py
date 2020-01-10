from __future__ import annotations
from typing import Dict, Any
from .cmdline import Command, Fail
from .chroot import Chroot
from .settings import Settings
from contextlib import contextmanager
import subprocess
import json
import logging
import os
import shutil
import time
from .utils import make_progressbar, run

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
        parser.add_argument("--config", "-C", action="store", metavar="file.conf",
                            default="himblick.conf",
                            help="configuration file to load (default: himblick.conf)")
        parser.add_argument("--force", "-f", action="store_true",
                            help="do not ask for confirmation before destructive operations")
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
        parser.add_argument("--provision", action="store_true",
                            help="provision a new SD card (write-image, partition, setup)")
        return parser

    def __init__(self, args):
        super().__init__(args)
        self.settings = Settings(self.args.config)

    def locate(self) -> Dict[str, Any]:
        """
        Locate the SD card to work on

        :returns: the lsblk data structure for the SD device
        """
        res = run(["lsblk", "--json", "--output-all", "--bytes"], capture_output=True, check=True)
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

    @contextmanager
    def pause_automounting(self, dev: Dict[str, Any]):
        """
        Pause automounting on the device for the duration of this context
        manager
        """
        # See /usr/lib/udisks2/udisks2-inhibit
        devpath = dev["path"]
        rules_dir = "/run/udev/rules.d"
        os.makedirs(rules_dir, exist_ok=True)
        rule_file = os.path.join(rules_dir, "90-udisks-inhibit-" + devpath.replace("/", "_") + ".rules")
        with open(rule_file, "wt") as fd:
            print('SUBSYSTEM=="block", ENV{DEVNAME}=="' + devpath + '*", ENV{UDISKS_IGNORE}="1"', file=fd)
            fd.flush()
            os.fsync(fd.fileno())
        run(["udevadm", "control", "--reload"])
        run(["udevadm", "trigger", "--settle", "--subsystem-match=block"])
        try:
            yield
        finally:
            os.unlink(rule_file)
            run(["udevadm", "control", "--reload"])
            run(["udevadm", "trigger", "--settle", "--subsystem-match=block"])

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
            run(["umount", mp])

    def write_image(self, dev: Dict[str, Any], sync=True):
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
                    if sync:
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
        need_root_resize = part_root.geometry.end - part_root.geometry.start < target_root_size - 16
        if need_root_resize:
            log.info("%s: partition is only %.1fGB and needs resizing (current: %d, target: %d)",
                     part_root.path, target_root_size * device.sectorSize / 1024**3,
                     part_root.geometry.end - part_root.geometry.start, target_root_size)

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

            run(["e2fsck", "-fy", part_root.path])
            run(["resize2fs", part_root.path])

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
            run(["mkexfatfs", "-n", "media", partition.path])
            log.info("%s media partition formatted", format_gb(free_space.length * device.sectorSize))
        else:
            # Current parted cannot seem to deal with exfat, let's use exfatfsck instead
            res = run(["exfatfsck", "-n", partitions[2].path], capture_output=True)
            if res.returncode != 0:
                raise Fail("SD media partition exFAT file system failed checks:"
                           " reset it with --write-image and rerun --partition")

    @contextmanager
    def ext4_dir_index_workaround(self, part):
        """
        Temporarily disable dir_index of the ext4 filesystem to work around the
        issue at https://lkml.org/lkml/2018/12/27/155
        """
        is_ext4 = part["fstype"] == "ext4"
        if is_ext4:
            log.info("Disabling dir_index on %s to workaround https://lkml.org/lkml/2018/12/27/155", part["path"])
            run(["tune2fs", "-O", "^dir_index", part["path"]])

        try:
            yield
        finally:
            if is_ext4:
                log.info("Reenabling dir_index on %s", part["path"])
                run(["tune2fs", "-O", "dir_index", part["path"]])
                log.info("Running e2fsck to reindex directories on %s", part["path"])
                run(["e2fsck", "-f", part["path"]])

    @contextmanager
    def mounted(self, label):
        part = self.locate_partition(label)

        if part["mountpoint"] is not None:
            raise RuntimeError(f"please call this function while the {label} filesystem is unmounted")

        with self.ext4_dir_index_workaround(part):
            log.info("Mounting %s partition %s", label, part["path"])
            run(["udisksctl", "mount", "-b", part["path"]], stdout=subprocess.DEVNULL)
            part = self.locate_partition(label)

            yield Chroot(part["mountpoint"])

            run(["udisksctl", "unmount", "-b", part["path"]], stdout=subprocess.DEVNULL)

    def setup_boot(self):
        with self.mounted("boot") as chroot:
            chroot.cleanup_raspbian_boot()

            # Himblick host configuration
            # Write a himblick.conf that will be processed by `himblick host-setup` on boot
            chroot.write_file("himblick.conf", self.settings.non_provision_settings)

            # Disable fsck on boot
            with chroot.edit_kernel_commandline() as parts:
                try:
                    parts.remove("fsck.repair=yes")
                except ValueError:
                    pass
                if "fsck.mode=skip" not in parts:
                    parts.append("fsck.mode=skip")

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

    @contextmanager
    def mount_rootfs(self):
        with self.mounted("boot") as boot:
            with self.mounted("rootfs") as rootfs:
                run(["mount", "--bind", boot.root, rootfs.abspath("/boot")])
                try:
                    yield rootfs
                finally:
                    run(["umount", rootfs.abspath("/boot")])

    def setup_rootfs(self):
        with self.mount_rootfs() as chroot:
            chroot.cleanup_raspbian_rootfs()

            self.restore_apt_cache(chroot)

            # Update apt cache
            apt_cache = chroot.abspath("/var/cache/apt/pkgcache.bin")
            if not os.path.exists(apt_cache) or time.time() - os.path.getmtime(apt_cache) > 86400:
                chroot.run(["apt", "update"], check=True)

            chroot.setup_readonly_root()

            # Generate SSH host keys
            ssh_dir = chroot.abspath("/etc/ssh")
            # Remove existing host keys
            for fn in os.listdir(ssh_dir):
                if fn.startswith("ssh_host_") and fn.endswith("_key"):
                    os.unlink(os.path.join(ssh_dir, fn))
            # Install or generate new ones
            if not self.settings.SSH_HOST_KEYS:
                # Generate new ones
                run(["ssh-keygen", "-A", "-f", chroot.root])
            else:
                run(["tar", "-C", ssh_dir, "-axf", self.settings.SSH_HOST_KEYS])

            # Install our own package
            if not os.path.exists(self.settings.HIMBLICK_PACKAGE):
                raise Fail(f"{self.settings.HIMBLICK_PACKAGE} (configured as HIMBLICK_PACKAGE) does not exist")
            debname = os.path.basename(self.settings.HIMBLICK_PACKAGE)
            dst_pkgfile = os.path.join("/srv/himblick", debname)
            if chroot.copy_if_unchanged(self.settings.HIMBLICK_PACKAGE, dst_pkgfile):
                chroot.run(["apt", "-y", "--no-install-recommends", "--reinstall", "install", dst_pkgfile])

            # Install what host-setup needs
            chroot.apt_install("keyboard-configuration")

            # Do the systemd unit manipulation here, because it does not work
            # in ansible's playbook, as systemd is not started in the chroot
            # and ansible requires it even to enable units, even if it
            # documents that it doesn't.
            #
            # See https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=895550)
            chroot.systemctl_enable("himblick_host_setup.service")

            # Enable ssh
            chroot.systemctl_enable("ssh.service")

            # Do not wait for being online to finish boot
            chroot.systemctl_disable("systemd-networkd-wait-online.service", mask=True)

            # Vars to pass to the ansible playbook
            playbook_vars = {
            }
            if self.settings.SSH_AUTHORIZED_KEY:
                with open(self.settings.SSH_AUTHORIZED_KEY, "rt") as fd:
                    playbook_vars["SSH_AUTHORIZED_KEY"] = fd.read()

            if self.settings.provision("ssh media public key"):
                with open(self.settings.provision("ssh media public key"), "rt") as fd:
                    playbook_vars["SSH_MEDIA_PUBLIC_KEY"] = fd.read()

            if self.settings.provision("ssh media private key"):
                with open(self.settings.provision("ssh media private key"), "rt") as fd:
                    playbook_vars["SSH_MEDIA_PRIVATE_KEY"] = fd.read()

            # TODO: take playbook and roles names from config?
            chroot.run_ansible("rootfs.yaml", "roles", playbook_vars)

            # Enable the /srv/media mount point, which ansible, as we run it
            # now, is unable to do
            chroot.systemctl_enable("srv-media.mount")
            chroot.systemctl_enable("srv-jail-media.mount")

            # chroot.run(["e2fsck", "-f", "/usr/share/mime"], check=True)

            self.save_apt_cache(chroot)

    def confirm_operation(self, dev, operation):
        """
        Ask for confirmation before performing a destructive operation on a
        device
        """
        if self.args.force:
            return True
        res = input(
                f"{operation} {dev['path']} ({dev['vendor']} {dev['model']} {format_gb(dev['size'])} (y/N)? ")
        return res.lower() == "y"

    def run(self):
        """
        Set up an imblick private image
        """
        self.cache = None
        if self.settings.CACHE_DIR:
            self.cache = Cache(self.settings.CACHE_DIR)

        if self.args.shell:
            dev = self.locate()
            self.umount(dev)
            with self.mount_rootfs() as chroot:
                chroot.run([])
        elif self.args.locate:
            print(self.locate()["path"])
        elif self.args.umount:
            dev = self.locate()
            self.umount(dev)
        elif self.args.write_image:
            dev = self.locate()
            if not self.confirm_operation(dev, "Write image to"):
                return 1
            self.umount(dev)
            self.write_image(dev)
        elif self.args.partition:
            dev = self.locate()
            if not self.confirm_operation(dev, "Adjust partitioning of"):
                return 1
            self.umount(dev)
            with self.pause_automounting(dev):
                self.partition(dev)
        elif self.args.setup:
            dev = self.locate()
            self.umount(dev)
            with self.pause_automounting(dev):
                if self.args.setup in ("boot", "all"):
                    self.setup_boot()
                if self.args.setup in ("rootfs", "all"):
                    self.setup_rootfs()
        elif self.args.provision:
            dev = self.locate()
            if not self.confirm_operation(dev, "Provision"):
                return 1
            self.umount(dev)
            with self.pause_automounting(dev):
                self.write_image(dev, sync=False)
                self.partition(dev)
                self.setup_boot()
                self.setup_rootfs()
        else:
            raise Fail("No command given: try --help")
