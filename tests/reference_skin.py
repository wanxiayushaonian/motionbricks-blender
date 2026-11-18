"""Sample the upstream SOMA clip and Newton's skinning for an independent check."""

import json
from pathlib import Path
import sys

import warp as wp

from motionbricks.io.bvh import load_bvh
from motionbricks.io.utils import get_soma_human_model
from motionbricks.renderers.skeletal_mesh_renderer import SkeletalMeshRenderer
from motionbricks.utils.space_conversion_utils import FacingDirectionType, SpaceConverter


def main(clip_path, result_path):
    with wp.ScopedDevice("cpu"):
        mesh = get_soma_human_model()
        skeleton = mesh.skeleton
        clip = load_bvh(Path(clip_path), skeleton)
        conversion = SpaceConverter(FacingDirectionType.MUJOCO).convert_transform(wp.transform_identity())
        renderer = SkeletalMeshRenderer(mesh)
        pose = wp.empty(skeleton.num_joints, dtype=wp.transform)
        world = wp.empty_like(pose)
        frames = []
        for seconds in (0.0, 1.0, 53 / 30):
            clip.sample(seconds, pose, root_offset=conversion)
            skeleton.compute_world_transforms(pose, world)
            renderer._skin_pose(pose, wp.transform_identity())
            frames.append({
                "seconds": seconds,
                "heads": world.numpy()[:, :3].tolist(),
                "vertices": renderer.skinned_points[0].numpy().tolist(),
            })
        Path(result_path).write_text(json.dumps({"joints": skeleton.joint_names, "frames": frames}))


if __name__ == "__main__":
    main(*sys.argv[1:])
