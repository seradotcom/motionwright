# Compiler-owned proposed-text probe. The only request input is bounded JSON.
# It cannot select a program, helper, runtime, font file or profile permission.
def _proposed_graph_text_observation():
    root = Path(sys.argv[1])
    raw = sys.stdin.buffer.read(_GRAPH_DATA["max_input_bytes"] + 1)
    if not raw or len(raw) > _GRAPH_DATA["max_input_bytes"]:
        raise ValueError("graph_text_probe_input_budget")
    request = json.loads(raw)
    if (set(request) != {"schema", "host_request_id", "purpose", "source_binding_sha256", "editorial_family", "font_size_px", "blocks"}
            or request["schema"] != "motionwright.manim.graph-text-probe-request/1"
            or request["purpose"] not in ("inspect_authoring", "upsert")
            or not 1 <= len(request["blocks"]) <= _GRAPH_DATA["max_blocks"]):
        raise ValueError("graph_text_probe_request_contract")
    if request["purpose"] == "inspect_authoring" and (
            request["font_size_px"] != _GRAPH_DATA["availability_font_size_px"]
            or request["blocks"] != [{"ordinal": 0, "text": _GRAPH_DATA["availability_sample"]}]):
        raise ValueError("graph_text_probe_authoring_control_only")
    if str(sys.modules["manim"].__version__) != "0.21.0":
        raise ValueError("graph_text_probe_manim_version")
    runtime, helpers = _graph_load_helpers(root)
    coverage = sys.modules["manim_graph_font_probe"]
    lock = _GRAPH_DATA["font_lock"]
    editorial = request["editorial_family"]
    package = next((row for row in lock["packages"] if row["editorial_family"] == editorial), None)
    coverage.require(package is not None, "graph_text_probe_family")
    selected, files = runtime.verify_fonts(root, lock, editorial)
    native = coverage.NativePango()
    blocks = []
    pango_version, fontconfig_version = None, None
    for ordinal, block in enumerate(request["blocks"]):
        coverage.require(set(block) == {"ordinal", "text"}
                         and type(block["ordinal"]) is int and block["ordinal"] == ordinal,
                         "graph_text_probe_block_contract")
        text = block["text"]
        coverage.require(type(text) is str and text.strip()
                         and not any((ord(ch) <= 31 and ch != "\n") or ord(ch) == 127 for ch in text),
                         "graph_text_probe_text_contract")
        text_bytes = text.encode("utf-8", "strict")
        coverage.require(0 < len(text_bytes) <= coverage.MAX_TEXT_BYTES,
                         "graph_text_probe_text_budget")
        size = request["font_size_px"]
        coverage.require(type(size) in (int, float) and 4 <= size <= 512,
                         "graph_text_probe_font_size")
        observation = native.observe(list(selected), package["expected_native_family"],
                                     text_bytes, size, editorial == "Instrument Sans Variable")
        runs = coverage.validate_observation(observation, selected,
                                             package["expected_native_family"], text_bytes)
        if pango_version is None:
            pango_version, fontconfig_version = observation["pango_version"], observation["fontconfig_version"]
        else:
            coverage.require(pango_version == observation["pango_version"]
                             and fontconfig_version == observation["fontconfig_version"],
                             "graph_text_probe_runtime_changed")
        blocks.append({"ordinal": ordinal, "text_sha256": hashlib.sha256(text_bytes).hexdigest(),
                       "text_bytes": len(text_bytes), "unknown_glyph_count": observation["unknown_glyph_count"],
                       "runs": runs})
    runtime.verify_fonts_unchanged(root, files)
    return {
        "schema": "motionwright.manim.graph-text-probe-observation/1",
        "host_request_id": request["host_request_id"], "purpose": request["purpose"],
        "profile_id": "mw-manim-semantic-graph-v1", "contract_sha256": lock["contract_sha256"],
        "source_binding_sha256": request["source_binding_sha256"], "request_sha256": hashlib.sha256(raw).hexdigest(),
        "editorial_family": editorial, "native_family": package["expected_native_family"],
        "font_size_px": request["font_size_px"], "manim_version": str(sys.modules["manim"].__version__),
        "pango_version": pango_version, "fontconfig_version": fontconfig_version,
        "helper_pins": helpers,
        "asset_pins": [{"relative_path": row["relative_path"], "sha256": row["sha256"],
                        "size_bytes": row["size_bytes"]} for row in files],
        "blocks": blocks, "glyph_coverage_verified": True,
        "coverage_scope": ("fixed_ascii_control_only" if request["purpose"] == "inspect_authoring"
                           else "exact_proposed_graph_text_only"),
        "future_label_coverage_verified": False, "box_fit_verified": False,
        "render_performed": False, "scene_mutation_performed": False, "profile_admitted": False,
    }


_encoded = json.dumps(_proposed_graph_text_observation(), ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False)
if len(_encoded.encode("ascii")) > _GRAPH_DATA["max_output_bytes"]:
    raise ValueError("graph_text_probe_output_budget")
sys.stdout.write(_encoded)
