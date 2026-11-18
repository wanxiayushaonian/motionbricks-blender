# GitHub 测试版发布

源码仓库包含插件、测试、安装说明和许可证。发行 ZIP 由 Blender 扩展构建命令生成，放入 GitHub Release 附件。

## 首次公开

1. 将仓库中待跟踪的源码加入 Git，检查首次提交内容。提交中应包含插件源码、文档、许可证和构建流程。
2. 在自己的 GitHub 账号下创建仓库，将其设为 remote，再推送源码。
3. 打 `v0.10.0` 标签并推送，发布工作流会校验扩展，创建 prerelease 并附上安装 ZIP 和 SHA-256。也可手动运行工作流进行构建验证，或在本机构建附件后自行发布。
4. 从 Release 附件下载 ZIP，在空白 Blender 配置和新的后端目录中验证安装。
5. 版本说明标注 Linux 已验证、Windows 待实测，以及 GPU 和首次构建需求。

本地 `dist/` 被 Git 忽略，其中保留的测试工程和导出动作应按来源核对许可。发布工作流只选择扩展 ZIP 与校验文件作为附件。发布示例可使用自摆姿势和适当授权的模型与动作。

## 版本与构建

`blender_manifest.toml` 保存插件版本。`backend_config.py` 固定上游 release 和 commit。更换上游前，用独立目录执行完整安装和实际补间测试。

```sh
blender --command extension build --source-dir motionbricks_blender --output-dir dist
blender --command extension validate dist/motionbricks_blender-0.10.0.zip
sha256sum dist/motionbricks_blender-0.10.0.zip
```

## 反馈与兼容性

GitHub 问题模板收集 Blender、系统、GPU、后端版本和错误。公开前核对日志中的个人路径。

当前源码级验证包括本地 Blender 的骨架蒙皮、预览恢复、姿势资产、分组和补间。新的后端安装使用官方锁文件，运行时缓存由用户的 GPU 环境构建。Windows 完整安装与推理属于待实测项。

[安装说明](installation.md) · [第三方声明](../motionbricks_blender/THIRD_PARTY_NOTICES.md)
