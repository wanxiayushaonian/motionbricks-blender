"""Blender viewport handles for selecting and placing sequence poses."""

import bpy
from bpy.types import GizmoGroup
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Matrix, Vector

from . import placement, sequence_preview


class MOTIONBRICKS_GGT_place_pose(GizmoGroup):
    bl_idname = "MOTIONBRICKS_GGT_place_pose"
    bl_label = "摆放序列姿势"
    bl_space_type = "VIEW_3D"
    bl_region_type = "WINDOW"
    bl_options = {"3D", "PERSISTENT"}

    @classmethod
    def poll(cls, context):
        return (bpy.ops.motionbricks.select_sequence_pose.poll()
                and sequence_preview.visible(context)
                and placement.selected_index(context.scene.motionbricks) >= 0)

    def setup(self, _context):
        colors = ((0.93, 0.20, 0.25), (0.38, 0.87, 0.22), (0.24, 0.59, 1.0))
        self.arrows = [self.gizmos.new("GIZMO_GT_arrow_3d") for _ in colors]
        self.dial = self.gizmos.new("GIZMO_GT_dial_3d")
        for gizmo, color in zip((*self.arrows, self.dial), (*colors, (1.0, 0.68, 0.20)), strict=True):
            gizmo.color = color
            gizmo.color_highlight = (1.0, 1.0, 1.0)
            gizmo.alpha = 0.9
            gizmo.alpha_highlight = 1.0
            gizmo.scale_basis = 0.52
            gizmo.line_width = 2.5
            gizmo.use_undo = True
        self.dial.scale_basis = 0.32
        self.bound = None

    def draw_prepare(self, context):
        state = context.scene.motionbricks
        index = placement.selected_index(state)
        action, item = placement.slots(state)[index]
        location = placement.location(action, item)
        for gizmo in (*self.arrows, self.dial):
            gizmo.hide = location is None
        if location is None:
            return

        key = (context.scene.as_pointer(), item.as_pointer())
        if self.bound != key:
            for axis, arrow in zip(("x", "y", "z"), self.arrows, strict=True):
                arrow.target_set_prop("offset", item, axis)
            self.dial.target_set_prop("offset", item, "yaw")
            self.bound = key

        for axis, arrow in enumerate(self.arrows):
            direction = Vector((float(axis == 0), float(axis == 1), float(axis == 2)))
            basis = direction.to_track_quat("Z", "Y").to_matrix().to_4x4()
            # The arrow itself applies its scalar offset along local Z.
            basis.translation = Vector(location) - direction * item.offset[axis]
            arrow.matrix_basis = basis
        self.dial.matrix_basis = Matrix.Translation(Vector(location))


class MOTIONBRICKS_GGT_pose_markers(GizmoGroup):
    bl_idname = "MOTIONBRICKS_GGT_pose_markers"
    bl_label = "选择序列姿势"
    bl_space_type = "VIEW_3D"
    bl_region_type = "WINDOW"
    bl_options = {"PERSISTENT", "SCALE"}

    @classmethod
    def poll(cls, context):
        return bpy.ops.motionbricks.select_sequence_pose.poll() and sequence_preview.visible(context)

    def setup(self, _context):
        self.buttons = []

    def draw_prepare(self, context):
        markers = sequence_preview.marker_positions()
        selected = placement.selected_index(context.scene.motionbricks)
        scale = context.preferences.system.ui_scale
        while len(self.buttons) > len(markers):
            self.gizmos.remove(self.buttons.pop()[0])
        while len(self.buttons) < len(markers):
            button = self.gizmos.new("GIZMO_GT_button_2d")
            button.draw_options = {"BACKDROP", "OUTLINE"}
            button.scale_basis = 9
            button.color_highlight = (1.0, 1.0, 1.0)
            button.alpha_highlight = 1.0
            self.buttons.append((button, button.target_set_operator("motionbricks.select_sequence_pose")))

        for (button, properties), (index, world) in zip(self.buttons, markers, strict=True):
            point = location_3d_to_region_2d(context.region, context.region_data, world)
            button.hide = point is None
            if point is None:
                continue
            margin = (36 if index == 0 else 21) * scale
            button.matrix_basis = Matrix.Translation(Vector((point.x - margin, point.y + 6 * scale, 0.0)))
            button.color = sequence_preview.pose_color(index, selected)[:3]
            button.alpha = 1.0 if index == selected else 0.75
            button.icon = "RADIOBUT_ON" if index == selected else "RADIOBUT_OFF"
            properties.index = index


CLASSES = (MOTIONBRICKS_GGT_place_pose, MOTIONBRICKS_GGT_pose_markers)
