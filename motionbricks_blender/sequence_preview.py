"""Draw the saved SOMA poses as a viewport-only preview."""

from itertools import product
from math import cos, sin, tau

import blf
import bpy
import gpu
import numpy as np
from bpy.app.handlers import persistent
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from . import placement
from .rig import POSE_DATA, RIG_DATA, RIG_REVISION, RigAdapter, read_pose_asset

START = (0.18, 0.88, 0.40, 1.0)
TARGET = (0.20, 0.67, 0.99, 1.0)
SELECTED = (1.0, 0.66, 0.18, 1.0)

_preview = None
_handlers = []
_surface_shader = None


def mesh_geometry(obj):
    mesh = obj.data
    positions = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    normals = np.empty_like(positions)
    triangles = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
    mesh.vertices.foreach_get("co", positions)
    mesh.vertices.foreach_get("normal", normals)
    mesh.loop_triangles.foreach_get("vertices", triangles)
    world = obj.matrix_world
    positions = positions.reshape(-1, 3) @ np.array(world.to_3x3(), dtype=np.float32).T
    positions += np.asarray(world.translation, dtype=np.float32)
    normals = normals.reshape(-1, 3) @ np.array(world.to_3x3().inverted(), dtype=np.float32)
    return positions, normals, triangles.reshape(-1, 3)


class PreviewPose:
    def __init__(self, pose, parts):
        self.parts = parts
        self.batches = None
        self.minimum = np.min([part[0].min(axis=0) for part in parts], axis=0)
        self.maximum = np.max([part[0].max(axis=0) for part in parts], axis=0)
        self.anchor = tuple(pose[0][:3])
        x, y, _z = self.anchor
        self.foot = (x, y, float(self.minimum[2]) + 0.015)
        self.label = Vector((x, y, float(self.maximum[2]) + 0.12))

    def draw(self, shader, color):
        if self.batches is None:
            self.batches = [
                batch_for_shader(shader, "TRIS", {"position": positions, "normal": normals}, indices=triangles)
                for positions, normals, triangles in self.parts
            ]
            self.parts.clear()
        shader.uniform_float("tint", color)
        for batch in self.batches:
            batch.draw(shader)


def preview_geometry(scene, rig, actions):
    adapter = RigAdapter(rig)
    poses = {key: read_pose_asset(action, adapter.signature)["pose"] for key, action in actions.items()}
    sources = [
        obj for obj in scene.objects if obj.type == "MESH" and len(obj.data.polygons)
        and any(mod.type == "ARMATURE" and mod.object == rig for mod in obj.modifiers)
    ]
    if not sources:
        raise ValueError("找不到绑定到当前 SOMA 骨架的网格。")

    # Evaluate on linked copies in an isolated scene. Neither the live rig's
    # Action nor the scene frame has to be touched to skin a saved pose.
    stage = bpy.data.scenes.new("MotionBricks Preview")
    copies = []
    try:
        clone = bpy.data.objects.new("MotionBricks Preview Rig", rig.data)
        copies.append(clone)
        stage.collection.objects.link(clone)
        clone.matrix_world = rig.matrix_world
        clone[RIG_DATA] = rig[RIG_DATA]
        clone[RIG_REVISION] = rig[RIG_REVISION]

        for source in sources:
            obj = source.copy()
            copies.append(obj)
            stage.collection.objects.link(obj)
            obj.animation_data_clear()
            obj.constraints.clear()
            obj.parent = None
            obj.matrix_world = source.matrix_world
            obj.hide_viewport = False
            for modifier in list(obj.modifiers):
                if modifier.type == "ARMATURE" and modifier.object == rig:
                    modifier.object = clone
                    modifier.show_viewport = True
                else:
                    obj.modifiers.remove(modifier)

        meshes = copies[1:]
        staging_layer = stage.view_layers[0]
        with bpy.context.temp_override(scene=stage, view_layer=staging_layer):
            staging_layer.update()
            staged_rig = RigAdapter(clone)
            result = {}
            for key, pose in poses.items():
                staged_rig.apply_pose(pose)
                staging_layer.update()
                depsgraph = bpy.context.evaluated_depsgraph_get()
                result[key] = PreviewPose(pose, [mesh_geometry(mesh.evaluated_get(depsgraph)) for mesh in meshes])
        return result
    finally:
        for obj in reversed(copies):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.scenes.remove(stage)


class SequencePreview:
    def __init__(self, scene, rig):
        self.scene = scene
        self.rig = rig
        self.scene_name = scene.name
        self.rig_name = rig.name
        self.actions = ()
        self.keys = ()
        self.geometry = {}
        self.entries = ()
        self.guides = None
        self.guide_feet = None
        self.selected = None

    def sync(self):
        if self.rig.name not in self.scene.objects:
            raise ValueError("预览所用的 SOMA 骨架已移出场景。")
        self.scene_name = self.scene.name
        self.rig_name = self.rig.name
        state = self.scene.motionbricks
        actions = tuple(action for action, _ in placement.slots(state))
        keys = tuple(action.get(POSE_DATA) if action else None for action in actions)
        selected = placement.selected_index(state)
        changed = keys != self.keys or actions != self.actions or self.selected != selected
        if keys != self.keys:
            if not any(keys):
                raise ValueError("请先添加起始姿势或目标姿势。")
            if any(action and not isinstance(key, str) for action, key in zip(actions, keys, strict=True)):
                raise ValueError("序列中有无法读取的姿势资产。")
            missing = {key: action for key, action in zip(keys, actions, strict=True)
                       if key is not None and key not in self.geometry}
            if missing:
                self.geometry.update(preview_geometry(self.scene, self.rig, missing))
            self.geometry = {key: self.geometry[key] for key in keys if key is not None}
            self.entries = tuple(self.geometry.get(key) for key in keys)
            self.keys = keys
        self.actions = actions
        self.selected = selected
        return changed

    def instances(self):
        state = self.scene.motionbricks
        selected = placement.selected_index(state)
        for index, (action, position) in enumerate(placement.slots(state)):
            if state.isolate_pose and index != selected:
                continue
            if index >= len(self.actions) or action != self.actions[index]:
                continue
            pose = self.entries[index]
            if pose is not None:
                yield index, pose, placement.matrix_at(pose.anchor, position)


def create_surface_shader():
    interface = gpu.types.GPUStageInterfaceInfo("motionbricks_preview_interface")
    interface.smooth("VEC3", "surface_normal")
    info = gpu.types.GPUShaderCreateInfo()
    info.push_constant("MAT4", "view_projection")
    info.push_constant("VEC4", "tint")
    info.push_constant("VEC2", "yaw_cs")
    info.vertex_in(0, "VEC3", "position")
    info.vertex_in(1, "VEC3", "normal")
    info.vertex_out(interface)
    info.fragment_out(0, "VEC4", "color")
    info.vertex_source("""
        void main() {
            surface_normal = vec3(yaw_cs.x * normal.x - yaw_cs.y * normal.y,
                                  yaw_cs.y * normal.x + yaw_cs.x * normal.y, normal.z);
            gl_Position = view_projection * vec4(position, 1.0);
        }
    """)
    info.fragment_source("""
        void main() {
            vec3 light = normalize(vec3(0.55, -0.50, 0.72));
            float shade = 0.42 + 0.58 * max(dot(normalize(surface_normal), light), 0.0);
            color = vec4(tint.rgb * shade, tint.a);
        }
    """)
    return gpu.shader.create_from_info(info)


def guide_points(feet):
    points = []
    for first, second in zip(feet, feet[1:]):
        if first is not None and second is not None:
            points.extend((first, second))
    for foot in feet:
        if foot is not None:
            x, y, z = foot
            ring = [(x + 0.22 * cos(i * tau / 32), y + 0.22 * sin(i * tau / 32), z) for i in range(32)]
            for first, second in zip(ring, ring[1:] + ring[:1], strict=True):
                points.extend((first, second))
    return points


def active(scene):
    return _preview is not None and _preview.scene == scene


def redraw():
    manager = bpy.context.window_manager
    for window in manager.windows if manager else ():
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def clear():
    global _preview
    if _preview is not None:
        _preview = None
        redraw()


def toggle(context, rig):
    global _preview
    if active(context.scene):
        clear()
    else:
        preview = SequencePreview(context.scene, rig)
        preview.sync()
        _preview = preview
        redraw()


def frame(context):
    if not active(context.scene):
        return
    _preview.sync()
    bounds = [
        matrix @ Vector(corner)
        for _, pose, matrix in _preview.instances()
        for corner in product(*zip(pose.minimum, pose.maximum, strict=True))
    ]
    if not bounds:
        return
    minimum = Vector(np.min(bounds, axis=0))
    maximum = Vector(np.max(bounds, axis=0))
    region = context.space_data.region_3d
    if region.view_perspective == "CAMERA":
        region.view_perspective = "PERSP"
    region.view_location = (minimum + maximum) * 0.5
    region.view_distance = max(3.0, (maximum - minimum).length * 1.6)
    context.area.tag_redraw()


def refresh():
    if _preview is None:
        return 0.4
    try:
        if _preview.sync():
            redraw()
    except ReferenceError:
        clear()
    except (KeyError, TypeError, ValueError, RuntimeError) as error:
        bpy.context.window_manager.motionbricks_status = f"序列预览已关闭：{error}"
        clear()
    return 0.15


@persistent
def after_undo(_scene):
    if _preview is None:
        return
    scene = bpy.data.scenes.get(_preview.scene_name)
    rig = scene.objects.get(_preview.rig_name) if scene else None
    if rig is None:
        clear()
    else:
        _preview.scene = scene
        _preview.rig = rig
        _preview.actions = ()
        redraw()


def visible(context):
    return (active(context.scene) and context.space_data.overlay.show_overlays
            and not context.scene.motionbricks.pose_edit.saved)


def marker_positions():
    return [(index, matrix @ pose.label) for index, pose, matrix in _preview.instances()] if _preview else []


def pose_color(index, selected):
    if index == 0:
        return START
    return SELECTED if index == selected else TARGET


def draw_view():
    global _surface_shader
    context = bpy.context
    if not visible(context):
        return
    if _surface_shader is None:
        _surface_shader = create_surface_shader()

    depth_test = gpu.state.depth_test_get()
    depth_mask = gpu.state.depth_mask_get()
    try:
        gpu.state.depth_test_set("LESS_EQUAL")
        gpu.state.depth_mask_set(True)
        _surface_shader.bind()
        projection = gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix()
        state = context.scene.motionbricks
        selected = placement.selected_index(state)
        feet = [None] * len(_preview.entries)
        for index, pose, matrix in _preview.instances():
            _surface_shader.uniform_float("view_projection", projection @ matrix)
            _surface_shader.uniform_float("yaw_cs", (matrix[0][0], matrix[1][0]))
            pose.draw(_surface_shader, pose_color(index, selected))
            feet[index] = tuple(matrix @ Vector(pose.foot))

        shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        if _preview.guide_feet != feet:
            points = guide_points(feet)
            _preview.guides = batch_for_shader(shader, "LINES", {"pos": points}) if points else None
            _preview.guide_feet = feet
        if _preview.guides is not None:
            shader.bind()
            shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
            shader.uniform_float("lineWidth", 2.0)
            shader.uniform_float("color", (0.95, 0.78, 0.39, 1.0))
            _preview.guides.draw(shader)
    finally:
        gpu.state.depth_test_set(depth_test)
        gpu.state.depth_mask_set(depth_mask)


def draw_labels():
    context = bpy.context
    if not visible(context):
        return
    font = 0
    blf.size(font, round(14 * context.preferences.system.ui_scale))
    blf.enable(font, blf.SHADOW)
    blf.shadow(font, 3, 0.0, 0.0, 0.0, 0.9)
    blf.shadow_offset(font, 1, -1)
    try:
        selected = placement.selected_index(context.scene.motionbricks)
        for index, label in marker_positions():
            point = location_3d_to_region_2d(context.region, context.region_data, label)
            if point is not None:
                text = "START" if index == 0 else str(index)
                blf.position(font, point.x - blf.dimensions(font, text)[0] / 2, point.y, 0)
                blf.color(font, *pose_color(index, selected))
                blf.draw(font, text)
    finally:
        blf.disable(font, blf.SHADOW)


def register():
    if not bpy.app.background:
        space = bpy.types.SpaceView3D
        _handlers.append(space.draw_handler_add(draw_view, (), "WINDOW", "POST_VIEW"))
        _handlers.append(space.draw_handler_add(draw_labels, (), "WINDOW", "POST_PIXEL"))
        bpy.app.timers.register(refresh, persistent=True)
        bpy.app.handlers.undo_post.append(after_undo)
        bpy.app.handlers.redo_post.append(after_undo)


def unregister():
    global _surface_shader
    clear()
    if bpy.app.timers.is_registered(refresh):
        bpy.app.timers.unregister(refresh)
        bpy.app.handlers.undo_post.remove(after_undo)
        bpy.app.handlers.redo_post.remove(after_undo)
    for handler in _handlers:
        bpy.types.SpaceView3D.draw_handler_remove(handler, "WINDOW")
    _handlers.clear()
    _surface_shader = None
