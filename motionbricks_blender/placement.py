"""The shared scene-space transform for a pose and its sampled motion."""

from mathutils import Matrix, Vector

from .rig import matrix_from_pose, pose_from_matrix, pose_root_position


def slots(state):
    return [(state.start, state.start_placement), *((target.pose, target.placement) for target in state.targets)]


def selected_index(state):
    if 0 <= state.target_index < len(state.targets):
        return state.target_index + 1
    return 0 if state.start else -1


def location(action, placement):
    anchor = pose_root_position(action)
    if anchor is None:
        return None
    return tuple(value + offset for value, offset in zip(anchor, placement.offset, strict=True))


def set_location(action, placement, position):
    anchor = pose_root_position(action)
    if anchor is not None:
        placement.offset = tuple(value - origin for value, origin in zip(position, anchor, strict=True))


def matrix_at(anchor, placement):
    if not placement.yaw and not any(placement.offset):
        return Matrix.Identity(4)
    origin = Vector(anchor)
    rotation = Matrix.Rotation(placement.yaw, 4, "Z")
    rotation.translation = origin + Vector(placement.offset) - rotation.to_3x3() @ origin
    return rotation


def place_frames(snapshot, frames, placement):
    if not placement.yaw and not any(placement.offset):
        return frames
    matrix = matrix_at(snapshot["pose"][0][:3], placement)
    return [[pose_from_matrix(matrix @ matrix_from_pose(pose[0])), *pose[1:]] for pose in frames]
