"""Invoke the existing Blender producer once across three owner contexts.

This helper returns private observations to the existing evidence-custody owner.
It creates no public acceptance summary and does not grant native consent.
"""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from mlt_official_runtime_intake import capture_regular
from native_mlt_loaded_module import _verified_binary
from native_blender_product_pixels import observe_product_surface_pixels
from native_blender_original_evidence import (
    COMMANDS, HEX, MAX_JSON, LiveBlenderObserver, _plain_owned_directory,
    _require_empty_private_append_custody,
    captured_json, live_observation_scope, new_json, new_original_runtime_paths,
    require, same_original_chain, unique_json,
)

PRODUCT_COMMANDS = COMMANDS | frozenset(
    "driver.blender." + name for name in (
        "introspect.summary", "composition.inspect", "composition.plan",
        "composition.apply", "composition.verify", "composition.measure",
        "render.settings", "render", "animation.keyframe.insert",
        "scene.marker.add", "node.add", "node.remove", "node.link", "material.assign",
    )
)


def run_product_contexts(
    app: Any, *, owner: Path, recipe_path: Path, request_id: str,
    annotation_body: str, record: dict, ffmpeg: Path, ffmpeg_sha256: str,
    expected_recipe_sha256: str,
) -> dict:
    """One explicit source cycle; the caller owns sealing this complete record."""
    require(type(record) is dict and not record, "product_context_record_must_be_new")
    _plain_owned_directory(owner)
    require(type(expected_recipe_sha256) is str
            and HEX.fullmatch(expected_recipe_sha256) is not None,
            "product_context_original_recipe_digest_invalid")
    require(type(request_id) is str and 1 <= len(request_id) <= 48
            and request_id.isascii()
            and all(char.isalnum() or char in "-_" for char in request_id),
            "product_context_request_identity_invalid")
    record.update({
        "schema": "motionwright.blender.product-context-capture/1",
        "automatic_native_retry": False, "human_acceptance": False,
        "source_sha": app.GITHUB_SHA, "semwright_sha": app.PIN,
        "seed_command": {}, "cycle_command": {}, "contexts": [],
        "secondary_errors": [],
    })

    def secondary(stage: str, error: BaseException) -> None:
        record["secondary_errors"].append({
            "stage": stage, "type": type(error).__name__, "message": str(error),
            "automatic_retry": False,
        })

    observer_custody = SimpleNamespace(secondary=secondary)
    phases = ("create_original", "edit_original", "verify_edited")
    contexts: list[dict] = []
    phase_records: list[dict] = record["contexts"]
    original = None
    try:
        record["preflight"] = app.exact_inputs()
        require(isinstance(ffmpeg, Path) and ffmpeg.is_absolute()
                and ffmpeg.resolve(strict=True) == ffmpeg
                and not any(path.is_symlink() for path in (ffmpeg, *ffmpeg.parents)),
                "product_context_decoder_path_invalid")
        require(type(ffmpeg_sha256) is str, "product_context_decoder_digest_invalid")
        with _verified_binary(ffmpeg, ffmpeg_sha256, 768 * 1024 * 1024):
            pass
        record["decoder_preflight"] = {"path": str(ffmpeg), "sha256": ffmpeg_sha256,
                                       "native_render_performed": False}
        # These helpers are shared with the existing source producer. Short
        # per-context socket roots, mounts, binary pins and policies are unchanged.
        runtime_paths = new_original_runtime_paths(owner)
        seed_root = owner / "seed-context"
        seed_root.mkdir(mode=0o700)
        seed_paths = app.new_owner_paths(seed_root, runtime_path=runtime_paths["seed-context"])
        seed_env = app.owned_environment(seed_paths)
        seed_output = owner / "seed"
        seed_output.mkdir(mode=0o700)
        database = owner / "motionwright.sqlite3"
        seeded = captured_json(
            [str(app.MW_BIN), "seed-product", str(database), str(recipe_path), str(seed_output)],
            env=seed_env, prefix=owner / "seed-command", record=record["seed_command"],
            timeout=60,
        )
        record["seeded"] = seeded
        require(seeded.get("schema") == "motionwright.blender.product-scene-seed/1"
                and seeded.get("native_execution") == "NOT_RUN"
                and seeded.get("recipe_cas_verified") is True
                and seeded.get("sqlite_reopened") is True
                and seeded.get("delivery_profile_selected") is False
                and seeded.get("source_sha256") == expected_recipe_sha256,
                "product_context_seed_not_bound_to_saved_recipe")
        record["seed_original_digest_verified"] = True
        record["expected_recipe_sha256"] = expected_recipe_sha256
        request = {
            "project_id": seeded["project_id"], "expected": seeded["expected"],
            "scene_id": seeded["scene_id"], "capsule_id": seeded["capsule_id"],
            "request_id": request_id, "annotation_body": annotation_body,
        }
        attempt = owner / "cycle-attempt.json"
        new_json(attempt, request)
        record["attempt"] = request
        evidence_path = owner / "cycle-evidence.json"
        with ExitStack() as native_stack:
            for phase in phases:
                phase_root = owner / phase
                phase_root.mkdir(mode=0o700)
                root = phase_root / "context"
                root.mkdir(mode=0o700)
                paths = app.new_owner_paths(root, runtime_path=runtime_paths[phase])
                env = app.owned_environment(paths)
                evidence = phase_root / "evidence"
                evidence.mkdir(mode=0o700)
                phase_record = {"phase": phase, "observer": None, "context_cleanup": None}
                phase_records.append(phase_record)
                context = native_stack.enter_context(
                    app.blender_context(paths, env, evidence, seeded, required=set(PRODUCT_COMMANDS)))
                contexts.append(context)
                phase_record["runtime_bindings"] = context["runtime_bindings"]
                phase_record["capabilities_checked"] = context["capabilities_checked"]
            with ExitStack() as observer_stack:
                finishers = []
                for phase, context, phase_record in zip(phases, contexts, phase_records, strict=True):
                    observer = LiveBlenderObserver(
                        context["daemon"].pid, app.BLENDER_BIN.resolve(strict=True),
                        context["runtime_bindings"]["blender_sha256"],
                        context["paths"]["workspace"],
                    )
                    finishers.append(observer_stack.enter_context(
                        live_observation_scope(observer, phase_record, observer_custody, phase)))
                returned = captured_json(
                    [str(app.MW_BIN), "product-cycle", str(database),
                     *(str(context["connection"]) for context in contexts),
                     str(attempt), str(evidence_path)],
                    env=seed_env, prefix=owner / "native-cycle",
                    record=record["cycle_command"], timeout=2700,
                )
                record["native_returned"] = returned
                observations = [finish() for finish in finishers]
                record["distinct_context_chain"] = same_original_chain(observations)
                raw, binding = capture_regular(evidence_path, MAX_JSON)
                persisted = unique_json(raw)
                record["private_evidence_binding"] = binding
                private_custody = persisted.pop("coordinator_append_custody", None)
                record["coordinator_append_custody"] = private_custody
                expected_persisted = dict(returned)
                expected_persisted["phase_contract_verified"] = False
                require(persisted == expected_persisted
                        and type(private_custody) is dict
                        and set(private_custody) == {"create", "edit", "verify"}
                        and returned.get("schema") == "motionwright.blender.product-cycle-evidence/1"
                        and returned.get("phase_contract_verified") is True
                        and returned.get("request") == request
                        and returned.get("native_terminal", {}).get("status") == "ok",
                        "product_context_private_publication_not_bound_to_original_terminal")
                for scope in ("create", "edit", "verify"):
                    _require_empty_private_append_custody({
                        "coordinator_append_custody": private_custody[scope]})
                record["caller_publication_bound"] = True
                pixel_root = owner / "decoded-product-pixels"
                pixel_root.mkdir(mode=0o700)
                pixel_record = {}
                record["independent_surface_pixels"] = pixel_record
                observation = observe_product_surface_pixels(
                    seeded, returned,
                    create_root=contexts[0]["paths"]["workspace"],
                    verify_root=contexts[2]["paths"]["workspace"],
                    evidence_root=pixel_root,
                    native_evidence_sha256=hashlib.sha256(raw).hexdigest(),
                    ffmpeg=ffmpeg, executable_sha256=ffmpeg_sha256, record=pixel_record,
                )
                require(observation["decoded_pixel_difference_verified"] is True,
                        "product_context_decoded_pixels_did_not_change")
                # The native terminal and its original narrow flags are preserved.
                return returned
    except BaseException as error:
        original = error
        record["original_error"] = {
            "type": type(error).__name__, "message": str(error),
            "automatic_retry": False,
        }
        raise
    finally:
        for context, phase_record in zip(contexts, phase_records):
            phase_record["context_cleanup"] = {
                "errors": list(context["cleanup_errors"]),
                "daemon_returncode": context.get("daemon_returncode"),
            }
        if original is not None:
            # Preserve errors in memory even when writing private files failed.
            record["producer_terminal_verified"] = False
