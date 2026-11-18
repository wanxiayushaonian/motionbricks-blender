"""Check pose groups and sequence references through renaming, editing and saving."""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import motionbricks_blender as addon
from motionbricks_blender import pose_library
from motionbricks_blender.rig import POSE_DATA, set_frame


def ok(operator, **arguments):
    assert operator(**arguments) == {"FINISHED"}


def check():
    state = bpy.context.scene.motionbricks
    ok(bpy.ops.motionbricks.job, operation="rig")
    rig = state.work_rig
    ok(bpy.ops.motionbricks.save_pose, name="旧姿势")
    legacy = state.pose
    assert pose_library.group_of(legacy) == pose_library.UNGROUPED
    ok(bpy.ops.motionbricks.pose_group, operation="CREATE", name="跳跃")
    group = state.pose_group_filter
    assert len(group) == 32 and state.pose is None
    ok(bpy.ops.motionbricks.scan, samples=True)
    state.clip_index = next(i for i, clip in enumerate(state.clips) if Path(clip.path).name.endswith("A437.bvh"))
    ok(bpy.ops.motionbricks.job, operation="clip")
    set_frame(bpy.context.scene, 28)
    ok(bpy.ops.motionbricks.save_pose, destination="START")
    first = state.start
    first.name = "起跳"
    assert state.start.name == "起跳" and pose_library.group_of(first) == group
    set_frame(bpy.context.scene, 48)
    ok(bpy.ops.motionbricks.save_pose, destination="TARGET")
    last = state.pose
    last.name = "落地"
    assert state.targets[0].pose == last
    payload = last[POSE_DATA]
    position = tuple(state.targets[0].world_position)
    ok(bpy.ops.motionbricks.pose_group, operation="RENAME", identifier=group, name="知更鸟跳跃")
    assert last[POSE_DATA] == payload and tuple(state.targets[0].world_position) == position
    assert pose_library.group_of(last) == group

    # Editing a sequence pose creates a new asset in its source group.
    ok(bpy.ops.motionbricks.edit_pose, operation="begin")
    ok(bpy.ops.motionbricks.edit_pose, operation="save")
    edited = state.pose
    assert state.targets[0].pose == edited and pose_library.group_of(edited) == group
    state.pose_search = "起跳"
    assert state.pose is None and pose_library.visible(first, state)
    state.pose_index = bpy.data.actions.find(first.name)
    assert state.pose == first and state.pose_search == "起跳"
    state.pose_search = ""
    ok(bpy.ops.motionbricks.pose_group, operation="CREATE", name="收藏")
    favorite = state.pose_group_filter
    state.pose = edited
    ok(bpy.ops.motionbricks.pose_group, operation="ASSIGN", identifier=favorite)
    assert state.targets[0].pose == edited and pose_library.group_of(edited) == favorite
    # Share the same group across scenes, and retain an empty group.
    other = bpy.data.scenes.new("另一场景")
    group_copy = other.motionbricks.pose_groups.add()
    group_copy.identifier, group_copy.name = group, "知更鸟跳跃"
    ok(bpy.ops.motionbricks.pose_group, operation="RENAME", identifier=group, name="跳跃动作")
    assert other.motionbricks.pose_groups[0].name == "跳跃动作"
    ok(bpy.ops.motionbricks.pose_group, operation="DELETE", identifier=group)
    assert first in bpy.data.actions[:] and pose_library.group_of(first) == pose_library.UNGROUPED
    assert legacy in bpy.data.actions[:] and state.start == first
    assert not other.motionbricks.pose_groups
    assert pose_library.group_of(edited) == favorite
    ok(bpy.ops.motionbricks.pose_group, operation="CREATE", name="待整理")
    state.pose = edited
    scene_name, first_name, edited_name = bpy.context.scene.name, first.name, edited.name
    ok(bpy.ops.motionbricks.stop_preview)
    with TemporaryDirectory(prefix="motionbricks-pose-library-") as directory:
        path = Path(directory) / "groups.blend"
        ok(bpy.ops.wm.save_as_mainfile, filepath=str(path))
        ok(bpy.ops.wm.open_mainfile, filepath=str(path))
        state = bpy.data.scenes[scene_name].motionbricks
        assert state.start == bpy.data.actions[first_name]
        assert state.targets[0].pose == bpy.data.actions[edited_name]
        assert pose_library.group_of(state.targets[0].pose) == favorite
        assert {"收藏", "待整理"} == set(pose_library.groups().values())
        assert json.loads(state.start[POSE_DATA])["pose"]
    print("PASS: legacy poses, groups, rename/move/delete, edited pose inheritance and saved sequence references", flush=True)


if __name__ == "__main__":
    addon.register()
    try:
        check()
    finally:
        addon.unregister()
