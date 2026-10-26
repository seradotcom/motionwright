

class MotionwrightScene(Scene):
    def construct(self):
        source = Path(__file__).absolute()
        # Host sets this from its already validated runtime mount. For an
        # extracted native source project, the identical bundled closure is
        # beside scene.py. Neither option grants execution to an importer.
        root = Path(os.environ.get(
            "MOTIONWRIGHT_MANIM_RUNTIME_ROOT", str(source.parent))).absolute()
        native, observation = _graph_load_helpers(root)
        native.render_graph(self, _GRAPH_DATA, source, root, observation)
