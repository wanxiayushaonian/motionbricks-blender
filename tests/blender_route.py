"""Exercise route generation through the Blender operator and its action library."""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_smoke import ok

import motionbricks_blender as addon
from motionbricks_blender import routes
from motionbricks_blender.rig import RigAdapter, set_frame


def assert_rejected(message):
    try:
        result = bpy.ops.motionbricks.job(operation="route")
    except RuntimeError as error:
        assert message in str(error), str(error)
    else:
        assert result == {"CANCELLED"}, result


def check_generated(scene, adapter, start, end, floor_z):
    state = scene.motionbricks
    clip = state.clips[state.clip_index]
    action = clip.action
    data = action[addon.LIBRARY_DATA]
    assert data["kind"] == "route" and data["end_error"] < 0.15 and data["stop_speed"] < 0.1
    ok(bpy.ops.motionbricks.job, operation="clip")
    assert adapter.rig.animation_data.action == action
    assert (Vector(adapter.capture()[0][:2]) - Vector(start)).length < 0.0002
    initial_height = adapter.capture()[0][2]
    assert 0.8 < initial_height - floor_z < 1.2
    set_frame(scene, action.frame_range[-1])
    assert (Vector(adapter.capture()[0][:2]) - Vector(end)).length < 0.15
    set_frame(scene, action.frame_range[0] + 5)
    ok(bpy.ops.motionbricks.save_pose, name="路线姿势", include_motion=True)
    ok(bpy.ops.motionbricks.sequence, operation="add")
    assert state.targets[-1].pose == state.pose
    ok(bpy.ops.motionbricks.stop_preview)
    return action.name


def main(directory):
    scene = bpy.context.scene
    state = scene.motionbricks
    ok(bpy.ops.motionbricks.job, operation="rig")
    adapter = RigAdapter(bpy.context.object)
    original = adapter.bake("原有动作", [adapter.capture()], [0], 1, 24)
    adapter.assign_action(original)
    route = state.route

    assert_rejected("曲线对象")
    path = directory / "zigzag.json"
    data = {"start": [0, 0, 90], "waypoints": [[2, -0.9], [3.6, -0.9], [4.6, 0]], "end_yaw": 180}
    path.write_text(json.dumps(dict(data, speed=0)), encoding="utf-8")
    route.source = "JSON"
    route.filepath = str(path)
    assert_rejected("行走速度")
    assert not state.clips

    path.write_text(json.dumps(data), encoding="utf-8")
    ok(bpy.ops.motionbricks.job, operation="route")
    assert adapter.rig.animation_data.action == original
    check_generated(scene, adapter, (0, 0), (4.6, 0), 0)
    assert adapter.rig.animation_data.action == original

    geometry = bpy.data.curves.new("路线曲线", "CURVE")
    geometry.dimensions = "3D"
    spline = geometry.splines.new("BEZIER")
    spline.bezier_points.add(2)
    for point, position in zip(spline.bezier_points, ((0, 0, 0), (2, 1, 0), (4, 1, 0.5)), strict=True):
        point.co = position
        point.handle_left_type = point.handle_right_type = "AUTO"
    curve = bpy.data.objects.new("弯道路线上坡", geometry)
    scene.collection.objects.link(curve)
    curve.location = (1.0, 2.0, 0.6)
    bpy.context.view_layer.update()
    route.source = "CURVE"
    route.curve = curve
    course = routes.from_curve(bpy.context, route)
    assert course["start"][:2] == (1, 2) and course["waypoints"][-1] == (5, 3)
    assert abs(course["floor_z"] - 0.6) < 0.00001
    ok(bpy.ops.motionbricks.job, operation="route")
    assert adapter.rig.animation_data.action == original
    name = check_generated(scene, adapter, (1, 2), (5, 3), 0.6)
    curve_name = curve.name
    saved = directory / "route.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(saved))
    bpy.ops.wm.open_mainfile(filepath=str(saved))
    state = bpy.context.scene.motionbricks
    assert state.route.curve is not None and state.route.curve.name == curve_name
    assert state.clips[state.clip_index].action.name == name and len(state.targets) == 2
    print("PASS: JSON and Bézier routes, invalid input, playback, pose capture and reopen", flush=True)


if __name__ == "__main__":
    addon.register()
    try:
        with TemporaryDirectory(prefix="motionbricks-route-") as directory:
            main(Path(directory))
    finally:
        addon.unregister()
