import os
import urllib.parse
import logging

from PyQt5.QtCore import QThread, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QTextEdit, QCheckBox, QFileDialog, QGroupBox, QMessageBox,
)
from PyQt5.QtGui import QTextCursor, QTextCharFormat, QColor, QFont

from GAG_tools.config_manager import ConfigManager
from GAG_tools.gsv_backend import GsvBackend

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class BackendStartThread(QThread):
    finished_start = pyqtSignal(bool, str)

    def __init__(self, backend):
        super().__init__()
        self.backend = backend

    def run(self):
        try:
            ok = self.backend.start(timeout=240)
            self.finished_start.emit(ok, "" if ok else "backend did not become ready (see log)")
        except Exception as e:  # noqa: BLE001
            self.finished_start.emit(False, str(e))


class APIManager(QWidget):
    """gsv.cpp backend tab: launches the Python sidecar and gsv_server."""

    # log() is called from the backend reader threads; never touch widgets from there.
    log_signal = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config_manager = ConfigManager()
        self.backend = None
        self.start_thread = None
        self.starting = False
        self.is_running = False
        self.initUI()
        self.log_signal.connect(self.update_output, Qt.QueuedConnection)
        if self.config_manager.get_value("autostart_api"):
            self.start_api()

    # ---- UI -------------------------------------------------------------
    def initUI(self):
        self.setGeometry(100, 100, 800, 800)
        layout = QVBoxLayout(self)

        cfg = QGroupBox(self.tr("gsv.cpp backend"))
        cfg_layout = QVBoxLayout()

        row = QHBoxLayout()
        row.addWidget(QLabel(self.tr("API URL:")))
        self.url_input = QLineEdit(self.config_manager.get_value("api_url"))
        row.addWidget(self.url_input)
        cfg_layout.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel(self.tr("gsv_server:")))
        self.server_input = QLineEdit(self.config_manager.get_value("gsv_server_path"))
        row.addWidget(self.server_input)
        btn = QPushButton(self.tr("Browse"))
        btn.clicked.connect(self.browse_server)
        row.addWidget(btn)
        cfg_layout.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel(self.tr("Models:")))
        self.models_input = QLineEdit(self.config_manager.get_value("models_dir"))
        row.addWidget(self.models_input)
        btn = QPushButton(self.tr("Browse"))
        btn.clicked.connect(self.browse_models)
        row.addWidget(btn)
        cfg_layout.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel(self.tr("Device:")))
        self.device_combo = QComboBox()
        self.device_combo.addItems(["vulkan", "cpu"])
        self.device_combo.setCurrentText(self.config_manager.get_value("device") or "vulkan")
        row.addWidget(self.device_combo)
        row.addStretch(1)
        cfg_layout.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel(self.tr("Sidecar Python (optional):")))
        self.sidecar_python_input = QLineEdit(self.config_manager.get_value("sidecar_python"))
        row.addWidget(self.sidecar_python_input)
        btn = QPushButton(self.tr("Browse"))
        btn.clicked.connect(self.browse_sidecar_python)
        row.addWidget(btn)
        cfg_layout.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel(self.tr("Frontend data:")))
        self.data_input = QLineEdit(self.config_manager.get_value("sidecar_data_dir"))
        row.addWidget(self.data_input)
        btn = QPushButton(self.tr("Browse"))
        btn.clicked.connect(self.browse_data_dir)
        row.addWidget(btn)
        cfg_layout.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel(self.tr("Reference dirs:")))
        self.allow_input = QLineEdit(self.config_manager.get_value("allow_dirs"))
        self.allow_input.setToolTip(
            self.tr("Directories the engine may read reference audio from, separated by ';'")
        )
        row.addWidget(self.allow_input)
        cfg_layout.addLayout(row)

        self.autostart_checkbox = QCheckBox(self.tr("Start backend with the GUI"))
        self.autostart_checkbox.setChecked(self.config_manager.get_value("autostart_api"))
        cfg_layout.addWidget(self.autostart_checkbox)

        cfg.setLayout(cfg_layout)
        layout.addWidget(cfg)

        control = QHBoxLayout()
        self.start_button = QPushButton(self.tr("Start"))
        self.start_button.setStyleSheet("min-height: 30px;")
        self.stop_button = QPushButton(self.tr("Stop"))
        self.stop_button.setStyleSheet("min-height: 30px;")
        self.start_button.clicked.connect(self.start_api)
        self.stop_button.clicked.connect(self.stop_api)
        self.stop_button.setEnabled(False)
        control.addWidget(self.start_button)
        control.addWidget(self.stop_button)
        layout.addLayout(control)

        layout.addWidget(QLabel(self.tr("Backend log:")))
        self.output_console = QTextEdit()
        self.output_console.setReadOnly(True)
        self.output_console.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #ffffff;
                font-family: "Microsoft YaHei Mono", Consolas, Monaco, monospace;
            }
        """)
        layout.addWidget(self.output_console)

    # ---- helpers --------------------------------------------------------
    def update_output(self, text, color=None, bold=False):
        if color is None:
            low = text.lower()
            color = "white"
            if "error" in low:
                color = "red"
            elif "warning" in low:
                color = "yellow"
            elif "listening" in low or "ready" in low:
                color = "green"
        cursor = self.output_console.textCursor()
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        if bold:
            fmt.setFontWeight(QFont.Bold)
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text + "\n", fmt)
        self.output_console.setTextCursor(cursor)
        self.output_console.ensureCursorVisible()

    def log(self, text):
        # Called from the sidecar/server reader threads: queue it to the GUI thread.
        self.log_signal.emit(text)

    def parse_url(self):
        url = self.url_input.text().strip() or "http://127.0.0.1:9880"
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 9880
        return host, port

    # ---- browse ---------------------------------------------------------
    def browse_server(self):
        p, _ = QFileDialog.getOpenFileName(
            self, self.tr("Select gsv_server"), "", "gsv_server (gsv_server*);;All files (*)"
        )
        if p:
            self.server_input.setText(p)
            self.config_manager.set_value("gsv_server_path", p)

    def browse_models(self):
        p = QFileDialog.getExistingDirectory(
            self, self.tr("Select models directory"), self.models_input.text()
        )
        if p:
            self.models_input.setText(p)
            self.config_manager.set_value("models_dir", p)

    def browse_sidecar_python(self):
        p, _ = QFileDialog.getOpenFileName(
            self, self.tr("Select Python"), "", "Python (python.exe python);;All files (*)"
        )
        if p:
            self.sidecar_python_input.setText(p)
            self.config_manager.set_value("sidecar_python", p)

    def browse_data_dir(self):
        p = QFileDialog.getExistingDirectory(
            self, self.tr("Select frontend data directory"), self.data_input.text()
        )
        if p:
            self.data_input.setText(p)
            self.config_manager.set_value("sidecar_data_dir", p)

    # ---- lifecycle ------------------------------------------------------
    def find_sidecar_script(self):
        models = self.models_input.text().strip()
        roots = [os.getcwd()]
        if models:
            roots.append(os.path.dirname(models))
        cands = []
        for root in roots:
            cands += [
                os.path.join(root, "tools", "text_sidecar.py"),
                os.path.join(root, "frontend", "text_sidecar.py"),
                os.path.join(root, "runtime", "frontend", "text_sidecar.py"),
            ]
        for c in cands:
            if os.path.exists(c):
                return c
        return ""

    def build_backend(self):
        host, port = self.parse_url()
        allow = [d.strip() for d in self.allow_input.text().replace(",", ";").split(";") if d.strip()]
        models = self.models_input.text().strip()
        if not allow and models:
            allow = [models]
        return GsvBackend(
            server_path=self.server_input.text().strip(),
            models_dir=models,
            device=self.device_combo.currentText(),
            port=port,
            allow_dirs=allow,
            sidecar_python=self.sidecar_python_input.text().strip(),
            sidecar_script=self.find_sidecar_script(),
            sidecar_port=port + 2,
            data_dir=self.data_input.text().strip(),
            log=self.log,
        )

    def save_config(self):
        self.config_manager.update_config(
            {
                "api_url": self.url_input.text(),
                "gsv_server_path": self.server_input.text(),
                "models_dir": self.models_input.text(),
                "device": self.device_combo.currentText(),
                "sidecar_python": self.sidecar_python_input.text(),
                "sidecar_data_dir": self.data_input.text(),
                "allow_dirs": self.allow_input.text(),
                "autostart_api": self.autostart_checkbox.isChecked(),
            }
        )

    def start_api(self):
        if self.is_running or self.starting:
            return
        self.save_config()
        if not os.path.exists(self.server_input.text().strip()):
            QMessageBox.warning(self, self.tr("Error"), self.tr("gsv_server not found"))
            return
        self.output_console.clear()
        self.update_output("Starting gsv.cpp backend...", color="#90EE90", bold=True)
        self.backend = self.build_backend()
        self.is_running = True
        self.starting = True
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.start_thread = BackendStartThread(self.backend)
        self.start_thread.finished_start.connect(self.on_started)
        # Keep the QThread alive until it has really finished: deleting it from inside its own
        # signal handler aborts with "QThread: Destroyed while thread is still running".
        self.start_thread.finished.connect(self.start_thread.deleteLater)
        self.start_thread.start()

    def on_started(self, ok, msg):
        self.starting = False
        if ok:
            self.update_output(
                "Backend is ready: http://127.0.0.1:%d" % self.backend.port,
                color="#90EE90",
                bold=True,
            )
        else:
            self.is_running = False
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.update_output("Backend failed: " + (msg or "unknown error"), color="red")
            QMessageBox.critical(self, self.tr("Error"), msg or self.tr("backend start failed"))

    def stop_api(self):
        if self.start_thread is not None and self.start_thread.isRunning():
            self.start_thread.wait(5000)
        self.starting = False
        if self.backend:
            self.backend.stop()
        self.backend = None
        self.is_running = False
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.update_output("Backend stopped.", color="#90EE90", bold=True)

    def cleanup(self):
        self.save_config()
        if self.is_running:
            self.stop_api()

    def closeEvent(self, event):
        self.cleanup()
        super().closeEvent(event)
