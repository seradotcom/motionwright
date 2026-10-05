# Security

Please report security issues privately through GitHub's security advisory flow rather than opening a public issue.

Motionwright treats model output, imported project bundles, media metadata, renderer responses and external process results as untrusted input. The application never treats JSON from a caller as Driver Host authority, Broker consent, a filesystem grant or a secret capability.

Default diagnostics are sanitized. They should identify operation, resource/revision, renderer/profile, error class and evidence references without copying user prompts, transcripts, home-directory paths, tokens or raw secrets.

The public repository must not contain the private specification/coordination kit used to bootstrap development.
