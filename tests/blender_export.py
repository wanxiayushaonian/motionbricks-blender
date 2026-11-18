"""Export actual route and transition Actions, then play the recordings in a new scene."""

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
from blender_recording import check_mesh, selected_action

import motionbricks_blender as addon
from motionbricks_blender.rig import RigAdapter, set_frame


def export_and_compare(scene, adapter, path, fps):
    rig = adapter.rig
    action = selected_action(scene)
    start, end = action.frame_range
    active = rig.animation_data.action
    frame = scene.frame_current_final
    scenes, objects = set(bpy.data.scenes), set(bpy.data.objects)
    ok(bpy.ops.motionbricks.export_recording, filepath=str(path), fps=fps)
    assert set(bpy.data.scenes) == scenes and set(bpy.data.objects) == objects
    assert rig.animation_data.action == active and scene.frame_current_final == frame

    with np.load(path, allow_pickle=False) as file:
        poses = file["poses"]
        metadata = json.loads(file["source"].item())
        assert metadata["source_frame_range"] == [start, end]
        assert int(file["fps"]) == fps
        assert abs((len(poses) - 1) / fps - (end - start) / scene.render.fps) <= 0.5 / fps + 1e-5
        adapter.assign_action(action)
        for sample in (0, len(poses) // 2, len(poses) - 1):
            set_frame(scene, start + (end - start) * sample / (len(poses) - 1))
            check_pose(adapter.capture(), poses[sample])
        assert (Vector(poses[-1, 0, :3]) - Vector(poses[0, 0, :3])).length > 0.3
        if "路线" in action.name:
            assert len(file["points"]) == 18056 and len(file["joint_names"]) == 77
    rig.animation_data.action = active
    set_frame(scene, frame)


def main(directory):
    scene = bpy.context.scene
    scene.render.fps = 24
    state = scene.motionbricks
    ok(bpy.ops.motionbricks.job, operation="rig")
    adapter = RigAdapter(bpy.context.object)
    original = adapter.bake("已有动作", [adapter.capture()], [0], 1, 24)
    adapter.assign_action(original)
    route_json = directory / "zigzag.json"
    route_json.write_text(json.dumps({"start": [0, 0, 90], "waypoints": [[2, -.9], [3.6, -.9], [4.6, 0]]}))
    state.route.source = "JSON"
    state.route.filepath = str(route_json)
    ok(bpy.ops.motionbricks.job, operation="route")
    route_path = directory / "zigzag.npz"
    export_and_compare(scene, adapter, route_path, 30)

    ok(bpy.ops.motionbricks.job, operation="clip")
    for frame, operation in ((24, "start"), (52, "add")):
        set_frame(scene, frame)
        ok(bpy.ops.motionbricks.save_pose, name=operation, include_motion=True)
        ok(bpy.ops.motionbricks.sequence, operation=operation)
    state.generation_start = 48
    state.generation_output = "NLA"
    ok(bpy.ops.motionbricks.job, operation="generate")
    transition_path = directory / "transition.npz"
    export_and_compare(scene, adapter, transition_path, 60)

    new_scene = bpy.data.scenes.new("另一个工程")
    bpy.context.window.scene = new_scene
    new_scene.render.fps = 60
    ok(bpy.ops.motionbricks.job, operation="rig")
    adapter = RigAdapter(bpy.context.object)
    for path in (route_path, transition_path):
        with np.load(path, allow_pickle=False) as file:
            ok(bpy.ops.motionbricks.import_recording, filepath=str(path))
            ok(bpy.ops.motionbricks.job, operation="clip")
            action = selected_action(new_scene)
            fps = int(file["fps"])
            assert math.isclose(action.frame_range[-1] - action.frame_range[0],
                                (len(file["poses"]) - 1) * 60 / fps, abs_tol=0.0001)
            middle = len(file["poses"]) // 2
            set_frame(new_scene, 1 + middle * 60 / fps)
            check_pose(adapter.capture(), file["poses"][middle])
            check_mesh(new_scene, adapter.rig, file, middle)
            ok(bpy.ops.motionbricks.stop_preview)
    print("PASS: route and frame-48 NLA transition export, unchanged animation, and 60 fps round trip", flush=True)


if __name__ == "__main__":
    addon.register()
    try:
        if "--" in sys.argv:
            main(Path(sys.argv[sys.argv.index("--") + 1]))
        else:
            with TemporaryDirectory(prefix="motionbricks-export-") as directory:
                main(Path(directory))
    finally:
        addon.unregister()
