"""Filtering for the motion library and the timeline position of its preview."""

import math

import bpy

from .rig import set_frame

LIBRARY_DATA = "mb_library"
SOURCE_FILTERS = (
    ("ALL", "全部来源", "显示全部动作"),
    ("BVH", "BVH 动作包", "显示从磁盘扫描的 BVH 片段"),
    ("IMPORTED", "导入动作", "显示导入的 NPZ 录制和 VMD 动作"),
    ("GENERATED", "生成结果", "显示生成的补间和路线行走"),
)
DIRECTORY_LABELS = {
    "avoidance": "避障", "jump": "跳跃", "locomotion": "移动", "variations": "动作变体",
}


def directory_label(category):
    root, _, suffix = category.partition("/")
    label = DIRECTORY_LABELS.get(root, "根目录" if root == "." else root)
    return f"{label} / {suffix}" if suffix else label


def action_kind(clip):
    if clip.path:
        return "BVH"
    if clip.action is None:
        return None
    metadata = clip.action.get(LIBRARY_DATA, {})
    if metadata.get("kind") in {"recording", "vmd"}:
        return "IMPORTED"
    return "GENERATED"


def matches(clip, source, directory, query):
    kind = action_kind(clip)
    if kind is None or (source != "ALL" and kind != source):
        return False
    if directory and (kind != "BVH" or not (
        clip.category == directory or clip.category.startswith(f"{directory}/")
    )):
        return False
    if not query:
        return True
    action = clip.action
    metadata = action.get(LIBRARY_DATA, {}) if action is not None else {}
    text = " ".join((
        clip.title, action.name if action is not None else "", clip.category,
        directory_label(clip.category), str(metadata.get("source_file", "")),
    )).casefold()
    return all(token in text for token in query)


def filter_flags(state, visible_bit):
    query = state.clip_search.casefold().split()
    directory = state.clip_directory if state.clip_source in {"ALL", "BVH"} else ""
    return [visible_bit if matches(clip, state.clip_source, directory, query) else 0 for clip in state.clips]


def directories(clips):
    paths = set()
    for clip in clips:
        if clip.path and clip.category:
            parts = clip.category.split("/")
            paths.update("/".join(parts[:count]) for count in range(1, len(parts) + 1))
    return sorted(paths, key=str.casefold)


def preview_action(state):
    rig = state.preview_rig
    return rig.animation_data.action if rig is not None and rig.animation_data is not None else None


def progress(state):
    action = preview_action(state)
    if action is None:
        return 0.0
    first, last = action.frame_range
    scene = state.id_data
    if last <= first:
        return 0.0
    frame = scene.frame_current + scene.frame_subframe
    return min(100.0, max(0.0, 100.0 * (frame - first) / (last - first)))


def seek(state, value):
    action = preview_action(state)
    if action is None or state.pose_edit.saved or not math.isfinite(value):
        return
    scene = state.id_data
    context = bpy.context
    if context.scene == scene and context.screen and context.screen.is_animation_playing:
        bpy.ops.screen.animation_cancel(restore_frame=False)
    first, last = action.frame_range
    set_frame(scene, first + (last - first) * min(100.0, max(0.0, value)) / 100.0)
