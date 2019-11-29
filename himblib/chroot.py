from __future__ import annotations
from typing import List, Union
from contextlib import contextmanager
import tempfile
import subprocess
import shutil
import os
import shlex
import logging
import yaml

log = logging.getLogger(__name__)


class Chroot:
    """
    Common operations on a chroot
    """
    def __init__(self, root):
        self.root = root

    def abspath(self, relpath: str, *args, create=False) -> str:
        """
        Get the out-of-chroot absolute path of ``relpath``.

        :arg create: if True, the destination is assumed to be a path, that is
                     created if it does not exist yet
        """
        if args:
            relpath = os.path.join(relpath, *args)
        res = os.path.join(self.root, relpath.lstrip("/"))
        if create:
            os.makedirs(res, exist_ok=True)
        return res

    def getmtime(self, relpath: str) -> float:
        """
        Get the mtime of a file inside the chroot, or 0 if it does not exist
        """
        try:
            return os.path.getmtime(self.abspath(relpath))
        except FileNotFoundError:
            return 0

    def write_file(self, relpath: str, contents: str):
        """
        Write/replace the file with the given content
        """
        dest = self.abspath(relpath)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.lexists(dest):
            os.unlink(dest)
        with open(dest, "wt") as fd:
            fd.write(contents)

    def write_symlink(self, relpath: str, target: str):
        """
        Write/replace the file with a symlink to the given target
        """
        dest = self.abspath(relpath)
        os.makedirs(os.path.basename(dest), exist_ok=True)
        if os.path.lexists(dest):
            os.unlink(dest)
        os.symlink(target, dest)

    @contextmanager
    def edit_kernel_commandline(self, fname="cmdline.txt"):
        """
        Manipulate the kernel command line as an editable list.

        If the list gets changed, it is written back.
        """
        dest = self.abspath(fname)
        with open(dest, "rt") as fd:
            line = fd.read().strip()

        line_split = line.split()
        yield line_split

        new_line = " ".join(line_split)
        if new_line != line:
            with open(dest, "wt") as fd:
                print(new_line, file=fd)

    def file_contents_replace(self, relpath: str, search: str, replace: str) -> bool:
        """
        Replace ``search`` with ``replace`` in ``relpath``.

        :return: True if the replace happened, False if ``relpath`` is
                 unchanged, or did not exist
        """
        # Remove ' init=/usr/lib/raspi-config/init_resize.sh' from cmdline.txt
        pathname = self.abspath(relpath)

        if not os.path.exists(pathname):
            return False

        with open(pathname, "rt") as fd:
            original = fd.read()

        replaced = original.replace(search, replace)
        if replaced == original:
            return False

        with open(pathname, "wt") as fd:
            fd.write(replaced)
        return True

    def copy_if_unchanged(self, src: str, dst_relpath: str) -> bool:
        """
        Copy ``src`` as ``dst_relpath`` inside the chroot, but only if
        ``dst_relpath`` does not exist or is different than ``src``.

        :return: True if the copy happened, False if ``dst_relpath`` was alredy
                 there with the right content
        """
        dest = self.abspath(dst_relpath)
        if os.path.exists(dest):
            # Do not install it twice if it didn't change
            with open(src, "rb") as fd:
                src_contents = fd.read()
            with open(dest, "rb") as fd:
                dst_contents = fd.read()
            if src_contents == dst_contents:
                return False

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.exists(dest):
            os.unlink(dest)
        shutil.copy(src, dest)
        return True

    def copy_to(self, src: str, dst_relpath: str):
        """
        Copy the given file or directory inside the given path in the chroot.

        The file name will not be changed.
        """
        basename = os.path.basename(src)
        dest = self.abspath(dst_relpath, basename)

        # Remove destination if it exists
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        elif os.path.exists(dest):
            os.unlink(dest)

        if os.path.isdir(src):
            shutil.copytree(src, dest)
        else:
            shutil.copy(src, dest)

    @contextmanager
    def working_resolvconf(self, relpath: str):
        """
        Temporarily replace /etc/resolv.conf in the chroot with the current
        system one
        """
        abspath = self.abspath(relpath)
        if os.path.lexists(abspath):
            fd, tmppath = tempfile.mkstemp(dir=os.path.dirname(abspath))
            os.close(fd)
            os.rename(abspath, tmppath)
            shutil.copy("/etc/resolv.conf", os.path.join(self.root, "etc/resolv.conf"))
        else:
            tmppath = None
        try:
            yield
        finally:
            if os.path.lexists(abspath):
                os.unlink(abspath)
            if tmppath is not None:
                os.rename(tmppath, abspath)

    def systemctl_enable(self, unit: str):
        """
        Enable (and if needed unmask) the given systemd unit
        """
        with self.working_resolvconf("/etc/resolv.conf"):
            env = dict(os.environ)
            env["LANG"] = "C"
            subprocess.run(["systemctl", "--root=" + self.root, "enable", unit], check=True, env=env)
            subprocess.run(["systemctl", "--root=" + self.root, "unmask", unit], check=True, env=env)

    def systemctl_disable(self, unit: str, mask=True):
        """
        Disable (and optionally mask) the given systemd unit
        """
        with self.working_resolvconf("/etc/resolv.conf"):
            env = dict(os.environ)
            env["LANG"] = "C"
            subprocess.run(["systemctl", "--root=" + self.root, "disable", unit], check=True, env=env)
            if mask:
                subprocess.run(["systemctl", "--root=" + self.root, "mask", unit], check=True, env=env)

    def run(self, cmd: List[str], check=True, **kw) -> subprocess.CompletedProcess:
        """
        Run the given command inside the chroot
        """
        log.info("%s: running %s", self.root, " ".join(shlex.quote(x) for x in cmd))
        chroot_cmd = ["systemd-nspawn", "-D", self.root]
        chroot_cmd.extend(cmd)
        if "env" not in kw:
            kw["env"] = dict(os.environ)
            kw["env"]["LANG"] = "C"
        with self.working_resolvconf("/etc/resolv.conf"):
            return subprocess.run(chroot_cmd, check=check, **kw)

    def apt_install(self, pkglist: Union[str, List[str]], recommends=False):
        """
        Install the given package(s), if they are not installed yet
        """
        if isinstance(pkglist, str):
            pkglist = [pkglist]

        cmd = ["apt", "-y", "install"]
        if not recommends:
            cmd.append("--no-install-recommends")

        has_packages = False
        for pkg in pkglist:
            if os.path.exists(os.path.join(self.root, "var", "lib", "dpkg", "info", pkg + ".list")):
                continue
            cmd.append(pkg)
            has_packages = True

        if not has_packages:
            return

        self.run(cmd)

    def dpkg_purge(self, pkglist: Union[str, List[str]]):
        """
        Deinstall and purge the given package(s), if they are installed
        """
        if isinstance(pkglist, str):
            pkglist = [pkglist]

        cmd = ["dpkg", "--purge"]
        has_packages = False
        for pkg in pkglist:
            if not os.path.exists(os.path.join(self.root, "var", "lib", "dpkg", "info", pkg + ".list")):
                continue
            cmd.append(pkg)
            has_packages = True

        if not has_packages:
            return

        self.run(cmd)

    def cleanup_raspbian_boot(self):
        """
        Remove the interactive raspbian customizations from the boot partition
        """
        # Remove ' init=/usr/lib/raspi-config/init_resize.sh' from cmdline.txt
        # This is present by default in raspbian to perform partition
        # resize on the first boot, and it removes itself and reboots after
        # running. We do not need it, as we do our own partition resizing.
        # Also, we can't keep it, since we remove raspi-config and the
        # init_resize.sh script would break without it
        with self.edit_kernel_commandline() as parts:
            try:
                parts.remove("init=/usr/lib/raspi-config/init_resize.sh")
            except ValueError:
                pass

    def cleanup_raspbian_rootfs(self):
        """
        Remove the interactive raspbian customizations from the rootfs
        partition
        """
        # To support multiple arm systems, ld.so.preload tends to contain something like:
        # /usr/lib/arm-linux-gnueabihf/libarmmem-${PLATFORM}.so
        # I'm not sure where that ${PLATFORM} would be expanded, but it
        # does not happen in a chroot/nspawn. Since we know we're working
        # on the 4B, we can expand it ourselves.
        self.file_contents_replace(
                relpath="/etc/ld.so.preload",
                search="${PLATFORM}",
                replace="aarch64")

        # Deinstall unneeded Raspbian packages
        self.dpkg_purge(["raspberrypi-net-mods", "raspi-config", "triggerhappy", "dhcpcd5", "ifupdown"])

        # Disable services we do not need
        self.systemctl_disable("apply_noobs_os_config")
        self.systemctl_disable("regenerate_ssh_host_keys")
        self.systemctl_disable("sshswitch")

        # Enable systemd-network and systemd-resolvd
        self.systemctl_disable("wpa_supplicant")
        self.systemctl_enable("wpa_supplicant@wlan0")
        self.systemctl_enable("systemd-networkd")
        self.write_symlink("/etc/resolv.conf", "/run/systemd/resolve/stub-resolv.conf")
        self.systemctl_enable("systemd-resolved")
        self.write_file("/etc/systemd/network/wlan0.network", """[Match]
Name=wlan0

[Network]
DHCP=ipv4

[DHCP]
RouteMetric=20
""")
        self.write_file("/etc/systemd/network/eth0.network", """[Match]
Name=eth0

[Network]
DHCP=all

[DHCP]
RouteMetric=10
""")

    def run_ansible(self, playbook, roles, host_vars):
        """
        Run ansible inside the chroot
        """
        # We cannot simply use ansible's chroot connector, since ansible does
        # not mount /dev, /proc and so on in chroots, so many packages fail to
        # install
        #
        # We work around it coping all ansible needs inside the rootfs, then
        # using systemd-nspawn to run ansible inside it using the `local`
        # connector.
        #
        # Local files lookups won't work, and we need to copy everything the
        # playbooks need inside the chroot in advance.
        #
        # systemd ansible operations still don't work, see
        # https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=895550)
        # so playbooks cannot use the 'systemd' module, and those operations
        # need to be performed directly in himblick instead

        # Make sure ansible is installed in the chroot
        self.apt_install("ansible")

        # Create an ansible environment inside the rootfs
        ansible_dir = self.abspath("/srv/himblick/ansible", create=True)

        # Copy the ansible playbook and roles
        self.copy_to("rootfs.yaml", "/srv/himblick/ansible")
        self.copy_to("roles", "/srv/himblick/ansible")

        # Write the variables
        vars_file = os.path.join(ansible_dir, "himblick-vars.yaml")
        with open(vars_file, "wt") as fd:
            yaml.dump(host_vars, fd)

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
            print("[inventory]", file=fd)
            # See https://github.com/ansible/ansible/issues/48859
            print("enable_plugins = ini", file=fd)

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
        self.run(["/srv/himblick/ansible/rootfs.sh"], check=True)
