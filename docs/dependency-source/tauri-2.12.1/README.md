# Tauri 2.12.1 IPC diagnostic source patch

Motionwright selects the exact official Tauri 2.12.1 source with three reviewed
changes to diagnostics. Rejected invocation keys, request and response bodies,
and channel callback errors are replaced by fixed diagnostic messages. The
patch preserves authorization checks, ACL decisions, callback routing and
authorized response bodies.

The original crate and its MIT and Apache-2.0 licenses are retained. The source
admission tool verifies its published checksum, all 142 vendor files and the
three exact postimages. Cargo must select the local patch at this version.

Run the following command to verify source custody and manifest/lock selection:

    python tooling/v01_tauri_ipc_product_admission.py --root .

Compilation, effective target features and runtime diagnostic observations are
separate verification steps. This patch does not claim to close all application
or platform logging channels.

The patch hashes and file identities are recorded in source-custody.json.
