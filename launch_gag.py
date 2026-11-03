"""Launcher used by the Windows one-click package (Start GAG.bat).

Runs the Qt app with pythonw.exe (no console window) and, if anything goes wrong, writes the
traceback to gag_error.log next to this file and shows it in a message box. That keeps a crash
diagnosable without leaving a console window open all the time.
"""
import os
import sys
import traceback

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)


def _fail(msg):
    try:
        with open(os.path.join(BASE, "gag_error.log"), "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass
    try:
        from PyQt5.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, "GAG", msg[-3000:])
    except Exception:
        pass


def main():
    try:
        import gsv_api_gui
        from PyQt5.QtWidgets import QApplication
        app = QApplication(sys.argv)
        window = gsv_api_gui.GSVApiGUI()
        window.show()
        return app.exec_()
    except Exception:
        _fail(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
