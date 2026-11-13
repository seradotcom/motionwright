"""Offline verification of explicitly reviewed supplemental Cargo notice material.

The caller authenticates the crate and the tracked record/material bytes. This
module proves raw Git-object linkage and classifies every packaged member. It
does not fetch, execute commands, write files, choose a license or grant rights.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tomllib
from urllib.parse import urlsplit

MAX_RECORD = 64 * 1024
MAX_OBJECTS = 512
MAX_TREE = 4 * 1024 * 1024
MAX_BLOB = 2 * 1024 * 1024
MAX_PROOF = 16 * 1024 * 1024
MAX_MEMBERS = 20000
MAX_MEMBER = 32 * 1024 * 1024
MAX_SOURCE = 128 * 1024 * 1024
MAX_NOTICES = 8
MAX_TEXT = 32 * 1024 * 1024
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
PART = re.compile(r"[A-Za-z0-9_.+-]+")
NOTICE_BASENAME = re.compile(
    r"^(?:LICENSE|LICENCE|COPYING|NOTICE|COPYRIGHT)(?:[._-].*)?$", re.IGNORECASE)
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    prefix + str(number) for prefix in ("COM", "LPT") for number in range(1, 10)}
REPOSITORIES = frozenset("https://github.com/" + name for name in (
    "dropbox/rust-alloc-no-stdlib", "Stranger6667/jsonschema", "servo/stylo",
    "tauri-apps/tauri", "Nugine/simd", "wravery/webview2-rs", "OpenByteDev/dlopen2",
    "madsmtm/objc2"))
REGISTRY = "registry+https://github.com/rust-lang/crates.io-index"
MPL_URL = "https://mozilla.org/MPL/2.0/"
MPL_TEXT_URL = "https://www.mozilla.org/media/MPL/2.0/index.txt"
MPL_HEADER = (
    b"/* This Source Code Form is subject to the terms of the Mozilla Public\n"
    b" * License, v. 2.0. If a copy of the MPL was not distributed with this\n"
    b" * file, You can obtain one at https://mozilla.org/MPL/2.0/. */\n")
# This is an observed, closed exception, not a normalization fallback.
LF_CRLF_IDENTITY = {
    "id": REGISTRY + "#webview2-com-macros@0.8.1", "license": "MIT",
    "name": "webview2-com-macros", "version": "0.8.1", "source": REGISTRY,
    "crate_sha256": "67a921c1b6914c367b2b823cd4cde6f96beec77d30a939c8199bb377cf9b9b54",
    "repository": "https://github.com/wravery/webview2-rs",
    "commit": "dffa41a8a46d3f5565eefbff2de57d38d399f158",
    "package_path": "crates/callback-macros",
}
LF_CRLF_MEMBERS = {
    "README.md": {
        "git_bytes": 425, "git_sha256": "dcffb41c431986c4458c0204413c2bcec8ebf81a6a45774888a5e71d50c3417a",
        "git_oid": "2097555df5987d2347f4a4bded90004883203689",
        "bytes": 429, "sha256": "94f2342f8b689e65aed07e90f52acf08a5705b8aeae57c68db9ea51610d68cf2"},
    "src/lib.rs": {
        "git_bytes": 8178, "git_sha256": "0e04b45fa54d548e31b5dbfe295f2eac28ea308bf0339d2357f0c0be1b9daf36",
        "git_oid": "49ea6a8b1e58aa7c1136f113099e0f3e9ef1fbcb",
        "bytes": 8428, "sha256": "3e0fb6feaaea5811a304205b8eaba424a5763947143fc27357f0106db3681ed3"},
}


# These two published archives have reviewed packaging transformations only.
# Exact crate, revision, member and input/output bytes remain mandatory.
DLOPEN_PACKAGING_PROFILES = {'registry+https://github.com/rust-lang/crates.io-index#dlopen2@0.8.2': {'declared_readme': {'bytes': 5053,
                                                                                             'declared_path': '../README.md',
                                                                                             'git_oid': '3f0ca2bc0f68bdff21f7b0e1ba4d62cd87d0b2b7',
                                                                                             'sha256': '66968e46ce37b520d1afee64f3fa7acb038bf24b6c9f86dcc39e46d3700b9dd0',
                                                                                             'upstream_path': 'README.md'},
                                                                         'identity': {'commit': 'cc80e4a0a90d499b677fdf7743699b4b3a43a989',
                                                                                      'crate_sha256': '5e2c5bd4158e66d1e215c49b837e11d62f3267b30c92f1d171c4d3105e3dc4d4',
                                                                                      'id': 'registry+https://github.com/rust-lang/crates.io-index#dlopen2@0.8.2',
                                                                                      'license': 'MIT',
                                                                                      'name': 'dlopen2',
                                                                                      'package_path': 'dlopen2',
                                                                                      'repository': 'https://github.com/OpenByteDev/dlopen2',
                                                                                      'source': 'registry+https://github.com/rust-lang/crates.io-index',
                                                                                      'version': '0.8.2'},
                                                                         'members': {'Cargo.toml.orig': {'bytes': 1611,
                                                                                                         'git_bytes': 1542,
                                                                                                         'git_oid': '51ad853aeaf65e937e26acfc9a918f4936de9e83',
                                                                                                         'git_sha256': '0153c3c729490e105d65aa883c3891323ae801aedbf24eac4edbe21de6d2efa7',
                                                                                                         'sha256': '4db2cb7eef7db93aac79ebab07af14ef27e00cc322ebb9f3744fcc971a1a6b6a'}}},
 'registry+https://github.com/rust-lang/crates.io-index#dlopen2_derive@0.4.3': {'declared_readme': {'bytes': 5053,
                                                                                                    'declared_path': '../README.md',
                                                                                                    'git_oid': '3f0ca2bc0f68bdff21f7b0e1ba4d62cd87d0b2b7',
                                                                                                    'sha256': '66968e46ce37b520d1afee64f3fa7acb038bf24b6c9f86dcc39e46d3700b9dd0',
                                                                                                    'upstream_path': 'README.md'},
                                                                                'identity': {'commit': 'cc80e4a0a90d499b677fdf7743699b4b3a43a989',
                                                                                             'crate_sha256': '0fbbb781877580993a8707ec48672673ec7b81eeba04cfd2310bd28c08e47c8f',
                                                                                             'id': 'registry+https://github.com/rust-lang/crates.io-index#dlopen2_derive@0.4.3',
                                                                                             'license': 'MIT',
                                                                                             'name': 'dlopen2_derive',
                                                                                             'package_path': 'dlopen2-derive',
                                                                                             'repository': 'https://github.com/OpenByteDev/dlopen2',
                                                                                             'source': 'registry+https://github.com/rust-lang/crates.io-index',
                                                                                             'version': '0.4.3'},
                                                                                'members': {'Cargo.toml.orig': {'bytes': 499,
                                                                                                                'git_bytes': 480,
                                                                                                                'git_oid': '22219c8c7e68216021094d111925caf485331573',
                                                                                                                'git_sha256': '8a2558abfe3b44960eaca355b1f3262b86d27760978f8a34d317d1b3daa92965',
                                                                                                                'sha256': '5e3d2d7fe64c4b47b01d4c2e88dbf669bf0950d937b43757b48f965bea4e254a'},
                                                                                            'src/common.rs': {'bytes': 2486,
                                                                                                              'git_bytes': 2407,
                                                                                                              'git_oid': '514ebb00973c852152451e90e1dc0ff1b3a98030',
                                                                                                              'git_sha256': '78d04c4bbea78447c7906b19c29738f33e410e7240f6182baeda603fd5f82467',
                                                                                                              'sha256': '2c986f6129c8e98785b4769bd35cd791438ee9ff8a78a015376f796ec275e16f'}}}}

# Exact objc2 publication identities; no generic Git redirect permission.
OBJC_PACKAGING_PROFILES = {'registry+https://github.com/rust-lang/crates.io-index#block2@0.6.2': {'identity': {'commit': 'b4167b582b2f75f9a1be75495c41b765344fd03c',
                                                                                     'crate_sha256': 'cdeb9d870516001442e364c5220d3574d2da8dc765554b4a617230d33fa58ef5',
                                                                                     'id': 'registry+https://github.com/rust-lang/crates.io-index#block2@0.6.2',
                                                                                     'license': 'MIT',
                                                                                     'name': 'block2',
                                                                                     'package_path': 'crates/block2',
                                                                                     'repository': 'https://github.com/madsmtm/objc2',
                                                                                     'source': 'registry+https://github.com/rust-lang/crates.io-index',
                                                                                     'version': '0.6.2'},
                                                                        'inrepo_copies': [],
                                                                        'official_licenses': ['MIT'],
                                                                        'root_tree': 'd0c0ec68bb362989649471ee9952db5a7fe43edc',
                                                                        'submodule_copy': None},
 'registry+https://github.com/rust-lang/crates.io-index#dispatch2@0.3.1': {'identity': {'commit': '8852b424193ca41602281b3d7540d7c8ed51e49a',
                                                                                        'crate_sha256': '1e0e367e4e7da84520dedcac1901e4da967309406d1e51017ae1abfb97adbd38',
                                                                                        'id': 'registry+https://github.com/rust-lang/crates.io-index#dispatch2@0.3.1',
                                                                                        'license': 'Zlib OR '
                                                                                                   'Apache-2.0 '
                                                                                                   'OR MIT',
                                                                                        'name': 'dispatch2',
                                                                                        'package_path': 'crates/dispatch2',
                                                                                        'repository': 'https://github.com/madsmtm/objc2',
                                                                                        'source': 'registry+https://github.com/rust-lang/crates.io-index',
                                                                                        'version': '0.3.1'},
                                                                           'inrepo_copies': [{'destination': {'bytes': 10145,
                                                                                                              'commit': '8852b424193ca41602281b3d7540d7c8ed51e49a',
                                                                                                              'git_blob_oid': 'd4c61a21974f4952d77cb63c9600c7d8a18cc0fe',
                                                                                                              'path': 'crates/objc2/src/rc/retained_forwarding_impls.rs',
                                                                                                              'repository': 'https://github.com/madsmtm/objc2',
                                                                                                              'sha256': '70c6c9e0e3089c5b5ffbb0535dfb63f355dab2932a20f87b8442cf4ff5f62518'},
                                                                                              'member_path': 'src/retained/forwarding_impls.rs',
                                                                                              'symlink': {'bytes': 60,
                                                                                                          'git_blob_oid': 'a153c0387f851488461425d0411eb3000be71012',
                                                                                                          'path': 'crates/dispatch2/src/retained/forwarding_impls.rs',
                                                                                                          'sha256': '27094abeeb009badb5be2df1c1138611917b622c28391dc92a3ef5885a5d763a',
                                                                                                          'text': '../../../../crates/objc2/src/rc/retained_forwarding_impls.rs'}}],
                                                                           'official_licenses': ['Zlib',
                                                                                                 'Apache-2.0',
                                                                                                 'MIT'],
                                                                           'root_tree': 'f5faa3fe0ff7188b494870478597adf9d0d6ffd5',
                                                                           'submodule_copy': {'generated_commit': '872de032903eec3d718cfd3b35056b010c02e4cb',
                                                                                              'generated_gitmodules_url': 'https://github.com/madsmtm/objc2-generated.git',
                                                                                              'generated_member_prefix': 'Dispatch/',
                                                                                              'generated_repository': 'https://github.com/madsmtm/objc2-generated',
                                                                                              'generated_root_tree': 'bb64726fff227b0f21a7df7cb1c4169e322f77d9',
                                                                                              'gitlink': {'mode': '160000',
                                                                                                          'oid': '872de032903eec3d718cfd3b35056b010c02e4cb',
                                                                                                          'path': 'generated'},
                                                                                              'gitmodules': {'bytes': 284,
                                                                                                             'git_blob_oid': 'ef468d74c849ee551279837bec823f4ddaa0afce',
                                                                                                             'path': '.gitmodules',
                                                                                                             'sha256': 'df2893883aa0e63c9c86a53d15f218ebac55e9d37471aa14a04423bc4775aba9',
                                                                                                             'text': '[submodule '
                                                                                                                     '"generated"]\n'
                                                                                                                     '\t'
                                                                                                                     'path '
                                                                                                                     '= '
                                                                                                                     'generated\n'
                                                                                                                     '\t'
                                                                                                                     'url '
                                                                                                                     '= '
                                                                                                                     'https://github.com/madsmtm/objc2-generated.git\n'
                                                                                                                     '\t'
                                                                                                                     '# '
                                                                                                                     'We '
                                                                                                                     'recommend '
                                                                                                                     'a '
                                                                                                                     'shallow '
                                                                                                                     'clone '
                                                                                                                     'here '
                                                                                                                     'since '
                                                                                                                     'there '
                                                                                                                     'are '
                                                                                                                     'a '
                                                                                                                     'lot '
                                                                                                                     'of '
                                                                                                                     'generated\n'
                                                                                                                     '\t'
                                                                                                                     '# '
                                                                                                                     'files '
                                                                                                                     'in '
                                                                                                                     'history, '
                                                                                                                     'and '
                                                                                                                     'I '
                                                                                                                     'suspect '
                                                                                                                     'most '
                                                                                                                     'contributors '
                                                                                                                     "won't "
                                                                                                                     'need '
                                                                                                                     'to '
                                                                                                                     'look '
                                                                                                                     'at\n'
                                                                                                                     '\t'
                                                                                                                     '# '
                                                                                                                     'the '
                                                                                                                     'history '
                                                                                                                     'of '
                                                                                                                     'these.\n'
                                                                                                                     '\t'
                                                                                                                     'shallow '
                                                                                                                     '= '
                                                                                                                     'true\n'},
                                                                                              'member_count': 1,
                                                                                              'member_prefix': 'src/generated/',
                                                                                              'symlink': {'bytes': 27,
                                                                                                          'git_blob_oid': 'fb3314b24954660af83fcdf1821d1ef92cb10ab7',
                                                                                                          'path': 'crates/dispatch2/src/generated',
                                                                                                          'sha256': '2b233c4f84711d44ceca5e99e974f88d4c06d203ab2ff1564bf54288406ba97f',
                                                                                                          'text': '../../../generated/Dispatch'}}},
 'registry+https://github.com/rust-lang/crates.io-index#objc2-app-kit@0.3.2': {'identity': {'commit': '7b1abfd750a2cacaea71d6a56ecfb83cb7de560b',
                                                                                            'crate_sha256': 'd49e936b501e5c5bf01fda3a9452ff86dc3ea98ad5f283e1455153142d97518c',
                                                                                            'id': 'registry+https://github.com/rust-lang/crates.io-index#objc2-app-kit@0.3.2',
                                                                                            'license': 'Zlib '
                                                                                                       'OR '
                                                                                                       'Apache-2.0 '
                                                                                                       'OR '
                                                                                                       'MIT',
                                                                                            'name': 'objc2-app-kit',
                                                                                            'package_path': 'framework-crates/objc2-app-kit',
                                                                                            'repository': 'https://github.com/madsmtm/objc2',
                                                                                            'source': 'registry+https://github.com/rust-lang/crates.io-index',
                                                                                            'version': '0.3.2'},
                                                                               'inrepo_copies': [],
                                                                               'official_licenses': ['Zlib',
                                                                                                     'Apache-2.0',
                                                                                                     'MIT'],
                                                                               'root_tree': '710642442d33002a98d95957801b30551b4edf82',
                                                                               'submodule_copy': {'generated_commit': '4c2d6fb86d17ed4b4b7e68e80994788b60521987',
                                                                                                  'generated_gitmodules_url': 'https://github.com/madsmtm/objc2-generated.git',
                                                                                                  'generated_member_prefix': 'AppKit/',
                                                                                                  'generated_repository': 'https://github.com/madsmtm/objc2-generated',
                                                                                                  'generated_root_tree': 'c2239b813eec2ba56884fbc068fd0367c9e1a5a0',
                                                                                                  'gitlink': {'mode': '160000',
                                                                                                              'oid': '4c2d6fb86d17ed4b4b7e68e80994788b60521987',
                                                                                                              'path': 'generated'},
                                                                                                  'gitmodules': {'bytes': 284,
                                                                                                                 'git_blob_oid': 'ef468d74c849ee551279837bec823f4ddaa0afce',
                                                                                                                 'path': '.gitmodules',
                                                                                                                 'sha256': 'df2893883aa0e63c9c86a53d15f218ebac55e9d37471aa14a04423bc4775aba9',
                                                                                                                 'text': '[submodule '
                                                                                                                         '"generated"]\n'
                                                                                                                         '\t'
                                                                                                                         'path '
                                                                                                                         '= '
                                                                                                                         'generated\n'
                                                                                                                         '\t'
                                                                                                                         'url '
                                                                                                                         '= '
                                                                                                                         'https://github.com/madsmtm/objc2-generated.git\n'
                                                                                                                         '\t'
                                                                                                                         '# '
                                                                                                                         'We '
                                                                                                                         'recommend '
                                                                                                                         'a '
                                                                                                                         'shallow '
                                                                                                                         'clone '
                                                                                                                         'here '
                                                                                                                         'since '
                                                                                                                         'there '
                                                                                                                         'are '
                                                                                                                         'a '
                                                                                                                         'lot '
                                                                                                                         'of '
                                                                                                                         'generated\n'
                                                                                                                         '\t'
                                                                                                                         '# '
                                                                                                                         'files '
                                                                                                                         'in '
                                                                                                                         'history, '
                                                                                                                         'and '
                                                                                                                         'I '
                                                                                                                         'suspect '
                                                                                                                         'most '
                                                                                                                         'contributors '
                                                                                                                         "won't "
                                                                                                                         'need '
                                                                                                                         'to '
                                                                                                                         'look '
                                                                                                                         'at\n'
                                                                                                                         '\t'
                                                                                                                         '# '
                                                                                                                         'the '
                                                                                                                         'history '
                                                                                                                         'of '
                                                                                                                         'these.\n'
                                                                                                                         '\t'
                                                                                                                         'shallow '
                                                                                                                         '= '
                                                                                                                         'true\n'},
                                                                                                  'member_count': 294,
                                                                                                  'member_prefix': 'src/generated/',
                                                                                                  'symlink': {'bytes': 25,
                                                                                                              'git_blob_oid': 'b15ea653112fb5949d37045618e231312bd3d941',
                                                                                                              'path': 'framework-crates/objc2-app-kit/src/generated',
                                                                                                              'sha256': '1f3a6bb62fa0d0403102c305558dfbd3ec9be7babcafe1a107aa585ebdc6938a',
                                                                                                              'text': '../../../generated/AppKit'}}},
 'registry+https://github.com/rust-lang/crates.io-index#objc2-core-foundation@0.3.2': {'identity': {'commit': '7b1abfd750a2cacaea71d6a56ecfb83cb7de560b',
                                                                                                    'crate_sha256': '2a180dd8642fa45cdb7dd721cd4c11b1cadd4929ce112ebd8b9f5803cc79d536',
                                                                                                    'id': 'registry+https://github.com/rust-lang/crates.io-index#objc2-core-foundation@0.3.2',
                                                                                                    'license': 'Zlib '
                                                                                                               'OR '
                                                                                                               'Apache-2.0 '
                                                                                                               'OR '
                                                                                                               'MIT',
                                                                                                    'name': 'objc2-core-foundation',
                                                                                                    'package_path': 'framework-crates/objc2-core-foundation',
                                                                                                    'repository': 'https://github.com/madsmtm/objc2',
                                                                                                    'source': 'registry+https://github.com/rust-lang/crates.io-index',
                                                                                                    'version': '0.3.2'},
                                                                                       'inrepo_copies': [{'destination': {'bytes': 10145,
                                                                                                                          'commit': '7b1abfd750a2cacaea71d6a56ecfb83cb7de560b',
                                                                                                                          'git_blob_oid': 'd4c61a21974f4952d77cb63c9600c7d8a18cc0fe',
                                                                                                                          'path': 'crates/objc2/src/rc/retained_forwarding_impls.rs',
                                                                                                                          'repository': 'https://github.com/madsmtm/objc2',
                                                                                                                          'sha256': '70c6c9e0e3089c5b5ffbb0535dfb63f355dab2932a20f87b8442cf4ff5f62518'},
                                                                                                          'member_path': 'src/retained/forwarding_impls.rs',
                                                                                                          'symlink': {'bytes': 60,
                                                                                                                      'git_blob_oid': 'a153c0387f851488461425d0411eb3000be71012',
                                                                                                                      'path': 'framework-crates/objc2-core-foundation/src/retained/forwarding_impls.rs',
                                                                                                                      'sha256': '27094abeeb009badb5be2df1c1138611917b622c28391dc92a3ef5885a5d763a',
                                                                                                                      'text': '../../../../crates/objc2/src/rc/retained_forwarding_impls.rs'}}],
                                                                                       'official_licenses': ['Zlib',
                                                                                                             'Apache-2.0',
                                                                                                             'MIT'],
                                                                                       'root_tree': '710642442d33002a98d95957801b30551b4edf82',
                                                                                       'submodule_copy': {'generated_commit': '4c2d6fb86d17ed4b4b7e68e80994788b60521987',
                                                                                                          'generated_gitmodules_url': 'https://github.com/madsmtm/objc2-generated.git',
                                                                                                          'generated_member_prefix': 'CoreFoundation/',
                                                                                                          'generated_repository': 'https://github.com/madsmtm/objc2-generated',
                                                                                                          'generated_root_tree': 'c2239b813eec2ba56884fbc068fd0367c9e1a5a0',
                                                                                                          'gitlink': {'mode': '160000',
                                                                                                                      'oid': '4c2d6fb86d17ed4b4b7e68e80994788b60521987',
                                                                                                                      'path': 'generated'},
                                                                                                          'gitmodules': {'bytes': 284,
                                                                                                                         'git_blob_oid': 'ef468d74c849ee551279837bec823f4ddaa0afce',
                                                                                                                         'path': '.gitmodules',
                                                                                                                         'sha256': 'df2893883aa0e63c9c86a53d15f218ebac55e9d37471aa14a04423bc4775aba9',
                                                                                                                         'text': '[submodule '
                                                                                                                                 '"generated"]\n'
                                                                                                                                 '\t'
                                                                                                                                 'path '
                                                                                                                                 '= '
                                                                                                                                 'generated\n'
                                                                                                                                 '\t'
                                                                                                                                 'url '
                                                                                                                                 '= '
                                                                                                                                 'https://github.com/madsmtm/objc2-generated.git\n'
                                                                                                                                 '\t'
                                                                                                                                 '# '
                                                                                                                                 'We '
                                                                                                                                 'recommend '
                                                                                                                                 'a '
                                                                                                                                 'shallow '
                                                                                                                                 'clone '
                                                                                                                                 'here '
                                                                                                                                 'since '
                                                                                                                                 'there '
                                                                                                                                 'are '
                                                                                                                                 'a '
                                                                                                                                 'lot '
                                                                                                                                 'of '
                                                                                                                                 'generated\n'
                                                                                                                                 '\t'
                                                                                                                                 '# '
                                                                                                                                 'files '
                                                                                                                                 'in '
                                                                                                                                 'history, '
                                                                                                                                 'and '
                                                                                                                                 'I '
                                                                                                                                 'suspect '
                                                                                                                                 'most '
                                                                                                                                 'contributors '
                                                                                                                                 "won't "
                                                                                                                                 'need '
                                                                                                                                 'to '
                                                                                                                                 'look '
                                                                                                                                 'at\n'
                                                                                                                                 '\t'
                                                                                                                                 '# '
                                                                                                                                 'the '
                                                                                                                                 'history '
                                                                                                                                 'of '
                                                                                                                                 'these.\n'
                                                                                                                                 '\t'
                                                                                                                                 'shallow '
                                                                                                                                 '= '
                                                                                                                                 'true\n'},
                                                                                                          'member_count': 46,
                                                                                                          'member_prefix': 'src/generated/',
                                                                                                          'symlink': {'bytes': 33,
                                                                                                                      'git_blob_oid': 'dbe67d8a3781965354d547b99b94d02f892a5517',
                                                                                                                      'path': 'framework-crates/objc2-core-foundation/src/generated',
                                                                                                                      'sha256': 'e174386eeb0b2ed0c176d6bcd0e2ffe49689d0c907a2a11a164b9531ef1d1f94',
                                                                                                                      'text': '../../../generated/CoreFoundation'}}},
 'registry+https://github.com/rust-lang/crates.io-index#objc2-encode@4.1.0': {'identity': {'commit': '8d214f5477365ffcbcbb7de058c86ed9a518efb7',
                                                                                           'crate_sha256': 'ef25abbcd74fb2609453eb695bd2f860d389e457f67dc17cafc8b8cbc89d0c33',
                                                                                           'id': 'registry+https://github.com/rust-lang/crates.io-index#objc2-encode@4.1.0',
                                                                                           'license': 'MIT',
                                                                                           'name': 'objc2-encode',
                                                                                           'package_path': 'crates/objc2-encode',
                                                                                           'repository': 'https://github.com/madsmtm/objc2',
                                                                                           'source': 'registry+https://github.com/rust-lang/crates.io-index',
                                                                                           'version': '4.1.0'},
                                                                              'inrepo_copies': [],
                                                                              'official_licenses': ['MIT'],
                                                                              'root_tree': '83c3be1498e17f9ac4ec91f786dd1c1615a9acfe',
                                                                              'submodule_copy': None},
 'registry+https://github.com/rust-lang/crates.io-index#objc2-exception-helper@0.1.1': {'identity': {'commit': '8d214f5477365ffcbcbb7de058c86ed9a518efb7',
                                                                                                     'crate_sha256': 'c7a1c5fbb72d7735b076bb47b578523aedc40f3c439bea6dfd595c089d79d98a',
                                                                                                     'id': 'registry+https://github.com/rust-lang/crates.io-index#objc2-exception-helper@0.1.1',
                                                                                                     'license': 'Zlib '
                                                                                                                'OR '
                                                                                                                'Apache-2.0 '
                                                                                                                'OR '
                                                                                                                'MIT',
                                                                                                     'name': 'objc2-exception-helper',
                                                                                                     'package_path': 'crates/objc2-exception-helper',
                                                                                                     'repository': 'https://github.com/madsmtm/objc2',
                                                                                                     'source': 'registry+https://github.com/rust-lang/crates.io-index',
                                                                                                     'version': '0.1.1'},
                                                                                        'inrepo_copies': [],
                                                                                        'official_licenses': ['Zlib',
                                                                                                              'Apache-2.0',
                                                                                                              'MIT'],
                                                                                        'root_tree': '83c3be1498e17f9ac4ec91f786dd1c1615a9acfe',
                                                                                        'submodule_copy': None},
 'registry+https://github.com/rust-lang/crates.io-index#objc2-quartz-core@0.3.2': {'identity': {'commit': '7b1abfd750a2cacaea71d6a56ecfb83cb7de560b',
                                                                                                'crate_sha256': '96c1358452b371bf9f104e21ec536d37a650eb10f7ee379fff67d2e08d537f1f',
                                                                                                'id': 'registry+https://github.com/rust-lang/crates.io-index#objc2-quartz-core@0.3.2',
                                                                                                'license': 'Zlib '
                                                                                                           'OR '
                                                                                                           'Apache-2.0 '
                                                                                                           'OR '
                                                                                                           'MIT',
                                                                                                'name': 'objc2-quartz-core',
                                                                                                'package_path': 'framework-crates/objc2-quartz-core',
                                                                                                'repository': 'https://github.com/madsmtm/objc2',
                                                                                                'source': 'registry+https://github.com/rust-lang/crates.io-index',
                                                                                                'version': '0.3.2'},
                                                                                   'inrepo_copies': [],
                                                                                   'official_licenses': ['Zlib',
                                                                                                         'Apache-2.0',
                                                                                                         'MIT'],
                                                                                   'root_tree': '710642442d33002a98d95957801b30551b4edf82',
                                                                                   'submodule_copy': {'generated_commit': '4c2d6fb86d17ed4b4b7e68e80994788b60521987',
                                                                                                      'generated_gitmodules_url': 'https://github.com/madsmtm/objc2-generated.git',
                                                                                                      'generated_member_prefix': 'QuartzCore/',
                                                                                                      'generated_repository': 'https://github.com/madsmtm/objc2-generated',
                                                                                                      'generated_root_tree': 'c2239b813eec2ba56884fbc068fd0367c9e1a5a0',
                                                                                                      'gitlink': {'mode': '160000',
                                                                                                                  'oid': '4c2d6fb86d17ed4b4b7e68e80994788b60521987',
                                                                                                                  'path': 'generated'},
                                                                                                      'gitmodules': {'bytes': 284,
                                                                                                                     'git_blob_oid': 'ef468d74c849ee551279837bec823f4ddaa0afce',
                                                                                                                     'path': '.gitmodules',
                                                                                                                     'sha256': 'df2893883aa0e63c9c86a53d15f218ebac55e9d37471aa14a04423bc4775aba9',
                                                                                                                     'text': '[submodule '
                                                                                                                             '"generated"]\n'
                                                                                                                             '\t'
                                                                                                                             'path '
                                                                                                                             '= '
                                                                                                                             'generated\n'
                                                                                                                             '\t'
                                                                                                                             'url '
                                                                                                                             '= '
                                                                                                                             'https://github.com/madsmtm/objc2-generated.git\n'
                                                                                                                             '\t'
                                                                                                                             '# '
                                                                                                                             'We '
                                                                                                                             'recommend '
                                                                                                                             'a '
                                                                                                                             'shallow '
                                                                                                                             'clone '
                                                                                                                             'here '
                                                                                                                             'since '
                                                                                                                             'there '
                                                                                                                             'are '
                                                                                                                             'a '
                                                                                                                             'lot '
                                                                                                                             'of '
                                                                                                                             'generated\n'
                                                                                                                             '\t'
                                                                                                                             '# '
                                                                                                                             'files '
                                                                                                                             'in '
                                                                                                                             'history, '
                                                                                                                             'and '
                                                                                                                             'I '
                                                                                                                             'suspect '
                                                                                                                             'most '
                                                                                                                             'contributors '
                                                                                                                             "won't "
                                                                                                                             'need '
                                                                                                                             'to '
                                                                                                                             'look '
                                                                                                                             'at\n'
                                                                                                                             '\t'
                                                                                                                             '# '
                                                                                                                             'the '
                                                                                                                             'history '
                                                                                                                             'of '
                                                                                                                             'these.\n'
                                                                                                                             '\t'
                                                                                                                             'shallow '
                                                                                                                             '= '
                                                                                                                             'true\n'},
                                                                                                      'member_count': 30,
                                                                                                      'member_prefix': 'src/generated/',
                                                                                                      'symlink': {'bytes': 29,
                                                                                                                  'git_blob_oid': '80147ec86113167725cbedcf21d439235fe2c3c6',
                                                                                                                  'path': 'framework-crates/objc2-quartz-core/src/generated',
                                                                                                                  'sha256': '6ad9496d91c4a438794a1f9f53a55715d7576f7573d402eb6d96333d5ab11c61',
                                                                                                                  'text': '../../../generated/QuartzCore'}}},
 'registry+https://github.com/rust-lang/crates.io-index#objc2-web-kit@0.3.2': {'identity': {'commit': '7b1abfd750a2cacaea71d6a56ecfb83cb7de560b',
                                                                                            'crate_sha256': 'b2e5aaab980c433cf470df9d7af96a7b46a9d892d521a2cbbb2f8a4c16751e7f',
                                                                                            'id': 'registry+https://github.com/rust-lang/crates.io-index#objc2-web-kit@0.3.2',
                                                                                            'license': 'Zlib '
                                                                                                       'OR '
                                                                                                       'Apache-2.0 '
                                                                                                       'OR '
                                                                                                       'MIT',
                                                                                            'name': 'objc2-web-kit',
                                                                                            'package_path': 'framework-crates/objc2-web-kit',
                                                                                            'repository': 'https://github.com/madsmtm/objc2',
                                                                                            'source': 'registry+https://github.com/rust-lang/crates.io-index',
                                                                                            'version': '0.3.2'},
                                                                               'inrepo_copies': [],
                                                                               'official_licenses': ['Zlib',
                                                                                                     'Apache-2.0',
                                                                                                     'MIT'],
                                                                               'root_tree': '710642442d33002a98d95957801b30551b4edf82',
                                                                               'submodule_copy': {'generated_commit': '4c2d6fb86d17ed4b4b7e68e80994788b60521987',
                                                                                                  'generated_gitmodules_url': 'https://github.com/madsmtm/objc2-generated.git',
                                                                                                  'generated_member_prefix': 'WebKit/',
                                                                                                  'generated_repository': 'https://github.com/madsmtm/objc2-generated',
                                                                                                  'generated_root_tree': 'c2239b813eec2ba56884fbc068fd0367c9e1a5a0',
                                                                                                  'gitlink': {'mode': '160000',
                                                                                                              'oid': '4c2d6fb86d17ed4b4b7e68e80994788b60521987',
                                                                                                              'path': 'generated'},
                                                                                                  'gitmodules': {'bytes': 284,
                                                                                                                 'git_blob_oid': 'ef468d74c849ee551279837bec823f4ddaa0afce',
                                                                                                                 'path': '.gitmodules',
                                                                                                                 'sha256': 'df2893883aa0e63c9c86a53d15f218ebac55e9d37471aa14a04423bc4775aba9',
                                                                                                                 'text': '[submodule '
                                                                                                                         '"generated"]\n'
                                                                                                                         '\t'
                                                                                                                         'path '
                                                                                                                         '= '
                                                                                                                         'generated\n'
                                                                                                                         '\t'
                                                                                                                         'url '
                                                                                                                         '= '
                                                                                                                         'https://github.com/madsmtm/objc2-generated.git\n'
                                                                                                                         '\t'
                                                                                                                         '# '
                                                                                                                         'We '
                                                                                                                         'recommend '
                                                                                                                         'a '
                                                                                                                         'shallow '
                                                                                                                         'clone '
                                                                                                                         'here '
                                                                                                                         'since '
                                                                                                                         'there '
                                                                                                                         'are '
                                                                                                                         'a '
                                                                                                                         'lot '
                                                                                                                         'of '
                                                                                                                         'generated\n'
                                                                                                                         '\t'
                                                                                                                         '# '
                                                                                                                         'files '
                                                                                                                         'in '
                                                                                                                         'history, '
                                                                                                                         'and '
                                                                                                                         'I '
                                                                                                                         'suspect '
                                                                                                                         'most '
                                                                                                                         'contributors '
                                                                                                                         "won't "
                                                                                                                         'need '
                                                                                                                         'to '
                                                                                                                         'look '
                                                                                                                         'at\n'
                                                                                                                         '\t'
                                                                                                                         '# '
                                                                                                                         'the '
                                                                                                                         'history '
                                                                                                                         'of '
                                                                                                                         'these.\n'
                                                                                                                         '\t'
                                                                                                                         'shallow '
                                                                                                                         '= '
                                                                                                                         'true\n'},
                                                                                                  'member_count': 215,
                                                                                                  'member_prefix': 'src/generated/',
                                                                                                  'symlink': {'bytes': 25,
                                                                                                              'git_blob_oid': 'f5bdaaccb2f7219fd23267fb9dcbe81b0376862c',
                                                                                                              'path': 'framework-crates/objc2-web-kit/src/generated',
                                                                                                              'sha256': '4b81daad3b9a759ec80b04449ffbbde81fd91b81ae5e7f6e037d1e227ea6ce59',
                                                                                                              'text': '../../../generated/WebKit'}}},
 'registry+https://github.com/rust-lang/crates.io-index#objc2@0.6.5': {'identity': {'commit': 'd7d2fa23ceaa5e6096c923b081040e5d81b3b9df',
                                                                                    'crate_sha256': '08849bbd4767dfae9457696856ae1c84fe4e0281bbe4a7abff2d0e06fb7981f8',
                                                                                    'id': 'registry+https://github.com/rust-lang/crates.io-index#objc2@0.6.5',
                                                                                    'license': 'MIT',
                                                                                    'name': 'objc2',
                                                                                    'package_path': 'crates/objc2',
                                                                                    'repository': 'https://github.com/madsmtm/objc2',
                                                                                    'source': 'registry+https://github.com/rust-lang/crates.io-index',
                                                                                    'version': '0.6.5'},
                                                                       'inrepo_copies': [],
                                                                       'official_licenses': ['MIT'],
                                                                       'root_tree': '6725a1f1378a8c0a7394d3c24bbb059e5c3e5765',
                                                                       'submodule_copy': None}}
OBJC_UPSTREAM_NOTICE = {'bytes': 1339,
 'git_blob_oid': '540ae4c81adb5f64a9f61e222b688880da24300e',
 'path': 'LICENSE.md',
 'sha256': '7f976f7e9cb2d87df7230606feb932c3f21ac0e664045a775b600046ff850c54'}
OBJC_LICENSE_REFERENCES = {'Apache-2.0': {'acquisition': {'content_type': 'text/plain',
                                'final_url': 'https://www.apache.org/licenses/LICENSE-2.0',
                                'http_status': 200,
                                'name': 'Apache-2.0',
                                'requested_url': 'https://www.apache.org/licenses/LICENSE-2.0',
                                'response_bytes': 11358,
                                'response_sha256': 'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30',
                                'retained_bytes': 11358,
                                'retained_offset': 0,
                                'retained_scope': 'COMPLETE_HTTP_RESPONSE',
                                'retained_sha256': 'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30',
                                'schema': 'motionwright.objc-license-acquisition/1'},
                'document': {'bytes': 11358,
                             'path': 'official/Apache-2.0.original.txt',
                             'sha256': 'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30'},
                'receipt_path': 'official/Apache-2.0.receipt.json',
                'text': {'bytes': 11358,
                         'path': 'official/Apache-2.0.original.txt',
                         'sha256': 'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30'}},
 'MIT': {'acquisition': {'content_type': 'text/html; charset=UTF-8',
                         'final_url': 'https://opensource.org/license/MIT',
                         'http_status': 200,
                         'name': 'MIT',
                         'requested_url': 'https://opensource.org/license/MIT',
                         'response_bytes': 196580,
                         'response_sha256': '46ca51ae629ef638fef495e2b060b44ad980b451fa1b1ccfa83c9aeeae90c0e7',
                         'retained_bytes': 1309,
                         'retained_offset': 156111,
                         'retained_scope': 'HTTP_RESPONSE_FRAGMENT',
                         'retained_sha256': '85559a9a92a63a532f5c28e54567ca5f27648dbef2d7f54be3d4ce2820165231',
                         'schema': 'motionwright.objc-license-acquisition/1'},
         'document': {'bytes': 1309,
                      'path': 'official/MIT.fragment.html',
                      'sha256': '85559a9a92a63a532f5c28e54567ca5f27648dbef2d7f54be3d4ce2820165231'},
         'receipt_path': 'official/MIT.receipt.json',
         'text': {'bytes': 1068,
                  'path': 'official/MIT.reference.txt',
                  'sha256': '21785883212d06d2c262b7e9f73f92ae7a03a7f2fe86809d7c7ad9bd6961d265'}},
 'Zlib': {'acquisition': {'content_type': 'text/html',
                          'final_url': 'https://zlib.net/zlib_license.html',
                          'http_status': 200,
                          'name': 'Zlib',
                          'requested_url': 'https://zlib.net/zlib_license.html',
                          'response_bytes': 2895,
                          'response_sha256': 'cf933153e9a60533c56fd1dcd555b15bff9a81c3468b5df1dc99aa7866c8b5fd',
                          'retained_bytes': 2895,
                          'retained_offset': 0,
                          'retained_scope': 'COMPLETE_HTTP_RESPONSE',
                          'retained_sha256': 'cf933153e9a60533c56fd1dcd555b15bff9a81c3468b5df1dc99aa7866c8b5fd',
                          'schema': 'motionwright.objc-license-acquisition/1'},
          'document': {'bytes': 2895,
                       'path': 'official/Zlib.original.html',
                       'sha256': 'cf933153e9a60533c56fd1dcd555b15bff9a81c3468b5df1dc99aa7866c8b5fd'},
          'receipt_path': 'official/Zlib.receipt.json',
          'text': {'bytes': 1107,
                   'path': 'official/Zlib.reference.txt',
                   'sha256': 'ef6c0da271fdc74603264907c463e4c979db7a93256ee5e1ba54ad5bc46b7e2a'}}}

class SupplementError(ValueError):
    """A static refusal code, without untrusted source text."""


@dataclass(frozen=True)
class VerifiedSupplement:
    source_proof: dict
    notices: tuple[dict, ...]
    texts: dict[str, bytes]


def _need(condition, code):
    if not condition:
        raise SupplementError("cargo_supplement_" + code)


def _object(value, keys, code="record_invalid"):
    _need(isinstance(value, dict) and set(value) == set(keys), code)
    return value


def _integer(value, maximum, minimum=0):
    return type(value) is int and minimum <= value <= maximum


def _path(value):
    _need(isinstance(value, str) and 0 < len(value) <= 512, "path_invalid")
    parts = value.split("/")
    _need(len(parts) <= 32 and all(
        PART.fullmatch(part) and part not in {".", ".."} and not part.endswith(".")
        and part.split(".", 1)[0].upper() not in RESERVED for part in parts), "path_invalid")
    return tuple(parts)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _oid(kind, raw):
    return hashlib.sha1(kind.encode("ascii") + b" " + str(len(raw)).encode("ascii")
                        + b"\0" + raw, usedforsecurity=False).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _need(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def _json(raw):
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)


def _toml(raw):
    _need(len(raw) <= MAX_BLOB, "manifest_budget")
    return tomllib.loads(raw.decode("utf-8"))


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, getattr(info, "st_file_attributes", 0))


def _redirect(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


class _Reader:
    """Same retained-descriptor boundary as source_lock_admission, with empty blobs allowed."""
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.total = 0

    def inspect(self, parts):
        path, observations = self.root, []
        for index, part in enumerate((None, *parts)):
            if part is not None:
                path = path / part
            info = path.lstat()
            leaf = index == len(parts)
            _need(not _redirect(info), "file_redirect")
            _need(stat.S_ISREG(info.st_mode) if leaf else stat.S_ISDIR(info.st_mode),
                  "file_not_regular")
            if leaf:
                _need(info.st_nlink == 1, "file_alias")
            observations.append(_identity(info))
        return path, observations

    def read(self, relative, maximum):
        path, before = self.inspect(_path(relative))
        _need(_integer(before[-1][4], maximum), "file_budget")
        self.total += before[-1][4]
        _need(self.total <= MAX_PROOF, "proof_budget")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            first = os.fstat(stream.fileno())
            opened = _identity(first)
            wanted = before[-1]
            if os.name == "nt":
                # Path creation time and descriptor change time are different domains.
                opened, wanted = opened[:6] + opened[7:], wanted[:6] + wanted[7:]
            _need(not _redirect(first) and stat.S_ISREG(first.st_mode)
                  and opened == wanted, "file_changed")
            raw = stream.read(maximum + 1)
            last = os.fstat(stream.fileno())
        _, after = self.inspect(_path(relative))
        _need(before == after and _identity(first) == _identity(last)
              and len(raw) == first.st_size and len(raw) <= maximum, "file_changed")
        return raw

    def reference(self, row, maximum):
        _object(row, {"path", "bytes", "sha256"}, "reference_invalid")
        _need(_integer(row["bytes"], maximum) and isinstance(row["sha256"], str)
              and HEX64.fullmatch(row["sha256"]), "reference_invalid")
        raw = self.read(row["path"], maximum)
        _need(len(raw) == row["bytes"] and _sha(raw) == row["sha256"], "reference_changed")
        return raw


class _Git:
    def __init__(self, reader, records, commit):
        _need(isinstance(records, list) and 1 <= len(records) <= MAX_OBJECTS, "object_budget")
        self.reader, self.records, self.cache, self.trees = reader, {}, {}, {}
        paths = {"record.json"}
        for row in records:
            _object(row, {"type", "oid", "path", "bytes", "sha256"}, "object_invalid")
            _need(row["type"] in {"commit", "tree", "blob"}
                  and isinstance(row["oid"], str) and HEX40.fullmatch(row["oid"])
                  and row["oid"] not in self.records, "object_invalid")
            path = "/".join(_path(row["path"])).casefold()
            _need(path.startswith("objects/") and path not in paths, "object_path_alias")
            paths.add(path)
            self.records[row["oid"]] = row
        for oid, row in self.records.items():
            self.get(oid, row["type"])
        raw = self.get(commit, "commit")
        _need(b"\0" not in raw and b"\n\n" in raw, "commit_malformed")
        headers = raw.split(b"\n\n", 1)[0].split(b"\n")
        roots = [line for line in headers if line.startswith(b"tree ")]
        _need(len(roots) == 1 and headers[0] == roots[0], "commit_tree_ambiguous")
        self.root = roots[0][5:].decode("ascii")
        _need(HEX40.fullmatch(self.root) is not None, "commit_tree_invalid")

    def get(self, oid, kind):
        key = (oid, kind)
        if key not in self.cache:
            row = self.records.get(oid)
            _need(row is not None and row["type"] == kind, "object_missing_or_wrong_type")
            maximum = MAX_TREE if kind == "tree" else MAX_BLOB if kind == "blob" else 256 * 1024
            data = self.reader.reference({field: row[field] for field in ("path", "bytes", "sha256")},
                                         maximum)
            _need(_oid(kind, data) == oid, "object_oid_mismatch")
            self.cache[key] = data
        return self.cache[key]

    def tree(self, oid):
        if oid not in self.trees:
            raw, offset, result, aliases, previous = self.get(oid, "tree"), 0, {}, set(), None
            while offset < len(raw):
                _need(len(result) < MAX_MEMBERS, "tree_entry_budget")
                space = raw.find(b" ", offset)
                nul = raw.find(b"\0", space + 1) if space >= 0 else -1
                _need(space > offset and nul > space + 1 and nul + 21 <= len(raw), "tree_malformed")
                mode, name = raw[offset:space], raw[space + 1:nul]
                _need(mode in {b"40000", b"100644", b"100755", b"120000", b"160000"}
                      and b"/" not in name and name not in {b".", b".."}, "tree_malformed")
                decoded = name.decode("utf-8")
                alias = decoded.casefold()
                sort_key = name + (b"/" if mode == b"40000" else b"")
                _need(decoded not in result and alias not in aliases
                      and (previous is None or previous < sort_key), "tree_alias_or_order")
                result[decoded] = (mode, raw[nul + 1:nul + 21].hex())
                aliases.add(alias)
                previous, offset = sort_key, nul + 21
            _need(offset == len(raw), "tree_malformed")
            self.trees[oid] = result
        return self.trees[oid]

    def entry(self, relative):
        parts, current = _path(relative), self.root
        for index, part in enumerate(parts):
            entry = self.tree(current).get(part)
            if entry is None:
                return None
            if index == len(parts) - 1:
                return entry
            _need(entry[0] == b"40000", "git_path_redirect")
            current = entry[1]
        raise SupplementError("cargo_supplement_path_invalid")

    def blob_id(self, relative):
        entry = self.entry(relative)
        _need(entry is not None and entry[0] in {b"100644", b"100755"}, "git_blob_path_invalid")
        return entry[1]

    def blob(self, relative):
        return self.get(self.blob_id(relative), "blob")


def _lf_to_crlf(original, packaged):
    _need(b"\r" not in original and b"\0" not in original, "transform_source_invalid")
    original.decode("utf-8")
    _need(original.replace(b"\n", b"\r\n") == packaged, "transform_not_exact")


def _packaging_identity(record):
    identity = {key: record["component"][key] for key in ("id", "name", "version", "source", "license", "crate_sha256")}
    identity.update({key: record[key] for key in ("repository", "commit", "package_path")})
    return identity


def _dlopen_profile(record):
    identity = _packaging_identity(record)
    profile = DLOPEN_PACKAGING_PROFILES.get(identity["id"])
    _need(profile is not None and identity == profile["identity"], "transform_scope_unapproved")
    return profile


def _transform(record, member, oid, original, packaged):
    identity = _packaging_identity(record)
    if identity == LF_CRLF_IDENTITY:
        expected = LF_CRLF_MEMBERS.get(member["path"])
    else:
        expected = _dlopen_profile(record)["members"].get(member["path"])
    _need(expected is not None and oid == expected["git_oid"]
          and len(original) == expected["git_bytes"] and _sha(original) == expected["git_sha256"]
          and len(packaged) == expected["bytes"] and _sha(packaged) == expected["sha256"],
          "transform_identity_mismatch")
    _lf_to_crlf(original, packaged)
    return {"transform": "LF_TO_CRLF", "git_bytes": len(original),
            "git_sha256": _sha(original), "git_blob_oid": oid}


def _declared_parent_readme(record, member, raw, original, normalized, git):
    expected = _dlopen_profile(record)["declared_readme"]
    _need(member["path"] == "README.md"
          and original.get("readme") == expected["declared_path"]
          and normalized.get("readme") == "README.md"
          and expected["upstream_path"] == "README.md"
          and expected["declared_path"] == "../README.md",
          "declared_readme_scope_invalid")
    oid = git.blob_id(expected["upstream_path"])
    _need(oid == expected["git_oid"] and _oid("blob", raw) == oid
          and len(raw) == expected["bytes"] and _sha(raw) == expected["sha256"],
          "declared_readme_identity_mismatch")
    return {"upstream_path": expected["upstream_path"], "git_blob_oid": oid,
            "git_byte_match": True, "source_copy": "CARGO_DECLARED_PARENT_README",
            "declared_by": record["package_path"] + "/Cargo.toml",
            "declared_path": expected["declared_path"]}


def _objc_profile(record):
    identity = _packaging_identity(record)
    profile = OBJC_PACKAGING_PROFILES.get(identity["id"])
    _need(profile is not None and identity == profile["identity"], "objc_profile_unapproved")
    return profile


def _git_relative_target(link_path, text):
    """Resolve only an immutable Git namespace; never open an OS symlink."""
    _need(isinstance(text, str) and text and not text.startswith("/")
          and "\\" not in text and ":" not in text and "\0" not in text,
          "objc_link_target_invalid")
    parts = list(_path(link_path)[:-1])
    for part in text.split("/"):
        if part == "..":
            _need(bool(parts), "objc_link_target_escape")
            parts.pop()
        else:
            _path(part)
            parts.append(part)
    return "/".join(parts)


def _objc_link(git, descriptor):
    entry = git.entry(descriptor["path"])
    _need(entry == (b"120000", descriptor["git_blob_oid"]), "objc_link_identity_mismatch")
    raw = git.get(entry[1], "blob")
    _need(len(raw) == descriptor["bytes"] and _sha(raw) == descriptor["sha256"]
          and raw == descriptor["text"].encode("utf-8"), "objc_link_bytes_mismatch")
    return _git_relative_target(descriptor["path"], descriptor["text"])


def _objc_materialized_member(record, descriptor, raw, git, secondary):
    profile = _objc_profile(record)
    name, kind = descriptor["path"], descriptor["kind"]
    if kind == "git_pinned_inrepo_copy":
        selected = [row for row in profile["inrepo_copies"] if row["member_path"] == name]
        _need(len(selected) == 1, "objc_inrepo_copy_unapproved")
        copy = selected[0]
        destination = copy["destination"]
        resolved = _objc_link(git, copy["symlink"])
        _need(resolved == destination["path"]
              and destination["repository"] == record["repository"]
              and destination["commit"] == record["commit"]
              and git.blob_id(resolved) == destination["git_blob_oid"]
              and _oid("blob", raw) == destination["git_blob_oid"]
              and len(raw) == destination["bytes"] and _sha(raw) == destination["sha256"],
              "objc_inrepo_destination_mismatch")
        return {"upstream_path": resolved, "upstream_repository": record["repository"],
                "upstream_commit": record["commit"], "git_blob_oid": destination["git_blob_oid"],
                "git_byte_match": True, "materialization": "PINNED_GIT_INREPO_COPY",
                "symlink": dict(copy["symlink"])}
    _need(kind == "git_pinned_submodule_copy", "objc_copy_kind_invalid")
    copy = profile["submodule_copy"]
    _need(copy is not None and name.startswith(copy["member_prefix"]),
          "objc_submodule_copy_unapproved")
    suffix = "/".join(_path(name[len(copy["member_prefix"]):]))
    resolved = _objc_link(git, copy["symlink"])
    _need(resolved == copy["gitlink"]["path"] + "/" + copy["generated_member_prefix"].rstrip("/"),
          "objc_submodule_target_mismatch")
    modules = copy["gitmodules"]
    modules_raw = git.blob(modules["path"])
    _need(git.blob_id(modules["path"]) == modules["git_blob_oid"]
          and len(modules_raw) == modules["bytes"] and _sha(modules_raw) == modules["sha256"]
          and modules_raw == modules["text"].encode("utf-8")
          and ('url = ' + copy["generated_gitmodules_url"]).encode() in modules_raw,
          "objc_gitmodules_mismatch")
    _need(git.entry(copy["gitlink"]["path"]) == (b"160000", copy["generated_commit"])
          and copy["gitlink"]["oid"] == copy["generated_commit"],
          "objc_gitlink_mismatch")
    revision = copy["generated_commit"]
    if revision not in secondary:
        secondary[revision] = _Git(git.reader, record["objects"], revision)
    generated = secondary[revision]
    _need(generated.root == copy["generated_root_tree"], "objc_generated_tree_mismatch")
    upstream_path = copy["generated_member_prefix"] + suffix
    blob_id = generated.blob_id(upstream_path)
    _need(_oid("blob", raw) == blob_id, "objc_materialized_bytes_mismatch")
    return {
        "upstream_path": upstream_path, "upstream_repository": copy["generated_repository"],
        "upstream_commit": revision, "git_blob_oid": blob_id, "git_byte_match": True,
        "materialization": "PINNED_GIT_SUBMODULE_COPY", "symlink": dict(copy["symlink"]),
        "gitmodules": {key: modules[key] for key in ("path", "git_blob_oid", "bytes", "sha256")},
        "gitlink": dict(copy["gitlink"]), "generated_root_tree": generated.root,
    }


def _objc_record_scope(record, git):
    profile = _objc_profile(record)
    _need(git.root == profile["root_tree"], "objc_root_tree_mismatch")
    submodule = profile["submodule_copy"]
    copied = [row for row in record["members"] if row["kind"] == "git_pinned_submodule_copy"]
    if submodule is None:
        _need(not copied, "objc_submodule_copy_unapproved")
    else:
        declared = [row for row in record["members"] if row["path"].startswith(submodule["member_prefix"])]
        _need(len(copied) == submodule["member_count"] and copied == declared,
              "objc_submodule_member_set_mismatch")
    inrepo = [row["path"] for row in record["members"] if row["kind"] == "git_pinned_inrepo_copy"]
    _need(set(inrepo) == {row["member_path"] for row in profile["inrepo_copies"]}
          and len(inrepo) == len(profile["inrepo_copies"]), "objc_inrepo_member_set_mismatch")
    expected_upstream = {"kind": "upstream_git", "upstream_path": "LICENSE.md",
                         "declared_by": record["package_path"] + "/Cargo.toml"}
    notices = record["notices"]
    _need(isinstance(notices, list) and len(notices) == 1 + len(profile["official_licenses"])
          and all(isinstance(row, dict) for row in notices)
          and [row for row in notices if row.get("kind") == "upstream_git"] == [expected_upstream]
          and sorted(row.get("license_id", "") for row in notices
                     if row.get("kind") == "objc2_official_license") == sorted(profile["official_licenses"]),
          "objc_notice_set_incomplete")
    notice_raw = git.blob("LICENSE.md")
    _need(git.blob_id("LICENSE.md") == OBJC_UPSTREAM_NOTICE["git_blob_oid"]
          and len(notice_raw) == OBJC_UPSTREAM_NOTICE["bytes"]
          and _sha(notice_raw) == OBJC_UPSTREAM_NOTICE["sha256"], "objc_upstream_notice_mismatch")


def _objc_official_license(record, notice, git, reader):
    """Verify reviewed reference documents; never present them as historical Git blobs."""
    import html

    profile = _objc_profile(record)
    _object(notice, {"kind", "license_id", "document", "text", "receipt"}, "objc_notice_invalid")
    license_id = notice["license_id"]
    _need(license_id in profile["official_licenses"], "objc_license_scope_invalid")
    expected = OBJC_LICENSE_REFERENCES[license_id]
    for key in ("document", "text"):
        _need(notice[key] == expected[key], "objc_document_reference_mismatch")
    _need(notice["receipt"]["path"] == expected["receipt_path"], "objc_receipt_path_mismatch")
    receipt = _json(reader.reference(notice["receipt"], MAX_RECORD))
    _object(receipt, set(expected["acquisition"]), "objc_acquisition_invalid")
    _need(receipt == expected["acquisition"]
          and all(type(receipt[key]) is int for key in
                  ("http_status", "response_bytes", "retained_offset", "retained_bytes")),
          "objc_acquisition_mismatch")
    notice_raw = git.blob("LICENSE.md")
    source_reference = ("[" + license_id + "]: " + receipt["requested_url"]).encode("utf-8")
    _need(_sha(notice_raw) == OBJC_UPSTREAM_NOTICE["sha256"]
          and notice_raw.count(source_reference) == 1, "objc_official_reference_missing")
    document = reader.reference(notice["document"], MAX_BLOB)
    text = reader.reference(notice["text"], MAX_BLOB)
    _need(len(document) == receipt["retained_bytes"]
          and _sha(document) == receipt["retained_sha256"], "objc_retained_document_mismatch")
    if license_id == "MIT":
        _need(document.count(b'<div id="separator">') == 1
              and document.count(b'<div id="LicenseText">') == 1,
              "objc_mit_fragment_invalid")
        paragraphs = re.findall(r"<p>(.*?)</p>", document.decode("utf-8"), flags=re.S)
        _need(len(paragraphs) == 4, "objc_mit_paragraphs_invalid")
        derived = ("\n\n".join(html.unescape(re.sub(r"<[^>]+>", "", paragraph)).strip()
                               for paragraph in paragraphs) + "\n").encode("utf-8")
        derivation = "FOUR_ORIGINAL_HTML_PARAGRAPHS_ENTITY_DECODED"
    elif license_id == "Zlib":
        _need(document.count(b"<PRE>") == document.count(b"</PRE>") == 1,
              "objc_zlib_fragment_invalid")
        start = document.index(b"<PRE>") + len(b"<PRE>")
        end = document.index(b"</PRE>")
        _need(start == 837 and end - start == 1107, "objc_zlib_fragment_invalid")
        derived = document[start:end]
        derivation = "EXACT_ORIGINAL_PRE_BYTES"
    else:
        _need(license_id == "Apache-2.0", "objc_license_scope_invalid")
        derived = document
        derivation = "ORIGINAL_TEXT_PLAIN_RESPONSE"
    _need(text == derived, "objc_official_text_derivation_mismatch")
    return text, {
        "upstream_notice_path": "LICENSE.md",
        "upstream_notice_git_blob_oid": OBJC_UPSTREAM_NOTICE["git_blob_oid"],
        "upstream_notice_sha256": OBJC_UPSTREAM_NOTICE["sha256"],
        "license_reference": license_id, "source_reference_url": receipt["requested_url"],
        "official_url": receipt["final_url"], "response_sha256": receipt["response_sha256"],
        "retained_document_sha256": receipt["retained_sha256"],
        "retained_document_scope": receipt["retained_scope"],
        "retained_document_offset": receipt["retained_offset"],
        "acquisition_receipt_sha256": notice["receipt"]["sha256"],
        "text_derivation": derivation, "historical_upstream_git_blob": False,
        "official_document_admission": "REVIEWED_RECORD_CONSISTENCY",
        "copyright_attribution": "REFERENCE_TEXT_ONLY_NO_HOLDERS_SUBSTITUTED",
    }


def _packaged_lock(raw, component):
    lock = _toml(raw)
    _object(lock, {"version", "package"}, "packaged_lock_invalid")
    _need(type(lock["version"]) is int and lock["version"] in {3, 4}
          and isinstance(lock["package"], list) and 1 <= len(lock["package"]) <= 4096,
          "packaged_lock_invalid")
    own = 0
    for row in lock["package"]:
        _need(isinstance(row, dict) and set(row) <= {
            "name", "version", "source", "checksum", "dependencies", "replace"},
            "packaged_lock_invalid")
        for key in ("name", "version"):
            _need(isinstance(row.get(key), str) and 0 < len(row[key]) <= 128, "packaged_lock_invalid")
        if "source" in row:
            _need(isinstance(row["source"], str) and 0 < len(row["source"]) <= 8192, "packaged_lock_invalid")
        if "checksum" in row:
            _need(isinstance(row["checksum"], str) and HEX64.fullmatch(row["checksum"]), "packaged_lock_invalid")
        if "replace" in row:
            _need(isinstance(row["replace"], str) and 0 < len(row["replace"]) <= 1024, "packaged_lock_invalid")
        if "dependencies" in row:
            _need(isinstance(row["dependencies"], list) and len(row["dependencies"]) <= 4096
                  and all(isinstance(value, str) and 0 < len(value) <= 1024
                          for value in row["dependencies"]), "packaged_lock_invalid")
        if row["name"] == component["name"] and row["version"] == component["version"] and "source" not in row:
            own += 1
    _need(own == 1, "packaged_lock_component_mismatch")


def _workspace(git, package, package_path, declared_path):
    _need(declared_path is None or isinstance(declared_path, str), "workspace_invalid")
    explicit = package.get("workspace")
    if explicit is not None:
        _need(isinstance(explicit, str) and explicit and "\\" not in explicit
              and not explicit.startswith("/") and ":" not in explicit, "workspace_invalid")
        parts = list(_path(package_path))
        for part in explicit.split("/"):
            if part == "..":
                _need(bool(parts), "workspace_escape")
                parts.pop()
            elif part not in {"", "."}:
                _path(part)
                parts.append(part)
        selected = "/".join((*parts, "Cargo.toml"))
        _need(declared_path == selected, "workspace_mismatch")
    else:
        selected = None
        parts = _path(package_path)
        for depth in range(len(parts) - 1, -1, -1):
            candidate = "/".join((*parts[:depth], "Cargo.toml"))
            if git.entry(candidate) is not None:
                document = _toml(git.blob(candidate))
                if isinstance(document.get("workspace"), dict):
                    selected = candidate
                    break
        _need(selected == declared_path, "workspace_mismatch")
    _need(selected is not None, "workspace_missing")
    document = _toml(git.blob(selected))
    table = document.get("workspace", {}).get("package")
    _need(isinstance(table, dict), "workspace_package_missing")
    return table


def _notice_text(raw):
    _need(0 < len(raw) <= MAX_BLOB and b"\0" not in raw and raw.decode("utf-8-sig").strip(),
          "notice_text_invalid")


def verify_supplement(evidence_root: Path, *, expected_record_sha256: str,
                      component_identity: dict, crate_members: Mapping[str, bytes],
                      expected_crate_sha256: str) -> VerifiedSupplement:
    """Verify one tracked record.json against a caller-authenticated lazy crate mapping."""
    try:
        return _verify(evidence_root, expected_record_sha256, component_identity,
                       crate_members, expected_crate_sha256)
    except SupplementError:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as error:
        raise SupplementError("cargo_supplement_input_unverified") from error


def _verify(root, expected_record_sha256, component, crate_members, expected_crate_sha256):
    _need(isinstance(expected_record_sha256, str) and HEX64.fullmatch(expected_record_sha256)
          and isinstance(expected_crate_sha256, str) and HEX64.fullmatch(expected_crate_sha256),
          "expected_digest_invalid")
    _object(component, {"id", "name", "version", "source", "license"}, "component_invalid")
    _need(component["source"] == REGISTRY and all(isinstance(component[key], str)
          and 0 < len(component[key]) <= (8192 if key == "id" else 4096)
          for key in ("id", "name", "version", "license")), "component_invalid")
    reader = _Reader(root)
    encoded = reader.read("record.json", MAX_RECORD)
    _need(_sha(encoded) == expected_record_sha256, "record_digest_mismatch")
    record = _json(encoded)
    _object(record, {"schema", "component", "repository", "commit", "package_path",
                     "workspace_manifest", "objects", "members", "notices"})
    _need(record["schema"] == "motionwright.cargo-notice-supplement/1", "schema_invalid")
    _need(record["component"] == {**component, "crate_sha256": expected_crate_sha256},
          "component_binding_mismatch")
    _need(record["repository"] in REPOSITORIES and isinstance(record["commit"], str)
          and HEX40.fullmatch(record["commit"]), "upstream_invalid")
    package_path = "/".join(_path(record["package_path"]))
    _need(isinstance(crate_members, Mapping), "crate_mapping_invalid")
    actual, aliases = set(), set()
    for name in crate_members:
        _need(len(actual) < MAX_MEMBERS, "member_budget")
        name = "/".join(_path(name))
        _need(name not in actual and name.casefold() not in aliases, "member_alias")
        actual.add(name); aliases.add(name.casefold())
    _need(isinstance(record["members"], list) and 1 <= len(record["members"]) <= MAX_MEMBERS,
          "member_budget")
    roster = {}
    for row in record["members"]:
        _object(row, {"path", "kind", "bytes", "sha256"}, "member_record_invalid")
        name = "/".join(_path(row["path"]))
        _need(name not in roster and _integer(row["bytes"], MAX_MEMBER)
              and isinstance(row["sha256"], str) and HEX64.fullmatch(row["sha256"]),
              "member_record_invalid")
        roster[name] = row
    _need(set(roster) == actual and {"Cargo.toml", "Cargo.toml.orig", ".cargo_vcs_info.json"} <= actual,
          "member_roster_mismatch")
    total, small = 0, {}
    def member(name):
        nonlocal total
        if name in small:
            return small[name]
        raw = crate_members[name]
        _need(isinstance(raw, bytes), "member_bytes_invalid")
        descriptor = roster[name]
        _need(len(raw) == descriptor["bytes"] and _sha(raw) == descriptor["sha256"], "member_bytes_changed")
        total += len(raw)
        _need(len(raw) <= MAX_MEMBER and total <= MAX_SOURCE, "source_budget")
        if name in {"Cargo.toml", "Cargo.toml.orig", ".cargo_vcs_info.json"}:
            small[name] = raw
        return raw
    normalized = _toml(member("Cargo.toml")).get("package")
    original = _toml(member("Cargo.toml.orig")).get("package")
    _need(isinstance(normalized, dict) and isinstance(original, dict), "package_manifest_invalid")
    vcs = _json(member(".cargo_vcs_info.json"))
    _object(vcs, {"git", "path_in_vcs"}, "vcs_invalid")
    _need(isinstance(vcs["git"], dict) and set(vcs["git"]) in ({"sha1"}, {"sha1", "dirty"})
          and vcs["git"].get("dirty", False) is False and vcs["git"]["sha1"] == record["commit"]
          and vcs["path_in_vcs"] == package_path, "vcs_mismatch_or_dirty")
    git = _Git(reader, record["objects"], record["commit"])
    if record["repository"] == "https://github.com/madsmtm/objc2":
        _objc_record_scope(record, git)
    secondary_git = {}
    manifest_oid = git.blob_id(package_path + "/Cargo.toml")
    manifest_transform = None
    if _oid("blob", member("Cargo.toml.orig")) != manifest_oid:
        _need(roster["Cargo.toml.orig"]["kind"] == "git_package_manifest_lf_to_crlf",
              "original_manifest_git_mismatch")
        manifest_transform = _transform(
            record, roster["Cargo.toml.orig"], manifest_oid, git.get(manifest_oid, "blob"),
            member("Cargo.toml.orig"))
    inherited = None
    for field, expected in (("name", component["name"]), ("version", component["version"]),
                            ("license", component["license"]), ("repository", record["repository"])):
        value = original.get(field)
        if isinstance(value, dict):
            _need(value == {"workspace": True} and field != "name", "inheritance_invalid")
            if inherited is None:
                inherited = _workspace(git, original, package_path, record["workspace_manifest"])
            value = inherited.get(field)
        _need(value == expected and normalized.get(field) == expected, "manifest_identity_mismatch")
    _need((inherited is not None) == (record["workspace_manifest"] is not None), "workspace_unused_or_missing")
    proof_members = []
    special = {"Cargo.toml": "cargo_normalized_manifest",
               "Cargo.toml.orig": ("git_package_manifest_lf_to_crlf" if manifest_transform is not None
                                   else "git_package_manifest"),
               ".cargo_vcs_info.json": "cargo_vcs_metadata"}
    for name, descriptor in roster.items():
        raw = member(name)
        kind = descriptor["kind"]
        item = dict(descriptor)
        if name in special:
            _need(kind == special[name], "member_classification_invalid")
            if name == "Cargo.toml.orig":
                item.update(upstream_path=package_path + "/Cargo.toml", git_blob_oid=manifest_oid)
                if manifest_transform is not None:
                    item.update(manifest_transform, git_byte_match=False)
        elif kind == "git_declared_parent_readme":
            item.update(_declared_parent_readme(record, descriptor, raw, original, normalized, git))
        elif kind in {"git_pinned_submodule_copy", "git_pinned_inrepo_copy"}:
            item.update(_objc_materialized_member(record, descriptor, raw, git, secondary_git))
        elif kind == "cargo_packaged_lock":
            _need(name == "Cargo.lock", "packaged_lock_path_invalid")
            _packaged_lock(raw, component)
            item["git_byte_match"] = False
        else:
            _need(kind in {"git_exact", "git_lf_to_crlf"}, "member_classification_invalid")
            upstream = package_path + "/" + name
            oid = git.blob_id(upstream)
            if kind == "git_exact":
                _need(_oid("blob", raw) == oid, "source_git_mismatch")
            else:
                item.update(_transform(record, descriptor, oid, git.get(oid, "blob"), raw))
            item.update(upstream_path=upstream, git_blob_oid=oid, git_byte_match=kind == "git_exact")
        proof_members.append(item)
    _need(isinstance(record["notices"], list) and 1 <= len(record["notices"]) <= MAX_NOTICES,
          "notice_budget")
    notices, texts, seen = [], {}, set()
    base = {"repository": record["repository"], "commit": record["commit"],
            "record_sha256": expected_record_sha256, "crate_sha256": expected_crate_sha256}
    def emit(raw, source_path, kind, provenance):
        _notice_text(raw)
        digest = _sha(raw)
        _need((kind, source_path) not in seen, "notice_duplicate")
        seen.add((kind, source_path))
        texts[digest] = raw
        _need(sum(len(value) for value in texts.values()) <= MAX_TEXT, "text_budget")
        notices.append({"source_path": source_path, "bytes": len(raw), "sha256": digest,
                        "source_kind": kind, "provenance": {**base, **provenance}})
    ancestors = {"/".join(_path(package_path)[:depth]) for depth in range(len(_path(package_path)) + 1)}
    for notice in record["notices"]:
        _need(isinstance(notice, dict), "notice_invalid")
        if notice.get("kind") == "upstream_git":
            _object(notice, {"kind", "upstream_path", "declared_by"}, "notice_invalid")
            upstream = "/".join(_path(notice["upstream_path"]))
            _need(upstream.rpartition("/")[0] in ancestors
                  and notice["declared_by"] == package_path + "/Cargo.toml", "notice_association_invalid")
            # declared_by identifies the manifest's license expression. Association
            # with this conventional notice path is reviewed in the record; it is
            # not a claim that the manifest explicitly named this notice file.
            _need(NOTICE_BASENAME.fullmatch(upstream.rsplit("/", 1)[-1]) is not None,
                  "notice_name_invalid")
            raw = git.blob(upstream)
            emit(raw, upstream, "supplemental_upstream_git", {
                "upstream_path": upstream, "git_blob_oid": git.blob_id(upstream),
                "declared_by": notice["declared_by"]})
        elif notice.get("kind") == "objc2_official_license":
            text, provenance = _objc_official_license(record, notice, git, reader)
            emit(text, notice["text"]["path"], "official_license_text_referenced_by_source", provenance)
        elif notice.get("kind") == "mozilla_mpl_2_0":
            _object(notice, {"kind", "header_member", "header_offset", "text", "receipt"}, "notice_invalid")
            _need(component["license"] == "MPL-2.0", "official_license_mismatch")
            name = "/".join(_path(notice["header_member"]))
            _need(name in roster and roster[name]["kind"] == "git_exact"
                  and _integer(notice["header_offset"], MAX_MEMBER), "official_header_invalid")
            offset = notice["header_offset"]
            raw_source = member(name)
            _need(raw_source[offset:offset + len(MPL_HEADER)] == MPL_HEADER, "official_header_mismatch")
            text = reader.reference(notice["text"], MAX_BLOB)
            receipt = _json(reader.reference(notice["receipt"], MAX_RECORD))
            _object(receipt, {"schema", "requested_url", "final_url", "http_status",
                              "content_type", "bytes", "sha256"}, "official_receipt_invalid")
            url = urlsplit(receipt["final_url"])
            _need(receipt["schema"] == "motionwright.official-license-acquisition/1"
                  and receipt["requested_url"] == MPL_TEXT_URL
                  and type(receipt["http_status"]) is int and receipt["http_status"] == 200
                  and receipt["final_url"] == MPL_TEXT_URL
                  and url.scheme == "https" and url.netloc in {"mozilla.org", "www.mozilla.org"}
                  and not url.query and not url.fragment
                  and url.path.startswith(("/MPL/2.0/", "/en-US/MPL/2.0/", "/media/MPL/2.0/"))
                  and isinstance(receipt["content_type"], str)
                  and receipt["content_type"].lower().split(";", 1)[0] == "text/plain"
                  and _integer(receipt["bytes"], MAX_BLOB, 1)
                  and receipt["bytes"] == len(text) and receipt["sha256"] == _sha(text),
                  "official_receipt_mismatch")
            upstream = package_path + "/" + name
            witness = {"upstream_path": upstream, "git_blob_oid": git.blob_id(upstream),
                       "header_offset": offset, "header_bytes": len(MPL_HEADER),
                       "header_sha256": _sha(MPL_HEADER)}
            emit(MPL_HEADER, upstream, "upstream_source_license_header", witness)
            emit(text, notice["text"]["path"], "official_license_text_referenced_by_source", {
                **witness, "source_reference_url": MPL_URL, "official_url": receipt["final_url"],
                "acquisition_receipt_sha256": notice["receipt"]["sha256"],
                "official_document_admission": "REVIEWED_RECORD_CONSISTENCY"})
        else:
            raise SupplementError("cargo_supplement_notice_kind_unsupported")
    return VerifiedSupplement({
        "schema": "motionwright.cargo-notice-source-proof/1",
        **base, "git_root_tree": git.root, "package_path": package_path,
        "all_archive_members_classified": True, "members": proof_members,
        "normalized_manifest_derivation": "NOT_PROVED_AS_GIT_BYTES",
        "redistribution_permission": "NOT_INFERRED", "full_package_inventory": "NOT_VERIFIED",
    }, tuple(notices), texts)
