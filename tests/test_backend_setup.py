"""Standard-library regression checks for installer isolation and verified downloads."""

import io
from pathlib import Path, PurePosixPath
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from hashlib import sha256

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "motionbricks_blender"))
import backend_config as config
import setup_backend as setup


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}


class SetupTests(unittest.TestCase):
    def test_verified_asset_resume_and_corruption(self):
        data = b"verified model payload" * 100
        pointer = f"version https://git-lfs.github.com/spec/v1\noid sha256:{sha256(data).hexdigest()}\nsize {len(data)}\n"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "model.onnx"
            partial = root / "model.onnx.part"
            partial.write_bytes(data[:100])
            response = Response(data[100:], status=206, headers={"Content-Range": f"bytes 100-{len(data)-1}/{len(data)}"})
            with patch.object(setup.subprocess, "check_output", return_value=pointer), patch.object(setup, "urlopen", return_value=response):
                setup.download_asset(root, "model.onnx", "git", root, {})
            self.assertEqual(model.read_bytes(), data)
            self.assertFalse(partial.exists())
            model.write_bytes(b"corruption")
            with patch.object(setup.subprocess, "check_output", return_value=pointer), patch.object(setup, "urlopen", return_value=Response(b"x" * len(data))):
                with self.assertRaisesRegex(ValueError, "校验失败"):
                    setup.download_asset(root, "model.onnx", "git", root, {})
            self.assertEqual(model.read_bytes(), b"corruption")
            self.assertFalse(partial.exists())

    def test_partial_download_can_retry(self):
        data = b"complete payload"
        pointer = f"oid sha256:{sha256(data).hexdigest()}\nsize {len(data)}\n"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(setup.subprocess, "check_output", return_value=pointer), patch.object(setup, "urlopen", return_value=Response(data[:5])):
                with self.assertRaisesRegex(ValueError, "尚未完成"):
                    setup.download_asset(root, "asset.onnx", "git", root, {})
            self.assertEqual((root / "asset.onnx.part").read_bytes(), data[:5])

    def test_existing_directory_and_concurrent_install_are_preserved(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "existing"
            target.mkdir()
            important = target / "user-file.txt"
            important.write_text("preserve")
            request = {"accept_terms": True, "repository": str(target)}
            with patch.object(setup, "executable", return_value="/tool"), patch.object(setup, "run") as command:
                with self.assertRaisesRegex(ValueError, "已有文件"):
                    setup.install(request, root)
                command.assert_not_called()
            self.assertEqual(important.read_text(), "preserve")
            with setup.install_lock(target):
                with self.assertRaisesRegex(ValueError, "已有安装任务"):
                    with setup.install_lock(target):
                        self.fail("second installer acquired the same directory")
            with setup.install_lock(target):
                pass

    def test_terms_are_required(self):
        with self.assertRaisesRegex(ValueError, "许可"):
            setup.install({"accept_terms": False}, Path("."))

    def test_platform_python_paths(self):
        with patch.object(config.os, "name", "nt"):
            # pathlib follows the host platform, so check the path rule through its path argument.
            with patch.object(config, "Path", return_value=PurePosixPath("/backend")):
                self.assertEqual(str(config.python_path("ignored")), "/backend/.venv/Scripts/python.exe")
        self.assertTrue(str(config.python_path("/backend")).endswith(".venv/bin/python"))


if __name__ == "__main__":
    unittest.main()
