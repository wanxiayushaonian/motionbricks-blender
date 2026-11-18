# MotionBricks for Blender

在 Blender 中浏览 SOMA 动作包、保存可复用的姿势，并用本地 MotionBricks 生成可编辑的骨骼补间。当前开发基线是 Blender 5.1.2 Linux、SOMA 参考角色和 FP16 模型。

## 安装

[下载测试版](https://github.com/wanxiayushaonian/motionbricks-blender/releases) · [English](README.en.md)

当前为社区开发的测试版。已验证 Blender 5.1.2、Linux x86-64、NVIDIA RTX 4060 Laptop GPU。Windows 路径已适配，完整安装和推理待实测。后端固定为官方 MotionBricks v0.2.0 提交 `54fc563aeaaf819a753d72cc9fdc6713989cdd43`。

1. 从本仓库 GitHub Releases 下载 `motionbricks_blender-0.10.0.zip`。在 Blender 的 **编辑 > 偏好设置 > 获取扩展 > 从磁盘安装** 中安装并启用。
2. 安装 Git 与 uv，并在 Blender 系统偏好设置中启用联网访问。
3. 打开 **N > MotionBricks > 后端配置 > 配置新后端**，选择空目录，点击「配置 / 继续安装」。阅读并接受上游代码与模型条款后，将从官方获取源码、骨架与 SOMA 模型，用锁文件创建独立环境。
4. 已有 MotionBricks 环境时，填写「已有后端」，运行「检查已有后端」。检查通过后创建 SOMA 角色，保存姿势并生成补间。

默认下载 SOMA FP16，可选择 FP32。建议预留至少 10 GB 空间，首次生成会在本机构建 TensorRT 缓存。取消后可以在同一目录重试。安装日志位于后端目录的 `.motionbricks-setup.log`。

[详细安装与排错](docs/installation.md) · [更新记录](CHANGELOG.md) · [English](README.en.md) · [第三方声明](motionbricks_blender/THIRD_PARTY_NOTICES.md)

## 从 0.1.0 或 0.1.1 更新

可在 Blender 中用「从磁盘安装」覆盖安装当前版本。新版会刷新 Blender 会话里残留的旧模块，修复覆盖安装 0.1.1 时的启用报错。0.1.1 的修正版示例工程仍适用。

0.1.1 使用 USD 的蒙皮绑定矩阵，并重新对齐 Blender 骨骼的显示方向。旧工程中的骨架与已烘焙 Action 使用旧绑定。

- 已有 0.1.1 示例工程可在本机继续使用。公开示例优先使用自摆姿势或已授权动作。
- 在自己的旧工程中，先另存一份。在物体模式点击「创建 SOMA 参考角色」，然后在大纲视图中隐藏旧角色所在的集合。已保存的 MotionBricks 姿势和目标序列可以用插件的「应用」和「生成补间」在新角色上复用。需要在 Blender 原生姿势库中使用时，可应用到新角色后重新保存为姿势资产。

## 操作

安装并启用插件后，可从空场景创建角色，并用自摆姿势体验补间。官方列出的动作样例适用独立评估许可，使用前阅读其条款。[官方样例说明](https://github.com/NVlabs/motionbricks/blob/54fc563aeaaf819a753d72cc9fdc6713989cdd43/assets/motions/README.md)

1. 点「创建 SOMA 参考角色」，导入仓库里的 SOMA 网格和骨架。创建后，插件会把该骨架设为侧栏顶部的「工作骨架」，并显示「目标序列」。可按需展开「姿势资产」「动作库」和「角色」。
2. 在「动作库」导入录制，或展开「BVH 动作包」，载入内置样例或扫描自己的 SOMA BVH 目录。使用顶部搜索框或按来源和目录筛选。选中片段并点「播放片段」，也可打开「选中即播放」。播放时拖动「进度」可暂停并定位到片段中的时刻，三角按钮用于逐帧微调。「退出」会恢复之前的 Action、姿势和预览范围。
3. 停在想要的一帧，点击「保存为起始」或「保存并添加为目标」，即可同时保存姿势资产并放入目标序列。名称自动带上片段名和片段内的时间，勾选「保留动势」会同时保存 30 Hz 下的前后三帧。要自定义名称，可点「另存姿势」，也可以在姿态模式里手动调整后从「姿势资产」保存。
4. 在「姿势资产」中选一个姿势，点「设为起始」或「添加目标」。目标列表显示摆位后的根节点世界坐标（X / Y / Z，单位米），Z 是高度。目标可以替换、删除和排序。选择目标，或点起始姿势右边的定位按钮，展开「选中姿势摆位」编辑世界坐标、朝向偏移和「保留动势」。
5. 点「目标序列」顶部的「视口预览」，在同一个 3D 视口查看所有姿势。绿色是起始姿势，蓝色是其他目标，橙色是选中的目标。点击编号旁的圆点选择姿势。拖动三色箭头可平移，拖动橙色圆环可绕世界 Z 轴转向。姿势重叠时可以勾选「只看选中」，放大镜按钮会框入当前可见的姿势。
6. 展开「生成设置」选择起始帧和输出方式。每个目标的「选中姿势摆位」里可设定到此段时长。点「生成补间」后可在面板顶部查看进度，也可以取消。生成完成后，「生成设置」会列出各段的实际到达帧，Action 标记取最接近的整数帧。保存 `.blend` 即可保留姿势资产、编排和生成结果。

第一版使用参考 SOMA 骨架和与之兼容的 BVH。模型决定过渡时长，插件按当前工程帧率放置关键帧。

## 摆位与姿势资产

摆位存到起始项和各个目标项里。同一姿势资产可在同一个序列里用多次，各自放到不同位置。替换目标时保留该项的世界位置和朝向偏移。选择不同的起始资产时从它原有的位置与朝向开始。点「恢复原位」可还原选中项的原始坐标和朝向，也可用 Blender 的撤销恢复修改。

开启「保留动势」时，捕获的相邻帧会跟着选中姿势一起平移和转向。关闭后，起始项使用四个相同姿势作为上下文，目标项使用单帧。0.2.0 及更早工程缺失的摆位默认以原姿势坐标为准。

### 姿势命名与自定义分组

在「姿势资产」中使用列表选中姿势，直接编辑下方的「名称」。目标序列和视口标签会跟随资产名称更新。同名姿势由 Blender 自动追加编号。

点击分组筛选旁的「+」创建分组，例如「起跳」「落地」或「知更鸟常用」。用「移动到分组」将选中姿势放入已有分组，也可移回「未分组」。筛选到自定义分组后，新保存的姿势会进入该组，包括动作库的「保存为起始」「保存并添加为目标」。「全部分组」和「未分组」视图中新保存的姿势进入「未分组」。

分组旁的铅笔用于改名，减号用于删除。删除分组后，其中的姿势回到「未分组」。编辑骨骼另存出的姿势继承原姿势分组。分组和姿势归属保存在当前 `.blend` 中，并在文件内的场景间共享。旧姿势默认属于「未分组」。

### 编辑骨骼

在「目标序列」中选中目标，或用起始姿势右边的按钮选中起始项，然后点「编辑骨骼」。插件会暂停动作并将选中的姿势按当前摆位应用到真实 SOMA 骨架。进入 Blender 姿态模式后，选中手臂、腿或躯干骨骼，用 `R` 旋转。

点「保存姿势」会另存一个姿势资产，并用它替换正在编辑的序列项。原姿势资产可以继续使用。如果取帧时保存了相邻帧，编辑时可选「保留动势」，把骨骼调整一并应用到相邻帧。取消勾选可保存为静态姿势。点「取消」放弃调整。保存或取消后会恢复编辑前的 Action 和时间轴位置。

### 补间时长与 NLA

「偏短」和「偏长」会缩小模型可选的时长范围。选「自动」可用模型默认范围。到达帧由生成结果与工程帧率换算，可能落在两个整数帧之间。

「活动 Action」用于直接预览和编辑生成结果。「NLA 条带」会把新动作放到指定起始帧。在启用了 NLA 评估的角色上，原有活动 Action 会进入下方条带，生成动作在其上方播放。

## 导入 MotionBricks 录制

确认 MotionBricks 的工作骨架后，在「动作库」点击「导入录制 (.npz)」，也可用 **文件 > 导入 > MotionBricks 录制 (.npz)**。支持参考版 Walker 和 Recorder 生成的 `motionbricks-blender-npz` v1，每份录制最多 12,000 帧。

导入创建独立 Action，按导入时的工程帧率保留源时长，和 BVH 一样跟随参考角色的物体摆放。导入成功后可以自行开始播放。录制和新版生成结果都可在动作库搜索、播放或取帧，保存 `.blend` 后可以离开源文件继续使用。动作库右上角的叉号只移除选中录制或生成结果的列表条目。

## 导出 MotionBricks 录制

确认工作骨架并选中动作库中的 Action，点「导出录制 (.npz)」，也可用 **文件 > 导出 > MotionBricks 录制 (.npz)**。选择文件路径和导出帧率。文件包含所选 Action 的完整帧范围、SOMA 骨架和参考网格蒙皮，可在另一个工程导入，也兼容参考版导入器。

导出采样包含动作的首尾姿势。NPZ v1 使用固定的整数帧率，时长会取最接近的采样间隔，最多导出 12,000 帧。原始帧范围和工程帧率记录在文件元数据中。导入时从目标工程的第一帧播放，后续可在时间轴安排位置。

## 路线行走

确认 MotionBricks 的工作骨架后，展开「路线行走」。选择场景中的单条贝塞尔或多段线样条，也可以选择 Walker 路线 JSON。曲线路径按世界坐标的 XY 平面取样，角色使用曲线起点的地面高度。JSON 使用 `start: [x, y, yaw]`、`waypoints: [[x, y], …]`、可选的 `speed` 和 `end_yaw`，地面高度默认是 0 米。转向以角度表示，0° 朝世界 −Y，90° 朝 +X。

可以调整行走速度、起止朝向和停留时间。曲线默认按路径方向推断朝向，JSON 默认采用文件里的设置。FP16 为默认，也可选择 FP32。路线生成在后台运行，可以取消。生成完成后在「动作库」播放、取帧，或将捕获的姿势加入目标序列。选中路线动作可查看根节点终点误差，以及最后一段停步推理的末五个输出帧测得的平均水平速度。角色沿路点行走，绕开障碍物时请在曲线或 JSON 中设置绕行路线。


## MMD 动作互通

1. 安装并启用 [MMD Tools](https://extensions.blender.org/add-ons/mmd-tools/)，用它将 `.pmx` 或 `.pmd` 导入到含 SOMA 参考角色的同一个 Blender 场景。记下导入比例，MMD Tools 默认是 0.08。
2. 在 **MotionBricks** 顶部确认「工作骨架」。补间、动作取帧、路线和 VMD 互通都使用这套 SOMA。旧工程中指定的 SOMA 会作为默认来源，当前视图层只有一套 SOMA 时也会自动识别。有多套时，可在顶部明确指定。选中 PMX 或 `OUTPUT` 时可以继续补间，「编辑骨骼」会激活工作骨架并进入姿态模式。
3. 在 **MMD 动作互通** 中选择 PMX 的骨架对象。在「动作库」选中 BVH、录制或生成结果，然后点「在目标角色上预览」。SOMA 和 PMX 在同一时间轴上播放，可用动作库的暂停和逐帧控制。退出预览会恢复原动画、IK 设置、时间位置和预览范围。保存工程也会先结束 MMD 预览。
4. **从 MotionBricks 到 MMD**。确认动作后，填入 MMD Tools 导入模型时使用的比例，再点「导出 VMD」。导出 BVH 时可先用普通播放或 MMD 预览载入。到 MikuMikuDance 中打开对应的 PMX，再加载 VMD。
5. **从 MMD 到 MotionBricks**。选择与 VMD 对应的 MMD 骨架，填入模型导入比例，点「导入 VMD」。转好的动作从工程第 1 帧开始，可在「动作库」中播放、保存姿势并作为补间目标。

映射使用 PMX 中的日文骨名，支持常见双足角色的身体和手指骨骼。VMD 按 30 fps 采样，导出时水平轨迹从角色原点开始，位移按两套骨架的腿长换算。导出的腿部使用 FK，VMD 在第 0 帧关闭对应的足部 IK。导入会采样 MMD Tools 计算后的骨架姿势，包括足部 IK。面部表情、镜头、灯光、物理和特殊骨骼需要在 MMD 工作流中处理。

## 开发与验证

扩展源码位于 `motionbricks_blender/`。模块 `rig.py` 负责骨骼坐标与 Action，`jobs.py` 管理本地任务，`backend.py` 在独立 Python 进程中加载动作或执行推理。

侧栏按用途使用独立的 Blender 子面板。新增工具区可沿用 `MotionBricksPanel`，将 `bl_parent_id` 指向 `MOTIONBRICKS_PT_main`，并通过 `bl_order` 安排位置。

从本目录运行以下命令。也可以将 Blender 可执行文件路径替换为自己的安装位置。

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

后台测试使用真实的 SOMA 模型，覆盖参考骨架导入、BVH 预览及恢复、姿势资产、两段生成和工程保存重开。另用官方运行时的蒙皮结果逐顶点核对三个 BVH 采样帧，并检查手臂和腿的骨骼方向。MMD 回归脚本位于 `tests/blender_mmd.py`，需启用两项扩展并在 `--` 后传入本地人形 PMX 路径。扩展代码采用 GPL-3.0-or-later。[许可证](motionbricks_blender/LICENSE)

[GitHub 发布步骤](docs/releasing.md)
