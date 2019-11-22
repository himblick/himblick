from __future__ import annotations
import sys
import logging

log = logging.getLogger()


class Settings:
    def __init__(self):
        from . import global_settings
        self.add_module(global_settings)

    def as_dict(self):
        res = {}
        for setting in dir(self):
            if setting.isupper():
                res[setting] = getattr(self, setting)
        return res

    def add_module(self, mod):
        """
        Add uppercase settings from mod into this module
        """
        for setting in dir(mod):
            if setting.isupper():
                setattr(self, setting, getattr(mod, setting))

    def load(self, pathname):
        """
        Load settings from a python file, importing only uppercase symbols
        """
        orig_dwb = sys.dont_write_bytecode
        try:
            sys.dont_write_bytecode = True
            # http://stackoverflow.com/questions/67631/how-to-import-a-module-given-the-full-path
            import importlib.util
            spec = importlib.util.spec_from_file_location("redlab.settings", pathname)
            user_settings = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(user_settings)
        finally:
            sys.dont_write_bytecode = orig_dwb

        self.add_module(user_settings)
