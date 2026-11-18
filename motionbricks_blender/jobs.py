"""Run a single local backend job without importing its GPU dependencies in Blender."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import signal
from tempfile import TemporaryDirectory

from .backend_config import MANAGED, default_install_path, python_path

PROTOCOL = 1


def default_backend():
    path = Path(__file__).resolve().parents[2] / "motionbricks-main"
    if python_path(path).is_file():
        return str(path)
    path = Path(default_install_path())
    try:
        ready = json.loads((path / MANAGED).read_text(encoding="utf-8")).get("state") == "ready"
    except (OSError, ValueError):
        ready = False
    return str(path) if ready and python_path(path).is_file() else ""


def child_environment():
    environment = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        environment.pop(key, None)
    return environment


class WorkerJob:
    def __init__(self, python, worker, request, cwd):
        self.temp = TemporaryDirectory(prefix="motionbricks-blender-")
        self.path = Path(self.temp.name)
        try:
            (self.path / "request.json").write_text(
                json.dumps({"protocol": PROTOCOL, **request}, ensure_ascii=False, allow_nan=False),
                encoding="utf-8",
            )
            environment = child_environment()
            with (self.path / "worker.log").open("wb") as log:
                self.process = subprocess.Popen(
                    [str(python), "-u", str(worker), str(self.path)],
                    cwd=cwd, env=environment, start_new_session=os.name != "nt",
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                )
        except BaseException:
            self.temp.cleanup()
            raise

    def finished(self):
        return self.process.poll() is not None

    def status(self):
        try:
            data = json.loads((self.path / "status.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            return "正在启动本地进程…"
        return data["message"]

    def result(self):
        return_code = self.process.poll()
        if return_code is None:
            raise RuntimeError("推理进程仍在运行。")
        path = self.path / "result.json"
        if path.is_file():
            result = json.loads(path.read_text(encoding="utf-8"))
            if result.get("protocol") != PROTOCOL:
                raise RuntimeError("MotionBricks 返回了不兼容的任务结果。")
            if result.get("error"):
                raise RuntimeError(result["error"])
            if return_code == 0:
                return result["data"]
        with (self.path / "worker.log").open("rb") as log:
            log.seek(max(0, log.seek(0, os.SEEK_END) - 1600))
            details = log.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"MotionBricks 进程退出（{return_code}）。{details}")

    def close(self):
        if not self.finished():
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            else:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self.process.wait()
        self.temp.cleanup()


class BackendJob(WorkerJob):
    def __init__(self, repository, request):
        repository = Path(repository).expanduser().resolve()
        python = python_path(repository)
        if not python.is_file() or not (repository / "motionbricks/assets/onnx").is_dir():
            raise ValueError("请在「后端配置」连接已有环境或配置新的 MotionBricks 后端。")
        super().__init__(python, Path(__file__).with_name("backend.py"), request, repository)
