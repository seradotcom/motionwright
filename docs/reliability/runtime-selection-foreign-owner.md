# Saved Semwright connection reference ownership

The local runtime registry stores only a reference to an owner-provisioned
Semwright connection file, scoped by Motionwright project and generation.
It does not store or approve Broker credentials.

Replacing an existing saved selection is permitted for the **same**
Motionwright project, including after a legitimate project-generation
change. The prior reference is staged and atomically replaced so a failed
rename does not remove the last valid selection.

A foreign record is different: when the existing JSON names a
`project_id` other than the destination project's ID, selection now fails
closed. It does not replace the original bytes, create a replacement
reference, or clear the foreign record. This includes a structurally
incomplete but valid JSON record that still contains an identifiable
`project_id`. The read and clear operations, as well as replacement,
also reject any file type the platform marks as a link/reparse point.
No authorization is inferred from the filename alone.

Previously malformed legacy JSON **without an identifiable project ID**
remains recoverable through an explicitly verified replacement; this is
a narrower guarantee than general ownership validation for arbitrary
malformed bytes. The caller must first validate the new Semwright
connection through the normal resource and read-only version probe.
Neither the registry nor these tests grant privileges or approve
destructive Broker commands.

The `selecting_new_connection_never_overwrites_foreign_project_record`
unit regression exercises a fully formed foreign selection and a
partial foreign JSON record, exact byte and directory readback on
rejection, and a legitimate same-project generation replacement. Existing
Unix symlink/permission, Windows locked-rename and NTFS junction tests
remain necessary. File path inspections are not atomic OS handle
confinement and do not prevent a privileged concurrent filesystem
substitution after the check.

No installed owner session, Broker foreground authorization or release
approval is established by these source tests.
