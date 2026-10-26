#!/usr/bin/env python3
"""Select SDK Foundation source contracts using the existing command observers.

Known source profiles do not grant execution authority. The authority catalogue
starts empty. Local Windows observations retain their direct-process limitation;
the two Linux native methods are a separate, explicitly admitted selection.
Hosted Windows requires reviewed authority, bound public transport metadata,
an exact technical bootstrap and the original work/custody budget. The catalogue
starts empty; capacity or chosen operating ceilings do not authorize execution.
"""
from __future__ import annotations

import argparse
import ast
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path, PureWindowsPath
import platform
import re
import shutil
import stat
import sys
import time
from types import MappingProxyType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tooling import ci_foundation_causal as observer
from tooling import ci_storage_apply_recovery as custody
from tooling import ci_sdk_native_source as producer
from tooling import ci_test_runner as fast_runner

require = custody.require
WORKFLOW = ".github/workflows/security-delivery.yml"
HELPER = "tooling/ci_sdk_foundation_candidate.py"
TEST = "tooling/tests/test_ci_sdk_foundation_candidate.py"
REVIEW_BRANCH = "verify/sdk-foundation-candidate-20261026-a6"
RUST = "1.98.1"
HOSTED_MODE = "windows-hosted-g1"
HOSTED_SCOPE = "sdk-foundation-windows-g1"
HOSTED_RUNNER = "windows-2025"
HOSTED_IMAGE_OS = "win25-vs2026"
# Windows os.environ snapshots canonicalize environment names to uppercase.
HOSTED_RUST = RUST + "-x86_64-pc-windows-gnu"
MODES = ("windows-g1", "linux-g2", HOSTED_MODE)
SCOPES = ("sdk-foundation-source-controls", "sdk-foundation-linux-two", HOSTED_SCOPE)
SCHEMA = "motionwright.sdk-foundation-candidate/1"
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9:]*(?:[A-Za-z_0-9])?\Z")
GATE_PINS = {
    "tooling/ci_foundation_causal.py": "1d31c58aa7d63c927ceb2518282a433e0f728fc125da8978fb635b18cff354c2",
    "tooling/ci_storage_apply_recovery.py": "9aea9a41d365a36fd1abae86ec5a0974236d62d9781a372eace0578ef2da1520",
    "tooling/ci_sdk_native_source.py": "65bcda73f16423ebb644a0979bca3a7927f8e7337c124702874bce51adcf8127",
    "tooling/ci_test_runner.py": "b7d39ad4c94be70abcac75880f92d00cf3c8d49eb0198b404a962ce5ab1a712f",
}
GATE_POLICIES = ("source_policy", "public_surface_policy", "acceptance_policy", "creative_status_policy")
LOCAL_GATE_POLICY_SECONDS = 480
GATE_PATTERNS = ("test_ci_sdk_foundation_candidate.py", "test_ci_foundation_causal.py", "test_ci_storage_apply_recovery.py")
GATE_FILES = tuple(dict.fromkeys((WORKFLOW, HELPER, TEST, *GATE_PINS,
    *("tooling/" + p + ".py" for p in GATE_POLICIES), "tooling/source_lock_admission.py",
    *("tooling/tests/" + p for p in GATE_PATTERNS), "Cargo.lock", "SOURCE_LOCK.json", "apps/studio/package-lock.json")))
# A reviewed entry pins a real coordinator lease or a selected Actions context.
# A dispatch input or a self-authored lease is never sufficient authority.
REVIEWED_AUTHORITIES = MappingProxyType({})

FIXED = {'sha': 'dac1f5886b97efb4cd7a9351e817f4b7321b79c9',
 'tree': '564852515f37f2ba7fad537a26c8085db338e3e3',
 'listing': '7ec70ebd978bfdaa3a98772aa35140fba04e3fc0451ef9b34271c0944cc7ee15',
 'pins': {'Cargo.lock': '69a2746b64e96d4fd39f3aaa900692b7ac55b54032303785f816f92cf2802a7e',
          'crates/backend-api/src/provider.rs': '7b865194957a046c915744dc2c326da5ca876a936614d0cf03755f69deb1aea1',
          'crates/core/src/lib.rs': '3d0d20d5132f10bc4d229c244beaed1c5f45c9180bc0dbdffb67fb8cd57f1457',
          'crates/core/src/providers.rs': '119bbe26f96d16fca852e17a708777bb13436de3b133843217a47b92efed2756',
          'crates/core/src/provider_mounts.rs': 'ec05973c7a1915f6e7a59cdd68bdeab8eda7463fceea0633693cc826caa862e6',
          'crates/driver-host/src/lib.rs': '837b0900a01bbf98a72f053262c085a68b399e9afac6910614fe0654aa626232',
          'crates/core/src/operation_permits.rs': 'ed14aa94d3715bfec54f6db7c5be38b85cde790c11247af694bb92b36bdf0452',
          'crates/driver-host/src/operation_permits.rs': '165b914574f247b887e50e267914683aa53ca631694b37ca806d855090138ac2',
          'crates/driver-sdk/src/operation_permit.rs': '0706348eebdc2165a052651075093f764677e1bc69da2d633fc5197bdbfa8008',
          'crates/driver-sdk/src/lib.rs': 'bbc00f4dc449378c526a0e1859fa3f521e8238c800109120a133c63886b96293',
          'crates/native-sdk/src/cooperation.rs': 'e1549547a1a6194b662f9f3b7fe9ea0229a22131d1ec9a7d8171d37543156f21',
          'crates/core/src/provider_mounts_tests.rs': 'bfff5d0b248f7b777542f8e8a59b5555c46cec721e9cd807ad50167cb9df21ca',
          'crates/native-sdk/src/effects_readback.rs': '77411b8f627ac456964b1ae2789cb829925cfcc2ada2b0c97bb31e6721ccd070',
          'crates/daemon/src/host_stdin_custody.rs': '6de074258791796c44244ef2fea7cb7149afb6cc1d0435c1bc885fa46ca94037',
          'crates/driver-host/src/bin/fixture.rs': '9411cf69142b555fe70d01a492bd822cc0a1f2b830e5df1f33d9550e35b1ac0b',
          'crates/daemon/src/host_stdin_custody_bridge_tests.rs': '06d3559bb5c25049b84863cc2d5a22424394d35a9353f50433b7e7ec39b4b8c5',
          'crates/native-sdk/Cargo.toml': '9614711e1d3cb30dc207dde8c27e4269e7195e83ceefefa2a3aa68deeea0f6b5',
          'examples/native/owner_operation_fixture.rs': '498fccc2332d23e2064f91bc5e6f69ae0b7e51ad58f278173db7fdf70f489896',
          'crates/core/src/owner_operation_process_tests.rs': 'ba64b1bacdbf2fea74f5c0af68245e045125007a9ff87bac82152680113c0d85',
          'crates/platform-windows-sys/src/launch.rs': '1e826d859a0ecd8313f2217f8e6516f774399dae9792c719e00f5076f90142f4',
          'crates/platform-windows-sys/src/launch/workspace_mount_binding.rs': '009fa3ad9c8d176791ddaad596fc911b1c71c8d267625ac7e86e54f6d08d8332',
          'docs/commands.md': '23b03db7d5e68d7340247b00b710d644e533b83a5bb9ac55d43f853c39aecf12'}}

CAUSALS = ({'id': 'pending-original-red',
  'sha': '80725d6a7a015211c3948b19e02c0557e46c1eec',
  'tree': 'a2d28eb0c5cb90941bcfa455c0a33da6a418c4d0',
  'listing': '6994cf051606e69b538b03902d6130248c7152a5275acbcba890257adb8975b9',
  'pins': {'Cargo.lock': '69a2746b64e96d4fd39f3aaa900692b7ac55b54032303785f816f92cf2802a7e',
           'crates/driver-host/src/lib.rs': '99681a40c1562dc9f4918a0bb6f0760434fc404b9cc4eef3ae708f90b3f78102',
           'docs/commands.md': '23b03db7d5e68d7340247b00b710d644e533b83a5bb9ac55d43f853c39aecf12'},
  'target': 'driver-host',
  'test': 'pending_request_custody_regression_tests::duplicate_pending_request_keeps_original_receiver',
  'producer': ['test', '--locked', '-p', 'semwright-driver-host', '--lib', '--no-run', '--message-format=json'],
  'failure': {'source': 'crates/driver-host/src/lib.rs',
              'line': 5286,
              'markers': ['duplicate admission closed the original response channel']}},
 {'id': 'effects-target-original-red',
  'sha': '799895aba94dc396b41b327ba06e40c1e3e65dbb',
  'tree': '1fc6caf0ac7a98f80dadfa86f79ad28ea8a36861',
  'listing': 'b939de993799a887fe1083b960398af8c657dbde7440897099c2bd24c9178264',
  'pins': {'Cargo.lock': '69a2746b64e96d4fd39f3aaa900692b7ac55b54032303785f816f92cf2802a7e',
           'crates/native-sdk/src/effects_readback.rs': '602fa9a0207e308d9014db5b21507bc9855f18e83c0f1d3c7cc2a72864e712f6',
           'docs/commands.md': '23b03db7d5e68d7340247b00b710d644e533b83a5bb9ac55d43f853c39aecf12'},
  'target': 'native-sdk',
  'test': 'effects_readback::target_linux_preparation_tests::target_linux_preparation_is_host_independent_and_preserves_canonical_root',
  'producer': ['test',
               '--locked',
               '-p',
               'semwright-native-sdk',
               '--features',
               'driver,effects',
               '--lib',
               '--no-run',
               '--message-format=json'],
  'failure': {'source': 'crates/native-sdk/src/effects_readback.rs',
              'line': 1149,
              'markers': ['pure Linux-target preparation']}},
 {'id': 'receiver-close-latch-red',
  'sha': '5a222bb9d5e33d63cf2b095d3cc32b01de6d2fea',
  'tree': 'a7982f5f7b5a42e9b04db59cb6dea1c6108e7536',
  'listing': '8a791f8ad271d8c7d40277418225c14eff7ba990ed45b8a149069f1ecafa2951',
  'pins': {'Cargo.lock': '69a2746b64e96d4fd39f3aaa900692b7ac55b54032303785f816f92cf2802a7e',
           'crates/driver-sdk/src/operation_permit.rs': '4500162fe2cdcaa49d6ef9e2158883b3a20ca463d40698052a5d485440c93987',
           'docs/commands.md': '23b03db7d5e68d7340247b00b710d644e533b83a5bb9ac55d43f853c39aecf12'},
  'target': 'driver-sdk',
  'test': 'operation_permit::tests::global_close_latch_denies_entry_and_check_before_registry_drain',
  'producer': ['test', '--locked', '-p', 'semwright-driver-sdk', '--lib', '--no-run', '--message-format=json'],
  'failure': {'source': 'crates/driver-sdk/src/operation_permit.rs',
              'line': 717,
              'markers': ['global closure allowed entry/check before registry drain']}},
 {'id': 'refresh-remove-red',
  'sha': 'c0878ca18d1f08df113224a6e9e386b6c453e1e4',
  'tree': '67918fc5f681532ac4ef45663235308bceebfec1',
  'listing': 'd85c5bd08bd6e7b2cf22523f12e88dda26002453d7863165f7e5f6db99928896',
  'pins': {'Cargo.lock': '69a2746b64e96d4fd39f3aaa900692b7ac55b54032303785f816f92cf2802a7e',
           'crates/core/src/provider_mounts_tests.rs': 'bfff5d0b248f7b777542f8e8a59b5555c46cec721e9cd807ad50167cb9df21ca',
           'docs/commands.md': '23b03db7d5e68d7340247b00b710d644e533b83a5bb9ac55d43f853c39aecf12'},
  'target': 'core',
  'test': 'provider_mounts::tests::owner_operation_issuer::cancelled_remove_tombstone_is_not_replaced_by_refresh',
  'producer': ['test', '--locked', '-p', 'semwright-core', '--lib', '--no-run', '--message-format=json'],
  'failure': {'source': 'crates/core/src/provider_mounts_tests.rs',
              'line': 1300,
              'markers': ['refresh published or failed outside the intended conflict']}})


FIXED["pins"]["crates/platform-linux-sys/src/sandbox_main.rs"] = "065f2dd6632f205ec6315b6adda5987df09d2e00b4e11137e72768ee369b8d18"

# These public commits preserve the reviewed causal trees without publishing the
# local histories. Only hosted Windows selects them; a matching tree alone does
# not admit another commit or substitute a local lease's source identities.
HOSTED_CAUSAL_SHAS = MappingProxyType({
    "pending-original-red": "5f6f79ae1e31e035f294b46ef958883c1fd75c92",
    "effects-target-original-red": "859f0f9b9e1f461a977efcbd32c70a5a6a056968",
    "receiver-close-latch-red": "0ae99dfe35f91aef3cfe301fbafb6085c474c357",
    "refresh-remove-red": "cad4c0e2ef921edc398cf9cf3ac60cc8d53e7a5e",
})


def causal_profiles(mode):
    require(type(mode) is str and mode in MODES, "unknown causal source selection mode")
    if mode == "windows-g1":
        return CAUSALS
    if mode == "linux-g2":
        return ()
    require(set(HOSTED_CAUSAL_SHAS) == {p["id"] for p in CAUSALS}
            and all(type(value) is str and SHA.fullmatch(value) for value in HOSTED_CAUSAL_SHAS.values())
            and len(set(HOSTED_CAUSAL_SHAS.values())) == 4
            and not set(HOSTED_CAUSAL_SHAS.values()) & {FIXED["sha"], *(p["sha"] for p in CAUSALS)},
            "hosted causal source catalogue differs")
    return tuple({**profile, "sha": HOSTED_CAUSAL_SHAS[profile["id"]]} for profile in CAUSALS)


def target(crate, kind="lib", name=None, source=None):
    return {"package": "semwright-" + crate, "crate": crate, "kind": kind,
            "name": name or "semwright_" + crate.replace("-", "_"),
            "source": source or "crates/" + crate + "/src/lib.rs"}


TARGETS = {
    "core": target("core"), "driver-sdk": target("driver-sdk"),
    "driver-host": target("driver-host"), "native-sdk": target("native-sdk"),
    "provider-runtime": target("core", "test", "provider_runtime", "crates/core/tests/provider_runtime.rs"),
    "mount-references": target("types", "test", "provider_mount_references", "crates/types/tests/provider_mount_references.rs"),
    "cooperation": target("native-sdk", "test", "cooperation", "crates/native-sdk/tests/cooperation.rs"),
    "daemon": target("daemon", "bin", "semwrightd", "crates/daemon/src/semwrightd.rs"),
    "fixture": target("native-sdk", "example", "owner-operation-permit-fixture", "examples/native/owner_operation_fixture.rs"),
    "sandbox": target("plugin-host", "bin", "semwright-sandbox", "crates/plugin-host/src/semwright-sandbox.rs"),
}
# collection, binary role, original libtest substring filter, closed namespace, count
GROUPS = (
    ("core", "core", "operation_permits::tests", "operation_permits::tests::", 3),
    ("core", "core", "provider_mounts::tests", "provider_mounts::tests::", 17),
    ("core", "provider-runtime", None, None, 21),
    ("types", "mount-references", None, None, 6),
    ("driver-sdk", "driver-sdk", None, None, 41),
    ("driver-host", "driver-host", "operation_permits::tests", "operation_permits::tests::", 6),
    ("driver-host", "driver-host", "owner_operation_pending_tests", "owner_operation_pending_tests::", 1),
    ("driver-host", "driver-host", "pending_request_custody_regression_tests", "pending_request_custody_regression_tests::", 1),
    ("driver-host", "driver-host", "host_tool_protocol_tests", "host_tool_protocol_tests::", 2),
    ("native-sdk", "cooperation", None, None, 23),
    ("native-sdk", "native-sdk", "target_linux_preparation_tests", "effects_readback::target_linux_preparation_tests::", 3),
    ("daemon-private", "daemon", None, None, 8),
    ("daemon-no-default", "daemon", None, None, 2),
)
G2_CASES = (
    "owner_operation_process_tests::linux_owner_permit_native_success_releases_while_process_stays_live",
    "owner_operation_process_tests::linux_owner_permit_session_close_keeps_guard_until_real_release",
)
TEST_OPTIONS = ("--nocapture", "--test-threads=1")
G0_REMAINING = ("sdk-python-contracts", "sdk-javascript-contracts", "linux-openat2",
                "ruff", "shellcheck", "actionlint", "complete-workspace-rust")


def validate_catalogue():
    require(len(GROUPS) == 13 and sum(row[4] for row in GROUPS) == 134,
            "closed group catalogue differs")
    require(len(CAUSALS) == 4 and len({r["sha"] for r in CAUSALS}) == 4,
            "causal source catalogue differs")
    hosted_causals = causal_profiles(HOSTED_MODE)
    for profile in (FIXED, *CAUSALS, *hosted_causals):
        require(SHA.fullmatch(profile["sha"]) and SHA.fullmatch(profile["tree"])
                and profile["pins"] and all(DIGEST.fullmatch(v) for v in profile["pins"].values()),
                "source profile is incomplete")
    for row in CAUSALS:
        require(type(row["failure"]["line"]) is int and row["failure"]["line"] > 0
                and row["failure"]["source"] in row["pins"] and NAME.fullmatch(row["test"]),
                "causal site or named method is unbound")
    return {"source_profile": FIXED["sha"], "authority_entries": len(REVIEWED_AUTHORITIES),
            "windows_groups": 13, "windows_positive_observations": 134,
            "causal_sources": 4, "linux_native_cases": 2, "product_execution": "NOT_RUN",
            "hosted_windows": {"mode": HOSTED_MODE, "transport": "COMPOSED_REVIEW_AND_AUTHORITY_REQUIRED",
                               "causal_source_shas": [p["sha"] for p in hosted_causals],
                               "execution": "NOT_RUN", "automatic_dispatch": False}}


def windows_selection(mode):
    return mode in ("windows-g1", HOSTED_MODE)


def hosted_binding(value, purpose="foundation-g1"):
    """A reviewed registry row, never authority created from dispatch inputs."""
    keys = {"repository", "workflow_ref", "gate_base_sha", "runner_label", "runner_image_os",
            "private_transport", "cold_growth"}
    require(type(value) is dict and set(value) == keys, "hosted authority binding shape differs")
    require(type(value["repository"]) is str and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["repository"])
            and type(value["workflow_ref"]) is str and value["workflow_ref"].startswith(
                value["repository"] + "/" + WORKFLOW + "@refs/heads/")
            and len(value["workflow_ref"].split("@refs/heads/", 1)[1]) > 0,
            "hosted repository or workflow ref differs")
    require(type(value["gate_base_sha"]) is str and SHA.fullmatch(value["gate_base_sha"])
            and value["runner_label"] == HOSTED_RUNNER and value["runner_image_os"] == HOSTED_IMAGE_OS,
            "reviewed gate base or hosted image differs")
    require(purpose in (HOSTED_FULL_PURPOSE, HOSTED_QUALIFICATION_PURPOSE), "unknown hosted authority purpose")
    selected_sources = sorted(profile["sha"] for _, profile in hosted_profiles(purpose))
    transport = value["private_transport"]
    require(type(transport) is dict and set(transport) == {
                "review_sha256", "source_shas", "incoming", "outgoing", "private_sources_published"}
            and type(transport["review_sha256"]) is str and DIGEST.fullmatch(transport["review_sha256"])
            and transport["source_shas"] == selected_sources
            and transport["incoming"] == ("OWNER_COMPOSED_FIXED_CHECKOUT" if purpose == HOSTED_QUALIFICATION_PURPOSE else "OWNER_COMPOSED_FIVE_CHECKOUTS")
            and transport["outgoing"] == "OWNER_COMPOSED_PRIVATE_ORIGINALS_CUSTODY"
            and transport["private_sources_published"] is False,
            "private incoming and outgoing source custody require an exact owner review")
    growth = value["cold_growth"]
    parts = {"source_transport", "source_checkouts", "toolchain", "cargo_downloads", "targets",
             "sealed_binary_originals", "temporary_and_logs", "safety"}
    require(type(growth) is dict and set(growth) == {"basis_sha256", "source_shas", "compiled_configurations", "bytes"}
            and type(growth["basis_sha256"]) is str and DIGEST.fullmatch(growth["basis_sha256"])
            and growth["source_shas"] == transport["source_shas"]
            and type(growth["compiled_configurations"]) is int
            and growth["compiled_configurations"] == (1 if purpose == HOSTED_QUALIFICATION_PURPOSE else 13)
            and type(growth["bytes"]) is dict and set(growth["bytes"]) == parts
            and all(type(n) is int and 0 < n <= 256 * 1024**3 for n in growth["bytes"].values())
            and sum(growth["bytes"].values()) <= 512 * 1024**3,
            "cold growth budget is absent, noninteger or belongs to another source/configuration")
    if purpose == HOSTED_QUALIFICATION_PURPOSE:
        require(growth["bytes"] == dict(HOSTED_QUALIFICATION_CEILINGS),
                "qualification requires the exact chosen operating ceilings, not an invented measured-growth estimate")
    return value


def catalogue_syntax(raw):
    """Normalize only the one literal registry assignment; executable code stays exact."""
    tree = ast.parse(raw)
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                   and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                   and node.targets[0].id == "REVIEWED_AUTHORITIES"]
    require(len(assignments) == 1, "gate authority assignment missing or repeated")
    node = assignments[0]
    require(isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "MappingProxyType" and len(node.value.args) == 1
            and not node.value.keywords and isinstance(node.value.args[0], ast.Dict),
            "gate authority is not one literal registry")
    keys = node.value.args[0].keys
    require(all(isinstance(key, ast.Constant) and type(key.value) is str for key in keys)
            and len({key.value for key in keys}) == len(keys),
            "gate authority keys are dynamic or duplicated")
    value = ast.literal_eval(node.value.args[0])
    require(type(value) is dict, "gate authority literal is not a dictionary")
    node.value = ast.Constant(value="REVIEWED_AUTHORITY_LITERAL_REMOVED_FOR_COMPARISON")
    return value, ast.dump(tree, include_attributes=False)


def verify_hosted_gate(gate, context, selected, budget):
    """G is reviewed code; actual G-prime may add only its one reviewed R row."""
    require(context["mode"] == HOSTED_MODE, "hosted gate verification used for a different mode")
    binding = hosted_binding(selected["binding"], hosted_purpose(context))
    actual = context["github"]["GITHUB_SHA"]
    base = binding["gate_base_sha"]
    def query(*argv):
        # Reuse the existing45s Git query. Do not start one without its full remainder.
        require(budget.remaining(45) >= 45, "gate query has insufficient original budget")
        raw = custody.git(gate, *argv)
        budget.remaining(1)
        return raw
    lineage = query("rev-list", "--parents", "-n", "1", "HEAD").decode().strip().split()
    require(lineage == [actual, base], "actual gate must be one ordinary child of the reviewed base")
    before = query("ls-tree", "-r", "-z", base)
    after = query("ls-tree", "-r", "-z", actual)
    def split_listing(raw):
        kept, helper_entry = [], []
        for row in raw.split(b"\0"):
            if row:
                header, path = row.split(b"\t", 1)
                (helper_entry if path == HELPER.encode() else kept).append(row)
        require(len(helper_entry) == 1 and helper_entry[0].split(b" ", 2)[:2] == [b"100644", b"blob"],
                "gate helper mode or identity changed")
        return kept
    require(split_listing(before) == split_listing(after), "gate child changed a non-catalogue file")
    old = query("show", base + ":" + HELPER)
    new = query("show", actual + ":" + HELPER)
    old_catalogue, old_code = catalogue_syntax(old)
    new_catalogue, new_code = catalogue_syntax(new)
    require(old_catalogue == {} and new_catalogue == {context["authority_id"]: selected}
            and old_code == new_code, "gate child changed code or contains unrelated authority")
    require((gate / HELPER).read_bytes() == new, "executed gate helper differs from its Git blob")
    return {"status": "EXACT_GIT_CODE_PLUS_REVIEWED_AUTHORITY_CHILD",
            "reviewed_gate_base_sha": base, "executed_gate_sha": actual,
            "actual_workflow_sha": context["github"]["GITHUB_WORKFLOW_SHA"],
            "base_listing_sha256": hashlib.sha256(before).hexdigest(),
            "executed_listing_sha256": hashlib.sha256(after).hexdigest(),
            "source_result_transferred": False}


def require_hosted_transport_composition(context, bootstrap=None, environment=None):
    require(context["mode"] == HOSTED_MODE, "private transport seam used for another mode")
    require(type(bootstrap) is tuple and len(bootstrap) == 2 and isinstance(bootstrap[0], Path)
            and type(bootstrap[1]) is str and DIGEST.fullmatch(bootstrap[1]),
            "BLOCKED_PRIVATE_TRANSPORT_UNCOMPOSED_FOR_THIS_INVOCATION: exact technical bootstrap absent; no boolean bypass")
    environment = dict(os.environ) if environment is None else dict(environment)
    return hosted_load_bootstrap(bootstrap[0], bootstrap[1], context, environment)


def hosted_growth_observation(resources, context, initial_disk=None):
    """A phase-bound volume observation, not a continuous allocation watchdog."""
    require(context["mode"] == HOSTED_MODE, "cold growth used for another mode")
    growth = context["cold_growth"]
    reserve = sum(growth["bytes"].values())
    floor = 18 * 1024**3
    disk = resources["disk"]
    require(type(disk) is int and disk >= floor, "hosted protected disk floor breached")
    if initial_disk is None:
        require(disk >= floor + reserve, "hosted cold growth reserve is unavailable")
        initial_disk = disk
    require(type(initial_disk) is int and initial_disk >= floor + reserve,
            "original hosted cold growth observation is incomplete")
    observed_delta = max(0, initial_disk - disk)
    require(observed_delta <= reserve, "observed hosted volume growth exceeds reviewed reserve")
    return {"basis_sha256": growth["basis_sha256"], "planned_bytes": growth["bytes"],
            "reserved_total_bytes": reserve, "initial_free_bytes": initial_disk,
            "current_free_bytes": disk, "observed_volume_delta_bytes": observed_delta,
            "exclusive_allocation_attribution": False, "continuous_resource_watchdog": False}


def authority(admission_id, mode, source_sha):
    require(mode in MODES and source_sha == FIXED["sha"], "unknown source profile or mode")
    require(admission_id in REVIEWED_AUTHORITIES,
            "execution authority is not reviewed; source controls only")
    row = REVIEWED_AUTHORITIES[admission_id]
    keys = {"kind", "mode", "source_sha", "review_sha256", "max_total_seconds", "binding"}
    require(type(row) is dict and (set(row) == keys or mode == HOSTED_MODE and set(row) == keys | {"purpose"}),
        "reviewed authority shape differs")
    require(row["mode"] == mode and row["source_sha"] == source_sha
            and type(row["review_sha256"]) is str and DIGEST.fullmatch(row["review_sha256"])
            and type(row["max_total_seconds"]) is int and 60 <= row["max_total_seconds"] <= (7200 if windows_selection(mode) else 3600),
            "reviewed authority source or finite budget differs")
    require(row["kind"] == ("local-lease" if mode == "windows-g1" else "github-dispatch")
            and type(row["binding"]) is dict, "authority mode differs")
    if mode == HOSTED_MODE:
        purpose = hosted_purpose(row)
        hosted_binding(row["binding"], purpose)
        if purpose == HOSTED_QUALIFICATION_PURPOSE:
            require(row["max_total_seconds"] == 7200, "qualification original H must remain exactly7200s")
    return row


def request_context(environment, mode, source_sha, admission_id, runtime_platform=None):
    selected = authority(admission_id, mode, source_sha)
    actual = sys.platform if runtime_platform is None else runtime_platform
    github = {name: environment.get(name, "") for name in
              ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_REPOSITORY",
               "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "GITHUB_EVENT_NAME")}
    if mode == "windows-g1":
        require(actual == "win32" and environment.get("GITHUB_ACTIONS") != "true"
                and not any(github.values()), "local Windows context must not impersonate Actions")
        require(set(selected["binding"]) == {"lease_sha256"}
                and DIGEST.fullmatch(selected["binding"]["lease_sha256"]), "local lease digest is absent")
    elif mode == HOSTED_MODE:
        binding = hosted_binding(selected["binding"], hosted_purpose(selected))
        require(actual == "win32" and environment.get("RUNNER_OS") == "Windows"
                and environment.get("RUNNER_ARCH") == "X64"
                and environment.get("RUNNER_ENVIRONMENT") == "github-hosted"
                and environment.get("IMAGEOS") == binding["runner_image_os"],
                "genuine hosted Windows x64 image required")
        require(environment.get("GITHUB_ACTIONS") == "true"
                and github["GITHUB_EVENT_NAME"] == "workflow_dispatch"
                and environment.get("SDK_FOUNDATION_SCOPE") == HOSTED_SCOPE
                and github["GITHUB_JOB"] == HOSTED_SCOPE and github["GITHUB_RUN_ATTEMPT"] == "1",
                "hosted Windows requires its original manual job and attempt")
        require(SHA.fullmatch(github["GITHUB_SHA"])
                and github["GITHUB_WORKFLOW_SHA"] == github["GITHUB_SHA"]
                and re.fullmatch(r"[1-9][0-9]*", github["GITHUB_RUN_ID"])
                and github["GITHUB_REPOSITORY"] == binding["repository"]
                and github["GITHUB_WORKFLOW_REF"] == binding["workflow_ref"],
                "hosted run, gate SHA or reviewed workflow identity differs")
        require(type(environment.get("IMAGEVERSION")) is str
                and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", environment["IMAGEVERSION"]),
                "hosted image version is unobserved")
    else:
        require(actual == "linux" and environment.get("RUNNER_OS") == "Linux"
                and environment.get("RUNNER_ARCH") == "X64", "Linux x64 runner required")
        require(environment.get("GITHUB_ACTIONS") == "true"
                and github["GITHUB_EVENT_NAME"] == "workflow_dispatch"
                and environment.get("SDK_FOUNDATION_SCOPE") == SCOPES[1]
                and github["GITHUB_JOB"] == "sdk-foundation-linux-two"
                and github["GITHUB_RUN_ATTEMPT"] == "1", "selected original manual attempt required")
        require(SHA.fullmatch(github["GITHUB_SHA"])
                and github["GITHUB_WORKFLOW_SHA"] == github["GITHUB_SHA"]
                and re.fullmatch(r"[1-9][0-9]*", github["GITHUB_RUN_ID"]), "runner identity incomplete")
        require(set(selected["binding"]) == {"repository", "workflow_ref"}
                and github["GITHUB_REPOSITORY"] == selected["binding"]["repository"]
                and github["GITHUB_WORKFLOW_REF"] == selected["binding"]["workflow_ref"]
                and github["GITHUB_WORKFLOW_REF"].startswith(
                    github["GITHUB_REPOSITORY"] + "/" + WORKFLOW + "@refs/heads/"),
                "dispatch is outside its reviewed context")
    return {"mode": mode, "product_source_sha": source_sha, "authority_id": admission_id,
            "authority_review_sha256": selected["review_sha256"], "runtime_platform": actual,
            "execution_kind": ("LOCAL_WINDOWS" if mode == "windows-g1" else
                               "GITHUB_WINDOWS" if mode == HOSTED_MODE else "GITHUB_LINUX"),
            "github": github, "max_total_seconds": selected["max_total_seconds"],
            "process_boundary": "DIRECT_PROCESS_ONLY" if windows_selection(mode) else "OWNED_POSIX_SESSION_GROUP",
            "escaped_descendants": "NOT_OBSERVED", "continuous_resource_watchdog": False,
            **({"purpose": hosted_purpose(selected), "runner_label": HOSTED_RUNNER, "image_os": environment["IMAGEOS"],
                "image_version": environment["IMAGEVERSION"], "cold_growth": selected["binding"]["cold_growth"],
                "private_transport_review_sha256": selected["binding"]["private_transport"]["review_sha256"]}
               if mode == HOSTED_MODE else {})}


def validate_lease(value, expected_digest, observed_digest, run_root, now_ns, max_seconds):
    require(observed_digest == expected_digest, "lease bytes lack reviewed authority")
    keys = {"schema", "issuer", "holder", "status", "mode", "source_shas", "run_root",
            "not_before_unix_ns", "expires_unix_ns", "max_total_seconds", "event_id",
            "process_boundary", "continuous_resource_watchdog"}
    require(type(value) is dict and set(value) == keys
            and value["schema"] == "motionwright.sdk-foundation-slot/1"
            and value["issuer"] == "release-integrator" and value["holder"] == "ci-verification"
            and value["status"] == "EXCLUSIVE_GRANTED" and value["mode"] == "windows-g1"
            and value["process_boundary"] == "DIRECT_PROCESS_ONLY"
            and value["continuous_resource_watchdog"] is False,
            "exclusive coordinator lease is incomplete")
    require(value["source_shas"] == sorted([FIXED["sha"], *(p["sha"] for p in CAUSALS)])
            and value["run_root"] == str(run_root)
            and type(value["event_id"]) is str and bool(value["event_id"]), "lease scope differs")
    for name in ("not_before_unix_ns", "expires_unix_ns", "max_total_seconds"):
        require(type(value[name]) is int, "lease clocks and budget must be integers")
    require(value["not_before_unix_ns"] <= now_ns < value["expires_unix_ns"]
            and value["max_total_seconds"] == max_seconds
            and value["expires_unix_ns"] - now_ns >= max_seconds * 1_000_000_000,
            "lease is expired, future or too short for its bounded execution")
    return value


class Budget:
    """One original monotonic budget; every existing observer gets the remainder."""
    def __init__(self, seconds, clock=time.monotonic_ns):
        require(type(seconds) is int and 60 <= seconds <= 7200, "finite total budget required")
        self.clock = clock
        self.started = clock()
        self.deadline = self.started + seconds * 1_000_000_000

    def remaining(self, cap):
        require(type(cap) is int and cap > 0, "positive command timeout required")
        remaining = (self.deadline - self.clock()) / 1_000_000_000
        require(math.isfinite(remaining) and remaining > 0, "original global budget exhausted")
        return min(cap, remaining)

    def call(self, delegate, argv, cwd, env, output, label, cap, context):
        timeout = self.remaining(cap)
        row = delegate(argv, cwd, env, output, label, timeout, context)
        self.remaining(1)
        return row


def clean_environment(environment, run_root, target_root, temporary):
    blocked = []
    for key in environment:
        if (key.startswith(("SEMWRIGHT_", "MOTIONWRIGHT_TEST_", "RUST_TEST_", "GIT_"))
                or key in {"RUSTFLAGS", "RUSTC", "RUSTDOC", "RUSTFMT", "CARGO", "CARGO_ENCODED_RUSTFLAGS",
                           "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER", "RUSTC_BOOTSTRAP", "CARGO_BUILD_RUSTC",
                           "CARGO_BUILD_TARGET", "CARGO_BUILD_RUSTFLAGS", "RUSTDOCFLAGS", "PYTHONPATH", "PYTHONHOME",
                           "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "DYLD_INSERT_LIBRARIES", "NODE_OPTIONS",
                           "CC", "CXX", "AR", "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "HOST_CC", "TARGET_CC", "HOST_CXX", "TARGET_CXX", "HOST_AR", "TARGET_AR"}
                or key.startswith(("CC_", "CXX_", "AR_", "CFLAGS_", "CXXFLAGS_", "CPPFLAGS_", "LDFLAGS_"))
                or (key.startswith("CARGO_TARGET_") and key.endswith(("_RUSTFLAGS", "_LINKER", "_RUNNER")))):
            blocked.append(key)
    require(not blocked, "inherited compiler, product, formatter or loader overrides: " + ",".join(sorted(blocked)))
    require(target_root.is_relative_to(run_root) and temporary.is_relative_to(run_root), "build path escaped run root")
    env = observer.build_environment(environment, target_root, temporary)
    env.update(CARGO_HOME=str(run_root / "cargo-home"), CARGO_BUILD_JOBS="1", RUSTUP_TOOLCHAIN=RUST,
               RUSTUP_AUTO_INSTALL="0", RUSTUP_NO_UPDATE_CHECK="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", PYTHONNOUSERSITE="1",
               PIP_CACHE_DIR=str(run_root / "pip-cache"), npm_config_cache=str(run_root / "npm-cache"))
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
               GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    for key, directory in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                           ("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state"), ("XDG_RUNTIME_DIR", "runtime")):
        env[key] = str(run_root / directory)
    return env


def full_snapshot(repo, profile):
    raw = custody.git(repo, "ls-tree", "-r", "-z", profile["sha"])
    if profile.get("listing"):
        require(hashlib.sha256(raw).hexdigest() == profile["listing"], "source tree listing differs")
    names = []
    for line in raw.split(b"\0"):
        if not line:
            continue
        metadata, name = line.split(b"\t", 1)
        mode, kind, _oid = metadata.split()
        path = name.decode("utf-8")
        require(mode in (b"100644", b"100755") and kind == b"blob"
                and not Path(path).is_absolute() and ".." not in Path(path).parts,
                "SDK input is not a confined regular blob")
        names.append(path)
    require(names and len(names) == len(set(names)) and set(profile["pins"]) <= set(names), "SDK source inventory incomplete")
    snapshot = custody.source_snapshot(repo, profile["sha"], tuple(sorted(names)), profile["tree"])
    require(all(snapshot["files"][name]["sha256"] == digest for name, digest in profile["pins"].items()),
            "reviewed SDK fixture or lock differs")
    return snapshot


def source_unchanged(repo, snapshot):
    require(custody.git(repo, "rev-parse", "HEAD").decode().strip() == snapshot["sha"]
            and not custody.git(repo, "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching"),
            "SDK checkout changed or gained unbound inputs")
    for name, expected in snapshot["files"].items():
        observed = custody.fingerprint(repo / name, repo, 16 * 1024**2)
        require(observed == {k: v for k, v in expected.items() if k != "git_blob_oid"},
                "SDK input changed after snapshot: " + name)
    return "EXACT_SDK_SOURCE_UNCHANGED"


def verify_tools(tools):
    for row in tools.values():
        path = Path(row["absolute_path"])
        require(custody.fingerprint(path, path.parent) == row["fingerprint"], "observed tool changed")


def observe_tools(repo, env, logs, context, call, *, python_executable=None):
    tools = {}
    rustup = shutil.which("rustup", path=env.get("PATH"))
    require(rustup, "installed selected toolchain resolver absent")
    resolver = Path(rustup).resolve(strict=True)
    resolver_identity = custody.fingerprint(resolver, resolver.parent)
    names = ("cargo", "rustc", "rustdoc", "cargo-fmt", "rustfmt")
    for name in names:
        require(custody.fingerprint(resolver, resolver.parent) == resolver_identity, "toolchain resolver changed")
        row = call([str(resolver), "which", "--toolchain", HOSTED_RUST if context["mode"] == HOSTED_MODE else RUST, name], repo, env, logs,
                   "tool-path-" + name, 45, context)
        require(type(row["exit_code"]) is int and row["exit_code"] == 0, "selected tool unavailable: " + name)
        path = Path(observer.original_stream(logs, row, "stdout").decode().strip())
        require(path.is_absolute(), "tool path must be absolute")
        path = path.resolve(strict=True)
        if tools:
            require(path.parent == Path(tools["cargo"]["absolute_path"]).parent, "selected tools differ in toolchain root")
        tools[name] = {"absolute_path": str(path), "fingerprint": custody.fingerprint(path, path.parent)}
    tools["rustup"] = {"absolute_path": str(resolver), "fingerprint": resolver_identity}
    for name, candidates in (("python", (str(python_executable) if python_executable is not None else sys.executable,)), ("git", ("git",)), ("node", ("node",)), ("bash", ("bash",))):
        found = candidates[0] if name == "python" else shutil.which(candidates[0], path=env.get("PATH"))
        require(found, "SDK G0 prerequisite absent: " + name)
        path = Path(found).resolve(strict=True)
        tools[name] = {"absolute_path": str(path), "fingerprint": custody.fingerprint(path, path.parent)}
    if context["mode"] == HOSTED_MODE:
        # Bind the GNU host compiler/linker actually used on this Windows image.
        for name in ("gcc", "g++", "ar", "as", "ld"):
            found = shutil.which(name, path=env.get("PATH"))
            require(found, "hosted GNU toolchain prerequisite absent: " + name)
            path = Path(found).resolve(strict=True)
            tools[name] = {"absolute_path": str(path), "fingerprint": custody.fingerprint(path, path.parent)}
        require(len({Path(tools[name]["absolute_path"]).parent for name in ("gcc", "g++", "ar", "as", "ld")}) == 1,
                "hosted GNU compiler and binutils come from different tool roots")
    tool_env = dict(env, CARGO=tools["cargo"]["absolute_path"], RUSTC=tools["rustc"]["absolute_path"],
                    RUSTDOC=tools["rustdoc"]["absolute_path"], RUSTFMT=tools["rustfmt"]["absolute_path"])
    tool_env["PATH"] = os.pathsep.join(dict.fromkeys([str(Path(row["absolute_path"]).parent) for row in tools.values()])) + os.pathsep + env.get("PATH", "")
    if context["mode"] == HOSTED_MODE:
        tool_env.update(CC=tools["gcc"]["absolute_path"], CXX=tools["g++"]["absolute_path"],
                        AR=tools["ar"]["absolute_path"],
                        CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER=tools["gcc"]["absolute_path"])
        for name in ("gcc", "g++"):
            row = call([tools[name]["absolute_path"], "-dumpmachine"], repo, tool_env, logs,
                       "tool-target-" + name, 45, context)
            require(type(row["exit_code"]) is int and row["exit_code"] == 0, "hosted GNU target observation failed")
            target = observer.original_stream(logs, row, "stdout").decode("ascii").strip()
            require(target == "x86_64-w64-mingw32", "hosted GNU tool target is incompatible with the frozen Windows selection")
            tools[name]["target"] = target
    for name, item in tools.items():
        flag = "-Vv" if name == "rustc" else "--version"
        row = call([item["absolute_path"], flag], repo, tool_env, logs, "tool-version-" + name, 45, context)
        require(type(row["exit_code"]) is int and row["exit_code"] == 0, "tool version failed: " + name)
        raw = observer.original_stream(logs, row, "stdout").decode("utf-8")
        item["version"] = raw
        require(bool(raw.strip()), "empty observed tool version")
        if name in ("cargo", "rustdoc"):
            require(raw.startswith(name + " "), "unexpected observed tool version")
        if name == "rustc":
            host = "x86_64-pc-windows-gnu" if windows_selection(context["mode"]) else "x86_64-unknown-linux-gnu"
            require("release: " + RUST + "\n" in raw.replace("\r\n", "\n")
                    and "host: " + host + "\n" in raw.replace("\r\n", "\n"), "compiler release or actual host differs")
        if name == "node":
            require(raw.startswith("v22."), "SDK G0 requires the reviewed Node22 major")
    verify_tools(tools)
    return tools, tool_env


def sdk_g0(repo, snapshot, tools, env, logs, context, call, stage=None):
    """SDK-owned focal checks. Their originals are not the gate's G0 results."""
    required = {"scripts/verify-oss-hygiene.py", "scripts/verify-source.py", "scripts/sync-contracts.py",
                "Cargo.toml", "Cargo.lock", "rust-toolchain.toml"}
    require(required <= set(snapshot["files"]), "SDK G0 source closure absent")
    require(context["executed_source_sha"] == snapshot["sha"] and context["executed_tree_sha"] == snapshot["tree"],
            "SDK G0 context belongs to another source")
    source_unchanged(repo, snapshot)
    verify_tools(tools)
    python = tools["python"]["absolute_path"]
    specs = (
        ("g0-prerequisites", [python, "-I", "-B", "-X", "utf8", "-c",
          "import sys,tomllib,jsonschema,yaml; assert sys.version_info >= (3,11); print('SDK_G0_PREREQUISITES_AVAILABLE')"]),
        ("g0-hygiene", [python, "-I", "-B", "-X", "utf8", str(repo / "scripts/verify-oss-hygiene.py")]),
        ("g0-source", [python, "-I", "-B", "-X", "utf8", str(repo / "scripts/verify-source.py")]),
        ("g0-format", [tools["cargo-fmt"]["absolute_path"], "fmt", "--all", "--", "--check"]),
    )
    stage = {} if stage is None else stage
    stage.update(status="NOT_COMPLETED", executed_source_sha=snapshot["sha"],
                 executed_tree_sha=snapshot["tree"], github=context["github"],
                 steps=[], rust_compiled=False, remaining_sdk_ci={x: "NOT_RUN" for x in G0_REMAINING})
    for label, argv in specs:
        verify_tools(tools)
        row = call(argv, repo, env, logs, label, 300, context)
        stage["steps"].append(row)
        require(type(row["exit_code"]) is int and row["exit_code"] == 0, "SDK focal G0 failed: " + label)
        stdout = observer.original_stream(logs, row, "stdout")
        observer.original_stream(logs, row, "stderr")
        if label == "g0-source":
            # sync-contracts may print before the final JSON. Parse only the
            # final exact report, never treating its text as independent proof.
            normalized = stdout.replace(b"\r\n", b"\n")
            marker = normalized.rfind(b'{\n  "source_validation"')
            require(marker >= 0, "SDK source report absent")
            result = custody.strict_json(normalized[marker:].strip())
            require(result.get("source_validation") == "PASS" and result.get("rust_compiled") is False
                    and type(result.get("counts")) is dict
                    and all(type(v) is int and v >= 0 for v in result["counts"].values()), "SDK source report differs")
            stage["source_report"] = result
    stage["source_postcheck"] = source_unchanged(repo, snapshot)
    verify_tools(tools)
    stage["status"] = "PASS_G0_SDK_FOCAL"
    custody.write_new(logs / "sdk-g0.json", stage)
    return stage


def producer_plan(mode, cargo, causal=None):
    if causal is not None:
        return ((causal["id"], [cargo, *causal["producer"]], (TARGETS[causal["target"]],), True),)
    if mode == "linux-g2":
        return (
            ("fixture", [cargo, "build", "--locked", "-p", "semwright-native-sdk", "--features", "driver",
                         "--example", "owner-operation-permit-fixture", "--message-format=json"], (TARGETS["fixture"],), False),
            ("sandbox", [cargo, "build", "--locked", "-p", "semwright-plugin-host", "--bin", "semwright-sandbox",
                         "--message-format=json"], (TARGETS["sandbox"],), False),
            ("core", [cargo, "test", "--locked", "-p", "semwright-core", "--lib", "--no-run",
                      "--message-format=json"], (TARGETS["core"],), True),
        )
    require(mode == "windows-g1", "unknown producer selection")
    configs = (
        ("core", ("-p", "semwright-core", "--lib", "--test", "provider_runtime"), ("core", "provider-runtime")),
        ("types", ("-p", "semwright-types", "--test", "provider_mount_references"), ("mount-references",)),
        ("driver-sdk", ("-p", "semwright-driver-sdk", "--lib"), ("driver-sdk",)),
        ("driver-host", ("-p", "semwright-driver-host", "--lib"), ("driver-host",)),
        ("native-sdk", ("-p", "semwright-native-sdk", "--features", "driver,effects", "--lib", "--test", "cooperation"), ("native-sdk", "cooperation")),
        ("daemon-private", ("-p", "semwright-daemon", "--bin", "semwrightd", "--features", "private-host-stdin-custody"), ("daemon",)),
        ("daemon-no-default", ("-p", "semwright-daemon", "--bin", "semwrightd", "--no-default-features"), ("daemon",)),
    )
    return tuple((label, [cargo, "test", "--locked", *args, "--no-run", "--message-format=json"],
                  tuple(TARGETS[k] for k in keys), True) for label, args, keys in configs)


def compiled_paths(raw, code, repo, target_root, specs, testing, argv):
    paths = producer.select_binary_paths(raw, code, repo, target_root, specs, testing=testing)
    rows = producer.cargo_rows(raw, code)
    for spec in specs:
        matching = [r for r in rows if r.get("reason") == "compiler-artifact"
                    and r.get("executable") == paths[spec["name"]]
                    and r.get("profile", {}).get("test") is testing]
        require(len(matching) == 1, "compiled configuration is ambiguous")
        features = matching[0].get("features")
        require(type(features) is list and all(type(x) is str for x in features)
                and len(features) == len(set(features)), "compiled feature identity is missing or duplicated")
        requested = argv[argv.index("--features") + 1].split(",") if "--features" in argv else []
        require(set(requested) <= set(features), "requested feature is absent in compiled artifact")
        if "--no-default-features" in argv:
            require(not features, "no-default daemon unexpectedly enabled features")
        if spec["crate"] == "daemon" and "private-host-stdin-custody" not in requested:
            require("private-host-stdin-custody" not in features, "daemon feature variants were mixed")
    return paths


def prepare_role(state, mode, cargo, barrier):
    prepared = {"collections": {}, "steps": []}
    for label, argv, specs, testing in producer_plan(mode, cargo, state.get("causal")):
        row = barrier(argv, state["repo"], state["env"], state["logs"], label + "-compile", 900, state["context"])
        raw = observer.original_stream(state["logs"], row, "stdout")
        observer.original_stream(state["logs"], row, "stderr")
        compiled_paths(raw, row["exit_code"], state["repo"], state["target"], specs, testing, argv)
        prepared["collections"][label] = {"command": row, "specs": specs, "testing": testing}
        prepared["steps"].append(label)
    return prepared


def parse_roster(raw, code):
    require(type(code) is int and code == 0, "compiled list did not complete")
    normalized = raw.decode("utf-8").replace("\r\n", "\n")
    names = tuple(re.findall(r"(?m)^([A-Za-z_][A-Za-z_0-9:]*): test$", normalized))
    # The shared parser rejects duplicates, benchmarks and non-list output.
    return observer.selected_roster(raw, code, {"prefixes": None}, names, "repair")["all"]


def group_roster(all_names, group):
    require(all_names and len(all_names) == len(set(all_names)), "compiled roster incomplete or repeated")
    _collection, _target, selector, namespace, count = group
    selected = tuple(name for name in all_names if selector is None or selector in name)
    require(len(selected) == count and (namespace is None or all(n.startswith(namespace) for n in selected)),
            "selected compiled namespace or expected count changed")
    return {"all": tuple(all_names), "selected": selected,
            "not_selected": tuple(n for n in all_names if n not in selected), "not_selected_credit": "NONE"}


def group_argv(binary, group):
    selector = group[2]
    return [binary["absolute_path"], *([] if selector is None else [selector]), *TEST_OPTIONS]


def admit_group(raw, row, binary, after, listed, expected_argv, context):
    require(row.get("observation") == "DIRECT_PROCESS_EXIT_OBSERVED"
            and row.get("log_custody") == "VERIFIED_SNAPSHOT"
            and type(row.get("exit_code")) is int and row["exit_code"] == 0,
            "group exit, process or originals are incomplete")
    require(row.get("argv") == expected_argv and row.get("context") == context
            and binary == after, "group argv, source context or binary changed")
    require(listed["selected"] and len(listed["selected"]) == len(set(listed["selected"]))
            and set(listed["selected"]) <= set(listed["all"]), "group roster is not closed")
    text = custody.ANSI.sub("", raw.decode("utf-8").replace("\r\n", "\n"))
    cases = re.findall(r"(?m)^test (.+) \.\.\. (ok|FAILED|ignored)\s*$", text)
    summaries = re.findall(r"(?m)^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; "
                           r"(\d+) ignored; (\d+) measured; (\d+) filtered out;", text)
    require(len(cases) == len(listed["selected"]) and len({n for n, _ in cases}) == len(cases)
            and {n for n, _ in cases} == set(listed["selected"])
            and all(state == "ok" for _, state in cases), "group omitted, duplicated, added or skipped a named case")
    expected = (len(cases), 0, 0, 0, len(listed["all"]) - len(cases))
    require(len(summaries) == 1 and summaries[0][0] == "ok"
            and tuple(map(int, summaries[0][1:])) == expected, "group counts or filtered roster differ")
    require(not re.search(r"(?m)^thread .+ panicked at ", text), "passing group emitted an unexpected panic")
    return {"status": "PASS_EXACT_COMPILED_GROUP", "ids": [n for n, _ in cases],
            "binary_sha256": binary["sha256"], "context": context, "command": row,
            "combined_original_sha256": hashlib.sha256(raw).hexdigest()}


def current_binary(binary, target_root):
    return {"absolute_path": binary["absolute_path"],
            **custody.fingerprint(Path(binary["absolute_path"]), target_root)}


def list_role(state, barrier):
    require(barrier.phase == "OBSERVING", "listing before producer seal")
    result = {}
    for collection, binaries in state["binaries"].items():
        for name, binary in binaries.items():
            require(current_binary(binary, state["target"]) == binary, "compiled binary changed before list")
            row = barrier([binary["absolute_path"], "--list", "--format", "terse"], state["repo"],
                          state["env"], state["logs"], collection + "-" + name + "-list", 45, state["context"])
            names = parse_roster(observer.original_stream(state["logs"], row, "stdout"), row["exit_code"])
            observer.original_stream(state["logs"], row, "stderr")
            require(current_binary(binary, state["target"]) == binary, "compiled binary changed after list")
            result[collection + "/" + name] = {"names": names, "command": row}
    custody.write_new(state["logs"] / "compiled-rosters.json", result)
    return result


def observe_causal(state, barrier):
    require(barrier.phase == "OBSERVING", "causal observation before producer seal")
    causal = state["causal"]
    name = TARGETS[causal["target"]]["name"]
    binary = state["binaries"][causal["id"]][name]
    names = state["rosters"][causal["id"] + "/" + name]["names"]
    require(causal["test"] in names, "exact causal method absent from compiled binary")
    listed = {"all": names, "selected": (causal["test"],)}
    pair = {"id": causal["id"], "baseline": {"targets": {causal["target"]: [causal["test"]]}},
            "repair": {"targets": {}}, "failures": {causal["test"]: causal["failure"]}}
    argv = observer.case_argv(binary, pair, "baseline", causal["test"])
    row = barrier(argv, state["repo"], state["env"], state["logs"], "causal-once", 900, state["context"])
    raw = observer.original_stream(state["logs"], row, "stdout") + b"\n" + observer.original_stream(state["logs"], row, "stderr")
    return observer.admit_case(pair, "baseline", causal["test"], raw, row, binary,
                               current_binary(binary, state["target"]), listed, state["context"])


def observe_fixed_groups(state, barrier, results):
    require(barrier.phase == "OBSERVING" and not results, "group observation must start once after sealing")
    for index, group in enumerate(GROUPS):
        collection, key = group[:2]
        name = TARGETS[key]["name"]
        binary = state["binaries"][collection][name]
        require(current_binary(binary, state["target"]) == binary, "group binary changed before execution")
        listed = group_roster(state["rosters"][collection + "/" + name]["names"], group)
        argv = group_argv(binary, group)
        row = barrier(argv, state["repo"], state["env"], state["logs"], "group-" + str(index).zfill(2), 900, state["context"])
        raw = observer.original_stream(state["logs"], row, "stdout") + b"\n" + observer.original_stream(state["logs"], row, "stderr")
        result = admit_group(raw, row, binary, current_binary(binary, state["target"]), listed, argv, state["context"])
        result.update(collection=collection, compiled_roster=listed)
        results.append(result)
    require(len(results) == 13 and sum(len(row["ids"]) for row in results) == 134, "fixed group closure incomplete")


class CapabilityUnavailable(custody.VerificationError):
    pass


def linux_capability(state, barrier):
    require(sys.platform == "linux" and platform.machine() == "x86_64", "real Linux x64 capability probe required")
    library = ctypes.CDLL(None, use_errno=True)
    library.syscall.restype = ctypes.c_long
    abi = library.syscall(ctypes.c_long(444), ctypes.c_void_p(), ctypes.c_size_t(0), ctypes.c_uint(1))
    if abi < 3:
        raise CapabilityUnavailable("Landlock ABI V3 unavailable; no fallback")
    bwrap = Path("/usr/bin/bwrap").resolve(strict=True)
    true_binary = Path("/usr/bin/true").resolve(strict=True)
    helper = state["binaries"]["sandbox"][TARGETS["sandbox"]["name"]]
    require(current_binary(helper, state["target"]) == helper, "sandbox helper changed")
    identities = {str(p): custody.fingerprint(p, p.parent) for p in (bwrap, true_binary)}
    argv = [str(bwrap), "--unshare-all", "--die-with-parent", "--new-session"]
    for directory in ("/usr", "/lib", "/lib64", "/etc"):
        if Path(directory).is_dir():
            argv.extend(["--ro-bind", directory, directory])
    argv.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/dev/shm",
                 "--dir", "/plugin", "--ro-bind", str(true_binary), "/plugin/bin", "--ro-bind",
                 helper["absolute_path"], "/plugin/helper", "--chdir", "/tmp", "--", "/plugin/helper", "--", "/plugin/bin"])
    row = barrier(argv, state["repo"], state["env"], state["logs"], "real-sandbox-capability", 30, state["context"])
    if (type(row["exit_code"]) is not int or row["exit_code"] != 0
            or row.get("owned_group_after_wait") != "ABSENT"):
        raise CapabilityUnavailable("actual sandbox enforcement unavailable; no host policy relaxation")
    for path, identity in identities.items():
        p = Path(path)
        require(custody.fingerprint(p, p.parent) == identity, "capability executable changed")
    require(current_binary(helper, state["target"]) == helper, "sandbox helper changed after capability query")
    return {"landlock_abi": int(abi), "actual_helper": helper, "original_command": row,
            "probe_payload": "TRUE_ONLY_NOT_NATIVE_CASE", "tools": identities}


def observe_linux_two(state, barrier, results):
    require(barrier.phase == "OBSERVING" and not results, "Linux observation must start once after sealing")
    state["capability"] = linux_capability(state, barrier)
    binary = state["binaries"]["core"][TARGETS["core"]["name"]]
    names = state["rosters"]["core/" + TARGETS["core"]["name"]]["names"]
    require(set(G2_CASES) == {n for n in names if n.startswith("owner_operation_process_tests::")},
            "Linux native roster changed")
    env = dict(state["env"], SEMWRIGHT_TEST_OWNER_PERMIT_DRIVER=state["binaries"]["fixture"][TARGETS["fixture"]["name"]]["absolute_path"],
               SEMWRIGHT_TEST_SANDBOX_HELPER=state["binaries"]["sandbox"][TARGETS["sandbox"]["name"]]["absolute_path"])
    for index, name in enumerate(G2_CASES):
        for binaries in state["binaries"].values():
            producer.verify_binaries(binaries, state["target"])
        argv = [binary["absolute_path"], "--exact", name, "--ignored", *TEST_OPTIONS]
        row = barrier(argv, state["repo"], env, state["logs"], "linux-native-" + str(index), 90, state["context"])
        raw = observer.original_stream(state["logs"], row, "stdout") + b"\n" + observer.original_stream(state["logs"], row, "stderr")
        result = admit_group(raw, row, binary, current_binary(binary, state["target"]),
                             {"all": names, "selected": (name,)}, argv, state["context"])
        result.update(scope="EXISTING_REAL_HOST_NATIVE_PERMIT_ORACLE", internal_success_tempdir="REMOVED_BY_PRODUCT",
                      internal_control_effect_audit_originals="NOT_RETAINED_ON_SUCCESS", wire_capture="NOT_OBSERVED")
        results.append(result)
    require(len(results) == 2, "Linux native selection incomplete")


def resource_values(value, mode, initial):
    require(type(value) is dict and set(value) == {"physical", "virtual", "disk", "compilers"}
            and all(type(value[n]) is int and value[n] >= 0 for n in ("physical", "virtual", "disk"))
            and type(value["compilers"]) is list, "resource observation is incomplete")
    if windows_selection(mode):
        limits = (4, 8, 18) if initial else (4, 0, 18)
        require(not value["compilers"], "another compiler owns the local slot")
    else:
        limits = (2, 0, 12)
    require(all(value[n] >= minimum * 1024**3 for n, minimum in zip(("physical", "virtual", "disk"), limits)),
            "insufficient resources for the selected finite budget")
    return {**value, "minimum_gib": dict(zip(("physical", "virtual", "disk"), limits)),
            "continuous_monitoring": False, "cargo_jobs": 1}


def observe_resources(mode, run_root, logs, context, call, label, initial=False):
    disk = shutil.disk_usage(run_root).free
    if windows_selection(mode):
        script = ("$ErrorActionPreference='Stop'; $m=Get-CimInstance Win32_OperatingSystem; "
                  "$a=@(Get-Process -ErrorAction Stop | Where-Object {$_.ProcessName -in @('cargo','rustc','rustdoc')} | "
                  "Select-Object Id,ProcessName); "
                  "@{physical=[UInt64]$m.FreePhysicalMemory*1024; virtual=[UInt64]$m.FreeVirtualMemory*1024; "
                  "compilers=$a} | ConvertTo-Json -Compress -Depth 4")
        shell = shutil.which("powershell")
        require(shell, "Windows resource observation requires PowerShell")
        row = call([shell, "-NoProfile", "-NonInteractive", "-Command", script], run_root,
                   dict(os.environ), logs, label + "-query", 45, context)
        require(type(row["exit_code"]) is int and row["exit_code"] == 0, "resource observation failed")
        value = custody.strict_json(observer.original_stream(logs, row, "stdout").strip())
        require(type(value) is dict and set(value) == {"physical", "virtual", "compilers"}, "resource record shape differs")
        value["disk"] = disk
    else:
        raw = Path("/proc/meminfo").read_text(encoding="ascii")
        match = re.findall(r"(?m)^MemAvailable:\s+([0-9]+) kB$", raw)
        require(len(match) == 1, "Linux available memory is not observable")
        value = {"physical": int(match[0]) * 1024, "virtual": 0, "disk": disk, "compilers": []}
    result = resource_values(value, mode, initial)
    custody.write_new(logs / (label + ".json"), {"at_utc": custody.now(), "context": context, **result})
    return result


def canonical_run_root(path):
    root = Path(path).absolute()
    require(root.parent.resolve(strict=True) == root.parent, "run root parent is linked or not canonical")
    return root


def owned_paths(gate, sdk, baselines, run_root, mode):
    paths = [gate, sdk, *baselines.values()]
    require(all(p.is_absolute() and p.is_dir() and ".." not in p.parts for p in paths)
            and len(set(paths)) == len(paths), "gate/product checkouts must be distinct existing absolute paths")
    require(run_root.is_absolute() and ".." not in run_root.parts and not run_root.exists(), "fresh absolute run root required")
    require(all(not run_root.is_relative_to(p) and not p.is_relative_to(run_root) for p in paths),
            "outputs and sources must be disjoint")
    for index, path in enumerate(paths):
        require(all(not path.is_relative_to(other) and not other.is_relative_to(path)
                    for other in paths[:index]), "source checkouts overlap")
        info = path.lstat()
        require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
                "linked checkout root")
    if mode == "windows-g1":
        controlled = PureWindowsPath("E:/Projects/motionwright")
        require(all(PureWindowsPath(str(p)).is_relative_to(controlled) and PureWindowsPath(str(p)) != controlled
                    for p in (*paths, run_root)), "local controlled resources must remain inside the requested E project")
    else:
        workspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
        require(all(p.is_relative_to(workspace) and p != workspace for p in (*paths, run_root)),
                "remote source/evidence escaped the owned workspace")


def retained_failure_roots(state, evidence):
    """Copy only observed failed fixture roots still inside the owned temp tree."""
    records = []
    for path in state["logs"].glob("linux-native-*.stderr.log"):
        raw = path.read_bytes()
        for value in re.findall(rb"(?m)^RETAINED_OWNER_PERMIT_FIXTURE_ROOT=([^\r\n]+)$", raw):
            root = Path(os.fsdecode(value)).absolute()
            require(root.is_relative_to(state["temporary"]) and root != state["temporary"], "failure fixture escaped owned temporary root")
            info = root.lstat()
            require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                    and not getattr(info, "st_file_attributes", 0) & 0x400, "failure fixture root is missing or linked")
            files, total = {}, 0
            def walk_error(error):
                raise error
            for directory, child_dirs, child_files in os.walk(root, followlinks=False, onerror=walk_error):
                require(not Path(directory).is_symlink(), "linked failure fixture directory")
                for name in child_dirs:
                    info = (Path(directory) / name).lstat()
                    require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
                            "linked failure fixture directory")
                for name in child_files:
                    p = Path(directory) / name
                    stamp = custody.fingerprint(p, state["temporary"], 16 * 1024**2)
                    total += stamp["bytes"]
                    require(len(files) < 1024 and total <= 64 * 1024**2, "failed fixture exceeded retained source-oracle bound")
                    files[p.relative_to(root).as_posix()] = observer.retain_file(p, state["temporary"], evidence, "failed-fixtures", stamp)
            records.append({"original_path": str(root), "files": files, "raw_wire_capture": False,
                            "status": "FILES_RETAINED" if files else "OBSERVED_EMPTY_DIRECTORY"})
    return records


def admit_gate_controls(path, snapshot, python_tool, context):
    """Validate the existing runner's exact-source receipt and verbose originals."""
    folder = path.parent
    stamp = custody.fingerprint(path, folder, 16 * 1024**2)
    receipt = custody.strict_json(path.read_bytes())
    require(custody.fingerprint(path, folder, 16 * 1024**2) == stamp, "gate runner receipt changed")
    if receipt.get("status") == "UNKNOWN":
        raise observer.UnknownExecution("gate controls uncertain; no successor", {"runner_receipt": receipt, "receipt": stamp})
    require(receipt.get("schema") == "motionwright.unittest-execution/1"
            and receipt.get("executed_source_sha") == snapshot["sha"] and receipt.get("tree_sha") == snapshot["tree"]
            and receipt.get("status") == "PASS" and type(receipt.get("exit_code")) is int and receipt["exit_code"] == 0
            and receipt.get("require_complete") is True and "source_postcheck_error" not in receipt,
            "gate controls are incomplete or belong to another source")
    files = snapshot["files"]
    require(receipt.get("runner_sha256") == files["tooling/ci_test_runner.py"]["sha256"]
            and receipt.get("python_executable_sha256") == python_tool["fingerprint"]["sha256"]
            and receipt.get("test_definition_sha256") == {"tooling/tests/" + p: files["tooling/tests/" + p]["sha256"] for p in GATE_PATTERNS}
            and receipt.get("source_locks") == {p: files[p]["sha256"] for p in ("Cargo.lock", "SOURCE_LOCK.json", "apps/studio/package-lock.json")}
            and receipt.get("github") == {k: context["github"].get(k, "") for k in
                ("GITHUB_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB")},
            "gate controls provider, definitions, locks or context differ")
    expected = {}
    python = python_tool["absolute_path"]
    for pattern in GATE_PATTERNS:
        module = "tooling.tests." + pattern[:-3]
        expected["discovery:" + pattern] = [python, "-B", "-E", "-s", "-m", "unittest", "discover", "-s", "tooling/tests", "-p", pattern, "-v"]
        expected["dotted:" + module] = [python, "-B", "-E", "-s", "-m", "unittest", module, "-v"]
    steps = receipt.get("steps")
    require(type(steps) is list and len(steps) == len(expected)
            and {r.get("entry_point") for r in steps} == set(expected), "gate entry points missing, repeated or foreign")
    rosters, originals = {}, [stamp]
    for index, step in enumerate(steps):
        label = step["entry_point"]
        require(step.get("command") == expected[label] and step.get("status") == "PASS"
                and type(step.get("exit_code")) is int and step["exit_code"] == 0, "gate entry point did not pass exactly")
        raw = {}
        for stream in ("stdout", "stderr"):
            name = path.stem + "-" + str(index).zfill(2) + "-" + stream + ".log"
            original = custody.fingerprint(folder / name, folder, 16 * 1024**2)
            require(step.get(stream) == {"file": name, "sha256": original["sha256"]}, "gate entry-point original differs")
            raw[stream] = (folder / name).read_bytes()
            require(hashlib.sha256(raw[stream]).hexdigest() == original["sha256"], "gate original changed during read")
            originals.append(original)
        text = raw["stderr"].decode("utf-8").replace("\r\n", "\n")
        counts = fast_runner.parse_unittest_summary(text)
        require(type(step.get("counts")) is dict and set(step["counts"]) == set(counts)
                and all(type(step["counts"][k]) is int for k in counts if k != "terminal_status")
                and step["counts"] == counts and counts["terminal_status"] == "OK"
                and counts["tests_run"] == counts["passed"] > 0
                and all(counts[k] == 0 for k in ("failures", "errors", "skipped", "expected_failures", "unexpected_successes")),
                "gate footer contains omissions, noninteger counts or failures")
        identifiers = []
        for line in text.splitlines():
            if not line.startswith("test_"):
                continue
            match = re.fullmatch(r"(test_\w+) \(([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\) \.\.\. ok", line)
            require(match is not None, "gate verbose record did not pass")
            method, qualified = match.groups()
            qualified = qualified.removeprefix("tooling.tests.")
            identifiers.append(qualified if qualified.endswith("." + method) else qualified + "." + method)
        require(len(identifiers) == len(set(identifiers)) == counts["passed"], "gate named roster is incomplete or duplicated")
        module = label.split(":", 1)[1]
        module = module[:-3] if label.startswith("discovery:") else module.removeprefix("tooling.tests.")
        require(all(name.startswith(module + ".") for name in identifiers), "gate verbose roster belongs to another module")
        rosters[label] = sorted(identifiers)
    for pattern in GATE_PATTERNS:
        require(rosters["discovery:" + pattern] == rosters["dotted:tooling.tests." + pattern[:-3]],
                "gate discovery and dotted rosters differ")
    for original in originals:
        require(custody.fingerprint(folder / original["path"], folder, 16 * 1024**2) == original,
                "gate controls originals changed after admission")
    return {"status": "PASS_EXACT_GATE_CONTRACTS", "source_sha": snapshot["sha"],
            "rosters": rosters, "originals": originals, "folder": str(folder)}


def local_gate_g0(gate, snapshot, tools, env, logs, context, call):
    frame = {**context, "executed_source_sha": snapshot["sha"], "executed_tree_sha": snapshot["tree"], "role": "gate-g0"}
    python = tools["python"]["absolute_path"]
    bootstrap = ("import pathlib,runpy,sys; p=pathlib.Path(sys.argv[1]); "
                 "sys.path.insert(0,str(p.parent)); runpy.run_path(str(p),run_name='__main__')")
    policies = []
    for name in GATE_POLICIES:
        row = call([python, "-I", "-B", "-c", bootstrap, str(gate / "tooling" / (name + ".py"))],
                   gate, env, logs, "gate-" + name, LOCAL_GATE_POLICY_SECONDS, frame)
        policies.append(row)
    report_path = logs / "gate-unittest.json"
    argv = [python, "-I", "-B", str(gate / "tooling/ci_test_runner.py"), "--repo", str(gate),
            "--mode", "both", "--require-complete", "--expected-source", snapshot["sha"], "--report", str(report_path)]
    for pattern in GATE_PATTERNS:
        argv.extend(["--pattern", pattern])
    row = call(argv, gate, env, logs, "gate-contracts", 300, frame)
    if row.get("exit_code") == 124:
        raise observer.UnknownExecution("gate contract runner timed out; no successor", row)
    controls = admit_gate_controls(report_path, snapshot, tools["python"], context)
    require(type(row.get("exit_code")) is int and row["exit_code"] == 0, "gate contract runner failed")
    require(all(type(r.get("exit_code")) is int and r["exit_code"] == 0 for r in policies), "gate policy failed")
    source_unchanged(gate, snapshot)
    verify_tools(tools)
    return {"status": "PASS_EXACT_LOCAL_GATE_G0", "executed_source_sha": snapshot["sha"],
            "policies": policies, "controls": controls, "sdk_g0_credit": "NONE"}


def execute_selection(states, mode, tools, env, evidence, budget, call, report):
    """All SDK G0 precedes every Cargo producer; no producer after sealing."""
    for state in states:
        report["phase"] = "sdk-g0:" + state["id"]
        state["g0"] = {}
        report["sdk_g0"][state["id"]] = state["g0"]
        sdk_g0(state["repo"], state["source"], tools, state["env"], state["logs"], state["context"], call, state["g0"])
    barrier = producer.ProducerBarrier(call, tools["cargo"]["absolute_path"])
    for state in states:
        budget.remaining(1)
        report["phase"] = "producers:" + state["id"]
        state["prepared"] = prepare_role(state, mode, tools["cargo"]["absolute_path"], barrier)
    barrier.begin_sealing()
    for state in states:
        source_unchanged(state["repo"], state["source"])
        verify_tools(tools)
        state["binaries"] = producer.seal_compiled_role(state["prepared"], state["repo"], state["target"],
                                                       state["logs"], evidence, barrier)
        paths = [b["absolute_path"] for group in state["binaries"].values() for b in group.values()]
        require(len(paths) == len(set(paths)), "compiled configurations alias the same mutable output")
        expected = 1 if state.get("causal") else (9 if mode == "windows-g1" else 3)
        require(len(paths) == expected, "sealed executable closure differs")
        report["binaries"][state["id"]] = state["binaries"]
    barrier.begin_observation()
    report["producer_barrier"] = "ALL_SELECTED_PRODUCERS_SEALED"
    for state in states:
        state["rosters"] = list_role(state, barrier)
        report["rosters"][state["id"]] = state["rosters"]
    # Preserve the four expected original failures before the fixed grouped run.
    for state in states:
        if state.get("causal"):
            report["phase"] = "causal:" + state["id"]
            report["causals"].append(observe_causal(state, barrier))
    fixed = next(s for s in states if s["id"] == "fixed")
    report["phase"] = "selected-fixed-observations"
    if mode == "windows-g1":
        observe_fixed_groups(fixed, barrier, report["groups"])
        require(len(report["causals"]) == 4, "causal closure incomplete")
    else:
        observe_linux_two(fixed, barrier, report["groups"])
        report["capability"] = fixed["capability"]
    budget.remaining(1)
    for state in states:
        source_unchanged(state["repo"], state["source"])
        for binaries in state["binaries"].values():
            producer.verify_binaries(binaries, state["target"])
    verify_tools(tools)
    report["source_postcheck"] = "ALL_EXACT_SOURCES_UNCHANGED"


def run(mode, source_sha, admission_id, sdk, baseline_paths, run_root, lease=None, environment=None, *, hosted_bootstrap=None):
    environment = dict(os.environ) if environment is None else dict(environment)
    context = request_context(environment, mode, source_sha, admission_id)
    selected = authority(admission_id, mode, source_sha)
    hosted = None
    if mode == HOSTED_MODE:
        hosted = require_hosted_transport_composition(context, hosted_bootstrap, environment)
        require(Path(sys.executable).resolve(strict=True) == Path(hosted["ack"]["prerequisites"]["worker_python_path"])
                and Path(sdk) == hosted["sdk"] and {k: Path(v) for k, v in baseline_paths.items()} == hosted["baselines"]
                and Path(run_root) == hosted["root"] / "execution", "hosted worker inputs differ from technical bootstrap")
    qualified = mode == HOSTED_MODE and hosted_purpose(context) == HOSTED_QUALIFICATION_PURPOSE
    selected_causals = () if qualified else causal_profiles(mode)
    selection_mode = "windows-g1" if windows_selection(mode) else mode
    gate = Path(__file__).resolve().parents[1]
    sdk = Path(sdk).resolve(strict=True)
    baselines = {k: Path(v).resolve(strict=True) for k, v in baseline_paths.items()}
    run_root = canonical_run_root(run_root)
    require(set(baselines) == {p["id"] for p in selected_causals},
            "baseline source roles are missing, duplicated or foreign")
    owned_paths(gate, sdk, baselines, run_root, mode)
    lease_identity = None
    if mode == "windows-g1":
        require(lease is not None, "reviewed exclusive local lease required")
        lease = Path(lease).absolute()
        require(PureWindowsPath(str(lease)).is_relative_to(PureWindowsPath("E:/Projects/motionwright")),
                "lease outside requested local project")
        lease_identity = custody.fingerprint(lease, lease.parent, 256 * 1024)
        value = custody.strict_json(lease.read_bytes())
        require(custody.fingerprint(lease, lease.parent, 256 * 1024) == lease_identity, "lease changed during admission")
        validate_lease(value, selected["binding"]["lease_sha256"], lease_identity["sha256"], run_root,
                       time.time_ns(), context["max_total_seconds"])
    else:
        require(lease is None, "local lease cannot authorize a remote native run")
    budget = hosted["budget"] if hosted is not None else Budget(context["max_total_seconds"])
    # Exclusive root is also the one-shot claim for this reviewed local lease.
    run_root.mkdir(mode=0o700, parents=False, exist_ok=False)
    evidence = run_root / "evidence"; evidence.mkdir(mode=0o700)
    logs = evidence / "commands"; logs.mkdir(mode=0o700)
    report = {"schema": SCHEMA, "status": "FAIL", "context": context, "phase": "gate-source",
              "started_utc": custody.now(), "budget_seconds": context["max_total_seconds"],
              "original_started_monotonic_ns": budget.started,
              "original_deadline_monotonic_ns": getattr(budget, "original_deadline", budget.deadline), "lease": lease_identity,
              **({"work_deadline_monotonic_ns": budget.deadline, "custody_reserved_seconds": HOSTED_TRANSPORT_RESERVE,
                  "technical_bootstrap_sha256": hosted["ack_sha256"]} if hosted is not None else {}),
              "budget_boundary": "COMMAND_TIMEOUTS_AND_PHASE_CHECKS_NOT_FILESYSTEM_INTERRUPTION",
              "sdk_g0": {}, "sources": {}, "binaries": {}, "rosters": {}, "causals": [], "groups": [],
              "commands": [], "formal_acceptance": False, "release_authorized": False,
              "retry_authorized": False, "success_fixture_internal_originals": "NOT_RETAINED_BY_PRODUCT",
              "source_policies_scope": "SDK_FOCAL_NOT_COMPLETE_SDK_CI", "full_process_tree_custody": False}
    custody.write_new(evidence / "started.json", report)
    observations, states = [], []
    counter = 0

    def observed(argv, cwd, env, output, label, timeout, frame):
        return observer.command(argv, cwd, env, output, label, timeout, frame, (evidence, observations))

    def bounded(argv, cwd, env, output, label, timeout, frame):
        return budget.call(observed, argv, cwd, env, output, label, timeout, frame)

    def guarded(argv, cwd, env, output, label, timeout, frame):
        nonlocal counter
        counter += 1
        resources = observe_resources(mode, run_root, logs, context, bounded, "pre-command-" + str(counter))
        if mode == HOSTED_MODE:
            sample = hosted_growth_observation(resources, context, hosted["ack"]["prerequisites"]["initial_resources"]["disk"])
            report.setdefault("hosted_growth_samples", []).append(sample)
            report.setdefault("hosted_component_samples", []).append(hosted_component_snapshot(
                hosted["root"], hosted["ack"]["context"], budget.deadline, phase="work",
                initial=hosted["ack"]["prerequisites"]["initial_components"], environment=environment))
        if lease_identity is not None:
            require(custody.fingerprint(lease, lease.parent, 256 * 1024) == lease_identity,
                    "exclusive lease changed; stop without replay")
            require(time.time_ns() < value["expires_unix_ns"], "exclusive lease expired; no renewal during execution")
        return bounded(argv, cwd, env, output, label, timeout, frame)

    try:
        gate_sha = custody.git(gate, "rev-parse", "HEAD").decode().strip()
        if mode in ("linux-g2", HOSTED_MODE):
            require(gate_sha == context["github"]["GITHUB_SHA"], "gate checkout differs from actual Actions source")
        context["gate_source_sha"] = gate_sha
        if mode == HOSTED_MODE:
            report["gate_admission"] = verify_hosted_gate(gate, context, selected, budget)
        gate_snapshot = custody.source_snapshot(gate, gate_sha, GATE_FILES)
        require(all(gate_snapshot["files"][p]["sha256"] == digest for p, digest in GATE_PINS.items()),
                "shared provider source differs")
        report["gate_source"] = gate_snapshot
        observer.retain_source(gate, gate_snapshot, evidence)
        for directory in ("cargo-home", "pip-cache", "npm-cache", "config", "data", "cache", "state", "runtime", "temporary", "targets"):
            (run_root / directory).mkdir(mode=0o700)
        env = clean_environment(environment, run_root, run_root / "targets", run_root / "temporary")
        if mode == HOSTED_MODE:
            env["RUSTUP_TOOLCHAIN"] = HOSTED_RUST
        report["initial_resources"] = observe_resources(mode, run_root, logs, context, bounded, "initial-resources", initial=True)
        if mode == HOSTED_MODE:
            report["hosted_cold_growth"] = hosted_growth_observation(report["initial_resources"], context,
                hosted["ack"]["prerequisites"]["initial_resources"]["disk"])
            report["hosted_component_origin"] = hosted["ack"]["prerequisites"]["initial_components"]
        tools, env = observe_tools(gate, env, logs, context, guarded)
        report["tools"] = tools
        if hosted is not None:
            report["hosted_image_tools"] = hosted_image_tool_roots(tools)
        if mode == "windows-g1":
            report["phase"] = "local-gate-g0"
            report["gate_g0"] = local_gate_g0(gate, gate_snapshot, tools, env, logs, context, guarded)
        else:
            report["gate_g0"] = {"scope": "EXACT_ACTIONS_GATE_JOB_REQUIRED_BY_NEEDS", "product_credit": "NONE"}
        profiles = [("fixed", sdk, FIXED, None)]
        if windows_selection(mode) and not qualified:
            profiles.extend((p["id"], baselines[p["id"]], p, p) for p in selected_causals)
        for identifier, repo, profile, causal in profiles:
            budget.remaining(1)
            folder = evidence / identifier; folder.mkdir(mode=0o700)
            target_root = run_root / "targets" / identifier; target_root.mkdir(mode=0o700)
            temporary = run_root / "temporary" / identifier; temporary.mkdir(mode=0o700)
            snapshot = full_snapshot(repo, profile)
            source_record = {"snapshot": snapshot, "retained": observer.retain_source(repo, snapshot, evidence)}
            report["sources"][identifier] = source_record
            frame = {**context, "executed_source_sha": profile["sha"], "executed_tree_sha": profile["tree"],
                     "role": identifier}
            state = {"id": identifier, "repo": repo, "source": snapshot, "causal": causal,
                     "context": frame, "logs": folder, "target": target_root, "temporary": temporary,
                     "env": dict(env, CARGO_TARGET_DIR=str(target_root), TMP=str(temporary), TEMP=str(temporary), TMPDIR=str(temporary))}
            states.append(state)
        if mode == HOSTED_MODE:
            actual_source_bytes = sum(item["bytes"] for state in states for item in state["source"]["files"].values())
            require(actual_source_bytes <= context["cold_growth"]["bytes"]["source_checkouts"],
                    "actual five-source bytes exceed reviewed cold source allowance")
            report["hosted_source_bytes"] = actual_source_bytes
        if qualified:
            execute_qualification(states, tools, evidence, budget, guarded, report)
        else:
            execute_selection(states, selection_mode, tools, env, evidence, budget, guarded, report)
        if hosted is not None:
            report["hosted_component_final"] = hosted_component_snapshot(hosted["root"], hosted["ack"]["context"], budget.deadline,
                phase="after-producer", initial=hosted["ack"]["prerequisites"]["initial_components"], environment=environment)
        source_unchanged(gate, gate_snapshot)
        producer.verify_commands(evidence, observations)
        if mode == "windows-g1":
            original_controls = report["gate_g0"]["controls"]
            refreshed = admit_gate_controls(Path(original_controls["folder"]) / "gate-unittest.json", gate_snapshot, tools["python"], context)
            require(refreshed == original_controls, "gate G0 original changed after product observation")
        report["status"] = ("OBSERVED_ONE_PRODUCER_COLD_GROWTH_QUALIFICATION" if qualified else
                            "PASS_HOSTED_WINDOWS_G1_SELECTED_ROOT_OBSERVATIONS" if mode == HOSTED_MODE else
                            "PASS_WINDOWS_G1_SELECTED_ROOT_OBSERVATIONS" if mode == "windows-g1"
                            else "PASS_LINUX_TWO_EXISTING_RUNTIME_ORACLES")
    except (observer.UnknownExecution, custody.UnknownObservation) as error:
        report.update(status="UNKNOWN_NO_RETRY", error=str(error), uncertain_command=error.command)
    except CapabilityUnavailable as error:
        report.update(status="BLOCKED_CAPABILITY_NOT_ACCEPTANCE", error=str(error))
    except BaseException as error:
        report.update(status="FAIL", error=type(error).__name__ + ": " + str(error))
    finally:
        if mode == "linux-g2":
            for state in states:
                try:
                    report.setdefault("retained_failed_fixtures", []).extend(retained_failure_roots(state, evidence))
                except BaseException as error:
                    report.setdefault("custody_errors", []).append(type(error).__name__ + ": " + str(error))
                    if report["status"].startswith("PASS_"):
                        report["status"] = "UNKNOWN_CUSTODY_NO_RETRY"
        report["commands"] = observations
        report["finished_utc"] = custody.now()
        if hosted is not None:
            report["finished_monotonic_ns"] = time.monotonic_ns()
            if report["finished_monotonic_ns"] >= budget.deadline:
                report["status"] = "UNKNOWN_ORIGINAL_WORK_BUDGET_EXHAUSTED_NO_RETRY"
        try:
            custody.write_new(evidence / "receipt.json", report)
        except BaseException as error:
            report.update(status="UNKNOWN_CUSTODY_NO_RETRY", receipt_error=type(error).__name__ + ": " + str(error))
            # Do not overwrite or retry a possibly partial terminal receipt.
            print(json.dumps(report, sort_keys=True))
            return 124
    print(json.dumps({"status": report["status"], "receipt": str(evidence / "receipt.json"),
                      "product_source_sha": source_sha, "formal_acceptance": False}, sort_keys=True))
    return 0 if (report["status"].startswith("PASS_") or report["status"] == "OBSERVED_ONE_PRODUCER_COLD_GROWTH_QUALIFICATION") else (124 if report["status"].startswith("UNKNOWN") else 1)


CAPACITY_SCHEMA = "motionwright.sdk-foundation-hosted-capacity/1"
CAPACITY_SECONDS = 120
CAPACITY_QUERY_SECONDS = 45
CAPACITY_WORKER_SECONDS = 60
CAPACITY_MAX_ENTRIES = 50000
CAPACITY_MAX_LOGICAL_BYTES = 256 * 1024**3
CAPACITY_MAX_RECORD = 512 * 1024
CAPACITY_ROLES = ("runner_temp", "targets", "cargo_home", "gate_source", "image_cargo_home", "image_rustup_home")
CAPACITY_MEMORY_SCRIPT = (
    "$ErrorActionPreference='Stop'; $m=Get-CimInstance Win32_OperatingSystem -ErrorAction Stop; "
    "$a=@(Get-Process -ErrorAction Stop | Where-Object {$_.ProcessName -in @('cargo','rustc','rustdoc')} | "
    "Select-Object Id,ProcessName); "
    "@{physical=[UInt64]$m.FreePhysicalMemory*1024; virtual=[UInt64]$m.FreeVirtualMemory*1024; "
    "physical_total=[UInt64]$m.TotalVisibleMemorySize*1024; virtual_total=[UInt64]$m.TotalVirtualMemorySize*1024; "
    "compilers=$a} | ConvertTo-Json -Compress -Depth 4")


def capacity_context(environment):
    """Read-only hosted observation does not consult or grant SDK authority."""
    expected = {"sys.platform": "win32", "GITHUB_ACTIONS": "true", "RUNNER_OS": "Windows",
                "RUNNER_ARCH": "X64", "RUNNER_ENVIRONMENT": "github-hosted", "IMAGEOS": HOSTED_IMAGE_OS,
                "GITHUB_EVENT_NAME": "workflow_dispatch", "SDK_FOUNDATION_SCOPE": HOSTED_SCOPE,
                "GITHUB_JOB": HOSTED_SCOPE}
    observed = {key: sys.platform if key == "sys.platform" else environment.get(key, "") for key in expected}
    # Diagnose only the closed public provenance keys. Never dump the inherited environment.
    diagnostic = {key: value[:128] if type(value) is str else "<non-string>" for key, value in observed.items()}
    require(observed == expected, "capacity observation needs the actual standard hosted job: "
            + json.dumps({"observed": diagnostic, "expected": expected}, sort_keys=True))
    keys = ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA", "GITHUB_REPOSITORY", "GITHUB_WORKFLOW_REF",
            "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "GITHUB_EVENT_NAME")
    github = {key: environment.get(key, "") for key in keys}
    require(all(type(github[key]) is str and SHA.fullmatch(github[key]) for key in ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA"))
            and github["GITHUB_SHA"] == github["GITHUB_WORKFLOW_SHA"]
            and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", github["GITHUB_REPOSITORY"])
            and github["GITHUB_WORKFLOW_REF"].startswith(github["GITHUB_REPOSITORY"] + "/" + WORKFLOW + "@refs/heads/")
            and all(re.fullmatch(r"[1-9][0-9]*", github[key]) for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")),
            "capacity workflow provenance differs")
    version = environment.get("IMAGEVERSION", "")
    require(type(version) is str and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", version), "hosted image version absent")
    return {"schema": CAPACITY_SCHEMA, "scope": "READ_ONLY_STANDARD_HOSTED_CAPACITY",
            "github": github, "runner_label": HOSTED_RUNNER, "image_os": HOSTED_IMAGE_OS,
            "image_version": version, "gate_source_sha": github["GITHUB_SHA"],
            "sdk_execution_authorized": False, "sdk_execution": "NOT_RUN",
            "continuous_resource_watchdog": False, "process_tree_closed": False}


def capacity_paths(environment, gate):
    workspace = Path(environment["GITHUB_WORKSPACE"]).resolve(strict=True)
    require(gate == (workspace / "gate").resolve(strict=True), "capacity gate checkout differs")
    runner_temp = Path(environment["RUNNER_TEMP"])
    profile = Path(environment["USERPROFILE"])
    require(runner_temp.is_absolute() and profile.is_absolute() and runner_temp.is_dir() and profile.is_dir(),
            "hosted capacity roots are not existing absolute directories")
    planned = workspace / "verification/sdk-foundation-execution"
    values = {"runner_temp": runner_temp, "targets": planned / "targets", "cargo_home": planned / "cargo-home",
              "gate_source": gate, "image_cargo_home": Path(environment.get("CARGO_HOME") or str(profile / ".cargo")),
              "image_rustup_home": Path(environment.get("RUSTUP_HOME") or str(profile / ".rustup"))}
    require(all(path.is_absolute() and ".." not in path.parts for path in values.values()), "capacity path is not absolute")
    return values


def capacity_anchor(path):
    """An absent planned path gets its real ancestor volume, never a fake cache."""
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts, "capacity path is not absolute")
    anchor = path
    missing = False
    while True:
        try:
            info = anchor.lstat()
            break
        except FileNotFoundError:
            missing = True
            require(anchor.parent != anchor, "capacity path has no existing ancestor")
            anchor = anchor.parent
    require(stat.S_ISDIR(info.st_mode), "capacity anchor is not a directory")
    physical = anchor.resolve(strict=True)
    return {"requested_path": str(path), "path_exists": not missing,
            "physical_anchor": str(physical), "ancestor_only": missing,
            "root_device": physical.stat().st_dev, "root_inode": physical.stat().st_ino}


def capacity_volume(physical_anchor):
    """Use the volume mount/GUID APIs and caller-available 64-bit bytes."""
    require(sys.platform == "win32", "Windows volume APIs required")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    mount = ctypes.create_unicode_buffer(32768)
    guid = ctypes.create_unicode_buffer(64)
    get_mount = kernel.GetVolumePathNameW
    get_mount.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    get_mount.restype = ctypes.c_int
    get_guid = kernel.GetVolumeNameForVolumeMountPointW
    get_guid.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    get_guid.restype = ctypes.c_int
    get_space = kernel.GetDiskFreeSpaceExW
    get_space.argtypes = [ctypes.c_wchar_p, *([ctypes.POINTER(ctypes.c_uint64)] * 3)]
    get_space.restype = ctypes.c_int
    for function, arguments, name in (
            (get_mount, (physical_anchor, mount, len(mount)), "GetVolumePathNameW"),
            (get_guid, (mount, guid, len(guid)), "GetVolumeNameForVolumeMountPointW")):
        require(bool(function(*arguments)), name + " failed: " + str(ctypes.get_last_error()))
    available, total, free = ctypes.c_uint64(), ctypes.c_uint64(), ctypes.c_uint64()
    require(bool(get_space(physical_anchor, ctypes.byref(available), ctypes.byref(total), ctypes.byref(free))),
            "GetDiskFreeSpaceExW failed: " + str(ctypes.get_last_error()))
    require(re.fullmatch(r"\\\\\?\\Volume\{[0-9a-fA-F-]{36}\}\\", guid.value)
            and mount.value.endswith("\\") and 0 <= available.value <= total.value
            and available.value <= free.value, "volume observation shape differs")
    return {"mount_path": mount.value, "volume_guid_path": guid.value.lower(),
            "caller_available_bytes": available.value, "caller_total_bytes": total.value,
            "volume_free_bytes": free.value, "api": "GetVolumePathNameW/GetVolumeNameForVolumeMountPointW/GetDiskFreeSpaceExW"}


def capacity_footprint(path, deadline_ns):
    """Bounded metadata inventory, not allocated bytes or immutable content."""
    result = {"state": "INCOMPLETE", "entries": 0, "files": 0, "logical_bytes": 0,
              "excluded_reparse_entries": 0, "metadata_sha256": None, "complete": False,
              "allocated_bytes": None, "content_hashes_measured": False,
              "atomic_namespace_snapshot": False, "inventory_paths_published": False}
    digest = hashlib.sha256()
    started = time.monotonic_ns()
    limit = min(deadline_ns, started + 5_000_000_000)
    try:
        require(time.monotonic_ns() < limit, "footprint original time budget exhausted")
        try:
            root_info = path.lstat()
        except FileNotFoundError:
            result.update(state="ABSENT_NOT_A_MEASURED_CACHE", logical_bytes=None, files=None,
                          metadata_sha256=None, complete=True)
            return result
        require(stat.S_ISDIR(root_info.st_mode) and not path.is_symlink()
                and not getattr(root_info, "st_file_attributes", 0) & 0x400, "footprint root is linked or non-directory")
        pending = [path]
        while pending:
            require(time.monotonic_ns() < limit, "footprint original time budget exhausted")
            directory = pending.pop()
            info = directory.lstat()
            require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                    and not getattr(info, "st_file_attributes", 0) & 0x400, "footprint directory changed or linked")
            with os.scandir(directory) as scan:
                entries = []
                for entry in scan:
                    require(time.monotonic_ns() < limit and result["entries"] + len(entries) < CAPACITY_MAX_ENTRIES,
                            "footprint namespace or original time budget exhausted")
                    entries.append(entry.name)
            for name in sorted(entries):
                require(time.monotonic_ns() < limit and result["entries"] < CAPACITY_MAX_ENTRIES,
                        "footprint namespace or original time budget exhausted")
                item = directory / name
                info = item.lstat()
                result["entries"] += 1
                reparse = bool(stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400)
                row = [item.relative_to(path).as_posix(), info.st_dev, info.st_ino, info.st_mode,
                       info.st_size, info.st_mtime_ns, info.st_ctime_ns, reparse]
                digest.update(json.dumps(row, ensure_ascii=True, separators=(",", ":")).encode() + b"\n")
                if reparse:
                    result["excluded_reparse_entries"] += 1
                elif stat.S_ISDIR(info.st_mode):
                    pending.append(item)
                else:
                    require(stat.S_ISREG(info.st_mode), "nonregular footprint entry")
                    result["files"] += 1
                    result["logical_bytes"] += info.st_size
                    require(result["logical_bytes"] <= CAPACITY_MAX_LOGICAL_BYTES, "footprint logical byte budget exhausted")
        result.update(state="METADATA_INVENTORY_OBSERVED", complete=result["excluded_reparse_entries"] == 0)
    except (OSError, custody.VerificationError) as error:
        result["error"] = type(error).__name__ + ": " + str(error)
    finally:
        result["elapsed_ns"] = time.monotonic_ns() - started
        if result["state"] != "ABSENT_NOT_A_MEASURED_CACHE":
            result["metadata_sha256"] = digest.hexdigest()
    return result


def capacity_worker(environment, deadline_ns):
    context = capacity_context(environment)
    require(type(deadline_ns) is int and 0 < deadline_ns - time.monotonic_ns() <= CAPACITY_SECONDS * 1_000_000_000,
            "capacity worker has no original bounded deadline")
    gate = Path(__file__).resolve().parents[1]
    paths = capacity_paths(environment, gate)
    source = custody.source_snapshot(gate, context["gate_source_sha"], GATE_FILES)
    require(all(source["files"][p]["sha256"] == digest for p, digest in GATE_PINS.items()), "capacity provider source differs")
    observations = {}
    for role in CAPACITY_ROLES:
        require(time.monotonic_ns() < deadline_ns, "capacity original deadline exhausted")
        path = paths[role]
        anchor = capacity_anchor(path)
        volume = capacity_volume(anchor["physical_anchor"])
        footprint = capacity_footprint(path, deadline_ns)
        require(capacity_anchor(path) == anchor, "capacity root changed during measurement")
        after = capacity_volume(anchor["physical_anchor"])
        require(after["volume_guid_path"] == volume["volume_guid_path"] and after["mount_path"] == volume["mount_path"],
                "capacity volume identity changed during measurement")
        observations[role] = {**anchor, "before": volume, "after": after, "footprint": footprint}
    source_unchanged(gate, source)
    require(time.monotonic_ns() < deadline_ns, "capacity original deadline exhausted")
    return {"schema": CAPACITY_SCHEMA, "context": context, "source": source, "paths": observations,
            "source_postcheck": "EXACT_GATE_SOURCE_UNCHANGED", "sdk_sources": "NOT_MATERIALIZED_BY_PROBE",
            "limits": {"entries_per_root": CAPACITY_MAX_ENTRIES, "seconds_per_root": 5,
                       "logical_bytes_per_root": CAPACITY_MAX_LOGICAL_BYTES, "record_bytes": CAPACITY_MAX_RECORD},
            "growth_reserve": {"status": "UNADMITTED_NO_COMPARABLE_BUILD_BASIS", "bytes": None,
                               "compiled_configurations": 13, "estimated_from_free_space": False},
            "observation_finished_ns": time.monotonic_ns()}


def admit_capacity(memory, value, context):
    require(type(memory) is dict and set(memory) == {"physical", "virtual", "physical_total", "virtual_total", "compilers"}
            and all(type(memory[n]) is int and 0 <= memory[n] <= 1024**5 for n in ("physical", "virtual", "physical_total", "virtual_total"))
            and memory["physical"] <= memory["physical_total"] and memory["virtual"] <= memory["virtual_total"]
            and type(memory["compilers"]) is list, "hosted memory observation is incomplete")
    for process in memory["compilers"]:
        require(type(process) is dict and set(process) == {"Id", "ProcessName"}
                and type(process["Id"]) is int and process["Id"] > 0
                and process["ProcessName"] in ("cargo", "rustc", "rustdoc"), "compiler observation shape differs")
    require(type(value) is dict and value.get("schema") == CAPACITY_SCHEMA and value.get("context") == context
            and value.get("source_postcheck") == "EXACT_GATE_SOURCE_UNCHANGED"
            and value.get("source", {}).get("sha") == context["gate_source_sha"]
            and type(value.get("paths")) is dict and set(value["paths"]) == set(CAPACITY_ROLES),
            "capacity source, context or path roster differs")
    floors = {}
    for role, item in value["paths"].items():
        require(type(item) is dict and type(item.get("path_exists")) is bool and type(item.get("ancestor_only")) is bool
                and item["path_exists"] is not item["ancestor_only"], "capacity path state differs")
        for key in ("before", "after"):
            volume = item[key]
            require(type(volume) is dict and re.fullmatch(r"\\\\\?\\volume\{[0-9a-f-]{36}\}\\", volume.get("volume_guid_path", ""))
                    and all(type(volume.get(n)) is int and volume[n] >= 0 for n in ("caller_available_bytes", "caller_total_bytes", "volume_free_bytes"))
                    and volume["caller_available_bytes"] <= volume["caller_total_bytes"]
                    and volume["caller_available_bytes"] <= volume["volume_free_bytes"], "capacity volume record differs")
        require(item["before"]["volume_guid_path"] == item["after"]["volume_guid_path"], "capacity volume changed")
        floors[role] = min(item[k]["caller_available_bytes"] for k in ("before", "after")) >= 18 * 1024**3
        require(type(item.get("footprint")) is dict and type(item["footprint"].get("complete")) is bool,
                "capacity footprint completion absent")
    complete = all(item["footprint"]["complete"] for item in value["paths"].values())
    initial = memory["physical"] >= 4 * 1024**3 and memory["virtual"] >= 8 * 1024**3 and not memory["compilers"]
    return {**value, "memory": {**memory, "provider": "Win32_OperatingSystem", "unit": "bytes",
                "physical_counter": "FreePhysicalMemory*1024", "virtual_counter": "FreeVirtualMemory*1024",
                "process_virtual_address_space_used_as_ram": False},
            "status": "CAPACITY_OBSERVED_NOT_ADMISSION" if complete else "CAPACITY_PARTIALLY_OBSERVED_NOT_ADMISSION",
            "initial_4_8_18_floor_satisfied": initial and all(floors[r] for r in ("runner_temp", "targets", "cargo_home")),
            "volume_floor_satisfied_by_role": floors, "between_commands_contract_gib": {"physical": 4, "virtual": 0, "disk": 18},
            "growth_reserve_admitted": False, "sdk_execution_authorized": False, "sdk_execution": "NOT_RUN",
            "full_process_tree_custody": False, "continuous_monitoring": False}


def observe_hosted_capacity(environment=None):
    environment = dict(os.environ) if environment is None else dict(environment)
    context = capacity_context(environment)
    budget = Budget(CAPACITY_SECONDS)
    gate = Path(__file__).resolve().parents[1]
    paths = capacity_paths(environment, gate)
    workspace = Path(environment["GITHUB_WORKSPACE"]).resolve(strict=True)
    output = workspace / "verification/sdk-foundation-hosted-admission/capacity"
    require(output.parent.is_dir() and output.parent.resolve(strict=True) == output.parent and not output.exists(),
            "capacity custody root must be fresh and canonical")
    output.mkdir(mode=0o700)
    observations = []
    report = {"schema": CAPACITY_SCHEMA, "context": context, "status": "FAIL_CAPACITY_OBSERVATION",
              "sdk_execution": "NOT_RUN", "sdk_execution_authorized": False,
              "growth_reserve_admitted": False, "original_deadline_monotonic_ns": budget.deadline,
              "budget_boundary": "COMMAND_TIMEOUTS_AND_PHASE_CHECKS_NOT_FILESYSTEM_INTERRUPTION",
              "commands": observations, "process_tree_closed": False}
    custody.write_new(output / "started.json", report)
    def call(argv, label, cap):
        return budget.call(lambda a, c, e, o, l, t, f: observer.command(a, c, e, o, l, t, f, (output, observations)),
                           argv, gate, environment, output, label, cap, context)
    try:
        shell = shutil.which("powershell", path=environment.get("PATH"))
        require(shell, "hosted capacity requires the observed Windows PowerShell")
        tool = Path(shell).resolve(strict=True)
        identity = custody.fingerprint(tool, tool.parent)
        row = call([str(tool), "-NoProfile", "-NonInteractive", "-Command", CAPACITY_MEMORY_SCRIPT],
                   "memory-and-compilers", CAPACITY_QUERY_SECONDS)
        require(type(row["exit_code"]) is int and row["exit_code"] == 0, "capacity memory query failed")
        memory_raw = observer.original_stream(output, row, "stdout")
        require(0 < len(memory_raw) <= CAPACITY_MAX_RECORD, "capacity memory record exceeded bound")
        memory = custody.strict_json(memory_raw.strip())
        require(custody.fingerprint(tool, tool.parent) == identity, "capacity PowerShell changed")
        python = Path(sys.executable).resolve(strict=True)
        python_identity = custody.fingerprint(python, python.parent)
        row = call([str(python), "-I", "-B", "-X", "utf8", str(gate / HELPER), "--mode", HOSTED_MODE,
                    "--capacity-worker", "--capacity-deadline-ns", str(budget.deadline)],
                   "volumes-and-footprints", CAPACITY_WORKER_SECONDS)
        require(type(row["exit_code"]) is int and row["exit_code"] == 0, "capacity volume/source query failed")
        raw = observer.original_stream(output, row, "stdout")
        require(0 < len(raw) <= CAPACITY_MAX_RECORD, "capacity source record exceeded bound")
        value = custody.strict_json(raw.strip())
        require(custody.fingerprint(python, python.parent) == python_identity, "capacity Python changed")
        report.update(admit_capacity(memory, value, context), powershell={"path": str(tool), "fingerprint": identity},
                      python={"path": str(python), "fingerprint": python_identity})
        require({name: row["requested_path"] for name, row in value["paths"].items()} ==
                {name: str(path) for name, path in paths.items()}, "capacity locations differ from this job")
        producer.verify_commands(output, observations)
        budget.remaining(1)
    except (observer.UnknownExecution, custody.UnknownObservation) as error:
        report.update(status="UNKNOWN_CAPACITY_NO_RETRY", error=str(error), uncertain_command=error.command)
    except BaseException as error:
        report.update(status="FAIL_CAPACITY_OBSERVATION", error=type(error).__name__ + ": " + str(error))
    report["commands"] = observations
    custody.write_new(output / "receipt.json", report)
    print(json.dumps({"status": report["status"], "receipt": str(output / "receipt.json"), "sdk_execution": "NOT_RUN"}))
    return 0 if report["status"] == "CAPACITY_OBSERVED_NOT_ADMISSION" else (124 if report["status"].startswith("UNKNOWN") else 1)


HOSTED_FULL_PURPOSE = "foundation-g1"
HOSTED_QUALIFICATION_PURPOSE = "cold-growth-qualification"
HOSTED_QUALIFICATION_CEILINGS = MappingProxyType({
    "source_transport": 1073741824, "source_checkouts": 1073741824,
    "toolchain": 8589934592, "cargo_downloads": 8589934592,
    "targets": 34359738368, "sealed_binary_originals": 268435456,
    "temporary_and_logs": 6442450944, "safety": 8589934592,
})
HOSTED_QUALIFICATION_TOTAL = 68987912192
HOSTED_QUALIFICATION_FREE = 88315265024
HOSTED_QUALIFICATION_CUSTODY_BYTES = 4 * 1024**3


def hosted_purpose(value):
    purpose = value.get("purpose", HOSTED_FULL_PURPOSE)
    require(type(purpose) is str and purpose in (HOSTED_FULL_PURPOSE, HOSTED_QUALIFICATION_PURPOSE),
            "unknown closed hosted purpose")
    return purpose


def hosted_profiles(purpose=HOSTED_FULL_PURPOSE):
    require(purpose in (HOSTED_FULL_PURPOSE, HOSTED_QUALIFICATION_PURPOSE), "unknown hosted source purpose")
    return [("fixed", FIXED)] + ([] if purpose == HOSTED_QUALIFICATION_PURPOSE else
                                  [(profile["id"], profile) for profile in causal_profiles(HOSTED_MODE)])


def qualification_producer(cargo):
    return ("driver-sdk", [cargo, "test", "--locked", "-p", "semwright-driver-sdk", "--lib",
                           "--no-run", "--message-format=json"], (TARGETS["driver-sdk"],), True)


def hosted_component_name(relative):
    """A disjoint account of owned paths; no attribution to another volume."""
    if relative.startswith("checkouts/"):
        return "source_checkouts"
    if relative.startswith("toolchain/"):
        return "toolchain"
    if relative.startswith(("execution/cargo-home/", "bootstrap/cargo-home/")):
        return "cargo_downloads"
    if relative.startswith(("execution/targets/", "bootstrap/targets/")):
        return "targets"
    if relative.startswith("execution/evidence/binaries/"):
        return "sealed_binary_originals"
    if relative.startswith("execution/evidence/sources/") or relative in (
            "input.original.json", "review.original.json", "recipient-public.original.json", "clock.original.json"):
        return "source_transport"
    return "temporary_and_logs"


def hosted_component_snapshot(root, context, deadline_ns, *, phase, initial=None, environment=None):
    """Phase-bound metadata and real-volume sample; not continuous physical quotas."""
    environment = dict(os.environ) if environment is None else dict(environment)
    require(context["mode"] == HOSTED_MODE and root.resolve(strict=True) == root
            and type(deadline_ns) is int and time.monotonic_ns() < deadline_ns,
            "hosted component observation lacks its original finite deadline")
    caps = context["cold_growth"]["bytes"]
    purpose = hosted_purpose(context)
    if purpose == HOSTED_QUALIFICATION_PURPOSE:
        require(caps == dict(HOSTED_QUALIFICATION_CEILINGS) and sum(caps.values()) == HOSTED_QUALIFICATION_TOTAL,
                "qualification operating ceilings differ from the closed source design")
    started = time.monotonic_ns()
    limit = min(deadline_ns, started + 30 * 1_000_000_000)
    work_anchor = capacity_anchor(root)
    work = capacity_volume(work_anchor["physical_anchor"])
    image = capacity_volume(capacity_anchor(Path(environment["USERPROFILE"]))["physical_anchor"])
    require(work["volume_guid_path"] != image["volume_guid_path"]
            and PureWindowsPath(work["mount_path"]).drive.upper() == "D:",
            "SDK work must not be directed to the runner image volume")
    require(image["caller_available_bytes"] >= 18 * 1024**3, "runner image protected floor breached")
    minimum = 18 * 1024**3 + sum(caps.values()) if initial is None else 18 * 1024**3 + caps["safety"]
    if phase != "custody": minimum += 0 if initial is None else HOSTED_QUALIFICATION_CUSTODY_BYTES
    require(work["caller_available_bytes"] >= minimum, "hosted work volume lacks its original operating reserve")
    component_bytes = {name: 0 for name in caps if name != "safety"}
    component_files = {name: 0 for name in component_bytes}
    digest = hashlib.sha256()
    pending, entries = [root], 0
    while pending:
        require(time.monotonic_ns() < limit, "hosted component metadata observation exhausted its phase bound")
        directory = pending.pop()
        info = directory.lstat()
        require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                and not getattr(info, "st_file_attributes", 0) & 0x400,
                "owned component namespace became linked or non-directory")
        names = []
        with os.scandir(directory) as scan:
            for entry in scan:
                require(entries + len(names) < CAPACITY_MAX_ENTRIES and time.monotonic_ns() < limit,
                        "hosted component namespace exceeds its bounded inventory")
                names.append(entry.name)
        for name in sorted(names):
            path = directory / name
            info = path.lstat(); entries += 1
            require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
                    "owned component is linked or a reparse point")
            relative = path.relative_to(root).as_posix()
            digest.update(transport_json([relative, info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns]))
            if stat.S_ISDIR(info.st_mode): pending.append(path)
            else:
                require(stat.S_ISREG(info.st_mode), "owned component contains a nonregular member")
                component = hosted_component_name(relative)
                component_bytes[component] += info.st_size
                component_files[component] += 1
    require(capacity_anchor(root) == work_anchor, "hosted work location changed during measurement")
    after = capacity_volume(work_anchor["physical_anchor"])
    require(after["volume_guid_path"] == work["volume_guid_path"] and after["caller_available_bytes"] >= minimum,
            "hosted work volume or protected reserve changed during measurement")
    for name, size in component_bytes.items():
        ceiling = caps[name]
        if name == "temporary_and_logs" and phase != "custody":
            ceiling -= HOSTED_QUALIFICATION_CUSTODY_BYTES
        require(ceiling > 0 and size <= ceiling, "hosted component operating ceiling exceeded: " + name)
    if initial is not None:
        require(initial["work_volume"]["volume_guid_path"] == work["volume_guid_path"]
                and initial["context"] == context, "hosted resource origin changed")
        spent = max(0, initial["work_volume"]["caller_available_bytes"] - after["caller_available_bytes"])
        require(spent <= sum(caps.values()) - caps["safety"]
                - (0 if phase == "custody" else HOSTED_QUALIFICATION_CUSTODY_BYTES),
                "hosted observed volume delta consumed protected custody or safety reserve")
    else:
        spent = 0
    finished = time.monotonic_ns()
    require(finished < limit, "hosted component observation finished after its original phase bound")
    return {"schema": "motionwright.sdk-foundation-owned-footprint/1", "context": context, "phase": phase,
            "observed_monotonic_ns": finished, "original_deadline_monotonic_ns": deadline_ns,
            "work_volume": after, "image_volume": image, "namespace_entries": entries,
            "component_logical_bytes": component_bytes, "component_file_counts": component_files,
            "metadata_sha256": digest.hexdigest(), "selected_operating_ceilings_bytes": caps,
            "observed_shared_volume_delta_bytes": spent, "remaining_operating_bytes": sum(caps.values()) - spent,
            "custody_bytes_reserved": HOSTED_QUALIFICATION_CUSTODY_BYTES, "safety_bytes_reserved": caps["safety"],
            "ceilings_are_measured_growth": False, "component_physical_quotas": False,
            "continuous_peak_observed": False, "namespace_snapshot_atomic": False,
            "exclusive_allocation_attribution": False, "full_foundation_growth_admitted": False}


def execute_qualification(states, tools, evidence, budget, call, report):
    """One fixed Cargo producer; deliberately never opens libtest observation."""
    require(len(states) == 1 and states[0]["id"] == "fixed" and states[0].get("causal") is None
            and hosted_purpose(report["context"]) == HOSTED_QUALIFICATION_PURPOSE,
            "qualification cannot select another source, causal or arbitrary subset")
    state = states[0]
    report["phase"] = "qualification-sdk-g0"
    state["g0"] = {}; report["sdk_g0"]["fixed"] = state["g0"]
    sdk_g0(state["repo"], state["source"], tools, state["env"], state["logs"], state["context"], call, state["g0"])
    barrier = producer.ProducerBarrier(call, tools["cargo"]["absolute_path"])
    label, argv, specs, testing = qualification_producer(tools["cargo"]["absolute_path"])
    report["phase"] = "qualification-single-producer"
    row = barrier(argv, state["repo"], state["env"], state["logs"], label + "-compile", 900, state["context"])
    raw = observer.original_stream(state["logs"], row, "stdout")
    observer.original_stream(state["logs"], row, "stderr")
    compiled_paths(raw, row["exit_code"], state["repo"], state["target"], specs, testing, argv)
    prepared = {"collections": {label: {"command": row, "specs": specs, "testing": testing}}, "steps": [label]}
    barrier.begin_sealing()
    source_unchanged(state["repo"], state["source"]); verify_tools(tools)
    binaries = producer.seal_compiled_role(prepared, state["repo"], state["target"], state["logs"], evidence, barrier)
    require(set(binaries) == {label} and set(binaries[label]) == {TARGETS["driver-sdk"]["name"]},
            "qualification compiled executable roster differs")
    state["binaries"] = binaries; report["binaries"]["fixed"] = binaries
    compiled = {"schema": "motionwright.sdk-foundation-compiled-output/1", "purpose": HOSTED_QUALIFICATION_PURPOSE,
                "source_sha": FIXED["sha"], "source_tree": FIXED["tree"], "targets": binaries,
                "compile_command": row, "listed": False, "test_methods_executed": 0,
                "causal_methods_executed": 0, "foundation_g1_credit": "NONE"}
    custody.write_new(state["logs"] / "compiled-rosters.json", compiled)
    report["rosters"]["fixed"] = compiled
    report["producer_barrier"] = "ONE_SELECTED_PRODUCER_SEALED_NO_OBSERVATION"
    require(barrier.phase == "SEALING" and not report["groups"] and not report["causals"],
            "qualification entered a list, test or causal phase")
    producer.verify_binaries(binaries[label], state["target"])
    source_unchanged(state["repo"], state["source"]); verify_tools(tools); budget.remaining(1)
    report["source_postcheck"] = "ALL_EXACT_SOURCES_UNCHANGED"
    report["qualification"] = {"compile_commands": 1, "executables": 1, "binary_lists": 0,
                               "test_methods": 0, "causal_methods": 0, "full_foundation_g1": "NOT_RUN",
                               "full_thirteen_configuration_growth": "NOT_ADMITTED"}


def qualification_measurement(value, context, *, expected_phase, original_clock, origin=None):
    require(type(expected_phase) is str and expected_phase in ("initial", "work", "after-producer", "custody")
            and type(original_clock) is dict
            and original_clock == hosted_clock(context, original_clock.get("original_started_monotonic_ns"),
                                               original_clock.get("original_started_monotonic_ns")),
            "qualification resource readback lacks its expected phase and original clock")
    custody_phase = expected_phase == "custody"
    expected_deadline = original_clock["original_deadline_monotonic_ns" if custody_phase else "work_deadline_monotonic_ns"]
    require(type(value) is dict and value.get("schema") == "motionwright.sdk-foundation-owned-footprint/1"
            and value.get("context") == context
            and value.get("selected_operating_ceilings_bytes") == dict(HOSTED_QUALIFICATION_CEILINGS)
            and value.get("ceilings_are_measured_growth") is False
            and value.get("component_physical_quotas") is False and value.get("continuous_peak_observed") is False
            and value.get("full_foundation_growth_admitted") is False,
            "qualification measured resource original is absent, foreign or promoted")
    expected = set(HOSTED_QUALIFICATION_CEILINGS) - {"safety"}
    require(type(value.get("component_logical_bytes")) is dict and set(value["component_logical_bytes"]) == expected
            and type(value.get("component_file_counts")) is dict and set(value["component_file_counts"]) == expected
            and all(type(number) is int and number >= 0 for number in (*value["component_logical_bytes"].values(),
                                                                       *value["component_file_counts"].values()))
            and value.get("custody_bytes_reserved") == HOSTED_QUALIFICATION_CUSTODY_BYTES
            and value.get("safety_bytes_reserved") == HOSTED_QUALIFICATION_CEILINGS["safety"],
            "qualification component measurement roster or type differs")
    require(value.get("phase") == expected_phase
            and type(value.get("observed_monotonic_ns")) is int and type(value.get("original_deadline_monotonic_ns")) is int
            and value["original_deadline_monotonic_ns"] == expected_deadline
            and original_clock["original_started_monotonic_ns"] <= value["observed_monotonic_ns"] < expected_deadline,
            "qualification component measurement was late, relabelled or renewed its deadline")
    for name, size in value["component_logical_bytes"].items():
        maximum = HOSTED_QUALIFICATION_CEILINGS[name] - (
            HOSTED_QUALIFICATION_CUSTODY_BYTES if name == "temporary_and_logs" and not custody_phase else 0)
        require(size <= maximum, "qualification retained component exceeds its closed operating ceiling")
    work, image = value.get("work_volume"), value.get("image_volume")
    require(type(work) is dict and type(image) is dict
            and work.get("volume_guid_path") != image.get("volume_guid_path")
            and type(work.get("caller_available_bytes")) is int and type(image.get("caller_available_bytes")) is int
            and PureWindowsPath(work.get("mount_path", "")).drive.upper() == "D:"
            and image["caller_available_bytes"] >= 18 * 1024**3
            and work["caller_available_bytes"] >= 18 * 1024**3 + HOSTED_QUALIFICATION_CEILINGS["safety"]
                 + (0 if custody_phase else HOSTED_QUALIFICATION_CUSTODY_BYTES),
            "qualification work/image volume floor or placement differs")
    if expected_phase == "initial":
        require(origin is None, "qualification initial observation cannot replace another origin")
        require(work["caller_available_bytes"] >= HOSTED_QUALIFICATION_FREE, "qualification initial chosen reserve was absent")
        spent = 0
    else:
        qualification_measurement(origin, context, expected_phase="initial", original_clock=original_clock)
        require(work["volume_guid_path"] == origin["work_volume"]["volume_guid_path"]
                and image["volume_guid_path"] == origin["image_volume"]["volume_guid_path"]
                and value["observed_monotonic_ns"] >= origin["observed_monotonic_ns"],
                "qualification measurement changed its original volume or predates the original sample")
        spent = max(0, origin["work_volume"]["caller_available_bytes"] - work["caller_available_bytes"])
    require(type(value.get("observed_shared_volume_delta_bytes")) is int
            and value["observed_shared_volume_delta_bytes"] == spent
            and type(value.get("remaining_operating_bytes")) is int
            and value["remaining_operating_bytes"] == HOSTED_QUALIFICATION_TOTAL - spent
            and spent <= HOSTED_QUALIFICATION_TOTAL - HOSTED_QUALIFICATION_CEILINGS["safety"]
                - (0 if custody_phase else HOSTED_QUALIFICATION_CUSTODY_BYTES),
            "qualification volume delta or remaining original reserve differs")
    return value

# The transport only repackages already retained originals. It grants no SDK
# authority, and the hosted execution blocker remains a separate closed gate.
HOSTED_TRANSPORT_SCHEMA = "motionwright.sdk-foundation-private-transport/1"
HOSTED_TRANSPORT_REVIEW_SCHEMA = "motionwright.sdk-foundation-private-transport-review/1"
HOSTED_TRANSPORT_RECIPIENT_SHA256 = "c3204ef97fb30495e1a40f373fe75da47eb0e0cb155fbe3539e4cdf0b6b3811a"
HOSTED_TRANSPORT_SPKI_SHA256 = "1ca6d673bd8c5ea4d0378310b25c254452c19504d7e784ab94b8a8d2d1115921"
HOSTED_TRANSPORT_REQUIREMENTS = "tooling/requirements/foundation-windows-custody.txt"
HOSTED_TRANSPORT_REQUIREMENTS_SHA256 = "82b7c9a4741d26411cdd6a0732cebeffd081cfad5f51ef4a75585aa7d38f162f"
HOSTED_PYTHON_DEPENDENCIES = (("cryptography", "50.0.2"), ("cffi", "2.1.1"), ("pycparser", "2.23"),
    ("jsonschema", "4.26.0"), ("PyYAML", "6.0.3"), ("attrs", "26.1.0"),
    ("jsonschema-specifications", "2025.9.1"), ("referencing", "0.37.0"), ("rpds-py", "2026.9.1"))
HOSTED_IMAGE_PREREQUISITE_SOURCES = MappingProxyType({
    "image_profile": "win25-vs2026/20261021.278.1",
    "readme_sha256": "319d1d4e0b8ae7f60d0db2f8b1a94ea6be68711cca854bf7ae8c0f770a89aac7",
    "mingw_install_source_sha256": "d5f6cd2b566ed77e67b6c0c0f2fb0254d0d4609678375a2bdce08be98d6ab817",
    "node_install_source_sha256": "02d0551024ff8cb04feb900232dc035ac0e7495b1a38ae238a313b53a25d8bae",
})
HOSTED_TRANSPORT_PINS = MappingProxyType({
    "tooling/mlt_official_runtime_intake.py": "a5c8843db38e48060f34e73afdcaf59341cac0ee88ddd6e13cbf85dadbfb770f",
    "tooling/ci_private_evidence.py": "2671eea354f672353a6b4ea1ac9c43de4b682e96f4dcc9146104a5f8d20a8eee",
    HOSTED_TRANSPORT_REQUIREMENTS: HOSTED_TRANSPORT_REQUIREMENTS_SHA256,
})
HOSTED_TRANSPORT_CIPHER_MAX = 256 * 1024**2
HOSTED_TRANSPORT_TOTAL_MAX = 512 * 1024**2
HOSTED_TRANSPORT_FILE_COUNT = 4096
HOSTED_TRANSPORT_INDEX_MAX = 512 * 1024
HOSTED_TRANSPORT_OBSERVATION_PATH_MAX = 320  # JSON-encoded bytes excluding quotes.
# Keep the existing maximum TAR/header overhead inside the ciphertext carrier.
# The actual resulting TAR and envelope are still measured before emission.
HOSTED_TRANSPORT_PAYLOAD_MAX = HOSTED_TRANSPORT_CIPHER_MAX - 4096 * 4096 - 8192 - 64
HOSTED_TRANSPORT_DATA_PARTS = 15  # At most one intact binary per part; ordinary originals may share it.
HOSTED_TRANSPORT_RESERVE = 1800
# Within the original7200s, not an additional clock. A later composed producer
# must stop work with this entire reserve intact. The current blocker stays shut.
HOSTED_TRANSPORT_PHASES = (("seal-data", 300), ("data-uploads", 1080),
                           ("seal-index", 180), ("index-upload", 60),
                           ("descriptor", 120), ("cleanup", 60))
HOSTED_TRANSPORT_UPLOAD_ACTION = "ea165f8d65b6e75b540449e92b4886f43607fa02"
HOSTED_TRANSPORT_ROLES = (
    "run-started", "run-final-or-original-failure", "controller-original",
    "command-original", "sdk-g0", "compiled-roster", "source-original", "compiled-binary",
)


def transport_json(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                       allow_nan=False) + "\n").encode("ascii")


def hosted_transport_recipe(purpose=HOSTED_FULL_PURPOSE):
    profiles = hosted_profiles(purpose)
    configurations = []
    for role, profile in profiles:
        plans = ((qualification_producer("<observed-cargo>"),) if purpose == HOSTED_QUALIFICATION_PURPOSE else
                 producer_plan("windows-g1", "<observed-cargo>", None if role == "fixed" else profile))
        for collection, _argv, specs, testing in plans:
            configurations.extend({"role": role, "collection": collection, "target": spec["name"],
                                   "source": spec["source"], "testing": testing} for spec in specs)
    require(len(configurations) == (1 if purpose == HOSTED_QUALIFICATION_PURPOSE else 13),
            "transport compiled configuration roster differs")
    return {"schema": HOSTED_TRANSPORT_SCHEMA, "purpose": purpose,
            "sources": [{"role": role, "sha": p["sha"], "tree": p["tree"], "listing_sha256": p["listing"]}
                        for role, p in profiles],
            "compiled_configurations": configurations, "original_roles": list(HOSTED_TRANSPORT_ROLES),
            "recipient_public_sha256": HOSTED_TRANSPORT_RECIPIENT_SHA256,
            "recipient_spki_sha256": HOSTED_TRANSPORT_SPKI_SHA256,
            "source_pins": dict(HOSTED_TRANSPORT_PINS),
            "python_dependencies": dict(HOSTED_PYTHON_DEPENDENCIES),
            "image_prerequisite_sources": dict(HOSTED_IMAGE_PREREQUISITE_SOURCES),
            "declarative_image_sources_are_actual_tool_observations": False,
            "limits": {"ciphertext_bytes": HOSTED_TRANSPORT_CIPHER_MAX,
                       "selected_total_bytes": HOSTED_TRANSPORT_TOTAL_MAX,
                       "physical_files": HOSTED_TRANSPORT_FILE_COUNT,
                       "source_file_bytes": 16 * 1024**2, "index_bytes": HOSTED_TRANSPORT_INDEX_MAX,
                       "snapshot_manifest_bytes": HOSTED_TRANSPORT_INDEX_MAX,
                       "observation_path_encoded_bytes": HOSTED_TRANSPORT_OBSERVATION_PATH_MAX,
                       "part_payload_bytes": HOSTED_TRANSPORT_PAYLOAD_MAX,
                       "data_parts": HOSTED_TRANSPORT_DATA_PARTS,
                       "original_total_seconds": 7200,
                       "required_custody_reserve_seconds": HOSTED_TRANSPORT_RESERVE,
                       "maximum_work_before_reserve_seconds": 5400,
                       "custody_phases_seconds": dict(HOSTED_TRANSPORT_PHASES)},
            "sdk_execution_authorized": False, "whole_build_growth": "UNADMITTED_NO_COMPARABLE_BASIS",
            **({"qualification_operating_ceilings_bytes": dict(HOSTED_QUALIFICATION_CEILINGS),
                "chosen_ceilings_are_measured_growth": False, "test_or_list_execution_selected": False}
               if purpose == HOSTED_QUALIFICATION_PURPOSE else {}),
            "upload_confirmed": False, "foundation_result_admitted": False}


def transport_relative_path(value):
    require(type(value) is str and 0 < len(value) <= 512 and "\\" not in value
            and not value.startswith("/") and ":" not in value
            and all(part not in ("", ".", "..") for part in value.split("/"))
            and all(32 <= ord(c) < 127 for c in value), "transport member path is unsafe")
    return value


def transport_role_path(path, role):
    profiles = {"fixed", *(p["id"] for p in CAUSALS)}
    if role in ("source-original", "compiled-binary"):
        prefix = "sources/" if role == "source-original" else "binaries/"
        return path.startswith(prefix) and DIGEST.fullmatch(path[len(prefix):]) is not None
    if role == "run-started":
        return path == "started.json"
    if role == "run-final-or-original-failure":
        return path in ("receipt.json", "controller/original-failure.json")
    if role == "controller-original":
        return path in ("controller/stdout.original", "controller/stderr.original", "controller/exit.original.json",
                        "controller/worker.stdout.original", "controller/worker.stderr.original", "controller/worker.exit.original.json",
                        "controller/input.original.json", "controller/clock.original.json",
                        "controller/bootstrap.original.json", "controller/terminal.original.json")
    pieces = path.split("/")
    if len(pieces) != 2 or pieces[0] not in {"commands", *profiles}:
        return False
    if role == "sdk-g0":
        return pieces[0] in profiles and pieces[1] == "sdk-g0.json"
    if role == "compiled-roster":
        return pieces[0] in profiles and pieces[1] == "compiled-rosters.json"
    return role == "command-original" and (re.fullmatch(
        r"[a-z][a-z0-9_+.-]{0,127}\.(stdout\.log|stderr\.log|command\.json)", pieces[1]) is not None
        or pieces[0] == "commands" and re.fullmatch(
            r"(?:bootstrap-)?(?:initial-resources|pre-command-[1-9][0-9]*)\.json", pieces[1]) is not None)


def transport_rows(rows):
    require(type(rows) is list and 0 < len(rows) <= HOSTED_TRANSPORT_FILE_COUNT,
            "transport physical file roster is absent or exceeds its existing bound")
    paths, result, total = set(), [], 0
    for row in rows:
        require(type(row) is dict and set(row) == {"path", "role", "state", "bytes", "sha256"},
                "transport original row is not closed")
        path = transport_relative_path(row["path"])
        require(type(row["role"]) is str and row["role"] in HOSTED_TRANSPORT_ROLES
                and transport_role_path(path, row["role"]), "transport role or namespace differs")
        require(path.casefold() not in paths, "transport originals alias or repeat a path")
        paths.add(path.casefold())
        require(row["state"] in ("PRESENT", "MISSING", "UNKNOWN"), "transport original state differs")
        if row["state"] == "PRESENT":
            limit = 16 * 1024**2 if row["role"] == "source-original" else HOSTED_TRANSPORT_PAYLOAD_MAX
            require(type(row["bytes"]) is int and 0 <= row["bytes"] <= limit
                    and type(row["sha256"]) is str and DIGEST.fullmatch(row["sha256"]),
                    "transport original size/hash differs; no implicit fragmentation")
            if row["role"] in ("source-original", "compiled-binary"):
                require(path.rsplit("/", 1)[1] == row["sha256"], "retained content-address differs")
            total += row["bytes"]
            require(total <= HOSTED_TRANSPORT_TOTAL_MAX, "transport total exceeds the unchanged snapshot bound")
        else:
            require(row["bytes"] is None and row["sha256"] is None,
                    "unobserved original cannot claim bytes or a digest")
        result.append(dict(row))
    return sorted(result, key=lambda row: (HOSTED_TRANSPORT_ROLES.index(row["role"]), row["path"]))


def transport_manifest_bound(file_count):
    """Bound the actual2671 manifest row, including its three physical maps."""
    require(type(file_count) is int and 0 <= file_count <= HOSTED_TRANSPORT_FILE_COUNT,
            "transport snapshot manifest row count is invalid")
    physical = {"device": 2**64 - 1, "inode": 2**128 - 1, "mode": 0o177777,
                "uid": 2**32 - 1, "gid": 2**32 - 1, "size": HOSTED_TRANSPORT_CIPHER_MAX,
                "mtime_ns": -(2**63), "ctime_ns": -(2**63)}
    row = {"archive_path": "files/originals/" + "f" * 64,
           "observation": {"path": "x" * HOSTED_TRANSPORT_OBSERVATION_PATH_MAX,
               "sha256": "f" * 64, "size": HOSTED_TRANSPORT_CIPHER_MAX,
               "before": physical, "after": physical, "namespace_after": physical}}
    # The newline accounts for the separating comma. Root/error metadata has a
    # separate allowance; any larger actual failure text is rejected before encryption.
    return 4096 + file_count * len(transport_json(row))


def transport_manifest_path(path):
    value = str(path)
    require(len(json.dumps(value, ensure_ascii=True).encode("ascii")) - 2 <= HOSTED_TRANSPORT_OBSERVATION_PATH_MAX,
            "transport physical observation path exceeds its encoded manifest budget")


def transport_manifest_identity(info):
    limits = (("st_dev", 0, 2**64 - 1), ("st_ino", 0, 2**128 - 1),
              ("st_mode", 0, 0o177777), ("st_uid", 0, 2**32 - 1), ("st_gid", 0, 2**32 - 1),
              ("st_size", 0, HOSTED_TRANSPORT_CIPHER_MAX),
              ("st_mtime_ns", -(2**63), 2**63 - 1), ("st_ctime_ns", -(2**63), 2**63 - 1))
    require(stat.S_ISREG(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400
            and all(type(getattr(info, key)) is int and minimum <= getattr(info, key) <= maximum
                    for key, minimum, maximum in limits),
            "transport physical metadata cannot fit the selected snapshot manifest schema")


def transport_snapshot_manifest(raw, byte_limit):
    """Inspect the actual existing TAR before encryption, without another capture."""
    import io
    import tarfile
    require(type(raw) is bytes and 0 < len(raw) <= HOSTED_TRANSPORT_CIPHER_MAX
            and type(byte_limit) is int and 0 < byte_limit <= HOSTED_TRANSPORT_INDEX_MAX,
            "transport snapshot or manifest budget is invalid")
    metadata, count = None, 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for member in archive:
            count += 1
            require(count <= HOSTED_TRANSPORT_FILE_COUNT + 1, "transport snapshot member budget exceeded")
            if member.name != "snapshot-manifest.json": continue
            require(metadata is None and member.isfile() and 0 < member.size <= byte_limit,
                    "transport actual snapshot manifest exceeds its unchanged bounded plan")
            stream = archive.extractfile(member)
            require(stream is not None, "transport snapshot manifest is unreadable")
            data = stream.read(member.size + 1)
            require(len(data) == member.size, "transport snapshot manifest size differs")
            metadata = custody.strict_json(data)
    require(type(metadata) is dict and metadata.get("schema") == "motionwright.ci.private-snapshot/1"
            and type(metadata.get("selected_originals_complete")) is bool
            and metadata.get("complete_owner_state_claimed") is False,
            "transport actual snapshot manifest is absent or has another contract")
    return metadata


def transport_partition_plan(rows, purpose=HOSTED_FULL_PURPOSE):
    rows = transport_rows(rows)
    allowed = {role for role, _ in hosted_profiles(purpose)}
    require(all(row["path"].split("/", 1)[0] not in {profile["id"] for profile in CAUSALS} - allowed for row in rows),
            "transport includes a source role outside its exact purpose")
    binaries = [r for r in rows if r["state"] == "PRESENT" and r["role"] == "compiled-binary"]
    require(len(binaries) <= (1 if purpose == HOSTED_QUALIFICATION_PURPOSE else 13),
            "transport has more retained binaries than selected configurations")
    ordinary = [r for r in rows if r["state"] == "PRESENT" and r["role"] != "compiled-binary"]
    bins = [{"payload_bytes": row["bytes"], "entries": [row]} for row in binaries]
    # First-fit decreasing is deterministic; no second capture is used to find
    # a partition that fits. Reject an unrepresentable plan before filesystem IO.
    for row in sorted(ordinary, key=lambda r: (-r["bytes"], r["path"])):
        slot = next((p for p in bins if p["payload_bytes"] + row["bytes"] <= HOSTED_TRANSPORT_PAYLOAD_MAX
                     and transport_manifest_bound(len(p["entries"]) + 1) <= HOSTED_TRANSPORT_INDEX_MAX), None)
        if slot is None:
            require(len(bins) < HOSTED_TRANSPORT_DATA_PARTS, "transport data partition budget exhausted")
            slot = {"payload_bytes": 0, "entries": []}; bins.append(slot)
        slot["entries"].append(row); slot["payload_bytes"] += row["bytes"]
    groups = bins
    require(groups and len(groups) <= HOSTED_TRANSPORT_DATA_PARTS, "transport has no bounded data partition")
    parts = [{"id": "part-" + str(i).zfill(2), "paths": sorted(r["path"] for r in p["entries"]),
              "payload_bytes": p["payload_bytes"],
              "snapshot_manifest_bytes_bound": transport_manifest_bound(len(p["entries"]))}
             for i, p in enumerate(groups, 1)]
    planned = [path for part in parts for path in part["paths"]]
    require(len(planned) == len(set(planned)) and set(planned) == {r["path"] for r in rows if r["state"] == "PRESENT"},
            "transport partition omitted, duplicated or invented an original")
    value = {"schema": HOSTED_TRANSPORT_SCHEMA, "purpose": purpose,
             "recipe_sha256": hashlib.sha256(transport_json(hosted_transport_recipe(purpose))).hexdigest(),
             "entries": rows, "parts": parts, "missing_or_unknown": [r["path"] for r in rows if r["state"] != "PRESENT"],
             "original_roster_completeness": "NOT_INFERRED_FROM_PRESENT_FILES", "foundation_result_admitted": False}
    require(len(transport_json(transport_pack_roster(value))) <= HOSTED_TRANSPORT_INDEX_MAX - 64 * 1024,
            "transport complete file roster exceeds the closed private-index budget")
    return value


def transport_pack_roster(plan):
    assignments = {path: number for number, part in enumerate(plan["parts"], 1) for path in part["paths"]}
    result = []
    for row in plan["entries"]:
        role = HOSTED_TRANSPORT_ROLES.index(row["role"])
        content_addressed = row["role"] in ("source-original", "compiled-binary")
        name = row["path"].split("/", 1)[1] if content_addressed else row["path"]
        result.append([role, name, row["state"], row["bytes"],
                       None if content_addressed else row["sha256"], assignments.get(row["path"])])
    return result


def transport_unpack_roster(packed, purpose=HOSTED_FULL_PURPOSE):
    require(type(packed) is list and 0 < len(packed) <= HOSTED_TRANSPORT_FILE_COUNT,
            "transport packed file roster exceeds its bound")
    rows, numbers = [], []
    for item in packed:
        require(type(item) is list and len(item) == 6 and type(item[0]) is int
                and 0 <= item[0] < len(HOSTED_TRANSPORT_ROLES), "transport packed row is not closed")
        role = HOSTED_TRANSPORT_ROLES[item[0]]
        addressed = role in ("source-original", "compiled-binary")
        if addressed:
            require(type(item[1]) is str and DIGEST.fullmatch(item[1]) and item[4] is None,
                    "transport packed content address differs")
        path = (("sources/" if role == "source-original" else "binaries/") + item[1]) if addressed else item[1]
        rows.append({"path": path, "role": role, "state": item[2], "bytes": item[3],
                     "sha256": (item[1] if item[2] == "PRESENT" else None) if addressed else item[4]})
        require(item[5] is None or type(item[5]) is int and 1 <= item[5] <= HOSTED_TRANSPORT_DATA_PARTS,
                "transport packed part number differs")
        numbers.append(item[5])
    plan = transport_partition_plan(rows, purpose)
    require(transport_pack_roster(plan) == packed, "transport packed roster is reordered, omitted or assigned to another part")
    return plan


def transport_review(raw, context):
    selected = authority(context["authority_id"], HOSTED_MODE, FIXED["sha"])
    purpose = hosted_purpose(context)
    binding = hosted_binding(selected["binding"], purpose)
    require(type(raw) is bytes and 0 < len(raw) <= HOSTED_TRANSPORT_INDEX_MAX
            and hashlib.sha256(raw).hexdigest() == binding["private_transport"]["review_sha256"],
            "transport review is not the exact catalogue-bound original")
    value = custody.strict_json(raw)
    expected = {"schema": HOSTED_TRANSPORT_REVIEW_SCHEMA, "purpose": purpose, "gate_base_sha": binding["gate_base_sha"],
                "recipe_sha256": hashlib.sha256(transport_json(hosted_transport_recipe(purpose))).hexdigest(),
                "recipient_public_sha256": HOSTED_TRANSPORT_RECIPIENT_SHA256,
                "recipient_spki_sha256": HOSTED_TRANSPORT_SPKI_SHA256,
                "cold_growth_basis_sha256": binding["cold_growth"]["basis_sha256"]}
    require(value == expected, "transport review, source recipe, growth or fixed recipient differs")
    require(context["mode"] == HOSTED_MODE and context["product_source_sha"] == FIXED["sha"]
            and context["private_transport_review_sha256"] == hashlib.sha256(raw).hexdigest(),
            "transport context is not the selected hosted review")
    return expected


def transport_aad(context):
    github = context["github"]
    require(context["mode"] == HOSTED_MODE and context["product_source_sha"] == FIXED["sha"]
            and github["GITHUB_SHA"] == github["GITHUB_WORKFLOW_SHA"]
            and SHA.fullmatch(github["GITHUB_SHA"]) and github["GITHUB_RUN_ATTEMPT"] == "1"
            and re.fullmatch(r"[1-9][0-9]*", github["GITHUB_RUN_ID"]), "transport actual run context differs")
    return {"source_sha": github["GITHUB_SHA"], "semwright_sha": FIXED["sha"],
            "run_id": github["GITHUB_RUN_ID"], "run_attempt": github["GITHUB_RUN_ATTEMPT"]}


def transport_recipient(raw):
    require(type(raw) is bytes and len(raw) == 663 and hashlib.sha256(raw).hexdigest() == HOSTED_TRANSPORT_RECIPIENT_SHA256,
            "transport recipient bytes differ from the reviewed public original")
    value = custody.strict_json(raw)
    require(set(value) == {"sha256", "spki_der_base64"} and value["sha256"] == HOSTED_TRANSPORT_SPKI_SHA256,
            "transport recipient SPKI differs")
    return value


def transport_primitives(gate):
    import importlib.metadata
    import types
    require(sys.platform == "win32" and sys.version_info[:2] == (3, 13),
            "transport dependency wheels require observed Windows CPython3.13")
    for name, version in HOSTED_PYTHON_DEPENDENCIES:
        require(importlib.metadata.version(name) == version, "transport installed dependency version differs")
    sources = {}
    for name, expected in HOSTED_TRANSPORT_PINS.items():
        path = gate / name
        stamp = custody.fingerprint(path, gate, 2 * 1024**2)
        require(stamp["sha256"] == expected, "transport shared source or requirements changed")
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == expected, "transport source changed during exact load")
        sources[name] = raw
    modules = {}
    for name in ("mlt_official_runtime_intake", "ci_private_evidence"):
        key = "tooling/" + name + ".py"
        require(name not in sys.modules, "transport shared module was preloaded outside this exact load")
        module = types.ModuleType(name); module.__file__ = str(gate / key)
        sys.modules[name] = module; modules[name] = module
        try:
            exec(compile(sources[key], module.__file__, "exec"), module.__dict__)
        except BaseException:
            for label, owned in modules.items():
                if sys.modules.get(label) is owned: del sys.modules[label]
            raise
    return modules["ci_private_evidence"]


def transport_descriptor(value, context):
    keys = {"artifact_id", "run_id", "run_attempt", "source_sha", "zip_sha256", "zip_bytes"}
    require(type(value) is dict and set(value) == keys
            and all(type(value[k]) is int and value[k] > 0 for k in ("artifact_id", "run_id", "run_attempt", "zip_bytes"))
            and value["run_id"] == int(context["github"]["GITHUB_RUN_ID"])
            and value["run_attempt"] == 1 and value["source_sha"] == context["github"]["GITHUB_SHA"]
            and type(value["zip_sha256"]) is str and DIGEST.fullmatch(value["zip_sha256"])
            and value["zip_bytes"] <= HOSTED_TRANSPORT_CIPHER_MAX,
            "transport artifact descriptor is foreign, unbounded or incomplete")
    return dict(value)


def transport_artifact_readback(descriptor, context, run, job, artifact, archive):
    """Admit original API/ZIP bytes, never uploader success as producer proof."""
    import io
    import zipfile
    value = transport_descriptor(descriptor, context)
    github = context["github"]
    require(type(run) is dict and type(run.get("id")) is int and type(run.get("run_attempt")) is int
            and run.get("id") == value["run_id"] and run.get("run_attempt") == 1
            and run.get("head_sha") == value["source_sha"]
            and run.get("repository", {}).get("full_name") == github["GITHUB_REPOSITORY"]
            and run.get("path") == WORKFLOW and run.get("event") == "workflow_dispatch",
            "transport actual run provenance differs")
    require(type(job) is dict and type(job.get("id")) is int and job["id"] > 0
            and type(job.get("run_id")) is int and job.get("run_id") == value["run_id"]
            and job.get("head_sha") == value["source_sha"] and job.get("name") == HOSTED_SCOPE,
            "transport actual producer job differs")
    require(type(artifact) is dict and type(artifact.get("id")) is int
            and artifact.get("id") == value["artifact_id"] and artifact.get("expired") is False
            and type(artifact.get("size_in_bytes")) is int and artifact["size_in_bytes"] == value["zip_bytes"]
            and artifact.get("digest") == "sha256:" + value["zip_sha256"]
            and type(artifact.get("workflow_run")) is dict and type(artifact["workflow_run"].get("id")) is int
            and artifact["workflow_run"]["id"] == value["run_id"]
            and artifact["workflow_run"].get("head_sha") == value["source_sha"], "transport actual artifact differs")
    require(type(archive) is bytes and len(archive) == value["zip_bytes"]
            and hashlib.sha256(archive).hexdigest() == value["zip_sha256"], "transport actual ZIP differs")
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        entries = zipped.infolist()
        require(len(entries) == 1 and not entries[0].is_dir()
                and re.fullmatch(r"(part-[0-9]{2}|private-index)\.bin", entries[0].filename)
                and stat.S_IFMT(entries[0].external_attr >> 16) in (0, stat.S_IFREG)
                and 0 < entries[0].file_size <= HOSTED_TRANSPORT_CIPHER_MAX
                and zipped.testzip() is None, "transport ZIP must contain one closed ciphertext member")
        raw = zipped.read(entries[0])
    return {"descriptor": value, "member": entries[0].filename, "ciphertext_bytes": len(raw),
            "ciphertext_sha256": hashlib.sha256(raw).hexdigest(), "ciphertext": raw,
            "producer_result_admitted": False}


def transport_clock(started, context, phase, now_ns=None):
    """Check the inherited clock; this function never starts or renews one."""
    now = time.monotonic_ns() if now_ns is None else now_ns
    require(type(started) is dict and started.get("schema") == SCHEMA
            and started.get("context") == context
            and type(started.get("original_started_monotonic_ns")) is int
            and type(started.get("original_deadline_monotonic_ns")) is int
            and type(started.get("budget_seconds")) is int
            and started["budget_seconds"] == context["max_total_seconds"]
            and HOSTED_TRANSPORT_RESERVE <= started["budget_seconds"] <= 7200,
            "transport has no original producer clock or exact source context")
    first, end = started["original_started_monotonic_ns"], started["original_deadline_monotonic_ns"]
    require(type(now) is int and 0 < first <= now < end
            and end == first + started["budget_seconds"] * 1_000_000_000,
            "transport original deadline is absent, late or renewed")
    names = [name for name, _ in HOSTED_TRANSPORT_PHASES]
    require(type(phase) is str and phase in names, "unknown original custody phase")
    remaining = sum(n for _, n in HOSTED_TRANSPORT_PHASES[names.index(phase):])
    require(end - now >= remaining * 1_000_000_000,
            "transport remaining original reserve is insufficient before this phase")
    return {"original_started_monotonic_ns": first, "original_deadline_monotonic_ns": end,
            "phase": phase, "observed_monotonic_ns": now,
            "phase_cap_seconds": dict(HOSTED_TRANSPORT_PHASES)[phase],
            "phase_deadline_monotonic_ns": min(end - (remaining - dict(HOSTED_TRANSPORT_PHASES)[phase]) * 1_000_000_000,
                                                now + dict(HOSTED_TRANSPORT_PHASES)[phase] * 1_000_000_000),
            "remaining_reserved_seconds": remaining,
            "hard_filesystem_watchdog": False}


def transport_resume_phase(started, context, phase, observed, now_ns=None):
    """Read the first phase clock; later upload guards cannot renew its cap."""
    now = time.monotonic_ns() if now_ns is None else now_ns
    require(type(observed) is dict and type(observed.get("observed_monotonic_ns")) is int,
            "transport phase has no original entry observation")
    checked = transport_clock(started, context, phase, observed["observed_monotonic_ns"])
    require(observed == checked and type(now) is int
            and checked["observed_monotonic_ns"] <= now < checked["phase_deadline_monotonic_ns"],
            "transport phase clock is foreign, future, late or renewed")
    return checked


def transport_original(path, root, limit=HOSTED_TRANSPORT_INDEX_MAX):
    before = custody.fingerprint(path, root, limit)
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit and len(raw) == before["bytes"]
            and hashlib.sha256(raw).hexdigest() == before["sha256"]
            and custody.fingerprint(path, root, limit) == before, "transport original changed during readback")
    return raw, before


def transport_inventory(evidence, purpose=HOSTED_FULL_PURPOSE):
    """Only the existing evidence namespace, never a target/cache/live checkout."""
    require(evidence.is_dir() and evidence.resolve(strict=True) == evidence,
            "transport evidence root is absent or noncanonical")
    rows, seen = [], set()
    def namespace():
        result = []
        for path in evidence.rglob("*"):
            require(len(result) < 16384, "transport evidence namespace exceeds existing bound")
            result.append(path)
        return sorted(result)
    initial = namespace()
    for path in initial:
        info = path.lstat()
        require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
                "transport evidence namespace contains a link or reparse point")
        if stat.S_ISDIR(info.st_mode):
            continue
        require(stat.S_ISREG(info.st_mode), "transport refuses nonregular evidence")
        name = path.relative_to(evidence).as_posix()
        roles = [role for role in HOSTED_TRANSPORT_ROLES if transport_role_path(name, role)]
        require(len(roles) == 1, "transport refuses an unselected evidence file")
        limit = 16 * 1024**2 if roles[0] == "source-original" else HOSTED_TRANSPORT_PAYLOAD_MAX
        stamp = custody.fingerprint(path, evidence, limit)
        rows.append({"path": name, "role": roles[0], "state": "PRESENT",
                     "bytes": stamp["bytes"], "sha256": stamp["sha256"]})
        seen.add(name)
        require(len(rows) <= HOSTED_TRANSPORT_FILE_COUNT, "transport physical file budget exceeded")
    required = [("started.json", "run-started"),
                *(('controller/' + name, "controller-original") for name in
                  ("stdout.original", "stderr.original", "exit.original.json")),
                *((role + "/" + name, kind) for role, _ in hosted_profiles(purpose)
                  for name, kind in (("sdk-g0.json", "sdk-g0"), ("compiled-rosters.json", "compiled-roster")))]
    if not {"receipt.json", "controller/original-failure.json"} & seen:
        required.append(("receipt.json", "run-final-or-original-failure"))
    for path, role in required:
        if path not in seen:
            rows.append({"path": path, "role": role, "state": "MISSING", "bytes": None, "sha256": None})
    require(initial == namespace(), "transport evidence namespace changed")
    return transport_rows(rows)


def transport_seal_parts(evidence, private_root, outgoing, rows, context, recipient, crypto, check_budget):
    """One snapshot and encryption per closed part of already retained files."""
    plan = transport_partition_plan(rows, hosted_purpose(context))
    check_budget()
    require(private_root.is_dir() and outgoing.is_dir()
            and private_root.resolve(strict=True) == private_root
            and outgoing.resolve(strict=True) == outgoing
            and not any(outgoing.iterdir()), "transport outputs must be fresh canonical owned directories")
    require(all(not left.is_relative_to(right) and not right.is_relative_to(left)
                for left, right in ((evidence, private_root), (evidence, outgoing), (private_root, outgoing))),
            "transport staging, outgoing and retained originals must be disjoint")
    crypto.public_key(recipient)
    require(recipient["sha256"] == HOSTED_TRANSPORT_SPKI_SHA256, "transport recipient differs")
    lookup = {row["path"]: row for row in plan["entries"]}
    crypto.new_json(private_root / "partition-plan.json", plan)
    result = {"schema": HOSTED_TRANSPORT_SCHEMA, "context": context,
              "plan_sha256": hashlib.sha256(transport_json(plan)).hexdigest(),
              "recipe_sha256": plan["recipe_sha256"], "parts": [],
              "missing_or_unknown": plan["missing_or_unknown"],
              "upload_confirmed": False, "foundation_result_admitted": False,
              "selected_snapshot_completeness_is_foundation_completeness": False}
    try:
        for part in plan["parts"]:
            check_budget()
            record = {"id": part["id"], "status": "INTENT", "paths": part["paths"], "staged": [], "copy_errors": []}
            result["parts"].append(record)
            directory = private_root / part["id"]
            directory.mkdir(mode=0o700)
            crypto.new_json(directory / "intent.json", {"part": part, "automatic_retry": False})
            payload = directory / "payload"; payload.mkdir(mode=0o700)
            # Copy through the already reviewed stable-file custodian. This is
            # not a hard link, retry of a capture, or observation of live state.
            for path in part["paths"]:
                check_budget()
                row = lookup[path]
                transport_manifest_path(payload / "originals" / row["sha256"])
                try:
                    stamp = observer.retain_file(evidence / path, evidence, payload, "originals", row)
                except BaseException as error:
                    record["copy_errors"].append({"path": path, "error_type": type(error).__name__,
                                                  "error": str(error), "retry": False})
                else:
                    transport_manifest_identity((payload / stamp["path"]).lstat())
                    record["staged"].append({"path": path, "archive_path": "files/" + stamp["path"],
                                             "bytes": stamp["bytes"], "sha256": stamp["sha256"]})
            check_budget()
            raw, snapshot_complete = crypto.gather_snapshot({"files": payload}, preserve_partial=True)
            check_budget()
            require(len(raw) + 8192 + 64 <= HOSTED_TRANSPORT_CIPHER_MAX,
                    "transport actual TAR cannot fit its unchanged ciphertext carrier")
            metadata = transport_snapshot_manifest(raw, part["snapshot_manifest_bytes_bound"])
            require(metadata["selected_originals_complete"] is snapshot_complete,
                    "transport actual manifest and capture result disagree")
            encrypted = crypto.encrypt_snapshot(raw, recipient, transport_aad(context))
            check_budget()
            require(type(encrypted) is bytes and 0 < len(encrypted) <= HOSTED_TRANSPORT_CIPHER_MAX,
                    "transport actual ciphertext exceeds carrier; no fragmentation or retry")
            crypto.new_bytes(outgoing / (part["id"] + ".bin"), encrypted)
            check_budget()
            record.update(status="SEALED_PRESENT_SNAPSHOT" if snapshot_complete and not record["copy_errors"] else "SEALED_PARTIAL_SNAPSHOT",
                          snapshot_complete=snapshot_complete, plaintext_bytes=len(raw),
                          plaintext_sha256=hashlib.sha256(raw).hexdigest(),
                          ciphertext_bytes=len(encrypted), ciphertext_sha256=hashlib.sha256(encrypted).hexdigest())
            crypto.new_json(directory / "terminal.json", record)
        result["status"] = ("SEALED_PRESENT_ORIGINALS" if not result["missing_or_unknown"]
                            and all(p["snapshot_complete"] and not p["copy_errors"] for p in result["parts"])
                            else "SEALED_INCOMPLETE_ORIGINALS_NO_RESULT_ADMISSION")
    except BaseException as error:
        result.update(status="UNKNOWN_SEAL_CUSTODY_NO_RETRY", error_type=type(error).__name__, error=str(error),
                      originals_retained_in_place=True)
        raise
    finally:
        # This one-shot terminal never overwrites a failed original or repairs a
        # partial ciphertext. A second write is not attempted if it is uncertain.
        crypto.new_json(private_root / "parts.original.json", result)
    return plan, result


def transport_index(plan, sealed, uploaded, context, post_seal_measurement=None):
    require(plan == transport_partition_plan(plan["entries"], hosted_purpose(context)), "transport partition plan was changed")
    require(sealed.get("schema") == HOSTED_TRANSPORT_SCHEMA and sealed.get("context") == context
            and sealed.get("plan_sha256") == hashlib.sha256(transport_json(plan)).hexdigest()
            and sealed.get("recipe_sha256") == plan["recipe_sha256"], "transport seal context or plan differs")
    expected = [part["id"] for part in plan["parts"]]
    require(type(sealed.get("parts")) is list and [part.get("id") for part in sealed["parts"]] == expected
            and type(uploaded) is dict and set(uploaded) == set(expected),
            "transport missing, extra or reordered data parts")
    descriptors, identities, digests = [], set(), set()
    for part, record in zip(plan["parts"], sealed["parts"]):
        value = uploaded[part["id"]]
        require(type(value) is dict and set(value) == {"descriptor", "member", "ciphertext_bytes", "ciphertext_sha256"},
                "transport uploaded part original is incomplete")
        descriptor = transport_descriptor(value["descriptor"], context)
        require(descriptor["artifact_id"] not in identities and descriptor["zip_sha256"] not in digests,
                "transport reused a data artifact")
        identities.add(descriptor["artifact_id"]); digests.add(descriptor["zip_sha256"])
        require(value["member"] == part["id"] + ".bin" and record["paths"] == part["paths"]
                and record.get("status") in ("SEALED_PRESENT_SNAPSHOT", "SEALED_PARTIAL_SNAPSHOT")
                and type(value["ciphertext_bytes"]) is int and value["ciphertext_bytes"] == record["ciphertext_bytes"]
                and value["ciphertext_sha256"] == record["ciphertext_sha256"],
                "transport data part changed after sealing or uploader observation")
        failed = [row["path"] for row in record["copy_errors"]]
        require(len(failed) == len(set(failed)) and set(failed) <= set(part["paths"])
                and [row["path"] for row in record["staged"]] == [path for path in part["paths"] if path not in failed],
                "transport failed-copy roster is ambiguous")
        descriptors.append({"id": part["id"], **value, "snapshot_complete": record["snapshot_complete"],
                            "plaintext_bytes": record["plaintext_bytes"], "plaintext_sha256": record["plaintext_sha256"],
                            "copy_failed_rows": [i for i, row in enumerate(plan["entries"]) if row["path"] in failed]})
    value = {"schema": HOSTED_TRANSPORT_SCHEMA, "kind": "private-index", "context": context,
             "recipe": hosted_transport_recipe(hosted_purpose(context)), "packed_roster": transport_pack_roster(plan), "data_parts": descriptors,
             "recipient_public_sha256": HOSTED_TRANSPORT_RECIPIENT_SHA256,
             "recipient_spki_sha256": HOSTED_TRANSPORT_SPKI_SHA256,
             "post_seal_measurement": post_seal_measurement,
             "source_result_admitted": False, "uploader_claim_is_producer_provenance": False,
             "index_own_descriptor": "EXTERNAL_ONLY", "automatic_retry": False}
    require(len(transport_json(value)) <= HOSTED_TRANSPORT_INDEX_MAX, "private index exceeds unchanged JSON bound")
    return value


def transport_index_records(value):
    require(type(value) is dict and set(value) == {"schema", "kind", "context", "recipe", "packed_roster", "data_parts",
                "recipient_public_sha256", "recipient_spki_sha256", "source_result_admitted",
                "uploader_claim_is_producer_provenance", "index_own_descriptor", "automatic_retry", "post_seal_measurement"}
            and value["schema"] == HOSTED_TRANSPORT_SCHEMA and value["kind"] == "private-index"
            and value["recipe"] == hosted_transport_recipe(hosted_purpose(value["context"]))
            and value["recipient_public_sha256"] == HOSTED_TRANSPORT_RECIPIENT_SHA256
            and value["recipient_spki_sha256"] == HOSTED_TRANSPORT_SPKI_SHA256
            and value["source_result_admitted"] is False and value["automatic_retry"] is False
            and value["uploader_claim_is_producer_provenance"] is False
            and value["index_own_descriptor"] == "EXTERNAL_ONLY", "private index contract differs")
    plan = transport_unpack_roster(value["packed_roster"], hosted_purpose(value["context"]))
    require(type(value["data_parts"]) is list
            and [row.get("id") for row in value["data_parts"]] == [part["id"] for part in plan["parts"]],
            "private index data part roster differs")
    records, ids, digests = [], set(), set()
    for part, row in zip(plan["parts"], value["data_parts"]):
        require(type(row) is dict and set(row) == {"id", "descriptor", "member", "ciphertext_bytes", "ciphertext_sha256",
                    "snapshot_complete", "plaintext_bytes", "plaintext_sha256", "copy_failed_rows"}
                and row["member"] == row["id"] + ".bin"
                and type(row["snapshot_complete"]) is bool
                and all(type(row[k]) is int and 0 < row[k] <= HOSTED_TRANSPORT_CIPHER_MAX
                        for k in ("ciphertext_bytes", "plaintext_bytes"))
                and all(type(row[k]) is str and DIGEST.fullmatch(row[k])
                        for k in ("ciphertext_sha256", "plaintext_sha256")), "private index part shape differs")
        descriptor = transport_descriptor(row["descriptor"], value["context"])
        require(descriptor["artifact_id"] not in ids and descriptor["zip_sha256"] not in digests,
                "private index reuses a data artifact")
        ids.add(descriptor["artifact_id"]); digests.add(descriptor["zip_sha256"])
        failed = row["copy_failed_rows"]
        require(type(failed) is list and all(type(i) is int and 0 <= i < len(plan["entries"]) for i in failed)
                and len(failed) == len(set(failed)), "private index copy-error rows differ")
        failed_paths = [plan["entries"][i]["path"] for i in failed]
        require(set(failed_paths) <= set(part["paths"]), "private index failed-copy path belongs to another part")
        lookup = {entry["path"]: entry for entry in plan["entries"]}
        staged = [{"path": path, "archive_path": "files/originals/" + lookup[path]["sha256"],
                   "bytes": lookup[path]["bytes"], "sha256": lookup[path]["sha256"]}
                  for path in part["paths"] if path not in failed_paths]
        records.append({**row, "staged": staged, "copy_errors": [{"path": path} for path in failed_paths]})
    return plan, records


def transport_seal_index(plan, sealed, uploaded, context, recipient, crypto, outgoing, post_seal_measurement=None):
    value = transport_index(plan, sealed, uploaded, context, post_seal_measurement)
    encrypted = crypto.encrypt_snapshot(transport_json(value), recipient, transport_aad(context))
    require(0 < len(encrypted) <= HOSTED_TRANSPORT_CIPHER_MAX, "private index ciphertext exceeds carrier")
    crypto.new_bytes(outgoing / "private-index.bin", encrypted)
    return {"member": "private-index.bin", "ciphertext_bytes": len(encrypted),
            "ciphertext_sha256": hashlib.sha256(encrypted).hexdigest(),
            "upload_confirmed": False, "foundation_result_admitted": False}


def transport_snapshot_readback(raw, record, plan):
    """Validate a decrypted existing snapshot without extraction or native IO."""
    import io
    import tarfile
    require(type(raw) is bytes and len(raw) == record["plaintext_bytes"]
            and hashlib.sha256(raw).hexdigest() == record["plaintext_sha256"], "transport plaintext differs")
    part = next(p for p in plan["parts"] if p["id"] == record["id"])
    transport_snapshot_manifest(raw, part["snapshot_manifest_bytes_bound"])
    staged = [row["path"] for row in record["staged"]]
    failed = [row["path"] for row in record["copy_errors"]]
    require(len(staged + failed) == len(set(staged + failed))
            and set(staged + failed) == set(part["paths"]), "transport staged logical roster differs")
    lookup = {row["path"]: row for row in plan["entries"]}
    members = {}
    for row in record["staged"]:
        require(row["archive_path"] == "files/originals/" + lookup[row["path"]]["sha256"]
                and row["sha256"] == lookup[row["path"]]["sha256"]
                and row["bytes"] == lookup[row["path"]]["bytes"], "transport staged original identity differs")
        members[row["archive_path"]] = row
    observed, contents = set(), {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        listing = archive.getmembers()
        require(len(listing) <= HOSTED_TRANSPORT_FILE_COUNT + 1
                and len({p.name for p in listing}) == len(listing)
                and all(p.isfile() and p.name in {*members, "snapshot-manifest.json"} for p in listing),
                "transport snapshot has an extra, repeated or nonregular member")
        for member in listing:
            stream = archive.extractfile(member)
            require(stream is not None, "transport snapshot member cannot be read")
            if member.name == "snapshot-manifest.json":
                require(member.size <= part["snapshot_manifest_bytes_bound"] <= HOSTED_TRANSPORT_INDEX_MAX,
                        "transport snapshot metadata is unbounded")
                metadata = custody.strict_json(stream.read(member.size + 1))
                require(metadata.get("schema") == "motionwright.ci.private-snapshot/1"
                        and type(metadata.get("selected_originals_complete")) is bool
                        and metadata["selected_originals_complete"] is record["snapshot_complete"]
                        and metadata.get("complete_owner_state_claimed") is False,
                        "transport snapshot completeness was promoted")
                observed.add(member.name)
            else:
                row = members[member.name]
                require(member.size == row["bytes"], "transport member size differs")
                data = stream.read(member.size + 1)
                require(len(data) == member.size and hashlib.sha256(data).hexdigest() == row["sha256"],
                        "transport member digest differs")
                for logical in record["staged"]:
                    if logical["archive_path"] == member.name: contents[logical["path"]] = data
                observed.add(member.name)
    require("snapshot-manifest.json" in observed and (not record["snapshot_complete"] or observed == {*members, "snapshot-manifest.json"}),
            "transport snapshot omitted a claimed complete original")
    return {"part": record["id"], "observed_originals": len(observed) - 1,
            "planned_physical_originals": len(members), "snapshot_complete": record["snapshot_complete"],
            "foundation_result_admitted": False, "originals": contents}


def transport_foundation_readback(files, context, source_listings):
    """Recheck existing oracles from originals; never launch or repair a test."""
    def raw(path):
        value = files.get(path)
        require(type(value) is bytes, "required retained Foundation original is missing: " + path)
        return value
    def obj(path):
        value = raw(path)
        require(0 < len(value) <= 16 * 1024**2, "Foundation original JSON exceeds source bound")
        return custody.strict_json(value)
    def command(folder, row):
        require(type(row) is dict and row.get("observation") == "DIRECT_PROCESS_EXIT_OBSERVED"
                and row.get("log_custody") == "VERIFIED_SNAPSHOT" and type(row.get("exit_code")) is int,
                "Foundation original command is unknown or lacks its terminal")
        streams = []
        for channel in ("stdout", "stderr"):
            expected = row[channel]
            name = transport_relative_path(expected["path"])
            require("/" not in name and name.endswith("." + channel + ".log"), "Foundation stream locator differs")
            data = raw(folder + "/" + name)
            require(type(expected.get("bytes")) is int and len(data) == expected["bytes"]
                    and hashlib.sha256(data).hexdigest() == expected["sha256"], "Foundation original stream differs")
            streams.append(data)
        label = row["stdout"]["path"][:-len(".stdout.log")]
        require(obj(folder + "/" + label + ".command.json") == row, "Foundation command original differs from report")
        return streams[0] + b"\n" + streams[1], streams[0]
    started, report = obj("started.json"), obj("receipt.json")
    qualified = hosted_purpose(context) == HOSTED_QUALIFICATION_PURPOSE
    expected_context = {**context, "gate_source_sha": context["github"]["GITHUB_SHA"]}
    require(started.get("schema") == report.get("schema") == SCHEMA
            and started.get("context") == context and report.get("context") == expected_context
            and report.get("status") == ("OBSERVED_ONE_PRODUCER_COLD_GROWTH_QUALIFICATION" if qualified else
                                         "PASS_HOSTED_WINDOWS_G1_SELECTED_ROOT_OBSERVATIONS")
            and report.get("source_postcheck") == "ALL_EXACT_SOURCES_UNCHANGED"
            and report.get("producer_barrier") == ("ONE_SELECTED_PRODUCER_SEALED_NO_OBSERVATION" if qualified else
                                                   "ALL_SELECTED_PRODUCERS_SEALED")
            and report.get("full_process_tree_custody") is False,
            "Foundation terminal is incomplete or does not belong to the expected original run")
    original_clock = obj("controller/clock.original.json")
    require(original_clock == hosted_clock(context, original_clock.get("original_started_monotonic_ns"),
                                          original_clock.get("original_started_monotonic_ns"))
            and all(started.get(key) == original_clock[key] and report.get(key) == original_clock[key]
                    for key in ("original_started_monotonic_ns", "original_deadline_monotonic_ns",
                                "work_deadline_monotonic_ns", "budget_seconds"))
            and type(report.get("finished_monotonic_ns")) is int
            and original_clock["original_started_monotonic_ns"] <= report["finished_monotonic_ns"]
                < original_clock["work_deadline_monotonic_ns"],
            "Foundation terminal renewed or exceeded the original work/custody clock")
    observed_commands = report.get("commands")
    require(report.get("hosted_image_tools") == hosted_image_tool_roots(report["tools"]),
            "Foundation original observed image tool roots differ")
    require(type(observed_commands) is list and 0 < len(observed_commands) <= HOSTED_TRANSPORT_FILE_COUNT,
            "Foundation observed command inventory is absent or unbounded")
    original_command_paths = set()
    for observation in observed_commands:
        require(type(observation) is dict and set(observation) == {"folder", "command", "receipt"},
                "Foundation command observation shape differs")
        folder = transport_relative_path(observation["folder"])
        command(folder, observation["command"])
        path = folder + "/" + observation["command"]["stdout"]["path"][:-len(".stdout.log")] + ".command.json"
        require(path not in original_command_paths, "Foundation command original is duplicated")
        original_command_paths.add(path)
        retained = observation["receipt"]
        require(type(retained.get("bytes")) is int and retained["bytes"] == len(raw(path))
                and retained.get("sha256") == hashlib.sha256(raw(path)).hexdigest(),
                "Foundation command original fingerprint differs")
    profiles = dict(hosted_profiles(hosted_purpose(context)))
    require(type(source_listings) is dict and set(source_listings) == set(profiles)
            and all(set(report.get(key, {})) == set(profiles) for key in ("sources", "sdk_g0", "binaries", "rosters")),
            "Foundation five-source roster is missing or foreign")
    binary_rows = {}
    for role, profile in profiles.items():
        source = report["sources"][role]["snapshot"]
        listing = source_listings[role]
        require(type(listing) is bytes and hashlib.sha256(listing).hexdigest() == profile["listing"]
                and source["sha"] == profile["sha"] and source["tree"] == profile["tree"],
                "Foundation source is not the independently pinned Git listing")
        listed = {}
        for line in listing.split(b"\0"):
            if not line: continue
            metadata, name = line.split(b"\t", 1)
            mode, kind, oid = metadata.split()
            path = transport_relative_path(name.decode("utf-8"))
            require(mode in (b"100644", b"100755") and kind == b"blob" and path not in listed,
                    "Foundation source listing is not a unique regular-blob roster")
            listed[path] = oid.decode("ascii")
        require(set(listed) == set(source["files"]), "Foundation retained source roster omitted or added an input")
        for path, oid in listed.items():
            entry = source["files"][path]
            data = raw("sources/" + entry["sha256"])
            require(len(data) == entry["bytes"] and hashlib.sha256(data).hexdigest() == entry["sha256"]
                    and entry["git_blob_oid"] == oid
                    and hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == oid,
                    "Foundation retained source blob differs from Git")
        require(all(source["files"][path]["sha256"] == digest for path, digest in profile["pins"].items()),
                "Foundation source policy or product pin differs")
        frame = {**expected_context, "executed_source_sha": profile["sha"],
                 "executed_tree_sha": profile["tree"], "role": role}
        g0 = obj(role + "/sdk-g0.json")
        require(g0 == report["sdk_g0"][role] and g0.get("status") == "PASS_G0_SDK_FOCAL"
                and g0.get("executed_source_sha") == profile["sha"] and g0.get("executed_tree_sha") == profile["tree"]
                and g0.get("github") == context["github"] and g0.get("rust_compiled") is False
                and g0.get("remaining_sdk_ci") == {key: "NOT_RUN" for key in G0_REMAINING}
                and len(g0.get("steps", [])) == 4, "Foundation SDK-owned focal G0 differs")
        python = report["tools"]["python"]["absolute_path"]
        expected_g0 = (
            [python, "-I", "-B", "-X", "utf8", "-c", "import sys,tomllib,jsonschema,yaml; assert sys.version_info >= (3,11); print('SDK_G0_PREREQUISITES_AVAILABLE')"],
            [python, "-I", "-B", "-X", "utf8", str(Path(source["checkout"]) / "scripts/verify-oss-hygiene.py")],
            [python, "-I", "-B", "-X", "utf8", str(Path(source["checkout"]) / "scripts/verify-source.py")],
            [report["tools"]["cargo-fmt"]["absolute_path"], "fmt", "--all", "--", "--check"])
        for number, (row, expected_argv) in enumerate(zip(g0["steps"], expected_g0)):
            combined, stdout = command(role, row)
            require(row["exit_code"] == 0 and row["context"] == frame and row["argv"] == expected_argv,
                    "Foundation SDK G0 contains a foreign or unsuccessful command")
            if number == 2:
                normalized = stdout.replace(b"\r\n", b"\n")
                marker = normalized.rfind(b'{\n  "source_validation"')
                require(marker >= 0 and custody.strict_json(normalized[marker:].strip()) == g0["source_report"],
                        "Foundation SDK source result differs from its original stdout")
        require(g0.get("source_report", {}).get("source_validation") == "PASS"
                and g0["source_report"].get("rust_compiled") is False,
                "Foundation SDK source report did not complete")
        rosters = obj(role + "/compiled-rosters.json")
        require(rosters == report["rosters"][role], "Foundation original compiled rosters differ")
        configurations = ((qualification_producer(report["tools"]["cargo"]["absolute_path"]),) if qualified else
                          producer_plan("windows-g1", report["tools"]["cargo"]["absolute_path"], None if role == "fixed" else profile))
        require(set(report["binaries"][role]) == {x[0] for x in configurations}, "Foundation compiled collection roster differs")
        expected_lists = set()
        for collection, compile_argv, specs, testing in configurations:
            binaries = report["binaries"][role][collection]
            require(set(binaries) == {spec["name"] for spec in specs}, "Foundation compiled target roster differs")
            compile_row = obj(role + "/" + collection + "-compile.command.json")
            combined, compile_stdout = command(role, compile_row)
            require(compile_row["context"] == frame and compile_row["argv"] == compile_argv,
                    "Foundation Cargo producer source or argv differs")
            roots = []
            for binary in binaries.values():
                relative = transport_relative_path(binary["path"])
                root = Path(binary["absolute_path"])
                for _ in relative.split("/"): root = root.parent
                require(root / relative == Path(binary["absolute_path"]), "Foundation target relative path differs")
                roots.append(root)
            require(roots and len(set(roots)) == 1, "Foundation compiled target roots are mixed")
            compiled = compiled_paths(compile_stdout, compile_row["exit_code"], Path(source["checkout"]),
                                      roots[0], specs, testing, compile_argv)
            require(compiled == {name: binary["absolute_path"] for name, binary in binaries.items()},
                    "Foundation sealed executable does not match original Cargo JSON")
            for name, binary in binaries.items():
                data = raw("binaries/" + binary["sha256"])
                require(len(data) == binary["bytes"] and hashlib.sha256(data).hexdigest() == binary["sha256"],
                        "Foundation retained executable differs")
                binary_rows[(role, collection, name)] = binary
                if qualified:
                    require(rosters.get("schema") == "motionwright.sdk-foundation-compiled-output/1"
                            and rosters.get("purpose") == HOSTED_QUALIFICATION_PURPOSE
                            and rosters.get("source_sha") == FIXED["sha"] and rosters.get("source_tree") == FIXED["tree"]
                            and rosters.get("targets") == report["binaries"][role]
                            and rosters.get("compile_command") == compile_row and rosters.get("listed") is False
                            and type(rosters.get("test_methods_executed")) is int and rosters["test_methods_executed"] == 0
                            and type(rosters.get("causal_methods_executed")) is int and rosters["causal_methods_executed"] == 0
                            and rosters.get("foundation_g1_credit") == "NONE", "qualification compiled-output receipt differs")
                    continue
                key = collection + "/" + name; expected_lists.add(key)
                row = rosters[key]["command"]
                combined, stdout = command(role, row)
                require(row["context"] == frame and row["argv"] == [binary["absolute_path"], "--list", "--format", "terse"]
                        and tuple(rosters[key]["names"]) == tuple(parse_roster(stdout, row["exit_code"])),
                        "Foundation actual binary list or source context differs")
        if not qualified:
            require(set(rosters) == expected_lists, "Foundation lists omitted or added a compiled configuration")
    if qualified:
        require(len(binary_rows) == 1 and report.get("groups") == [] and report.get("causals") == []
                and report.get("qualification") == {"compile_commands": 1, "executables": 1, "binary_lists": 0,
                    "test_methods": 0, "causal_methods": 0, "full_foundation_g1": "NOT_RUN",
                    "full_thirteen_configuration_growth": "NOT_ADMITTED"},
                "qualification claims a list, test, causal or another configuration")
        cargo_commands = [item["command"] for item in report["commands"]
                          if item["command"]["argv"][0] == report["tools"]["cargo"]["absolute_path"]]
        product_commands = [row for row in cargo_commands if row["argv"][1:] != ["--version"]]
        require(len(product_commands) == 1 and product_commands[0]["argv"] == qualification_producer(
                report["tools"]["cargo"]["absolute_path"])[1]
                and all("--list" not in item["command"]["argv"] and "--exact" not in item["command"]["argv"]
                        for item in report["commands"]), "qualification observed another producer or libtest command")
        origin = qualification_measurement(report["hosted_component_origin"], context,
                                          expected_phase="initial", original_clock=original_clock)
        samples = report.get("hosted_component_samples")
        require(type(samples) is list and 0 < len(samples) <= HOSTED_TRANSPORT_FILE_COUNT,
                "qualification work samples are absent or unbounded")
        last_observed = origin["observed_monotonic_ns"]
        for phase, sample in [("work", sample) for sample in samples] + [("after-producer", report["hosted_component_final"])]:
            qualification_measurement(sample, context, expected_phase=phase, original_clock=original_clock, origin=origin)
            require(last_observed <= sample["observed_monotonic_ns"] <= report["finished_monotonic_ns"],
                    "qualification resource samples are reordered or later than the original terminal")
            last_observed = sample["observed_monotonic_ns"]
        return {"status": "RETAINED_ONE_PRODUCER_QUALIFICATION_RECHECKED", "compile_commands": 1,
                "executables": 1, "binary_lists": 0, "test_methods": 0, "full_foundation_g1": "NOT_RUN",
                "full_thirteen_configuration_growth": "NOT_ADMITTED", "human_acceptance": False}
    require(len(binary_rows) == 13 and len(report.get("groups", [])) == 13 and len(report.get("causals", [])) == 4,
            "Foundation selected executable/oracle closure is incomplete")
    fixed_frame = {**expected_context, "executed_source_sha": FIXED["sha"], "executed_tree_sha": FIXED["tree"], "role": "fixed"}
    ids = []
    for spec, result in zip(GROUPS, report["groups"]):
        collection, target = spec[:2]; name = TARGETS[target]["name"]
        binary = binary_rows[("fixed", collection, name)]
        listed = group_roster(report["rosters"]["fixed"][collection + "/" + name]["names"], spec)
        combined, _stdout = command("fixed", result["command"])
        observed = admit_group(combined, result["command"], binary, binary, listed, group_argv(binary, spec), fixed_frame)
        require(result["ids"] == observed["ids"] and result["compiled_roster"] == {
                    key: list(value) if isinstance(value, tuple) else value for key, value in listed.items()},
                "Foundation selected fixed IDs differ from their original streams")
        ids.extend(observed["ids"])
    for profile, result in zip(causal_profiles(HOSTED_MODE), report["causals"]):
        role = profile["id"]; name = TARGETS[profile["target"]]["name"]
        binary = binary_rows[(role, role, name)]
        names = report["rosters"][role][role + "/" + name]["names"]
        frame = {**expected_context, "executed_source_sha": profile["sha"], "executed_tree_sha": profile["tree"], "role": role}
        pair = {"id": role, "baseline": {"targets": {profile["target"]: [profile["test"]]}},
                "repair": {"targets": {}}, "failures": {profile["test"]: profile["failure"]}}
        combined, _stdout = command(role, result["command"])
        observed = observer.admit_case(pair, "baseline", profile["test"], combined, result["command"], binary, binary,
                                       {"all": names, "selected": (profile["test"],)}, frame)
        require(observed == result, "Foundation original causal assertion receipt differs")
    require(len(ids) == 134, "Foundation actual fixed IDs do not total the selected roster")
    return {"status": "RETAINED_SOURCE_AND_SELECTED_ORACLES_RECHECKED", "fixed_observations": len(ids),
            "original_causal_assertions": 4, "compiled_configurations": len(binary_rows),
            "full_sdk_ci": "NOT_ADMITTED", "descendants": "NOT_OBSERVED", "human_acceptance": False}


def transport_cipher_header(raw, context, crypto):
    require(type(raw) is bytes and 0 < len(raw) <= HOSTED_TRANSPORT_CIPHER_MAX
            and raw.startswith(crypto.MAGIC), "transport ciphertext magic or size differs")
    offset = len(crypto.MAGIC)
    require(len(raw) > offset + 4, "transport ciphertext has no complete header")
    size = int.from_bytes(raw[offset:offset + 4], "big")
    require(0 < size <= 8192 and offset + 4 + size + 16 < len(raw), "transport AAD exceeds original bound")
    encoded = raw[offset + 4:offset + 4 + size]
    header = crypto.strict(encoded)
    require(crypto.json_bytes(header) == encoded and header.get("context") == transport_aad(context)
            and header.get("recipient_spki_sha256") == HOSTED_TRANSPORT_SPKI_SHA256,
            "transport encrypted context or exact recipient differs")
    return header


def transport_independent_readback(gate, context, review_raw, index_descriptor,
                                  fetch_originals, crypto, private_key, source_listings, budget):
    """Offline consumer: original Git/API/ZIP plus the existing decryption API.

    fetch_originals receives only the already bound descriptor and must retain
    original run, job, artifact and ZIP bytes. It cannot provide authority by a
    boolean; every relationship is rechecked here. The private key never enters
    the producing workflow or an upload argument.
    """
    transport_review(review_raw, context)
    selected = authority(context["authority_id"], HOSTED_MODE, FIXED["sha"])
    provenance = verify_hosted_gate(gate, context, selected, budget)
    def fetch(descriptor):
        budget.remaining(1)
        originals = fetch_originals(transport_descriptor(descriptor, context))
        require(type(originals) is dict and set(originals) == {"run", "job", "artifact", "archive"},
                "independent transport intake lacks its original API and ZIP responses")
        run, job, artifact = (custody.strict_json(originals[key]) for key in ("run", "job", "artifact"))
        require(run.get("status") == "completed" and job.get("status") == "completed",
                "independent transport readback requires original terminal run and job")
        value = transport_artifact_readback(descriptor, context, run, job, artifact, originals["archive"])
        value["producer_job_conclusion"] = job.get("conclusion")
        value["api_originals"] = {key: {"bytes": len(originals[key]), "sha256": hashlib.sha256(originals[key]).hexdigest()}
                                  for key in ("run", "job", "artifact")}
        transport_cipher_header(value["ciphertext"], context, crypto)
        plaintext, aad = crypto.decrypt_snapshot(value["ciphertext"], private_key)
        require(aad == transport_aad(context), "decrypted AAD differs from actual source and run")
        budget.remaining(1)
        return value, plaintext
    outer, index_raw = fetch(index_descriptor)
    require(outer["member"] == "private-index.bin" and len(index_raw) <= HOSTED_TRANSPORT_INDEX_MAX,
            "external index descriptor selected another member or an oversized index")
    index = custody.strict_json(index_raw)
    require(index.get("context") == context, "private index contains a foreign source context")
    plan, parts = transport_index_records(index)
    require(outer["descriptor"]["artifact_id"] not in {p["descriptor"]["artifact_id"] for p in parts}
            and outer["descriptor"]["zip_sha256"] not in {p["descriptor"]["zip_sha256"] for p in parts},
            "private index reuses one of its own data artifacts")
    contents, observations = {}, []
    for record in parts:
        value, plaintext = fetch(record["descriptor"])
        require(value["member"] == record["member"]
                and value["ciphertext_bytes"] == record["ciphertext_bytes"]
                and value["ciphertext_sha256"] == record["ciphertext_sha256"],
                "independent data ciphertext differs from the authenticated private index")
        observation = transport_snapshot_readback(plaintext, record, plan)
        originals = observation.pop("originals")
        require(not set(contents) & set(originals), "independent data parts repeat a logical original")
        contents.update(originals)
        observations.append({**observation, "descriptor": value["descriptor"], "api_originals": value["api_originals"]})
        budget.remaining(1)
    selected_paths = {row["path"] for row in plan["entries"] if row["state"] == "PRESENT"}
    complete = (set(contents) == selected_paths and not plan["missing_or_unknown"]
                and all(row["snapshot_complete"] and not row["copy_errors"] for row in parts))
    producer_result = {"status": "NOT_ADMITTED_INCOMPLETE_OR_UNKNOWN_ORIGINALS", "automatic_retry": False}
    if complete and "receipt.json" in contents:
        original_terminal = custody.strict_json(contents["receipt.json"])
        producer_result["original_terminal_status"] = original_terminal.get("status")
        wanted = ("OBSERVED_ONE_PRODUCER_COLD_GROWTH_QUALIFICATION" if hosted_purpose(context) == HOSTED_QUALIFICATION_PURPOSE
                  else "PASS_HOSTED_WINDOWS_G1_SELECTED_ROOT_OBSERVATIONS")
        if (original_terminal.get("status") == wanted
                and outer["producer_job_conclusion"] == "success"):
            producer_result = transport_foundation_readback(contents, context, source_listings)
            if hosted_purpose(context) == HOSTED_QUALIFICATION_PURPOSE:
                qualification_measurement(index["post_seal_measurement"], context, expected_phase="custody",
                    original_clock=custody.strict_json(contents["controller/clock.original.json"]),
                    origin=original_terminal["hosted_component_origin"])
                require(index["post_seal_measurement"]["observed_monotonic_ns"] >= original_terminal["finished_monotonic_ns"],
                        "qualification custody measurement predates the original producer terminal")
    return {"schema": HOSTED_TRANSPORT_SCHEMA, "status": "INDEPENDENT_PRIVATE_CIPHERTEXT_READBACK",
            "gate_provenance": provenance, "index_descriptor": outer["descriptor"],
            "index_api_originals": outer["api_originals"], "data_parts": observations,
            "present_originals_complete": complete, "foundation": producer_result,
            "upload_confirmed": True, "sdk_execution_authorized": False, "formal_acceptance": False,
            "automatic_retry": False, "global_process_tree_closed": False}


def transport_gh_originals(gate, context, directory, budget, environment=None):
    """Concrete independent API reader using the existing gh/command custody.

    The caller retains this fresh private directory. It is never published by
    the producer's artifact paths. A failed request consumes its descriptor;
    there is no fallback URL, PID reacquisition or second download attempt.
    """
    require(not directory.exists() and directory.is_absolute()
            and directory.parent.resolve(strict=True) == directory.parent,
            "independent API originals need a fresh canonical directory")
    selected = authority(context["authority_id"], HOSTED_MODE, FIXED["sha"])
    hosted_binding(selected["binding"], hosted_purpose(context))
    env = dict(os.environ) if environment is None else dict(environment)
    executable = shutil.which("gh", path=env.get("PATH"))
    require(executable is not None, "independent gh executable is unavailable")
    executable = Path(executable).resolve(strict=True)
    identity = custody.fingerprint(executable, executable.parent)
    directory.mkdir(mode=0o700)
    consumed = set()
    prefix = "repos/" + context["github"]["GITHUB_REPOSITORY"] + "/actions/"
    def fetch(descriptor):
        value = transport_descriptor(descriptor, context)
        require(value["artifact_id"] not in consumed, "artifact request was already consumed; no retry")
        consumed.add(value["artifact_id"])
        folder = directory / ("artifact-" + str(value["artifact_id"])); folder.mkdir(mode=0o700)
        def query(route, label, limit):
            require(custody.fingerprint(executable, executable.parent) == identity, "independent API tool changed")
            row = observer.command([str(executable), "api", route], gate, env, folder, label,
                                   budget.remaining(120), context)
            require(type(row.get("exit_code")) is int and row["exit_code"] == 0,
                    "independent API original failed; do not retry")
            data = observer.original_stream(folder, row, "stdout")
            observer.original_stream(folder, row, "stderr")
            require(0 < len(data) <= limit, "independent API response exceeds its fixed bound")
            require(custody.fingerprint(executable, executable.parent) == identity, "independent API tool changed during request")
            budget.remaining(1)
            return data
        run = query(prefix + "runs/" + str(value["run_id"]), "run", HOSTED_TRANSPORT_INDEX_MAX)
        jobs_raw = query(prefix + "runs/" + str(value["run_id"]) + "/attempts/1/jobs?per_page=100",
                         "jobs", HOSTED_TRANSPORT_INDEX_MAX)
        jobs = custody.strict_json(jobs_raw)
        require(type(jobs.get("total_count")) is int and type(jobs.get("jobs")) is list
                and 0 < jobs["total_count"] == len(jobs["jobs"]) <= 100,
                "independent job inventory is incomplete or paginated")
        candidates = [row for row in jobs["jobs"] if row.get("name") == HOSTED_SCOPE]
        require(len(candidates) == 1 and type(candidates[0].get("id")) is int
                and candidates[0]["id"] > 0, "independent producer job is missing or duplicated")
        job = query(prefix + "jobs/" + str(candidates[0]["id"]), "job", HOSTED_TRANSPORT_INDEX_MAX)
        require(custody.strict_json(job).get("id") == candidates[0]["id"], "independent job API identity differs")
        artifact = query(prefix + "artifacts/" + str(value["artifact_id"]), "artifact", HOSTED_TRANSPORT_INDEX_MAX)
        api = custody.strict_json(artifact)
        # Check the descriptor size before asking gh to write the ZIP original.
        require(api.get("id") == value["artifact_id"] and type(api.get("size_in_bytes")) is int
                and api["size_in_bytes"] == value["zip_bytes"] and api.get("digest") == "sha256:" + value["zip_sha256"],
                "independent artifact metadata differs before download")
        archive = query(prefix + "artifacts/" + str(value["artifact_id"]) + "/zip", "archive", HOSTED_TRANSPORT_CIPHER_MAX)
        return {"run": run, "job": job, "artifact": artifact, "archive": archive}
    return fetch


def transport_phase_remaining(started, phase, clock=time.monotonic_ns, *, phase_started_ns=None):
    """A finite slice of original H, after keeping later custody phases intact."""
    names = [name for name, _ in HOSTED_TRANSPORT_PHASES]
    require(phase in names, "unknown transport phase")
    later = sum(n for _, n in HOSTED_TRANSPORT_PHASES[names.index(phase) + 1:])
    end = started["original_deadline_monotonic_ns"] - later * 1_000_000_000
    if phase_started_ns is not None:
        require(type(phase_started_ns) is int and started["original_started_monotonic_ns"] <= phase_started_ns <= clock(),
                "transport phase start differs from its retained original")
        end = min(end, phase_started_ns + dict(HOSTED_TRANSPORT_PHASES)[phase] * 1_000_000_000)
    def remaining(cap=1):
        left = (end - clock()) / 1_000_000_000
        require(type(cap) is int and cap > 0 and math.isfinite(left) and left > 0,
                "original transport phase exhausted; no retry or new deadline")
        return min(cap, left)
    return remaining


def transport_api_metadata(gate, private_root, uploaded, context, remaining, *, index_only=False):
    """Same gh API and original command custodian; no URL or shell supplied by a file."""
    executable = shutil.which("gh")
    require(executable is not None, "observed gh executable is unavailable")
    executable = Path(executable).resolve(strict=True)
    tool = custody.fingerprint(executable, executable.parent)
    logs = private_root / ("uploader-api-index" if index_only else "uploader-api-data"); logs.mkdir(mode=0o700)
    api = "repos/" + context["github"]["GITHUB_REPOSITORY"] + "/actions/"
    def query(route, label):
        require(custody.fingerprint(executable, executable.parent) == tool, "observed API tool changed")
        row = observer.command([str(executable), "api", route], gate, dict(os.environ), logs,
                               label, remaining(45), context)
        require(type(row.get("exit_code")) is int and row["exit_code"] == 0, "actual artifact API failed; no retry")
        raw = observer.original_stream(logs, row, "stdout")
        observer.original_stream(logs, row, "stderr")
        require(0 < len(raw) <= HOSTED_TRANSPORT_INDEX_MAX, "artifact API original exceeded JSON bound")
        require(custody.fingerprint(executable, executable.parent) == tool, "API tool changed during query")
        remaining(1)
        return custody.strict_json(raw), {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    result = {}
    for name, row in sorted(uploaded.items()):
        require((name == "private-index" if index_only else re.fullmatch(r"part-(0[1-9]|1[0-5])", name)) and type(row) is dict
                and set(row) == {"artifact_id", "zip_sha256"} and type(row["artifact_id"]) is int
                and row["artifact_id"] > 0 and type(row["zip_sha256"]) is str and DIGEST.fullmatch(row["zip_sha256"]),
                "actual uploader outputs are absent or malformed")
        item, original = query(api + "artifacts/" + str(row["artifact_id"]), name + "-metadata")
        require(type(item.get("id")) is int and item["id"] == row["artifact_id"] and item.get("expired") is False
                and item.get("digest") == "sha256:" + row["zip_sha256"]
                and item.get("workflow_run", {}).get("id") == int(context["github"]["GITHUB_RUN_ID"])
                and item["workflow_run"].get("head_sha") == context["github"]["GITHUB_SHA"],
                "actual uploader artifact belongs to another source/run")
        descriptor = transport_descriptor({**row, "run_id": int(context["github"]["GITHUB_RUN_ID"]),
            "run_attempt": 1, "source_sha": context["github"]["GITHUB_SHA"], "zip_bytes": item.get("size_in_bytes")}, context)
        result[name] = {"descriptor": descriptor, "api_original": original}
    return result


def transport_dependency_argv(python, gate):
    return [str(python), "-I", "-B", "-m", "pip", "install", "--isolated", "--no-input",
            "--disable-pip-version-check", "--no-cache-dir", "--index-url", "https://pypi.org/simple",
            "--only-binary=:all:", "--require-hashes", "--no-deps", "-r", str(gate / HOSTED_TRANSPORT_REQUIREMENTS)]


def transport_entry(action, admission_id, environment=None):
    environment = dict(os.environ) if environment is None else dict(environment)
    require(action in ("preflight", "data", "upload-data-check", "index", "upload-index-check", "descriptor"),
            "unknown private transport action")
    context = request_context(environment, HOSTED_MODE, FIXED["sha"], admission_id)
    gate = Path(__file__).resolve().parents[1]
    root = hosted_private_root(environment, context)
    require(root.is_dir() and root.resolve(strict=True) == root and not root.is_relative_to(gate),
            "private bootstrap root is absent or overlaps the gate")
    review_raw, _ = transport_original(root / "review.original.json", root)
    transport_review(review_raw, context)
    recipient_raw, _ = transport_original(root / "recipient-public.original.json", root)
    recipient = transport_recipient(recipient_raw)
    evidence = root / "execution" / "evidence"
    # Custody can preserve an original bootstrap failure before a worker wrote
    # started.json. Its clock remains the controller's original pre-input t0.
    started_raw, _ = transport_original(root / "clock.original.json", root)
    started = custody.strict_json(started_raw)
    phase = {"preflight": "seal-data", "data": "seal-data", "upload-data-check": "data-uploads",
             "index": "seal-index", "upload-index-check": "index-upload", "descriptor": "descriptor"}[action]
    new_upload_phase = False
    if action == "data":
        preflight_raw, _ = transport_original(root / "transport-preflight.original.json", root)
        preflight = custody.strict_json(preflight_raw)
        checked = transport_resume_phase(started, context, phase, preflight)
    elif action == "upload-data-check":
        slot = environment.get("FOUNDATION_UPLOAD_SLOT", "")
        require(type(slot) is str and re.fullmatch(r"0[1-9]|1[0-5]", slot), "unknown ciphertext upload slot")
        phase_path = root / "transport/data-uploads-phase.original.json"
        if phase_path.exists():
            original_phase = custody.strict_json(transport_original(phase_path, root)[0])
            checked = transport_resume_phase(started, context, phase, original_phase)
        else:
            require(slot == "01", "upload phase cannot start at a later selected part")
            checked = transport_clock(started, context, phase)
            new_upload_phase = True
    else:
        checked = transport_clock(started, context, phase)
    remaining = transport_phase_remaining(started, phase, phase_started_ns=checked["observed_monotonic_ns"])
    original_budget = Budget.__new__(Budget)
    original_budget.started = started["original_started_monotonic_ns"]
    original_budget.deadline = checked["phase_deadline_monotonic_ns"]
    original_budget.clock = time.monotonic_ns
    verify_hosted_gate(gate, context, authority(admission_id, HOSTED_MODE, FIXED["sha"]), original_budget)
    if new_upload_phase:
        custody.write_new(phase_path, checked)
        require(custody.strict_json(transport_original(phase_path, root)[0]) == checked,
                "original data-upload phase readback differs")
    if action == "preflight":
        custody.write_new(root / "transport-preflight.original.json", checked)
        require(custody.strict_json(transport_original(root / "transport-preflight.original.json", root)[0]) == checked,
                "transport preflight readback differs")
        remaining(1)
        return 0
    crypto = transport_primitives(gate)
    private_root = root / "transport"
    outgoing = root / "outgoing"
    if action == "data":
        hosted_retain_outer_originals(root, context, environment)
        rows = transport_inventory(evidence, hosted_purpose(context))
        plan = transport_partition_plan(rows, hosted_purpose(context))  # Full carrier planning before any staging copy.
        remaining(1)
        private_root.mkdir(mode=0o700); outgoing.mkdir(mode=0o700)
        try:
            transport_seal_parts(evidence, private_root, outgoing, rows, context, recipient, crypto, remaining)
            ack_path = root / "bootstrap/technical-ready.original.json"
            if ack_path.is_file():
                ack = custody.strict_json(transport_original(ack_path, root)[0])
                sample = hosted_component_snapshot(root, context, started["original_deadline_monotonic_ns"], phase="custody",
                    initial=ack["prerequisites"]["initial_components"], environment=environment)
                remaining(1)
                crypto.new_json(private_root / "post-seal-components.original.json", sample)
        finally:
            # Earlier complete ciphertext parts may still be uploaded after a
            # later capture failure. This never grants an index or a result.
            remaining(1)
            terminal_raw, _ = transport_original(private_root / "parts.original.json", root, 4 * 1024**2)
            sealed = custody.strict_json(terminal_raw)
            ready = set()
            for part in sealed["parts"]:
                if part.get("status") not in ("SEALED_PRESENT_SNAPSHOT", "SEALED_PARTIAL_SNAPSHOT"): continue
                fingerprint = custody.fingerprint(outgoing / (part["id"] + ".bin"), outgoing)
                require((fingerprint["bytes"], fingerprint["sha256"]) ==
                        (part["ciphertext_bytes"], part["ciphertext_sha256"]), "ready ciphertext changed")
                ready.add(part["id"])
            remaining(1)
            # Only opaque fixed identifiers cross into the public action interface.
            with Path(environment["GITHUB_OUTPUT"]).open("a", encoding="ascii", newline="\n") as stream:
                for number in range(1, HOSTED_TRANSPORT_DATA_PARTS + 1):
                    name = "part-" + str(number).zfill(2)
                    stream.write(name.replace("-", "_") + "=" + ("ready" if name in ready else "absent") + "\n")
        return 0
    plan_raw, _ = transport_original(private_root / "partition-plan.json", root, 4 * 1024**2)
    seal_raw, _ = transport_original(private_root / "parts.original.json", root, 4 * 1024**2)
    plan, sealed = custody.strict_json(plan_raw), custody.strict_json(seal_raw)
    if action in ("upload-data-check", "upload-index-check"):
        if action == "upload-data-check":
            value = environment.get("FOUNDATION_UPLOAD_SLOT", "")
            require(type(value) is str and re.fullmatch(r"0[1-9]|1[0-5]", value), "unknown ciphertext upload slot")
            name = "part-" + value
            numbers = [row["id"] for row in plan["parts"]]
            require(name in numbers, "ciphertext upload slot is not in the original closed plan")
            remaining_uploads = len(numbers) - numbers.index(name)
            require(checked["phase_deadline_monotonic_ns"] - time.monotonic_ns()
                    >= remaining_uploads * 60 * 1_000_000_000,
                    "original upload reserve cannot cover the remaining actual data actions")
            records = [row for row in sealed["parts"] if row["id"] == name]
            require(len(records) == 1 and records[0].get("status") in ("SEALED_PRESENT_SNAPSHOT", "SEALED_PARTIAL_SNAPSHOT"),
                    "ciphertext upload part has no original seal terminal")
            record = records[0]
        else:
            name = "private-index"
            record = custody.strict_json(transport_original(private_root / "index.original.json", root)[0])
        stamp = custody.fingerprint(outgoing / (name + ".bin"), outgoing)
        require((stamp["bytes"], stamp["sha256"]) == (record["ciphertext_bytes"], record["ciphertext_sha256"]),
                "ciphertext changed before the original upload action")
        custody.write_new(private_root / (name + "-upload-guard.original.json"), checked)
        remaining(1)
        with Path(environment["GITHUB_OUTPUT"]).open("a", encoding="ascii", newline="\n") as stream:
            stream.write("ready=true\n")
        return 0
    if action == "index":
        supplied = {}
        for number in range(1, HOSTED_TRANSPORT_DATA_PARTS + 1):
            name = "part-" + str(number).zfill(2)
            value = environment.get("FOUNDATION_ARTIFACT_" + str(number).zfill(2), "")
            digest = environment.get("FOUNDATION_DIGEST_" + str(number).zfill(2), "")
            if value or digest:
                require(re.fullmatch(r"[1-9][0-9]*", value) and DIGEST.fullmatch(digest), "uploader descriptor is partial")
                supplied[name] = {"artifact_id": int(value), "zip_sha256": digest}
        require(set(supplied) == {part["id"] for part in plan["parts"]}, "uploader omitted or added a planned part")
        metadata = transport_api_metadata(gate, private_root, supplied, context, remaining)
        uploaded = {record["id"]: {"descriptor": metadata[record["id"]]["descriptor"],
                    "member": record["id"] + ".bin", "ciphertext_bytes": record["ciphertext_bytes"],
                    "ciphertext_sha256": record["ciphertext_sha256"]} for record in sealed["parts"]}
        crypto.new_json(private_root / "uploaded.original.json", metadata)
        remaining(1)
        measured_path = private_root / "post-seal-components.original.json"
        measured = custody.strict_json(transport_original(measured_path, root)[0]) if measured_path.is_file() else None
        record = transport_seal_index(plan, sealed, uploaded, context, recipient, crypto, outgoing, measured)
        remaining(1)
        crypto.new_json(private_root / "index.original.json", record)
        return 0
    # The public descriptor is intentionally small and never claims upload
    # confirmation or Foundation PASS. Independent API/ZIP readback is separate.
    artifact_id, digest = environment.get("FOUNDATION_INDEX_ARTIFACT", ""), environment.get("FOUNDATION_INDEX_DIGEST", "")
    require(re.fullmatch(r"[1-9][0-9]*", artifact_id) and DIGEST.fullmatch(digest), "index uploader output is incomplete")
    record_raw, _ = transport_original(private_root / "index.original.json", root)
    record = custody.strict_json(record_raw)
    require(record["member"] == "private-index.bin", "index upload selected another carrier")
    original = transport_api_metadata(gate, private_root, {"private-index": {
        "artifact_id": int(artifact_id), "zip_sha256": digest}}, context, remaining, index_only=True)["private-index"]
    opaque = {"artifact_id": int(artifact_id), "zip_sha256": digest,
              "zip_bytes": original["descriptor"]["zip_bytes"],
              "ciphertext_sha256": record["ciphertext_sha256"], "ciphertext_bytes": record["ciphertext_bytes"],
              "upload_confirmed": False, "foundation_result_admitted": False}
    crypto.new_json(root / "index-uploader-descriptor.original.json", opaque)
    remaining(1)
    print(json.dumps(opaque, sort_keys=True))
    return 0

# Hosted composition uses the existing dispatch, Git, command observer and
# ciphertext upload surfaces. A technical bootstrap receipt is not human or
# lease authority; the independent reviewed catalogue remains mandatory.
HOSTED_INPUT_SCHEMA = "motionwright.sdk-foundation-public-transport-input/1"
HOSTED_BOOTSTRAP_SCHEMA = "motionwright.sdk-foundation-technical-bootstrap/1"
HOSTED_PUBLIC_SOURCE_REPOSITORY = "https://github.com/seradotcom/semwright.git"
HOSTED_CONTROL_CLEANUP_SECONDS = 30


def hosted_image_tool_roots(tools):
    """Check the resolved observed binaries, not a guessed Node MSI path."""
    require(PureWindowsPath(tools["node"]["absolute_path"]).drive.upper() == "C:"
            and tools["node"]["version"].startswith("v22."),
            "hosted Node22 must resolve to the image volume selected for tool reads")
    for name in ("gcc", "g++", "ar", "as", "ld"):
        require(str(PureWindowsPath(tools[name]["absolute_path"]).parent).casefold() == "c:\\mingw64\\bin",
                "hosted GNU binaries differ from the declared image tool root")
    return {"declarative_sources": dict(HOSTED_IMAGE_PREREQUISITE_SOURCES),
            "actual_observed_binary_fingerprints_required": True,
            "node_installation_path_inferred_from_MSI": False}


def hosted_transport_input(raw, context):
    import base64
    require(type(raw) is bytes and 0 < len(raw) <= 8192, "public transport input is absent or unbounded")
    value = custody.strict_json(raw)
    require(type(value) is dict and set(value) == {"schema", "review_base64", "recipient_base64"}
            and value["schema"] == HOSTED_INPUT_SCHEMA, "public transport input is not closed")
    originals = {}
    for name in ("review", "recipient"):
        encoded = value[name + "_base64"]
        require(type(encoded) is str and 0 < len(encoded) <= 6144, "public transport original encoding differs")
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as error:
            raise custody.VerificationError("public transport original is not exact base64") from error
        require(base64.b64encode(decoded).decode("ascii") == encoded, "public transport base64 is not canonical")
        originals[name] = decoded
    review = transport_review(originals["review"], context)
    recipient = transport_recipient(originals["recipient"])
    return {"review": review, "recipient": recipient, "originals": originals,
            "input_sha256": hashlib.sha256(raw).hexdigest(), "human_acknowledgement": False}


def hosted_private_root(environment, context):
    workspace = Path(environment["GITHUB_WORKSPACE"]).resolve(strict=True)
    parent = workspace / "verification"
    require(parent.is_dir() and parent.resolve(strict=True) == parent,
            "owned hosted verification parent is absent or linked")
    info = parent.lstat()
    require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
            "owned hosted verification parent is a reparse point")
    return parent / ("foundation-private-" + context["github"]["GITHUB_RUN_ID"] + "-1")


def hosted_clock(context, started_ns, now_ns=None):
    now = time.monotonic_ns() if now_ns is None else now_ns
    seconds = context["max_total_seconds"]
    require(type(seconds) is int and HOSTED_TRANSPORT_RESERVE + 60 <= seconds <= 7200
            and type(started_ns) is int and type(now) is int and 0 < started_ns <= now,
            "hosted original clock or finite work/custody budget differs")
    deadline = started_ns + seconds * 1_000_000_000
    require(now < deadline - HOSTED_TRANSPORT_RESERVE * 1_000_000_000,
            "hosted work cannot begin inside the custody reserve")
    return {"schema": SCHEMA, "context": context, "budget_seconds": seconds,
            "original_started_monotonic_ns": started_ns,
            "original_deadline_monotonic_ns": deadline,
            "work_deadline_monotonic_ns": deadline - HOSTED_TRANSPORT_RESERVE * 1_000_000_000,
            "custody_reserved_seconds": HOSTED_TRANSPORT_RESERVE,
            "budget_boundary": "COMMAND_TIMEOUTS_AND_PHASE_CHECKS_NOT_FILESYSTEM_INTERRUPTION"}


class HostedWorkBudget(Budget):
    """An inherited view ending before custody; construction never samples t0."""
    def __init__(self, original, context, clock=time.monotonic_ns):
        require(type(original) is dict and original == hosted_clock(
            context, original.get("original_started_monotonic_ns"), clock()),
            "hosted inherited clock changed or renewed")
        self.clock = clock
        self.started = original["original_started_monotonic_ns"]
        self.deadline = original["work_deadline_monotonic_ns"]
        self.original_deadline = original["original_deadline_monotonic_ns"]
        self.custody_reserved_seconds = HOSTED_TRANSPORT_RESERVE


def hosted_write_original(path, raw):
    """Exclusive original bytes; final encrypted custody supplies its readback."""
    require(type(raw) is bytes and len(raw) <= HOSTED_TRANSPORT_TOTAL_MAX, "hosted original exceeds bound")
    with path.open("xb") as stream:
        stream.write(raw)
    value = custody.fingerprint(path, path.parent)
    require(value["bytes"] == len(raw) and value["sha256"] == hashlib.sha256(raw).hexdigest(),
            "hosted original write/readback differs; no retry")
    return value


def hosted_source_plan(root, purpose=HOSTED_FULL_PURPOSE):
    return [(role, profile, root / "checkouts" / role) for role, profile in hosted_profiles(purpose)]


def hosted_fetch_sources(root, gate, context, budget, call, environment):
    executable = shutil.which("git")
    require(executable is not None, "observed Git is unavailable")
    executable = Path(executable).resolve(strict=True)
    identity = custody.fingerprint(executable, executable.parent)
    rows = {}
    for role, profile, checkout in hosted_source_plan(root, hosted_purpose(context)):
        budget.remaining(1)
        require(not checkout.exists(), "hosted incoming checkout already exists; no fetch retry")
        commands = (
            ("init", [str(executable), "--no-replace-objects", "init", "--template=", "--quiet", str(checkout)]),
            ("fetch", [str(executable), "--no-replace-objects", "-C", str(checkout),
                       "-c", "core.fsmonitor=false", "-c", "protocol.file.allow=never",
                       "fetch", "--no-tags", "--depth=1", "--no-recurse-submodules", HOSTED_PUBLIC_SOURCE_REPOSITORY, profile["sha"]]),
            ("checkout", [str(executable), "--no-replace-objects", "-C", str(checkout),
                          "-c", "core.autocrlf=false", "-c", "core.eol=lf", "-c", "core.fsmonitor=false",
                          "checkout", "--detach", "--no-recurse-submodules", profile["sha"]]),
        )
        for phase, argv in commands:
            require(custody.fingerprint(executable, executable.parent) == identity, "hosted Git changed before incoming command")
            row = call(argv, gate, environment, root / "bootstrap/commands", "incoming-" + role + "-" + phase,
                       180 if phase == "fetch" else 45, context)
            require(type(row.get("exit_code")) is int and row["exit_code"] == 0,
                    "hosted incoming Git failed; no source substitution or retry")
        snapshot = full_snapshot(checkout, profile)
        budget.remaining(1)
        raw = transport_json(snapshot)
        stamp = hosted_write_original(root / "bootstrap" / (role + "-source.json"), raw)
        rows[role] = {"checkout": str(checkout), "sha": profile["sha"], "tree": profile["tree"],
                      "listing_sha256": profile["listing"], "snapshot_sha256": stamp["sha256"],
                      "snapshot_bytes": stamp["bytes"]}
    require(custody.fingerprint(executable, executable.parent) == identity, "hosted Git changed during incoming custody")
    return rows


def hosted_gate_job(gate, root, context, budget, call, environment):
    executable = shutil.which("gh")
    require(executable is not None, "observed Actions API tool unavailable")
    executable = Path(executable).resolve(strict=True)
    identity = custody.fingerprint(executable, executable.parent)
    route = ("repos/" + context["github"]["GITHUB_REPOSITORY"] + "/actions/runs/"
             + context["github"]["GITHUB_RUN_ID"] + "/attempts/1/jobs?per_page=100")
    row = call([str(executable), "api", route], gate, environment, root / "bootstrap/commands", "gate-source-job", 45, context)
    require(type(row.get("exit_code")) is int and row["exit_code"] == 0, "actual gate G0 API failed")
    raw = observer.original_stream(root / "bootstrap/commands", row, "stdout")
    observer.original_stream(root / "bootstrap/commands", row, "stderr")
    require(0 < len(raw) <= HOSTED_TRANSPORT_INDEX_MAX, "actual gate G0 API is unbounded")
    value = custody.strict_json(raw)
    require(type(value.get("total_count")) is int and type(value.get("jobs")) is list
            and len(value["jobs"]) == value["total_count"] <= 100,
            "actual attempt job roster is truncated")
    jobs = [item for item in value["jobs"] if item.get("name") == "sdk-foundation-g0"]
    require(len(jobs) == 1 and type(jobs[0].get("id")) is int and jobs[0]["id"] > 0
            and type(jobs[0].get("run_id")) is int and jobs[0]["run_id"] == int(context["github"]["GITHUB_RUN_ID"])
            and jobs[0].get("head_sha") == context["github"]["GITHUB_SHA"]
            and jobs[0].get("status") == "completed" and jobs[0].get("conclusion") == "success",
            "actual original gate G0 job is missing, foreign or unsuccessful")
    require(custody.fingerprint(executable, executable.parent) == identity, "Actions API tool changed")
    budget.remaining(1)
    return {"job_id": jobs[0]["id"], "source_sha": context["github"]["GITHUB_SHA"],
            "run_id": jobs[0]["run_id"], "run_attempt": 1, "conclusion": "success",
            "api_original_sha256": hashlib.sha256(raw).hexdigest(), "command": row,
            "sdk_g0_credit": "NONE"}


def hosted_load_bootstrap(root, expected_ack_sha256, context, environment):
    require(type(expected_ack_sha256) is str and DIGEST.fullmatch(expected_ack_sha256),
            "technical bootstrap digest is absent")
    require(root == hosted_private_root(environment, context) and root.is_dir()
            and root.resolve(strict=True) == root, "technical bootstrap is outside this original job")
    ack_raw, _ = transport_original(root / "bootstrap/technical-ready.original.json", root)
    require(hashlib.sha256(ack_raw).hexdigest() == expected_ack_sha256, "technical bootstrap original differs")
    ack = custody.strict_json(ack_raw)
    keys = {"schema", "kind", "context", "clock", "input_sha256", "review_sha256", "recipient_sha256",
            "gate_admission", "gate_g0", "sources", "crypto_sources", "prerequisites", "human_acknowledgement",
            "lease_authority", "sdk_started", "automatic_retry"}
    require(type(ack) is dict and set(ack) == keys and ack["schema"] == HOSTED_BOOTSTRAP_SCHEMA
            and ack["kind"] == "TECHNICAL_INPUT_AND_CUSTODY_READY" and ack["context"] == context
            and ack["human_acknowledgement"] is False and ack["lease_authority"] is False
            and ack["sdk_started"] is False and ack["automatic_retry"] is False,
            "technical bootstrap shape or source differs")
    input_raw, _ = transport_original(root / "input.original.json", root, 8192)
    parsed = hosted_transport_input(input_raw, context)
    require(ack["input_sha256"] == parsed["input_sha256"]
            and ack["review_sha256"] == hashlib.sha256(parsed["originals"]["review"]).hexdigest()
            and ack["recipient_sha256"] == HOSTED_TRANSPORT_RECIPIENT_SHA256,
            "technical bootstrap input/R/recipient differs")
    for name, original in parsed["originals"].items():
        path = root / ("review.original.json" if name == "review" else "recipient-public.original.json")
        require(transport_original(path, root)[0] == original, "technical bootstrap original was altered")
    clock_raw, _ = transport_original(root / "clock.original.json", root)
    require(custody.strict_json(clock_raw) == ack["clock"], "technical bootstrap original clock differs")
    budget = HostedWorkBudget(ack["clock"], context)
    gate = Path(__file__).resolve().parents[1]
    verified_gate = verify_hosted_gate(gate, context, authority(context["authority_id"], HOSTED_MODE, FIXED["sha"]), budget)
    require(ack["gate_admission"] == verified_gate and ack["crypto_sources"] == dict(HOSTED_TRANSPORT_PINS),
            "technical bootstrap gate or custody sources differ")
    expected_sources = hosted_source_plan(root, hosted_purpose(context))
    require(set(ack["sources"]) == {role for role, _, _ in expected_sources}, "technical bootstrap source roles differ")
    for role, profile, checkout in expected_sources:
        row = ack["sources"][role]
        source_raw, _ = transport_original(root / "bootstrap" / (role + "-source.json"), root, 16 * 1024**2)
        require(type(row) is dict and set(row) == {"checkout", "sha", "tree", "listing_sha256", "snapshot_sha256", "snapshot_bytes"}
                and row["checkout"] == str(checkout) and row["sha"] == profile["sha"] and row["tree"] == profile["tree"]
                and row["listing_sha256"] == profile["listing"] and type(row["snapshot_bytes"]) is int
                and row["snapshot_bytes"] == len(source_raw) and row["snapshot_sha256"] == hashlib.sha256(source_raw).hexdigest(),
                "technical bootstrap source snapshot binding differs")
        snapshot = custody.strict_json(source_raw)
        require(snapshot.get("sha") == profile["sha"] and snapshot.get("tree") == profile["tree"]
                and snapshot.get("checkout") == str(checkout), "technical bootstrap snapshot names a foreign source")
        budget.remaining(1)
        source_unchanged(checkout, snapshot)
        budget.remaining(1)
    require(ack["prerequisites"].get("status") == "OBSERVED_EXISTING_PREREQUISITES"
            and ack["gate_g0"].get("conclusion") == "success"
            and ack["gate_g0"].get("source_sha") == context["github"]["GITHUB_SHA"],
            "technical bootstrap prerequisites or actual gate G0 are incomplete")
    logs = root / "bootstrap/commands"
    for key, label in (("python_command", "sdk-prerequisites"), ("dependency_command", "transport-dependencies"),
                       ("custody_capability_command", "custody-capability"), ("toolchain_command", "owned-toolchain")):
        row = ack["prerequisites"][key]
        require(custody.strict_json(transport_original(logs / (label + ".command.json"), root)[0]) == row
                and row.get("context") == context and type(row.get("exit_code")) is int and row["exit_code"] == 0
                and row.get("observation") == "DIRECT_PROCESS_EXIT_OBSERVED" and row.get("log_custody") == "VERIFIED_SNAPSHOT",
                "technical bootstrap command original is foreign, changed or uncertain")
        observer.original_stream(logs, row, "stdout"); observer.original_stream(logs, row, "stderr")
        budget.remaining(1)
    gate_row = ack["gate_g0"]["command"]
    require(custody.strict_json(transport_original(logs / "gate-source-job.command.json", root)[0]) == gate_row
            and gate_row.get("context") == context and type(gate_row.get("exit_code")) is int and gate_row["exit_code"] == 0
            and gate_row.get("observation") == "DIRECT_PROCESS_EXIT_OBSERVED" and gate_row.get("log_custody") == "VERIFIED_SNAPSHOT",
            "technical bootstrap gate G0 command original differs")
    gate_api_raw = observer.original_stream(logs, gate_row, "stdout")
    observer.original_stream(logs, gate_row, "stderr")
    require(hashlib.sha256(gate_api_raw).hexdigest() == ack["gate_g0"]["api_original_sha256"],
            "technical bootstrap actual G0 API original differs")
    jobs = custody.strict_json(gate_api_raw)
    require(type(jobs.get("total_count")) is int and type(jobs.get("jobs")) is list
            and 0 < jobs["total_count"] == len(jobs["jobs"]) <= 100, "bootstrap G0 job roster is incomplete")
    observed = [item for item in jobs["jobs"] if item.get("name") == "sdk-foundation-g0"]
    require(len(observed) == 1 and type(observed[0].get("id")) is int
            and observed[0]["id"] == ack["gate_g0"]["job_id"]
            and type(observed[0].get("run_id")) is int
            and observed[0]["run_id"] == int(context["github"]["GITHUB_RUN_ID"])
            and observed[0].get("head_sha") == context["github"]["GITHUB_SHA"]
            and observed[0].get("status") == "completed" and observed[0].get("conclusion") == "success",
            "technical bootstrap actual gate G0 observation is foreign or unsuccessful")
    tools = ack["prerequisites"]["tools"]
    require(type(tools) is dict and set(tools) == {"cargo", "rustc", "rustdoc", "cargo-fmt", "rustfmt", "rustup",
                "python", "git", "node", "bash", "gcc", "g++", "ar", "as", "ld"}
            and all(type(row.get("version")) is str and row["version"].strip() for row in tools.values()),
            "technical bootstrap observed tool roster is incomplete")
    require(ack["prerequisites"].get("image_tools") == hosted_image_tool_roots(tools),
            "technical bootstrap image tool placement differs")
    verify_tools(tools)
    python = root / "toolchain/custody-python/Scripts/python.exe"
    require(ack["prerequisites"].get("worker_python") == custody.fingerprint(python, root)
            and ack["prerequisites"].get("worker_python_path") == str(python),
            "technical bootstrap owned worker interpreter changed")
    return {"root": root, "ack": ack, "ack_sha256": expected_ack_sha256, "budget": budget,
            "sdk": expected_sources[0][2], "baselines": {role: path for role, _, path in expected_sources[1:]}}


def hosted_collect_controller(root, context, terminal):
    """Retain controller originals once; uncertainty never creates a worker PASS."""
    evidence = root / "execution/evidence"
    evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = evidence / "controller"; destination.mkdir(mode=0o700, exist_ok=True)
    errors = []
    originals = [("input.original.json", root / "input.original.json"),
                 ("clock.original.json", root / "clock.original.json"),
                 ("bootstrap.original.json", root / "bootstrap/technical-ready.original.json"),
                 ("worker.stdout.original", root / "bootstrap/commands/sdk-worker.stdout.log"),
                 ("worker.stderr.original", root / "bootstrap/commands/sdk-worker.stderr.log"),
                 ("worker.exit.original.json", root / "bootstrap/commands/sdk-worker.command.json")]
    for name, original in originals:
        if not original.exists():
            errors.append({"path": name, "status": "MISSING_NOT_RECONSTRUCTED"})
            continue
        try:
            raw, _ = transport_original(original, root, HOSTED_TRANSPORT_PAYLOAD_MAX)
            hosted_write_original(destination / name, raw)
        except BaseException as error:
            errors.append({"path": name, "status": "UNKNOWN_NO_RETRY", "error": str(error)})
    commands = evidence / "commands"; commands.mkdir(mode=0o700, exist_ok=True)
    # The bootstrap uses only the same observed command triples and resource
    # JSONs. They remain private, with new fixed prefixes to avoid overwrites.
    for original in sorted((root / "bootstrap/commands").iterdir()):
        try:
            name = "bootstrap-" + original.name
            require(transport_role_path("commands/" + name, "command-original"),
                    "bootstrap command original is outside the closed roster")
            raw, _ = transport_original(original, root, HOSTED_TRANSPORT_PAYLOAD_MAX)
            hosted_write_original(commands / name, raw)
        except BaseException as error:
            errors.append({"path": original.name, "status": "UNKNOWN_NO_RETRY", "error": str(error)})
    value = {**terminal, "controller_custody_errors": errors,
             "controller_originals_complete": not errors, "foundation_result_admitted": False}
    if any(row["status"].startswith("UNKNOWN") for row in errors):
        value["status"] = "UNKNOWN_CONTROLLER_CUSTODY_NO_RETRY"
    custody.write_new(destination / "original-failure.json" if not value["status"].startswith("WORKER_EXIT_OBSERVED")
                      else destination / "terminal.original.json", value)
    return value


def hosted_retain_outer_originals(root, context, environment):
    """After controller exit, preserve the real workflow shell's three originals."""
    public_parent = Path(environment["GITHUB_WORKSPACE"]).resolve(strict=True) / "verification"
    originals = public_parent / ("foundation-controller-" + context["github"]["GITHUB_RUN_ID"] + "-1")
    require(originals.is_dir() and originals.resolve(strict=True) == originals,
            "original private controller streams are absent or linked")
    destination = root / "execution/evidence/controller"
    require(destination.is_dir(), "retained controller namespace is absent")
    for name in ("stdout.original", "stderr.original", "exit.original.json"):
        raw, _ = transport_original(originals / name, originals, HOSTED_TRANSPORT_PAYLOAD_MAX)
        if name == "exit.original.json":
            terminal = custody.strict_json(raw)
            require(type(terminal) is dict and set(terminal) == {"schema", "run_id", "run_attempt", "source_sha", "exit_code"}
                    and terminal["schema"] == HOSTED_BOOTSTRAP_SCHEMA
                    and terminal["run_id"] == context["github"]["GITHUB_RUN_ID"]
                    and terminal["run_attempt"] == "1" and terminal["source_sha"] == context["github"]["GITHUB_SHA"]
                    and type(terminal["exit_code"]) is int,
                    "workflow controller terminal is absent, unknown or foreign")
        hosted_write_original(destination / name, raw)


def hosted_crypto_probe(admission_id, environment=None):
    environment = dict(os.environ) if environment is None else dict(environment)
    context = request_context(environment, HOSTED_MODE, FIXED["sha"], admission_id)
    parsed = hosted_transport_input(environment.get("SDK_FOUNDATION_TRANSPORT", "").encode(), context)
    root = hosted_private_root(environment, context)
    require(Path(sys.executable).resolve(strict=True) == (root / "toolchain/custody-python/Scripts/python.exe").resolve(strict=True),
            "custody capability probe must use the owned interpreter")
    original = custody.strict_json(transport_original(root / "clock.original.json", root)[0])
    budget = HostedWorkBudget(original, context)
    module = transport_primitives(Path(__file__).resolve().parents[1])
    module.public_key(parsed["recipient"])
    budget.remaining(1)
    value = {"schema": HOSTED_BOOTSTRAP_SCHEMA, "status": "EXISTING_CUSTODY_IMPORTS_AND_RECIPIENT_OBSERVED",
             "context": context, "source_pins": dict(HOSTED_TRANSPORT_PINS),
             "recipient_sha256": HOSTED_TRANSPORT_RECIPIENT_SHA256, "private_key_loaded": False,
             "python": custody.fingerprint(Path(sys.executable).resolve(strict=True), root)}
    print(json.dumps(value, sort_keys=True))
    return 0


def hosted_prepare_custody_python(root, gate, context, budget, call, environment):
    base = Path(sys.executable).resolve(strict=True)
    base_identity = custody.fingerprint(base, base.parent)
    home = root / "toolchain/custody-python"
    require(not home.exists(), "owned custody interpreter already exists; no provisioning retry")
    row = call([str(base), "-I", "-B", "-m", "venv", "--copies", str(home)],
               gate, environment, root / "bootstrap/commands", "custody-python", 120, context)
    require(type(row.get("exit_code")) is int and row["exit_code"] == 0
            and custody.fingerprint(base, base.parent) == base_identity,
            "owned custody interpreter provisioning failed")
    python = home / "Scripts/python.exe"
    python_identity = custody.fingerprint(python, root)
    lock = custody.fingerprint(gate / HOSTED_TRANSPORT_REQUIREMENTS, gate, 4096)
    require(lock["sha256"] == HOSTED_TRANSPORT_REQUIREMENTS_SHA256, "Windows custody requirements differ")
    dependency = call(transport_dependency_argv(python, gate), gate, environment,
                      root / "bootstrap/commands", "transport-dependencies", 120, context)
    require(type(dependency.get("exit_code")) is int and dependency["exit_code"] == 0
            and custody.fingerprint(python, root) == python_identity
            and custody.fingerprint(gate / HOSTED_TRANSPORT_REQUIREMENTS, gate, 4096) == lock,
            "exact Windows custody preparation failed before SDK")
    argv = [str(python), "-I", "-B", "-X", "utf8", str(gate / HELPER), "--mode", HOSTED_MODE,
            "--authority", context["authority_id"], "--hosted-crypto-probe"]
    probe = call(argv, gate, environment, root / "bootstrap/commands", "custody-capability", 45, context)
    require(type(probe.get("exit_code")) is int and probe["exit_code"] == 0, "owned existing crypto capability unavailable")
    raw = observer.original_stream(root / "bootstrap/commands", probe, "stdout")
    value = custody.strict_json(raw.strip())
    require(value.get("status") == "EXISTING_CUSTODY_IMPORTS_AND_RECIPIENT_OBSERVED"
            and value.get("context") == context and value.get("source_pins") == dict(HOSTED_TRANSPORT_PINS)
            and value.get("recipient_sha256") == HOSTED_TRANSPORT_RECIPIENT_SHA256
            and value.get("private_key_loaded") is False and value.get("python") == python_identity,
            "owned crypto capability receipt differs")
    budget.remaining(1)
    return python, python_identity, dependency, probe


def hosted_controller(admission_id, environment=None):
    environment = dict(os.environ) if environment is None else dict(environment)
    context = request_context(environment, HOSTED_MODE, FIXED["sha"], admission_id)
    raw_input = environment.get("SDK_FOUNDATION_TRANSPORT", "").encode("utf-8")
    parsed = hosted_transport_input(raw_input, context)  # Pure authority/hash checks first.
    original = Budget(context["max_total_seconds"])
    clock = hosted_clock(context, original.started)
    budget = HostedWorkBudget(clock, context)
    gate = Path(__file__).resolve().parents[1]
    root = hosted_private_root(environment, context)
    require(not root.exists(), "private original run already exists; no retry")
    root.mkdir(mode=0o700)
    (root / "bootstrap").mkdir(mode=0o700)
    (root / "bootstrap/commands").mkdir(mode=0o700)
    (root / "checkouts").mkdir(mode=0o700)
    (root / "toolchain").mkdir(mode=0o700)
    terminal = {"schema": HOSTED_BOOTSTRAP_SCHEMA, "context": context, "clock": clock,
                "status": "FAIL_BEFORE_SDK", "worker_started": False, "sdk_execution": "NOT_INFERRED_FROM_WORKER_LAUNCH",
                "automatic_retry": False,
                "process_boundary": "DIRECT_PROCESS_ONLY", "escaped_descendants": "NOT_OBSERVED"}
    observations = []
    resource_number = 0
    def bounded(argv, cwd, env, output, label, cap, frame):
        row = observer.command(argv, cwd, env, output, label, budget.remaining(cap), frame,
                               (root, observations))
        budget.remaining(1)
        return row
    def call(argv, cwd, env, output, label, cap, frame):
        nonlocal resource_number
        if "initial_components" in terminal:
            resource_number += 1
            resources = observe_resources(HOSTED_MODE, root, root / "bootstrap/commands", context, bounded,
                                          "pre-command-" + str(resource_number))
            hosted_growth_observation(resources, context, terminal["initial_resources"]["disk"])
            terminal.setdefault("component_samples", []).append(hosted_component_snapshot(
                root, context, budget.deadline, phase="preparation", initial=terminal["initial_components"], environment=environment))
        return bounded(argv, cwd, env, output, label, cap, frame)
    try:
        hosted_write_original(root / "input.original.json", raw_input)
        hosted_write_original(root / "review.original.json", parsed["originals"]["review"])
        hosted_write_original(root / "recipient-public.original.json", parsed["originals"]["recipient"])
        custody.write_new(root / "clock.original.json", clock)
        selected = authority(admission_id, HOSTED_MODE, FIXED["sha"])
        gate_admission = verify_hosted_gate(gate, context, selected, budget)
        for name in ("targets", "temporary", "cargo-home", "pip-cache", "npm-cache", "config", "data", "cache", "state", "runtime"):
            (root / "bootstrap" / name).mkdir(mode=0o700)
        env = clean_environment(environment, root / "bootstrap", root / "bootstrap/targets", root / "bootstrap/temporary")
        for name in ("GH_TOKEN", "GITHUB_TOKEN"):
            env.pop(name, None)
        env["RUSTUP_TOOLCHAIN"] = HOSTED_RUST
        env["RUSTUP_HOME"] = str(root / "toolchain/rustup")
        initial = observe_resources(HOSTED_MODE, root, root / "bootstrap/commands", context, call, "initial-resources", initial=True)
        terminal["initial_resources"] = initial
        terminal["initial_growth"] = hosted_growth_observation(initial, context)
        terminal["initial_components"] = hosted_component_snapshot(root, context, budget.deadline,
                                                                   phase="initial", environment=environment)
        gate_api_environment = dict(env, GH_TOKEN=environment.get("GH_TOKEN", ""))
        gate_g0 = hosted_gate_job(gate, root, context, budget, call, gate_api_environment)
        python, python_identity, dependency, capability = hosted_prepare_custody_python(root, gate, context, budget, call, env)
        resolver = shutil.which("rustup")
        require(resolver is not None, "observed Rust toolchain resolver unavailable")
        resolver = Path(resolver).resolve(strict=True)
        resolver_identity = custody.fingerprint(resolver, resolver.parent)
        toolchain = call([str(resolver), "toolchain", "install", HOSTED_RUST, "--profile", "minimal",
                          "--component", "rustfmt", "--component", "clippy", "--no-self-update"],
                         gate, env, root / "bootstrap/commands", "owned-toolchain", 600, context)
        require(type(toolchain.get("exit_code")) is int and toolchain["exit_code"] == 0
                and custody.fingerprint(resolver, resolver.parent) == resolver_identity,
                "owned exact GNU toolchain provisioning failed")
        # All nine Python dependencies come from the same hashed Windows lock
        # into the isolated owned venv, with no inherited global site packages.
        # Observe their imports before source retrieval and again at SDK G0.
        prerequisite = call([str(python), "-I", "-B", "-X", "utf8", "-c",
                             "import sys,tomllib,jsonschema,yaml; assert sys.version_info >= (3,11); print('SDK_G0_PREREQUISITES_AVAILABLE')"],
                            gate, env, root / "bootstrap/commands", "sdk-prerequisites", 45, context)
        require(type(prerequisite.get("exit_code")) is int and prerequisite["exit_code"] == 0,
                "SDK source-check prerequisites are unavailable before incoming checkouts")
        tools, _ = observe_tools(gate, env, root / "bootstrap/commands", context, call, python_executable=python)
        image_tools = hosted_image_tool_roots(tools)
        sources = hosted_fetch_sources(root, gate, context, budget, call, env)
        terminal["prepared_components"] = hosted_component_snapshot(root, context, budget.deadline, phase="prepared",
            initial=terminal["initial_components"], environment=environment)
        budget.remaining(HOSTED_CONTROL_CLEANUP_SECONDS + 1)
        ack = {"schema": HOSTED_BOOTSTRAP_SCHEMA, "kind": "TECHNICAL_INPUT_AND_CUSTODY_READY",
               "context": context, "clock": clock, "input_sha256": parsed["input_sha256"],
               "review_sha256": hashlib.sha256(parsed["originals"]["review"]).hexdigest(),
               "recipient_sha256": HOSTED_TRANSPORT_RECIPIENT_SHA256, "gate_admission": gate_admission,
               "gate_g0": gate_g0, "sources": sources, "crypto_sources": dict(HOSTED_TRANSPORT_PINS),
               "prerequisites": {"status": "OBSERVED_EXISTING_PREREQUISITES", "tools": tools,
                                 "image_tools": image_tools,
                                 "python_command": prerequisite, "dependency_command": dependency,
                                 "custody_capability_command": capability, "toolchain_command": toolchain,
                                 "worker_python": python_identity, "worker_python_path": str(python),
                                 "initial_components": terminal["initial_components"],
                                 "initial_resources": terminal["initial_resources"]},
               "human_acknowledgement": False, "lease_authority": False, "sdk_started": False,
               "automatic_retry": False}
        ack_stamp = hosted_write_original(root / "bootstrap/technical-ready.original.json", transport_json(ack))
        state = hosted_load_bootstrap(root, ack_stamp["sha256"], context, environment)
        require(state["ack"] == ack, "technical bootstrap readback differs before launch")
        argv = [str(python), "-I", "-B", "-X", "utf8", str(gate / HELPER), "--mode", HOSTED_MODE,
                "--authority", admission_id, "--hosted-worker", "--bootstrap-sha256", ack_stamp["sha256"]]
        remaining = budget.remaining(7200) - HOSTED_CONTROL_CLEANUP_SECONDS
        require(remaining > 0, "hosted worker has no original work window plus cleanup")
        terminal.update(worker_launch_intent=True, bootstrap_sha256=ack_stamp["sha256"], worker_argv=argv)
        child_environment = dict(environment, RUSTUP_HOME=str(root / "toolchain/rustup"))
        for name in ("GH_TOKEN", "GITHUB_TOKEN"):
            child_environment.pop(name, None)
        row = observer.command(argv, gate, child_environment, root / "bootstrap/commands", "sdk-worker", remaining,
                               context, (root, observations))
        terminal.update(status="WORKER_EXIT_OBSERVED_NOT_RESULT_ADMISSION", worker=row,
                        worker_started=type(row.get("pid")) is int and row["pid"] > 0)
        require(type(row.get("exit_code")) is int, "hosted worker terminal code absent")
        # An exit0 is still not producer evidence. The encrypted independent
        # consumer rechecks every retained source, list, G0 and selected oracle.
    except (observer.UnknownExecution, custody.UnknownObservation) as error:
        terminal.update(status="UNKNOWN_CONTROLLER_NO_RETRY", error=str(error), uncertain_command=error.command)
    except BaseException as error:
        terminal.update(status="FAIL_CONTROLLER_NO_RETRY", error=type(error).__name__ + ": " + str(error))
    finally:
        terminal["commands"] = observations
        terminal["ended_monotonic_ns"] = time.monotonic_ns()
        terminal["custody_reserve_intact"] = terminal["ended_monotonic_ns"] <= clock["work_deadline_monotonic_ns"]
        if not terminal["custody_reserve_intact"]:
            terminal["status"] = "UNKNOWN_ORIGINAL_RESERVE_CONSUMED_NO_RETRY"
        terminal = hosted_collect_controller(root, context, terminal)
        custody.write_new(root / "controller.original.json", terminal)
        # The only public crossing is a fixed readiness token for ciphertext
        # custody. It cannot claim source/SDK success or human authority.
        if terminal["custody_reserve_intact"] and (root / "clock.original.json").is_file():
            with Path(environment["GITHUB_OUTPUT"]).open("a", encoding="ascii", newline="\n") as stream:
                stream.write("custody_ready=true\n")
    return 124 if terminal["status"].startswith("UNKNOWN") else (
        0 if terminal["status"] == "WORKER_EXIT_OBSERVED_NOT_RESULT_ADMISSION" and terminal["worker"]["exit_code"] == 0 else 1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=MODES, default="windows-g1")
    parser.add_argument("--source-sha", default=FIXED["sha"])
    parser.add_argument("--authority", default="")
    parser.add_argument("--sdk")
    parser.add_argument("--baseline", action="append", default=[], metavar="ROLE=PATH")
    parser.add_argument("--run-root")
    parser.add_argument("--lease")
    parser.add_argument("--admit-only", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--observe-hosted-capacity", action="store_true")
    parser.add_argument("--capacity-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--capacity-deadline-ns", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--hosted-controller", action="store_true")
    parser.add_argument("--hosted-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--hosted-crypto-probe", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--bootstrap-sha256", help=argparse.SUPPRESS)
    parser.add_argument("--transport-action", choices=("preflight", "data", "upload-data-check", "index", "upload-index-check", "descriptor"))
    args = parser.parse_args(argv)
    owned = [args.hosted_controller, args.hosted_worker, args.hosted_crypto_probe, args.transport_action is not None]
    if any(owned):
        require(sum(owned) == 1 and args.mode == HOSTED_MODE and args.source_sha == FIXED["sha"]
                and not args.execute and not args.admit_only and not args.sdk and not args.baseline
                and not args.run_root and not args.lease and not args.observe_hosted_capacity
                and not args.capacity_worker and args.capacity_deadline_ns is None,
                "hosted composition does not accept arbitrary subsets, paths or mixed actions")
        if args.hosted_worker:
            context = request_context(os.environ, HOSTED_MODE, FIXED["sha"], args.authority)
            root = hosted_private_root(os.environ, context)
            sources = hosted_source_plan(root, hosted_purpose(context))
            return run(HOSTED_MODE, FIXED["sha"], args.authority, sources[0][2],
                       {role: path for role, _, path in sources[1:]}, root / "execution",
                       hosted_bootstrap=(root, args.bootstrap_sha256))
        require(args.bootstrap_sha256 is None, "bootstrap digest belongs only to the owned worker")
        if args.hosted_controller: return hosted_controller(args.authority)
        if args.hosted_crypto_probe: return hosted_crypto_probe(args.authority)
        return transport_entry(args.transport_action, args.authority)
    require(args.bootstrap_sha256 is None, "technical bootstrap without its owned worker")
    if args.observe_hosted_capacity or args.capacity_worker:
        require(args.mode == HOSTED_MODE and not args.execute and not args.admit_only
                and not args.authority and not args.sdk and not args.baseline and not args.run_root and not args.lease
                and args.source_sha == FIXED["sha"]
                and not (args.observe_hosted_capacity and args.capacity_worker),
                "capacity-only observation cannot carry SDK execution or authority arguments")
        if args.capacity_worker:
            value = capacity_worker(dict(os.environ), args.capacity_deadline_ns)
            raw = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
            require(len(raw.encode()) <= CAPACITY_MAX_RECORD, "capacity worker record exceeded bound")
            print(raw)
            return 0
        require(args.capacity_deadline_ns is None, "only the observed worker inherits the original deadline")
        return observe_hosted_capacity()
    require(args.capacity_deadline_ns is None, "capacity deadline without its observation mode")
    if not args.execute and not args.admit_only:
        print(json.dumps(validate_catalogue(), sort_keys=True))
        return 0
    require(not (args.execute and args.admit_only), "choose admission or execution, not both")
    if args.admit_only:
        require(args.mode in ("linux-g2", HOSTED_MODE), "local execution requires the real lease file")
        context = request_context(os.environ, args.mode, args.source_sha, args.authority)
        if args.mode == HOSTED_MODE:
            selected = authority(args.authority, args.mode, args.source_sha)
            context["gate_admission"] = verify_hosted_gate(
                Path(__file__).resolve().parents[1], context, selected, Budget(selected["max_total_seconds"]))
            hosted_transport_input(os.environ.get("SDK_FOUNDATION_TRANSPORT", "").encode("utf-8"), context)
            require(context["max_total_seconds"] >= HOSTED_TRANSPORT_RESERVE + 60, "hosted original custody reserve absent")
            print(json.dumps({"status": "REVIEWED_HOSTED_GATE_INPUT_BOUND_NO_SDK", "product_execution": "NOT_RUN",
                              "source_sha": context["github"]["GITHUB_SHA"], "purpose": hosted_purpose(context)}))
            return 0
        print(json.dumps(context, sort_keys=True))
        return 0
    require(args.sdk and args.run_root, "SDK checkout and fresh owned run root are required")
    rows = [x.split("=", 1) for x in args.baseline]
    require(all(len(r) == 2 and all(r) for r in rows) and len({r[0] for r in rows}) == len(rows), "baseline arguments differ")
    return run(args.mode, args.source_sha, args.authority, args.sdk, dict(rows), args.run_root, args.lease)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except custody.VerificationError as error:
        print("SDK Foundation admission rejected: " + str(error), file=sys.stderr)
        raise SystemExit(1)
