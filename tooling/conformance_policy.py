"""Validate source-scoped technical assessments without granting release authority.

These checks validate recorded identity, scope and integrity. They do not authenticate
a reviewer or prove that an external execution happened; reviewers inspect that evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
import zipfile
from datetime import datetime
from pathlib import Path, PureWindowsPath

SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
RESULTS = {"pass", "partial", "fail", "not_run", "inconclusive", "stale"}
KINDS = {"source", "unit", "synthetic", "integration", "native", "installed"}
IMPLEMENTATIONS = {"implemented", "partial", "absent", "not_assessed"}


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _strings(value, label, nonempty=False):
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise ValueError(f"{label} must be a list of nonempty strings")
    if len(set(value)) != len(value) or (nonempty and not value):
        raise ValueError(f"{label} must contain distinct entries")
    return value


def _hex(value, pattern, label):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{label} has an invalid identity")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field: " + key)
        result[key] = value
    return result


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_pairs)


def public_file(root, pointer, *, allow_missing=False):
    if not isinstance(pointer, str) or not pointer.strip() or "\\" in pointer:
        raise ValueError("evidence must use a nonempty repository-relative path")
    relative = Path(pointer)
    windows = PureWindowsPath(pointer)
    if relative.is_absolute() or windows.drive or ".." in relative.parts or ":" in pointer:
        raise ValueError("evidence path escapes the repository")
    root = Path(root).resolve()
    candidate = root / relative
    current = root
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError as error:
            # Only source/oracle references opt into absence. Every existing
            # ancestor has already passed the same path and link checks.
            if allow_missing:
                return None
            raise ValueError("missing repository evidence: " + pointer) from error
        except OSError as error:
            raise ValueError("repository evidence could not be inspected: " + pointer) from error
        if current.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("linked evidence is not admitted: " + pointer)
        if current != candidate and not stat.S_ISDIR(info.st_mode):
            raise ValueError("evidence path ancestor must be a directory: " + pointer)
    try:
        candidate.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as error:
        raise ValueError("evidence escaped the repository") from error
    if not candidate.is_file():
        raise ValueError("evidence must be a regular file: " + pointer)
    return candidate


def validate_archive_member(path, artifact):
    if "member" not in artifact and "member_sha256" not in artifact:
        return
    member = artifact.get("member")
    if (not isinstance(member, str) or not member or "\\\\" in member
            or PureWindowsPath(member).drive or ":" in member
            or any(part in {"", ".", ".."} for part in member.split("/"))):
        raise ValueError("archive member must have an unambiguous relative identity")
    expected = _hex(artifact.get("member_sha256"), DIGEST, "archive member digest")
    try:
        with zipfile.ZipFile(path) as archive:
            matches = [item for item in archive.infolist() if item.filename == member]
            if len(matches) != 1 or matches[0].is_dir():
                raise ValueError("archive member is missing, duplicated or not a file")
            actual = hashlib.sha256()
            with archive.open(matches[0]) as stream:
                while chunk := stream.read(1024 * 1024):
                    actual.update(chunk)
            if actual.hexdigest() != expected:
                raise ValueError("archive member bytes do not match their digest")
    except (zipfile.BadZipFile, OSError, KeyError, RuntimeError) as error:
        raise ValueError("archive member cannot be verified") from error


def source_digest(path):
    """Hash canonical UTF-8 Git text; runtime media and logs use exact raw bytes."""
    data = Path(path).read_bytes()
    data.decode("utf-8", errors="strict")
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()



def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def validate_sufficiency_reviews(data, records):
    catalog = data["criteria"]
    profiles = _object(data.get("verification_profiles"), "canonical verification profiles")
    if not set(profiles).issubset(catalog):
        raise ValueError("verification profiles must refer to canonical criteria")
    for profile in profiles.values():
        _object(profile, "verification profile")
        obligations = _object(profile.get("obligations"), "canonical verification obligations")
        if not obligations:
            raise ValueError("a verification profile must identify its obligations")
        for obligation_id, obligation in obligations.items():
            if not isinstance(obligation_id, str) or not obligation_id:
                raise ValueError("invalid canonical obligation identity")
            _object(obligation, "verification obligation")
            _strings(obligation.get("execution_profiles"), "required execution profiles", nonempty=True)
            if not set(obligation["execution_profiles"]).issubset(data["execution_profiles"]):
                raise ValueError("canonical obligation names an unknown execution profile")
    reviews = data.get("sufficiency_reviews")
    if not isinstance(reviews, list):
        raise ValueError("sufficiency reviews must be explicit")
    seen = set()
    stale_reviews = {}
    for review in reviews:
        _object(review, "sufficiency review")
        review_id = review.get("id")
        if not isinstance(review_id, str) or not review_id or review_id in seen:
            raise ValueError("sufficiency review identities must be unique")
        seen.add(review_id)
        if type(review.get("schema_version")) is not int or review["schema_version"] != 1:
            raise ValueError("invalid sufficiency review schema")
        criterion = review.get("criterion_id")
        if criterion not in profiles or review.get("source_clause_sha256") != catalog[criterion]:
            raise ValueError("sufficiency review changed its canonical criterion")
        review_source = source_identity(review.get("source"))
        stale_reasons = []
        if review_source != data["source"]:
            stale_reasons.append("sufficiency review source is historical; reassess stale evidence")
        profile = profiles[criterion]
        if review.get("oracle_profile_sha256") != canonical_digest(profile):
            raise ValueError("sufficiency review uses a stale verification profile")
        reviewer = _object(review.get("reviewer"), "sufficiency reviewer")
        if reviewer.get("kind") not in {"automated_technical", "human", "mixed"}:
            raise ValueError("invalid sufficiency reviewer kind")
        if not isinstance(reviewer.get("reference"), str) or not reviewer["reference"].strip():
            raise ValueError("sufficiency reviewer reference must be explicit")
        if reviewer.get("identity_authentication") != "not_asserted":
            raise ValueError("this verifier does not authenticate a reviewer")
        try:
            timestamp = datetime.fromisoformat(review["reviewed_at_utc"].replace("Z", "+00:00"))
            if timestamp.utcoffset() is None:
                raise ValueError("timezone missing")
        except (KeyError, TypeError, AttributeError, ValueError) as error:
            raise ValueError("sufficiency review needs an actual zoned timestamp") from error
        bindings = _object(review.get("evidence_record_sha256"), "reviewed evidence records")
        for record_id, record_digest in bindings.items():
            if record_id not in records or record_digest != canonical_digest(records[record_id]):
                raise ValueError("evidence record changed after sufficiency review")
            if records[record_id]["source"] != review_source:
                raise ValueError("sufficiency review and reviewed record refer to different sources")
            stale_reasons.extend(data.get("_stale_evidence_records", {}).get(record_id, []))
        observed = review.get("obligations")
        if not isinstance(observed, list):
            raise ValueError("review obligations must be a list")
        observations = {}
        for item in observed:
            _object(item, "review obligation")
            identity = item.get("id")
            if not isinstance(identity, str) or identity in observations:
                raise ValueError("review obligations must have unique identities")
            observations[identity] = item
        if set(observations) != set(profile["obligations"]):
            raise ValueError("sufficiency review dropped or added canonical obligations")
        for identity, item in observations.items():
            if item.get("status") not in {"pass", "missing", "fail", "inconclusive"}:
                raise ValueError("invalid sufficiency obligation status")
            evidence_ids = _strings(item.get("evidence_ids"), "sufficiency evidence")
            if not set(evidence_ids).issubset(bindings):
                raise ValueError("sufficiency evidence lacks reviewed record digests")
            if not isinstance(item.get("basis"), str) or not item["basis"].strip():
                raise ValueError("sufficiency review must explain its observed basis")
            if item["status"] == "pass":
                if not evidence_ids:
                    raise ValueError("sufficient obligation requires evidence")
                accepted_profiles = set(profile["obligations"][identity]["execution_profiles"])
                matching = False
                for evidence_id in evidence_ids:
                    record = records[evidence_id]
                    if criterion not in record["criterion_ids"] or record["source"] != review_source or record["execution"]["status"] != "pass":
                        raise ValueError("sufficiency cannot use unrelated, historical or failed execution")
                    matching |= record["execution"].get("profile") in accepted_profiles
                if not matching:
                    raise ValueError("execution surface does not satisfy its canonical obligation")
        if review.get("decision") not in {"sufficient", "insufficient", "inconclusive"}:
            raise ValueError("invalid sufficiency decision")
        _strings(review.get("limitations"), "sufficiency limitations")
        if review["decision"] == "sufficient" and any(item["status"] != "pass" for item in observations.values()):
            raise ValueError("sufficient review cannot retain missing obligations")
        if stale_reasons:
            stale_reviews[review_id] = sorted(set(stale_reasons))
    # Currency is derived separately; retain the reviewed record bindings and
    # every original review field without relabelling its historical decision.
    data["_stale_sufficiency_reviews"] = stale_reviews

def source_identity(source):
    source = _object(source, "source")
    if source.get("digest_kind") != "git-text-lf":
        raise ValueError("source identities require explicit Git text normalization")
    _hex(source.get("motionwright_sha"), SHA, "Motionwright source")
    _hex(source.get("source_lock_sha256"), DIGEST, "source lock")
    return source


def load_evidence_index(root, descriptor):
    descriptor = _object(descriptor, "evidence index")
    path = public_file(root, descriptor.get("path"))
    expected = _hex(descriptor.get("sha256"), DIGEST, "evidence index digest")
    if descriptor.get("digest_kind") != "git-text-lf":
        raise ValueError("tracked evidence index requires explicit Git text normalization")
    if source_digest(path) != expected:
        raise ValueError("evidence index bytes do not match their digest")
    data = _object(load_json(path), "evidence index")
    if data.get("schema") != "motionwright.technical-evidence/1":
        raise ValueError("unknown technical evidence schema")
    source_identity(data.get("source"))
    lock = public_file(root, "SOURCE_LOCK.json")
    if source_digest(lock) != data["source"]["source_lock_sha256"]:
        raise ValueError("assessed source lock is stale for this checkout")
    catalog = _object(data.get("criteria"), "criterion catalog")
    for criterion_id, clause_digest in catalog.items():
        if not isinstance(criterion_id, str) or "#/" not in criterion_id:
            raise ValueError("invalid canonical criterion identity")
        _hex(clause_digest, DIGEST, "canonical clause digest")
    contracts = _object(data.get("contracts"), "canonical contract profiles")
    owners = {identity.split("#/", 1)[0] for identity in catalog}
    if set(contracts) != owners:
        raise ValueError("contract profiles must preserve every canonical row")
    for owner, profile in contracts.items():
        _object(profile, "contract profile")
        _hex(profile.get("source_record_sha256"), DIGEST, "contract source record")
        if type(profile.get("human_review_required")) is not bool:
            raise ValueError("canonical human obligation must be boolean")
        human_ids = _strings(profile.get("human_criterion_ids"), "canonical human criteria")
        owned = {identity for identity in catalog if identity.startswith(owner + "#/")}
        if not set(human_ids).issubset(owned) or bool(human_ids) != profile["human_review_required"]:
            raise ValueError("canonical human obligation must identify its exact criteria")
        reasons = _strings(profile.get("not_applicable_reason_codes"), "canonical applicability rules")
        if profile.get("scope") not in {"required", "optional", "conditional"}:
            raise ValueError("invalid canonical applicability scope")
        if profile["scope"] == "required" and reasons:
            raise ValueError("required criteria cannot be waived as not applicable")
    execution_profiles = _object(data.get("execution_profiles"), "execution surface profiles")
    for identity, profile in execution_profiles.items():
        if not isinstance(identity, str) or not identity or not isinstance(profile, dict) or profile.get("kind") not in KINDS:
            raise ValueError("invalid execution surface profile")
    records = data.get("records")
    if not isinstance(records, list):
        raise ValueError("evidence records must be a list")
    by_id = {}
    stale_records = {}
    for record in records:
        _object(record, "evidence record")
        identity = record.get("id")
        if not isinstance(identity, str) or not identity or identity in by_id:
            raise ValueError("evidence record identities must be unique")
        source_identity(record.get("source"))
        stale_inputs = []
        covered = set(_strings(record.get("criterion_ids"), "evidence criteria", nonempty=True))
        complete = set(_strings(record.get("complete_criterion_ids"), "complete evidence criteria"))
        if not covered.issubset(catalog) or not complete.issubset(covered):
            raise ValueError("evidence completeness must refer to declared canonical criteria")
        inputs = record.get("source_inputs")
        if not isinstance(inputs, list) or not inputs:
            raise ValueError("execution must bind its product source inputs")
        for source_input in inputs:
            _object(source_input, "source input")
            expected_input = _hex(source_input.get("sha256"), DIGEST, "product source digest")
            path = public_file(root, source_input.get("path"), allow_missing=True)
            if path is None:
                stale_inputs.append("product source input missing; reassess stale evidence")
            elif source_digest(path) != expected_input:
                stale_inputs.append("product source input changed; reassess stale evidence")
        _strings(record.get("assertions"), "observed assertions", nonempty=True)
        execution = _object(record.get("execution"), "execution")
        if execution.get("kind") not in KINDS or execution.get("status") not in RESULTS:
            raise ValueError("invalid execution kind or result")
        profile = execution_profiles.get(execution.get("profile"))
        if profile is None or profile["kind"] != execution["kind"]:
            raise ValueError("execution profile and kind are inconsistent")
        for field in ("passed", "failed", "skipped"):
            count = execution.get(field)
            if type(count) is not int or count < 0:
                raise ValueError("execution counts must be nonnegative integers")
        if execution["status"] == "pass" and (
            execution["passed"] == 0 or execution["failed"] or execution["skipped"]
        ):
            raise ValueError("passing evidence needs executed assertions and no failures or skips")
        definitions = record.get("definitions")
        if not isinstance(definitions, list) or not definitions:
            raise ValueError("execution needs exact test or oracle definitions")
        for definition in definitions:
            _object(definition, "definition")
            expected_definition = _hex(definition.get("sha256"), DIGEST, "definition digest")
            definition_path = public_file(root, definition.get("path"), allow_missing=True)
            if definition_path is None:
                stale_inputs.append("execution definition missing; reassess stale evidence")
            elif source_digest(definition_path) != expected_definition:
                stale_inputs.append("execution definition changed; reassess stale evidence")
        artifacts = record.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise ValueError("execution needs inspectable output artifacts")
        for artifact in artifacts:
            _object(artifact, "artifact")
            _hex(artifact.get("sha256"), DIGEST, "artifact digest")
            pointer = artifact.get("path")
            url = artifact.get("url")
            if pointer is not None:
                actual = public_file(root, pointer)
                if hashlib.sha256(actual.read_bytes()).hexdigest() != artifact["sha256"]:
                    raise ValueError("artifact bytes do not match their digest")
                validate_archive_member(actual, artifact)
            else:
                raise ValueError("execution needs retained artifact bytes; a URL alone is not verified evidence")
        by_id[identity] = record
        if stale_inputs:
            stale_records[identity] = sorted(set(stale_inputs))
    # Derived currency is not inserted into records or their hashed content.
    # Historical executions remain recorded; they cannot support complete claims.
    data["_stale_evidence_records"] = stale_records
    human_reviews = data.get("human_reviews")
    if not isinstance(human_reviews, list):
        raise ValueError("human review records must be explicit")
    human_ids = set()
    artifact_digests = {artifact["sha256"] for record in records for artifact in record["artifacts"]}
    for report in human_reviews:
        _object(report, "human review report")
        report_id = report.get("id")
        if not isinstance(report_id, str) or not report_id or report_id in human_ids:
            raise ValueError("human review report identities must be unique")
        human_ids.add(report_id)
        source_identity(report.get("source"))
        if report.get("status") != "completed" or report.get("reviewer_kind") != "human":
            raise ValueError("a completed human review report must identify its method")
        _hex(report.get("reviewer_reference_sha256"), DIGEST, "private reviewer reference")
        report_criteria = set(_strings(report.get("criterion_ids"), "human review scope", nonempty=True))
        if not report_criteria.issubset(catalog):
            raise ValueError("human review scope is outside the canonical contract")
        observed = set(_strings(report.get("observed_artifact_sha256"), "human observed artifacts", nonempty=True))
        if not observed.issubset(artifact_digests):
            raise ValueError("human review must bind the artifacts actually retained")
        proof = _object(report.get("record"), "human review record")
        proof_path = public_file(root, proof.get("path"))
        if hashlib.sha256(proof_path.read_bytes()).hexdigest() != _hex(proof.get("sha256"), DIGEST, "human review record digest"):
            raise ValueError("human review record bytes changed")
    validate_sufficiency_reviews(data, by_id)
    return data, by_id


def validate_assessment(assessment, row_id, index, records):
    assessment = _object(assessment, row_id + " technical assessment")
    if type(assessment.get("schema_version")) is not int or assessment["schema_version"] != 1:
        raise ValueError("unknown technical assessment schema")
    source = source_identity(assessment.get("source"))
    if source != index["source"]:
        raise ValueError("assessment and evidence index refer to different source snapshots")
    implementation = _object(assessment.get("implementation"), "implementation")
    if implementation.get("status") not in IMPLEMENTATIONS:
        raise ValueError("invalid implementation assessment")
    integration = _object(assessment.get("integration"), "integration")
    if integration.get("status") not in {"main", "branch", "not_located"}:
        raise ValueError("invalid integration assessment")
    if integration.get("source_sha") != source["motionwright_sha"]:
        raise ValueError("integration must identify the assessed source")
    verification = _object(assessment.get("technical_verification"), "technical verification")
    if verification.get("status") not in RESULTS:
        raise ValueError("invalid technical verification result")
    kinds = _strings(verification.get("execution_kinds"), "execution kinds")
    if any(kind not in KINDS for kind in kinds):
        raise ValueError("invalid execution kind")
    cited = set(_strings(verification.get("evidence_ids"), "technical evidence"))
    criteria = assessment.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise ValueError("technical assessment must preserve criterion-level coverage")
    seen = set()
    used = set()
    pending = set()
    for criterion in criteria:
        _object(criterion, "criterion")
        identity = criterion.get("id")
        if not isinstance(identity, str) or not identity.startswith(row_id + "#/") or identity in seen:
            raise ValueError("criterion identities must belong to their row and be unique")
        seen.add(identity)
        _hex(criterion.get("source_clause_sha256"), DIGEST, "source clause")
        if index["criteria"].get(identity) != criterion["source_clause_sha256"]:
            raise ValueError("criterion differs from its preserved canonical source")
        status = criterion.get("status")
        if status not in RESULTS:
            raise ValueError("invalid criterion result")
        ids = _strings(criterion.get("evidence_ids"), "criterion evidence")
        missing = _strings(criterion.get("missing_assertions"), "missing assertions")
        used.update(ids)
        if status != "pass":
            pending.add(identity)
            if not missing:
                raise ValueError("incomplete criterion must identify its missing assertions")
        elif not ids or missing:
            raise ValueError("passing criterion needs evidence and no missing assertions")
        for evidence_id in ids:
            if evidence_id not in records:
                raise ValueError("unknown scoped evidence: " + evidence_id)
            record = records[evidence_id]
            if status == "pass" and evidence_id in index.get("_stale_evidence_records", {}):
                raise ValueError("; ".join(index["_stale_evidence_records"][evidence_id]))
            if identity not in record["criterion_ids"]:
                raise ValueError("evidence does not cover the referenced criterion")
            if status == "pass" and (
                record["source"] != source or record["execution"]["status"] != "pass"
            ):
                raise ValueError("historical, failed or incomplete execution cannot close a criterion")
        if status == "pass" and not any(identity in records.get(evidence_id, {}).get("complete_criterion_ids", []) for evidence_id in ids):
            raise ValueError("scoped evidence does not establish a complete criterion")
        if status == "pass":
            sufficient = [
                item for item in index["sufficiency_reviews"]
                if item["criterion_id"] == identity and item["decision"] == "sufficient"
                and item["source"] == source
                and item["id"] not in index.get("_stale_sufficiency_reviews", {})
                and set(item["evidence_record_sha256"]).issubset(ids)
            ]
            if not sufficient:
                raise ValueError("complete criterion needs a separate sufficiency review")
    expected = {identity for identity in index["criteria"] if identity.startswith(row_id + "#/")}
    if seen != expected:
        raise ValueError("assessment dropped or added canonical criteria")
    actual_kinds = {records[evidence_id]["execution"]["kind"] for evidence_id in used}
    if set(kinds) != actual_kinds:
        raise ValueError("execution kinds must match the cited evidence exactly")
    if cited != used:
        raise ValueError("technical evidence summary differs from criterion evidence")
    if verification["status"] == "pass" and (
        pending or implementation["status"] != "implemented" or integration["status"] != "main"
    ):
        raise ValueError("complete technical verification requires all criteria on integrated implementation")
    review = _object(assessment.get("review"), "review")
    if review.get("method") not in {"automated", "manual", "hybrid"}:
        raise ValueError("invalid review method")
    matching_reviewers = {"automated": "automated_technical", "manual": "human", "hybrid": "mixed"}
    if review.get("reviewer_kind") != matching_reviewers[review["method"]]:
        raise ValueError("review method and reviewer kind are inconsistent")
    closure = _object(assessment.get("contract_acceptance"), "contract acceptance")
    if closure.get("status") not in {"open", "blocked_external", "accepted", "not_applicable"}:
        raise ValueError("invalid contract acceptance")
    obligations = _strings(closure.get("pending_criteria"), "contractual pending criteria")
    if not set(obligations).issubset(expected):
        raise ValueError("contract pending criteria must belong to this canonical row")
    if not pending.issubset(set(obligations)):
        raise ValueError("contract acceptance omits pending technical criteria")
    if type(closure.get("human_review_required")) is not bool:
        raise ValueError("human review obligation must be explicit")
    canonical = index["contracts"][row_id]
    if closure["human_review_required"] != canonical["human_review_required"]:
        raise ValueError("assessment cannot rewrite its canonical human obligation")
    if closure.get("human_review_status") not in {"not_required", "not_run", "reviewed"}:
        raise ValueError("invalid human review state")
    if closure["human_review_required"] and closure["human_review_status"] == "not_required":
        raise ValueError("required human review cannot be waived")
    if closure["status"] == "accepted":
        if verification["status"] != "pass" or obligations:
            raise ValueError("contract acceptance requires complete technical evidence")
        if closure["human_review_required"] and closure["human_review_status"] != "reviewed":
            raise ValueError("contract acceptance still needs its actual human review")
        if review["method"] == "automated" and closure["human_review_required"]:
            raise ValueError("automated technical review cannot certify human acceptance")
    if closure["human_review_status"] == "reviewed":
        report = next((item for item in index["human_reviews"] if item["id"] == review.get("human_review_id")), None)
        if report is None or report["source"] != source:
            raise ValueError("reviewed human acceptance needs its source-bound retained report")
        if not set(canonical["human_criterion_ids"]).issubset(report["criterion_ids"]):
            raise ValueError("human review does not cover every required human criterion")
        observed = set(report["observed_artifact_sha256"])
        by_criterion = {item["id"]: item for item in criteria}
        for identity in canonical["human_criterion_ids"]:
            compatible = {
                artifact["sha256"]
                for evidence_id in by_criterion[identity]["evidence_ids"]
                for record in [records[evidence_id]]
                if record["source"] == source and identity in record["criterion_ids"]
                and evidence_id not in index.get("_stale_evidence_records", {})
                for artifact in record["artifacts"]
            }
            if not observed.intersection(compatible):
                raise ValueError("human review artifacts do not cover current cited evidence for criterion: " + identity)
        if review["method"] == "automated":
            raise ValueError("automated technical review cannot certify human acceptance")
    if closure["status"] == "not_applicable":
        if not closure.get("reason", "").strip() or closure.get("reason_code") not in canonical["not_applicable_reason_codes"]:
            raise ValueError("not applicable needs an eligible canonical reason")



def validate_catalog_links(index, rows):
    owners = set(index["contracts"])
    requirements = {identity for identity in owners if identity.startswith(("CR-", "MW05-"))}
    cases = {identity for identity in owners if identity.startswith(("ACC-", "A05-"))}
    if len(requirements) != 272 or len(cases) != 124 or owners != requirements | cases:
        raise ValueError("the canonical index must preserve all272 requirements and124 cases")
    edges = index.get("normative_edges")
    if not isinstance(edges, list) or len(edges) != 506:
        raise ValueError("all506 original traceability links must be retained")
    pairs = set()
    for edge in edges:
        _object(edge, "normative edge")
        pair = (edge.get("requirement_id"), edge.get("case_id"))
        if pair[0] not in requirements or pair[1] not in cases or pair in pairs:
            raise ValueError("normative links must have distinct canonical endpoints")
        pairs.add(pair)
    if index.get("normative_edges_sha256") != canonical_digest(edges):
        raise ValueError("original normative link identity changed")
    for row in rows:
        identity = row["id"]
        if identity not in owners or row.get("source_record_sha256") != index["contracts"][identity]["source_record_sha256"]:
            raise ValueError("ledger row differs from its preserved source record")
        if identity in requirements:
            actual = set(_strings(row.get("acceptance_case_ids"), "requirement case links", nonempty=True))
            expected = {case for requirement, case in pairs if requirement == identity}
        else:
            actual = set(_strings(row.get("requirement_ids"), "case requirement links", nonempty=True))
            expected = {requirement for requirement, case in pairs if case == identity}
        if actual != expected:
            raise ValueError("ledger row dropped or changed original normative links")

def validate_ledger_assessments(data, rows, root):
    enabled = "conformance_schema_version" in data
    if not enabled:
        if any("technical_assessment" in row for row in rows):
            raise ValueError("technical assessments need an explicit schema and evidence index")
        return
    if type(data.get("conformance_schema_version")) is not int or data["conformance_schema_version"] != 1:
        raise ValueError("unsupported conformance schema version")
    index, records = load_evidence_index(root, data.get("technical_evidence_index"))
    validate_catalog_links(index, rows)
    for row in rows:
        validate_assessment(row.get("technical_assessment"), row["id"], index, records)
