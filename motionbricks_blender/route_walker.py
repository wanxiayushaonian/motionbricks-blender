# SPDX-FileCopyrightText: 2026 hyun2xyz
# SPDX-License-Identifier: Apache-2.0
# Adapted from walker/mb_walk.py for the MotionBricks Blender worker.

"""Generate SOMA locomotion along ordered ground-plane waypoints."""

import math

import numpy as np
import warp as wp
from scipy.spatial.transform import Rotation as R

from motionbricks.inference.builder import TensorRTBuildOptions
from motionbricks.io.utils import get_soma_human_model
from motionbricks.model.contract import MODEL_POSES_PER_TOKEN, MODEL_SAMPLE_RATE
from motionbricks.model.soma.contract import SOMA_N1_FP16_ONNX_PATH, SOMA_N1_FP32_ONNX_PATH
from motionbricks.model.soma.engine import MotionBricksSOMAEngine
from motionbricks.runtime.motion_player import GeneratedMotionPlayer
from motionbricks.runtime.target_pose import TargetPoseInferenceParams, adjust_target_pose_speed, place_target_poses

FPS = MODEL_SAMPLE_RATE
FORWARD = np.array([0.0, 1.0, 0.0])  # hips-local axis that points where the body faces
LEAD_SECONDS = 1.0  # how far ahead the moving target is placed
TRANSITION_EARLY_BY = 4
TURN_THRESHOLD = 15.0  # degrees of steering change that trigger a new generation
ARRIVE = 0.7  # metres; a waypoint counts as reached inside this radius


def fk(local, parents):
    """World positions and rotations of every joint for local transforms [J,7] (x y z qx qy qz qw)."""
    rot, pos = [None] * len(parents), np.zeros((len(parents), 3))
    for j, p in enumerate(parents):
        r = R.from_quat(local[j, 3:7])
        if p < 0:
            rot[j], pos[j] = r, local[j, :3]
        else:
            rot[j], pos[j] = rot[p] * r, pos[p] + rot[p].apply(local[j, :3])
    return pos, rot


def aim_joint(local, parents, names, joint, child, direction):
    """Rotate `joint` so the bone from it to `child` points along `direction` (world space)."""
    j, c = names.index(joint), names.index(child)
    pos, rot = fk(local, parents)
    d0 = (pos[c] - pos[j]) / np.linalg.norm(pos[c] - pos[j])
    d1 = np.asarray(direction, float) / np.linalg.norm(direction)
    axis = np.cross(d0, d1)
    s = np.linalg.norm(axis)
    if s < 1e-8:
        return
    delta = R.from_rotvec(axis / s * math.atan2(s, float(np.dot(d0, d1))))
    local[j, 3:7] = (rot[parents[j]].inv() * delta * rot[j]).as_quat()


def make_stand(bind, parents, names, m2r):
    """The bind T-pose in runtime space, with the arms lowered to the sides."""
    pose = bind.astype(np.float64).copy()
    q = R.from_quat(m2r.q)
    pose[0, :3] = q.apply(pose[0, :3]) + np.array(m2r.p)
    pose[0, 3:7] = (q * R.from_quat(pose[0, 3:7])).as_quat()
    for side, sx in (("Left", 1.0), ("Right", -1.0)):
        aim_joint(pose, parents, names, f"{side}Arm", f"{side}ForeArm", [0.16 * sx, -0.04, -1.0])
        aim_joint(pose, parents, names, f"{side}ForeArm", f"{side}Hand", [0.10 * sx, -0.30, -1.0])
    return pose


def place(pose, x, y, yaw_deg):
    """Move a pose [J,7] so its root stands at (x, y) facing yaw (0 = -Y, 90 = +X)."""
    out = pose.copy()
    yaw0 = math.atan2(*R.from_quat(pose[0, 3:7]).apply(FORWARD)[[1, 0]])
    want = math.atan2(-math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg)))
    rz = R.from_euler("z", want - yaw0)
    out[0, :3] = rz.apply(pose[0, :3] * [0.0, 0.0, 1.0]) + [x, y, 0.0]
    out[0, 3:7] = (rz * R.from_quat(pose[0, 3:7])).as_quat()
    return out


def canonical(window):
    """Move a [4,J,7] window so its first root is at the ground origin and it travels along +X."""
    out = window.copy()
    roots = window[:, 0, :3]
    off = roots[-1, :2] - roots[0, :2]
    rz = R.from_euler("z", -math.atan2(off[1], off[0]))
    origin = np.array([roots[0, 0], roots[0, 1], 0.0])
    for k in range(len(window)):
        out[k, 0, :3] = rz.apply(window[k, 0, :3] - origin)
        out[k, 0, 3:7] = (rz * R.from_quat(window[k, 0, 3:7])).as_quat()
    return out


def fastest_window(motion, lo, hi):
    """Four consecutive frames with the highest ground speed, from the [lo, hi] part of motion."""
    a, b = int(len(motion) * lo), int(len(motion) * hi) - MODEL_POSES_PER_TOKEN
    speed = [np.linalg.norm(motion[i + 3, 0, :2] - motion[i, 0, :2]) for i in range(a, b)]
    if not speed:
        raise RuntimeError("行走样本太短，无法提取步态。")
    i = a + int(np.argmax(speed))
    return motion[i:i + MODEL_POSES_PER_TOKEN].copy()


def to_wp(a):
    return wp.array(a.astype(np.float32), dtype=wp.transform)


class Walker:
    def __init__(self, engine, skeleton, stand):
        self.engine, self.sk, self.stand = engine, skeleton, stand
        self.settings = TargetPoseInferenceParams(transition_early_by_frames=TRANSITION_EARLY_BY, num_poses_to_use=4)

    def first_steps(self, distance):
        """Stand at the origin facing +X, generate once toward a stand `distance` ahead."""
        engine = self.engine
        ctx = np.repeat(place(self.stand, 0.0, 0.0, 90.0)[None], MODEL_POSES_PER_TOKEN, axis=0)
        tgt = np.repeat(place(self.stand, distance, 0.0, 90.0)[None], MODEL_POSES_PER_TOKEN, axis=0)
        context, target, output = engine.create_input(self.sk), engine.create_input(self.sk), engine.create_output(self.sk)
        context.update_from_poses(to_wp(ctx), stream=engine.stream)
        target.update_from_poses(to_wp(tgt), stream=engine.stream)
        engine.generate_animation(context, target, output)
        engine.synchronize()
        n = output.num_frames
        return output.poses[:n].numpy().reshape(n, -1, 7).astype(np.float64)

    def walk(self, template, speed, start, waypoints, end_yaw=None, *, report=None):
        """Walk from start through the waypoints and stop at the last one. Returns poses at 30 fps."""
        engine = self.engine
        tmpl = to_wp(canonical(template))
        adjust_target_pose_speed(tmpl, speed, stream=engine.stream)
        target_poses = wp.empty_like(tmpl)
        player = GeneratedMotionPlayer(engine, self.sk)
        current = to_wp(place(self.stand, *start))
        player.seed_history_from_pose(current)
        contacts = wp.zeros(4, dtype=wp.float32)
        dt = 1.0 / FPS
        shown = [current.numpy().reshape(-1, 7).copy()]
        steer, wi, stop_left = None, 0, None
        last_report = -1
        for _ in range(int(120 * FPS)):
            pos = current.numpy().reshape(-1, 7)[0, :3] * [1.0, 1.0, 0.0]
            goal = np.array([*waypoints[wi], 0.0])
            if wi < len(waypoints) - 1 and np.linalg.norm(goal - pos) < ARRIVE:
                wi += 1
                goal = np.array([*waypoints[wi], 0.0])
            if report is not None and wi != last_report:
                report(f"正在生成路线行走 · 路点 {wi + 1}/{len(waypoints)}")
                last_report = wi
            dist = float(np.linalg.norm(goal - pos))
            direction = ((goal - pos) / max(dist, 1e-6)).astype(np.float32)
            if stop_left is None and wi == len(waypoints) - 1 and dist < speed * LEAD_SECONDS * 1.2:
                yaw = end_yaw if end_yaw is not None else math.degrees(math.atan2(direction[0], -direction[1]))
                stop = to_wp(place(self.stand, goal[0], goal[1], yaw)[None])
                player.generate(stop, TargetPoseInferenceParams(transition_early_by_frames=0, num_poses_to_use=1))
                stop_left = player.time_to_transition
            elif stop_left is None:
                turned = steer is not None and math.degrees(
                    math.acos(max(-1.0, min(1.0, float(direction @ steer))))) > TURN_THRESHOLD
                if steer is None or turned or player.time_to_transition <= dt:
                    ground = pos + direction * speed * LEAD_SECONDS
                    place_target_poses(wp.vec3(*ground), wp.vec3(*direction), tmpl, target_poses, stream=engine.stream)
                    player.generate(target_poses, self.settings)
                    steer = direction
            player.sample(dt, current, contacts)
            player.record_pose(dt, current)
            shown.append(current.numpy().reshape(-1, 7).astype(np.float64))
            if stop_left is not None:
                stop_left -= dt
                if stop_left <= 1e-8:
                    engine.synchronize()
                    frames = player.output.poses[:player.output.num_frames].numpy()
                    tail = frames[-5:, 0, :2]
                    stop_speed = float(np.linalg.norm(np.diff(tail, axis=0), axis=1).mean() * FPS)
                    return np.stack(shown), stop_speed
        raise RuntimeError(f"120 秒内未能完成路线，已到路点 {wi + 1}/{len(waypoints)}。")


def generate(course, skeleton, to_world, report):
    fp16 = course["precision"] == "FP16"
    speed = course["speed"]
    waypoints = course["waypoints"]
    names = skeleton.joint_names
    rest = skeleton.reference_local_transforms.numpy().reshape(-1, 7)
    stand = make_stand(rest, skeleton.parent_indices.numpy(), names, to_world)
    arms = [i for i, name in enumerate(names) if name.startswith(("Left", "Right"))
            and any(part in name for part in ("Shoulder", "Arm", "Hand"))]

    def template_from(motion, lo, hi):
        template = fastest_window(motion, lo, hi)
        template[:, arms, 3:7] = stand[arms, 3:7]
        return template

    report(f"正在加载 SOMA {course['precision']} 推理引擎…")
    model = SOMA_N1_FP16_ONNX_PATH if fp16 else SOMA_N1_FP32_ONNX_PATH
    with MotionBricksSOMAEngine(
        model, model_to_runtime=to_world, build_options=TensorRTBuildOptions(enable_fp16=fp16),
    ) as engine, wp.ScopedStream(engine.stream):
        walker = Walker(engine, skeleton, stand)
        report("正在生成步态样本…")
        template = template_from(walker.first_steps(1.3), 0.25, 0.75)
        for index in range(2):
            report(f"正在调整步态 · {index + 1}/2")
            straight, _ = walker.walk(template, speed, (0.0, 0.0, 90.0), [(10.0, 0.0)])
            template = template_from(straight, 0.35, 0.65)
        motion, stop_speed = walker.walk(template, speed, course["start"], waypoints, course["end_yaw"], report=report)
        human = get_soma_human_model()
        override = human.override_poses["RelaxedHands"].numpy().reshape(len(names), 7)
        fingers = human.override_pose_masks["Fingers"].numpy() > 0.5
        motion[:, fingers] = override[fingers]

    end_error = float(np.linalg.norm(motion[-1, 0, :2] - waypoints[-1]))
    motion[:, 0, 2] += course["floor_z"]
    hold = round(course["hold"] * FPS)
    motion = np.concatenate([np.repeat(motion[:1], hold, 0), motion, np.repeat(motion[-1:], hold, 0)])
    return {
        "poses": motion.tolist(), "times": (np.arange(len(motion)) / FPS).tolist(),
        "end_error": end_error, "stop_speed": stop_speed,
    }

