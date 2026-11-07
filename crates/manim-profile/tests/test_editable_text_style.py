"""Native Manim text-style regression; run in the pinned Linux runtime.

This constructs Pango/SVG glyphs with the production class. It does not render
a scene or claim editable-source-cycle acceptance. Missing native dependencies
or different runtime versions are errors, never skipped or mocked results.
"""

import ast
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest


PROFILE = Path(__file__).resolve().parents[1]
TEMPLATE = PROFILE / "src" / "editable_scene.py"
FONT = PROFILE / "tests" / "fixtures" / "Montserrat-variable.ttf"
FONT_SHA = "0f7b311b2f3279e4eef9b2f968bcdbab6e28f4daeb1f049f4f278a902bcd82f7"


def _restore_environment(previous):
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _production_text_class():
    # Load the trusted class and its real imports, without executing scene setup
    # or replacing any Manim/Pango API. Unbound template data stays unexecuted.
    tree = ast.parse(TEMPLATE.read_text(encoding="utf-8"), filename=str(TEMPLATE))
    body = [
        node for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        or isinstance(node, ast.ClassDef) and node.name == "MotionwrightPlainText"
    ]
    namespace = {"__name__": "motionwright_native_text_style"}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(TEMPLATE), "exec"), namespace)
    return namespace["MotionwrightPlainText"]


class NativeEditableTextStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != "linux":
            raise RuntimeError("native text-style regression requires the pinned Linux runtime")
        if "manim" in sys.modules or "manimpango" in sys.modules:
            raise RuntimeError("run the native text-style regression in a fresh process")
        font_bytes = FONT.read_bytes()
        if hashlib.sha256(font_bytes).hexdigest() != FONT_SHA:
            raise RuntimeError("native text-style fixture font identity changed")
        temporary = tempfile.TemporaryDirectory(prefix="mw-manim-text-style-")
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        font_path = cls.root / "Montserrat.ttf"
        font_path.write_bytes(font_bytes)
        fontconfig = cls.root / "fonts.conf"
        fontconfig.write_text(
            '<?xml version="1.0"?>\n'
            '<fontconfig><dir prefix="relative">.</dir>'
            '<cachedir prefix="relative">cache</cachedir>'
            '<match target="pattern"><edit name="dpi" mode="assign">'
            '<double>96</double></edit></match></fontconfig>\n',
            encoding="utf-8",
        )
        names = ("FONTCONFIG_FILE", "FONTCONFIG_PATH")
        cls.addClassCleanup(_restore_environment, {name: os.environ.get(name) for name in names})
        os.environ["FONTCONFIG_FILE"] = str(fontconfig)
        os.environ["FONTCONFIG_PATH"] = str(cls.root)

        import manim
        import manimpango
        import numpy

        if manim.__version__ != "0.21.0" or manimpango.__version__ != "0.6.1":
            raise RuntimeError("native text-style regression runtime identity differs")
        if not manimpango.register_font(str(font_path)) or "Montserrat" not in manimpango.list_fonts():
            raise RuntimeError("native text-style fixture font registration failed")
        manim.Text.font_list.cache_clear()
        cls.manim, cls.np = manim, numpy
        cls.text_class = _production_text_class()
        # A real Text constructor with the same no-wrap SVG implementation
        # reproduces the pre-fix native container without copying that code.
        cls.before_class = type(
            "UnnormalizedPlainText", (cls.text_class,), {"__init__": manim.Text.__init__}
        )

    def setUp(self):
        context = self.manim.tempconfig({
            "renderer": "cairo",
            "media_dir": str(self.root),
            "text_dir": str(self.root / "text"),
        })
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def make_text(self, color, opacity, *, before=False):
        native_class = self.before_class if before else self.text_class
        return native_class(
            "Native fill", font="Montserrat", font_size=64.0, weight="BOLD",
            slant="NORMAL", color=color, fill_opacity=opacity,
            stroke_width=0, stroke_opacity=0, should_center=True,
            disable_ligatures=False, use_svg_cache=False, warn_missing_font=True,
        )

    def test_fill_matches_container_and_every_native_glyph(self):
        for color, opacity in (("#EAC454", 1.0), ("#EAC454", 0.65), ("#3A7DB8", 0.35)):
            with self.subTest(color=color, opacity=opacity):
                text = self.make_text(color, opacity)
                glyphs = text.family_members_with_points()
                self.assertGreater(len(glyphs), 0)
                for member in [text, *glyphs]:
                    self.assertEqual(member.get_fill_color().to_hex().upper(), color)
                    for observed in member.get_fill_opacities():
                        self.assertAlmostEqual(float(observed), opacity, places=12)
                    self.assertEqual(float(member.get_stroke_width()), 0.0)
                    self.assertEqual(float(member.get_stroke_opacity()), 0.0)

    def test_fill_preserves_native_outlines_and_typography(self):
        before = self.make_text("#EAC454", 0.65, before=True)
        after = self.make_text("#EAC454", 0.65)
        self.assertEqual(before.get_fill_color().to_hex().upper(), "#000000")
        self.assertEqual(after.get_fill_color().to_hex().upper(), "#EAC454")
        self.np.testing.assert_array_equal(before.get_all_points(), after.get_all_points())
        self.assertEqual(len(before.submobjects), len(after.submobjects))
        for property_name in ("original_text", "font", "weight", "font_size"):
            self.assertEqual(getattr(before, property_name), getattr(after, property_name))
        self.assertEqual(before.width, after.width)
        self.assertEqual(before.height, after.height)


if __name__ == "__main__":
    unittest.main(verbosity=2)
