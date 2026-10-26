#!/usr/bin/env python3
"""Select SDK Foundation source contracts using the existing command observers.

Known source profiles do not grant execution authority. The authority catalogue
starts empty. Local Windows observations retain their direct-process limitation;
the two Linux native methods are a separate, explicitly admitted selection.
Hosted Windows remains blocked until private incoming and outgoing custody is
composed into reviewed code; a registry entry does not activate that missing seam.
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
            "hosted_windows": {"mode": HOSTED_MODE, "transport": "UNCOMPOSED_BLOCKED",
                               "causal_source_shas": [p["sha"] for p in hosted_causals],
                               "execution": "NOT_RUN", "automatic_dispatch": False}}


def windows_selection(mode):
    return mode in ("windows-g1", HOSTED_MODE)


def hosted_binding(value):
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
    transport = value["private_transport"]
    require(type(transport) is dict and set(transport) == {
                "review_sha256", "source_shas", "incoming", "outgoing", "private_sources_published"}
            and type(transport["review_sha256"]) is str and DIGEST.fullmatch(transport["review_sha256"])
            and transport["source_shas"] == sorted([FIXED["sha"], *(p["sha"] for p in causal_profiles(HOSTED_MODE))])
            and transport["incoming"] == "OWNER_COMPOSED_FIVE_CHECKOUTS"
            and transport["outgoing"] == "OWNER_COMPOSED_PRIVATE_ORIGINALS_CUSTODY"
            and transport["private_sources_published"] is False,
            "private incoming and outgoing source custody require an exact owner review")
    growth = value["cold_growth"]
    parts = {"source_transport", "source_checkouts", "toolchain", "cargo_downloads", "targets",
             "sealed_binary_originals", "temporary_and_logs", "safety"}
    require(type(growth) is dict and set(growth) == {"basis_sha256", "source_shas", "compiled_configurations", "bytes"}
            and type(growth["basis_sha256"]) is str and DIGEST.fullmatch(growth["basis_sha256"])
            and growth["source_shas"] == transport["source_shas"]
            and type(growth["compiled_configurations"]) is int and growth["compiled_configurations"] == 13
            and type(growth["bytes"]) is dict and set(growth["bytes"]) == parts
            and all(type(n) is int and 0 < n <= 256 * 1024**3 for n in growth["bytes"].values())
            and sum(growth["bytes"].values()) <= 512 * 1024**3,
            "cold growth budget is absent, noninteger or belongs to another source/configuration")
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
    binding = hosted_binding(selected["binding"])
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


def require_hosted_transport_composition(context):
    # A future reviewed base must compose actual private input AND output custody.
    # A populated registry or an input boolean cannot substitute for that code.
    require(context["mode"] == HOSTED_MODE, "private transport seam used for another mode")
    raise custody.VerificationError(
        "BLOCKED_PRIVATE_TRANSPORT_UNCOMPOSED: incoming five exact SDK checkouts and "
        "durable private originals custody require reviewed workflow/helper composition; "
        "do not publish private source originals or start SDK producers")


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
    require(type(row) is dict and set(row) == {
        "kind", "mode", "source_sha", "review_sha256", "max_total_seconds", "binding"},
        "reviewed authority shape differs")
    require(row["mode"] == mode and row["source_sha"] == source_sha
            and type(row["review_sha256"]) is str and DIGEST.fullmatch(row["review_sha256"])
            and type(row["max_total_seconds"]) is int and 60 <= row["max_total_seconds"] <= (7200 if windows_selection(mode) else 3600),
            "reviewed authority source or finite budget differs")
    require(row["kind"] == ("local-lease" if mode == "windows-g1" else "github-dispatch")
            and type(row["binding"]) is dict, "authority mode differs")
    if mode == HOSTED_MODE:
        hosted_binding(row["binding"])
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
        binding = hosted_binding(selected["binding"])
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
            **({"runner_label": HOSTED_RUNNER, "image_os": environment["IMAGEOS"],
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


def observe_tools(repo, env, logs, context, call):
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
    for name, candidates in (("python", (sys.executable,)), ("git", ("git",)), ("node", ("node",)), ("bash", ("bash",))):
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


def run(mode, source_sha, admission_id, sdk, baseline_paths, run_root, lease=None, environment=None):
    environment = dict(os.environ) if environment is None else dict(environment)
    context = request_context(environment, mode, source_sha, admission_id)
    selected = authority(admission_id, mode, source_sha)
    if mode == HOSTED_MODE:
        # No catalogue or CLI value activates incomplete private transport.
        require_hosted_transport_composition(context)
    selected_causals = causal_profiles(mode)
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
    budget = Budget(context["max_total_seconds"])
    # Exclusive root is also the one-shot claim for this reviewed local lease.
    run_root.mkdir(mode=0o700, parents=False, exist_ok=False)
    evidence = run_root / "evidence"; evidence.mkdir(mode=0o700)
    logs = evidence / "commands"; logs.mkdir(mode=0o700)
    report = {"schema": SCHEMA, "status": "FAIL", "context": context, "phase": "gate-source",
              "started_utc": custody.now(), "budget_seconds": context["max_total_seconds"],
              "original_deadline_monotonic_ns": budget.deadline, "lease": lease_identity,
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
            sample = hosted_growth_observation(resources, context, report["initial_resources"]["disk"])
            report.setdefault("hosted_growth_samples", []).append(sample)
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
            report["hosted_cold_growth"] = hosted_growth_observation(report["initial_resources"], context)
        tools, env = observe_tools(gate, env, logs, context, guarded)
        report["tools"] = tools
        if mode == "windows-g1":
            report["phase"] = "local-gate-g0"
            report["gate_g0"] = local_gate_g0(gate, gate_snapshot, tools, env, logs, context, guarded)
        else:
            report["gate_g0"] = {"scope": "EXACT_ACTIONS_GATE_JOB_REQUIRED_BY_NEEDS", "product_credit": "NONE"}
        profiles = [("fixed", sdk, FIXED, None)]
        if windows_selection(mode):
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
        execute_selection(states, selection_mode, tools, env, evidence, budget, guarded, report)
        source_unchanged(gate, gate_snapshot)
        producer.verify_commands(evidence, observations)
        if mode == "windows-g1":
            original_controls = report["gate_g0"]["controls"]
            refreshed = admit_gate_controls(Path(original_controls["folder"]) / "gate-unittest.json", gate_snapshot, tools["python"], context)
            require(refreshed == original_controls, "gate G0 original changed after product observation")
        report["status"] = ("PASS_HOSTED_WINDOWS_G1_SELECTED_ROOT_OBSERVATIONS" if mode == HOSTED_MODE else
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
        try:
            custody.write_new(evidence / "receipt.json", report)
        except BaseException as error:
            report.update(status="UNKNOWN_CUSTODY_NO_RETRY", receipt_error=type(error).__name__ + ": " + str(error))
            # Do not overwrite or retry a possibly partial terminal receipt.
            print(json.dumps(report, sort_keys=True))
            return 124
    print(json.dumps({"status": report["status"], "receipt": str(evidence / "receipt.json"),
                      "product_source_sha": source_sha, "formal_acceptance": False}, sort_keys=True))
    return 0 if report["status"].startswith("PASS_") else (124 if report["status"].startswith("UNKNOWN") else 1)


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
    args = parser.parse_args(argv)
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
            require_hosted_transport_composition(context)
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
