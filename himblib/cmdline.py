import sys
import logging

log = logging.getLogger()


class Fail(RuntimeError):
    """
    Exception thrown to show the user an error message without a stack trace
    """
    pass


class Command:
    # Command name (as used in command line)
    # Defaults to the lowercased class name
    NAME = None

    # Command description (as used in command line help)
    # Defaults to the strip()ped class docstring.
    DESC = None

    def __init__(self, args):
        self.args = args
        self.setup_logging()

    def setup_logging(self):
        FORMAT = "%(asctime)-15s %(levelname)s %(message)s"
        if self.args.debug:
            logging.basicConfig(level=logging.DEBUG, stream=sys.stderr, format=FORMAT)
        elif self.args.verbose:
            logging.basicConfig(level=logging.INFO, stream=sys.stderr, format=FORMAT)
        else:
            logging.basicConfig(level=logging.WARN, stream=sys.stderr, format=FORMAT)

    @classmethod
    def make_subparser(cls, subparsers):
        name = cls.NAME
        if name is None:
            name = cls.__name__.lower()

        desc = cls.DESC
        if desc is None:
            desc = cls.__doc__.strip()

        parser = subparsers.add_parser(name, help=desc)
        parser.set_defaults(handler=cls)
        parser.add_argument("-v", "--verbose", action="store_true", help="verbose output")
        parser.add_argument("--debug", action="store_true", help="verbose output")
        return parser
