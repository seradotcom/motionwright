#!/usr/bin/env python3
"""Verify/reproduce fixed native SFNT assets from the retained WOFF2 originals.

This source maintenance utility uses FontTools 4.59.0 and Brotli 1.1.0. It never
runs in Native, installs fonts, grants a profile or writes a Project. Runtime
staging copies already pinned assets; no converter runs after Host launch.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
from pathlib import Path
import struct

from manim_bundled_font_intake import NATIVE_FONT_DERIVATIONS, require


def verify(repo):
    import fontTools
    import brotli
    from fontTools.ttLib import TTFont, woff2
    require(fontTools.__version__ == "4.59.0" and brotli.__version__ == "1.1.0",
            "native_font_decoder_versions")
    root = Path(repo) / "crates/manim-profile/testdata/graph-fonts"
    observations = []
    for row in NATIVE_FONT_DERIVATIONS:
        raw = (root / row["source_relative_path"]).read_bytes()
        require(len(raw) == row["source_size_bytes"]
                and hashlib.sha256(raw).hexdigest() == row["source_sha256"], "native_font_source_pin")
        results = []
        for _ in range(2):
            result = io.BytesIO()
            woff2.decompress(io.BytesIO(raw), result)
            results.append(result.getvalue())
        actual = results[0]
        require(actual == results[1] and actual[:4] == b"\x00\x01\x00\x00"
                and len(actual) == row["size_bytes"]
                and hashlib.sha256(actual).hexdigest() == row["sha256"], "native_font_decoded_pin")
        require((root / row["relative_path"]).read_bytes() == actual, "native_font_committed_bytes")
        padded = actual + b"\0" * ((-len(actual)) % 4)
        require(sum(struct.unpack(">" + str(len(padded) // 4) + "I", padded)) & 0xffffffff
                == 0xb1b0afba, "native_font_sfnt_checksum")
        with TTFont(io.BytesIO(raw), recalcBBoxes=False, recalcTimestamp=False) as source, \
                TTFont(io.BytesIO(actual), recalcBBoxes=False, recalcTimestamp=False) as native:
            require(set(source.keys()) == set(native.keys())
                    and source.getGlyphOrder() == native.getGlyphOrder()
                    and source.getBestCmap() == native.getBestCmap(), "native_font_glyph_identity")
            tables = []
            for tag in source.keys():
                if tag in ("GlyphOrder", "head", "loca"):
                    continue
                left, right = io.BytesIO(), io.BytesIO()
                source.saveXML(left, tables=[tag])
                native.saveXML(right, tables=[tag])
                require(left.getvalue() == right.getvalue(), "native_font_decoded_table_changed:" + tag)
                tables.append(tag)
            left, right = source["head"].__dict__.copy(), native["head"].__dict__.copy()
            # These describe physical SFNT packing, not outlines or metrics.
            for key in ("checkSumAdjustment", "indexToLocFormat"):
                left.pop(key, None)
                right.pop(key, None)
            require(left == right, "native_font_head_semantics_changed")
        observations.append({**row, "decoded_tables_equal": tables,
                             "glyph_order_and_cmap_equal": True, "repeat_byte_identical": True,
                             "sfnt_checksum_verified": True})
    return {"schema": "motionwright.manim.native-font-reproduction/1", "fonts": observations,
            "fonttools_version": fontTools.__version__, "brotli_version": brotli.__version__,
            "native_font_open_observed": False, "profile_admitted": False, "graph_save_performed": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    print(json.dumps(verify(args.repo), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
