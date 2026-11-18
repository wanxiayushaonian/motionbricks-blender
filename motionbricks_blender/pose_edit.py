"""Edit a placed sequence pose on the live SOMA rig and restore its animation afterward."""

import json

import bpy
from bpy.props import BoolProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Action, Object, PropertyGroup
from mathutils import Matrix, Quaternion, Vector

from . import placement, pose_library, sequence_preview
from .rig import (
    RigAdapter, make_pose_asset, matrix_from_pose, pose_from_matrix, read_pose_asset, scene_fps, set_frame,
)


class MotionPoseEdit(PropertyGroup):
    rig: PointerProperty(type=Object)
    source: PointerProperty(type=Action)
    previous_action: PointerProperty(type=Action)
    index: IntProperty()
    saved: StringProperty()
    can_keep_motion: BoolProperty()
    keep_motion: BoolProperty(name="保留动势", description="将骨骼修改应用到原姿势的相邻采样帧")


def begin(context, adapter):
    scene = context.scene
    state = scene.motionbricks
    edit = state.pose_edit
    index = placement.selected_index(state)
    if edit.saved or index < 0:
        raise ValueError("请选择序列中的一个姿势。")
    action, item = placement.slots(state)[index]
    snapshot = read_pose_asset(action, adapter.signature)
    posed = placement.place_frames(snapshot, [snapshot["pose"]], item)[0]
    rig = adapter.rig
    animation = rig.animation_data
    if animation is not None and animation.use_tweak_mode:
        raise ValueError("请先退出 NLA 编辑模式。")

    edit.saved = json.dumps({
        "mode": rig.mode,
        "frame": scene.frame_current + scene.frame_subframe,
        "auto_key": scene.tool_settings.use_keyframe_insert_auto,
        "animation": animation is not None,
        "use_nla": animation.use_nla if animation else False,
        "slot": animation.action_slot.identifier if animation and animation.action_slot else "",
        "bones": [(bone.name, bone.rotation_mode, [list(row) for row in bone.matrix_basis])
                  for bone in adapter.bones],
    }, separators=(",", ":"))
    edit.rig = rig
    edit.source = action
    edit.previous_action = animation.action if animation else None
    edit.index = index
    edit.can_keep_motion = bool(snapshot.get("target" if index else "context"))
    edit.keep_motion = edit.can_keep_motion and (state.targets[index - 1].moving if index else state.start_moving)
    try:
        scene.tool_settings.use_keyframe_insert_auto = False
        if animation is not None:
            animation.action = None
            animation.use_nla = False
        context.view_layer.update()
        if rig.mode != "POSE" and bpy.ops.object.mode_set(mode="POSE") != {"FINISHED"}:
            raise RuntimeError("无法切换到姿态模式。")
        adapter.apply_pose(posed)
        context.view_layer.update()
    except Exception:
        restore(context)
        raise
    region = context.space_data.region_3d if context.area and context.area.type == "VIEW_3D" else None
    if region and region.view_perspective != "CAMERA":
        region.view_location = Vector(posed[0][:3])
    sequence_preview.redraw()


def restore(context):
    scene = context.scene
    edit = scene.motionbricks.pose_edit
    if not edit.saved:
        return
    saved = json.loads(edit.saved)
    rig = edit.rig
    if rig is not None:
        if context.object == rig and rig.mode != saved["mode"] and rig.mode in {"POSE", "OBJECT"}:
            bpy.ops.object.mode_set(mode=saved["mode"])
        for name, rotation_mode, _ in saved["bones"]:
            bone = rig.pose.bones.get(name)
            if bone is not None:
                bone.rotation_mode = rotation_mode
        if saved["animation"]:
            animation = rig.animation_data_create()
            animation.action = edit.previous_action
            if animation.action is not None and saved["slot"]:
                animation.action_slot = animation.action.slots.get(saved["slot"])
            animation.use_nla = saved["use_nla"]
        else:
            rig.animation_data_clear()
    scene.tool_settings.use_keyframe_insert_auto = saved["auto_key"]
    set_frame(scene, saved["frame"])
    if rig is not None:
        for name, _, basis in saved["bones"]:
            bone = rig.pose.bones.get(name)
            if bone is not None:
                bone.matrix_basis = Matrix(basis)
    context.view_layer.update()
    edit.rig = edit.source = edit.previous_action = None
    edit.saved = ""
    sequence_preview.redraw()


def update_motion(snapshot, edited, original):
    changes = []
    for before, after in zip(original["pose"], edited, strict=True):
        translation = Vector(after[:3]) - Vector(before[:3])
        rotation = Quaternion((after[6], *after[3:6])) @ Quaternion((before[6], *before[3:6])).inverted()
        changes.append((translation, rotation))
    for key, anchor in (("context", -1), ("target", 0)):
        if key not in original:
            continue
        frames = []
        for frame in original[key]:
            joints = []
            for joint, (translation, rotation) in zip(frame, changes, strict=True):
                q = (rotation @ Quaternion((joint[6], *joint[3:6]))).normalized()
                joints.append([*(Vector(joint[:3]) + translation), q.x, q.y, q.z, q.w])
            frames.append(joints)
        frames[anchor] = edited
        snapshot[key] = frames


def save(context):
    state = context.scene.motionbricks
    edit = state.pose_edit
    slots = placement.slots(state)
    index = edit.index
    if index >= len(slots) or edit.source is None or slots[index][0] != edit.source:
        raise ValueError("编辑中的序列项已改变，请取消编辑后重试。")
    if edit.rig is None or edit.rig.name not in context.view_layer.objects or edit.rig.mode == "EDIT":
        raise ValueError("请保留参考骨架并退出骨架编辑模式。")

    adapter = RigAdapter(edit.rig)
    _, item = slots[index]
    offset, yaw = item.offset, item.yaw
    context.view_layer.update()
    captured = adapter.capture()
    root = Matrix.Rotation(-yaw, 4, "Z") @ matrix_from_pose(captured[0])
    root.translation = Vector(captured[0][:3]) - Vector(offset)
    captured[0] = pose_from_matrix(root)
    snapshot = {"signature": adapter.signature, "pose": captured}
    keep_motion = edit.can_keep_motion and edit.keep_motion
    if keep_motion:
        update_motion(snapshot, captured, read_pose_asset(edit.source, adapter.signature))
    action = make_pose_asset(adapter, f"{edit.source.name} 编辑", snapshot, scene_fps(context.scene))
    try:
        pose_library.assign(action, pose_library.group_of(edit.source))
        restore(context)
    except Exception:
        bpy.data.actions.remove(action)
        raise
    if index:
        target = state.targets[index - 1]
        target.pose = action
        target.moving = keep_motion
    else:
        state.start = action
        state.start_placement.offset = offset
        state.start_placement.yaw = yaw
        state.start_moving = keep_motion
    state.pose = action
    return action
