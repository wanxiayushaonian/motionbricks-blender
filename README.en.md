# MotionBricks for Blender

A community Blender extension for browsing SOMA motion, capturing reusable poses, generating transitions, and exchanging body animation with MMD.

The current release is an experimental build. Tested with Blender 5.1.2 on Linux x86-64 and an NVIDIA RTX 4060 Laptop GPU. Windows path handling is implemented; Windows installation and GPU inference still require validation.

## Install

[Download test releases](https://github.com/wanxiayushaonian/motionbricks-blender/releases) · [中文](README.md)

1. Download `motionbricks_blender-0.10.0.zip` from this repository's GitHub Releases and install it using Blender's **Install from Disk**.
2. Install Git and uv, then enable network access in Blender preferences.
3. Open **N sidebar > MotionBricks > 后端配置**. Expand **配置新后端**, choose an empty directory, and click **配置 / 继续安装**.
4. Read and accept the upstream code and model terms. Setup downloads the fixed upstream revision, SOMA rig and selected model, then runs the upstream locked Python installation. Allow at least 10 GB for model files, the environment and package caches.
5. Run **检查已有后端** to inspect dependencies, CUDA and model files. The first generation builds a local TensorRT engine.

You can connect an existing backend by selecting its root directory instead. Setup pins NVIDIA MotionBricks v0.2.0 commit `54fc563aeaaf819a753d72cc9fdc6713989cdd43`, with Python 3.12. An existing environment is expected to use the same backend API.

[Git](https://git-scm.com/downloads) · [uv](https://docs.astral.sh/uv/getting-started/installation/) · [Detailed Chinese setup guide](docs/installation.md)

## Workflow

Create a SOMA reference rig and set it as the working rig. Pose it in Blender or load an authorized motion, then save a starting pose and one or more targets. Adjust placement and generate a new Action or NLA strip.

The motion library supports search, source and directory filters, selection playback and timeline scrubbing. Poses support names, custom groups and direct capture into the target sequence. MMD Tools adds PMX-target preview and VMD import/export.

The add-on interface currently uses Chinese labels. MMD exchange covers body motion and fingers. Facial animation, model physics and camera motion remain part of the MMD workflow.

## Dependencies and licenses

The extension uses GPL-3.0-or-later and includes its third-party notices. NVIDIA MotionBricks source, rigs, models and example motions retain their respective terms. The model uses the NVIDIA Open Model Agreement. Official sample motions are covered separately by the Sample Data Evaluation License and have internal evaluation and redistribution restrictions.

Use your own poses or appropriately licensed motion for public examples and production. Consult each MMD asset's terms.

[Extension license](LICENSE) · [Third-party notices](motionbricks_blender/THIRD_PARTY_NOTICES.md) · [Upstream MotionBricks](https://github.com/NVlabs/motionbricks)

## Build

Run Blender from the repository root.

```sh
blender --command extension build --source-dir motionbricks_blender --output-dir dist
blender --command extension validate dist/motionbricks_blender-0.10.0.zip
```

GPU integration tests run separately on a configured local backend. The release workflow builds and validates the extension package.
