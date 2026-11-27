"""Prepare a single KXMLGUI action in a Foundation-verified disposable home."""
from __future__ import annotations

import ctypes as C
from hashlib import sha256
from http.client import HTTPSConnection
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from kdenlive_native_distribution import publish
from source_checkout_observation import regular_file, require


KDE_VERSION = "26.08.3"
KDE_TAG_OBJECT = "11f0d694c3f939b36def039c5c004a7ffd9158b9"
KDE_SOURCE = "73f290d57a9ec01d5b6513f644f631d9eac4923a"
KDE_XML_SHA256 = "fbd80bd4481a7c0eb1ab5aa76c41041103b794f0f0441fddce97882198877b09"
KDE_XML_BYTES = 13561
KDE_SOURCE_HOST = "raw.githubusercontent.com"
KDE_XML_PATH = f"/KDE/kdenlive/{KDE_SOURCE}/src/kdenliveui.rc"
TOOLBAR_NAME = "timelineToolBar"
ACTION_NAME = "activate_video_1"
VIDEO_TRACK_ACTION = b'    <Action name="activate_video_1"/>\n'


def toolbar_override(original):
    require(len(original) == KDE_XML_BYTES and sha256(original).hexdigest() == KDE_XML_SHA256,
            "Kdenlive release XML preimage differs")
    base = ET.fromstring(original)
    bars = [node for node in base.findall("ToolBar") if node.get("name") == TOOLBAR_NAME]
    marker = b"    <text>Timeline Toolbar</text>\n"
    require(base.tag == "kpartgui" and base.get("name") == "kdenlive" and len(bars) == 1
            and original.count(marker) == 1
            and not any(node.get("name") == ACTION_NAME for node in base.iter("Action")),
            "Kdenlive release XML preimage does not uniquely admit a Timeline Toolbar insertion")
    index = original.index(marker) + len(marker)
    changed = original[:index] + VIDEO_TRACK_ACTION + original[index:]
    result = ET.fromstring(changed)
    [toolbar] = [node for node in result.findall("ToolBar") if node.get("name") == TOOLBAR_NAME]
    require(len([node for node in toolbar.findall("Action") if node.get("name") == ACTION_NAME]) == 1
            and changed[:index] + changed[index + len(VIDEO_TRACK_ACTION):] == original,
            "Kdenlive toolbar override is not the one exact action insertion")
    return changed


def download_xml():
    connection = HTTPSConnection(KDE_SOURCE_HOST, timeout=25)
    try:
        connection.request("GET", KDE_XML_PATH, headers={"User-Agent": "Motionwright-Kdenlive-qualification"})
        response = connection.getresponse()
        require(response.status == 200, "official Kdenlive XML preimage endpoint did not return HTTP 200")
        return response.read(KDE_XML_BYTES + 1)
    finally:
        connection.close()


def foundation_paths():
    foundation = C.CDLL("/System/Library/Frameworks/Foundation.framework/Foundation")
    objective_c = C.CDLL("/usr/lib/libobjc.A.dylib")
    pointer = C.c_void_p
    home = foundation.NSHomeDirectory
    home.restype, home.argtypes = pointer, ()
    search = foundation.NSSearchPathForDirectoriesInDomains
    search.restype, search.argtypes = pointer, (C.c_ulong, C.c_ulong, C.c_bool)
    selector = objective_c.sel_registerName
    selector.restype, selector.argtypes = pointer, (C.c_char_p,)
    push = objective_c.objc_autoreleasePoolPush
    push.restype, push.argtypes = pointer, ()
    pop = objective_c.objc_autoreleasePoolPop
    pop.restype, pop.argtypes = None, (pointer,)
    send = C.cast(objective_c.objc_msgSend, pointer).value
    utf8 = C.CFUNCTYPE(C.c_char_p, pointer, pointer)(send)
    count = C.CFUNCTYPE(C.c_ulong, pointer, pointer)(send)
    at = C.CFUNCTYPE(pointer, pointer, pointer, C.c_ulong)(send)

    def string(value):
        text = utf8(value, selector(b"UTF8String")) if value else None
        return text.decode("utf-8") if text else None

    pool = push()
    try:
        # NSApplicationSupportDirectory, NSUserDomainMask, expandTilde=YES.
        entries = search(14, 1, True)
        length = count(entries, selector(b"count")) if entries else 0
        require(0 <= length <= 8, "Foundation Application Support paths exceed observation bounds")
        return {"home": string(home()),
                "application_support": [string(at(entries, selector(b"objectAtIndex:"), index))
                                        for index in range(length)]}
    finally:
        pop(pool)


def probe_foundation(output, environment):
    command = [sys.executable, "-B", "-E", "-s", str(Path(__file__).resolve()), "--foundation-paths"]
    result = subprocess.run(command, cwd=output, env=environment, stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=15, check=False)
    require(result.returncode == 0 and 0 < len(result.stdout) <= 8192,
            "Foundation paths were not observed from the final child environment")
    observed = json.loads(result.stdout)
    require(type(observed) is dict, "invalid Foundation path observation")
    return observed


def prepare_profile(output, environment):
    home = output / "isolated-user"
    home.mkdir(mode=0o700)
    support = home / "Library" / "Application Support"
    child = {**environment, "CFFIXED_USER_HOME": str(home)}
    observed = probe_foundation(output, child)
    publish(output / "kxmlgui-foundation-paths.json", {
        "requested_home": str(home), "observed": observed,
        "application_support_must_be": str(support),
    })
    require(observed == {"home": str(home), "application_support": [str(support)]}
            and home.resolve(strict=True) == home,
            "Foundation did not isolate the Kdenlive profile; no override written")

    original = download_xml()
    changed = toolbar_override(original)
    directory = home
    for component in ("Library", "Application Support", "kxmlgui5", "kdenlive"):
        directory = directory / component
        directory.mkdir(mode=0o700, exist_ok=True)
        require(directory.resolve(strict=True) == directory
                and directory.is_dir() and not directory.is_symlink()
                and directory.stat().st_uid == os.getuid(),
                "Foundation selected a non-owned Kdenlive profile directory")
    target = directory / "kdenliveui.rc"
    receipt = {"path": str(target), "original_version": KDE_VERSION, "tag_object": KDE_TAG_OBJECT,
               "source_commit": KDE_SOURCE, "original_rc_sha256": KDE_XML_SHA256,
               "original_rc_bytes": KDE_XML_BYTES, "action": ACTION_NAME, "toolbar": TOOLBAR_NAME,
               "override": {"bytes": len(changed), "sha256": sha256(changed).hexdigest()}}
    publish(output / "kxmlgui-override-intent.json", receipt)
    with target.open("xb") as stream:
        stream.write(changed)
        stream.flush()
        os.fsync(stream.fileno())
    require(regular_file(target, 64 * 1024) == receipt["override"],
            "new isolated Kdenlive override differs from the sole permitted insertion")
    publish(output / "kxmlgui-override-ready.json", receipt)
    return child, receipt


if __name__ == "__main__":
    require(sys.platform == "darwin" and sys.argv[1:] == ["--foundation-paths"],
            "this entry point only observes Foundation paths in macOS")
    print(json.dumps(foundation_paths(), sort_keys=True))
