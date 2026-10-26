# Compiler-owned fixed probe. _GRAPH_DATA contains helper/font constants only.
# No Scene is instantiated, Graph is created, or archive intake is asserted.
def _fixed_graph_profile_observation():
    if len(sys.argv) != 2:
        raise ValueError("graph_profile_probe_exact_arguments")
    root = Path(sys.argv[1])
    runtime, helpers = _graph_load_helpers(root)
    coverage = sys.modules["manim_graph_font_probe"]
    lock = _GRAPH_DATA["font_lock"]
    text = _GRAPH_DATA["sample_text"]
    font_size = _GRAPH_DATA["sample_font_size_px"]
    text_bytes = text.encode("ascii", "strict")
    families, assets = [], None
    for package in lock["packages"]:
        editorial = package["editorial_family"]
        selected, files = runtime.verify_fonts(root, lock, editorial)
        current_assets = [{
            "relative_path": item["relative_path"],
            "sha256": item["sha256"], "size_bytes": item["size_bytes"],
        } for item in files]
        if assets is None:
            assets = current_assets
        else:
            coverage.require(assets == current_assets, "graph_profile_probe_asset_inventory_changed")
        native = coverage.NativePango()
        observation = native.observe(
            list(selected), package["expected_native_family"], text_bytes,
            font_size, editorial == "Instrument Sans Variable",
        )
        runs = coverage.validate_observation(
            observation, selected, package["expected_native_family"], text_bytes,
        )
        runtime.verify_fonts_unchanged(root, files)
        families.append({
            "editorial_family": editorial,
            "native_family": package["expected_native_family"],
            "unknown_glyph_count": observation["unknown_glyph_count"],
            "pango_version": observation["pango_version"],
            "fontconfig_version": observation["fontconfig_version"],
            "runs": runs,
        })
    return {
        "schema": "motionwright.manim.fixed-graph-profile-observation/1",
        "profile_id": "mw-manim-semantic-graph-v1",
        "contract_sha256": lock["contract_sha256"],
        "manim_version": str(sys.modules["manim"].__version__),
        "sample_text": text, "sample_font_size_px": font_size,
        "coverage_scope": "fixed_ascii_sample_only",
        "helper_pins": helpers, "asset_pins": assets, "families": families,
        "future_label_coverage_verified": False,
        "render_performed": False, "scene_mutation_performed": False,
        "profile_admitted": False,
    }


_encoded = json.dumps(_fixed_graph_profile_observation(), ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False)
if len(_encoded.encode("ascii")) > _GRAPH_DATA["max_output_bytes"]:
    raise ValueError("graph_profile_probe_output_budget")
sys.stdout.write(_encoded)
