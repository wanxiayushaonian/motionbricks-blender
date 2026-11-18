"""Preview and generate SOMA motion in Blender using a local MotionBricks worker."""

import importlib
from itertools import islice
import json
import math
from pathlib import Path
import subprocess
import sys

import bpy
from bpy.app.handlers import persistent
from bpy.props import (
    BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty,
    IntProperty, PointerProperty, StringProperty,
)
from bpy.types import Action, AddonPreferences, Menu, Object, Operator, Panel, PropertyGroup, UIList
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Matrix

# Blender can retain submodules when reinstalling an extension that was disabled.
# Reload our helper modules before importing symbols from this release.
importlib.invalidate_caches()
for _dependency in (
    "backend_config", "jobs", "setup_ui", "rig", "pose_library", "clip_library", "recording", "mmd_bridge", "mmd_preview", "placement", "sequence_preview", "placement_gizmos", "pose_edit", "timeline", "routes",
):
    if _cached := sys.modules.get(f"{__package__}.{_dependency}"):
        importlib.reload(_cached)

from . import setup_ui, pose_library, clip_library, mmd_bridge, mmd_preview, placement, placement_gizmos, pose_edit, recording, routes, sequence_preview, timeline
from .clip_library import LIBRARY_DATA, preview_action
from .jobs import BackendJob, default_backend
from .backend_config import default_install_path
from .rig import (
    CURRENT_BIND_REVISION, POSE_DATA, RIG_DATA, RIG_REVISION,
    ActionBake, RigAdapter, create_reference_rig, make_pose_asset,
    pose_root_position, read_pose_asset, scene_fps, set_frame, snapshot_pose,
)

_active_operator = None
_pending_clip_preview = None


def repository(context):
    addon = context.preferences.addons.get(__package__)
    value = (addon.preferences.repository if addon else "") or default_backend()
    if not value:
        raise ValueError("请在插件偏好设置中指定 motionbricks-main 目录。")
    return Path(bpy.path.abspath(value)).expanduser().resolve()


def clip_duration(path):
    frames = None
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in islice(stream, 5000):
                label, _, value = line.strip().partition(":")
                if label == "Frames":
                    frames = int(value)
                elif label == "Frame Time" and frames is not None:
                    step = float(value)
                    if frames > 0 and math.isfinite(step) and step > 0.0:
                        return (frames - 1) * step
                    break
    except (OSError, ValueError):
        pass
    return -1.0


def pose_asset_only(_self, action):
    return POSE_DATA in action


def position_text(position, *, compact=False):
    if position is None:
        return "坐标不可用"
    precision = 2 if compact else 3
    separator = "/" if compact else " / "
    return separator.join(f"{value:z.{precision}f}" for value in position)


def pause_playback(context):
    if context.screen and context.screen.is_animation_playing:
        bpy.ops.screen.animation_cancel(restore_frame=False)


def preview_range(scene, start, end):
    scene.frame_preview_end = max(scene.frame_preview_start, end)
    scene.frame_preview_start = start
    scene.frame_preview_end = end


def start_preview(context, adapter, action, *, play=True):
    scene = context.scene
    state = scene.motionbricks
    rig = adapter.rig
    if rig.animation_data and rig.animation_data.use_tweak_mode:
        raise ValueError("请先退出 NLA 编辑模式。")
    if state.mmd_preview.saved or (state.preview_rig is not None and state.preview_rig != rig):
        stop_preview(context)
    if state.preview_rig is None:
        animation = rig.animation_data
        state.preview_rig = rig
        state.previous_action = animation.action if animation else None
        state.previous_animation = json.dumps(mmd_preview.animation_settings(animation))
        state.previous_slot = (
            animation.action_slot.identifier if animation and animation.action_slot else ""
        )
        state.previous_bases = json.dumps([
            (bone.name, bone.rotation_mode, [list(row) for row in bone.matrix_basis])
            for bone in rig.pose.bones
        ], separators=(",", ":"))
        state.previous_frame = scene.frame_current + scene.frame_subframe
        state.previous_preview = scene.use_preview_range
        state.previous_start = scene.frame_preview_start
        state.previous_end = scene.frame_preview_end
    mmd_preview.isolate_animation(rig.animation_data_create())
    adapter.assign_action(action)
    start, end = action.frame_range
    scene.use_preview_range = True
    preview_range(scene, math.floor(start), math.ceil(end))
    set_frame(scene, start)
    if play:
        play_preview(context)


def play_preview(context):
    if context.screen and not bpy.app.background and not context.screen.is_animation_playing:
        bpy.ops.screen.animation_play()


def stop_preview(context):
    scene = context.scene
    state = scene.motionbricks
    rig = state.preview_rig
    if rig is None and not state.mmd_preview.saved and not state.previous_bases:
        return
    pause_playback(context)
    pmx, pmx_bases = mmd_preview.stop(state.mmd_preview)
    previous_bones = json.loads(state.previous_bases or "[]")
    if rig is not None:
        if state.previous_animation == "{}":
            rig.animation_data_clear()
        else:
            animation = rig.animation_data_create()
            animation.action = state.previous_action
            if state.previous_action is not None:
                animation.action_slot = state.previous_action.slots.get(state.previous_slot)
            for field, value in json.loads(state.previous_animation or "{}").items():
                setattr(animation, field, value)
        for name, mode, _basis in previous_bones:
            if bone := rig.pose.bones.get(name):
                bone.rotation_mode = mode
    preview_range(scene, state.previous_start, state.previous_end)
    scene.use_preview_range = state.previous_preview
    set_frame(scene, state.previous_frame)
    mmd_preview.restore_bases(rig, previous_bones)
    mmd_preview.restore_bases(pmx, pmx_bases)
    context.view_layer.update()
    state.previous_bases = state.previous_animation = state.previous_slot = ""
    state.preview_rig = state.previous_action = None


def start_mmd_preview(context, adapter, source, target):
    mmd_bridge.tools()
    for rig in (adapter.rig, target):
        if rig.animation_data and rig.animation_data.use_tweak_mode:
            raise ValueError("请先退出 NLA 编辑模式。")
    action, ik_names = mmd_preview.bake(context, adapter, source, target)
    state = context.scene.motionbricks
    try:
        stop_preview(context)
        start_preview(context, adapter, source, play=False)
        mmd_preview.start(context, target, action, ik_names)
        set_frame(context.scene, source.frame_range[0])
        play_preview(context)
    except Exception:
        was_bound = state.mmd_preview.action == action
        stop_preview(context)
        if not was_bound and action.users == 0:
            bpy.data.actions.remove(action)
        raise


def work_rig_changed(state, context):
    state.mmd_source = None
    if context.scene and context.scene.motionbricks == state:
        stop_preview(context)
        if sequence_preview.active(context.scene):
            sequence_preview.clear()


class MotionBricksPreferences(AddonPreferences):
    bl_idname = __package__

    repository: StringProperty(
        name="MotionBricks 目录", subtype="DIR_PATH", default=default_backend(),
        description="选择包含 .venv、motionbricks 和 assets 的 motionbricks-main 目录",
    )

    install_directory: StringProperty(
        name="新后端安装目录", subtype="DIR_PATH", default=default_install_path(),
        description="选择空目录，或选择之前由本插件创建的后端目录继续安装",
    )
    setup_fp32: BoolProperty(name="同时下载 FP32 模型", default=False)
    backend_report: StringProperty(options={"SKIP_SAVE"})

    def draw(self, context):
        setup_ui.draw_setup(self.layout, context, self)


class MotionClip(PropertyGroup):
    action: PointerProperty(type=Action)
    path: StringProperty(subtype="FILE_PATH")
    title: StringProperty()
    category: StringProperty()
    duration: FloatProperty(default=-1.0)


def placement_changed(_data, context):
    if context.scene and sequence_preview.active(context.scene):
        sequence_preview.redraw()


class MotionPlacement(PropertyGroup):
    x: FloatProperty(options=set(), update=placement_changed)
    y: FloatProperty(options=set(), update=placement_changed)
    z: FloatProperty(options=set(), update=placement_changed)
    yaw: FloatProperty(
        name="朝向偏移", subtype="ANGLE", options=set(), update=placement_changed,
        description="相对原姿势绕世界 Z 轴旋转",
    )

    @property
    def offset(self):
        return (self.x, self.y, self.z)

    @offset.setter
    def offset(self, value):
        self.x, self.y, self.z = value


class MotionTarget(PropertyGroup):
    pose: PointerProperty(type=Action, poll=pose_asset_only)
    moving: BoolProperty(name="保留动势", default=True)
    duration_preference: EnumProperty(name="到此段时长", items=(
        ("AUTO", "自动", "由模型从默认长度中选择"),
        ("SHORT", "偏短", "从较短的模型生成长度中选择"),
        ("LONG", "偏长", "从较长的模型生成长度中选择"),
    ), default="AUTO")
    placement: PointerProperty(type=MotionPlacement)
    world_position: FloatVectorProperty(
        name="世界坐标（米）", subtype="XYZ", size=3, precision=3, options=set(),
        get=lambda self: placement.location(self.pose, self.placement) or (0.0, 0.0, 0.0),
        set=lambda self, value: placement.set_location(self.pose, self.placement, value),
    )


def start_changed(state, context):
    state.start_placement.offset = (0.0, 0.0, 0.0)
    state.start_placement.yaw = 0.0
    placement_changed(state, context)


class MotionRoute(PropertyGroup):
    source: EnumProperty(name="路线来源", items=(
        ("CURVE", "曲线", "使用场景中的贝塞尔曲线或多段线"),
        ("JSON", "路线 JSON", "读取 Walker 路线文件"),
    ), default="CURVE")
    curve: PointerProperty(name="路线曲线", type=Object, poll=lambda _self, obj: obj.type == "CURVE")
    filepath: StringProperty(name="路线文件", subtype="FILE_PATH")
    override_speed: BoolProperty(name="覆盖文件速度")
    speed: FloatProperty(name="速度（米/秒）", default=1.2, min=0.0, precision=2)
    set_start_yaw: BoolProperty(name="指定起点朝向")
    set_end_yaw: BoolProperty(name="指定终点朝向")
    start_yaw: FloatProperty(name="起点朝向", subtype="ANGLE", description="0° 朝世界 −Y，90° 朝 +X")
    end_yaw: FloatProperty(name="终点朝向", subtype="ANGLE", description="0° 朝世界 −Y，90° 朝 +X")
    hold: FloatProperty(name="起止停留（秒）", default=0.5, min=0.0, max=10.0, precision=2)
    precision: EnumProperty(name="模型精度", items=(
        ("FP16", "FP16", "使用现有的 SOMA 混合精度模型"),
        ("FP32", "FP32", "使用 SOMA 全精度模型"),
    ), default="FP16")


def cancel_clip_autoplay():
    global _pending_clip_preview
    _pending_clip_preview = None
    if bpy.app.timers.is_registered(play_selected_clip):
        bpy.app.timers.unregister(play_selected_clip)


def play_selected_clip():
    global _pending_clip_preview
    request = _pending_clip_preview
    _pending_clip_preview = None
    if request is None or _active_operator is not None:
        return None
    window, area, scene, index = request
    try:
        if (not any(candidate == window for candidate in bpy.context.window_manager.windows)
                or window.scene != scene or not any(candidate == area for candidate in window.screen.areas)):
            return None
        state = scene.motionbricks
        if not state.clip_autoplay or index != state.clip_index or state.pose_edit.saved:
            return None
        region = next((region for region in area.regions if region.type == "WINDOW"), None)
        if region is not None:
            with bpy.context.temp_override(window=window, area=area, region=region):
                if rig_ready(bpy.context) and bpy.context.mode in {"OBJECT", "POSE"}:
                    bpy.ops.motionbricks.job("EXEC_DEFAULT", operation="clip")
    except (ReferenceError, RuntimeError):
        # The operator reports failures in the sidebar; the window can also close before the timer fires.
        pass
    return None


def choose_library_index(state, index):
    # Programmatic changes (scan, import, filters) should not start playback.
    cancel_clip_autoplay()
    state["clip_index"] = index


def clip_index_changed(state, context):
    global _pending_clip_preview
    cancel_clip_autoplay()
    if (bpy.app.background or not state.clip_autoplay or _active_operator is not None
            or state.pose_edit.saved or not context.window or not context.area
            or context.area.type != "VIEW_3D" or not 0 <= state.clip_index < len(state.clips)):
        return
    _pending_clip_preview = context.window, context.area, context.scene, state.clip_index
    bpy.app.timers.register(play_selected_clip, first_interval=0.16)


def clip_filters_changed(state, _context):
    cancel_clip_autoplay()
    flags = clip_library.filter_flags(state, 1)
    index = state.clip_index
    if not (0 <= index < len(flags) and flags[index]):
        choose_library_index(state, -1)


class MotionSettings(PropertyGroup):
    directory: StringProperty(name="动作包目录", subtype="DIR_PATH")
    clips: CollectionProperty(type=MotionClip)
    clip_index: IntProperty(default=0, min=-1, update=clip_index_changed)
    clip_search: StringProperty(name="搜索动作", options={"TEXTEDIT_UPDATE"}, update=clip_filters_changed)
    clip_source: EnumProperty(name="动作来源", items=clip_library.SOURCE_FILTERS, update=clip_filters_changed)
    clip_directory: StringProperty(name="动作目录", update=clip_filters_changed)
    clip_autoplay: BoolProperty(name="选中即播放", description="选中动作库条目后在 SOMA 上循环播放")
    preview_progress: FloatProperty(
        name="播放进度", subtype="PERCENTAGE", min=0, max=100, precision=1,
        description="拖动到片段内的位置并暂停播放",
        get=clip_library.progress, set=clip_library.seek, options={"SKIP_SAVE"},
    )
    capture_motion: BoolProperty(name="保留动势", default=True,
                                 description="同时保存当前帧前后的采样，供补间使用")
    pose: PointerProperty(name="姿势资产", type=Action, poll=pose_asset_only, update=pose_library.show_pose)
    pose_groups: CollectionProperty(type=pose_library.MotionPoseGroup)
    pose_group_filter: StringProperty(update=pose_library.filters_changed)
    pose_search: StringProperty(name="搜索姿势", options={"TEXTEDIT_UPDATE"}, update=pose_library.filters_changed)
    pose_index: IntProperty(get=pose_library.pose_index, set=pose_library.select_pose)
    start: PointerProperty(name="起始姿势", type=Action, poll=pose_asset_only, update=start_changed)
    start_placement: PointerProperty(type=MotionPlacement)
    start_moving: BoolProperty(name="保留动势", default=True)
    start_world_position: FloatVectorProperty(
        name="世界坐标（米）", subtype="XYZ", size=3, precision=3, options=set(),
        get=lambda self: placement.location(self.start, self.start_placement) or (0.0, 0.0, 0.0),
        set=lambda self, value: placement.set_location(self.start, self.start_placement, value),
    )
    targets: CollectionProperty(type=MotionTarget)
    target_index: IntProperty(default=0)
    isolate_pose: BoolProperty(name="只看选中", options={"SKIP_SAVE"}, update=placement_changed)
    pose_edit: PointerProperty(type=pose_edit.MotionPoseEdit)
    route: PointerProperty(type=MotionRoute)
    work_rig: PointerProperty(
        name="工作骨架", type=Object, poll=lambda _self, obj: is_soma_rig(obj),
        description="此场景用于播放、保存姿势、生成补间和 MMD 转换的 SOMA 骨架",
        update=work_rig_changed,
    )
    mmd_target: PointerProperty(name="MMD 骨架", type=Object, poll=lambda _self, obj: mmd_bridge.is_mmd_rig(obj))
    mmd_preview: PointerProperty(type=mmd_preview.MotionMMDPreview)
    # Older projects store the explicitly selected SOMA here.
    mmd_source: PointerProperty(
        name="SOMA 骨架", type=Object,
        poll=lambda _self, obj: is_soma_rig(obj),
    )
    mmd_scale: FloatProperty(
        name="PMX 导入比例", default=0.08, min=0.0001, max=100.0, precision=4,
        description="填写 MMD Tools 导入这个 PMX 时使用的比例，默认值为 0.08",
    )
    generation_start: IntProperty(name="补间起始帧", default=1, min=-1048574, max=1048574)
    generation_output: EnumProperty(name="输出到", items=(
        ("ACTION", "活动 Action", "将结果设为角色当前的活动 Action"),
        ("NLA", "NLA 条带", "在 NLA 中添加生成动作"),
    ), default="ACTION")
    last_generated: PointerProperty(type=Action)
    preview_rig: PointerProperty(type=Object)
    previous_action: PointerProperty(type=Action)
    previous_slot: StringProperty()
    previous_bases: StringProperty()
    previous_animation: StringProperty()
    previous_frame: FloatProperty()
    previous_preview: BoolProperty()
    previous_start: IntProperty()
    previous_end: IntProperty()


def add_library_action(state, adapter, action, kind, duration, source_fps, **source):
    action[LIBRARY_DATA] = {
        "kind": kind, "signature": adapter.signature, "layout": adapter.layout_signature,
        "source_fps": source_fps, **source,
    }
    clip = state.clips.add()
    clip.title = action.name
    clip.duration = duration
    clip.action = action
    index = len(state.clips) - 1
    if not clip_library.filter_flags(state, 1)[index]:
        state.clip_search = ""
        state.clip_source = "ALL"
        state.clip_directory = ""
    choose_library_index(state, index)


def library_selection(state):
    return state.clips[state.clip_index].action if 0 <= state.clip_index < len(state.clips) else None


def bvh_cache_key(path, adapter, fps):
    return json.dumps(
        [str(path), path.stat().st_mtime_ns, adapter.signature, adapter.layout_signature, fps],
        ensure_ascii=False,
    )


class MOTIONBRICKS_OT_scan(Operator):
    bl_idname = "motionbricks.scan"
    bl_label = "扫描动作包"
    bl_options = {"REGISTER", "UNDO"}

    samples: BoolProperty(default=False, options={"SKIP_SAVE"})

    def execute(self, context):
        state = context.scene.motionbricks
        try:
            if self.samples:
                state.directory = str(repository(context) / "assets/motions/soma")
            if not state.directory:
                raise ValueError("请先选择动作包目录。")
            path = Path(bpy.path.abspath(state.directory)).expanduser().resolve()
            if not path.is_dir():
                raise ValueError(f"找不到动作包目录：{path}")
            files = sorted(
                (file for file in path.rglob("*") if file.suffix.lower() == ".bvh" and file.is_file()),
                key=lambda file: file.as_posix().casefold(),
            )
            selected = state.clips[state.clip_index] if 0 <= state.clip_index < len(state.clips) else None
            selected_action = selected.action if selected is not None else None
            selected_path = selected.path if selected is not None else ""
            for index in reversed(range(len(state.clips))):
                if state.clips[index].path:
                    state.clips.remove(index)
            for file in files:
                clip = state.clips.add()
                clip.path = str(file)
                clip.title = file.stem
                clip.category = file.parent.relative_to(path).as_posix()
                clip.duration = clip_duration(file)
            if state.clip_directory and state.clip_directory not in clip_library.directories(state.clips):
                state.clip_directory = ""
            flags = clip_library.filter_flags(state, 1)
            index = next(
                (i for i, clip in enumerate(state.clips) if flags[i] and (
                    (selected_action is not None and clip.action == selected_action)
                    or (selected_path and clip.path == selected_path)
                )),
                next((i for i, flag in enumerate(flags) if flag), -1),
            )
            choose_library_index(state, index)
            context.window_manager.motionbricks_status = f"已找到 {len(files)} 个 BVH 片段。"
            return {"FINISHED"}
        except (OSError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


class MOTIONBRICKS_OT_job(Operator):
    bl_idname = "motionbricks.job"
    bl_label = "运行 MotionBricks"
    bl_options = {"REGISTER", "UNDO"}

    operation: EnumProperty(items=(
        ("rig", "创建参考角色", "导入标准 SOMA 角色"),
        ("clip", "预览选中动作", "载入 BVH 并播放"),
        ("mmd_preview", "预览 MMD 角色", "让工作骨架和 MMD 角色同步播放选中动作"),
        ("generate", "生成补间", "生成经过目标姿势的骨骼动画"),
        ("route", "生成路线行走", "沿曲线或路点生成 SOMA 行走动作"),
        ("export", "导出录制", "将选中的动作导出为 MotionBricks 录制"),
    ), options={"SKIP_SAVE"})
    filepath: StringProperty(subtype="FILE_PATH", options={"HIDDEN", "SKIP_SAVE"})
    export_fps: IntProperty(default=30, min=1, max=240, options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        global _active_operator
        cancel_clip_autoplay()
        if context.scene.motionbricks.pose_edit.saved:
            self.report({"WARNING"}, "请先保存或取消骨骼编辑。")
            return {"CANCELLED"}
        if _active_operator is not None:
            self.report({"WARNING"}, "已有一个 MotionBricks 任务在运行。")
            return {"CANCELLED"}

        self._job = None
        self._timer = None
        self._manager = context.window_manager
        self._scene_id = context.scene.as_pointer()
        self._cancel_requested = False
        self._rig = working_rig(context)
        try:
            if context.mode not in {"OBJECT", "POSE"}:
                raise ValueError("请先切换到物体模式或姿态模式。")
            request = self._prepare(context)
            if request is None:
                return {"FINISHED"}
            self._job = BackendJob(repository(context), request)
            _active_operator = self
            if bpy.app.background:
                self._job.process.wait(timeout=300)
                return self._complete(context)
            self._manager.motionbricks_status = "正在启动本地 MotionBricks…"
            self._timer = self._manager.event_timer_add(0.12, window=context.window)
            self._manager.modal_handler_add(self)
            return {"RUNNING_MODAL"}
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            self._cleanup()
            return self._failed(error)

    def _prepare(self, context):
        if self.operation == "rig":
            return {"command": "describe"}
        state = context.scene.motionbricks
        adapter = RigAdapter(self._rig)
        self._signature = adapter.signature
        self._rig_world = self._rig.matrix_world.copy()
        if self.operation == "export":
            action = library_selection(state)
            metadata = action.get(LIBRARY_DATA, {}) if action is not None else {}
            if metadata.get("signature") != adapter.signature or metadata.get("layout") != adapter.layout_signature:
                raise ValueError("请选中与当前 SOMA 骨架匹配的动作库 Action。")
            if context.mode not in {"OBJECT", "POSE"}:
                raise ValueError("请在物体模式或姿态模式下导出录制。")
            if not self.filepath:
                raise ValueError("请选择录制的保存位置。")
            self._export_path = Path(bpy.path.abspath(bpy.path.ensure_ext(self.filepath, ".npz")))
            self._export_action = action
            self._export_scene_fps = scene_fps(context.scene)
            recording.sampling_frames(action, self._export_scene_fps, self.export_fps)
            return {"command": "recording_assets", "signature": adapter.signature}
        if self.operation == "route":
            self._route_name, course = routes.from_settings(context, state.route)
            self._generation_fps = scene_fps(context.scene)
            return {"command": "route", "signature": adapter.signature, "course": course}
        if self.operation in {"clip", "mmd_preview"}:
            if self.operation == "mmd_preview":
                self._preview_target = state.mmd_target
                if not mmd_bridge.is_mmd_rig(self._preview_target) or not rig_in_view(context, self._preview_target):
                    raise ValueError("请选择当前视图层中的 MMD 骨架。")
            if not 0 <= state.clip_index < len(state.clips):
                raise ValueError("请先在动作包里选中一个片段。")
            clip = state.clips[state.clip_index]
            if clip.action is not None:
                metadata = clip.action.get(LIBRARY_DATA, {})
                if metadata.get("signature") != adapter.signature or metadata.get("layout") != adapter.layout_signature:
                    raise ValueError("动作与当前 SOMA 参考骨架不匹配。")
                self._play_clip(context, adapter, clip.action)
                return None
            if not clip.path:
                raise ValueError("动作库中缺少此动作，请重新导入。")
            self._clip = Path(clip.path)
            self._clip_key = bvh_cache_key(self._clip, adapter, scene_fps(context.scene))
            cached = next((action for action in bpy.data.actions if action.get("mb_clip") == self._clip_key), None)
            if cached is not None:
                self._play_clip(context, adapter, cached)
                self._set_duration(state, cached["mb_clip_duration"])
                return None
            return {"command": "clip", "signature": adapter.signature, "path": str(self._clip)}

        start = read_pose_asset(state.start, adapter.signature)
        if not state.targets:
            raise ValueError("请至少添加一个目标姿势。")
        self._generation_start = state.generation_start
        self._generation_output = state.generation_output
        self._generation_fps = scene_fps(context.scene)
        self._target_names = [target.pose.name if target.pose else "" for target in state.targets]
        if self._generation_output == "NLA" and self._rig.animation_data and self._rig.animation_data.use_tweak_mode:
            raise ValueError("请退出 NLA 编辑模式后再生成。")
        targets = []
        for target in state.targets:
            snapshot = read_pose_asset(target.pose, adapter.signature)
            frames = snapshot.get("target", [snapshot["pose"]]) if target.moving else [snapshot["pose"]]
            targets.append(placement.place_frames(snapshot, frames, target.placement))
        start_frames = [start["pose"]] * 4
        if state.start_moving:
            start_frames = start.get("context", start_frames)
        return {
            "command": "generate", "signature": adapter.signature,
            "context": placement.place_frames(start, start_frames, state.start_placement),
            "targets": targets,
            "durations": [target.duration_preference for target in state.targets],
        }

    def _play_clip(self, context, adapter, action):
        if self.operation == "mmd_preview":
            if self._preview_target != context.scene.motionbricks.mmd_target or not rig_in_view(context, self._preview_target):
                raise ValueError("预览期间 MMD 骨架已改变，请重试。")
            start_mmd_preview(context, adapter, action, self._preview_target)
            self._manager.motionbricks_status = f"正在同步预览 {action.name}"
        else:
            start_preview(context, adapter, action)
            self._manager.motionbricks_status = f"正在预览 {action.name}"

    def _set_duration(self, state, seconds):
        for clip in state.clips:
            if clip.path == str(self._clip):
                clip.duration = seconds
                break

    def modal(self, context, event):
        if _active_operator is not self:
            return {"CANCELLED"}
        if event.type == "ESC" or self._cancel_requested:
            self._manager.motionbricks_status = "任务已取消。"
            self._cleanup()
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        if context.scene is None or context.scene.as_pointer() != self._scene_id:
            self._cleanup()
            return self._failed(ValueError("场景已切换，任务已取消。"))
        if self._job.finished():
            return self._complete(context)
        self._manager.motionbricks_status = self._job.status()
        if context.area:
            context.area.tag_redraw()
        return {"PASS_THROUGH"}

    def cancel(self, _context):
        self._cleanup()

    def _complete(self, context):
        try:
            result = self._job.result()
            if self.operation == "rig":
                rig = create_reference_rig(context, result["rig"])
                stop_preview(context)
                context.scene.motionbricks.work_rig = rig
                self._manager.motionbricks_status = f"已创建 {rig.name}。"
                return {"FINISHED"}

            if self._rig != working_rig(context):
                raise ValueError("任务运行时工作骨架已改变，请重试。")
            adapter = RigAdapter(self._rig)
            if adapter.signature != self._signature:
                raise ValueError("任务运行时参考骨架已改变。")
            scene = context.scene
            state = scene.motionbricks
            if self.operation == "export":
                if self._rig.mode == "EDIT":
                    raise ValueError("请退出骨架编辑模式后导出。")
                self._manager.motionbricks_status = "正在采样并写入录制…"
                count = recording.save_recording(
                    self._export_path, context, adapter, self._export_action,
                    self._export_scene_fps, self.export_fps, result,
                )
                self._manager.motionbricks_status = f"已导出 {count} 帧 · {self.export_fps} fps"
                self.report({"INFO"}, f"已导出 {self._export_path.name}")
                return {"FINISHED"}
            if self.operation in {"clip", "mmd_preview"}:
                action = adapter.bake(
                    self._clip.stem, result["poses"], result["times"],
                    1.0, scene_fps(scene), clip_space=True,
                )
                action["mb_clip"] = self._clip_key
                action["mb_clip_duration"] = result["duration"]
                action["mb_clip_source_fps"] = result["source_fps"]
                self._set_duration(state, result["duration"])
                self._play_clip(context, adapter, action)
            elif self.operation == "route":
                if self._rig.matrix_world != self._rig_world or scene_fps(scene) != self._generation_fps:
                    raise ValueError("任务运行时角色变换或工程帧率已改变，请重新生成。")
                action = adapter.bake(
                    f"路线 · {self._route_name}", result["poses"], result["times"], 1.0, self._generation_fps,
                )
                add_library_action(
                    state, adapter, action, "route", result["times"][-1], adapter.sample_rate,
                    source_file=self._route_name, end_error=result["end_error"], stop_speed=result["stop_speed"],
                )
                self._manager.motionbricks_status = "已生成路线行走，可在动作库播放。"
            else:
                if self._rig.matrix_world != self._rig_world:
                    raise ValueError("任务运行时角色的物体变换已改变，请重新生成。")
                if scene_fps(scene) != self._generation_fps:
                    raise ValueError("任务运行时工程帧率已改变，请重新生成。")
                start = self._generation_start
                fps = self._generation_fps
                arrival_frames = [start + result["times"][end] * fps for end in result["segment_ends"]]
                if arrival_frames[-1] > 1048574:
                    raise ValueError("生成结果超出 Blender 的最大帧，请提早起始帧。")
                action = adapter.bake("MotionBricks 补间", result["poses"], result["times"], start, fps)
                try:
                    for number, (name, frame) in enumerate(zip(self._target_names, arrival_frames, strict=True), start=1):
                        marker = action.pose_markers.new(f"{number}. {name}")
                        marker.frame = round(frame)
                    stop_preview(context)
                    if self._generation_output == "NLA":
                        timeline.add_to_nla(adapter, action)
                    else:
                        adapter.assign_action(action)
                except Exception:
                    bpy.data.actions.remove(action)
                    raise
                set_frame(scene, start)
                add_library_action(state, adapter, action, "generated", result["times"][-1], adapter.sample_rate,
                                   arrival_frames=arrival_frames)
                state.last_generated = action
                destination = " · NLA" if self._generation_output == "NLA" else ""
                self._manager.motionbricks_status = f"已生成 {len(arrival_frames)} 段 · 末帧 {arrival_frames[-1]:.2f}{destination}"
            return {"FINISHED"}
        except (KeyError, OSError, ReferenceError, ValueError, RuntimeError) as error:
            return self._failed(error)
        finally:
            self._cleanup()

    def _failed(self, error):
        message = str(error)
        self._manager.motionbricks_status = message.splitlines()[0] if message else "MotionBricks 任务失败。"
        self.report({"ERROR"}, message)
        return {"CANCELLED"}

    def _cleanup(self):
        global _active_operator
        if self._timer is not None:
            self._manager.event_timer_remove(self._timer)
            self._timer = None
        if self._job is not None:
            self._job.close()
            self._job = None
        if _active_operator is self:
            _active_operator = None


class MOTIONBRICKS_OT_import_recording(Operator, ImportHelper):
    bl_idname = "motionbricks.import_recording"
    bl_label = "导入 MotionBricks 录制"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".npz"
    filter_glob: StringProperty(default="*.npz", options={"HIDDEN"})

    def execute(self, context):
        global _active_operator
        if context.scene.motionbricks.pose_edit.saved:
            self.report({"WARNING"}, "请先保存或取消骨骼编辑。")
            return {"CANCELLED"}
        if _active_operator is not None:
            self.report({"WARNING"}, "已有一个 MotionBricks 任务在运行。")
            return {"CANCELLED"}

        self._bake = None
        self._timer = None
        self._manager = context.window_manager
        self._scene_id = context.scene.as_pointer()
        self._cancel_requested = False
        try:
            if context.mode not in {"OBJECT", "POSE"}:
                raise ValueError("请切换到物体或姿态模式后导入录制。")
            adapter = RigAdapter(working_rig(context))
            path = Path(bpy.path.abspath(self.filepath))
            source = recording.load_recording(path, json.loads(adapter.rig[RIG_DATA]))
            times = [index / source.fps for index in range(len(source.poses))]
            self._bake = ActionBake(adapter, source.poses, times, 1.0, scene_fps(context.scene), clip_space=True)
            self._file = path.name
            self._duration = source.duration
            self._fps = source.fps
            self._scene_fps = scene_fps(context.scene)
            if bpy.app.background:
                self._bake.advance(len(times))
                return self._complete(context)
            self._timer = self._manager.event_timer_add(0.03, window=context.window)
            self._manager.modal_handler_add(self)
            _active_operator = self
            self._manager.motionbricks_status = "正在准备录制的动作通道…"
            return {"RUNNING_MODAL"}
        except (OSError, ValueError, RuntimeError, OverflowError) as error:
            return self._failed(error)

    def modal(self, context, event):
        if _active_operator is not self:
            return {"CANCELLED"}
        if event.type == "ESC" or self._cancel_requested:
            self._manager.motionbricks_status = "录制导入已取消。"
            self._cleanup()
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        try:
            if context.scene is None or context.scene.as_pointer() != self._scene_id:
                raise ValueError("场景已切换，录制导入已取消。")
            rig = self._bake.adapter.rig
            if rig.name not in context.scene.objects or rig.mode == "EDIT":
                raise ValueError("参考角色已移出场景或进入编辑模式，录制导入已取消。")
            if self._bake.advance(32):
                return self._complete(context)
            self._manager.motionbricks_status = f"正在导入录制 {self._bake.prepared}/{len(self._bake.frames)} 帧"
            if context.area:
                context.area.tag_redraw()
            return {"PASS_THROUGH"}
        except (OSError, ReferenceError, ValueError, RuntimeError, OverflowError) as error:
            return self._failed(error)

    def _complete(self, context):
        baked_for = self._bake.adapter
        adapter = RigAdapter(baked_for.rig)
        if (adapter.signature != baked_for.signature or adapter.layout_signature != baked_for.layout_signature
                or scene_fps(context.scene) != self._scene_fps):
            raise ValueError("录制导入期间参考骨架或工程帧率已改变，请重新导入。")
        action = self._bake.write(Path(self._file).stem)
        try:
            add_library_action(
                context.scene.motionbricks, adapter, action, "recording", self._duration, self._fps,
                source_file=self._file, format_version=recording.VERSION,
            )
        except Exception:
            bpy.data.actions.remove(action)
            raise
        self._manager.motionbricks_status = f"已导入录制「{action.name}」，可在动作库预览。"
        self._cleanup()
        return {"FINISHED"}

    def _failed(self, error):
        self._cleanup()
        self._manager.motionbricks_status = str(error)
        self.report({"ERROR"}, str(error))
        return {"CANCELLED"}

    def _cleanup(self):
        global _active_operator
        timer = getattr(self, "_timer", None)
        if timer is not None:
            self._manager.event_timer_remove(timer)
            self._timer = None
        self._bake = None
        if _active_operator is self:
            _active_operator = None

    def cancel(self, _context):
        self._cleanup()


class MOTIONBRICKS_OT_export_recording(Operator, ExportHelper):
    bl_idname = "motionbricks.export_recording"
    bl_label = "导出 MotionBricks 录制"
    filename_ext = ".npz"

    filter_glob: StringProperty(default="*.npz", options={"HIDDEN"})
    fps: IntProperty(name="导出帧率", default=30, min=1, max=240)

    @classmethod
    def poll(cls, context):
        if context.scene is None or _active_operator is not None or not rig_ready(context):
            return False
        state = context.scene.motionbricks
        return not state.pose_edit.saved and library_selection(state) is not None

    def invoke(self, context, event):
        action = library_selection(context.scene.motionbricks)
        if not self.properties.is_property_set("fps"):
            source_fps = action.get(LIBRARY_DATA, {}).get("source_fps", 30)
            self.fps = min(240, max(1, round(source_fps)))
        return super().invoke(context, event)

    def draw(self, _context):
        self.layout.prop(self, "fps")

    def execute(self, _context):
        try:
            result = bpy.ops.motionbricks.job(operation="export", filepath=self.filepath, export_fps=self.fps)
        except RuntimeError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"} if "RUNNING_MODAL" in result else result


class MMDBridgeOperator:
    @classmethod
    def poll(cls, context):
        if context.scene is None or _active_operator is not None:
            return False
        state = context.scene.motionbricks
        return (context.mode in {"OBJECT", "POSE"} and not state.pose_edit.saved
                and mmd_bridge.is_mmd_rig(state.mmd_target) and rig_in_view(context, state.mmd_target)
                and rig_ready(context))

    def run(self, context, *, exporting):
        state = context.scene.motionbricks
        try:
            adapter = RigAdapter(working_rig(context))
            if not self.filepath:
                raise ValueError("请选择 VMD 文件。")
            filename = bpy.path.ensure_ext(self.filepath, ".vmd") if exporting else self.filepath
            path = Path(bpy.path.abspath(filename))
            if exporting:
                clip = state.clips[state.clip_index]
                action = clip.action
                if action is None and clip.path:
                    key = bvh_cache_key(Path(clip.path), adapter, scene_fps(context.scene))
                    action = next((item for item in bpy.data.actions if item.get("mb_clip") == key), None)
                    if action is None:
                        raise ValueError("请先在动作库播放一次选中的 BVH 片段。")
                else:
                    metadata = action.get(LIBRARY_DATA, {}) if action else {}
                    if metadata.get("signature") != adapter.signature or metadata.get("layout") != adapter.layout_signature:
                        raise ValueError("请在动作库选中与 SOMA 骨架匹配的 Action。")
                frames, bones = mmd_bridge.export_vmd(context, adapter, action, state.mmd_target, path, state.mmd_scale)
                status = f"已导出 VMD · {frames} 帧 / {bones} 根骨骼"
            else:
                poses = mmd_bridge.read_vmd(context, adapter, state.mmd_target, path, state.mmd_scale)
                times = [index / mmd_bridge.FPS for index in range(len(poses))]
                action = adapter.bake(path.stem, poses, times, 1, scene_fps(context.scene), clip_space=True)
                add_library_action(state, adapter, action, "vmd", times[-1], mmd_bridge.FPS, source_file=path.name)
                status = f"已将 {path.name} 转成 SOMA 动作"
        except (OSError, ValueError, RuntimeError) as error:
            self.report({"ERROR"}, str(error) or "VMD 文件无法读取。")
            return {"CANCELLED"}
        context.window_manager.motionbricks_status = status
        self.report({"INFO"}, status)
        return {"FINISHED"}


class MOTIONBRICKS_OT_export_vmd(MMDBridgeOperator, Operator, ExportHelper):
    bl_idname = "motionbricks.export_vmd"
    bl_label = "导出 MMD 动作 (.vmd)"
    filename_ext = ".vmd"
    filter_glob: StringProperty(default="*.vmd", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        if not super().poll(context):
            return False
        state = context.scene.motionbricks
        return 0 <= state.clip_index < len(state.clips)

    def invoke(self, context, event):
        if not self.filepath:
            state = context.scene.motionbricks
            title = state.clips[state.clip_index].title
            self.filepath = title.translate({ord("/"): "_", 92: "_"}) + ".vmd"
        return super().invoke(context, event)

    def execute(self, context):
        return self.run(context, exporting=True)


class MOTIONBRICKS_OT_import_vmd(MMDBridgeOperator, Operator, ImportHelper):
    bl_idname = "motionbricks.import_vmd"
    bl_label = "导入 MMD 动作 (.vmd)"
    bl_options = {"UNDO"}
    filename_ext = ".vmd"
    filter_glob: StringProperty(default="*.vmd", options={"HIDDEN"})

    def execute(self, context):
        return self.run(context, exporting=False)


class MOTIONBRICKS_OT_cancel(Operator):
    bl_idname = "motionbricks.cancel"
    bl_label = "取消任务"

    def execute(self, _context):
        if _active_operator is not None:
            _active_operator._cancel_requested = True
        return {"FINISHED"}


class MOTIONBRICKS_OT_remove_library_action(Operator):
    bl_idname = "motionbricks.remove_library_action"
    bl_label = "从动作库移除"
    bl_description = "移除列表条目，Action 仍保存在当前 Blender 工程中"
    bl_options = {"UNDO"}

    def execute(self, context):
        state = context.scene.motionbricks
        index = state.clip_index
        if _active_operator is not None or not 0 <= index < len(state.clips) or state.clips[index].path:
            return {"CANCELLED"}
        state.clips.remove(index)
        flags = clip_library.filter_flags(state, 1)
        candidates = [i for i, visible in enumerate(flags) if visible]
        choose_library_index(state, min(candidates, key=lambda i: abs(i - index), default=-1))
        return {"FINISHED"}


class MOTIONBRICKS_OT_stop_preview(Operator):
    bl_idname = "motionbricks.stop_preview"
    bl_label = "退出动作预览"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if context.scene.motionbricks.pose_edit.saved:
            return {"CANCELLED"}
        cancel_clip_autoplay()
        stop_preview(context)
        context.window_manager.motionbricks_status = "已退出动作预览。"
        return {"FINISHED"}


class MOTIONBRICKS_OT_step_preview(Operator):
    bl_idname = "motionbricks.step_preview"
    bl_label = "逐帧预览"
    bl_description = "暂停并移动到相邻的工程帧"

    step: IntProperty(default=1, min=-1, max=1, options={"SKIP_SAVE"})

    def execute(self, context):
        scene = context.scene
        if scene.motionbricks.pose_edit.saved:
            return {"CANCELLED"}
        action = preview_action(scene.motionbricks)
        if action is None:
            return {"CANCELLED"}
        pause_playback(context)
        start, end = action.frame_range
        frame = scene.frame_current + scene.frame_subframe
        next_frame = math.ceil(frame) - 1 if self.step < 0 else math.floor(frame) + 1
        set_frame(scene, min(end, max(start, next_frame)))
        return {"FINISHED"}


class MOTIONBRICKS_OT_save_pose(Operator):
    bl_idname = "motionbricks.save_pose"
    bl_label = "保存当前姿势"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="姿势名称", default="SOMA 姿势")
    destination: EnumProperty(items=(
        ("ASSET", "姿势资产", "保存到姿势资产"),
        ("START", "起始姿势", "保存并设为补间的起点"),
        ("TARGET", "目标姿势", "保存并添加到目标序列末尾"),
    ), default="ASSET", options={"SKIP_SAVE"})
    include_motion: BoolProperty(
        name="同时采样相邻帧", default=False,
        description="按模型的 30 Hz 采样动作前后三帧，用于保留动势。手动摆的静态姿势可留空",
    )

    def draw(self, context):
        self.layout.prop(self, "name")
        self.layout.prop(self, "include_motion")
        state = context.scene.motionbricks
        self.layout.label(text=f"保存分组 · {pose_library.group_label(state.pose_group_filter or pose_library.UNGROUPED)}")

    def invoke(self, context, _event):
        if self.destination != "ASSET":
            return self.execute(context)
        pause_playback(context)
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        scene = context.scene
        state = scene.motionbricks
        if _active_operator is not None or state.pose_edit.saved:
            return {"CANCELLED"}
        try:
            pause_playback(context)
            adapter = RigAdapter(working_rig(context))
            action = preview_action(state)
            quick = self.destination != "ASSET"
            if quick and (action is None or state.preview_rig != adapter.rig):
                raise ValueError("请先播放一个动作，再从当前帧取姿势。")
            fps = scene_fps(scene)
            if quick:
                first, last = action.frame_range
                frame = min(last, max(first, scene.frame_current + scene.frame_subframe))
                set_frame(scene, frame)
                name = f"{action.name} · {(frame - first) / fps:.3f}s"
            else:
                name = self.name.strip() or "SOMA 姿势"
            snapshot = snapshot_pose(context, adapter, state.capture_motion if quick else self.include_motion)
            pose = make_pose_asset(adapter, name, snapshot, fps)
            pose_library.save_to_current_group(pose, state)
            state.pose = pose
            if quick:
                put_pose_in_sequence(state, pose, self.destination, moving=state.capture_motion)
                sequence_preview.redraw()
            destination = {"START": "，已设为起始", "TARGET": "，已加入目标序列"}.get(self.destination, "")
            context.window_manager.motionbricks_status = f"已保存姿势「{pose.name}」{destination}。"
            return {"FINISHED"}
        except (ValueError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


def put_pose_in_sequence(state, pose, destination, *, moving=None):
    if destination == "START":
        if state.start != pose:
            state.start = pose
        if moving is not None:
            state.start_moving = moving
        state.target_index = -1
    else:
        target = state.targets.add()
        target.pose = pose
        if moving is not None:
            target.moving = moving
        state.target_index = len(state.targets) - 1


class MOTIONBRICKS_OT_sequence(Operator):
    bl_idname = "motionbricks.sequence"
    bl_label = "编辑目标序列"
    bl_options = {"REGISTER", "UNDO"}

    operation: EnumProperty(items=[
        ("apply", "应用姿势", "在当前角色上应用所选姿势"),
        ("start", "设为起始", "将所选姿势用作补间的起点"),
        ("add", "添加为目标", "将所选姿势添加到目标序列末尾"),
        ("replace", "替换目标", "用「姿势资产」中选中的姿势替换当前目标"),
        ("remove", "删除目标", "从序列中移除当前目标"),
        ("up", "上移", "将当前目标提前一项"),
        ("down", "下移", "将当前目标推后一项"),
    ], options={"SKIP_SAVE"})

    @classmethod
    def description(cls, _context, properties):
        operation = bpy.ops.motionbricks.sequence.get_rna_type().properties["operation"]
        return operation.enum_items[properties.operation].description

    def execute(self, context):
        state = context.scene.motionbricks
        if state.pose_edit.saved:
            return {"CANCELLED"}
        index = state.target_index
        try:
            if self.operation in {"apply", "start", "add", "replace"}:
                if state.pose is None:
                    raise ValueError("请先选择一个姿势资产。")
                if self.operation == "apply":
                    pause_playback(context)
                    adapter = RigAdapter(working_rig(context))
                    adapter.apply_pose(read_pose_asset(state.pose, adapter.signature)["pose"])
                    context.view_layer.update()
                elif self.operation == "start":
                    put_pose_in_sequence(state, state.pose, "START")
                elif self.operation == "add":
                    put_pose_in_sequence(state, state.pose, "TARGET")
                elif 0 <= index < len(state.targets):
                    target = state.targets[index]
                    position = placement.location(target.pose, target.placement)
                    target.pose = state.pose
                    if position is not None:
                        placement.set_location(target.pose, target.placement, position)
            elif 0 <= index < len(state.targets):
                if self.operation == "remove":
                    state.targets.remove(index)
                    state.target_index = max(0, min(index, len(state.targets) - 1))
                else:
                    destination = index + (-1 if self.operation == "up" else 1)
                    if 0 <= destination < len(state.targets):
                        state.targets.move(index, destination)
                        state.target_index = destination
            return {"FINISHED"}
        except (ValueError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


class MOTIONBRICKS_OT_clip_directory(Operator):
    bl_idname = "motionbricks.clip_directory"
    bl_label = "筛选动作目录"
    directory: StringProperty(options={"SKIP_SAVE"})

    def execute(self, context):
        context.scene.motionbricks.clip_directory = self.directory
        return {"FINISHED"}


class MOTIONBRICKS_MT_clip_directories(Menu):
    bl_label = "动作目录"

    def draw(self, context):
        state = context.scene.motionbricks
        layout = self.layout
        layout.operator("motionbricks.clip_directory", text="全部目录").directory = ""
        for directory in clip_library.directories(state.clips):
            layout.operator("motionbricks.clip_directory", text=clip_library.directory_label(directory),
                            icon="FILE_FOLDER").directory = directory


class MOTIONBRICKS_UL_clips(UIList):
    def filter_items(self, _context, data, property_name):
        clips = getattr(data, property_name)
        helper = bpy.types.UI_UL_list
        flags = clip_library.filter_flags(data, self.bitflag_filter_item)
        order = helper.sort_items_by_name(clips, "title") if self.use_filter_sort_alpha else []
        return flags, order

    def draw_filter(self, _context, layout):
        layout.prop(self, "use_filter_sort_alpha", text="按名称排序")

    def draw_item(self, _context, layout, _data, clip, _icon, _active_data, _active_property, _index=0, _filter_flag=0):
        row = layout.split(factor=0.72, align=True)
        row.label(text=clip.action.name if clip.action else clip.title, icon="ACTION" if clip.action else "FILE")
        if clip.duration >= 0:
            duration = row.row()
            duration.alignment = "RIGHT"
            duration.label(text=f"{clip.duration:.2f} s")


class MOTIONBRICKS_OT_preview_sequence(Operator):
    bl_idname = "motionbricks.preview_sequence"
    bl_label = "在视口预览序列"
    frame_only: BoolProperty(default=False, options={"SKIP_SAVE"})

    @classmethod
    def description(cls, _context, properties):
        if properties.frame_only:
            return "将序列中的全部姿势框进视口"
        return "开关起始姿势和各个目标的视口预览"

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"

    def execute(self, context):
        if context.scene.motionbricks.pose_edit.saved:
            return {"CANCELLED"}
        try:
            if not self.frame_only:
                sequence_preview.toggle(context, working_rig(context))
            sequence_preview.frame(context)
            return {"FINISHED"}
        except (KeyError, TypeError, ValueError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


class MOTIONBRICKS_OT_select_sequence_pose(Operator):
    bl_idname = "motionbricks.select_sequence_pose"
    bl_label = "选中序列姿势"

    index: IntProperty(min=0, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return (_active_operator is None and context.area is not None and context.area.type == "VIEW_3D"
                and context.mode in {"OBJECT", "POSE"} and rig_ready(context)
                and not context.scene.motionbricks.pose_edit.saved)

    def execute(self, context):
        state = context.scene.motionbricks
        slots = placement.slots(state)
        if self.index >= len(slots) or slots[self.index][0] is None:
            return {"CANCELLED"}
        state.target_index = self.index - 1
        placement_changed(state, context)
        return {"FINISHED"}


class MOTIONBRICKS_OT_reset_placement(Operator):
    bl_idname = "motionbricks.reset_placement"
    bl_label = "恢复原位"
    bl_description = "恢复选中姿势资产的原始位置与朝向"
    bl_options = {"UNDO"}

    def execute(self, context):
        state = context.scene.motionbricks
        index = placement.selected_index(state)
        if _active_operator is not None or state.pose_edit.saved or index < 0:
            return {"CANCELLED"}
        action, item = placement.slots(state)[index]
        if action is None:
            return {"CANCELLED"}
        item.offset = (0.0, 0.0, 0.0)
        item.yaw = 0.0
        return {"FINISHED"}


class MOTIONBRICKS_OT_edit_pose(Operator):
    bl_idname = "motionbricks.edit_pose"
    bl_label = "编辑骨骼"
    bl_options = {"UNDO"}

    operation: EnumProperty(items=(
        ("begin", "编辑骨骼", "在真实 SOMA 骨架上编辑选中的序列姿势"),
        ("save", "保存姿势", "将骨骼调整另存为新资产，并替换当前序列项"),
        ("cancel", "取消编辑", "放弃本次骨骼调整"),
    ), options={"SKIP_SAVE"})

    def execute(self, context):
        state = context.scene.motionbricks
        edit = state.pose_edit
        if _active_operator is not None or (self.operation == "begin") == bool(edit.saved):
            return {"CANCELLED"}
        try:
            pause_playback(context)
            if self.operation == "begin":
                if context.mode not in {"OBJECT", "POSE"}:
                    raise ValueError("请先退出骨架编辑模式。")
                adapter = RigAdapter(working_rig(context))
                if not selectable_soma_rigs(context, [adapter.rig]):
                    raise ValueError("请先在视口显示工作骨架并允许选择。")
                if state.mmd_preview.saved:
                    stop_preview(context)
                if context.object != adapter.rig:
                    if context.mode == "POSE":
                        bpy.ops.object.mode_set(mode="OBJECT")
                    activate_soma(context, adapter.rig)
                pose_edit.begin(context, adapter)
            elif self.operation == "save":
                action = pose_edit.save(context)
                context.window_manager.motionbricks_status = f"已保存姿势「{action.name}」。"
            else:
                pose_edit.restore(context)
                context.window_manager.motionbricks_status = "已取消骨骼编辑。"
            return {"FINISHED"}
        except (ValueError, RuntimeError, ReferenceError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


class MOTIONBRICKS_UL_targets(UIList):
    def draw_item(self, _context, layout, _data, target, _icon, _active_data, _active_property, index=0, _filter_flag=0):
        row = layout.split(factor=0.43, align=True)
        row.label(text=f"{index + 1}. {target.pose.name if target.pose else '缺少姿势'}")
        position = row.row()
        position.alignment = "RIGHT"
        position.label(text=position_text(placement.location(target.pose, target.placement), compact=True))


def has_soma_binding(obj):
    return obj is not None and obj.type == "ARMATURE" and RIG_DATA in obj


def is_soma_rig(obj):
    return has_soma_binding(obj) and obj.get(RIG_REVISION) == CURRENT_BIND_REVISION


def rig_in_view(context, rig):
    return rig is not None and context.view_layer.objects.get(rig.name) == rig


def working_rig(context):
    state = context.scene.motionbricks
    if state.work_rig is not None:
        return state.work_rig if is_soma_rig(state.work_rig) and rig_in_view(context, state.work_rig) else None
    if is_soma_rig(state.mmd_source) and rig_in_view(context, state.mmd_source):
        return state.mmd_source
    rigs = [obj for obj in context.view_layer.objects if is_soma_rig(obj)]
    if len(rigs) == 1:
        return rigs[0]
    return None


def rig_ready(context):
    return working_rig(context) is not None


def activate_soma(context, rig):
    rig.select_set(True)
    if not rig.select_get():
        raise ValueError("请在大纲视图中启用 SOMA 骨架的选择。")
    for obj in context.selected_objects:
        if obj != rig:
            obj.select_set(False)
    context.view_layer.objects.active = rig


def selectable_soma_rigs(context, rigs):
    layer = context.view_layer
    space = context.space_data
    viewport = space if space is not None and space.type == "VIEW_3D" else None
    return [rig for rig in rigs if layer.objects.get(rig.name) == rig
            and not rig.hide_select and rig.visible_get(view_layer=layer, viewport=viewport)]


class MOTIONBRICKS_OT_select_soma(Operator):
    bl_idname = "motionbricks.select_soma"
    bl_label = "选中现有 SOMA 骨架"
    bl_description = "将现有 SOMA 骨架设为活动对象，恢复动作补间和姿势编辑"

    @classmethod
    def poll(cls, context):
        if context.mode != "OBJECT":
            cls.poll_message_set("请先切换到物体模式。")
            return False
        return (context.scene is not None and _active_operator is None
                and not context.scene.motionbricks.pose_edit.saved)

    def execute(self, context):
        rig = working_rig(context)
        if rig is None or not selectable_soma_rigs(context, [rig]):
            self.report({"WARNING"}, "请先设置工作骨架，并在视口中显示它。")
            return {"CANCELLED"}
        try:
            activate_soma(context, rig)
        except ValueError as error:
            self.report({"WARNING"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class MotionBricksPanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MotionBricks"

    @classmethod
    def poll(cls, context):
        return not context.scene.motionbricks.pose_edit.saved


class MOTIONBRICKS_PT_main(MotionBricksPanel, Panel):
    bl_label = "MotionBricks 动作补间"

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    def draw(self, context):
        layout = self.layout
        edit = context.scene.motionbricks.pose_edit
        if edit.saved:
            layout.label(text=f"正在编辑 · {edit.source.name if edit.source else '姿势'}", icon="BONE_DATA")
            if edit.can_keep_motion:
                layout.prop(edit, "keep_motion")
            else:
                layout.label(text="保存为单帧姿势")
            row = layout.row(align=True)
            row.operator("motionbricks.edit_pose", text="保存姿势", icon="CHECKMARK").operation = "save"
            row.operator("motionbricks.edit_pose", text="取消", icon="CANCEL").operation = "cancel"
            return
        status = context.window_manager.motionbricks_status
        if status:
            layout.label(text=status, icon="INFO")
        if _active_operator is not None:
            layout.operator("motionbricks.cancel", icon="CANCEL")
        else:
            state = context.scene.motionbricks
            row = layout.row(align=True)
            row.prop(state, "work_rig")
            rig = working_rig(context)
            if rig is not None:
                if state.work_rig != rig:
                    layout.label(text=f"当前使用 {rig.name}", icon="ARMATURE_DATA")
                if context.object != rig and selectable_soma_rigs(context, [rig]):
                    row.operator("motionbricks.select_soma", text="", icon="RESTRICT_SELECT_OFF")
            elif any(is_soma_rig(obj) for obj in context.scene.objects):
                layout.label(text="请选择当前视图层中的 SOMA 工作骨架", icon="INFO")
            else:
                if any(has_soma_binding(obj) for obj in context.scene.objects):
                    layout.label(text="现有 SOMA 使用旧版绑定", icon="ERROR")
                else:
                    layout.label(text="此场景还没有 SOMA 参考骨架", icon="INFO")
                configured = layout.column()
                configured.enabled = bool((context.preferences.addons.get(__package__).preferences.repository
                                           if context.preferences.addons.get(__package__) else default_backend()))
                configured.operator("motionbricks.job", text="创建 SOMA 参考角色", icon="ARMATURE_DATA").operation = "rig"


class MOTIONBRICKS_PT_sequence(MotionBricksPanel, Panel):
    bl_label = "目标序列"
    bl_parent_id = "MOTIONBRICKS_PT_main"
    bl_order = 10

    @classmethod
    def poll(cls, context):
        return super().poll(context) and (rig_ready(context) or sequence_preview.active(context.scene))

    def draw_header(self, _context):
        self.layout.label(icon="SORT_ASC")

    def draw(self, context):
        layout = self.layout
        state = context.scene.motionbricks
        layout.enabled = _active_operator is None and not state.pose_edit.saved
        ready = rig_ready(context)
        previewing = sequence_preview.active(context.scene)

        actions = layout.row(align=True)
        actions.scale_y = 1.2
        preview = actions.row(align=True)
        preview.enabled = previewing or (ready and (state.start is not None or any(t.pose for t in state.targets)))
        preview.operator("motionbricks.preview_sequence", text="视口预览", icon="HIDE_OFF", depress=previewing)
        if previewing:
            preview.operator("motionbricks.preview_sequence", text="", icon="VIEWZOOM").frame_only = True
        generate = actions.row(align=True)
        generate.enabled = ready and state.start is not None and bool(state.targets) and all(t.pose for t in state.targets)
        generate.operator("motionbricks.job", text="生成补间", icon="PLAY").operation = "generate"
        if previewing and not context.space_data.overlay.show_overlays:
            layout.label(text="请开启视口叠加层", icon="INFO")
        if previewing:
            layout.prop(state, "isolate_pose")

        if not ready:
            return

        header, options = layout.panel("motionbricks_generation_settings", default_closed=True)
        header.label(text="生成设置")
        if options is not None:
            options.prop(state, "generation_start")
            options.prop(state, "generation_output")
            latest = state.last_generated
            if latest is not None:
                arrivals = latest.get(LIBRARY_DATA, {}).get("arrival_frames", ())
                options.label(text="最近生成的到达帧", icon="MARKER")
                for marker, frame in zip(latest.pose_markers, arrivals):
                    row = options.split(factor=0.66)
                    row.label(text=marker.name)
                    value = row.row()
                    value.alignment = "RIGHT"
                    value.label(text=f"{frame:.2f}")

        sequence = layout.column(align=True)
        start = sequence.row(align=True)
        start.prop(state, "start", text="起始")
        if state.start is not None:
            start.operator(
                "motionbricks.select_sequence_pose", text="", icon="PIVOT_CURSOR",
                depress=placement.selected_index(state) == 0,
            ).index = 0
            position = placement.location(state.start, state.start_placement)
            sequence.label(text=f"坐标  {position_text(position)} m")
        if state.targets:
            sequence.label(text="目标 · 世界坐标 X/Y/Z（米）")
            sequence.template_list("MOTIONBRICKS_UL_targets", "", state, "targets", state, "target_index", rows=3)
            index = state.target_index
            controls = sequence.row(align=True)
            controls.enabled = 0 <= index < len(state.targets)
            replace = controls.row(align=True)
            replace.enabled = state.pose is not None
            replace.operator("motionbricks.sequence", text="替换").operation = "replace"
            controls.operator("motionbricks.sequence", text="删除", icon="REMOVE").operation = "remove"
            up = controls.row(align=True)
            up.enabled = index > 0
            up.operator("motionbricks.sequence", text="", icon="TRIA_UP").operation = "up"
            down = controls.row(align=True)
            down.enabled = index < len(state.targets) - 1
            down.operator("motionbricks.sequence", text="", icon="TRIA_DOWN").operation = "down"
        else:
            sequence.label(text="从「姿势资产」添加目标", icon="INFO")
        index = placement.selected_index(state)
        if index >= 0 and placement.slots(state)[index][0] is not None:
            layout.operator("motionbricks.edit_pose", icon="BONE_DATA").operation = "begin"


class MOTIONBRICKS_PT_target_details(MotionBricksPanel, Panel):
    bl_label = "选中姿势摆位"
    bl_parent_id = "MOTIONBRICKS_PT_sequence"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        state = context.scene.motionbricks
        return super().poll(context) and rig_ready(context) and placement.selected_index(state) >= 0

    def draw(self, context):
        layout = self.layout
        layout.enabled = _active_operator is None
        state = context.scene.motionbricks
        index = placement.selected_index(state)
        slots = placement.slots(state)
        action, item = slots[index]
        position = placement.location(action, item)
        title = str(index) if index else "起始"
        layout.label(text=f"{title} · {action.name if action else '缺少姿势'}")
        if position is None:
            return
        layout.label(text="世界坐标 X / Y / Z（米）")
        if index:
            target = state.targets[index - 1]
            layout.prop(target, "world_position", text="")
            previous_position = placement.location(*slots[index - 1])
            if previous_position is not None:
                delta = tuple(current - last for current, last in zip(position, previous_position, strict=True))
                layout.label(text=f"较上一项  {position_text(delta)} m")
        else:
            layout.prop(state, "start_world_position", text="")
        layout.prop(item, "yaw")
        if index:
            layout.prop(state.targets[index - 1], "duration_preference")
        row = layout.row(align=True)
        row.prop(state.targets[index - 1] if index else state, "moving" if index else "start_moving")
        row.operator("motionbricks.reset_placement", icon="LOOP_BACK")


class MOTIONBRICKS_PT_assets(MotionBricksPanel, Panel):
    bl_label = "姿势资产"
    bl_parent_id = "MOTIONBRICKS_PT_main"
    bl_order = 20
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return super().poll(context) and rig_ready(context)

    def draw_header(self, _context):
        self.layout.label(icon="ASSET_MANAGER")

    def draw(self, context):
        layout = self.layout
        layout.enabled = _active_operator is None
        state = context.scene.motionbricks
        pose_library.draw_browser(layout, state)
        if state.pose is not None:
            layout.label(text=f"根节点  {position_text(pose_root_position(state.pose))} m")
        row = layout.row(align=True)
        row.enabled = state.pose is not None
        for operation, text in (("apply", "应用"), ("start", "设为起始"), ("add", "添加目标")):
            row.operator("motionbricks.sequence", text=text).operation = operation
        layout.operator("motionbricks.save_pose", icon="POSE_HLT")


class MOTIONBRICKS_PT_clips(MotionBricksPanel, Panel):
    bl_label = "动作库"
    bl_parent_id = "MOTIONBRICKS_PT_main"
    bl_order = 30
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, _context):
        self.layout.label(icon="FILE_FOLDER")

    def draw(self, context):
        layout = self.layout
        layout.enabled = _active_operator is None
        state = context.scene.motionbricks
        ready = rig_ready(context)
        selected = 0 <= state.clip_index < len(state.clips)
        toolbar = layout.row(align=True)
        import_row = toolbar.row(align=True)
        import_row.enabled = ready
        import_row.operator("motionbricks.import_recording", text="导入录制 (.npz)", icon="IMPORT")
        if selected and not state.clips[state.clip_index].path:
            toolbar.operator("motionbricks.remove_library_action", text="", icon="X")
        if state.clips:
            layout.prop(state, "clip_search", text="", icon="VIEWZOOM", placeholder="搜索动作")
            filters = layout.row(align=True)
            filters.prop(state, "clip_source", text="")
            directories = filters.row(align=True)
            directories.enabled = state.clip_source in {"ALL", "BVH"} and any(clip.path for clip in state.clips)
            directory = state.clip_directory if directories.enabled else ""
            directories.menu("MOTIONBRICKS_MT_clip_directories",
                             text=clip_library.directory_label(directory) if directory else "全部目录")
            layout.prop(state, "clip_autoplay")
            layout.template_list(
                "MOTIONBRICKS_UL_clips", "", state, "clips", state, "clip_index",
                item_dyntip_propname="title", rows=5,
            )
            if not any(clip_library.filter_flags(state, 1)):
                layout.label(text="没有匹配的动作", icon="INFO")
            if selected:
                clip = state.clips[state.clip_index]
                if clip.action is not None:
                    metadata = clip.action.get(LIBRARY_DATA, {})
                    if metadata.get("kind") in {"recording", "vmd"}:
                        layout.label(text=metadata.get("source_file", "NPZ 录制"), icon="FILE")
                    elif metadata.get("kind") == "route":
                        layout.label(text=f"终点误差 {metadata['end_error']:.3f} 米", icon="CURVE_DATA")
                        layout.label(text=f"停步速度 {metadata['stop_speed']:.3f} 米/秒")
                    else:
                        layout.label(text="MotionBricks 生成结果", icon="ACTION")
                    layout.operator("motionbricks.export_recording", text="导出录制 (.npz)", icon="EXPORT")
                elif clip.path and clip.category != ".":
                    layout.label(text=clip_library.directory_label(clip.category), icon="FILE_FOLDER")
        else:
            layout.label(text="选择目录或载入内置样例", icon="INFO")

        if state.preview_rig or selected:
            play = layout.row(align=True)
            if state.preview_rig:
                playing = context.screen.is_animation_playing
                play.operator("screen.animation_play", text="暂停" if playing else "继续", icon="PAUSE" if playing else "PLAY")
                play.operator("motionbricks.stop_preview", text="退出", icon="CANCEL")
            if selected:
                preview = play.row(align=True)
                preview.enabled = ready and bool(clip.path or clip.action is not None)
                preview.operator("motionbricks.job", text="播放片段", icon="ACTION" if state.preview_rig else "PLAY").operation = "clip"
        if state.preview_rig:
            action = preview_action(state)
            if action is not None:
                scene = context.scene
                frame = scene.frame_current + scene.frame_subframe
                first, last = action.frame_range
                fps = scene_fps(scene)
                elapsed = (min(last, max(first, frame)) - first) / fps
                source_fps = action.get(LIBRARY_DATA, {}).get("source_fps", action.get("mb_clip_source_fps"))
                info = f"{elapsed:.2f} / {(last - first) / fps:.2f} 秒 · 第 {frame:g} 帧"
                if source_fps is not None:
                    info += f" · 源 {source_fps:g} fps"
                layout.label(text=action.name, icon="ACTION")
                layout.label(text=info, icon="TIME")
                scrub = layout.row()
                scrub.enabled = last > first
                scrub.prop(state, "preview_progress", text="进度", slider=True)
            capture = layout.row()
            capture.enabled = action is not None
            steps = capture.row(align=True)
            steps.operator("motionbricks.step_preview", text="", icon="TRIA_LEFT").step = -1
            steps.operator("motionbricks.step_preview", text="", icon="TRIA_RIGHT").step = 1
            save = capture.row()
            save.enabled = ready and working_rig(context) == state.preview_rig
            save.operator("motionbricks.save_pose", text="另存姿势", icon="POSE_HLT").destination = "ASSET"
            quick = layout.row(align=True)
            quick.enabled = action is not None and ready and working_rig(context) == state.preview_rig
            quick.operator("motionbricks.save_pose", text="保存为起始").destination = "START"
            quick.operator("motionbricks.save_pose", text="保存并添加为目标").destination = "TARGET"
            layout.prop(state, "capture_motion")

        header, source = layout.panel("motionbricks_clip_source", default_closed=any(clip.path for clip in state.clips))
        header.label(text="BVH 动作包")
        if source is not None:
            source.prop(state, "directory", text="")
            row = source.row(align=True)
            scan = row.row(align=True)
            scan.enabled = bool(state.directory)
            scan.operator("motionbricks.scan", text="扫描目录")
            row.operator("motionbricks.scan", text="载入内置样例").samples = True


class MOTIONBRICKS_PT_route(MotionBricksPanel, Panel):
    bl_label = "路线行走"
    bl_parent_id = "MOTIONBRICKS_PT_main"
    bl_order = 35
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, _context):
        self.layout.label(icon="CURVE_DATA")

    def draw(self, context):
        layout = self.layout
        layout.enabled = _active_operator is None
        state = context.scene.motionbricks.route
        layout.prop(state, "source", expand=True)
        if state.source == "CURVE":
            layout.prop(state, "curve", text="")
        else:
            layout.prop(state, "filepath", text="")
            layout.prop(state, "override_speed")
        if state.source == "CURVE" or state.override_speed:
            layout.prop(state, "speed")
        for toggle, angle in (("set_start_yaw", "start_yaw"), ("set_end_yaw", "end_yaw")):
            layout.prop(state, toggle)
            if getattr(state, toggle):
                layout.prop(state, angle)
        row = layout.row(align=True)
        row.prop(state, "hold")
        row.prop(state, "precision", text="")
        generate = layout.row()
        source_ready = state.curve is not None if state.source == "CURVE" else bool(state.filepath)
        generate.enabled = rig_ready(context) and source_ready
        generate.operator("motionbricks.job", text="生成路线行走", icon="PLAY").operation = "route"


class MOTIONBRICKS_PT_mmd(MotionBricksPanel, Panel):
    bl_label = "MMD 动作互通"
    bl_parent_id = "MOTIONBRICKS_PT_main"
    bl_order = 45
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, _context):
        self.layout.label(icon="ARMATURE_DATA")

    def draw(self, context):
        layout = self.layout
        layout.enabled = _active_operator is None
        state = context.scene.motionbricks
        if not hasattr(bpy.types.PoseBone, "mmd_bone"):
            layout.label(text="请先安装并启用 MMD Tools", icon="INFO")
            return
        layout.prop(state, "mmd_target")
        layout.prop(state, "mmd_scale")
        if 0 <= state.clip_index < len(state.clips):
            layout.label(text=state.clips[state.clip_index].title, icon="ACTION")
        preview = layout.row(align=True)
        start = preview.row(align=True)
        start.enabled = (rig_ready(context) and rig_in_view(context, state.mmd_target)
                         and context.mode in {"OBJECT", "POSE"} and 0 <= state.clip_index < len(state.clips))
        start.operator("motionbricks.job", text="在目标角色上预览", icon="PLAY").operation = "mmd_preview"
        if state.mmd_preview.saved:
            layout.operator("motionbricks.stop_preview", text="停止 MMD 预览", icon="CANCEL")
            if (pmx := state.mmd_preview.rig) is not None:
                layout.label(text=f"正在预览 · {pmx.name}")
        row = layout.row(align=True)
        row.operator("motionbricks.import_vmd", text="导入 VMD", icon="IMPORT")
        row.operator("motionbricks.export_vmd", text="导出 VMD", icon="EXPORT")


class MOTIONBRICKS_PT_backend(MotionBricksPanel, Panel):
    bl_label = "后端配置"
    bl_parent_id = "MOTIONBRICKS_PT_main"
    bl_order = 60
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        addon = context.preferences.addons.get(__package__)
        if addon is not None:
            setup_ui.draw_setup(self.layout, context, addon.preferences)
        else:
            self.layout.label(text="请先安装并启用扩展。")


class MOTIONBRICKS_PT_rig(MotionBricksPanel, Panel):
    bl_label = "角色"
    bl_parent_id = "MOTIONBRICKS_PT_main"
    bl_order = 40
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return super().poll(context) and rig_ready(context)

    def draw_header(self, _context):
        self.layout.label(icon="ARMATURE_DATA")

    def draw(self, context):
        layout = self.layout
        layout.enabled = _active_operator is None
        layout.label(text=working_rig(context).name, icon="ARMATURE_DATA")
        layout.operator("motionbricks.job", text="创建 SOMA 参考角色", icon="ADD").operation = "rig"


CLASSES = (
    *setup_ui.CLASSES, *pose_library.CLASSES,
    MotionBricksPreferences, MotionClip, MotionPlacement, MotionTarget, pose_edit.MotionPoseEdit, MotionRoute, mmd_preview.MotionMMDPreview, MotionSettings,
    MOTIONBRICKS_OT_scan, MOTIONBRICKS_OT_job, MOTIONBRICKS_OT_import_recording, MOTIONBRICKS_OT_export_recording,
    MOTIONBRICKS_OT_import_vmd, MOTIONBRICKS_OT_export_vmd, MOTIONBRICKS_OT_cancel,
    MOTIONBRICKS_OT_remove_library_action,
    MOTIONBRICKS_OT_stop_preview, MOTIONBRICKS_OT_step_preview, MOTIONBRICKS_OT_save_pose, MOTIONBRICKS_OT_sequence,
    MOTIONBRICKS_OT_preview_sequence, MOTIONBRICKS_OT_select_sequence_pose, MOTIONBRICKS_OT_reset_placement,
    MOTIONBRICKS_OT_edit_pose, MOTIONBRICKS_OT_select_soma,
    MOTIONBRICKS_OT_clip_directory, MOTIONBRICKS_MT_clip_directories,
    MOTIONBRICKS_UL_clips, MOTIONBRICKS_UL_targets, MOTIONBRICKS_PT_main,
    MOTIONBRICKS_PT_sequence, MOTIONBRICKS_PT_target_details, MOTIONBRICKS_PT_assets,
    MOTIONBRICKS_PT_clips, MOTIONBRICKS_PT_route, MOTIONBRICKS_PT_mmd, MOTIONBRICKS_PT_backend, MOTIONBRICKS_PT_rig, *placement_gizmos.CLASSES,
)


@persistent
def cancel_before_load(_file):
    cancel_clip_autoplay()
    if _active_operator is not None:
        _active_operator._cleanup()
    sequence_preview.clear()


@persistent
def stop_mmd_previews_before_save(_file):
    context = bpy.context
    for scene in bpy.data.scenes:
        if scene.motionbricks.mmd_preview.saved:
            if scene == context.scene:
                stop_preview(context)
            else:
                with context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
                    stop_preview(context)


def import_menu(self, _context):
    self.layout.operator("motionbricks.import_recording", text="MotionBricks 录制 (.npz)")


def export_menu(self, _context):
    self.layout.operator("motionbricks.export_recording", text="MotionBricks 录制 (.npz)")


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.motionbricks = PointerProperty(type=MotionSettings)
    bpy.types.WindowManager.motionbricks_status = StringProperty(options={"SKIP_SAVE"})
    bpy.types.TOPBAR_MT_file_import.append(import_menu)
    bpy.types.TOPBAR_MT_file_export.append(export_menu)
    bpy.app.handlers.load_pre.append(cancel_before_load)
    bpy.app.handlers.save_pre.append(stop_mmd_previews_before_save)
    sequence_preview.register()


def unregister():
    stop_mmd_previews_before_save(None)
    if getattr(bpy.context, "scene", None) is not None:
        pose_edit.restore(bpy.context)
        stop_preview(bpy.context)
    cancel_before_load(None)
    sequence_preview.unregister()
    bpy.app.handlers.load_pre.remove(cancel_before_load)
    bpy.app.handlers.save_pre.remove(stop_mmd_previews_before_save)
    bpy.types.TOPBAR_MT_file_import.remove(import_menu)
    bpy.types.TOPBAR_MT_file_export.remove(export_menu)
    del bpy.types.WindowManager.motionbricks_status
    del bpy.types.Scene.motionbricks
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
