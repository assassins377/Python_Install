import argparse
import logging
import sys

import wx

import config
import core
from gui import MInstAllFrame


def main() -> None:
    parser = argparse.ArgumentParser(description="MInstAll — мастер тихой установки программ")
    parser.add_argument("--version", action="version", version=f"MInstAll v{config.APP_VERSION}")
    parser.parse_args()

    core.setup_logging()

    logging.info("========================================")
    logging.info(f"Запуск MInstAll v{config.APP_VERSION} (config schema v{config.CONFIG_VERSION})")
    logging.info("========================================")

    app = wx.App(False)
    frame = MInstAllFrame()
    frame.Show()
    app.MainLoop()


if __name__ == "__main__":
    main()
