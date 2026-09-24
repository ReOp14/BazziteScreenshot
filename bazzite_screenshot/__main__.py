import argparse
import logging
import sys

from . import __version__


def main() -> int:
    parser = argparse.ArgumentParser(prog="bazzite-screenshot", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    daemon = sub.add_parser("daemon", help="run the resident capture daemon")
    daemon.add_argument("--notify", action="store_true", help="show desktop notifications after copy/failure")
    sub.add_parser("trigger", help="ask the running daemon to take a screenshot")
    shortcut = sub.add_parser("register-shortcut", help="bind Print Screen to BazziteScreenshot")
    shortcut.add_argument("--no-force", action="store_true", help="fail instead of taking Print from another app")
    sub.add_parser("unregister-shortcut", help="remove the Print Screen binding")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.command == "daemon":
        from .daemon import run_daemon
        return run_daemon(notify=args.notify)
    if args.command == "trigger":
        from .daemon import send_trigger
        return send_trigger()
    if args.command == "register-shortcut":
        from .shortcut import register
        return register(force=not args.no_force)
    if args.command == "unregister-shortcut":
        from .shortcut import unregister
        return unregister()
    return 2


if __name__ == "__main__":
    sys.exit(main())
