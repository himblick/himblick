from __future__ import annotations
from typing import List, Union
import subprocess
import shutil
import os
import shlex
import logging

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
            
    def systemctl_enable(self, unit: str):
        """
        Enable (and if needed unmask) the given systemd unit
        """
        env = dict(os.environ)
        env["LANG"] = "C"
        subprocess.run(["systemctl", "--root=" + self.root, "enable", unit], check=True, env=env)
        subprocess.run(["systemctl", "--root=" + self.root, "unmask", unit], check=True, env=env)

    def systemctl_disable(self, unit: str, mask=True):
        """
        Disable (and optionally mask) the given systemd unit
        """
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
        return subprocess.run(chroot_cmd, check=check, **kw)

    def apt_install(self, pkglist: Union[str, List[str]]):
        """
        Install the given package(s), if they are not installed yet
        """
        if isinstance(pkglist, str):
            pkglist = [pkglist]

        cmd = ["apt", "-y", "install"]
        has_packages = False
        for pkg in pkglist:
            if os.path.exists(os.path.join(self.root, "var", "lib", "dpkg", "info", pkg + ".list")):
                continue
            cmd.append(pkg)
            has_packages = True

        if not has_packages:
            return

        self.run(cmd)