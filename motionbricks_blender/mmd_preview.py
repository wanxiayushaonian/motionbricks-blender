"""Temporary PMX Action that uses the same retarget samples as VMD export."""

from array import array
import json

import bpy
from bpy.props import PointerProperty, StringProperty
from bpy.types import Action, Object, PropertyGroup
from mathutils import Matrix

from . import mmd_bridge
from .rig import write_bone_curves


class MotionMMDPreview(PropertyGroup):
    rig: PointerProperty(type=Object)
    action: PointerProperty(type=Action)
    previous_action: PointerProperty(type=Action)
    saved: StringProperty()


def animation_settings(animation):
    if animation is None:
        return {}
    return {
        "use_nla": animation.use_nla, "action_blend_type": animation.action_blend_type,
        "action_influence": animation.action_influence, "action_extrapolation": animation.action_extrapolation,
    }


def isolate_animation(animation):
    animation.use_nla = False
    animation.action_blend_type = "REPLACE"
    animation.action_influence = 1.0
    animation.action_extrapolation = "HOLD"


def bake(context, adapter, source, rig):
    mapping, frames, poses = mmd_bridge.retarget_action(context, adapter, source, rig)
    mapped = mapping.mapped | {"Center": mapping.center}
    channels = {name: [array("f") for _ in range(7)] for name in mapped}
    previous = {}
    for frame, sample in zip(frames, mmd_bridge.retarget_samples(mapping, poses), strict=True):
        for name, (location, rotation) in sample.items():
            if name in previous and previous[name].dot(rotation) < 0:
                rotation.negate()
            previous[name] = rotation
            for coordinates, value in zip(channels[name], (*location, *rotation), strict=True):
                coordinates.extend((frame, value))

    action = bpy.data.actions.new(f"MotionBricks MMD 预览 · {source.name}")
    try:
        slot = action.slots.new(id_type="OBJECT", name=rig.name)
        strip = action.layers.new("MotionBricks").strips.new(type="KEYFRAME")
        bag = strip.channelbag(slot, ensure=True)
        write_bone_curves(bag, mapped.values(), channels.values(), len(frames))
        return action, set(mmd_bridge.normalized(name) for name in mapping.leg_ik())
    except Exception:
        bpy.data.actions.remove(action)
        raise


def start(context, rig, action, ik_names):
    state = context.scene.motionbricks.mmd_preview
    animation = rig.animation_data
    ik_bones = [bone for bone in rig.pose.bones
                if not bone.is_mmd_shadow_bone
                and mmd_bridge.normalized(mmd_bridge.japanese_name(bone)) in ik_names]
    constraints = [(bone.name, c.name, c.influence) for bone in rig.pose.bones
                   for c in bone.constraints if c.type in {"IK", "LIMIT_ROTATION"}]
    state.saved = json.dumps({
        "animation": animation_settings(animation),
        "slot": animation.action_slot.identifier if animation and animation.action_slot else "",
        "bones": [(bone.name, bone.rotation_mode, [list(row) for row in bone.matrix_basis])
                  for bone in rig.pose.bones],
        "ik": [(bone.name, bone.mmd_ik_toggle) for bone in ik_bones],
        "constraints": constraints,
    }, separators=(",", ":"))
    state.rig = rig
    state.action = action
    state.previous_action = animation.action if animation else None

    animation = rig.animation_data_create()
    isolate_animation(animation)
    animation.action = action
    animation.action_slot = action.slots[0]
    for bone in rig.pose.bones:
        bone.rotation_mode = "QUATERNION"
        bone.matrix_basis = Matrix.Identity(4)
    for bone in ik_bones:
        bone.mmd_ik_toggle = False
    # Some rigs have an already-disabled switch with a separate constraint influence.
    ik_targets = {bone.name for bone in ik_bones}
    for bone in rig.pose.bones:
        for constraint in bone.constraints:
            if constraint.type == "IK" and constraint.target == rig and constraint.subtarget in ik_targets:
                constraint.influence = 0.0


def stop(state):
    """Restore bindings before the scene frame is evaluated. Return saved bone bases."""
    if not state.saved:
        return None, ()
    saved = json.loads(state.saved)
    rig, action = state.rig, state.action
    if rig is not None:
        if saved["animation"]:
            animation = rig.animation_data_create()
            animation.action = state.previous_action
            if state.previous_action is not None:
                animation.action_slot = state.previous_action.slots.get(saved["slot"])
            for field, value in saved["animation"].items():
                setattr(animation, field, value)
        else:
            rig.animation_data_clear()
        for name, mode, _basis in saved["bones"]:
            if bone := rig.pose.bones.get(name):
                bone.rotation_mode = mode
        if hasattr(bpy.types.PoseBone, "mmd_ik_toggle"):
            for name, enabled in saved["ik"]:
                if bone := rig.pose.bones.get(name):
                    bone.mmd_ik_toggle = enabled
        for bone_name, name, influence in saved["constraints"]:
            if (bone := rig.pose.bones.get(bone_name)) and (constraint := bone.constraints.get(name)):
                constraint.influence = influence
    state.rig = state.action = state.previous_action = None
    state.saved = ""
    if action is not None and action.users == 0:
        bpy.data.actions.remove(action)
    return rig, saved["bones"]


def restore_bases(rig, bones):
    if rig is not None:
        for name, _mode, basis in bones:
            if bone := rig.pose.bones.get(name):
                bone.matrix_basis = Matrix(basis)
