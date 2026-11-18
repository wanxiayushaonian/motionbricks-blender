"""Standard-library setup worker. Runs in a separate Python process or as a CLI."""

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import subprocess
import ssl
import sys
import time
from urllib.request import Request, urlopen

from backend_config import (
    CODE_LICENSE, FP16, FP32, MANAGED, MODEL_LICENSE, RELEASE, REVISION, RIG,
    UPSTREAM, executable, python_path,
)

PROTOCOL = 1


def write_json(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def publish(directory, message):
    write_json(directory / "status.json", {"message": message})


def run(arguments, cwd, environment, log):
    result = subprocess.run(arguments, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                            stdout=log, stderr=subprocess.STDOUT, check=False)
    log.flush()
    if result.returncode:
        with open(log.name, "rb") as reader:
            reader.seek(max(0, reader.seek(0, os.SEEK_END) - 1600))
            details = reader.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"命令 {Path(arguments[0]).name} 失败（{result.returncode}）。{details}")


@contextmanager
def install_lock(target):
    path = target.parent / f".{target.name}.setup.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            if path.stat().st_size == 0:
                lock.write(b"0")
                lock.flush()
                lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise ValueError("该目录已有安装任务，请等待完成或取消原任务。") from error
        else:
            import fcntl
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError("该目录已有安装任务，请等待完成或取消原任务。") from error
        yield


def digest(path):
    with path.open("rb") as stream:
        return sha256_stream(stream)


def sha256_stream(stream):
    checksum = sha256()
    while block := stream.read(4 * 1024 * 1024):
        checksum.update(block)
    return checksum.hexdigest()


def download_asset(repository, relative, git, directory, environment):
    pointer = subprocess.check_output(
        [git, "show", f"HEAD:{relative}"], cwd=repository, env=environment, text=True,
    )
    fields = dict(line.split(" ", 1) for line in pointer.splitlines() if " " in line)
    expected, size = fields["oid"].removeprefix("sha256:"), int(fields["size"])
    path = repository / relative
    if path.is_file() and path.stat().st_size == size and digest(path) == expected:
        return
    partial = path.with_suffix(path.suffix + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > size:
        partial.unlink()
        offset = 0
    if offset == size:
        if digest(partial) == expected:
            partial.replace(path)
            return
        partial.unlink()
        offset = 0
    url = f"https://media.githubusercontent.com/media/NVlabs/motionbricks/{REVISION}/{relative}"
    request = Request(url, headers={"User-Agent": "MotionBricks-Blender-Setup/0.10",
                                   **({"Range": f"bytes={offset}-"} if offset else {})})
    tls = ssl.create_default_context()
    if platform.system() == "Linux" and Path("/etc/ssl/certs").is_dir():
        # Blender's embedded Python can use a different OpenSSL certificate location.
        tls.load_verify_locations(capath="/etc/ssl/certs")
    publish(directory, f"连接官方下载源 · {path.name}…")
    with urlopen(request, timeout=60, context=tls) as response:
        resumed = offset and response.status == 206
        if resumed and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
            raise ValueError("下载服务器返回了不一致的断点，请重试。")
        downloaded = offset if resumed else 0
        last_update = 0
        with partial.open("ab" if resumed else "wb") as stream:
            while block := response.read(1024 * 1024):
                downloaded += len(block)
                if downloaded > size:
                    raise ValueError("资源大小超过官方 Git LFS 记录。")
                stream.write(block)
                if time.monotonic() - last_update > .4:
                    publish(directory, f"下载 {path.name} · {downloaded / size:.0%}")
                    last_update = time.monotonic()
    if downloaded != size:
        raise ValueError("下载尚未完成，请重新配置以继续下载。")
    publish(directory, f"校验 {path.name} 的 SHA-256…")
    if digest(partial) != expected:
        partial.unlink()
        raise ValueError("资源校验失败，请重试下载。")
    partial.replace(path)


def install(request, directory):
    if not request.get("accept_terms"):
        raise ValueError("请先阅读并接受上游代码与模型的许可条款。")
    if platform.system() not in {"Linux", "Windows"} or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise ValueError("当前安装流程面向 Linux / Windows x86-64，Linux 已验证。")
    git, uv = executable("git"), executable("uv")
    if not git or not uv:
        raise ValueError("请先安装 Git 和 uv，再重试。下载入口在插件的「后端配置」面板。")
    target = Path(request["repository"]).expanduser().resolve()
    environment = os.environ.copy()
    environment["GIT_LFS_SKIP_SMUDGE"] = "1"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment.pop("UV_PROJECT_ENVIRONMENT", None)
    environment.pop("UV_PYTHON", None)
    with install_lock(target):
        if target.exists() and any(target.iterdir()) and not (target / MANAGED).is_file():
            raise ValueError("目标目录已有文件。请选择空目录，或用「检查已有后端」连接它。")
        target.mkdir(parents=True, exist_ok=True)
        marker = target / MANAGED
        metadata = json.loads(marker.read_text()) if marker.exists() else {}
        if metadata and metadata.get("revision") != REVISION:
            raise ValueError("该目录由其他版本管理，请选择新的安装目录。")
        write_json(marker, {"revision": REVISION, "release": RELEASE, "state": "installing",
                            "code_license": CODE_LICENSE, "model_license": MODEL_LICENSE})
        with (target / ".motionbricks-setup.log").open("a", encoding="utf-8") as log:
            publish(directory, "获取官方 MotionBricks v0.2.0 源码…")
            if not (target / ".git").is_dir():
                run([git, "init", "--quiet", str(target)], target, environment, log)
            run([git, "-c", "credential.interactive=never", "fetch", "--depth=1", UPSTREAM, REVISION],
                target, environment, log)
            current = subprocess.check_output([git, "rev-parse", "--verify", "HEAD"],
                                              cwd=target, env=environment, text=True,
                                              stderr=subprocess.DEVNULL).strip() if (target / "pyproject.toml").exists() else ""
            if current and current != REVISION:
                raise ValueError("安装目录的 Git 版本已改变，请选择新的安装目录。")
            if not current:
                run([git, "-c", "advice.detachedHead=false", "checkout", "--detach", REVISION],
                    target, environment, log)
            publish(directory, "核对官方骨架与 SOMA 模型…")
            for relative in (RIG, FP16, *((FP32,) if request.get("fp32") else ())):
                download_asset(target, relative, git, directory, environment)
            publish(directory, "创建 Python 3.12 环境并安装锁定依赖，详情见安装日志…")
            run([uv, "sync", "--locked", "--python", "3.12"], target, environment, log)
            publish(directory, "检查 Python、GPU 和模型…")
            probe = subprocess.run(
                [str(python_path(target)), str(Path(__file__).with_name("backend_probe.py")), str(target)],
                cwd=target, env=environment, capture_output=True, text=True, encoding="utf-8", check=False,
            )
            if probe.returncode:
                raise RuntimeError("依赖检查失败。" + probe.stderr[-1200:])
            report = json.loads(probe.stdout.split("MOTIONBRICKS_PROBE=")[-1])
            if any(item["level"] == "ERROR" for item in report["checks"]):
                raise ValueError("\n".join(item["text"] for item in report["checks"] if item["level"] == "ERROR"))
            write_json(marker, {"revision": REVISION, "release": RELEASE, "state": "ready",
                                "code_license": CODE_LICENSE, "model_license": MODEL_LICENSE})
            return report


def main():
    directory = Path(sys.argv[1])
    try:
        (directory / "result.json").unlink(missing_ok=True)
        request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
        result = install(request, directory)
        write_json(directory / "result.json", {"protocol": PROTOCOL, "data": result})
    except Exception as error:
        write_json(directory / "result.json", {"protocol": PROTOCOL, "error": str(error)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
