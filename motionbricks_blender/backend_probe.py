"""Inspect an existing backend without loading Blender or constructing a TensorRT engine."""

import importlib
import json
from pathlib import Path
import platform
import subprocess
import sys

from backend_config import FP16, FP32, RELEASE, REVISION, RIG, executable


def probe(repository):
    checks = []
    def add(level, text):
        checks.append({"level": level, "text": text})

    add("OK" if sys.version_info >= (3, 12) else "ERROR",
        f"Python {platform.python_version()} · {sys.executable}")
    add("OK" if platform.system() == "Linux" else "WARN",
        f"平台 {platform.system()} {platform.machine()} · Linux x86-64 已验证，Windows 待实测")
    for name in ("numpy", "warp", "pxr", "tensorrt", "motionbricks"):
        try:
            module = importlib.import_module(name)
            if name == "motionbricks":
                version = getattr(module, "__version__", "?")
                correct = Path(module.__file__).resolve().is_relative_to(repository)
                add("OK" if correct and version == RELEASE.removeprefix("v") else "ERROR",
                    f"MotionBricks {version} · {module.__file__}")
            else:
                add("OK", f"{name} 可导入")
        except Exception as error:
            add("ERROR", f"{name} 无法导入 · {error}")
    try:
        import warp as wp
        wp.init()
        devices = wp.get_cuda_devices()
        add("OK" if devices else "ERROR", "NVIDIA GPU · " + ", ".join(d.name for d in devices)
            if devices else "未检测到 CUDA GPU，补间和路线生成需要 NVIDIA GPU。")
    except Exception as error:
        add("ERROR", f"CUDA 检查失败 · {error}")

    for relative in (RIG, FP16, FP32):
        path = repository / relative
        optional = relative == FP32
        if not path.is_file():
            add("WARN" if optional else "ERROR", f"{path.name} 缺失" + ("，FP32 路线不可用" if optional else ""))
            continue
        with path.open("rb") as stream:
            pointer = stream.read(128).startswith(b"version https://git-lfs.github.com/spec/v1")
        add("WARN" if optional and pointer else "ERROR" if pointer else "OK",
            f"{path.name} " + ("仍是 Git LFS 指针，请下载实际文件。" if pointer else f"{path.stat().st_size / 1024**2:.1f} MiB"))

    git = executable("git")
    if git and (repository / ".git").exists():
        result = subprocess.run([git, "rev-parse", "HEAD"], cwd=repository,
                                capture_output=True, text=True, check=False)
        revision = result.stdout.strip()
        add("OK" if revision == REVISION else "WARN",
            f"源码版本 {revision or '无法读取'} · 已验证 {REVISION[:12]}")
        changes = subprocess.run(
            [git, "diff", "--name-only", "HEAD", "--", ":(glob)**/*.py", "pyproject.toml", "uv.lock"],
            cwd=repository, capture_output=True, text=True, check=False,
        )
        if changes.stdout.strip():
            add("WARN", "后端代码或依赖文件有本地修改，请核对与已验证版本的兼容性。")
    else:
        add("WARN", "后端没有 Git 版本记录，当前目录的修改情况无法核验。")
    return {"repository": str(repository), "checks": checks}


if __name__ == "__main__":
    directory = Path(sys.argv[1]).resolve()
    if (directory / "request.json").is_file():
        try:
            request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
            report = probe(Path(request["repository"]).resolve())
            result = {"protocol": 1, "data": report}
        except Exception as error:
            result = {"protocol": 1, "error": str(error)}
        (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    else:
        print("MOTIONBRICKS_PROBE=" + json.dumps(probe(directory), ensure_ascii=False))
