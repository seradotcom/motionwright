import base64, gzip, hashlib, io, json, tarfile, unittest
import npm_archive_authentication as auth

class NpmArchiveAuthenticationTests(unittest.TestCase):
    def fixture(self, extra=(), *, name="fixture", version="1.0.0"):
        files = {"package/package.json":json.dumps({"name":name,"version":version,"license":"MIT"}).encode(),
                 "package/index.js":b"export const fixture = 1;\n",
                 "package/LICENSE":b"FIXTURE AUTHENTICITY TEXT ONLY; NOT A DISTRIBUTION LICENSE\n"}
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode="w") as tar:
            for path,data in files.items():
                member=tarfile.TarInfo(path);member.size=len(data);tar.addfile(member,io.BytesIO(data))
            for member,data in extra:
                tar.addfile(member,io.BytesIO(data) if data is not None else None)
        archive=gzip.compress(stream.getvalue(),mtime=0)
        sri="sha512-"+base64.b64encode(hashlib.sha512(archive).digest()).decode()
        lock=json.dumps({"lockfileVersion":3,"packages":{"node_modules/fixture":{"version":"1.0.0","resolved":"https://registry.npmjs.org/fixture/-/fixture-1.0.0.tgz","integrity":sri,"license":"MIT","dev":True}}}).encode()
        observations=[{"path":path[len("package/"):],"bytes":len(data),"sha256":auth.digest(data)} for path,data in files.items()]
        return lock,archive,observations
    def test_actual_tarball_and_original_contributing_bytes_are_required(self):
        lock,archive,observations=self.fixture()
        result=auth.authenticate_npm_sources(lock,"node_modules/fixture",archive,observations)
        self.assertEqual(result["status"],"LOCKED_ARCHIVE_AND_OBSERVED_SOURCE_BYTES_VERIFIED")
        self.assertEqual(result["archive_regular_files"],3)
        self.assertEqual(result["redistribution_permission"],"NOT_INFERRED")
        self.assertNotIn("full_package_inventory",result)
        self.assertEqual(result["verified_observations"],sorted(observations,key=lambda x:x["path"]))
    def test_declared_integrity_does_not_admit_different_archive(self):
        lock,archive,observations=self.fixture()
        with self.assertRaisesRegex(auth.ArchiveError,"archive_sri_mismatch"):
            auth.authenticate_npm_sources(lock,"node_modules/fixture",archive+b"x",observations)
    def test_same_size_installed_module_mutation_is_rejected(self):
        lock,archive,observations=self.fixture()
        next(r for r in observations if r["path"]=="index.js")["sha256"]=auth.digest(b"export const fixture = 2;\n")
        with self.assertRaisesRegex(auth.ArchiveError,"observed_source_not_in_locked_archive:index.js"):
            auth.authenticate_npm_sources(lock,"node_modules/fixture",archive,observations)
    def test_original_notice_cannot_be_replaced_by_generated_generic_text(self):
        lock,archive,observations=self.fixture()
        notice=next(r for r in observations if r["path"]=="LICENSE");notice.update(bytes=3,sha256=auth.digest(b"MIT"))
        with self.assertRaisesRegex(auth.ArchiveError,"observed_source_not_in_locked_archive:LICENSE"):
            auth.authenticate_npm_sources(lock,"node_modules/fixture",archive,observations)
    def test_package_identity_is_checked_inside_archive(self):
        for name,version in (("foreign","1.0.0"),("fixture","2.0.0")):
            lock,archive,observations=self.fixture(name=name,version=version)
            with self.subTest(name=name,version=version),self.assertRaisesRegex(auth.ArchiveError,"archive_package_identity_mismatch"):
                auth.authenticate_npm_sources(lock,"node_modules/fixture",archive,observations)
    def test_symlink_and_hardlink_archive_entries_reject_without_extraction(self):
        for kind in (tarfile.SYMTYPE,tarfile.LNKTYPE):
            member=tarfile.TarInfo("package/redirect");member.type=kind;member.linkname="outside"
            lock,archive,observations=self.fixture([(member,None)])
            with self.subTest(kind=kind),self.assertRaisesRegex(auth.ArchiveError,"non_regular_archive_member"):
                auth.authenticate_npm_sources(lock,"node_modules/fixture",archive,observations)
    def test_traversal_absolute_backslash_and_control_member_paths_reject(self):
        for name in ("../outside","package/../outside","/absolute","package\\outside","package/a:stream","package/line\nbreak"):
            member=tarfile.TarInfo(name);member.size=1
            lock,archive,observations=self.fixture([(member,b"x")])
            with self.subTest(name=name),self.assertRaises(auth.ArchiveError):
                auth.authenticate_npm_sources(lock,"node_modules/fixture",archive,observations)
    def test_duplicate_and_case_colliding_members_reject(self):
        for name in ("package/LICENSE","package/license"):
            member=tarfile.TarInfo(name);member.size=1
            lock,archive,observations=self.fixture([(member,b"x")])
            with self.subTest(name=name),self.assertRaisesRegex(auth.ArchiveError,"duplicate_or_case_colliding_member"):
                auth.authenticate_npm_sources(lock,"node_modules/fixture",archive,observations)
    def test_file_directory_collision_rejects(self):
        member=tarfile.TarInfo("package/LICENSE/child");member.size=1
        lock,archive,observations=self.fixture([(member,b"x")])
        with self.assertRaisesRegex(auth.ArchiveError,"file_directory_collision"):
            auth.authenticate_npm_sources(lock,"node_modules/fixture",archive,observations)
    def test_compressed_expanded_member_and_entry_budgets_are_independent(self):
        _,archive,_=self.fixture()
        for kwargs,reason in (({"max_compressed":8},"compressed_archive_budget"),
                              ({"max_expanded":64},"expanded_archive_budget"),
                              ({"max_member":8},"archive_member_budget"),
                              ({"max_entries":2},"archive_entry_budget")):
            with self.subTest(kwargs=kwargs),self.assertRaisesRegex(auth.ArchiveError,reason):
                auth.archive_members(archive,**kwargs)
    def test_truncated_concatenated_and_nonzero_trailing_archives_reject(self):
        _,archive,_=self.fixture()
        raw=gzip.decompress(archive)
        for malformed in (archive[:-7],archive+archive,gzip.compress(raw+b"foreign",mtime=0)):
            with self.subTest(length=len(malformed)),self.assertRaises(auth.ArchiveError):
                auth.archive_members(malformed)
    def test_strongest_sri_cannot_fall_back_to_a_weaker_matching_hash(self):
        raw=b"fixture"; good256=base64.b64encode(hashlib.sha256(raw).digest()).decode()
        bad512=base64.b64encode(bytes(64)).decode()
        with self.assertRaisesRegex(auth.ArchiveError,"archive_sri_mismatch"):
            auth.verify_sri(raw,"sha256-"+good256+" sha512-"+bad512)
        good512=base64.b64encode(hashlib.sha512(raw).digest()).decode()
        self.assertEqual(auth.verify_sri(raw,"sha512-"+bad512+" sha512-"+good512),"sha512")
    def test_insecure_or_malformed_sri_is_not_accepted(self):
        for declaration in ("","sha1-"+base64.b64encode(bytes(20)).decode(),"sha512-***","sha512-YQ==","sha512-"+base64.b64encode(bytes(64)).decode()+"?foreign"):
            with self.subTest(sri=declaration),self.assertRaises(auth.ArchiveError):
                auth.verify_sri(b"fixture",declaration)
    def test_observations_cannot_omit_package_json_or_duplicate_a_member(self):
        lock,archive,observations=self.fixture()
        for rows in ([r for r in observations if r["path"]!="package.json"],observations+[observations[0]],[]):
            with self.subTest(rows=len(rows)),self.assertRaises(auth.ArchiveError):
                auth.authenticate_npm_sources(lock,"node_modules/fixture",archive,rows)
    def test_lock_cannot_select_links_credentials_or_another_package(self):
        lock,archive,observations=self.fixture()
        for change in ({"link":True},{"resolved":"http://registry.npmjs.org/a"},{"resolved":"https://secret@registry.npmjs.org/a"},{"resolved":"file:///a"},{"integrity":""}):
            data=json.loads(lock);data["packages"]["node_modules/fixture"].update(change)
            with self.subTest(change=change),self.assertRaises(auth.ArchiveError):
                auth.authenticate_npm_sources(json.dumps(data).encode(),"node_modules/fixture",archive,observations)
        with self.assertRaises(auth.ArchiveError):
            auth.authenticate_npm_sources(lock,"node_modules/other",archive,observations)
    def test_scoped_nested_and_aliased_lock_entries_use_actual_package_identity(self):
        lock,archive,observations=self.fixture()
        data=json.loads(lock);entry=data["packages"].pop("node_modules/fixture");entry["name"]="fixture"
        path="node_modules/@scope/parent/node_modules/alias"
        data["packages"][path]=entry
        result=auth.authenticate_npm_sources(json.dumps(data).encode(),path,archive,observations)
        self.assertEqual(result["name"],"fixture")
    def test_duplicate_lock_keys_and_boolean_byte_counts_reject(self):
        lock,archive,observations=self.fixture()
        with self.assertRaises(auth.ArchiveError):
            auth.authenticate_npm_sources(b'{"lockfileVersion":2,"lockfileVersion":3}',"node_modules/fixture",archive,observations)
        observations[0]["bytes"]=True
        with self.assertRaisesRegex(auth.ArchiveError,"invalid_source_observation"):
            auth.authenticate_npm_sources(lock,"node_modules/fixture",archive,observations)

if __name__=="__main__":
    unittest.main(verbosity=2)
