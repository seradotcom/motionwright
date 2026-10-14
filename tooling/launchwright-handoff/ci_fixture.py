#!/usr/bin/env python3
"""Disposable CI-only authentic MP4 fixture; NOT a product/Launchwright import."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
from export_handoff import package
from test_handoff import HandoffContractTests

def main()->None:
    parser=argparse.ArgumentParser()
    parser.add_argument('--mp4',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    assert args.mp4.is_file() and 1000<args.mp4.stat().st_size<10*1024*1024
    assert not args.output_dir.exists()
    args.output_dir.mkdir(parents=True)
    synthetic=HandoffContractTests()
    synthetic.setUp()
    try:
        shutil.copyfile(args.mp4,synthetic.master)
        synthetic.payload['handoffs'][0]['artifact_sha256']=synthetic.ref(synthetic.master)['sha256']
        synthetic.refresh()
        synthetic.request['master_video']=synthetic.ref(synthetic.master)
        archive=args.output_dir/'motionwright-to-launchwright-SYNTHETIC-NOT_PUBLISHED.zip'
        status=package(synthetic.request,synthetic.root,archive)
        assert status['status']=='SYNTHETIC_HANDOFF_PROTOCOL_ONLY'
        with zipfile.ZipFile(archive)as zipped:
            assert zipped.testzip() is None
            manifest=json.loads(zipped.read('handoff.json'))
            assert manifest['mode']=='synthetic_fixture'
            assert manifest['publication_approved'] is False
            assert manifest['launchwright_import_status']=='NOT_PERFORMED'
            assert hashlib.sha256(zipped.read('media/creative-master.mp4')).hexdigest()==manifest['master_video']['sha256']
            assert hashlib.sha256(zipped.read('source/motionwright-project.json')).hexdigest()==manifest['native_project']['file']['sha256']
        (args.output_dir/'status.json').write_text(json.dumps(status,indent=2)+'\n')
        print(json.dumps(status))
    finally:
        synthetic.doCleanups()

if __name__=='__main__':main()
