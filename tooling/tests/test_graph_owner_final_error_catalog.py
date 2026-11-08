"""Source-only alignment of original owner final error statuses and closed contracts.

Final UNKNOWN receipts must retain typed errors rather than being discarded by
a schema mismatch. This checks only original source, never Host, G2 or Graph6.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
OWNER = ROOT / "tooling/native_graph_browser_owner.py"
FINAL_JSON = ROOT / "apps/studio/tests/support/graph-owner/schemas/final.r2.json"
JS = ROOT / "apps/studio/tests/support/graph-owner/contracts.mjs"
NEW_CODE = "PROVIDER_RELEASE_ORIGINAL_MISSING"


def source_catalog() -> tuple[set[str], set[str]]:
    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    item = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_FINAL_SCHEMA" for t in n.targets))
    schema = ast.literal_eval(item.value)
    catalog = set(schema["properties"]["errors"]["items"]["enum"])
    final = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name == "graph_close_v6")
    emitted = {
        n.args[0].value for n in ast.walk(final)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "append" and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "errors" and len(n.args) == 1
        and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)
    }
    return catalog, emitted


def contracted_catalogs() -> tuple[set[str], set[str]]:
    json_catalog = set(json.loads(FINAL_JSON.read_text(encoding="utf-8"))
                       ["properties"]["errors"]["items"]["enum"])
    text = JS.read_text(encoding="utf-8")
    match = re.search(r"export const ERROR_CODES = new Set\(\[(.*?)\]\);", text, flags=re.S)
    if match is None:
        raise AssertionError("closed JS ERROR_CODES projection missing")
    js_catalog = set(re.findall(r"'([A-Z_]+)'", match.group(1)))
    return json_catalog, js_catalog


class GraphOwnerFinalErrorCatalog(unittest.TestCase):
    def test_three_closed_error_code_catalogs_are_identical(self):
        owner, _ = source_catalog()
        node, js = contracted_catalogs()
        self.assertEqual(owner, node)
        self.assertEqual(owner, js)

    def test_all_owner_final_emitted_literals_are_schema_admissible(self):
        owner, emitted = source_catalog()
        node, js = contracted_catalogs()
        self.assertIn("OWNER_CLOSE_FAILED", emitted)
        for label, codes in (("owner", owner), ("node", node), ("js", js)):
            with self.subTest(projection=label):
                self.assertFalse(emitted - codes,
                                 "original owner final errors missing: " +
                                 ",".join(sorted(emitted - codes)))

    def test_missing_provider_release_keeps_a_typed_unknown_not_schema_failure(self):
        owner, emitted = source_catalog()
        node, js = contracted_catalogs()
        self.assertIn(NEW_CODE, emitted)
        self.assertIn(NEW_CODE, owner)
        self.assertIn(NEW_CODE, node)
        self.assertIn(NEW_CODE, js)
        self.assertNotIn("PROVIDER_RELEASE_APPROVED", owner)

    def test_closed_final_error_vocab_cannot_claim_acceptance(self):
        owner, emitted = source_catalog()
        self.assertNotIn("PASS", owner)
        self.assertNotIn("CLOSED", owner)
        self.assertNotIn("G2_GRANTED", owner)
        self.assertTrue(emitted <= owner)


if __name__ == "__main__":
    unittest.main()
