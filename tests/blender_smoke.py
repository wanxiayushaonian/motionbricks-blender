"""Run with Blender --background --factory-startup --python-exit-code 1 --python."""

import json
from math import atan2, radians
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

import bpy
from mathutils import Quaternion, Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import motionbricks_blender
from motionbricks_blender import placement
from motionbricks_blender.rig import (
    POSE_DATA, RigAdapter, pose_root_position, read_pose_asset, scene_fps, set_frame,
)
from motionbricks_blender.sequence_preview import SequencePreview

WALK = "Neutral_walk_forward_002__A057.bvh"


def ok(operator, **args):
    result = operator(**args)
    assert result == {"FINISHED"}, (operator, args, result, bpy.context.window_manager.motionbricks_status)


def check_pose(actual, expected, position_error=0.001, angle_error=0.001):
    for joint, (first, second) in enumerate(zip(actual, expected, strict=True)):
        distance = (Vector(first[:3]) - Vector(second[:3])).length
        q1 = Quaternion((first[6], *first[3:6])).normalized()
        q2 = Quaternion((second[6], *second[3:6])).normalized()
        delta = q1.rotation_difference(q2)
        angle = 2.0 * atan2(Vector((delta.x, delta.y, delta.z)).length, abs(delta.w))
        assert distance < position_error and angle < angle_error, (joint, distance, angle)


def check_skin_against_upstream(scene, rig, clip_path):
    repository = motionbricks_blender.repository(bpy.context)
    with TemporaryDirectory(prefix="motionbricks-skin-test-") as directory:
        reference = Path(directory) / "reference.json"
        subprocess.run(
            [str(repository / ".venv/bin/python"), str(Path(__file__).with_name("reference_skin.py")),
             clip_path, str(reference)],
            cwd=repository, check=True, timeout=30,
        )
        expected = json.loads(reference.read_text())

    mesh = next(obj for obj in scene.objects if obj.type == "MESH"
                and any(mod.type == "ARMATURE" and mod.object == rig for mod in obj.modifiers))
    max_error = 0.0
    for frame in expected["frames"]:
        set_frame(scene, 1.0 + frame["seconds"] * scene_fps(scene))
        bpy.context.view_layer.update()
        evaluated = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
        error = max(
            (evaluated.matrix_world @ vertex.co - Vector(point)).length
            for vertex, point in zip(evaluated.data.vertices, frame["vertices"], strict=True)
        )
        max_error = max(max_error, error)
        assert error < 0.0002, ("skinned vertex error in meters", frame["seconds"], error)

        for name, expected_head in zip(expected["joints"], frame["heads"], strict=True):
            head = rig.matrix_world @ rig.pose.bones[name].head
            assert (head - Vector(expected_head)).length < 0.0002, (name, head, expected_head)

        for side in ("Left", "Right"):
            for name, child in (("Arm", "ForeArm"), ("ForeArm", "Hand"), ("Leg", "Shin"), ("Shin", "Foot")):
                bone = rig.pose.bones[side + name]
                child_head = rig.pose.bones[side + child].head
                assert (bone.tail - bone.head).angle(child_head - bone.head) < 0.01, bone.name

    print(f"PASS: upstream vertex and joint positions, anatomically aligned limb axes ({max_error:.3g} m)", flush=True)


def save_pose(name, seconds, include_motion):
    scene = bpy.context.scene
    set_frame(scene, 1.0 + seconds * scene_fps(scene))
    ok(bpy.ops.motionbricks.save_pose, name=name, include_motion=include_motion)
    pose = scene.motionbricks.pose
    assert pose.asset_data is not None and tuple(pose.frame_range) == (1.0, 1.0)
    rig = bpy.context.object
    root = rig.matrix_world @ rig.pose.bones["Hips"].head
    assert (Vector(pose_root_position(pose)) - root).length < 0.0002
    return pose


def check_placement(state, preview, signature):
    middle = state.targets[0].pose
    original = middle[POSE_DATA]
    snapshot = read_pose_asset(middle, signature)
    source = Vector(snapshot["pose"][0][:3])
    first = state.targets[0]
    first.world_position = source + Vector((0.3, -0.2, 0.1))
    first.placement.yaw = radians(90)

    state.pose = middle
    ok(bpy.ops.motionbricks.sequence, operation="add")
    second = state.targets[2]
    second.world_position = source + Vector((-0.2, -1.0, 0.0))
    assert preview.sync()
    assert preview.entries[1] is preview.entries[3]
    for index, pose, matrix in preview.instances():
        if index in (1, 3):
            target = state.targets[index - 1]
            assert (matrix @ Vector(pose.anchor) - Vector(target.world_position)).length < 0.00001

    frames = placement.place_frames(snapshot, snapshot["target"], first.placement)
    sample = Vector(snapshot["target"][1][0][:3]) - source
    moved = Vector(frames[1][0][:3]) - Vector(frames[0][0][:3])
    assert (moved - Vector((-sample.y, sample.x, sample.z))).length < 0.00001
    assert frames[1][1:] == snapshot["target"][1][1:]
    assert (Vector(frames[0][0][:3]) - Vector(first.world_position)).length < 0.00001
    assert middle[POSE_DATA] == original

    state.isolate_pose = True
    assert [index for index, _, _ in preview.instances()] == [3]
    state.isolate_pose = False
    second_position = tuple(second.world_position)
    ok(bpy.ops.motionbricks.sequence, operation="up")
    assert state.targets[1].pose == middle and tuple(state.targets[1].world_position) == second_position
    ok(bpy.ops.motionbricks.sequence, operation="remove")
    state.target_index = 0
    ok(bpy.ops.motionbricks.reset_placement)
    assert not any(state.targets[0].placement.offset) and state.targets[0].placement.yaw == 0.0


def check_pose_edit(scene, rig):
    state = scene.motionbricks
    adapter = RigAdapter(rig)
    state.target_index = 0
    target = state.targets[0]
    target.placement.offset = (0.15, -0.1, 0.0)
    target.placement.yaw = radians(17)
    source = target.pose
    snapshot = read_pose_asset(source, adapter.signature)
    assert "target" in snapshot and target.moving
    other = state.targets[1].pose
    action = rig.animation_data.action
    slot = rig.animation_data.action_slot
    old_mode = rig.mode
    scene.tool_settings.use_keyframe_insert_auto = True
    set_frame(scene, 7.25)
    bpy.context.view_layer.update()
    original = adapter.capture()
    placed = placement.place_frames(snapshot, [snapshot["pose"]], target.placement)[0]
    arm = rig.pose.bones["LeftForeArm"]
    joint = adapter.bones.index(arm)

    for operation in ("cancel", "save"):
        ok(bpy.ops.motionbricks.edit_pose, operation="begin")
        assert rig.mode == "POSE" and rig.animation_data.action is None
        assert not scene.tool_settings.use_keyframe_insert_auto
        check_pose(adapter.capture(), placed)
        arm.rotation_mode = "QUATERNION"
        arm.rotation_quaternion = arm.rotation_quaternion @ Quaternion((1, 0, 0), radians(25))
        bpy.context.view_layer.update()
        captured = adapter.capture()
        assert captured[joint] != placed[joint]
        if operation == "save":
            state.pose_edit.keep_motion = False
        ok(bpy.ops.motionbricks.edit_pose, operation=operation)
        assert rig.mode == old_mode and rig.animation_data.action == action and rig.animation_data.action_slot == slot
        assert scene.tool_settings.use_keyframe_insert_auto and scene.frame_current_final == 7.25
        check_pose(adapter.capture(), original)

    assert source != target.pose and state.pose == target.pose and state.targets[1].pose == other
    assert not target.moving and read_pose_asset(source, adapter.signature) == snapshot
    result = read_pose_asset(target.pose, adapter.signature)
    assert "target" not in result
    check_pose(placement.place_frames(result, [result["pose"]], target.placement)[0], captured)

    state.target_index = -1
    start = state.start
    offset, yaw = state.start_placement.offset, state.start_placement.yaw
    ok(bpy.ops.motionbricks.edit_pose, operation="begin")
    ok(bpy.ops.motionbricks.edit_pose, operation="save")
    assert state.start != start and state.start_placement.offset == offset and state.start_placement.yaw == yaw

    state.clip_index = next(i for i, clip in enumerate(state.clips) if Path(clip.path).name == "run_loop_180_R_003__A331.bvh")
    ok(bpy.ops.motionbricks.job, operation="clip")
    run = save_pose("run1", 0.8, True)
    state.target_index = 0
    target.pose = run
    target.moving = True
    placement.set_location(run, target.placement, (-0.1, -2.4, 1.0))
    original_run = read_pose_asset(run, adapter.signature)
    ok(bpy.ops.motionbricks.edit_pose, operation="begin")
    assert state.pose_edit.keep_motion
    arm.rotation_mode = "QUATERNION"
    arm.rotation_quaternion = arm.rotation_quaternion @ Quaternion((1, 0, 0), radians(20))
    bpy.context.view_layer.update()
    edited = adapter.capture()
    ok(bpy.ops.motionbricks.edit_pose, operation="save")
    saved_run = read_pose_asset(target.pose, adapter.signature)
    assert target.moving and saved_run["target"][0] == saved_run["pose"]
    assert saved_run["context"][-1] == saved_run["pose"] and read_pose_asset(run, adapter.signature) == original_run
    root_delta = lambda sample: Vector(sample["target"][-1][0][:3]) - Vector(sample["target"][0][0][:3])
    assert root_delta(original_run).length > 0.05
    assert (root_delta(original_run) - root_delta(saved_run)).length < 0.00001
    assert saved_run["target"][-1][joint] != original_run["target"][-1][joint]
    check_pose(placement.place_frames(saved_run, [saved_run["pose"]], target.placement)[0], edited)
    ok(bpy.ops.motionbricks.stop_preview)

    state.generation_start = 48
    state.generation_output = "NLA"
    target.duration_preference = "SHORT"
    state.targets[1].duration_preference = "LONG"
    ok(bpy.ops.motionbricks.job, operation="generate")
    generated = state.last_generated
    arrivals = tuple(generated[motionbricks_blender.LIBRARY_DATA]["arrival_frames"])
    assert generated.frame_range[0] == 48 and scene.frame_current == 48
    assert abs(generated.frame_range[-1] - arrivals[-1]) < 0.0001
    assert [marker.frame for marker in generated.pose_markers] == [round(frame) for frame in arrivals]
    assert arrivals[0] - 48 <= 25 * scene_fps(scene) / 30 + 0.00001
    assert arrivals[1] - arrivals[0] >= 41 * scene_fps(scene) / 30 - 0.00001
    animation = rig.animation_data
    assert animation.action is None and animation.nla_tracks[-2].strips[0].action == action
    strip = animation.nla_tracks[-1].strips[0]
    assert strip.action == generated and strip.frame_start == 48
    assert abs(strip.frame_end - arrivals[-1]) < 0.0001
    set_frame(scene, arrivals[0])
    bpy.context.view_layer.update()
    check_pose(adapter.capture()[:1], edited[:1])
    print(f"PASS: edited running pose retains motion, generated at frame 48 into NLA, arrivals {arrivals}", flush=True)


def main():
    motionbricks_blender.register()
    try:
        scene = bpy.context.scene
        scene.render.fps = 24
        scene.frame_start, scene.frame_end = 1, 250
        state = scene.motionbricks
        ok(bpy.ops.motionbricks.job, operation="rig")
        rig = bpy.context.object
        adapter = RigAdapter(rig)
        assert len(adapter.bones) == 77
        assert any(modifier.type == "ARMATURE" and modifier.object == rig
                   for obj in scene.objects if obj.type == "MESH" for modifier in obj.modifiers)

        rest = adapter.capture()
        original = adapter.bake("已有动作", [rest], [0.0], 1.0, 24)
        adapter.assign_action(original)
        original_slot = rig.animation_data.action_slot.identifier

        ok(bpy.ops.motionbricks.scan, samples=True)
        assert len(state.clips) >= 38
        state.clip_index = next(i for i, clip in enumerate(state.clips) if Path(clip.path).name == WALK)
        ok(bpy.ops.motionbricks.job, operation="clip")
        clip_action = rig.animation_data.action
        clip_duration = state.clips[state.clip_index].duration
        assert clip_duration > 0 and scene.use_preview_range
        check_skin_against_upstream(scene, rig, state.clips[state.clip_index].path)

        ok(bpy.ops.motionbricks.stop_preview)
        assert rig.animation_data.action == original and not scene.use_preview_range
        assert rig.animation_data.action_slot.identifier == original_slot
        ok(bpy.ops.motionbricks.job, operation="clip")
        assert rig.animation_data.action == clip_action

        start = save_pose("行走起点", 1.0, True)
        ok(bpy.ops.motionbricks.sequence, operation="start")
        middle = save_pose("继续前进", 1.8, True)
        ok(bpy.ops.motionbricks.sequence, operation="add")
        end = save_pose("结束姿势", 3.4, False)
        ok(bpy.ops.motionbricks.sequence, operation="add")
        assert state.start == start and [item.pose for item in state.targets] == [middle, end]
        assert len(read_pose_asset(start, adapter.signature)["context"]) == 4
        assert "target" not in read_pose_asset(end, adapter.signature)
        positions = [pose_root_position(asset) for asset in (start, middle, end)]
        assert len(set(positions)) == 3
        preview = SequencePreview(scene, rig)
        datablocks = (set(bpy.data.objects), set(bpy.data.scenes))
        before_preview = adapter.capture()
        assert preview.sync() and len(preview.entries) == 3
        assert tuple(entry.foot[:2] for entry in preview.entries) == tuple(position[:2] for position in positions)
        assert (set(bpy.data.objects), set(bpy.data.scenes)) == datablocks
        assert rig.animation_data.action == clip_action
        check_pose(adapter.capture(), before_preview)
        preview_poses = preview.entries
        ok(bpy.ops.motionbricks.sequence, operation="up")
        assert [pose_root_position(item.pose) for item in state.targets] == [positions[2], positions[1]]
        assert preview.sync() and preview.entries == (preview_poses[0], preview_poses[2], preview_poses[1])
        ok(bpy.ops.motionbricks.sequence, operation="down")
        assert [pose_root_position(item.pose) for item in state.targets] == positions[1:]

        # A baked Action round-trips an existing pose without touching the original Action.
        snapshot = read_pose_asset(middle, adapter.signature)
        state.pose = middle
        ok(bpy.ops.motionbricks.sequence, operation="apply")
        check_pose(adapter.capture(), snapshot["pose"])
        mesh = next(obj for obj in scene.objects if obj.type == "MESH" and
                    any(mod.type == "ARMATURE" and mod.object == rig for mod in obj.modifiers))
        evaluated = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
        preview_vertices = preview_poses[1].parts[0][0]
        max_error = max(
            (evaluated.matrix_world @ vertex.co - Vector(point)).length
            for vertex, point in zip(evaluated.data.vertices, preview_vertices, strict=True)
        )
        assert max_error < 0.0002, ("preview vertex error in meters", max_error)
        check_placement(state, preview, adapter.signature)
        test_action = adapter.bake("往返测试", [snapshot["pose"]], [0.0], 1.0, 24)
        adapter.assign_action(test_action)
        set_frame(scene, 1.0)
        bpy.context.view_layer.update()
        check_pose(adapter.capture(), snapshot["pose"])
        adapter.assign_action(clip_action)

        state.start_world_position = Vector(positions[0]) + Vector((0.05, 0.0, 0.0))
        state.start_placement.yaw = radians(-4)
        state.targets[-1].world_position = Vector(positions[-1]) + Vector((0.15, -0.15, 0.0))
        state.targets[-1].placement.yaw = radians(9)
        placed_start = tuple(state.start_world_position)
        placed_end = tuple(state.targets[-1].world_position)
        # Switching to the USD parent leaves the original working rig in use.
        context = bpy.context
        output = scene.objects["OUTPUT"]
        rig.select_set(False)
        output.select_set(True)
        context.view_layer.objects.active = output
        assert motionbricks_blender.MOTIONBRICKS_PT_sequence.poll(context)
        assert motionbricks_blender.selectable_soma_rigs(context, [rig]) == [rig]
        rig.hide_set(True)
        assert not motionbricks_blender.selectable_soma_rigs(context, [rig])
        rig.hide_set(False)
        objects, frame = set(scene.objects), scene.frame_current_final
        assert motionbricks_blender.working_rig(context) == rig
        assert set(scene.objects) == objects and scene.frame_current_final == frame
        assert state.start == start and state.targets[-1].pose == end
        assert rig.animation_data.action == clip_action
        ok(bpy.ops.motionbricks.job, operation="generate")
        assert context.object == output and output.select_get()
        ok(bpy.ops.motionbricks.select_soma)
        assert context.object == rig and rig.select_get() and not output.select_get()
        generated = rig.animation_data.action
        assert generated not in (clip_action, original)
        assert len(generated.pose_markers) == 2
        assert not scene.use_preview_range and (scene.frame_start, scene.frame_end) == (1, 250), (
            scene.use_preview_range, scene.frame_start, scene.frame_end,
        )
        assert bpy.context.window_manager.motionbricks_status.startswith("已生成 2 段")
        for frame, asset, item in zip(
                generated.frame_range, (start, end), (state.start_placement, state.targets[-1].placement), strict=True):
            set_frame(scene, frame)
            bpy.context.view_layer.update()
            pose = adapter.capture()
            snapshot = read_pose_asset(asset, adapter.signature)
            expected = placement.place_frames(snapshot, [snapshot["pose"]], item)[0]
            check_pose(pose[:1], expected[:1])
        set_frame(scene, 1.0)

        # The data survives saving and reopening the project.
        with TemporaryDirectory(prefix="motionbricks-blend-test-") as directory:
            path = str(Path(directory) / "smoke.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            state = bpy.context.scene.motionbricks
            rig = bpy.context.object
            adapter = RigAdapter(rig)
            assert state.start.name == "行走起点" and len(state.targets) == 2
            assert POSE_DATA in state.targets[0].pose
            assert [pose_root_position(state.start), *(pose_root_position(item.pose) for item in state.targets)] == positions
            assert tuple(state.start_world_position) == placed_start and tuple(state.targets[-1].world_position) == placed_end
            assert abs(state.targets[-1].placement.yaw - radians(9)) < 0.000001
            assert rig.animation_data.action.name.startswith("MotionBricks 补间")
            check_pose_edit(bpy.context.scene, rig)
        print("PASS: USD rig, previews, pose assets, independent placement, placed TensorRT endpoints and saved project",
              flush=True)
    finally:
        motionbricks_blender.unregister()


if __name__ == "__main__":
    main()
