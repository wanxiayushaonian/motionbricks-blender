"""Verify the packaged extension against a configured backend."""

import json
from pathlib import Path
import sys

import bpy
from mathutils import Vector

repository = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
dist = Path(__file__).resolve().parents[1] / "dist"
for version in ("0.9.0", "0.10.0"):
    assert bpy.ops.extensions.package_install_files(
        filepath=str(dist / f"motionbricks_blender-{version}.zip"),
        repo="user_default", enable_on_install=True,
    ) == {"FINISHED"}

import bl_ext.user_default.motionbricks_blender as addon
from bl_ext.user_default.motionbricks_blender.rig import RigAdapter, pose_root_position, set_frame


def ok(operator, **arguments):
    assert operator(**arguments) == {"FINISHED"}


settings = bpy.context.preferences.addons[addon.__name__].preferences
settings.repository = str(repository)
ok(bpy.ops.motionbricks.backend_setup, operation="PROBE")
report = json.loads(settings.backend_report)
assert not any(item["level"] == "ERROR" for item in report["checks"]), report
ok(bpy.ops.motionbricks.job, operation="rig")
state = bpy.context.scene.motionbricks
rig = state.work_rig
adapter = RigAdapter(rig)
ok(bpy.ops.motionbricks.save_pose, name="自摆起始")
ok(bpy.ops.motionbricks.sequence, operation="start")
arm = rig.pose.bones["LeftArm"]
arm.rotation_mode = "XYZ"
arm.rotation_euler.z += .12
bpy.context.view_layer.update()
ok(bpy.ops.motionbricks.save_pose, name="自摆目标")
ok(bpy.ops.motionbricks.sequence, operation="add")
state.targets[0].world_position = Vector(pose_root_position(state.start)) + Vector((0, -.6, 0))
expected = Vector(state.targets[0].world_position)
ok(bpy.ops.motionbricks.job, operation="generate")
generated = state.last_generated
assert generated is not None
set_frame(bpy.context.scene, generated.frame_range[-1])
assert (Vector(adapter.capture()[0][:3]) - expected).length < .001
assert not any(clip.path for clip in state.clips)
print("PASS: packaged 0.9.0 to 0.10.0 update, diagnostic, official rig and authored-pose generation", flush=True)
