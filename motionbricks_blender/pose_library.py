"""Names and persistent groups for MotionBricks pose Actions in a blend file."""

import sys
from uuid import uuid4

import bpy
from bpy.props import StringProperty
from bpy.types import Menu, Operator, PropertyGroup, UIList

from .rig import POSE_DATA

GROUP_DATA = "motionbricks_pose_group"
UNGROUPED = "UNGROUPED"


class MotionPoseGroup(PropertyGroup):
    identifier: StringProperty()
    name: StringProperty()


def groups():
    result = {}
    for scene in bpy.data.scenes:
        for group in scene.motionbricks.pose_groups:
            result.setdefault(group.identifier, group.name)
    return result


def group_of(action):
    identifier = action.get(GROUP_DATA, "")
    return identifier if identifier in groups() else UNGROUPED


def group_label(identifier):
    if not identifier:
        return "全部分组"
    return groups().get(identifier, "未分组")


def pose_index(state):
    return bpy.data.actions.find(state.pose.name) if state.pose is not None else -1


def select_pose(state, index):
    if 0 <= index < len(bpy.data.actions):
        action = bpy.data.actions[index]
        if POSE_DATA in action:
            state.pose = action


def visible(action, state, known_groups=None):
    if POSE_DATA not in action:
        return False
    known = groups() if known_groups is None else known_groups
    identifier = action.get(GROUP_DATA, "")
    identifier = identifier if identifier in known else UNGROUPED
    return ((not state.pose_group_filter or state.pose_group_filter == identifier)
            and all(word in action.name.casefold() for word in state.pose_search.casefold().split()))


def show_pose(state, _context):
    action = state.pose
    if action is not None and not visible(action, state):
        # Keep the newly selected Action while revealing it in the browser.
        state["pose_search"] = ""
        if not visible(action, state):
            state["pose_group_filter"] = group_of(action)


def filters_changed(state, _context):
    if state.pose is not None and not visible(state.pose, state):
        state.pose = None


def assign(action, identifier):
    if action.library is not None:
        raise ValueError("请先将链接姿势转为本地资产，再修改分组。")
    if identifier and identifier != UNGROUPED and identifier not in groups():
        raise ValueError("该姿势分组已被删除，请重新选择。")
    if identifier and identifier != UNGROUPED:
        action[GROUP_DATA] = identifier
    elif GROUP_DATA in action:
        del action[GROUP_DATA]


def save_to_current_group(action, state):
    identifier = state.pose_group_filter
    assign(action, identifier if identifier in groups() else UNGROUPED)


class PoseLibraryOperator:
    @classmethod
    def poll(cls, context):
        return (context.scene is not None and sys.modules[__package__]._active_operator is None
                and not context.scene.motionbricks.pose_edit.saved)


class MOTIONBRICKS_OT_pose_group(PoseLibraryOperator, Operator):
    bl_idname = "motionbricks.pose_group"
    bl_label = "管理姿势分组"
    bl_options = {"UNDO"}

    operation: StringProperty(default="SELECT", options={"SKIP_SAVE"})
    identifier: StringProperty(options={"SKIP_SAVE"})
    name: StringProperty(name="分组名称")

    def invoke(self, context, _event):
        if self.operation in {"CREATE", "RENAME"}:
            self.name = groups().get(self.identifier, "") if self.operation == "RENAME" else ""
            return context.window_manager.invoke_props_dialog(self)
        if self.operation == "DELETE":
            return context.window_manager.invoke_confirm(self, _event)
        return self.execute(context)

    def draw(self, _context):
        self.layout.prop(self, "name")

    def execute(self, context):
        state = context.scene.motionbricks
        known = groups()
        try:
            if self.operation in {"CREATE", "RENAME"}:
                name = self.name.strip()
                if not name:
                    raise ValueError("请输入分组名称。")
                if name in {"全部分组", "未分组"} or any(
                    value.casefold() == name.casefold() and key != self.identifier
                    for key, value in known.items()
                ):
                    raise ValueError("分组名称已存在，请使用其他名称。")
            if self.operation == "CREATE":
                group = state.pose_groups.add()
                group.identifier = uuid4().hex
                group.name = name
                state.pose_group_filter = group.identifier
            elif self.operation == "RENAME":
                if self.identifier not in known:
                    raise ValueError("该分组已被删除。")
                for scene in bpy.data.scenes:
                    for group in scene.motionbricks.pose_groups:
                        if group.identifier == self.identifier:
                            group.name = name
            elif self.operation == "DELETE":
                if self.identifier not in known:
                    raise ValueError("该分组已被删除。")
                members = [action for action in bpy.data.actions if POSE_DATA in action
                           and action.get(GROUP_DATA) == self.identifier]
                if any(action.library is not None for action in members):
                    raise ValueError("分组中有链接姿势，请先转为本地资产。")
                for action in members:
                    assign(action, UNGROUPED)
                for scene in bpy.data.scenes:
                    other = scene.motionbricks
                    for index in reversed(range(len(other.pose_groups))):
                        if other.pose_groups[index].identifier == self.identifier:
                            other.pose_groups.remove(index)
                    if other.pose_group_filter == self.identifier:
                        other.pose_group_filter = UNGROUPED
            elif self.operation == "SELECT":
                if self.identifier not in {"", UNGROUPED} and self.identifier not in known:
                    raise ValueError("该分组已被删除。")
                state.pose_group_filter = self.identifier
            elif self.operation == "ASSIGN":
                if state.pose is None or POSE_DATA not in state.pose:
                    raise ValueError("请先选择姿势资产。")
                assign(state.pose, self.identifier)
                state.pose_group_filter = group_of(state.pose)
            else:
                raise ValueError("未知的分组操作。")
            return {"FINISHED"}
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


def draw_groups(layout, *, operation):
    if operation == "SELECT":
        layout.operator("motionbricks.pose_group", text="全部分组").identifier = ""
    item = layout.operator("motionbricks.pose_group", text="未分组")
    item.operation, item.identifier = operation, UNGROUPED
    for identifier, name in sorted(groups().items(), key=lambda pair: pair[1].casefold()):
        item = layout.operator("motionbricks.pose_group", text=name, icon="FILE_FOLDER")
        item.operation, item.identifier = operation, identifier


class MOTIONBRICKS_MT_pose_groups(Menu):
    bl_label = "姿势分组"

    def draw(self, _context):
        draw_groups(self.layout, operation="SELECT")


class MOTIONBRICKS_MT_move_pose(Menu):
    bl_label = "移动到分组"

    def draw(self, _context):
        draw_groups(self.layout, operation="ASSIGN")


class MOTIONBRICKS_UL_poses(UIList):
    def filter_items(self, context, data, property_name):
        state = context.scene.motionbricks
        known = groups()
        flags = [self.bitflag_filter_item if visible(action, state, known) else 0
                 for action in getattr(data, property_name)]
        order = bpy.types.UI_UL_list.sort_items_by_name(getattr(data, property_name), "name")
        return flags, order

    def draw_item(self, _context, layout, _data, action, _icon, _active_data, _active_property, _index=0):
        layout.label(text=action.name, icon="ASSET_MANAGER")


def draw_browser(layout, state):
    layout.prop(state, "pose_search", text="", icon="VIEWZOOM", placeholder="搜索姿势")
    row = layout.row(align=True)
    row.menu("MOTIONBRICKS_MT_pose_groups", text=group_label(state.pose_group_filter))
    row.operator("motionbricks.pose_group", text="", icon="ADD").operation = "CREATE"
    identifier = state.pose_group_filter
    if identifier in groups():
        rename = row.operator("motionbricks.pose_group", text="", icon="GREASEPENCIL")
        rename.operation, rename.identifier = "RENAME", identifier
        delete = row.operator("motionbricks.pose_group", text="", icon="REMOVE")
        delete.operation, delete.identifier = "DELETE", identifier
    layout.template_list("MOTIONBRICKS_UL_poses", "", bpy.data, "actions", state, "pose_index", rows=4)
    known = groups()
    if not any(visible(action, state, known) for action in bpy.data.actions):
        layout.label(text="当前分组没有匹配的姿势", icon="INFO")
    if state.pose is not None:
        column = layout.column()
        column.enabled = state.pose.library is None
        column.prop(state.pose, "name", text="名称")
        column.menu("MOTIONBRICKS_MT_move_pose", text=f"移动到分组 · {group_label(group_of(state.pose))}")


CLASSES = (
    MotionPoseGroup, MOTIONBRICKS_OT_pose_group, MOTIONBRICKS_MT_pose_groups,
    MOTIONBRICKS_MT_move_pose, MOTIONBRICKS_UL_poses,
)
