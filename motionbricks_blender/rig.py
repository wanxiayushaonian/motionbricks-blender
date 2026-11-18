"""Convert between the imported Blender rig and MotionBricks SOMA poses."""

from __future__ import annotations

from array import array
from functools import lru_cache
from hashlib import sha256
import json
import math

import bpy
from mathutils import Matrix, Quaternion, Vector

RIG_DATA = "motionbricks_soma_v1"
POSE_DATA = "motionbricks_pose_v1"
RIG_REVISION = "motionbricks_bind_revision"
CURRENT_BIND_REVISION = 2


def matrix_from_pose(pose):
    x, y, z, qx, qy, qz, qw = pose
    return Matrix.LocRotScale(Vector((x, y, z)), Quaternion((qw, qx, qy, qz)), None)


def pose_from_matrix(matrix):
    position, rotation, scale = matrix.decompose()
    if any(abs(value - 1.0) > 0.001 for value in scale):
        raise ValueError("姿势含有缩放。请使用参考角色的原始比例，并重置骨骼缩放。")
    return [*position, rotation.x, rotation.y, rotation.z, rotation.w]


def world_matrices(poses, parents):
    matrices = []
    for index, (pose, parent) in enumerate(zip(poses, parents, strict=True)):
        local = matrix_from_pose(pose)
        if parent >= index:
            raise ValueError("SOMA 骨架关节顺序无效。")
        matrices.append(local if parent < 0 else matrices[parent] @ local)
    return matrices


def rest_signature(bones):
    values = [(bone.name, bone.parent.name if bone.parent else None,
               [[round(value, 6) for value in row] for row in bone.matrix_local]) for bone in bones]
    return sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def align_bone_tails(rig, definition):
    names = definition["joints"]
    children = [[] for _ in names]
    for index, parent in enumerate(definition["parents"]):
        if parent >= 0:
            children[parent].append(index)
    preferred = {
        "Hips": "Spine1",
        "Chest": "Neck1",
        "Head": "HeadEnd",
        "LeftHand": "LeftHandMiddle1",
        "RightHand": "RightHandMiddle1",
    }
    to_armature = rig.matrix_world.inverted().to_3x3()
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        bones = rig.data.edit_bones
        # Heads must stay at their original bind positions. A connected bone
        # would have its head moved when its parent's tail is moved.
        for bone in bones:
            bone.use_connect = False
        for index, name in enumerate(names):
            bone = bones[name]
            candidates = children[index]
            direction = None
            if candidates:
                child = next((i for i in candidates if names[i] == preferred.get(name)), candidates[0])
                local_direction = Vector(definition["rest"][child][:3])
                bind_rotation = matrix_from_pose(definition["joint_binds"][index]).to_3x3()
                direction = to_armature @ bind_rotation @ local_direction
            if direction is None or direction.length_squared < 1e-8:
                # End joints mark a point and need only a short handle.
                handle = rig.matrix_world.to_3x3() @ (bone.tail - bone.head)
                direction = to_armature @ (handle.normalized() * 0.012)
                if direction.length_squared < 1e-8:
                    direction = to_armature @ Vector((0.0, 0.0, 0.012))
            roll_axis = bone.z_axis.copy()
            if abs(roll_axis.dot(direction.normalized())) > 0.95:
                roll_axis = bone.x_axis.copy()
            bone.tail = bone.head + direction
            bone.align_roll(roll_axis)
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")


def create_reference_rig(context, definition):
    if context.mode != "OBJECT":
        raise ValueError("请先切换到物体模式，再创建 SOMA 参考角色。")
    existing = set(bpy.data.objects)
    result = bpy.ops.wm.usd_import(
        filepath=definition["usd_path"], import_skeletons=True,
        import_materials=False, create_collection=True, apply_unit_conversion_scale=True,
        set_frame_range=False,
    )
    imported = [obj for obj in bpy.data.objects if obj not in existing]
    armatures = [obj for obj in imported if obj.type == "ARMATURE"]
    try:
        if "FINISHED" not in result or len(armatures) != 1:
            raise ValueError("参考 USD 必须导入一套 SOMA 骨架。")
        rig = armatures[0]
        rig.name = "MotionBricks SOMA"
        context.view_layer.update()
        rig.animation_data_clear()
        for obj in context.selected_objects:
            obj.select_set(False)
        rig.select_set(True)
        context.view_layer.objects.active = rig
        align_bone_tails(rig, definition)
        rig.data.display_type = "STICK"
        rig.show_in_front = True
        for bone in rig.pose.bones:
            bone.matrix_basis = Matrix.Identity(4)
            bone.rotation_mode = "QUATERNION"

        definition = dict(definition)
        definition.pop("usd_path", None)
        definition["bind_world"] = [list(row) for row in rig.matrix_world]
        definition["blender_rest"] = rest_signature(rig.data.bones)
        rig[RIG_DATA] = json.dumps(definition, separators=(",", ":"))
        rig[RIG_REVISION] = CURRENT_BIND_REVISION
        adapter = RigAdapter(rig)
        rest = list(definition["rest"])
        rest[0] = pose_from_matrix(matrix_from_pose(definition["to_world"]) @ matrix_from_pose(rest[0]))
        adapter.apply_pose(rest)
        context.view_layer.update()
        return rig
    except Exception:
        for obj in imported:
            bpy.data.objects.remove(obj, do_unlink=True)
        raise


class RigAdapter:
    """Map editable Blender bones to the joint transforms used by SOMA skinning."""

    def __init__(self, rig):
        if rig is None or rig.type != "ARMATURE" or RIG_DATA not in rig:
            raise ValueError("请先创建并选择 MotionBricks SOMA 参考角色。")
        if rig.get(RIG_REVISION) != CURRENT_BIND_REVISION:
            raise ValueError("这是旧版参考角色。请新建 SOMA 参考角色，再应用已有姿势或重新生成。")
        self.rig = rig
        definition = json.loads(rig[RIG_DATA])
        self.signature = definition["signature"]
        self.sample_rate = definition["sample_rate"]
        self.parents = definition["parents"]
        self.bones = [rig.pose.bones.get(name) for name in definition["joints"]]
        if any(bone is None for bone in self.bones) or rest_signature(rig.data.bones) != definition["blender_rest"]:
            raise ValueError("参考角色的骨架结构或静止姿势已改变，请重新创建参考角色。")
        self.bind_world = Matrix(definition["bind_world"])
        pose_from_matrix(rig.matrix_world @ self.bind_world.inverted())
        self.layout_signature = definition["blender_rest"]
        # The rest change of basis cancels out in Blender's armature modifier:
        # B_pose * inverse(B_bind) equals SOMA_pose * inverse(SOMA_bind).
        self.offsets = [
            matrix_from_pose(bind).inverted() @ self.bind_world @ bone.bone.matrix_local
            for bind, bone in zip(definition["joint_binds"], self.bones, strict=True)
        ]
        self.inverse_offsets = [offset.inverted() for offset in self.offsets]

    def capture(self):
        rig_world = self.rig.matrix_world
        matrices = [
            rig_world @ bone.matrix @ inverse
            for bone, inverse in zip(self.bones, self.inverse_offsets, strict=True)
        ]
        return [
            pose_from_matrix(matrix if parent < 0 else matrices[parent].inverted() @ matrix)
            for matrix, parent in zip(matrices, self.parents, strict=True)
        ]

    def bases(self, pose, *, clip_space=False):
        """Return bone basis matrices for a canonical pose, without moving the rig."""
        world = self.bind_world if clip_space else self.rig.matrix_world
        inverse_world = world.inverted()
        pose_matrices = [
            inverse_world @ canonical @ offset
            for canonical, offset in zip(world_matrices(pose, self.parents), self.offsets, strict=True)
        ]
        bases = []
        for bone, matrix, parent in zip(self.bones, pose_matrices, self.parents, strict=True):
            rest = bone.bone.matrix_local
            if parent < 0:
                basis = bone.bone.convert_local_to_pose(matrix, rest, invert=True)
            else:
                basis = bone.bone.convert_local_to_pose(
                    matrix, rest, parent_matrix=pose_matrices[parent],
                    parent_matrix_local=self.bones[parent].bone.matrix_local, invert=True,
                )
            bases.append(basis)
        return bases

    def apply_pose(self, pose):
        for bone, basis in zip(self.bones, self.bases(pose), strict=True):
            bone.matrix_basis = basis

    def bake(self, name, poses, times, start_frame, fps, *, clip_space=False):
        """Write a new Action with position and quaternion curves for each bone."""
        bake = ActionBake(self, poses, times, start_frame, fps, clip_space=clip_space)
        bake.advance(len(poses))
        return bake.write(name)

    def assign_action(self, action):
        animation = self.rig.animation_data_create()
        animation.action = action
        animation.action_slot = action.slots[0]
        for bone in self.bones:
            bone.rotation_mode = "QUATERNION"


class ActionBake:
    """Prepare channels in batches, then create the Action after all samples pass."""

    def __init__(self, adapter, poses, times, start_frame, fps, *, clip_space=False):
        if len(poses) == 0 or len(poses) != len(times):
            raise ValueError("动作没有有效帧，或帧数与采样时间不一致。")
        self.adapter = adapter
        self.poses = poses
        self.clip_space = clip_space
        self.frames = [start_frame + seconds * fps for seconds in times]
        self.channels = [[array("f") for _ in range(7)] for _ in adapter.bones]
        self.previous = [None] * len(adapter.bones)
        self.prepared = 0

    def advance(self, limit):
        end = min(self.prepared + limit, len(self.frames))
        for sample in range(self.prepared, end):
            frame = self.frames[sample]
            bases = self.adapter.bases(self.poses[sample], clip_space=self.clip_space)
            for index, basis in enumerate(bases):
                location, rotation, _ = basis.decompose()
                last_rotation = self.previous[index]
                if last_rotation is not None and last_rotation.dot(rotation) < 0.0:
                    rotation.negate()
                self.previous[index] = rotation
                for coordinates, value in zip(self.channels[index], (*location, *rotation), strict=True):
                    coordinates.extend((frame, value))
        self.prepared = end
        return end == len(self.frames)

    def write(self, name):
        if self.prepared != len(self.frames):
            raise RuntimeError("动作通道尚未准备完成。")
        adapter = self.adapter
        action = bpy.data.actions.new(name)
        try:
            slot = action.slots.new(id_type="OBJECT", name=adapter.rig.name)
            strip = action.layers.new("MotionBricks").strips.new(type="KEYFRAME")
            bag = strip.channelbag(slot, ensure=True)
            write_bone_curves(bag, adapter.bones, self.channels, len(self.frames))
            action.use_fake_user = True
            return action
        except Exception:
            bpy.data.actions.remove(action)
            raise

def write_bone_curves(bag, bones, channels, frame_count):
    linear = bpy.types.Keyframe.bl_rna.properties["interpolation"].enum_items["LINEAR"].value
    interpolations = array("i", [linear]) * frame_count
    for bone, curves in zip(bones, channels, strict=True):
        for index, coordinates in enumerate(curves):
            field = "location" if index < 3 else "rotation_quaternion"
            array_index = index if index < 3 else index - 3
            curve = bag.fcurves.new(bone.path_from_id(field), index=array_index, group_name=bone.name)
            points = curve.keyframe_points
            points.add(frame_count)
            points.foreach_set("co", coordinates)
            points.foreach_set("interpolation", interpolations)
            curve.update()


def set_frame(scene, frame):
    integer = math.floor(frame)
    scene.frame_set(integer, subframe=frame - integer)


def scene_fps(scene):
    return scene.render.fps / scene.render.fps_base


def snapshot_pose(context, adapter, include_motion):
    scene = context.scene
    timeline_frame = scene.frame_current + scene.frame_subframe
    context.view_layer.update()
    anchor = adapter.capture()
    snapshot = {"signature": adapter.signature, "pose": anchor}
    animation = adapter.rig.animation_data
    action = animation.action if animation else None
    if include_motion and action is not None:
        fps = scene_fps(scene)
        minimum, maximum = action.frame_range
        basis = [bone.matrix_basis.copy() for bone in adapter.bones]
        try:
            # Capture around the current timeline frame at the model's sampling cadence.
            before, after = [], []
            for offset in (-3, -2, -1, 1, 2, 3):
                sample_frame = max(minimum, min(maximum, timeline_frame + offset * fps / adapter.sample_rate))
                set_frame(scene, sample_frame)
                (before if offset < 0 else after).append(adapter.capture())
            snapshot.update(context=[*before, anchor], target=[anchor, *after])
        finally:
            set_frame(scene, timeline_frame)
            for bone, value in zip(adapter.bones, basis, strict=True):
                bone.matrix_basis = value
            context.view_layer.update()
    return snapshot


def make_pose_asset(adapter, name, snapshot, fps):
    action = adapter.bake(name, [snapshot["pose"]], [0.0], 1.0, fps)
    action[POSE_DATA] = json.dumps(snapshot, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    action.asset_mark()
    action.asset_data.description = "MotionBricks SOMA 姿势"
    if not bpy.app.background:
        action.asset_generate_preview()
    return action


@lru_cache(maxsize=64)
def _snapshot_root_position(payload):
    # The first joint is the SOMA root. Its translation is already in Blender
    # world space in meters, including for snapshots saved by earlier versions.
    try:
        x, y, z = json.loads(payload)["pose"][0][:3]
        if all(math.isfinite(value) for value in (x, y, z)):
            return x, y, z
    except (KeyError, IndexError, TypeError, ValueError, OverflowError):
        pass
    return None


def pose_root_position(action):
    payload = action.get(POSE_DATA) if action is not None else None
    return _snapshot_root_position(payload) if isinstance(payload, str) else None


def read_pose_asset(action, signature):
    if action is None or POSE_DATA not in action:
        raise ValueError("请选择通过 MotionBricks 保存的姿势资产。")
    data = json.loads(action[POSE_DATA])
    if data["signature"] != signature:
        raise ValueError(f"姿势「{action.name}」与当前 SOMA 骨架版本不匹配。")
    return data
