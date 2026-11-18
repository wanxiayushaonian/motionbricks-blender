"""Shared backend paths and the exact upstream revision used by the installer."""

import os
from pathlib import Path
import shutil

UPSTREAM = "https://github.com/NVlabs/motionbricks.git"
REVISION = "54fc563aeaaf819a753d72cc9fdc6713989cdd43"
RELEASE = "v0.2.0"
MODEL_LICENSE = "https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-agreement/"
CODE_LICENSE = f"https://github.com/NVlabs/motionbricks/blob/{REVISION}/LICENSE"
FP16 = "motionbricks/assets/onnx/soma/mmm_v6_soma_fp16.onnx"
FP32 = "motionbricks/assets/onnx/soma/mmm_v6_soma_fp32.onnx"
RIG = "motionbricks/assets/human/soma_base_skel_minimal_no_root.usd"
MANAGED = ".motionbricks-managed.json"


def python_path(repository):
    environment = Path(repository) / ".venv"
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def default_install_path():
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return str(base / "motionbricks-blender/backends" / RELEASE)


def executable(name):
    found = shutil.which(name)
    if found:
        return found
    suffix = ".exe" if os.name == "nt" else ""
    for directory in (Path.home() / ".local/bin", Path.home() / ".cargo/bin"):
        candidate = directory / (name + suffix)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return ""
