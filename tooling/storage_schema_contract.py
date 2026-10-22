"""Source-bound SQLite cohorts for existing read-only acceptance consumers.

Storage and project-document versions are separate contracts. This module does
not open a store, migrate a database, write receipts, or authorize a mutation.
The existing source observer owns checkout admission; these pure validators
interpret its bounded original declaration and the selected SQLite layout.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import re

SCHEMA = "motionwright.storage-source-cohort/1"
SOURCE_PATH = "crates/storage/src/lib.rs"
MAX_SOURCE_BYTES = 2 * 1024 * 1024
DOCUMENT_SCHEMA_AFTER_SAVE = 2
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")
FAMILIES = {2: "historical-v0", 3: "historical-v0",
            4: "v1-with-legacy-zero-null"}
FIELDS = {"schema", "source_sha", "source_tree", "storage_schema",
          "document_schema_after_save", "receipt_family", "source_input"}


class StorageContractError(ValueError):
    """A source or representation mismatch; never permission to retry."""


def require(ok, code):
    if not ok:
        raise StorageContractError(code)


def _identity(value):
    require(isinstance(value, dict) and set(value) == {"bytes", "sha256"},
            "storage_source_identity_fields")
    require(type(value["bytes"]) is int and 0 < value["bytes"] <= MAX_SOURCE_BYTES
            and isinstance(value["sha256"], str) and SHA64.fullmatch(value["sha256"]),
            "storage_source_identity_invalid")


def validate_cohort(value, *, source_sha=None, source_tree=None):
    """Validate an already admitted record; this alone is not source admission."""
    require(isinstance(value, dict) and set(value) == FIELDS,
            "storage_cohort_fields")
    require(value["schema"] == SCHEMA
            and isinstance(value["source_sha"], str) and SHA40.fullmatch(value["source_sha"])
            and isinstance(value["source_tree"], str) and SHA40.fullmatch(value["source_tree"]),
            "storage_cohort_source_invalid")
    require(source_sha is None or value["source_sha"] == source_sha,
            "storage_cohort_source_mismatch")
    require(source_tree is None or value["source_tree"] == source_tree,
            "storage_cohort_tree_mismatch")
    version = value["storage_schema"]
    require(type(version) is int and version in FAMILIES,
            "storage_cohort_version_unsupported")
    require(type(value["document_schema_after_save"]) is int
            and value["document_schema_after_save"] == DOCUMENT_SCHEMA_AFTER_SAVE
            and value["receipt_family"] == FAMILIES[version],
            "storage_cohort_family_mismatch")
    item = value["source_input"]
    require(isinstance(item, dict) and set(item) == {
        "path", "git", "checkout", "checkout_encoding"}
        and item["path"] == SOURCE_PATH, "storage_source_input_fields")
    _identity(item["git"])
    _identity(item["checkout"])
    require(item["checkout_encoding"] in {"EXACT_GIT_BYTES", "EXACT_LF_TO_CRLF"},
            "storage_source_encoding_unsupported")
    require(item["checkout_encoding"] != "EXACT_GIT_BYTES"
            or item["git"] == item["checkout"], "storage_source_exact_bytes_mismatch")
    return copy.deepcopy(value)


def admit_source(observation, original):
    """Interpret one literal declaration in the already admitted source bytes.

    This is deliberately narrower than a Rust parser. The reviewed declaration
    must occupy one uncommented line; duplicate, unsupported or conditional
    declarations are not inferred from another source or an observed database.
    """
    require(isinstance(observation, dict) and
            all(key in observation for key in ("sha", "tree", "files")),
            "storage_source_observation_missing")
    require(type(original) is bytes and 0 < len(original) <= MAX_SOURCE_BYTES
            and b"\0" not in original, "storage_source_bytes_invalid")
    files = observation["files"]
    require(isinstance(files, dict) and SOURCE_PATH in files,
            "storage_source_not_observed")
    item = files[SOURCE_PATH]
    require(isinstance(item, dict) and set(item) == {
        "git_bytes", "git_sha256", "checkout", "checkout_encoding"},
        "storage_source_observation_fields")
    actual = {"bytes": len(original), "sha256": hashlib.sha256(original).hexdigest()}
    require(item["checkout"] == actual, "storage_original_bytes_changed")
    try:
        text = original.decode("utf-8", "strict")
    except UnicodeError as error:
        raise StorageContractError("storage_source_not_utf8") from error
    # Remove comments before matching the fixed declaration. Reject nested or
    # unfinished block-comment ambiguity rather than guessing Rust semantics.
    comments = re.findall(r"/\*.*?\*/", text, flags=re.S)
    require(all("/*" not in part[2:] for part in comments),
            "storage_source_comment_ambiguous")
    code = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    require("/*" not in code and "*/" not in code, "storage_source_comment_ambiguous")
    code = re.sub(r"//[^\r\n]*", "", code)
    declarations = re.findall(
        r"(?m)^[ \t]*pub const STORAGE_SCHEMA_VERSION: u32 = ([0-9]+);[ \t]*\r?$", code)
    require(len(declarations) == 1, "storage_source_declaration_not_unique")
    declaration = re.search(
        r"(?m)^[ \t]*pub const STORAGE_SCHEMA_VERSION: u32 = ([0-9]+);[ \t]*\r?$", code)
    previous = [line.strip() for line in code[:declaration.start()].splitlines() if line.strip()]
    require(not previous or not (previous[-1].startswith("#[") or previous[-1].endswith("]")),
            "storage_source_declaration_attributed")
    version = int(declarations[0])
    record = {"schema": SCHEMA, "source_sha": observation["sha"],
              "source_tree": observation["tree"], "storage_schema": version,
              "document_schema_after_save": DOCUMENT_SCHEMA_AFTER_SAVE,
              "receipt_family": FAMILIES.get(version),
              "source_input": {"path": SOURCE_PATH,
                  "git": {"bytes": item["git_bytes"], "sha256": item["git_sha256"]},
                  "checkout": actual, "checkout_encoding": item["checkout_encoding"]}}
    return validate_cohort(record)


def admit_checkout(repo, source_sha):
    """Reuse the existing canonical observer; never select by PRAGMA user_version."""
    if __package__:
        from .source_checkout_observation import source_observation
    else:
        from source_checkout_observation import source_observation
    root = Path(repo).absolute()
    require(root == root.resolve(strict=True), "storage_source_root_not_canonical")
    path = root / SOURCE_PATH
    for current in (path, *path.parents):
        if current == root.parent:
            break
        info = current.lstat()
        require(not current.is_symlink()
                and not getattr(info, "st_file_attributes", 0) & 0x400,
                "storage_source_path_linked")
    before = source_observation(root, source_sha, (SOURCE_PATH,))
    raw = path.read_bytes()
    result = validate_cohort(admit_source(before, raw), source_sha=source_sha)
    require(source_observation(root, source_sha, (SOURCE_PATH,)) == before,
            "storage_source_changed_during_admission")
    return result


def expected_schema(cohort, *, historical_default=3):
    """Missing explicit admission retains only the existing historical contract."""
    require(type(historical_default) is int and historical_default in (2, 3),
            "storage_default_not_historical")
    return (historical_default if cohort is None
            else validate_cohort(cohort)["storage_schema"])


def validate_receipt_columns(columns, cohort):
    """Check the selected column family; row/binding semantics belong to the oracle."""
    version = validate_cohort(cohort)["storage_schema"]
    require(isinstance(columns, (list, tuple)) and 0 < len(columns) <= 128,
            "storage_receipt_columns_missing")
    require(all(isinstance(row, (list, tuple)) and len(row) == 7
                and isinstance(row[1], str) and isinstance(row[2], str) for row in columns),
            "storage_receipt_columns_malformed")
    names = [row[1].casefold() for row in columns]
    require(len(set(names)) == len(names), "storage_receipt_column_alias")
    reserved = {"apply_binding_version", "apply_binding_json"}
    selected = {row[1].casefold(): row for row in columns if row[1].casefold() in reserved}
    if version in (2, 3):
        require(not selected, "historical_storage_has_reserved_binding_columns")
        return
    require(set(selected) == reserved, "storage_binding_columns_missing")
    marker, payload = selected["apply_binding_version"], selected["apply_binding_json"]
    require(marker[1] == "apply_binding_version" and marker[2].upper() == "INTEGER"
            and marker[3] == 1 and marker[4] == "0" and marker[5] == 0 and marker[6] == 0
            and payload[1] == "apply_binding_json" and payload[2].upper() == "TEXT"
            and payload[3] == 0 and payload[4] is None and payload[5] == 0 and payload[6] == 0,
            "storage_binding_columns_differ")


def validate_document(document, cohort):
    version = validate_cohort(cohort)["storage_schema"]
    allowed = (1, 2) if version == 2 else (2,)
    require(isinstance(document, dict) and type(document.get("schema_version")) is int
            and document["schema_version"] in allowed,
            "project_document_schema_differs_from_storage_cohort")
