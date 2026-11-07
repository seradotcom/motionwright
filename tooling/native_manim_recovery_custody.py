#!/usr/bin/env python3
"""Admit four new original phases without reclassifying either historical run."""
from __future__ import annotations
import ci_private_evidence as engine
from native_manim_camera_evidence import read, read_object, require, sha
from native_manim_recovery_cycle import PHASES, scopes, verify_phase
from native_manim_recovery_gate import check_runtime, check_source_gate
from native_manim_resume_cycle import admitted_originals
from native_manim_resume_transfer import ORIGIN, RECOVERY_SCENARIO, SOURCE_RECOVERY, selected_scenario


def verify_cycle(current):
    from native_manim_resume_custody import verify_outer
    require(selected_scenario() == RECOVERY_SCENARIO, "recovery_runtime_selected_custody")
    root, sealed, public = scopes(current)
    gate, gate_sha = check_source_gate(current)
    runtime = check_runtime(current)
    require(gate["runtime"] == runtime, "recovery_runtime_unchanged_through_custody")
    admitted_originals(current, frozen=True)
    outer = verify_outer(current)
    phases = []
    for name in PHASES:
        evidence = root / name
        observation = read_object(evidence / "result.json")
        require(observation.get("phase") == name and observation.get("context") == current
                and observation.get("runtime_binding") == runtime
                and read_object(evidence / "frozen-runtime-binding.json") == runtime,
                "recovery_runtime_phase_context_and_environment")
        verify_phase({"current": current, "evidence": evidence, "phase_observation": observation,
                      "native_path": evidence / "motionwright-manim-evidence.json",
                      "sealed_originals": sealed / (name + ".mwseal")}, current)
        identities = read_object(evidence / "native-input-identities.json")
        require(identities.get("motionwright_sha") == current["source_sha"]
                and identities.get("semwright_sha") == current["semwright_sha"]
                and identities.get("binaries", {}).get("native_example") == gate["compiled_binary_after"]["sha256"],
                "recovery_runtime_tested_example_used_for_every_phase")
        phases.append({"phase": name, "result_sha256": sha(read(evidence / "result.json")),
                       "ciphertext_sha256": sha(read(sealed / (name + ".mwseal"), 256 * 1024 * 1024))})
    result = read_object(root / "cycle-result.json")
    require(result.get("schema") == "motionwright.manim.recovery-runtime.cycle-observation/1"
            and result.get("status") == "NEW_REFERENCE_TWO_PLUS_EDITED_TWO_REQUIRES_INDEPENDENT_CUSTODY"
            and result.get("context") == current and result.get("origin_context") == ORIGIN
            and result.get("phase_results") == phases and result.get("native_effect_invocations") == 4
            and result.get("additional_reference_renders") == 2 and result.get("source_recovery") == SOURCE_RECOVERY
            and result.get("runtime_binding") == runtime and result.get("project_changes_applied") == 0
            and result.get("original_edit_report_recreated") is False
            and result.get("equivalence_to_old_runtime") is False and result.get("old_result_replaced") is False
            and result.get("formal_G2_granted") is False and result.get("final_F_association") == "PENDING"
            and result.get("automatic_retry") is False,
            "recovery_runtime_cannot_promote_old_results_or_skip_final_custody")
    pixels_raw = read(root / "reopen-edited/recovery-runtime-pixels.json")
    pixels = engine.strict(pixels_raw)
    require(result.get("pixel_cycle_sha256") == sha(pixels_raw)
            and pixels.get("frame_count_per_phase") == 24
            and pixels.get("original_reopen_all_frames_equal") is True
            and pixels.get("edited_reopen_all_frames_equal") is True
            and pixels.get("saved_text_change_observed_in_all_frames") is True
            and pixels.get("original_media_as_edited_negative", {}).get("status") == "EXPECTED_PIXEL_REJECTION",
            "recovery_runtime_four_physical_pixel_phases")
    public.mkdir(mode=0o700, exist_ok=False)
    summary = {"schema": "motionwright.manim.recovery-runtime.public-summary/1", "status": result["status"],
               "context": current, "origin_context": ORIGIN, "native_effect_invocations": 4,
               "additional_reference_renders": 2, "project_changes_applied": 0,
               "source_gate_sha256": gate_sha, "actual_rust_focals_passed": 10,
               "runtime_binding": runtime, "cycle_result_sha256": sha(read(root / "cycle-result.json")),
               "outer_ciphertext_sha256": outer["ciphertext_sha256"], "phase_results": phases,
               "old_result_replaced": False, "equivalence_to_old_runtime": False,
               "independent_custody": "PENDING", "final_F_association": "PENDING",
               "formal_G2_granted": False, "human_acceptance": False, "automatic_retry": False}
    engine.new_json(public / "summary.json", summary)
    return summary
