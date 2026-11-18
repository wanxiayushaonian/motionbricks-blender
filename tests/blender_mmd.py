"""Verify the installed MotionBricks and MMD Tools extensions with local PMX models."""

import importlib
from pathlib import Path
import math
import sys
from tempfile import TemporaryDirectory

import bpy
from mathutils import Vector

addon = importlib.import_module(next(
    entry.module for entry in bpy.context.preferences.addons
    if entry.module.rsplit(".", 1)[-1] == "motionbricks_blender"
))
mmd_bridge, recording, rig = (importlib.import_module(f"{addon.__name__}.{name}")
                              for name in ("mmd_bridge", "recording", "rig"))


def ok(operator, **kwargs):
    result = operator(**kwargs)
    assert result == {"FINISHED"}, result


def compare_pose(mapping, source, target):
    source_world = dict(zip(mapping.names, rig.world_matrices(source, mapping.parents), strict=True))
    target_world = dict(zip(mapping.names, rig.world_matrices(target, mapping.parents), strict=True))
    errors = {}
    for name in mmd_bridge.CORE:
        source_rotation = source_world[name].to_quaternion()
        target_rotation = target_world[name].to_quaternion()
        errors[name] = math.degrees(2 * math.acos(min(1.0, abs(source_rotation.dot(target_rotation)))))
    return errors


def test_pmx(scene, path, adapter, original, poses, directory):
    state = scene.motionbricks
    source = adapter.rig
    before_import = set(scene.objects)
    ok(bpy.ops.mmd_tools.import_model, filepath=str(path), types={"ARMATURE", "MESH"},
       scale=0.08, log_level="ERROR")
    target = next(obj for obj in set(scene.objects) - before_import if mmd_bridge.is_mmd_rig(obj))
    state.mmd_target = target
    state.clip_index = 0
    mapping = mmd_bridge.BodyMap(adapter, target)
    vmd = mmd_bridge.tools()
    sample_path = directory / (path.stem + ".vmd")
    objects, scenes, actions = set(bpy.data.objects), set(bpy.data.scenes), set(bpy.data.actions)
    rig.set_frame(scene, 61)
    ok(bpy.ops.motionbricks.export_vmd, filepath=str(sample_path))
    assert set(bpy.data.actions) == actions
    ok(bpy.ops.motionbricks.import_vmd, filepath=str(sample_path))
    assert set(bpy.data.objects) == objects and set(bpy.data.scenes) == scenes
    assert len(set(bpy.data.actions) - actions) == 1
    assert source.animation_data.action == original and scene.frame_current == 61
    assert target.animation_data is None or target.animation_data.action is None

    motion = vmd.File()
    motion.load(filepath=str(sample_path))
    assert len(motion.boneAnimation) == len(mapping.mapped) + 1
    center = motion.boneAnimation[mmd_bridge.japanese_name(mapping.center)]
    assert len(center) == len(poses) and center[-1].frame_number == len(poses) - 1
    assert {name for name, enabled in motion.propertyAnimation[0].ik_states if not enabled} == set(mapping.leg_ik())
    result = state.clips[state.clip_index].action
    frames = recording.sampling_frames(result, rig.scene_fps(scene), mmd_bridge.FPS)
    roundtrip = recording.sample_action(bpy.context, adapter, result, frames)
    assert len(roundtrip) == len(poses)
    max_angle = 0.0
    max_position = 0.0
    for index in (0, len(poses) // 4, len(poses) // 2, 3 * len(poses) // 4, len(poses) - 1):
        max_angle = max(max_angle, *compare_pose(mapping, poses[index], roundtrip[index]).values())
        delta = Vector(poses[index, 0, :3] - poses[0, 0, :3])
        returned = Vector(roundtrip[index, 0, :3] - roundtrip[0, 0, :3])
        max_position = max(max_position, (delta - returned).length)
    print(f"ROUND TRIP {path.name} · {len(poses)} frames · {len(motion.boneAnimation)} tracks · "
          f"max {max_angle:.3f}° / {max_position:.6f} m", flush=True)
    assert max_angle < 0.3 and max_position < 0.001

    # The regular MMD Tools import operator should play the file on the PMX armature.
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    rig.set_frame(scene, 1)
    ok(bpy.ops.mmd_tools.import_vmd, filepath=str(sample_path), scale=0.08,
       bone_mapper="PMX", update_scene_settings=False, log_level="ERROR")
    rig.set_frame(scene, 1)
    first = mapping.center.matrix.translation.copy()
    rig.set_frame(scene, len(poses))
    last = mapping.center.matrix.translation.copy()
    expected = Vector(poses[-1, 0, :3] - poses[0, 0, :3]) * mapping.scale
    assert (last - first - expected).length < 0.001
    assert all(not bone.mmd_ik_toggle for bone in target.pose.bones if mmd_bridge.japanese_name(bone) in mapping.leg_ik())


def test_preview(scene, adapter, target, directory):
    state = scene.motionbricks
    context = bpy.context
    source = adapter.rig
    assert context.object == target
    assert addon.working_rig(context) == source
    state.work_rig = None
    state.mmd_source = source
    assert addon.working_rig(context) == source
    state.work_rig = source
    assert state.mmd_source is None
    assert addon.MOTIONBRICKS_PT_sequence.poll(context)
    pmx_animation = target.animation_data
    pmx_action = pmx_animation.action
    slot = pmx_animation.action_slot
    track = pmx_animation.nla_tracks.new()
    track.strips.new(pmx_action.name, 80, pmx_action)
    pmx_animation.use_nla = True
    pmx_animation.action_blend_type = "ADD"
    pmx_animation.action_influence = 0.35
    pmx_animation.action_extrapolation = "NOTHING"
    rig.set_frame(scene, 61.25)
    frame = scene.frame_current_final
    limits = scene.use_preview_range, scene.frame_preview_start, scene.frame_preview_end
    modes = {bone.name: bone.rotation_mode for bone in target.pose.bones}
    ik_before = {bone.name: bone.mmd_ik_toggle for bone in target.pose.bones}
    assert source.animation_data is None
    actions = set(bpy.data.actions)
    state.clip_index = 0
    ok(bpy.ops.motionbricks.job, operation="mmd_preview")
    assert context.object == target
    assert state.preview_rig == source and state.mmd_preview.rig == target
    assert not target.animation_data.use_nla
    assert tuple(target.animation_data.action.frame_range) == tuple(source.animation_data.action.frame_range)
    mapping = mmd_bridge.BodyMap(adapter, target)
    frames = recording.sampling_frames(source.animation_data.action, rig.scene_fps(scene), mmd_bridge.FPS)
    poses = recording.sample_action(context, adapter, source.animation_data.action, frames)
    samples = list(mmd_bridge.retarget_samples(mapping, poses))
    for index in (0, len(frames)//2, len(frames)-1):
        rig.set_frame(scene, frames[index])
        translation = samples[index]["Center"][0]
        assert (mapping.center.location - translation).length < 0.0001
        for name, bone in mapping.mapped.items():
            expected = samples[index][name][1]
            assert abs(bone.rotation_quaternion.dot(expected)) > 0.99999, name
    ok(bpy.ops.motionbricks.stop_preview)
    assert scene.frame_current_final == frame
    assert limits == (scene.use_preview_range, scene.frame_preview_start, scene.frame_preview_end)
    assert target.animation_data.action == pmx_action and target.animation_data.action_slot == slot
    assert target.animation_data.use_nla and len(target.animation_data.nla_tracks) == 1
    assert target.animation_data.action_blend_type == "ADD" and target.animation_data.action_extrapolation == "NOTHING"
    assert abs(target.animation_data.action_influence - 0.35) < 0.0001
    assert {bone.name: bone.rotation_mode for bone in target.pose.bones} == modes
    assert {bone.name: bone.mmd_ik_toggle for bone in target.pose.bones} == ik_before
    assert set(bpy.data.actions) == actions and source.animation_data is None

    # Create a sequence and generate while the PMX stays selected.
    ok(bpy.ops.motionbricks.job, operation="mmd_preview")
    for index, op in ((24, "start"), (52, "add")):
        rig.set_frame(scene, index)
        ok(bpy.ops.motionbricks.save_pose, name=op, include_motion=True)
        ok(bpy.ops.motionbricks.sequence, operation=op)
    ok(bpy.ops.motionbricks.job, operation="generate")
    assert context.object == target and source.animation_data.action == state.last_generated
    assert not state.mmd_preview.saved
    ok(bpy.ops.motionbricks.job, operation="mmd_preview")
    assert source.animation_data.action == state.last_generated
    ok(bpy.ops.wm.save_as_mainfile, filepath=str(directory / "mmd-preview.blend"))
    assert not state.mmd_preview.saved and target.animation_data.action == pmx_action
    assert not any(action.name.startswith("MotionBricks MMD 预览") for action in bpy.data.actions)
    ok(bpy.ops.motionbricks.edit_pose, operation="begin")
    assert context.object == source and context.mode == "POSE"
    ok(bpy.ops.motionbricks.edit_pose, operation="cancel")
    assert context.mode == "OBJECT"
    print("PASS: PMX selection, generation, synchronized preview, restoration, save and pose editing", flush=True)


def test_ik(adapter, target, directory):
    vmd = mmd_bridge.tools()
    mapping = mmd_bridge.BodyMap(adapter, target)
    controls = {mmd_bridge.normalized(mmd_bridge.japanese_name(bone)): bone for bone in target.pose.bones
                if not bone.is_mmd_shadow_bone}
    controller = controls["左足IK"]
    converter = vmd.importer.BoneConverter(controller, 1 / 0.08, invert=True)
    lift = controller.bone.matrix_local.to_3x3().inverted() @ Vector((0, 0.08, 0.12))
    motion = vmd.File()
    motion.boneAnimation = vmd.BoneAnimation()
    motion.propertyAnimation = vmd.PropertyAnimation()
    for frame, position in ((0, (0, 0, 0)), (8, lift), (16, lift)):
        key = vmd.BoneFrameKey()
        key.frame_number = frame
        key.location = converter.convert_location(position)
        key.rotation = (0, 0, 0, 1)
        key.interp = mmd_bridge.linear_interpolation()
        motion.boneAnimation["左足IK"].append(key)
    for frame, enabled in ((0, True), (16, False)):
        key = vmd.PropertyFrameKey()
        key.frame_number = frame
        key.ik_states = [("左足IK", enabled)]
        motion.propertyAnimation.append(key)
    path = directory / "ik.vmd"
    motion.save(filepath=str(path))
    current = target.animation_data.action
    poses = mmd_bridge.read_vmd(bpy.context, adapter, target, path, 0.08)
    foot_index = mapping.names.index("LeftFoot")
    positions = [rig.world_matrices(poses[i], mapping.parents)[foot_index].translation for i in (0, 8, 16)]
    assert (positions[1] - positions[0]).length > 0.07, positions
    assert (positions[2] - positions[1]).length > 0.07, positions
    assert target.animation_data.action == current
    print("PASS: VMD leg IK with alternate bone spelling and a mid-clip toggle", flush=True)


def test_jump_library(scene, adapter, target):
    state = scene.motionbricks
    assert bpy.context.object == adapter.rig or bpy.context.object == target
    if bpy.context.object != target:
        bpy.context.object.select_set(False)
        target.select_set(True)
        bpy.context.view_layer.objects.active = target
    ok(bpy.ops.motionbricks.scan, samples=True)
    state.clip_source = "BVH"
    ok(bpy.ops.motionbricks.clip_directory, directory="jump")
    state.clip_search = "跳跃 a437"
    flags = addon.clip_library.filter_flags(state, 1)
    assert sum(flags) == 1
    state.clip_index = flags.index(1)
    selected_path = state.clips[state.clip_index].path
    ok(bpy.ops.motionbricks.scan, samples=True)
    assert state.clips[state.clip_index].path == selected_path
    ok(bpy.ops.motionbricks.job, operation="clip")
    action = addon.preview_action(state)
    frames = recording.sampling_frames(action, rig.scene_fps(scene), 30)
    poses = recording.sample_action(bpy.context, adapter, action, frames)
    peak = max(range(len(poses)), key=lambda i: poses[i][0][2])
    first, last = action.frame_range
    assert 8 < peak < len(poses) - 12

    state.targets.clear()
    state.capture_motion = True
    for sample, destination in ((peak - 8, "START"), (peak + 12, "TARGET")):
        frame = frames[sample]
        percentage = (frame - first) / (last - first) * 100
        state.preview_progress = percentage
        assert abs(scene.frame_current_final - frame) < 0.0001
        assert abs(state.preview_progress - percentage) < 0.0001
        ok(bpy.ops.motionbricks.save_pose, destination=destination)
        pose = state.pose
        assert f"{(frame - first) / rig.scene_fps(scene):.3f}s" in pose.name
        snapshot = rig.read_pose_asset(pose, adapter.signature)
        assert len(snapshot["context"]) == len(snapshot["target"]) == 4
        assert (Vector(snapshot["pose"][0][:3]) - Vector(poses[sample][0][:3])).length < 0.0001
        if destination == "START":
            assert state.start == pose and state.target_index == -1 and state.start_moving
        else:
            assert state.targets[0].pose == pose and state.target_index == 0 and state.targets[0].moving

    state.capture_motion = False
    ok(bpy.ops.motionbricks.save_pose, destination="TARGET")
    assert not state.targets[-1].moving
    assert "target" not in rig.read_pose_asset(state.pose, adapter.signature)
    ok(bpy.ops.motionbricks.sequence, operation="remove")

    ok(bpy.ops.motionbricks.job, operation="generate")
    generated = state.last_generated
    assert generated == addon.library_selection(state) and bpy.context.object == target
    state.clip_source = "IMPORTED"
    assert state.clip_index == -1
    assert any(addon.clip_library.filter_flags(state, 1))
    state.clip_source = "GENERATED"
    flags = addon.clip_library.filter_flags(state, 1)
    assert all(not visible or not clip.path for visible, clip in zip(flags, state.clips, strict=True))
    state.clip_index = next(i for i, clip in enumerate(state.clips) if clip.action == generated)
    ok(bpy.ops.motionbricks.job, operation="mmd_preview")
    assert addon.preview_action(state) == generated and state.mmd_preview.rig == target
    state.preview_progress = 50
    start, end = state.mmd_preview.action.frame_range
    assert abs(scene.frame_current_final - (start + end) / 2) < 0.0001
    ok(bpy.ops.motionbricks.stop_preview)
    print("PASS: filtered jump, takeoff/landing scrubbing and quick capture, generated PMX preview", flush=True)


def main(paths, directory):
    scene = bpy.context.scene
    scene.render.fps = 24
    state = scene.motionbricks
    ok(bpy.ops.motionbricks.job, operation="rig")
    source = bpy.context.object
    assert state.work_rig == source
    adapter = rig.RigAdapter(source)
    clip = state.clips.add()
    clip.title = "步行测试"
    clip.path = str(addon.repository(bpy.context) / "assets/motions/soma/locomotion/bs_1d_linear/Neutral_walk_forward_002__A057.bvh")
    ok(bpy.ops.motionbricks.job, operation="clip")
    original = source.animation_data.action
    frames = recording.sampling_frames(original, rig.scene_fps(scene), mmd_bridge.FPS)
    poses = recording.sample_action(bpy.context, adapter, original, frames)
    for path in paths:
        test_pmx(scene, path, adapter, original, poses, directory)
    test_ik(adapter, state.mmd_target, directory)
    malformed = directory / "malformed.vmd"
    malformed.write_bytes(b"invalid")
    before = set(bpy.data.actions), set(bpy.data.objects), set(bpy.data.scenes), len(state.clips)
    try:
        result = bpy.ops.motionbricks.import_vmd(filepath=str(malformed))
    except RuntimeError as error:
        assert "VMD 0002" in str(error)
    else:
        assert result == {"CANCELLED"}
    assert before == (set(bpy.data.actions), set(bpy.data.objects), set(bpy.data.scenes), len(state.clips))
    ok(bpy.ops.motionbricks.stop_preview)
    test_preview(scene, adapter, state.mmd_target, directory)
    test_jump_library(scene, adapter, state.mmd_target)
    print("PASS: PMX/VMD round trip, MMD Tools playback, scene isolation, malformed VMD rejection", flush=True)


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:]
    assert args, "Pass one or more paths to local humanoid PMX models."
    with TemporaryDirectory(prefix="motionbricks-mmd-test-") as directory:
        main([Path(path) for path in args], Path(directory))
