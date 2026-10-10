#!/usr/bin/env python3
"""Independent source-bound native PNG transfer reconstruction.

Only verifies and copies an existing old cache + exact dirty PNGs into a new
output directory. Never launches software, applies edits, resamples audio,
claims codec savings, or treats an unapproved source as trusted.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import zipfile

SCHEMA="motionwright.native-dirty-png-transfer/1"
HEX=re.compile(r"^[0-9a-f]{64}$")
MAX_FRAMES=3600
MAX_PNG=32*1024*1024

class TransferRejected(ValueError):
    pass

def admit(value:bool,message:str)->None:
    if not value:raise TransferRejected(message)

def sha(data:bytes)->str:return hashlib.sha256(data).hexdigest()

def intervals_mask(intervals:object,total:int,name:str)->set[int]:
    admit(isinstance(intervals,list) and len(intervals)<=MAX_FRAMES,f"{name} must contain bounded ranges")
    results=set()
    previous_end=0
    for item in intervals:
        admit(isinstance(item,dict) and set(item)=={'start','end_exclusive'},f"{name} range fields differ")
        start=item['start'];end=item['end_exclusive']
        admit(type(start)is int and type(end)is int and 0<=previous_end<=start<end<=total,
              f"{name} ranges are overlapping, unordered or out of bounds")
        results.update(range(start,end))
        previous_end=end
    return results

def rebuild(transfer:Path,receipt:Path,cache:Path,destination:Path)->dict:
    admit(receipt.is_file() and not receipt.is_symlink() and receipt.stat().st_size<1024*1024,
          "Exact source transfer receipt missing or untrusted")
    doc=json.loads(receipt.read_text(encoding='utf8'))
    expected={'schema','before_source_sha256','after_source_sha256','total_frames',
              'reusable_intervals','dirty_intervals','reusable_frame_receipts',
              'dirty_frame_receipts','metadata_reused','native_frames_not_rendered',
              'encoder_stage_avoided','source_only_visual_not_audio'}
    admit(isinstance(doc,dict)and set(doc)==expected and doc['schema']==SCHEMA,
          "Source transfer contract differs from admitted schema")
    admit(all(isinstance(doc.get(k),str)and HEX.fullmatch(doc[k])for k in
              ('before_source_sha256','after_source_sha256')),
          "Source transfer revision digest missing")
    admit(doc['metadata_reused'] is False and type(doc['native_frames_not_rendered']) is int and doc['native_frames_not_rendered']==0
          and doc['encoder_stage_avoided'] is False and doc['source_only_visual_not_audio'] is True,
          "Source-only transfer cannot claim to bypass renderer, readback, codec, or audio work")
    n=doc['total_frames']
    admit(type(n)is int and 1<=n<=MAX_FRAMES,"Source transfer frame count outside budget")
    cached=intervals_mask(doc['reusable_intervals'],n,'Cached')
    changed=intervals_mask(doc['dirty_intervals'],n,'Dirty')
    admit(not cached&changed and cached|changed==set(range(n)),
          "Dirty/cache ranges do not cover the exact full timeline")
    old_hashes=doc['reusable_frame_receipts']
    new_hashes=doc['dirty_frame_receipts']
    admit(isinstance(old_hashes,list)and len(old_hashes)==len(cached)
          and isinstance(new_hashes,list)and len(new_hashes)==len(changed),
          "Source receipt count does not match its precise dirty frame indexes")
    admit(all(isinstance(s,str)and HEX.fullmatch(s)for s in old_hashes+new_hashes),
          "Source receipts contain malformed hashes")
    admit(cache.is_dir()and not cache.is_symlink()and
          destination.parent.is_dir()and not destination.parent.is_symlink()and not destination.exists(),
          "Old cache/new output roots must be real and output cannot be overwritten")
    admit(transfer.is_file()and not transfer.is_symlink()
          and transfer.stat().st_size<=n*MAX_PNG+1024*1024,
          "Compressed transfer archive invalid or exceeds byte budget")
    names={f"frames/frame-{i:06}.png"for i in changed}
    total_consumed=0
    destination.mkdir(parents=False,exist_ok=False)
    try:
        with zipfile.ZipFile(transfer)as archive:
            entries=archive.namelist()
            admit(len(entries)==len(names)and len(set(entries))==len(names)
                  and set(entries)==names,
                  "Archive must contain exactly the dirty PNGs and nothing executable")
            for item in archive.infolist():
                admit(not stat.S_ISLNK((item.external_attr>>16)&0xffff)
                      and 0<item.file_size<=MAX_PNG,
                      "Transfer contains symlink/oversized PNG source")
            for i,expected_hash in zip(sorted(cached),old_hashes):
                name=f'frame-{i:06}.png'
                src=cache/name
                admit(src.is_file()and not src.is_symlink()and
                      0<src.stat().st_size<=MAX_PNG,"Required cached PNG missing")
                data=src.read_bytes()
                admit(data.startswith(b'\x89PNG\r\n\x1a\n')and sha(data)==expected_hash,
                      "Reused original PNG no longer matches owner source receipt")
                (destination/name).write_bytes(data)
                total_consumed+=len(data)
            for i,expected_hash in zip(sorted(changed),new_hashes):
                name=f'frame-{i:06}.png'
                data=archive.read(f'frames/{name}')
                admit(len(data)<=MAX_PNG and data.startswith(b'\x89PNG\r\n\x1a\n')
                      and sha(data)==expected_hash,
                      "Transferred dirty PNG digest or native magic changed")
                (destination/name).write_bytes(data)
                total_consumed+=len(data)
        admit(len(list(destination.glob('frame-*.png')))==n,
              "After-frame reconstruction was incomplete")
    except Exception:
        for file in destination.glob('*'):
            if file.is_file():file.unlink()
        destination.rmdir()
        raise
    return {
        'schema':'motionwright.native-dirty-transfer-rebuild/1',
        'before_source_sha256':doc['before_source_sha256'],
        'after_source_sha256':doc['after_source_sha256'],
        'frames':n,'reused':len(cached),'transferred':len(changed),
        'receipts_verified':'ALL_FRAME_SHA256',
        'actual_native_render_frames_avoided':0,
        'audio_and_encoding_reuse':'NOT_PERFORMED',
        'cache_authority':'owner_sources_must_be_verified_separately',
        'total_png_bytes_materialized':total_consumed,
        'creative_approval':'NOT_REVIEWED',
    }

def main()->None:
    parser=argparse.ArgumentParser()
    parser.add_argument('--patch',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(rebuild(args.patch,args.receipt,args.cache,args.output),sort_keys=True))
if __name__=='__main__':main()
