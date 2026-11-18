# Third-party notices

MotionBricks Blender is a community Blender integration. NVIDIA authors the separate MotionBricks runtime and pretrained models.

The add-on is distributed under GPL-3.0-or-later. A copy is included as `LICENSE`.

## Walker adaptation

`route_walker.py` is adapted from `walker/mb_walk.py` in hyun2xyz/motionbricks-blender.

Copyright 2026 hyun2xyz. SPDX license identifier Apache-2.0.

The adaptation connects the walking generator to this add-on's worker, progress reporting and route input. The original copyright and modification notice remain in the source. The Apache 2.0 text is included as `LICENSE-WALKER`.

[Original Walker source](https://github.com/hyun2xyz/motionbricks-blender/blob/main/walker/mb_walk.py)

## External MotionBricks backend

The setup tool obtains NVIDIA MotionBricks v0.2.0 at commit `54fc563aeaaf819a753d72cc9fdc6713989cdd43`.

Upstream code and the listed NVIDIA SOMA rig contributions use Apache 2.0. ONNX models use the NVIDIA Open Model Agreement. Setup presents these terms before downloading. Each environment retains upstream license files.

[Upstream license](https://github.com/NVlabs/motionbricks/blob/54fc563aeaaf819a753d72cc9fdc6713989cdd43/LICENSE)  
[Model license](https://github.com/NVlabs/motionbricks/blob/54fc563aeaaf819a753d72cc9fdc6713989cdd43/motionbricks/assets/onnx/LICENSE.md)

## Motion samples and MMD assets

The official motion samples listed by NVIDIA have a separate Sample Data Evaluation License. Users must follow its internal evaluation and redistribution conditions when using these files or derivatives. The backend installer downloads the SOMA rig and selected ONNX models. Model motion samples are obtained separately by the user for evaluation.

PMX/PMD models, VMD files and other user assets retain their respective licenses. MMD Tools is a separately installed optional Blender extension.

[Official sample-data notice](https://github.com/NVlabs/motionbricks/blob/54fc563aeaaf819a753d72cc9fdc6713989cdd43/assets/motions/README.md)  
[Sample license](https://github.com/NVlabs/motionbricks/blob/54fc563aeaaf819a753d72cc9fdc6713989cdd43/assets/motions/LICENSE.txt)  
[MMD Tools](https://github.com/MMD-Blender/blender_mmd_tools)
