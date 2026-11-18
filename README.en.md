# MotionBricks for Blender

Browse motion, save poses, arrange targets and generate editable skeletal transitions inside Blender using NVIDIA MotionBricks. MMD Tools integration adds PMX-target preview and VMD import/export.

This community Blender extension runs local inference through a separate MotionBricks backend.

[Download](https://github.com/wanxiayushaonian/motionbricks-blender/releases/tag/v0.10.0) · [Setup guide (中文)](docs/installation.md) · [User guide (中文)](docs/user-guide.md) · [中文](README.md)

## Features

| Feature | What you can do |
| --- | --- |
| Motion library | Search and filter SOMA BVH, recordings and generated results; play, scrub and capture poses |
| Pose library | Name and group poses; capture directly into the start/target sequence |
| Pose editing | Place targets in the viewport and edit their bones in Pose Mode |
| Transitions | Generate editable Actions or NLA strips through a target sequence |
| Route walking | Generate motion along Blender curves or Walker JSON routes |
| MMD exchange | Preview on PMX rigs and import/export VMD |
| Recording exchange | Import/export NPZ to reuse motion across projects |

## Requirements

Version **0.10.0** is an experimental release. The interface currently uses Chinese labels.

| Component | Requirement and validation |
| --- | --- |
| Blender | 5.1.2 |
| OS | Linux x86-64 tested; the complete Windows workflow needs validation |
| GPU | Generation requires NVIDIA CUDA / TensorRT; tested on an RTX 4060 Laptop GPU |
| Backend | MotionBricks v0.2.0; setup pins an upstream commit and uses Python 3.12 |
| MMD features | Install and enable MMD Tools separately |

Existing-backend connection, asset downloads and verification, and generation are tested. A complete cold-cache dependency installation is still pending validation.

## Install

Download the extension ZIP and install it using Blender's **Edit → Preferences → Get Extensions → Install from Disk**. In the 3D Viewport, open the **N sidebar → MotionBricks → 后端配置**.

**Existing backend** — Select its root directory in **已有后端**, then click **检查已有后端**.

**New backend** — Install Git and uv and enable network access in Blender. Expand **配置新后端**, choose an empty directory and click **配置 / 继续安装**. Read and accept the upstream terms to start setup. Allow at least 10 GB for a new environment, models and package caches.

The first generation builds a local TensorRT engine and may take several minutes. Directory selection, retry behavior and troubleshooting are covered in the setup guide. [Setup details (中文)](docs/installation.md)

## Generate your first motion

1. Click **创建 SOMA 参考角色** and confirm the working rig, **工作骨架**.
2. Pose the rig manually or capture frames from appropriately licensed motion. Save a starting pose and at least one target.
3. Open the sequence viewport preview and arrange target positions, headings and order.
4. Click **生成补间** to generate an Action or NLA strip, then play the result.
5. For MMD, choose the PMX target rig and click **在目标角色上预览**. Export VMD after checking the motion.

The user guide covers pose groups, bone editing, motion context, timing options and file exchange. [Full user guide (中文)](docs/user-guide.md)

## Documentation and feedback

- [Setup and troubleshooting (中文)](docs/installation.md)
- [User guide (中文)](docs/user-guide.md)
- [Changelog](CHANGELOG.md)
- [Development and validation (中文)](docs/development.md)
- [Report an issue](https://github.com/wanxiayushaonian/motionbricks-blender/issues) — Include add-on, Blender, OS and GPU versions, plus the relevant error.

## License and credits

The extension uses GPL-3.0-or-later. NVIDIA MotionBricks code, models, official motion samples and MMD assets retain their respective terms.

The listed official motion samples have internal-evaluation and redistribution restrictions. Use your own or appropriately licensed poses and motion for public examples and production.

[Extension license](LICENSE) · [Third-party notices](motionbricks_blender/THIRD_PARTY_NOTICES.md) · [NVIDIA MotionBricks](https://github.com/NVlabs/motionbricks) · [MMD Tools](https://github.com/MMD-Blender/blender_mmd_tools)
