"""Transfer body motion between the canonical SOMA skeleton and an imported MMD rig."""

from contextlib import contextmanager
import importlib
import json
import math
import os
import struct
from pathlib import Path
from tempfile import NamedTemporaryFile
import unicodedata

import bpy
from mathutils import Matrix, Vector

from . import recording
from .rig import RIG_DATA, matrix_from_pose, pose_from_matrix, scene_fps, world_matrices

FPS = 30
# Japanese names come from the PMX metadata, even when Blender displays translated names.
CORE = {
    "Hips": ("下半身", "腰"), "Spine1": ("上半身",), "Head": ("頭",),
    **{side + source: (prefix + target,) for side, prefix in (("Left", "左"), ("Right", "右"))
       for source, target in (("Arm", "腕"), ("ForeArm", "ひじ"), ("Hand", "手首"),
                              ("Leg", "足"), ("Shin", "ひざ"), ("Foot", "足首"))},
}
OPTIONAL = {"Spine2": ("上半身1",), "Chest": ("上半身2",), "Neck1": ("首",)}
AIM = {}
for _side, _prefix in (("Left", "左"), ("Right", "右")):
    for _source, _target in (("Shoulder", "肩"), ("ToeBase", "つま先")):
        OPTIONAL[_side + _source] = (_prefix + _target,)
    for _first, _second in (("Arm", "ForeArm"), ("ForeArm", "Hand"), ("Leg", "Shin"), ("Shin", "Foot")):
        AIM[_side + _first] = _side + _second
    for _finger, _jp in (("Index", "人指"), ("Middle", "中指"), ("Ring", "薬指"), ("Pinky", "小指")):
        for _joint in range(1, 4):
            # SOMA includes a metacarpal. PMX's first segment starts at the knuckle.
            OPTIONAL[f"{_side}Hand{_finger}{_joint + 1}"] = (_prefix + _jp + str(_joint),)
    for _joint in range(1, 4):
        OPTIONAL[f"{_side}HandThumb{_joint}"] = (_prefix + "親指" + str(_joint - 1),)


def tools():
    for addon in bpy.context.preferences.addons:
        if addon.module.rsplit(".", 1)[-1] == "mmd_tools":
            try:
                return importlib.import_module(f"{addon.module}.core.vmd")
            except ImportError as error:
                raise ValueError("MMD Tools 无法加载，请检查扩展安装。") from error
    raise ValueError("请先在 Blender 中安装并启用 MMD Tools。")


def is_mmd_rig(obj):
    return (obj is not None and obj.type == "ARMATURE"
            and getattr(obj.parent, "mmd_type", None) == "ROOT")


def japanese_name(bone):
    return bone.mmd_bone.name_j or bone.name


def normalized(name):
    return unicodedata.normalize("NFKC", name)


def vmd_bone_name(name):
    try:
        encoded = name.encode("cp932")
    except UnicodeEncodeError as error:
        raise ValueError(f"骨名「{name}」无法写入 VMD，请检查 PMX 的日文骨名。") from error
    if len(encoded) > 15 or not encoded or b"\0" in encoded:
        raise ValueError(f"骨名「{name}」超出 VMD 骨名字段限制。")
    return name


def leg_length(points):
    return sum(
        (points[side + first] - points[side + second]).length
        for side in ("Left", "Right") for first, second in (("Leg", "Shin"), ("Shin", "Foot"))
    ) / 2


class BodyMap:
    def __init__(self, soma, mmd):
        if not is_mmd_rig(mmd):
            raise ValueError("请选择使用 MMD Tools 导入的 PMX/PMD 骨架。")
        self.rig = mmd
        bones = [bone for bone in mmd.pose.bones if not bone.is_mmd_shadow_bone]
        by_name = {}
        for bone in bones:
            by_name.setdefault(normalized(japanese_name(bone)), []).append(bone)

        def find(aliases):
            for alias in aliases:
                candidates = by_name.get(alias, [])
                if len(candidates) > 1:
                    raise ValueError(f"MMD 骨名「{alias}」有多个匹配。")
                if candidates:
                    return candidates[0]
            return None

        self.mapped = {name: find(aliases) for name, aliases in (CORE | OPTIONAL).items()}
        missing = [CORE[name][0] for name in CORE if self.mapped[name] is None]
        if missing:
            raise ValueError("PMX 缺少人体骨骼 " + "、".join(missing))
        self.mapped = {name: bone for name, bone in self.mapped.items() if bone is not None}
        self.center = next((bone for name in ("センター", "全ての親")
                            if (bone := find((name,))) is not None and not all(bone.lock_location)), None)
        if self.center is None:
            raise ValueError("PMX 缺少可平移的「センター」或「全ての親」骨骼。")
        if any(self.center.name not in [p.name for p in bone.parent_recursive] for bone in self.mapped.values()):
            raise ValueError("MMD 的中心骨骼必须是全部已匹配骨骼的上级。")

        data = json.loads(soma.rig[RIG_DATA])
        self.parents = data["parents"]
        self.names = data["joints"]
        self.soma_bind = dict(zip(self.names, map(matrix_from_pose, data["joint_binds"]), strict=True))
        self.soma_local = list(map(matrix_from_pose, data["rest"]))
        self.mmd_bind = {name: bone.bone.matrix_local.copy() for name, bone in self.mapped.items()}
        source_points = {name: mat.translation for name, mat in self.soma_bind.items()}
        target_points = {name: mat.translation for name, mat in self.mmd_bind.items()}
        source_length, target_length = leg_length(source_points), leg_length(target_points)
        if min(source_length, target_length) < 1e-5:
            raise ValueError("骨架腿长无效，无法换算位移。")
        self.scale = target_length / source_length
        self.alignment = {}
        for name, target in self.mmd_bind.items():
            source = self.soma_bind[name]
            next_joint = AIM.get(name)
            alignment = Matrix.Identity(3).to_quaternion()
            if next_joint:
                target_dir = target_points[next_joint] - target_points[name]
                source_dir = source_points[next_joint] - source_points[name]
                if min(target_dir.length, source_dir.length) > 1e-5:
                    # Preserve the posed segment direction across differing A/T-pose binds.
                    alignment = target_dir.rotation_difference(source_dir)
            self.alignment[name] = source.to_quaternion().inverted() @ alignment @ target.to_quaternion()

        # Unmapped helpers keep their rest offsets under their nearest animated ancestor.
        self.target_order = sorted(self.mapped, key=lambda name: len(self.mapped[name].parent_recursive))
        mapped_names = {bone.name: name for name, bone in self.mapped.items()}
        self.target_parent = {}
        for name in self.target_order:
            bone = self.mapped[name]
            self.target_parent[name] = next(
                (mapped_names[parent.name] for parent in bone.parent_recursive if parent.name in mapped_names),
                None,
            )

    def outbound(self, pose):
        source = dict(zip(self.names, world_matrices(pose, self.parents), strict=True))
        rotations = {}
        for name in self.target_order:
            parent = self.target_parent[name]
            rest = self.mmd_bind[name].to_quaternion()
            inherited = (rotations[parent] @ self.mmd_bind[parent].to_quaternion().inverted() @ rest
                         if parent else rest)
            desired = source[name].to_quaternion() @ self.alignment[name]
            rotations[name] = desired
            yield name, inherited.inverted() @ desired

    def inbound(self, evaluated):
        rotations = {
            name: evaluated.pose.bones[bone.name].matrix.to_quaternion() @ self.alignment[name].inverted()
            for name, bone in self.mapped.items()
        }
        pelvis = evaluated.pose.bones[self.mapped["Hips"].name].matrix.translation
        delta = (pelvis - self.mmd_bind["Hips"].translation) / self.scale
        root = self.soma_bind["Hips"].translation + delta
        result = []
        world = []
        for name, parent, rest in zip(self.names, self.parents, self.soma_local, strict=True):
            if parent < 0:
                local = Matrix.LocRotScale(root, rotations[name], None)
                world.append(local)
            else:
                parent_matrix = world[parent]
                rotation = (parent_matrix.to_quaternion().inverted() @ rotations[name]
                            if name in rotations else rest.to_quaternion())
                local = Matrix.LocRotScale(rest.translation, rotation, None)
                world.append(parent_matrix @ local)
            result.append(pose_from_matrix(local))
        return result

    def leg_ik(self):
        leg_bones = {self.mapped[side + name].name for side in ("Left", "Right")
                     for name in ("Leg", "Shin", "Foot", "ToeBase") if side + name in self.mapped}
        controllers = set()
        for owner in self.rig.pose.bones:
            for constraint in owner.constraints:
                if constraint.type != "IK" or constraint.target != self.rig:
                    continue
                chain = [owner, *owner.parent_recursive]
                if not any(bone.name in leg_bones for bone in chain[:constraint.chain_count or None]):
                    continue
                controller = self.rig.pose.bones.get(constraint.subtarget)
                if controller is not None and not controller.is_mmd_shadow_bone:
                    controllers.add(japanese_name(controller))
        # MMD Tools can build toe IK using shadow bones. Use the original PMX names.
        for bone in self.rig.pose.bones:
            name = japanese_name(bone)
            if not bone.is_mmd_shadow_bone and normalized(name) in {"左足IK", "右足IK", "左つま先IK", "右つま先IK"}:
                controllers.add(name)
        return sorted(map(vmd_bone_name, controllers))


def linear_interpolation():
    curve = [20] * 8 + [107] * 8
    return [value for channel in range(4) for value in curve[channel:] + [0] * channel]


def retarget_action(context, adapter, action, target):
    mapping = BodyMap(adapter, target)
    frames = recording.sampling_frames(action, scene_fps(context.scene), FPS)
    poses = recording.sample_action(context, adapter, action, frames)
    return mapping, frames, poses


def retarget_samples(mapping, poses):
    first = Vector(poses[0][0][:3])
    origin = Vector((first.x, first.y, mapping.soma_bind["Hips"].translation.z))
    center_inverse = mapping.center.bone.matrix_local.to_3x3().inverted()
    identity = Matrix.Identity(3).to_quaternion()
    for pose in poses:
        translation = center_inverse @ ((Vector(pose[0][:3]) - origin) * mapping.scale)
        yield {"Center": (translation, identity),
               **{name: ((0, 0, 0), rotation) for name, rotation in mapping.outbound(pose)}}


def export_vmd(context, adapter, action, target, path, pmx_scale):
    vmd = tools()
    mapping, frames, poses = retarget_action(context, adapter, action, target)
    motion = vmd.File()
    motion.header = vmd.Header()
    name = target.parent.mmd_root.name if getattr(target.parent, "mmd_type", None) == "ROOT" else target.name
    # The header is informational; truncate at a character boundary.
    motion.header.model_name = name.encode("cp932", errors="replace")[:20].decode("cp932", errors="ignore")
    motion.boneAnimation = vmd.BoneAnimation()
    names = {name: vmd_bone_name(japanese_name(bone)) for name, bone in mapping.mapped.items()}
    names["Center"] = vmd_bone_name(japanese_name(mapping.center))
    converters = {name: vmd.importer.BoneConverter(bone, 1 / pmx_scale, invert=True)
                  for name, bone in (mapping.mapped | {"Center": mapping.center}).items()}
    interpolation = linear_interpolation()
    previous = {}

    def add(name, frame, rotation, translation):
        converter = converters[name]
        key = vmd.BoneFrameKey()
        key.frame_number = frame
        key.location = converter.convert_location(translation)
        current = converter.convert_rotation((*rotation[1:], rotation.w))
        if name in previous:
            current.make_compatible(previous[name])
        previous[name] = current
        key.rotation = (*current[1:], current.w)
        key.interp = interpolation
        motion.boneAnimation[names[name]].append(key)

    for index, sample in enumerate(retarget_samples(mapping, poses)):
        for name, (translation, rotation) in sample.items():
            add(name, index, rotation, translation)

    if ik_names := mapping.leg_ik():
        motion.propertyAnimation = vmd.PropertyAnimation()
        key = vmd.PropertyFrameKey()
        key.ik_states = [(name, False) for name in ik_names]
        motion.propertyAnimation.append(key)
    path = Path(path)
    with NamedTemporaryFile(prefix=".motionbricks-", suffix=".vmd", dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
    try:
        motion.save(filepath=str(temp))
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return len(frames), len(names)


@contextmanager
def import_stage(context, rig):
    stage = bpy.data.scenes.new("MotionBricks VMD Import")
    clone = None
    action = None
    try:
        clone = rig.copy()
        stage.collection.objects.link(clone)
        clone.animation_data_clear()
        action = bpy.data.actions.new("MotionBricks VMD Sampling")
        animation = clone.animation_data_create()
        animation.action = action
        animation.action_slot = action.slots.new(id_type="OBJECT", name=clone.name)
        clone.parent = None
        clone.matrix_world = Matrix.Identity(4)
        for bone in clone.pose.bones:
            bone.matrix_basis = Matrix.Identity(4)
            bone.rotation_mode = "QUATERNION"
            for constraint in bone.constraints:
                for field in ("target", "pole_target"):
                    if getattr(constraint, field, None) == rig:
                        setattr(constraint, field, clone)
        layer = stage.view_layers[0]
        with context.temp_override(scene=stage, view_layer=layer):
            stage.frame_set(1)
            yield stage, clone
    finally:
        if clone is not None:
            bpy.data.objects.remove(clone, do_unlink=True)
        if action is not None:
            bpy.data.actions.remove(action)
        bpy.data.scenes.remove(stage)


def read_vmd(context, adapter, target, path, pmx_scale):
    vmd = tools()
    mapping = BodyMap(adapter, target)
    # Validate model matching and duration before allocating Action channels.
    with open(path, "rb") as stream:
        try:
            vmd.Header().load(stream)
        except (vmd.InvalidFileError, struct.error) as error:
            raise ValueError("文件不是有效的 VMD 0002 动作。") from error
        count_data = stream.read(4)
        if len(count_data) != 4:
            raise ValueError("VMD 缺少骨骼轨道。")
        count, = struct.unpack("<I", count_data)
        if count > recording.MAX_FRAMES * 128 or os.fstat(stream.fileno()).st_size < 54 + count * 111:
            raise ValueError("VMD 骨骼轨道过大或文件已损坏。")
    motion = vmd.File()
    motion.load(filepath=str(path))
    originals = {japanese_name(bone): bone.name for bone in target.pose.bones if not bone.is_mmd_shadow_bone}
    aliases = {}
    for name, bone in originals.items():
        aliases.setdefault(normalized(name), set()).add(bone)
    input_names = set(motion.boneAnimation)
    input_names.update(name for key in motion.propertyAnimation for name, _enabled in key.ik_states)
    bones = {}
    for name in input_names:
        if name in originals:
            bones[name] = originals[name]
        elif len(matches := aliases.get(normalized(name), ())) == 1:
            bones[name] = next(iter(matches))
    tracks = {name: track for name, track in motion.boneAnimation.items() if name in bones and track}
    body = {bone.name for bone in (mapping.center, *mapping.mapped.values())}
    controls = {"全ての親", "センター", "グルーブ", "腰", *map(normalized, mapping.leg_ik())}
    body.update(bone for name, bone in originals.items() if normalized(name) in controls)
    if not body.intersection(bones[name] for name in tracks):
        raise ValueError("VMD 中没有与当前 PMX 匹配的身体骨骼轨道。")
    if len(tracks) != len({bones[name] for name in tracks}):
        raise ValueError("VMD 有多个动作轨道匹配到同一根 PMX 骨骼。")
    last = max(key.frame_number for track in tracks.values() for key in track)
    if last >= recording.MAX_FRAMES:
        raise ValueError(f"VMD 超过 {recording.MAX_FRAMES} 帧，请先拆成多个片段。")
    if not all(math.isfinite(value) for track in tracks.values() for key in track
               for value in (*key.location, *key.rotation)):
        raise ValueError("VMD 轨道包含无效的数值。")
    poses = []
    with import_stage(context, target) as (stage, clone):
        for name in mapping.leg_ik():
            clone.pose.bones[originals[name]].mmd_ik_toggle = True
        importer = vmd.importer.VMDImporter(
            str(path), scale=pmx_scale, frame_margin=0,
            bone_mapper=lambda rig: {name: rig.pose.bones[bone] for name, bone in bones.items()},
        )
        importer.assign(clone)
        ik_changes = {}
        for key in sorted(motion.propertyAnimation, key=lambda key: key.frame_number):
            ik_changes.setdefault(key.frame_number + 1, []).extend(key.ik_states)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for frame in range(1, last + 2):
            stage.frame_set(frame)
            for name, enabled in ik_changes.get(frame, ()):
                if name in bones:
                    clone.pose.bones[bones[name]].mmd_ik_toggle = bool(enabled)
            if frame in ik_changes:
                stage.view_layers[0].update()
            poses.append(mapping.inbound(clone.evaluated_get(depsgraph)))
    return poses
