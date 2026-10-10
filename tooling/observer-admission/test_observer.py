#!/usr/bin/env python3
"""Adversarial cryptographic admission tests, ephemeral identities only."""
from __future__ import annotations
import base64,hashlib,json
from pathlib import Path
import tempfile,unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from PIL import Image
from verify_observer import inspect,AdmissionRejected,SCHEMA,POLICY,EVIDENCE,HEAD,canonical

SHA="ab"*32
def raw_pub(key:Ed25519PrivateKey)->bytes:
 return key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
def b64(value:bytes)->str:return base64.b64encode(value).decode("ascii")

class SignedVerifierTests(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory(prefix="mw-owner-verified-observer-test-")
  self.addCleanup(temp.cleanup)
  self.base=Path(temp.name)
  self.root=self.base/"artifact";self.root.mkdir()
  self.trust=self.base/"owner";self.trust.mkdir()
  self.owner=Ed25519PrivateKey.generate()
  self.observer=Ed25519PrivateKey.generate()
  self.keyfile=self.trust/"trusted-owner.pub"
  self.keyfile.write_bytes(raw_pub(self.owner))
  self.policy_head_file=self.trust/"trusted-policy-head.json"
  self.source=self.trust/"separately-reviewed-verifier.py"
  self.source.write_text("'''Independent PNG dimension method v1: source bytes are data, never executed by the gate.'''\n")
  self.image=self.root/"frame-000030.png"
  Image.new("RGBA",(320,180),(35,50,76,255)).save(self.image)
  self.admission={
   "schema":SCHEMA,"admission_id":"owner_png_dimension_v1",
   "verifier_id":"reviewed_png_inspector","verifier_version":"1.0.0",
   "verifier_source_sha256":hashlib.sha256(self.source.read_bytes()).hexdigest(),
   "verifier_public_key_b64":b64(raw_pub(self.observer)),
   "method":"png_dimensions_v1","units":"pixels",
   "coverage":"single_full_frame",
   "limitations":["Dimension readback cannot prove text quality, movement or authentic creative approval."],
   "owner_generation":7,"permission":"observe_png_dimensions_only"
  }
  self.policy={"schema":POLICY,"owner_generation":7,
               "admitted_ids":[self.admission["admission_id"]],"revoked_ids":[]}
  self.evidence={
   "schema":EVIDENCE,"admission_id":self.admission["admission_id"],
   "verifier_id":self.admission["verifier_id"],
   "verifier_version":"1.0.0","method":"png_dimensions_v1",
   "units":"pixels","coverage":"single_full_frame",
   "limitations":self.admission["limitations"].copy(),
   "producer_id":"motionwright_original_native_capture",
   "producer_sha256":SHA,"source_frame_path":"frame-000030.png",
   "source_frame_sha256":hashlib.sha256(self.image.read_bytes()).hexdigest(),
   "frame_index":30,"width":320,"height":180
  }
  self.write_all()
 def write(self,name:str,payload:dict,key:Ed25519PrivateKey):
  signature=b64(key.sign(canonical(payload)))
  (self.root/name).write_text(json.dumps({"payload":payload,"signature_b64":signature},indent=2)+"\n")
 def write_all(self):
  self.write("owner-admission.json",self.admission,self.owner)
  self.write("owner-policy.json",self.policy,self.owner)
  self.write("observed-frame.json",self.evidence,self.observer)
  envelope=json.loads((self.root/"owner-policy.json").read_text())
  self.policy_head_file.write_text(json.dumps({
   "schema":HEAD,"owner_generation":self.policy["owner_generation"],
   "canonical_policy_envelope_sha256":hashlib.sha256(canonical(envelope)).hexdigest()
  })+"\n")
 def check(self):
  return inspect(self.root,self.keyfile,self.policy_head_file,self.source,SHA)
 def test_separate_owner_and_observer_signatures_plus_actual_png_decode(self):
  result=self.check()
  self.assertTrue(result["gate_eligible"])
  self.assertEqual(result["status"],"TECHNICAL_OBSERVATION_ADMITTED")
  self.assertEqual(result["measured_pixels"],{"width":320,"height":180})
  self.assertEqual(result["owner_signed_admission"],"VERIFIED")
  self.assertEqual(result["independent_verifier_signed_observation"],"VERIFIED")
  self.assertFalse(result["creative_quality_approved"])
  self.assertFalse(result["producer_execution_granted"])
  self.assertFalse(result["verifier_source_code_executed"])
  self.assertFalse(result["publication_approved"])
 def test_forged_renderer_report_without_verifier_signature_is_rejected(self):
  self.evidence["width"]=999
  (self.root/"observed-frame.json").write_text(json.dumps({
    "payload":self.evidence,"signature_b64":"A"*88}))
  with self.assertRaisesRegex(AdmissionRejected,"signature"):
   self.check()
 def test_a_renderer_cannot_self_certify_by_becoming_the_observer(self):
  self.evidence["producer_id"]=self.admission["verifier_id"]
  self.write_all()
  with self.assertRaisesRegex(AdmissionRejected,"self-certify"):
   self.check()
 def test_signed_false_dimensions_are_rejected_by_independent_actual_decode(self):
  self.evidence["height"]=181
  self.write_all()
  with self.assertRaisesRegex(AdmissionRejected,"Independent PNG decoder"):
   self.check()
 def test_png_changes_after_signing_fail_even_when_pixel_dimensions_are_same(self):
  Image.new("RGBA",(320,180),(60,40,30,255)).save(self.image)
  with self.assertRaisesRegex(AdmissionRejected,"Source frame bytes"):
   self.check()
 def test_owner_revocation_blocks_future_gate_but_keeps_valid_historical_bytes(self):
  before=self.check()
  self.assertTrue(before["gate_eligible"])
  self.policy["owner_generation"]=8
  self.policy["revoked_ids"]=[self.admission["admission_id"]]
  self.write_all()
  revoked=self.check()
  self.assertEqual(revoked["status"],"REVOKED_RETAIN_HISTORICAL_EVIDENCE")
  self.assertFalse(revoked["gate_eligible"])
  self.assertTrue(revoked["historical_evidence_preserved"])
  self.assertEqual(revoked["source_frame_sha256"],before["source_frame_sha256"])
 def test_old_signed_policy_cannot_reactivate_revoked_verifier_after_current_owner_rotation(self):
  archived=(self.root/"owner-policy.json").read_bytes()
  self.policy["owner_generation"]=8
  self.policy["revoked_ids"]=[self.admission["admission_id"]]
  self.write_all()
  self.assertFalse(self.check()["gate_eligible"])
  (self.root/"owner-policy.json").write_bytes(archived)
  with self.assertRaisesRegex(AdmissionRejected,"Replay of a stale"):
   self.check()
 def test_unadmitted_observer_never_becomes_a_gate_just_from_a_renderer_install(self):
  self.policy["admitted_ids"]=[]
  self.write_all()
  with self.assertRaisesRegex(AdmissionRejected,"did not admit"):
   self.check()
 def test_stale_owner_generation_does_not_restore_revoked_admission(self):
  self.policy["owner_generation"]=6
  self.write_all()
  with self.assertRaisesRegex(AdmissionRejected,"stale generation"):
   self.check()
 def test_signatures_cannot_substitute_untrusted_owner_key_or_verifier(self):
  unrelated=Ed25519PrivateKey.generate()
  self.keyfile.write_bytes(raw_pub(unrelated))
  with self.assertRaisesRegex(AdmissionRejected,"signature"):
   self.check()
  self.keyfile.write_bytes(raw_pub(self.owner))
  self.source.write_text("'''A changed verifier source; NOT the approved bytes.'''\n")
  with self.assertRaisesRegex(AdmissionRejected,"Verifier code bytes"):
   self.check()
 def test_owner_method_units_and_limitations_are_a_separate_exact_admission(self):
  self.admission["method"]="claim_creative_excellence"
  self.evidence["method"]="claim_creative_excellence"
  self.write_all()
  with self.assertRaisesRegex(AdmissionRejected,"unknown measurements"):
   self.check()
  self.admission["method"]="png_dimensions_v1"
  self.evidence["method"]="png_dimensions_v1"
  self.admission["limitations"].append("New unreviewed approval criteria.")
  self.write_all()
  with self.assertRaisesRegex(AdmissionRejected,"observation is outside"):
   self.check()
 def test_source_image_symlink_and_missing_raw_owner_anchor_rejected(self):
  original=self.root/"real.png";self.image.rename(original)
  self.image.symlink_to(original)
  with self.assertRaisesRegex(AdmissionRejected,"symlinks"):
   self.check()
  self.image.unlink();original.rename(self.image)
  self.keyfile.unlink()
  with self.assertRaisesRegex(AdmissionRejected,"Trusted owner public key"):
   self.check()
 def test_unrecognized_admission_or_evidence_fields_do_not_grant_new_powers(self):
  self.admission["install_renderer"]=True
  self.write_all()
  with self.assertRaisesRegex(AdmissionRejected,"extra fields"):
   self.check()
  self.admission.pop("install_renderer")
  self.evidence["approved_for_publication"]=True
  self.write_all()
  with self.assertRaisesRegex(AdmissionRejected,"extra fields"):
   self.check()
if __name__=="__main__":unittest.main()
