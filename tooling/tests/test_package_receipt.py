import importlib.util
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "package_receipt", ROOT / "tooling" / "package_receipt.py"
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class PackageReceiptTests(unittest.TestCase):
    def test_bundle_classification_is_platform_bounded(self) -> None:
        self.assertEqual(
            module.classify("linux", pathlib.Path("Motionwright_0.1.0_amd64.AppImage")),
            "appimage",
        )
        self.assertEqual(
            module.classify("linux", pathlib.Path("Motionwright_0.1.0_amd64.deb")),
            "deb",
        )
        self.assertEqual(
            module.classify("windows", pathlib.Path("Motionwright_0.1.0_x64-setup.exe")),
            "nsis",
        )
        self.assertEqual(
            module.classify("macos", pathlib.Path("Motionwright_0.1.0_aarch64.dmg")),
            "dmg",
        )

    def test_windows_rejects_generic_executable(self) -> None:
        with self.assertRaisesRegex(ValueError, "NSIS -setup.exe"):
            module.classify("windows", pathlib.Path("motionwright.exe"))

    def test_cross_platform_suffix_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported linux"):
            module.classify("linux", pathlib.Path("Motionwright.dmg"))

    def test_sha256_is_streamed_and_exact(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "artifact"
            path.write_bytes(b"motionwright-candidate")
            self.assertEqual(
                module.sha256(path),
                "41033d9ba4a55fe8742c3f8da24ade04edc25a5c2dcfa2354fafb947db3c7849",
            )


if __name__ == "__main__":
    unittest.main()
