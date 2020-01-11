from tornado import web
import pathlib
import os


class StaticFileHandler(web.StaticFileHandler):
    """
    StaticFileHandler that allows overriding paths in the static directory with
    system provided versions
    """
    SYSTEM_ASSET_PATH = pathlib.Path("/usr/share/javascript")

    @classmethod
    def get_absolute_path(self, root, path):
        orig_path = super().get_absolute_path(root, path)

        path = pathlib.PurePath(path)
        if not path.parts:
            return orig_path

        if os.path.exists(orig_path):
            return orig_path

        system_dir = self.SYSTEM_ASSET_PATH.joinpath(path.parts[0])
        if system_dir.is_dir():
            # If that asset directory exists in the system, look for things in
            # there
            return self.SYSTEM_ASSET_PATH.joinpath(path).as_posix()
        else:
            return orig_path

    def validate_absolute_path(self, root, absolute_path):
        """
        Rewrite of tornado's validate_absolute_path not to raise an error for
        paths in /usr/share/javascript/
        """
        root = pathlib.Path(root)
        absolute_path = pathlib.Path(absolute_path)

        is_system_root = absolute_path.parts[
            :len(self.SYSTEM_ASSET_PATH.parts)
            ] == self.SYSTEM_ASSET_PATH.parts
        is_static_root = absolute_path.parts[:len(root.parts)] == root.parts

        if not is_system_root and not is_static_root:
            raise web.HTTPError(
                403,
                "%s is not in root static directory or system assets path",
                self.path
                )

        if absolute_path.is_dir() and self.default_filename is not None:
            # need to look at the request.path here for when path is empty
            # but there is some prefix to the path that was already
            # trimmed by the routing
            if not self.request.path.endswith("/"):
                self.redirect(self.request.path + "/", permanent=True)
                return
            absolute_path = absolute_path.joinpath(self.default_filename)
        if not absolute_path.exists():
            raise web.HTTPError(404)
        if not absolute_path.is_file():
            raise web.HTTPError(403, "%s is not a file", self.path)
        return str(absolute_path)
