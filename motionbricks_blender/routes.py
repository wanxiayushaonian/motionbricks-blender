"""Read ordered ground-plane routes from JSON or a Blender curve."""

import json
import math
from pathlib import Path


def heading(first, second):
    return math.degrees(math.atan2(second[0] - first[0], first[1] - second[1]))


def number(value, label):
    if type(value) not in (int, float):
        raise ValueError(f"{label}须为有限数值。")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError(f"{label}超出数值范围。") from error
    if not math.isfinite(result):
        raise ValueError(f"{label}须为有限数值。")
    return result


def coordinates(value, size, label):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise ValueError(f"{label}需要 {size} 个数值。")
    return tuple(number(component, label) for component in value)


def validate_course(data):
    if not isinstance(data, dict):
        raise ValueError("路线须为包含 start 和 waypoints 的对象。")
    start = coordinates(data.get("start"), 3, "起点")
    speed = number(data.get("speed", 1.2), "行走速度")
    if speed <= 0.0:
        raise ValueError("行走速度须大于 0 米/秒。")
    hold = number(data.get("hold", 0.5), "停留时间")
    if not 0.0 <= hold <= 10.0:
        raise ValueError("停留时间须在 0 至 10 秒之间。")
    precision = data.get("precision", "FP16")
    if precision not in ("FP16", "FP32"):
        raise ValueError("模型精度须为 FP16 或 FP32。")
    raw_points = data.get("waypoints")
    if not isinstance(raw_points, (tuple, list)) or not raw_points:
        raise ValueError("请为路线提供至少一个路点。")

    points = [start[:2]]
    for raw in raw_points:
        point = coordinates(raw, 2, "路点")
        if math.dist(points[-1], point) > 0.0001:
            points.append(point)
    if len(points) < 2:
        raise ValueError("路线的起点和路点重合。")
    end_yaw = data.get("end_yaw")
    return {
        "start": (*start[:2], start[2] % 360),
        "waypoints": points[1:],
        "end_yaw": heading(*points[-2:]) if end_yaw is None else number(end_yaw, "终点朝向") % 360,
        "floor_z": number(data.get("floor_z", 0.0), "地面高度"),
        "speed": speed, "hold": hold, "precision": precision,
    }


def load_json(path, *, speed=None):
    if path.suffix.lower() != ".json" or not path.is_file():
        raise ValueError("请选择路线 JSON 文件。")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("路线 JSON 无法读取。") from error
    if speed is not None and isinstance(data, dict):
        data["speed"] = speed
    return validate_course(data)


def curve_points(context, curve):
    from mathutils.geometry import interpolate_bezier

    if curve is None or curve.type != "CURVE" or curve.name not in context.scene.objects:
        raise ValueError("请选择当前场景中的曲线对象。")
    evaluated = curve.evaluated_get(context.evaluated_depsgraph_get())
    if len(evaluated.data.splines) != 1:
        raise ValueError("请选择包含一条连续样条的曲线。")
    spline = evaluated.data.splines[0]
    world = evaluated.matrix_world
    if spline.type == "POLY":
        points = [world @ point.co.to_3d() for point in spline.points]
        if spline.use_cyclic_u and points:
            points.append(points[0])
        return points
    if spline.type != "BEZIER":
        raise ValueError("路线曲线支持贝塞尔和多段线样条。")

    handles = list(spline.bezier_points)
    if len(handles) < 2:
        return []
    if spline.use_cyclic_u:
        handles.append(handles[0])
    points = [world @ handles[0].co]
    for first, second in zip(handles, handles[1:]):
        controls = [world @ point for point in (first.co, first.handle_right, second.handle_left, second.co)]
        length = sum(math.dist(a.xy, b.xy) for a, b in zip(controls, controls[1:]))
        steps = max(12, min(256, math.ceil(number(length, "曲线长度") / 0.25)))
        for point in interpolate_bezier(*controls, steps)[1:-1]:
            if math.dist(points[-1].xy, point.xy) >= 0.9:
                points.append(point)
        points.append(controls[-1])
    return points


def from_curve(context, settings):
    points = curve_points(context, settings.curve)
    if len(points) < 2:
        raise ValueError("路线曲线需要至少两个点。")
    start = tuple(points[0].xy)
    waypoints = [tuple(point.xy) for point in points[1:]]
    direction = next((point for point in waypoints if math.dist(start, point) > 0.0001), start)
    return validate_course({
        "start": (*start, math.degrees(settings.start_yaw) if settings.set_start_yaw else heading(start, direction)),
        "waypoints": waypoints,
        "end_yaw": math.degrees(settings.end_yaw) if settings.set_end_yaw else None,
        "floor_z": points[0].z,
        "speed": settings.speed,
    })


def from_settings(context, settings):
    import bpy

    if settings.source == "JSON":
        path = Path(bpy.path.abspath(settings.filepath)).expanduser()
        name = path.stem
        course = load_json(path, speed=settings.speed if settings.override_speed else None)
        if settings.set_start_yaw:
            course["start"] = (*course["start"][:2], math.degrees(settings.start_yaw))
        if settings.set_end_yaw:
            course["end_yaw"] = math.degrees(settings.end_yaw)
    else:
        course = from_curve(context, settings)
        name = settings.curve.name
    return name, validate_course(dict(course, hold=settings.hold, precision=settings.precision))
