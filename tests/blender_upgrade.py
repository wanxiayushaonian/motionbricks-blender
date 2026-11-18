"""Test hot-installing the extension in a Blender session with stale modules.

Install the v0.1.0 archive with -e into an isolated Blender profile first.
Pass --after-failed-install to exercise recovery from a failed v0.1.1 update.
"""

from pathlib import Path
import sys

import addon_utils
import bpy

NAME = "bl_ext.user_default.motionbricks_blender"
DIST = Path(__file__).resolve().parents[1] / "dist"


def install(version):
    return bpy.ops.extensions.package_install_files(
        filepath=str(DIST / f"motionbricks_blender-{version}.zip"),
        repo="user_default", enable_on_install=True,
    )


old = sys.modules[NAME + ".rig"]
assert not hasattr(old, "CURRENT_BIND_REVISION")
addon_utils.disable(NAME, default_set=True)
assert addon_utils.check(NAME) == (False, False)
assert sys.modules[NAME + ".rig"] is old

if "--after-failed-install" in sys.argv:
    try:
        install("0.1.1")
    except RuntimeError as error:
        assert "CURRENT_BIND_REVISION" in str(error), str(error)
    else:
        raise AssertionError("The original update failure was not reproduced.")
    assert sys.modules[NAME + ".rig"] is old

assert install("0.1.2") == {"FINISHED"}
assert addon_utils.check(NAME) == (True, True)
assert sys.modules[NAME + ".rig"].CURRENT_BIND_REVISION == 2
assert bpy.context.scene.motionbricks is not None
print("PASS: MotionBricks enabled in the same Blender session after installing v0.1.2", flush=True)
