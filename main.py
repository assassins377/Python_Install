import wx
import logging
from gui import MInstAllFrame
import config

def main():
    logging.info("========================================")
    logging.info(f"Запуск MInstAll v{config.CONFIG_VERSION}")
    logging.info("========================================")
    
    app = wx.App(False)
    frame = MInstAllFrame()
    frame.Show()
    app.MainLoop()

if __name__ == "__main__":
    main()