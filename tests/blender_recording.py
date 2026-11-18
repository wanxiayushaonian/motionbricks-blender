"""Run in Blender background, optionally passing real NPZ recordings after --."""

import json
import math
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_smoke import check_pose, ok

import motionbricks_blender as addon
from motionbricks_blender.rig import (
    RIG_DATA, RigAdapter, matrix_from_pose, pose_from_matrix, read_pose_asset, scene_fps, set_frame,
)


def make_fixture(adapter, path):
    definition = json.loads(adapter.rig[RIG_DATA])
    runtime = matrix_from_pose(definition["to_world"])
    inverse_binds = [pose_from_matrix(matrix_from_pose(bind).inverted() @ runtime)
                     for bind in definition["joint_binds"]]
    poses = np.repeat(np.array([adapter.capture()], dtype=np.float32), 15, axis=0)
    poses[:, 0, 1] -= np.arange(len(poses)) * 0.03
    np.savez_compressed(
        path, format=np.array("motionbricks-blender-npz"), format_version=np.int32(1), fps=np.int32(30),
        joint_names=np.array(definition["joints"]), parents=np.array(definition["parents"]),
        inverse_bind=np.asarray(inverse_binds), model_to_runtime=np.asarray(definition["to_world"]), poses=poses,
    )


def selected_action(scene):
    state = scene.motionbricks
    return state.clips[state.clip_index].action


def assert_rejected(path, rig):
    scene = bpy.context.scene
    before = (set(bpy.data.actions), len(scene.motionbricks.clips), rig.animation_data.action, scene.frame_current_final)
    try:
        result = bpy.ops.motionbricks.import_recording(filepath=str(path))
    except RuntimeError as error:
        assert "录制" in str(error), str(error)
    else:
        assert result == {"CANCELLED"}, result
    after = (set(bpy.data.actions), len(scene.motionbricks.clips), rig.animation_data.action, scene.frame_current_final)
    assert before == after


def check_mesh(scene, rig, arrays, sample):
    if "points" not in arrays:
        return
    world = []
    for parent, joint in zip(arrays["parents"], arrays["poses"][sample], strict=True):
        local = matrix_from_pose(joint)
        world.append(local if parent < 0 else world[parent] @ local)
    deform = [pose @ matrix_from_pose(bind) for pose, bind in zip(world, arrays["inverse_bind"], strict=True)]
    mesh = next(obj for obj in scene.objects if obj.type == "MESH" and
                any(mod.type == "ARMATURE" and mod.object == rig for mod in obj.modifiers))
    evaluated = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
    error = 0.0
    for point, vertex, indices, weights in zip(arrays["points"], evaluated.data.vertices,
            arrays["joint_indices"], arrays["joint_weights"], strict=True):
        influences = [(deform[index] @ Vector(point), float(weight)) for index, weight in zip(indices, weights)
                      if index >= 0 and weight > 0.0]
        total = sum(weight for _, weight in influences)
        expected = sum((position * (weight / total) for position, weight in influences), Vector((0, 0, 0)))
        error = max(error, (evaluated.matrix_world @ vertex.co - expected).length)
    assert error < 0.0002, error
    print(f"PASS: recorded mesh error {error:.3g} m", flush=True)


def check_playback(path, rig, adapter, original):
    scene = bpy.context.scene
    with np.load(path, allow_pickle=False) as arrays:
        source_fps = int(arrays["fps"])
        expected = (len(arrays["poses"]) - 1) / source_fps
        for fps in (24, 30, 60):
            scene.render.fps = fps
            set_frame(scene, 17.25)
            ok(bpy.ops.motionbricks.import_recording, filepath=str(path))
            action = selected_action(scene)
            assert rig.animation_data.action == original and scene.frame_current_final == 17.25
            assert scene.render.fps == fps
            assert action[addon.LIBRARY_DATA]["source_file"] == path.name
            start, end = action.frame_range
            assert math.isclose((end - start) / scene_fps(scene), expected, abs_tol=0.00001)
            ok(bpy.ops.motionbricks.job, operation="clip")
            assert rig.animation_data.action == action
            ok(bpy.ops.motionbricks.step_preview, step=1)
            assert scene.frame_current == 2
            ok(bpy.ops.motionbricks.step_preview, step=-1)
            assert scene.frame_current == 1
            sample = len(arrays["poses"]) // 2
            set_frame(scene, start + sample * fps / source_fps)
            bpy.context.view_layer.update()
            check_pose(adapter.capture(), arrays["poses"][sample])
            if fps == 30:
                check_mesh(scene, rig, arrays, sample)
            ok(bpy.ops.motionbricks.stop_preview)
            assert rig.animation_data.action == original and scene.frame_current_final == 17.25
            assert not scene.use_preview_range
        return action


def check_generation(path, adapter):
    scene = bpy.context.scene
    scene.motionbricks.targets.clear()
    ok(bpy.ops.motionbricks.import_recording, filepath=str(path))
    ok(bpy.ops.motionbricks.job, operation="clip")
    action = adapter.rig.animation_data.action
    start, end = action.frame_range
    for index, fraction in enumerate((0.25, 0.65)):
        set_frame(scene, start + fraction * (end - start))
        ok(bpy.ops.motionbricks.save_pose, name=f"录制取帧 {index}", include_motion=not index)
        assert len(read_pose_asset(scene.motionbricks.pose, adapter.signature)["pose"]) == 77
        ok(bpy.ops.motionbricks.sequence, operation="add" if index else "start")
    ok(bpy.ops.motionbricks.job, operation="generate")
    generated = selected_action(scene)
    assert generated == adapter.rig.animation_data.action and len(generated.pose_markers) == 1
    assert generated[addon.LIBRARY_DATA]["kind"] == "generated"


def main(directory, files):
    scene = bpy.context.scene
    ok(bpy.ops.motionbricks.job, operation="rig")
    rig = bpy.context.object
    adapter = RigAdapter(rig)
    original = adapter.bake("原有动作", [adapter.capture()], [0], 1, 30)
    adapter.assign_action(original)

    fixture = directory / "fixture.npz"
    make_fixture(adapter, fixture)
    broken = directory / "broken.npz"
    broken.write_bytes(b"broken")
    assert_rejected(broken, rig)
    with np.load(fixture) as archive:
        values = dict(archive)
    order = np.arange(len(values["parents"]))[::-1]
    original_parents = values["parents"][order]
    remapped = dict(values, joint_names=values["joint_names"][order],
                    parents=np.where(original_parents < 0, -1, order[original_parents]),
                    inverse_bind=values["inverse_bind"][order], poses=values["poses"][:, order])
    reordered = directory / "reordered.npz"
    np.savez_compressed(reordered, **remapped)
    ok(bpy.ops.motionbricks.import_recording, filepath=str(reordered))
    values["inverse_bind"][0, 0] += 0.1
    np.savez_compressed(broken, **values)
    assert_rejected(broken, rig)

    for path in (fixture, *files):
        check_playback(path, rig, adapter, original)
    selected = selected_action(scene)
    ok(bpy.ops.motionbricks.scan, samples=True)
    assert selected == selected_action(scene)
    if files:
        for path in files:
            check_generation(path, adapter)
    generated_or_imported = selected_action(scene).name
    saved = directory / "library.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(saved))
    bpy.ops.wm.open_mainfile(filepath=str(saved))
    scene = bpy.context.scene
    assert selected_action(scene).name == generated_or_imported
    ok(bpy.ops.motionbricks.job, operation="clip")
    assert bpy.context.object.animation_data.action.name == generated_or_imported
    print("PASS: NPZ validation, playback at 24/30/60 fps, stepping, restoration, rescanning and saved library",
          flush=True)


if __name__ == "__main__":
    files = [Path(arg).resolve() for arg in sys.argv[sys.argv.index("--") + 1:]] if "--" in sys.argv else []
    addon.register()
    try:
        with TemporaryDirectory(prefix="motionbricks-recording-") as directory:
            main(Path(directory), files)
    finally:
        addon.unregister()
