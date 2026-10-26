"""Source-bound SQLite cohorts for existing read-only acceptance consumers.

Storage and project-document versions are separate contracts. This module does
not open a store, migrate a database, write receipts, or authorize a mutation.
The existing source observer owns checkout admission; these read-only validators
interpret its bounded original declaration and the selected SQLite layout.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import re
from uuid import UUID

SCHEMA = "motionwright.storage-source-cohort/1"
SOURCE_PATH = "crates/storage/src/lib.rs"
MAX_SOURCE_BYTES = 2 * 1024 * 1024
DOCUMENT_SCHEMA_AFTER_SAVE = 2
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")
FAMILIES = {2: "historical-v0", 3: "historical-v0",
            4: "v1-with-legacy-zero-null", 5: "v1-with-legacy-zero-null",
            6: "v1-with-legacy-zero-null"}
FIELDS = {"schema", "source_sha", "source_tree", "storage_schema",
          "document_schema_after_save", "receipt_family", "source_input"}

# The schema-5 receipt representation is unchanged. Its CAS control layout is
# separately required; a source-admitted version 5 is not an alias for version 4.
# These are the declarations created by cas_retention::migrate_schema. There are
# no project foreign keys: committed import markers intentionally survive a
# deleted project and must never be treated as a cascading project projection.
CAS_DDL = {
    "cas_store_identity": """CREATE TABLE cas_store_identity (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        store_id TEXT NOT NULL UNIQUE
    )""",
    "cas_import_commits": """CREATE TABLE cas_import_commits (
        operation_id TEXT PRIMARY KEY NOT NULL,
        root_id TEXT NOT NULL,
        store_id TEXT NOT NULL,
        request_sha256 TEXT NOT NULL,
        project_id TEXT NOT NULL,
        generation TEXT NOT NULL
    )""",
}
CAS_COLUMNS = {
    "cas_store_identity": (
        (0, "singleton", "INTEGER", 0, None, 1, 0),
        (1, "store_id", "TEXT", 1, None, 0, 0),
    ),
    "cas_import_commits": tuple(
        (position, name, "TEXT", 1, None, int(position == 0), 0)
        for position, name in enumerate(("operation_id", "root_id", "store_id",
                                         "request_sha256", "project_id", "generation"))
    ),
}
CAS_INDEXES = {
    "cas_store_identity": ("sqlite_autoindex_cas_store_identity_1", "u", 1, "store_id"),
    "cas_import_commits": ("sqlite_autoindex_cas_import_commits_1", "pk", 0, "operation_id"),
}


# Exact source622/candidate278 schema-six declarations; input module 27365 bytes.
BUDGET_SOURCE_PATH = 'crates/storage/src/root_operation_budget.rs'
BUDGET_SOURCE_GIT_BYTES = 27365
BUDGET_SOURCE_GIT_SHA256 = 'a09bf76a6c80fdc08cf0304c9127b5b67b0c3f5b8af0dba1f658486a79cec3b6'
BUDGET_OBJECTS = {'production_operation_debit_budget_gate': ('trigger',
                                            'production_operation_debits',
                                            'CREATE TRIGGER '
                                            'production_operation_debit_budget_gate\n'
                                            'BEFORE INSERT ON production_operation_debits\n'
                                            'BEGIN\n'
                                            " SELECT RAISE(ABORT,'unknown or stale root')\n"
                                            ' WHERE NOT EXISTS(\n'
                                            '  SELECT 1 FROM production_operation_roots AS r\n'
                                            '  WHERE r.project_id=NEW.project_id AND '
                                            'r.root_id=NEW.root_id\n'
                                            '   AND r.generation=NEW.generation AND '
                                            'r.revision=NEW.revision\n'
                                            ' );\n'
                                            " SELECT RAISE(ABORT,'root operation budget "
                                            "exhausted')\n"
                                            ' WHERE EXISTS(\n'
                                            '   SELECT 1 FROM production_operation_roots AS r\n'
                                            '   WHERE r.project_id=NEW.project_id AND '
                                            'r.root_id=NEW.root_id\n'
                                            '     AND (\n'
                                            '       (SELECT COALESCE(SUM(d.attempts),0) FROM '
                                            'production_operation_debits AS d\n'
                                            '        WHERE d.project_id=NEW.project_id AND '
                                            'd.root_id=NEW.root_id)\n'
                                            '            > r.limit_attempts-NEW.attempts\n'
                                            '       OR (SELECT COALESCE(SUM(d.model_calls),0) FROM '
                                            'production_operation_debits AS d\n'
                                            '           WHERE d.project_id=NEW.project_id AND '
                                            'd.root_id=NEW.root_id)\n'
                                            '            > r.limit_model_calls-NEW.model_calls\n'
                                            '       OR (SELECT COALESCE(SUM(d.render_frames),0) '
                                            'FROM production_operation_debits AS d\n'
                                            '           WHERE d.project_id=NEW.project_id AND '
                                            'd.root_id=NEW.root_id)\n'
                                            '            > '
                                            'r.limit_render_frames-NEW.render_frames\n'
                                            '       OR (SELECT COALESCE(SUM(d.output_bytes),0) '
                                            'FROM production_operation_debits AS d\n'
                                            '           WHERE d.project_id=NEW.project_id AND '
                                            'd.root_id=NEW.root_id)\n'
                                            '            > r.limit_output_bytes-NEW.output_bytes\n'
                                            '     )\n'
                                            ' );\n'
                                            'END'),
 'production_operation_debit_immutable': ('trigger',
                                          'production_operation_debits',
                                          'CREATE TRIGGER production_operation_debit_immutable\n'
                                          'BEFORE UPDATE ON production_operation_debits\n'
                                          "BEGIN SELECT RAISE(ABORT,'operation debits are "
                                          "immutable'); END"),
 'production_operation_debit_nodelete': ('trigger',
                                         'production_operation_debits',
                                         'CREATE TRIGGER production_operation_debit_nodelete\n'
                                         'BEFORE DELETE ON production_operation_debits\n'
                                         "BEGIN SELECT RAISE(ABORT,'operation debits cannot be "
                                         "discarded'); END"),
 'production_operation_debits': ('table',
                                 'production_operation_debits',
                                 'CREATE TABLE production_operation_debits(\n'
                                 ' project_id TEXT NOT NULL,\n'
                                 ' request_id TEXT NOT NULL,\n'
                                 ' root_id TEXT NOT NULL,\n'
                                 ' receipt_id TEXT NOT NULL UNIQUE,\n'
                                 ' generation TEXT NOT NULL,\n'
                                 ' revision INTEGER NOT NULL CHECK(revision >= 0),\n'
                                 ' request_sha256 TEXT NOT NULL,\n'
                                 ' command TEXT NOT NULL,\n'
                                 ' attempts INTEGER NOT NULL CHECK(attempts = 1),\n'
                                 ' model_calls INTEGER NOT NULL CHECK(model_calls >= 0),\n'
                                 ' render_frames INTEGER NOT NULL CHECK(render_frames >= 0),\n'
                                 ' output_bytes INTEGER NOT NULL CHECK(output_bytes >= 0),\n'
                                 ' PRIMARY KEY(project_id,request_id),\n'
                                 ' FOREIGN KEY(project_id,root_id) REFERENCES '
                                 'production_operation_roots(project_id,root_id)\n'
                                 '   ON DELETE RESTRICT\n'
                                 ')'),
 'production_operation_dispatch_gate': ('trigger',
                                        'production_receipts',
                                        'CREATE TRIGGER production_operation_dispatch_gate\n'
                                        'BEFORE INSERT ON production_receipts\n'
                                        'WHEN EXISTS(SELECT 1 FROM production_operation_roots '
                                        'WHERE project_id=NEW.project_id)\n'
                                        " AND (NEW.stage='dispatching'\n"
                                        '      OR NOT EXISTS(\n'
                                        '        SELECT 1 FROM production_receipts AS prior\n'
                                        '        WHERE prior.project_id=NEW.project_id AND '
                                        'prior.request_id=NEW.request_id\n'
                                        '      ))\n'
                                        'BEGIN\n'
                                        " SELECT RAISE(ABORT,'budgeted dispatch requires matching "
                                        "retained debit')\n"
                                        ' WHERE NOT EXISTS(\n'
                                        '  SELECT 1 FROM production_operation_debits AS d\n'
                                        '  WHERE d.project_id=NEW.project_id AND '
                                        'd.request_id=NEW.request_id\n'
                                        '   AND d.receipt_id=NEW.receipt_id AND '
                                        'd.generation=NEW.generation\n'
                                        '   AND d.revision=NEW.revision AND d.command=NEW.command\n'
                                        '   AND d.request_sha256=NEW.request_sha256\n'
                                        ' );\n'
                                        'END'),
 'production_operation_root_immutable': ('trigger',
                                         'production_operation_roots',
                                         'CREATE TRIGGER production_operation_root_immutable\n'
                                         'BEFORE UPDATE ON production_operation_roots\n'
                                         "BEGIN SELECT RAISE(ABORT,'operation roots are "
                                         "immutable'); END"),
 'production_operation_root_nodelete': ('trigger',
                                        'production_operation_roots',
                                        'CREATE TRIGGER production_operation_root_nodelete\n'
                                        'BEFORE DELETE ON production_operation_roots\n'
                                        "BEGIN SELECT RAISE(ABORT,'operation roots cannot be "
                                        "discarded'); END"),
 'production_operation_roots': ('table',
                                'production_operation_roots',
                                'CREATE TABLE production_operation_roots(\n'
                                ' project_id TEXT NOT NULL,\n'
                                ' root_id TEXT NOT NULL,\n'
                                ' operation_key TEXT NOT NULL,\n'
                                ' generation TEXT NOT NULL,\n'
                                ' revision INTEGER NOT NULL CHECK(revision >= 0),\n'
                                ' limit_attempts INTEGER NOT NULL CHECK(limit_attempts > 0),\n'
                                ' limit_model_calls INTEGER NOT NULL CHECK(limit_model_calls >= '
                                '0),\n'
                                ' limit_render_frames INTEGER NOT NULL CHECK(limit_render_frames '
                                '>= 0),\n'
                                ' limit_output_bytes INTEGER NOT NULL CHECK(limit_output_bytes >= '
                                '0),\n'
                                ' PRIMARY KEY(project_id,root_id),\n'
                                ' UNIQUE(project_id,operation_key),\n'
                                ' FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE '
                                'RESTRICT\n'
                                ')'),
 'sqlite_autoindex_production_operation_debits_1': ('index', 'production_operation_debits', None),
 'sqlite_autoindex_production_operation_debits_2': ('index', 'production_operation_debits', None),
 'sqlite_autoindex_production_operation_roots_1': ('index', 'production_operation_roots', None),
 'sqlite_autoindex_production_operation_roots_2': ('index', 'production_operation_roots', None)}
BUDGET_METADATA = {'production_operation_roots': {'columns': [(0, 'project_id', 'TEXT', 1, None, 1, 0),
                                            (1, 'root_id', 'TEXT', 1, None, 2, 0),
                                            (2, 'operation_key', 'TEXT', 1, None, 0, 0),
                                            (3, 'generation', 'TEXT', 1, None, 0, 0),
                                            (4, 'revision', 'INTEGER', 1, None, 0, 0),
                                            (5, 'limit_attempts', 'INTEGER', 1, None, 0, 0),
                                            (6, 'limit_model_calls', 'INTEGER', 1, None, 0, 0),
                                            (7, 'limit_render_frames', 'INTEGER', 1, None, 0, 0),
                                            (8, 'limit_output_bytes', 'INTEGER', 1, None, 0, 0)],
                                'foreign_keys': [(0,
                                                  0,
                                                  'projects',
                                                  'project_id',
                                                  'id',
                                                  'NO ACTION',
                                                  'RESTRICT',
                                                  'NONE')],
                                'indexes': [(0,
                                             'sqlite_autoindex_production_operation_roots_2',
                                             1,
                                             'u',
                                             0),
                                            (1,
                                             'sqlite_autoindex_production_operation_roots_1',
                                             1,
                                             'pk',
                                             0)],
                                'index_columns': {'sqlite_autoindex_production_operation_roots_2': [(0,
                                                                                                     0,
                                                                                                     'project_id',
                                                                                                     0,
                                                                                                     'BINARY',
                                                                                                     1),
                                                                                                    (1,
                                                                                                     2,
                                                                                                     'operation_key',
                                                                                                     0,
                                                                                                     'BINARY',
                                                                                                     1),
                                                                                                    (2,
                                                                                                     -1,
                                                                                                     None,
                                                                                                     0,
                                                                                                     'BINARY',
                                                                                                     0)],
                                                  'sqlite_autoindex_production_operation_roots_1': [(0,
                                                                                                     0,
                                                                                                     'project_id',
                                                                                                     0,
                                                                                                     'BINARY',
                                                                                                     1),
                                                                                                    (1,
                                                                                                     1,
                                                                                                     'root_id',
                                                                                                     0,
                                                                                                     'BINARY',
                                                                                                     1),
                                                                                                    (2,
                                                                                                     -1,
                                                                                                     None,
                                                                                                     0,
                                                                                                     'BINARY',
                                                                                                     0)]}},
 'production_operation_debits': {'columns': [(0, 'project_id', 'TEXT', 1, None, 1, 0),
                                             (1, 'request_id', 'TEXT', 1, None, 2, 0),
                                             (2, 'root_id', 'TEXT', 1, None, 0, 0),
                                             (3, 'receipt_id', 'TEXT', 1, None, 0, 0),
                                             (4, 'generation', 'TEXT', 1, None, 0, 0),
                                             (5, 'revision', 'INTEGER', 1, None, 0, 0),
                                             (6, 'request_sha256', 'TEXT', 1, None, 0, 0),
                                             (7, 'command', 'TEXT', 1, None, 0, 0),
                                             (8, 'attempts', 'INTEGER', 1, None, 0, 0),
                                             (9, 'model_calls', 'INTEGER', 1, None, 0, 0),
                                             (10, 'render_frames', 'INTEGER', 1, None, 0, 0),
                                             (11, 'output_bytes', 'INTEGER', 1, None, 0, 0)],
                                 'foreign_keys': [(0,
                                                   0,
                                                   'production_operation_roots',
                                                   'project_id',
                                                   'project_id',
                                                   'NO ACTION',
                                                   'RESTRICT',
                                                   'NONE'),
                                                  (0,
                                                   1,
                                                   'production_operation_roots',
                                                   'root_id',
                                                   'root_id',
                                                   'NO ACTION',
                                                   'RESTRICT',
                                                   'NONE')],
                                 'indexes': [(0,
                                              'sqlite_autoindex_production_operation_debits_2',
                                              1,
                                              'pk',
                                              0),
                                             (1,
                                              'sqlite_autoindex_production_operation_debits_1',
                                              1,
                                              'u',
                                              0)],
                                 'index_columns': {'sqlite_autoindex_production_operation_debits_2': [(0,
                                                                                                       0,
                                                                                                       'project_id',
                                                                                                       0,
                                                                                                       'BINARY',
                                                                                                       1),
                                                                                                      (1,
                                                                                                       1,
                                                                                                       'request_id',
                                                                                                       0,
                                                                                                       'BINARY',
                                                                                                       1),
                                                                                                      (2,
                                                                                                       -1,
                                                                                                       None,
                                                                                                       0,
                                                                                                       'BINARY',
                                                                                                       0)],
                                                   'sqlite_autoindex_production_operation_debits_1': [(0,
                                                                                                       3,
                                                                                                       'receipt_id',
                                                                                                       0,
                                                                                                       'BINARY',
                                                                                                       1),
                                                                                                      (1,
                                                                                                       -1,
                                                                                                       None,
                                                                                                       0,
                                                                                                       'BINARY',
                                                                                                       0)]}}}
WRITER_TABLES = ('projects', 'receipts', 'events', 'production_receipts', 'production_receipt_order', 'render_window_cache_entries', 'render_window_cache_evictions', 'cas_store_identity', 'cas_import_commits')
WRITER_EVENTS = ('INSERT', 'UPDATE', 'DELETE')
WRITER_TEMPLATE = "CREATE TRIGGER {} BEFORE {event} ON {table} BEGIN\n           SELECT RAISE(ABORT,'schema6 registered writer required')\n           WHERE {WRITER_FENCE_FUNCTION}() IS NOT 6;\n         END"


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
    paths = schema_six_source_paths(result)
    if result["storage_schema"] == 6:
        budget_path = root / BUDGET_SOURCE_PATH
        for current in (budget_path, *budget_path.parents):
            if current == root.parent:
                break
            info = current.lstat()
            require(not current.is_symlink()
                    and not getattr(info, "st_file_attributes", 0) & 0x400,
                    "storage_source_path_linked")
        before = source_observation(root, source_sha, paths)
        require(admit_source(before, raw) == result, "storage_source_changed_during_admission")
        validate_schema_six_source(result, before, budget_path.read_bytes())
    require(source_observation(root, source_sha, paths) == before,
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


def _cas_ddl(value):
    require(isinstance(value, str) and 0 < len(value) <= 16384,
            "storage_cas_ddl_missing")
    # The admitted declarations have no quoted values or comments. Whitespace
    # and keyword case are immaterial; additional clauses or constraints are not.
    return re.sub(r"\s+", "", value).casefold()




def schema_six_source_paths(cohort):
    """The /1 cohort still describes lib.rs; schema-six closure also binds this module."""
    return ((SOURCE_PATH, BUDGET_SOURCE_PATH)
            if validate_cohort(cohort)["storage_schema"] == 6 else (SOURCE_PATH,))


def validate_schema_six_observation(cohort, observation):
    """Check the separate canonical budget-module observation, never a runtime grant."""
    selected = validate_cohort(cohort)
    require(selected["storage_schema"] == 6, "storage_schema6_cohort_required")
    require(isinstance(observation, dict)
            and observation.get("sha") == selected["source_sha"]
            and observation.get("tree") == selected["source_tree"],
            "storage_schema6_source_identity_mismatch")
    item = observation.get("files", {}).get(BUDGET_SOURCE_PATH)
    require(isinstance(item, dict), "storage_schema6_source_not_observed")
    require(set(item) == {"git_bytes", "git_sha256", "checkout", "checkout_encoding"},
            "storage_schema6_source_fields")
    require(type(item["git_bytes"]) is int
            and item["git_bytes"] == BUDGET_SOURCE_GIT_BYTES
            and item["git_sha256"] == BUDGET_SOURCE_GIT_SHA256,
            "storage_schema6_source_git_not_reviewed")
    _identity(item["checkout"])
    require(item["checkout_encoding"] in {"EXACT_GIT_BYTES", "EXACT_LF_TO_CRLF"},
            "storage_schema6_source_encoding")
    if item["checkout_encoding"] == "EXACT_GIT_BYTES":
        require(item["checkout"] == {"bytes": BUDGET_SOURCE_GIT_BYTES,
                                     "sha256": BUDGET_SOURCE_GIT_SHA256},
                "storage_schema6_source_original_changed")
    return copy.deepcopy(item)


def validate_schema_six_source(cohort, observation, original):
    """Bind retained/physical module bytes to the same exact product SHA/tree."""
    item = validate_schema_six_observation(cohort, observation)
    require(type(original) is bytes and 0 < len(original) <= MAX_SOURCE_BYTES,
            "storage_schema6_source_original_changed")
    require(item["checkout"] == {"bytes": len(original),
                                  "sha256": hashlib.sha256(original).hexdigest()},
            "storage_schema6_source_original_changed")
    git_bytes = (original if item["checkout_encoding"] == "EXACT_GIT_BYTES"
                 else original.replace(b"\r\n", b"\n"))
    require((item["checkout_encoding"] != "EXACT_LF_TO_CRLF"
             or (b"\r" not in git_bytes and original == git_bytes.replace(b"\n", b"\r\n")))
            and len(git_bytes) == BUDGET_SOURCE_GIT_BYTES
            and hashlib.sha256(git_bytes).hexdigest() == BUDGET_SOURCE_GIT_SHA256,
            "storage_schema6_source_original_changed")
    return item


def _schema6_sql(value):
    """Normalize SQL tokens while preserving quoted literal contents."""
    require(isinstance(value, str) and 0 < len(value) <= 16384,
            "storage_schema6_ddl_missing")
    tokens = re.findall(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|[A-Za-z_][A-Za-z_0-9]*"
                        r"|[0-9]+|<>|!=|<=|>=|[^\s]", value)
    while tokens and tokens[-1] == ";":
        tokens.pop()
    return tuple(token if token.startswith(("'", '"')) else token.lower() for token in tokens)


def schema_six_writer_sql(table, event):
    require(table in WRITER_TABLES and event in WRITER_EVENTS,
            "storage_schema6_writer_identifier")
    name = "mw_writer_v6_" + table + "_" + event.lower()
    return name, WRITER_TEMPLATE.format(name, event=event, table=table,
                                       WRITER_FENCE_FUNCTION="motionwright_writer_epoch_guard_v6")


def _validate_schema_six_objects(connection, version):
    budget = connection.execute(
        "SELECT type,name,tbl_name,sql FROM main.sqlite_schema "
        "WHERE lower(name) GLOB 'production_operation_*' "
        "OR lower(tbl_name) GLOB 'production_operation_*' LIMIT 13").fetchall()
    writers = connection.execute(
        "SELECT type,name,tbl_name,sql FROM main.sqlite_schema "
        "WHERE lower(name) GLOB 'mw_writer_v6_*' LIMIT 28").fetchall()
    if version < 6:
        require(not budget and not writers, "historical_storage_has_reserved_budget_objects")
        return
    expected_budget = {(kind, name, table) for name, (kind, table, sql) in BUDGET_OBJECTS.items()}
    require(len(budget) == len(expected_budget)
            and {tuple(row[:3]) for row in budget} == expected_budget,
            "storage_schema6_budget_objects_differ")
    for kind, name, table, sql in budget:
        expected = BUDGET_OBJECTS[name][2]
        require(sql is None if expected is None else _schema6_sql(sql) == _schema6_sql(expected),
                "storage_schema6_budget_ddl_differs")
    for table, metadata in BUDGET_METADATA.items():
        require(connection.execute(f"PRAGMA main.table_xinfo('{table}')").fetchall()
                == metadata["columns"], "storage_schema6_budget_columns_differ")
        require(connection.execute(f"PRAGMA main.foreign_key_list('{table}')").fetchall()
                == metadata["foreign_keys"], "storage_schema6_budget_foreign_keys_differ")
        require(connection.execute(f"PRAGMA main.index_list('{table}')").fetchall()
                == metadata["indexes"], "storage_schema6_budget_indexes_differ")
        for index, expected in metadata["index_columns"].items():
            require(connection.execute(f"PRAGMA main.index_xinfo('{index}')").fetchall()
                    == expected, "storage_schema6_budget_index_columns_differ")
    expected_writers = {}
    for table in WRITER_TABLES:
        for event in WRITER_EVENTS:
            name, sql = schema_six_writer_sql(table, event)
            expected_writers[name] = (table, sql)
    require(len(writers) == 27
            and {tuple(row[:3]) for row in writers}
            == {("trigger", name, value[0]) for name, value in expected_writers.items()},
            "storage_schema6_writer_objects_differ")
    for kind, name, table, sql in writers:
        require(_schema6_sql(sql) == _schema6_sql(expected_writers[name][1]),
                "storage_schema6_writer_ddl_differs")


def validate_schema_layout(connection, cohort):
    """Inspect the admitted layout through the caller's read-only connection.

    No database is opened, migrated or repaired here. Source admission selects
    the expected version; PRAGMA user_version only checks that selection.
    """
    version = validate_cohort(cohort)["storage_schema"]
    require(connection.execute("PRAGMA main.user_version").fetchone() == (version,),
            "storage_schema_version_mismatch")
    validate_receipt_columns(
        connection.execute("PRAGMA main.table_xinfo('receipts')").fetchall(), cohort)
    _validate_schema_six_objects(connection, version)
    tables = tuple(CAS_DDL)
    indexes = tuple(value[0] for value in CAS_INDEXES.values())
    objects = connection.execute(
        "SELECT type,name,tbl_name,sql FROM main.sqlite_schema "
        "WHERE name COLLATE NOCASE IN (?,?,?,?) "
        "OR tbl_name COLLATE NOCASE IN (?,?) LIMIT ?",
        (*tables, *indexes, *tables, 11 if version == 6 else 5),
    ).fetchall()
    if version < 5:
        require(not objects, "historical_storage_has_reserved_cas_objects")
        return
    expected = {("table", name, name) for name in tables}
    expected.update(("index", CAS_INDEXES[name][0], name) for name in tables)
    if version == 6:
        expected.update(("trigger", schema_six_writer_sql(table, event)[0], table)
                        for table in tables for event in WRITER_EVENTS)
    require(len(objects) == len(expected) and {tuple(row[:3]) for row in objects} == expected,
            "storage_cas_objects_differ")
    declarations = {row[1]: row[3] for row in objects}
    for name in tables:
        require(_cas_ddl(declarations[name]) == _cas_ddl(CAS_DDL[name]),
                "storage_cas_ddl_differs")
        columns = connection.execute(f"PRAGMA main.table_xinfo('{name}')").fetchall()
        require(tuple(columns) == CAS_COLUMNS[name], "storage_cas_columns_differ")
        index, origin, column_id, column = CAS_INDEXES[name]
        require(declarations[index] is None, "storage_cas_index_ddl_differs")
        require(connection.execute(f"PRAGMA main.index_list('{name}')").fetchall()
                == [(0, index, 1, origin, 0)], "storage_cas_indexes_differ")
        require(connection.execute(f"PRAGMA main.index_xinfo('{index}')").fetchall()
                == [(0, column_id, column, 0, "BINARY", 1),
                    (1, -1, None, 0, "BINARY", 0)], "storage_cas_index_columns_differ")
        require(not connection.execute(f"PRAGMA main.foreign_key_list('{name}')").fetchall(),
                "storage_cas_foreign_keys_present")
    identity = connection.execute(
        "SELECT singleton,store_id FROM main.cas_store_identity LIMIT 2").fetchall()
    require(len(identity) == 1 and type(identity[0][0]) is int and identity[0][0] == 1
            and isinstance(identity[0][1], str), "storage_cas_identity_not_singular")
    try:
        UUID(identity[0][1])
    except (ValueError, AttributeError) as error:
        raise StorageContractError("storage_cas_identity_malformed") from error


def validate_document(document, cohort):
    version = validate_cohort(cohort)["storage_schema"]
    allowed = (1, 2) if version == 2 else (2,)
    require(isinstance(document, dict) and type(document.get("schema_version")) is int
            and document["schema_version"] in allowed,
            "project_document_schema_differs_from_storage_cohort")
