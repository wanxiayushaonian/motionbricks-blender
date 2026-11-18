"""Configure the external MotionBricks environment from Blender preferences or the sidebar."""

import json
from pathlib import Path
import sys
import subprocess

import bpy
from bpy.props import BoolProperty, EnumProperty
from bpy.types import Operator

from .backend_config import (
    CODE_LICENSE, MODEL_LICENSE, RELEASE, REVISION, executable, python_path,
)
from .jobs import WorkerJob


def addon_module():
    return sys.modules[__package__]


def preferences(context):
    addon = context.preferences.addons.get(__package__)
    if addon is None:
        raise ValueError("请先安装并启用 MotionBricks 扩展。")
    return addon.preferences


def draw_setup(layout, context, settings):
    addon = addon_module()
    busy = addon._active_operator is not None
    fields = layout.column()
    fields.enabled = not busy
    fields.prop(settings, "repository", text="已有后端")
    fields.operator("motionbricks.backend_setup", text="检查已有后端", icon="CHECKMARK").operation = "PROBE"
    header, install = fields.panel("motionbricks_backend_install", default_closed=True)
    header.label(text="配置新后端")
    if install is not None:
        install.label(text=f"官方 {RELEASE} · {REVISION[:12]}")
        install.label(text="Linux 已验证 · Windows 待实测")
        install.prop(settings, "install_directory", text="安装目录")
        install.prop(settings, "setup_fp32")
        row = install.row(align=True)
        row.operator("wm.url_open", text="Git").url = "https://git-scm.com/downloads"
        row.operator("wm.url_open", text="uv").url = "https://docs.astral.sh/uv/getting-started/installation/"
        install.label(text=f"Git {'已找到' if executable('git') else '未找到'} · uv {'已找到' if executable('uv') else '未找到'}")
        install.operator("motionbricks.backend_setup", text="配置 / 继续安装", icon="IMPORT").operation = "INSTALL"
    if busy and isinstance(addon._active_operator, MOTIONBRICKS_OT_backend_setup):
        layout.label(text=context.window_manager.motionbricks_status, icon="TIME")
        layout.operator("motionbricks.cancel", text="取消配置", icon="CANCEL")
    if settings.backend_report:
        for check in json.loads(settings.backend_report).get("checks", ()):
            icon = {"OK": "CHECKMARK", "WARN": "INFO", "ERROR": "ERROR"}[check["level"]]
            layout.label(text=check["text"], icon=icon)
    install_path = Path(bpy.path.abspath(settings.install_directory)).expanduser() if settings.install_directory else None
    if install_path is not None and (install_path / ".motionbricks-setup.log").is_file():
        layout.operator("wm.path_open", text="打开安装日志所在目录", icon="FILE_FOLDER").filepath = str(install_path)
    if settings.repository:
        layout.operator("wm.path_open", text="打开后端目录", icon="FILE_FOLDER").filepath = bpy.path.abspath(settings.repository)


class MOTIONBRICKS_OT_backend_setup(Operator):
    bl_idname = "motionbricks.backend_setup"
    bl_label = "配置 MotionBricks 后端"
    operation: EnumProperty(items=(("INSTALL", "配置新后端", ""), ("PROBE", "检查已有后端", "")))
    accept_terms: BoolProperty(name="我已阅读并接受上游代码与模型许可", default=False, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return (addon_module()._active_operator is None and context.scene is not None
                and not context.scene.motionbricks.pose_edit.saved)

    def invoke(self, context, _event):
        if self.operation == "INSTALL":
            self.accept_terms = False
            return context.window_manager.invoke_props_dialog(self, width=440)
        return self.execute(context)

    def draw(self, _context):
        layout = self.layout
        layout.label(text="将下载官方源码、SOMA 骨架和模型，并安装 Python 依赖。")
        layout.label(text="默认仅下载 SOMA FP16，建议预留至少 10 GB。")
        row = layout.row()
        row.operator("wm.url_open", text="上游代码许可").url = CODE_LICENSE
        row.operator("wm.url_open", text="模型许可").url = MODEL_LICENSE
        layout.prop(self, "accept_terms")

    def execute(self, context):
        addon = addon_module()
        self._job = None
        self._timer = None
        self._cancel_requested = False
        self._manager = context.window_manager
        self._scene_id = context.scene.as_pointer()
        try:
            settings = preferences(context)
            if self.operation == "INSTALL":
                if not bpy.app.online_access:
                    raise ValueError("请在 Blender 偏好设置的系统选项中启用联网访问，再配置后端。")
                if not self.accept_terms:
                    raise ValueError("请先阅读并接受上游代码与模型许可。")
                if not settings.install_directory.strip():
                    raise ValueError("请选择新后端安装目录。")
                repository = Path(bpy.path.abspath(settings.install_directory)).expanduser().resolve()
                request = {"repository": str(repository), "accept_terms": True, "fp32": settings.setup_fp32}
                python = Path(sys.executable)
                worker = Path(__file__).with_name("setup_backend.py")
            else:
                if not settings.repository.strip():
                    raise ValueError("请选择已有 MotionBricks 目录，或展开「配置新后端」。")
                repository = Path(bpy.path.abspath(settings.repository)).expanduser().resolve()
                python = python_path(repository)
                if not python.is_file():
                    raise ValueError(f"找不到后端 Python · {python}")
                request = {"repository": str(repository)}
                worker = Path(__file__).with_name("backend_probe.py")
            settings.backend_report = ""
            self._repository = str(repository)
            self._job = WorkerJob(python, worker, request, Path.home())
            addon._active_operator = self
            self._manager.motionbricks_status = "正在检查后端…" if self.operation == "PROBE" else "正在配置后端…"
            if bpy.app.background:
                self._job.process.wait(timeout=1800)
                return self._complete(context)
            self._timer = self._manager.event_timer_add(.2, window=context.window)
            self._manager.modal_handler_add(self)
            return {"RUNNING_MODAL"}
        except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            self._cleanup()
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

    def modal(self, context, event):
        if self._cancel_requested or event.type == "ESC" or context.scene.as_pointer() != self._scene_id:
            self._cleanup()
            self._manager.motionbricks_status = "已取消后端任务，可在同一目录重试配置。"
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        if self._job.finished():
            return self._complete(context)
        self._manager.motionbricks_status = self._job.status()
        for window in self._manager.windows:
            for area in window.screen.areas:
                if area.type in {"VIEW_3D", "PREFERENCES"}:
                    area.tag_redraw()
        return {"PASS_THROUGH"}

    def _complete(self, context):
        try:
            report = self._job.result()
            if not any(item["level"] == "ERROR" for item in report["checks"]):
                preferences(context).repository = self._repository
            report["checks"].append({
                "level": "OK" if hasattr(bpy.types.PoseBone, "mmd_bone") else "WARN",
                "text": "MMD Tools 已启用" if hasattr(bpy.types.PoseBone, "mmd_bone")
                        else "MMD Tools 未启用，MMD 互通需要另行安装",
            })
            preferences(context).backend_report = json.dumps(report, ensure_ascii=False)
            errors = sum(item["level"] == "ERROR" for item in report["checks"])
            self._manager.motionbricks_status = f"后端检查发现 {errors} 项问题。" if errors else "后端就绪，首次生成将构建 TensorRT 缓存。"
            return {"FINISHED"}
        except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            text = str(error)
            preferences(context).backend_report = json.dumps({"checks": [{"level": "ERROR", "text": text}]}, ensure_ascii=False)
            self._manager.motionbricks_status = text.splitlines()[0]
            self.report({"ERROR"}, text)
            return {"CANCELLED"}
        finally:
            self._cleanup()

    def _cleanup(self):
        if self._timer is not None:
            self._manager.event_timer_remove(self._timer)
            self._timer = None
        if self._job is not None:
            self._job.close()
            self._job = None
        if addon_module()._active_operator is self:
            addon_module()._active_operator = None


CLASSES = (MOTIONBRICKS_OT_backend_setup,)
