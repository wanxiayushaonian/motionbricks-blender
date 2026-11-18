# MotionBricks for Blender

在 Blender 中挑选动作、保存姿势、编排目标，并使用 NVIDIA MotionBricks 生成可编辑的骨骼补间。通过 MMD Tools，可在 PMX 角色上预览动作，并导入、导出 VMD。

这是社区开发的 Blender 插件，使用独立的 MotionBricks 后端运行本地推理。

[下载安装包](https://github.com/wanxiayushaonian/motionbricks-blender/releases/tag/v0.10.0) · [安装与排错](docs/installation.md) · [使用指南](docs/user-guide.md) · [English](README.en.md)

## 可以做什么

| 功能 | 操作 |
| --- | --- |
| 浏览动作 | 搜索和筛选 SOMA BVH、导入录制及生成结果，播放、拖动进度和逐帧取姿势 |
| 整理姿势 | 自定义名称和分组，直接保存为补间的起始或目标 |
| 编排与编辑 | 在视口查看目标姿势，调整位置和朝向，进入姿态模式修改骨骼 |
| 生成补间 | 按目标序列生成 Action 或 NLA 条带，继续在 Blender 中编辑 |
| 路线行走 | 沿 Blender 曲线或 Walker JSON 路线生成动作 |
| MMD 互通 | 在 PMX 角色上同步预览，导入和导出 VMD |
| 录制交换 | 导入和导出 NPZ，在不同工程间复用动作 |

## 使用要求

当前版本 **0.10.0** 为测试版。

| 项目 | 要求与验证情况 |
| --- | --- |
| Blender | 5.1.2 |
| 系统 | Linux x86-64 已验证，Windows 完整流程待实测 |
| GPU | 生成依赖 NVIDIA CUDA / TensorRT，已在 RTX 4060 Laptop GPU 验证 |
| 后端 | MotionBricks v0.2.0，安装工具使用固定提交和 Python 3.12 |
| MMD 功能 | 另需安装并启用 MMD Tools |

已验证连接已有后端、资源下载与校验，以及实际补间。完整冷缓存依赖安装仍待验证。界面目前使用中文。

## 下载与安装

先下载插件 ZIP，在 Blender 的 **编辑 → 偏好设置 → 获取扩展 → 从磁盘安装** 中安装并启用。进入 3D 视口，按 **N** 打开 **MotionBricks → 后端配置**。

**已有 MotionBricks 环境时**，在「已有后端」中选择环境根目录，点击「检查已有后端」。

**首次配置后端时**，先安装 Git 和 uv，启用 Blender 联网访问，再展开「配置新后端」，选择空目录并点击「配置 / 继续安装」。阅读并接受上游许可后开始配置。新环境连同模型和包缓存建议预留至少 10 GB。

首次生成会构建 TensorRT 缓存，可能需要几分钟。目录选择、下载重试和常见错误的处理见安装文档。[详细安装步骤](docs/installation.md)

## 生成第一段动作

1. **创建角色**。点击「创建 SOMA 参考角色」，确认顶部的「工作骨架」。
2. **准备姿势**。手动摆姿势，或从有相应使用权限的动作中取帧，保存一个起始姿势和至少一个目标。
3. **编排目标**。在「目标序列」开启视口预览，调整目标的位置、朝向和顺序。
4. **生成补间**。选择输出到 Action 或 NLA，点击「生成补间」，播放并检查结果。
5. **用于 MMD**。选择 PMX 目标骨架，点击「在目标角色上预览」，确认后导出 VMD。

姿势分组、骨骼编辑、动势、时长选项和文件交换的具体操作见使用指南。[完整使用指南](docs/user-guide.md)

## 文档与反馈

- [安装与排错](docs/installation.md) — 新环境配置、连接已有环境、旧版升级。
- [使用指南](docs/user-guide.md) — 动作库、姿势资产、补间、路线和 MMD。
- [更新记录](CHANGELOG.md) — 各版本功能变更。
- [开发与验证](docs/development.md) — 源码结构、测试与本地构建。
- [GitHub 问题反馈](https://github.com/wanxiayushaonian/motionbricks-blender/issues) — 请附插件、Blender、系统和 GPU 版本，以及错误信息。

## 许可与致谢

插件采用 GPL-3.0-or-later。MotionBricks 源码、模型、官方示例动作和 MMD 素材分别适用其许可。

官方列出的示例动作受评估用途及分发限制。公开示例和正式制作请使用自己的姿势、动作或已获得相应授权的素材。

[插件许可证](LICENSE) · [第三方声明](motionbricks_blender/THIRD_PARTY_NOTICES.md) · [NVIDIA MotionBricks](https://github.com/NVlabs/motionbricks) · [MMD Tools](https://github.com/MMD-Blender/blender_mmd_tools)
