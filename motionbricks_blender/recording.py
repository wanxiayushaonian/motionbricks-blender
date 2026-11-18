"""Validate the MotionBricks Blender recording format before creating any Action."""

from dataclasses import dataclass
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import BadZipFile, ZipFile

import bpy
import numpy as np

from .rig import RIG_DATA, RIG_REVISION, RigAdapter, matrix_from_pose, set_frame

FORMAT = "motionbricks-blender-npz"
VERSION = 1
MAX_ARCHIVE_BYTES = 192 * 1024 * 1024
MAX_FRAMES = 12_000


@dataclass(frozen=True)
class Recording:
    poses: np.ndarray
    fps: int

    @property
    def duration(self):
        return (len(self.poses) - 1) / self.fps


def transforms(data, shape, label):
    if data.shape != shape or data.dtype.kind != "f" or not np.isfinite(data).all():
        raise ValueError(f"录制中的 {label} 形状或数值无效。")
    if not np.allclose(np.linalg.norm(data[..., 3:], axis=-1), 1.0, atol=0.002, rtol=0.0):
        raise ValueError(f"录制中的 {label} 含有无效四元数。")
    return data


def joint_order(names, parents, definition):
    expected = definition["joints"]
    if names.shape != (len(expected),) or names.dtype.kind != "U":
        raise ValueError("录制的关节名称与当前 SOMA 不兼容。")
    names = names.tolist()
    if len(set(names)) != len(names) or set(names) != set(expected):
        raise ValueError("录制的关节名称与当前 SOMA 不兼容。")
    if parents.shape != (len(names),) or parents.dtype.kind != "i":
        raise ValueError("录制的 SOMA 关节层级无效。")
    indices = {name: index for index, name in enumerate(names)}
    order = [indices[name] for name in expected]
    wanted_parents = [-1 if parent < 0 else order[parent] for parent in definition["parents"]]
    if parents[order].tolist() != wanted_parents:
        raise ValueError("录制的关节层级与当前 SOMA 不兼容。")
    return order


def read_arrays(file, definition):
    format_id, version, fps = (file[key] for key in ("format", "format_version", "fps"))
    if (format_id.shape != () or format_id.item() != FORMAT
            or version.shape != () or version.dtype.kind not in "iu" or version.item() != VERSION):
        raise ValueError("请选择 MotionBricks Blender NPZ v1 录制。")
    if fps.shape != () or fps.dtype.kind not in "iu" or not 1 <= fps.item() <= 240:
        raise ValueError("录制帧率必须为 1 至 240 之间的整数。")

    joints = len(definition["joints"])
    order = joint_order(file["joint_names"], file["parents"], definition)
    runtime = matrix_from_pose(transforms(file["model_to_runtime"], (7,), "坐标变换"))
    expected_runtime = matrix_from_pose(definition["to_world"])
    if not np.allclose(np.asarray(runtime), np.asarray(expected_runtime), atol=0.0002, rtol=0.0):
        raise ValueError("录制的坐标系与当前 SOMA 不兼容。")
    inverse_binds = transforms(file["inverse_bind"], (joints, 7), "绑定矩阵")
    for row, expected in zip(inverse_binds[order], definition["joint_binds"], strict=True):
        found = runtime @ matrix_from_pose(row).inverted()
        if not np.allclose(np.asarray(found), np.asarray(matrix_from_pose(expected)), atol=0.0002, rtol=0.0):
            raise ValueError("录制的绑定矩阵与当前 SOMA 不兼容。")

    poses = file["poses"]
    if poses.ndim != 3 or not 1 <= len(poses) <= MAX_FRAMES:
        raise ValueError(f"录制须包含 1 至 {MAX_FRAMES} 帧姿势。")
    transforms(poses, (len(poses), joints, 7), "姿势数组")
    if order != list(range(joints)):
        poses = poses[:, order, :]
    return Recording(poses, int(fps.item()))


def load_recording(path, definition):
    if path.suffix.lower() != ".npz" or not path.is_file():
        raise ValueError("请选择有效的 MotionBricks .npz 文件。")
    try:
        with path.open("rb") as stream:
            with ZipFile(stream) as archive:
                members = archive.infolist()
                if len(members) > 64 or sum(item.file_size for item in members) > MAX_ARCHIVE_BYTES:
                    raise ValueError("录制解压后过大，请分段导入。")
                if len({item.filename for item in members}) != len(members):
                    raise ValueError("录制包含重复的数据项。")
            stream.seek(0)
            with np.load(stream, allow_pickle=False) as file:
                return read_arrays(file, definition)
    except (BadZipFile, EOFError, KeyError, TypeError) as error:
        raise ValueError("录制文件损坏或缺少必要的 NPZ 数据。") from error


def sample_action(context, adapter, action, frames):
    rig = adapter.rig
    stage = bpy.data.scenes.new("MotionBricks Export")
    clone = None
    try:
        clone = bpy.data.objects.new("MotionBricks Export Rig", rig.data)
        stage.collection.objects.link(clone)
        clone.matrix_world = rig.matrix_world
        clone[RIG_DATA] = rig[RIG_DATA]
        clone[RIG_REVISION] = rig[RIG_REVISION]
        layer = stage.view_layers[0]
        poses = np.empty((len(frames), len(adapter.bones), 7), dtype=np.float32)
        with context.temp_override(scene=stage, view_layer=layer):
            layer.update()
            staged = RigAdapter(clone)
            staged.assign_action(action)
            for index, frame in enumerate(frames):
                set_frame(stage, float(frame))
                poses[index] = staged.capture()
        return poses
    finally:
        if clone is not None:
            bpy.data.objects.remove(clone, do_unlink=True)
        bpy.data.scenes.remove(stage)


def sampling_frames(action, scene_fps, fps):
    if not len(action.slots) or action.is_empty:
        raise ValueError("选中的 Action 没有可导出的动画。")
    first, last = action.frame_range
    intervals = max(1, round((last - first) / scene_fps * fps)) if last > first else 0
    if intervals + 1 > MAX_FRAMES:
        raise ValueError(f"导出超过 {MAX_FRAMES} 帧，请降低导出帧率。")
    return np.linspace(first, last, intervals + 1)


def save_recording(path, context, adapter, action, scene_fps, fps, assets):
    frames = sampling_frames(action, scene_fps, fps)
    metadata = {
        "action": action.name,
        "source_frame_range": [float(frames[0]), float(frames[-1])],
        "source_scene_fps": scene_fps,
    }
    data = {
        "format": np.array(FORMAT),
        "format_version": np.int32(VERSION),
        "fps": np.int32(fps),
        "source": np.array(json.dumps(metadata, ensure_ascii=False)),
        "poses": sample_action(context, adapter, action, frames),
        "joint_names": np.asarray(assets["joint_names"], dtype=str),
        "parents": np.asarray(assets["parents"], dtype=np.int32),
        "inverse_bind": np.asarray(assets["inverse_bind"], dtype=np.float32),
        "model_to_runtime": np.asarray(assets["model_to_runtime"], dtype=np.float32),
        "points": np.asarray(assets["points"], dtype=np.float32),
        "faces": np.asarray(assets["faces"], dtype=np.int32),
        "joint_indices": np.asarray(assets["joint_indices"], dtype=np.int32),
        "joint_weights": np.asarray(assets["joint_weights"], dtype=np.float32),
    }
    read_arrays(data, json.loads(adapter.rig[RIG_DATA]))
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, suffix=".npz", delete=False) as stream:
            temporary = Path(stream.name)
            np.savez_compressed(stream, **data)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return len(frames)
