# 安装与排错

当前发布为测试版。已验证环境是 Blender 5.1.2、Linux x86-64 和 NVIDIA RTX 4060 Laptop GPU。Windows 的 Python 路径已适配，完整安装与 GPU 流程待实测。后端使用 Python 3.12，固定官方 MotionBricks v0.2.0 提交 `54fc563aeaaf819a753d72cc9fdc6713989cdd43`。

已验证连接现有环境、官方资源下载与校验、取消及重试保护，以及实际补间。完整冷缓存依赖安装本轮中止，仍待独立验证。

## 安装插件和新后端

1. 在本仓库 GitHub Releases 下载 `motionbricks_blender-0.10.0.zip`。Blender 中打开「编辑 > 偏好设置 > 获取扩展 > 从磁盘安装」，选择 ZIP 并启用。
2. 安装 Git 和 uv。在 Blender 的「偏好设置 > 系统」启用联网访问。
3. 进入 3D 视口的「N > MotionBricks > 后端配置」，展开「配置新后端」。选择空目录。默认是系统用户数据目录下的 `motionbricks-blender/backends/v0.2.0`。
4. 点击「配置 / 继续安装」，阅读上游代码与模型许可并勾选接受。默认下载 SOMA FP16，需要 FP32 路线精度时可另选 FP32。
5. 安装完成后运行「检查已有后端」。Python、MotionBricks、USD、TensorRT、GPU 和必需模型检查通过后，可以创建 SOMA 参考角色。

安装会获取固定的官方源码、SOMA 骨架与所选模型，用官方 `uv.lock` 创建独立 Python 环境。资源按 Git LFS 中的大小和 SHA-256 校验。TensorRT 运行时下载约 3.1 GB，连同模型、环境和包缓存建议预留至少 10 GB。安装日志位于后端目录的 `.motionbricks-setup.log`。

可以取消配置任务。再次使用相同目录配置，会核对资源并重试未完成步骤。安装目标可以是空目录或本插件之前创建的目录，已有手动配置环境请走下方连接流程。

[Git 安装](https://git-scm.com/downloads) · [uv 安装](https://docs.astral.sh/uv/getting-started/installation/) · [模型协议](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-agreement/)

## 连接已有后端

在「后端配置」的「已有后端」中选择 MotionBricks 根目录，点击「检查已有后端」。该目录应包含 `motionbricks/`、`pyproject.toml` 和 `.venv`，Python 位于 Linux 的 `.venv/bin/python` 或 Windows 的 `.venv/Scripts/python.exe`。

手动安装可参考官方步骤，并固定以上提交，运行 `uv sync --locked --python 3.12`。模型和骨架的 Git LFS 指针须换成真实资源文件。检查会报告版本不一致、缺失依赖或仍为 LFS 指针的文件。

已有后端检查只连接和诊断。使用其他源码提交或修改过的环境时，需自行验证兼容性。

[官方安装说明](https://github.com/NVlabs/motionbricks/tree/54fc563aeaaf819a753d72cc9fdc6713989cdd43#setup)

## 第一次生成

可以自行在 SOMA 姿态模式摆两个姿势，保存后设为起始与目标，也可导入自己有权使用的录制。先按绑定姿势保存起始，稍微移动目标根节点并调整手臂，再点击「生成补间」。

首次使用某种模型精度时会构建 TensorRT 引擎，可能需要几分钟。插件在后台执行并显示进度。缓存留在用户后端中，可由后续运行复用。

官方列出的 BVH 示例有单独的评估用途和分发限制。加载此类数据前阅读其条款。正式制作使用自己的动作或已获得相应授权的动作。MMD 互通另需安装 MMD Tools，并使用有相应使用权限的 PMX/PMD 模型。

[示例动作许可与适用文件](https://github.com/NVlabs/motionbricks/blob/54fc563aeaaf819a753d72cc9fdc6713989cdd43/assets/motions/README.md)

## 常见问题

| 情况 | 处理 |
| --- | --- |
| 未找到 Git 或 uv | 安装后重启 Blender，让它继承更新后的 PATH。插件也会查找用户的 `.local/bin`。 |
| 下载失败 | 检查网络、系统证书和日志，再点「配置 / 继续安装」。HTTPS 始终校验服务器证书。 |
| 模型仍是 Git LFS 指针 | 下载实际模型文件，或对插件管理的目录重新配置。 |
| 没有 CUDA GPU | 检查 NVIDIA 驱动与 GPU。当前生成后端依赖 NVIDIA CUDA / TensorRT。 |
| 缺少 FP32 | 默认 FP16 可继续使用。需要 FP32 时勾选该项再配置同一后端目录。 |
| 首次生成耗时较长 | 等待 TensorRT 构建，进度在面板顶部。可查看后端日志或取消任务。 |
| MMD 功能不可用 | 单独安装并启用 MMD Tools，选择工作骨架和 MMD 目标骨架。 |

反馈问题时附上 Blender 版本、系统、GPU、插件版本和诊断结果。发布前检查日志中的个人路径等信息。

## 从早期版本升级

可在 Blender 中用「从磁盘安装」覆盖安装当前版本。新版会刷新 Blender 会话里残留的旧模块，修复覆盖安装 0.1.1 时的启用报错。0.1.1 的修正版示例工程仍适用。

0.1.1 使用 USD 的蒙皮绑定矩阵，并重新对齐 Blender 骨骼的显示方向。旧工程中的骨架与已烘焙 Action 使用旧绑定。

- 已有 0.1.1 示例工程可在本机继续使用。公开示例优先使用自摆姿势或已授权动作。
- 在自己的旧工程中，先另存一份。在物体模式点击「创建 SOMA 参考角色」，然后在大纲视图中隐藏旧角色所在的集合。已保存的 MotionBricks 姿势和目标序列可以用插件的「应用」和「生成补间」在新角色上复用。需要在 Blender 原生姿势库中使用时，可应用到新角色后重新保存为姿势资产。


[返回项目首页](../README.md) · [使用指南](user-guide.md)
