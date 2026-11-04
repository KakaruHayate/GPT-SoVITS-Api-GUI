"""Launch and supervise the gsv.cpp backend (gsv_server + optional Python sidecar).

Kept free of Qt so it can be unit-tested headlessly. The GUI only builds this object from
its widgets and calls start()/stop().
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

IS_WIN = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0

# Windows job object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: when the GUI process dies (even
# if it is force-killed), the OS terminates gsv_server/sidecar with it, so no model stays
# resident in VRAM.
_JOB = None


def _ensure_job():
    global _JOB
    if not IS_WIN or _JOB is not None:
        return _JOB
    try:
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                        ("WriteOperationCount", ctypes.c_ulonglong),
                        ("OtherOperationCount", ctypes.c_ulonglong),
                        ("ReadTransferCount", ctypes.c_ulonglong),
                        ("WriteTransferCount", ctypes.c_ulonglong),
                        ("OtherTransferCount", ctypes.c_ulonglong)]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                        ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.windll.kernel32
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000   # KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return None
        _JOB = job
    except Exception:
        _JOB = None
    return _JOB


def _assign_job(proc):
    job = _ensure_job()
    if not job:
        return
    try:
        import ctypes
        ctypes.windll.kernel32.AssignProcessToJobObject(job, int(proc._handle))
    except Exception:
        pass


def _spawn(cmd, env=None, cwd=None):
    kwargs = dict(
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        cwd=cwd,
        env=env,
    )
    if IS_WIN:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = si
        kwargs["creationflags"] = CREATE_NO_WINDOW
    proc = subprocess.Popen(cmd, **kwargs)
    _assign_job(proc)
    return proc


class GsvBackend:
    def __init__(
        self,
        server_path,
        models_dir,
        device="vulkan",
        port=9880,
        allow_dirs=None,
        sidecar_python=None,
        sidecar_script=None,
        sidecar_port=9882,
        data_dir=None,
        log=print,
    ):
        self.server_path = server_path
        self.models_dir = models_dir
        self.device = device or "vulkan"
        self.port = int(port)
        self.allow_dirs = [d for d in (allow_dirs or []) if d]
        self.sidecar_python = sidecar_python or ""
        self.sidecar_script = sidecar_script or ""
        self.sidecar_port = int(sidecar_port)
        self.data_dir = data_dir or ""
        self.log = log
        self.sidecar = None
        self.server = None
        self._readers = []

    # ---- command lines -------------------------------------------------
    def _sidecar_enabled(self):
        return bool(self.sidecar_python and self.sidecar_script)

    def sidecar_cmd(self):
        if not self._sidecar_enabled():
            return None
        return [
            self.sidecar_python,
            self.sidecar_script,
            "--host",
            "127.0.0.1",
            "--port",
            str(self.sidecar_port),
            "--parent-pid",
            str(os.getpid()),
        ]

    def sidecar_env(self):
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        if self.data_dir:
            parent = os.path.dirname(os.path.abspath(self.data_dir))
            for key, sub in (
                ("GSV_G2PW_MODEL", "g2pw"),
                ("GSV_BERT_DIR", "bert_tokenizer"),
                ("NLTK_DATA", "nltk_data"),
                ("GSV_FAST_LANGDETECT", "fast_langdetect"),
            ):
                for base in (self.data_dir, parent):
                    path = os.path.abspath(os.path.join(base, sub))
                    if os.path.isdir(path):
                        env[key] = path
                        break
        return env

    def server_cmd(self):
        cmd = [
            self.server_path,
            "--models",
            self.models_dir,
            "--device",
            self.device,
            "--port",
            str(self.port),
            "--sidecar",
            "127.0.0.1:%d" % self.sidecar_port,
            "--parent-pid",
            str(os.getpid()),
        ]
        for d in self.allow_dirs:
            cmd += ["--allow-path", d]
        return cmd

    # ---- lifecycle -----------------------------------------------------
    def is_running(self):
        return bool(self.server and self.server.poll() is None)

    def start(self, timeout=180.0):
        if self.is_running():
            return True
        if not os.path.exists(self.server_path):
            raise FileNotFoundError(self.server_path)
        sc = self.sidecar_cmd()
        if sc:
            # A sidecar left over from a previous run is fine to reuse; spawning a second one
            # would fail to bind the port and leave the server without a sidecar.
            if self.sidecar_up():
                self.log("[gsv] reusing sidecar already listening on :%d" % self.sidecar_port)
            else:
                self.log("[gsv] sidecar: %s" % " ".join(sc))
                self.sidecar = _spawn(sc, env=self.sidecar_env())
                self._attach_reader(self.sidecar, "sidecar")
                if not self.wait_sidecar(min(120.0, timeout)):
                    self.log("[gsv] sidecar not ready, retrying once")
                    self.stop_sidecar()
                    time.sleep(1.5)
                    self.sidecar = _spawn(sc, env=self.sidecar_env())
                    self._attach_reader(self.sidecar, "sidecar")
                    if not self.wait_sidecar(min(120.0, timeout)):
                        self.log("[gsv] warning: sidecar did not become ready")
        self.log("[gsv] server: %s" % " ".join(self.server_cmd()))
        self.server = _spawn(self.server_cmd(), env=self.server_env())
        self._attach_reader(self.server, "server")
        return self.wait_health(timeout)

    def server_env(self):
        """ggml-vulkan reads GGML_VK_DISABLE_COOPMAT2 at static-init time; on NVIDIA the AR
        logits lose precision with coopmat2 (see the engine warning), and AR is cheap anyway."""
        env = os.environ.copy()
        if self.device.startswith("vulkan"):
            env.setdefault("GGML_VK_DISABLE_COOPMAT2", "1")
        return env

    def sidecar_up(self):
        url = "http://127.0.0.1:%d/health" % self.sidecar_port
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                return bool(json.loads(r.read().decode("utf-8")).get("ready"))
        except Exception:
            return False

    def stop_sidecar(self):
        if self.sidecar and self.sidecar.poll() is None:
            try:
                self.sidecar.terminate()
                self.sidecar.wait(timeout=5)
            except Exception:
                try:
                    self.sidecar.kill()
                except Exception:
                    pass
        self.sidecar = None

    def _attach_reader(self, proc, tag):
        def pump():
            try:
                for raw in iter(proc.stdout.readline, b""):
                    line = raw.decode("utf-8", "replace").rstrip()
                    if line:
                        self.log("[%s] %s" % (tag, line))
            except Exception:
                pass

        import threading

        t = threading.Thread(target=pump, daemon=True)
        t.start()
        self._readers.append(t)

    def health(self):
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:%d/health" % self.port, timeout=2
            ) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            return None

    def wait_health(self, timeout=180.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            h = self.health()
            if h and h.get("ok"):
                return True
            if self.server and self.server.poll() is not None:
                return False
            time.sleep(1.0)
        return False

    def wait_sidecar(self, timeout=60.0):
        deadline = time.time() + timeout
        url = "http://127.0.0.1:%d/health" % self.sidecar_port
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=2) as r:
                    if json.loads(r.read().decode("utf-8")).get("ready"):
                        return True
            except Exception:
                pass
            if self.sidecar and self.sidecar.poll() is not None:
                return False
            time.sleep(1.0)
        return False

    def stop(self):
        for proc in (self.server, self.sidecar):
            if not proc:
                continue
            if proc.poll() is not None:
                continue
            try:
                if IS_WIN:
                    proc.terminate()
                else:
                    proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception:
                pass
        self.server = None
        self.sidecar = None

    def tts(self, payload, timeout=600):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/tts" % self.port,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
