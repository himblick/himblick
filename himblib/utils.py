from __future__ import annotations
import logging
import os
import tempfile
import sys

log = logging.getLogger(__name__)


class atomic_writer(object):
    """
    Atomically write to a file
    """
    def __init__(self, fname, mode="w+b", chmod=0o664, sync=True, **kw):
        self.fname = fname
        self.chmod = chmod
        self.sync = sync
        dirname = os.path.dirname(self.fname)
        if not os.path.isdir(dirname):
            os.makedirs(dirname)
        self.fd, self.abspath = tempfile.mkstemp(dir=dirname, text="b" not in mode)
        self.outfd = open(self.fd, mode, closefd=True, **kw)

    def __enter__(self):
        return self.outfd

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.outfd.flush()
            if self.sync:
                os.fdatasync(self.fd)
            os.fchmod(self.fd, self.chmod)
            os.rename(self.abspath, self.fname)
        else:
            os.unlink(self.abspath)
        self.outfd.close()
        return False


def progress(lst):
    if os.isatty(sys.stdout.fileno()):
        try:
            import progressbar
        except ModuleNotFoundError:
            log.warn("install python3-progressbar for a fancier progressbar")
            progressbar = None

        total = len(lst)
        if progressbar:
            pbar = progressbar.ProgressBar(maxval=total, widgets=[
                progressbar.Timer(), " ",
                progressbar.Bar(), " ",
                progressbar.SimpleProgress(), " ",
                progressbar.Percentage(), " ",
                progressbar.AdaptiveETA(),
            ])
            yield from pbar(lst)
        else:
            for idx, el in enumerate(lst, start=1):
                if idx % 100 == 0:
                    print(f"{idx}/{total}")
                yield el
            print(f"{total}/{total}")
    else:
        yield from lst
