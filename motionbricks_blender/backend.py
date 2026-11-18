"""Headless jobs run by the existing MotionBricks Python environment."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sys
import traceback

import numpy as np
import warp as wp

from motionbricks.animation.animation_buffer import create_animation_buffer_for_skeleton
from motionbricks.inference.builder import TensorRTBuildOptions
from motionbricks.io.bvh import load_bvh
from motionbricks.io.usd import load_skeleton_from_usd
from motionbricks.io.utils import SOMA_SKELETON_PRIM_PATH, get_asset_file, get_soma_human_model
from motionbricks.model.config import MotionBricksInferenceConfig
from motionbricks.model.contract import MODEL_POSES_PER_TOKEN, MODEL_SAMPLE_RATE
from motionbricks.model.soma.contract import SOMA_N1_CONTRACT, SOMA_N1_FP16_ONNX_PATH
from motionbricks.model.soma.engine import MotionBricksSOMAEngine
from motionbricks.utils.space_conversion_utils import FacingDirectionType, SpaceConverter

PROTOCOL = 1
DURATION_CONFIGS = {
    "AUTO": MotionBricksInferenceConfig(),
    "SHORT": MotionBricksInferenceConfig(allowed_token_counts=tuple(range(6, 9))),
    "LONG": MotionBricksInferenceConfig(allowed_token_counts=tuple(range(12, 17))),
}


def write_json(path, data):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    temporary.replace(path)


def load_soma():
    usd_path = get_asset_file("human", "soma_base_skel_minimal_no_root.usd")
    skeleton = load_skeleton_from_usd(usd_path, SOMA_SKELETON_PRIM_PATH)
    to_world = SpaceConverter(FacingDirectionType.MUJOCO).convert_transform(wp.transform_identity())
    rig = {
        "joints": skeleton.joint_names,
        "parents": skeleton.parent_indices.numpy().tolist(),
        "rest": skeleton.reference_local_transforms.numpy().tolist(),
        "to_world": list(to_world),
        "required_joints": list(SOMA_N1_CONTRACT.joint_names),
        "sample_rate": MODEL_SAMPLE_RATE,
    }
    rig["signature"] = sha256(json.dumps(rig, sort_keys=True).encode()).hexdigest()
    rig["usd_path"] = str(usd_path)
    return skeleton, to_world, rig


def read_clip(path, skeleton, to_world):
    path = Path(path)
    if not path.is_file() or path.suffix.lower() != ".bvh":
        raise ValueError("请选择有效的 SOMA BVH 动作文件。")
    source = load_bvh(path)
    missing = set(SOMA_N1_CONTRACT.joint_names) - set(source.skeleton.joint_names)
    if missing:
        raise ValueError("BVH 缺少 SOMA 关节：" + "、".join(sorted(missing)))
    clip = create_animation_buffer_for_skeleton(source, skeleton)
    duration = (clip.num_frames - 1) / clip.sample_rate
    times = np.arange(0.0, duration, 1.0 / MODEL_SAMPLE_RATE).tolist()
    if times and duration - times[-1] < 1.0e-8:
        times.pop()
    times.append(duration)
    poses = wp.empty((len(times), skeleton.num_joints), dtype=wp.transform)
    for i, time in enumerate(times):
        clip.sample(time, poses[i], root_offset=to_world)
    return {"poses": poses.numpy().tolist(), "times": times, "duration": duration, "source_fps": source.sample_rate}


def checked_poses(values, joints, minimum, maximum):
    poses = np.asarray(values, dtype=np.float32)
    if poses.ndim != 3 or poses.shape[1:] != (joints, 7) or not minimum <= len(poses) <= maximum:
        raise ValueError("姿势数组的帧数或关节布局不正确。")
    if not np.isfinite(poses).all():
        raise ValueError("姿势包含非有限数值。")
    rotation_norm = np.linalg.norm(poses[:, :, 3:], axis=2)
    if not np.allclose(rotation_norm, 1.0, atol=0.002):
        raise ValueError("姿势包含未归一化的旋转四元数。")
    return poses


def recording_assets(to_world):
    human = get_soma_human_model()
    mesh = human.skinned_meshes[0]
    return {
        "joint_names": human.skeleton.joint_names,
        "parents": human.skeleton.parent_indices.numpy().tolist(),
        "inverse_bind": human.inverse_bind_transforms.numpy().reshape(-1, 7).tolist(),
        "model_to_runtime": list(to_world),
        "points": mesh.points.numpy().tolist(),
        "faces": mesh.indices.numpy().reshape(-1, 3).tolist(),
        "joint_indices": mesh.joint_indices.numpy().reshape(mesh.num_points, -1).tolist(),
        "joint_weights": mesh.joint_weights.numpy().reshape(mesh.num_points, -1).tolist(),
    }


def generate(job, skeleton, to_world, report):
    count = MODEL_POSES_PER_TOKEN
    context_poses = checked_poses(job["context"], skeleton.num_joints, count, count)
    targets = [
        checked_poses(target, skeleton.num_joints, 1, count) for target in job["targets"]
    ]
    if not targets:
        raise ValueError("请至少添加一个目标姿势。")
    durations = job.get("durations", ["AUTO"] * len(targets))
    if len(durations) != len(targets) or any(duration not in DURATION_CONFIGS for duration in durations):
        raise ValueError("补间时长偏好与目标序列不匹配。")

    report("正在加载 SOMA FP16 推理引擎…")
    with MotionBricksSOMAEngine(
        SOMA_N1_FP16_ONNX_PATH,
        model_to_runtime=to_world,
        build_options=TensorRTBuildOptions(enable_fp16=True),
    ) as engine, wp.ScopedStream(engine.stream):
        context = engine.create_input(skeleton)
        target = engine.create_input(skeleton)
        output = engine.create_output(skeleton)
        segments = []
        ends = []
        frames = 0
        for index, (target_poses, duration) in enumerate(zip(targets, durations, strict=True)):
            report(f"正在生成第 {index + 1}/{len(targets)} 段…")
            context.update_from_poses(wp.array(context_poses, dtype=wp.transform), stream=engine.stream)
            target.update_from_poses(wp.array(target_poses, dtype=wp.transform), stream=engine.stream)
            engine.generate_animation(context, target, output, config=DURATION_CONFIGS[duration])
            valid = output.poses[:output.num_frames].numpy()
            context_poses = valid[-count:]
            # Consecutive segments share an endpoint.
            segment = valid[1:] if segments else valid
            segments.append(segment)
            frames += len(segment)
            ends.append(frames - 1)
    return {
        "poses": np.concatenate(segments).tolist(),
        "times": (np.arange(frames) / MODEL_SAMPLE_RATE).tolist(),
        "segment_ends": ends,
    }


def run(job, report):
    if job.get("protocol") != PROTOCOL:
        raise ValueError("任务协议版本不兼容。")
    command = job["command"]
    device = "cuda:0" if command in {"generate", "route"} else "cpu"
    with wp.ScopedDevice(device):
        skeleton, to_world, rig = load_soma()
        if command == "describe":
            # Match the bind poses used by MotionBricks' linear-blend skinning.
            # The skeleton's reference pose is a separate set of local transforms.
            mesh = get_soma_human_model()
            rig["joint_binds"] = [
                list(wp.mul(to_world, wp.transform_inverse(wp.transform(*row))))
                for row in mesh.inverse_bind_transforms.numpy()
            ]
            return {"rig": rig}
        if job.get("signature") != rig["signature"]:
            raise ValueError("SOMA 骨架与当前模型不匹配，请重新创建参考角色。")
        if command == "clip":
            report("正在读取 BVH 动作…")
            return read_clip(job["path"], skeleton, to_world)
        if command == "recording_assets":
            report("正在读取 SOMA 网格与蒙皮…")
            return recording_assets(to_world)
        if command == "generate":
            return generate(job, skeleton, to_world, report)
        if command == "route":
            from routes import validate_course
            from route_walker import generate as generate_route

            return generate_route(validate_course(job["course"]), skeleton, to_world, report)
    raise ValueError(f"未知任务类型：{command}")


def main(directory):
    path = Path(directory)
    try:
        job = json.loads((path / "request.json").read_text(encoding="utf-8"))
        result = run(job, lambda message: write_json(path / "status.json", {"message": message}))
        write_json(path / "result.json", {"protocol": PROTOCOL, "data": result})
        return 0
    except Exception as error:
        traceback.print_exc()
        write_json(path / "result.json", {"protocol": PROTOCOL, "error": str(error)})
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
