# 开发与验证

扩展源码位于 `motionbricks_blender/`。模块 `rig.py` 负责骨骼坐标与 Action，`jobs.py` 管理本地任务，`backend.py` 在独立 Python 进程中加载动作或执行推理。

侧栏按用途使用独立的 Blender 子面板。新增工具区可沿用 `MotionBricksPanel`，将 `bl_parent_id` 指向 `MOTIONBRICKS_PT_main`，并通过 `bl_order` 安排位置。

在仓库根目录运行以下命令。也可以将 Blender 可执行文件路径替换为自己的安装位置。

```bash
blender=blender
"$blender" --background --factory-startup --python-exit-code 1 --python tests/blender_smoke.py
"$blender" --background --factory-startup --python-exit-code 1 --python tests/blender_recording.py
"$blender" --background --factory-startup --python-exit-code 1 --python tests/blender_route.py
"$blender" --background --factory-startup --python-exit-code 1 --python tests/blender_export.py
"$blender" --background --factory-startup --python-exit-code 1 --python tests/blender_pose_library.py

mkdir -p dist
"$blender" --command extension build --source-dir motionbricks_blender --output-dir dist
"$blender" --command extension validate dist/motionbricks_blender-0.10.0.zip
```

后台测试使用真实的 SOMA 模型，覆盖参考骨架导入、BVH 预览及恢复、姿势资产、两段生成和工程保存重开。另用官方运行时的蒙皮结果逐顶点核对三个 BVH 采样帧，并检查手臂和腿的骨骼方向。MMD 回归脚本位于 `tests/blender_mmd.py`，需启用两项扩展并在 `--` 后传入本地人形 PMX 路径。扩展代码采用 GPL-3.0-or-later。[许可证](../motionbricks_blender/LICENSE)

[GitHub 发布步骤](releasing.md)
