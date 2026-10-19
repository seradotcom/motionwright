# Atomic SQLite schema upgrade

The first application open is an authority boundary. A failed schema upgrade must not leave a partially created schema even when the `user_version` stamp remains old. A later application run cannot safely treat a half-migrated SQLite file as a normal older database.

Motionwright's storage opener executes connection pragmas outside a transaction, acquires a SQLite `BEGIN IMMEDIATE` writer transaction, rechecks `user_version` under that lock, and performs all `CREATE TABLE`, `CREATE INDEX`, `CREATE TRIGGER` statements plus the schema version stamp inside that transaction. A failure rolls back attempted schema DDL and keeps existing tables, records and the old version untouched. Successful opens commit exactly once; a newer-than-supported schema is always rejected without mutation.

The storage and service source uses the same database, journal, project revisions, receipts and blob CAS as before. There is no extra migration engine, backend, implicit recovery of uncertain jobs or invented rollback of filesystem effects. The SQLite transaction does not cover creating/validating a separate CAS directory. A power loss and the durability of a host filesystem must be checked independently at an installed-device acceptance gate.

## Reproduction

On the original source, the integration test `schema_migration_atomicity::failed_schema_upgrade_never_publishes_partial_new_tables_or_version` writes a deliberate malformed legacy `events` table, calls `Store::open`, and then inspects the real SQLite file using an independent `rusqlite::Connection`. Before the repair, the opener refuses the malformed events index but leaves newly created production tables behind. The test proves that the legacy row and version 0 remain preserved while the partial new schema escapes the failed operation. Run [Actions 39077368631](https://github.com/seradotcom/motionwright/actions/runs/39077368631) on commit `5d28a5c1287eed54046c6f65c46b78a42484ec4c`: two controls pass and the partial-schema regression fails as expected.

After the repair, that same test requires the only surviving application table to be the original `events`, with the original row unchanged. Other controls require fresh/reopened schema version `2` and no added objects on a no-op reopen, plus rejecting schema version `3` without modifying an unrelated original table. No accepted user projects are changed by these synthetic tests.

To check the affected product boundary:

```sh
cargo test --locked -p motionwright-storage --test schema_migration_atomicity
cargo test --locked -p motionwright-storage -p motionwright-service --all-targets
cargo clippy --locked -p motionwright-storage -p motionwright-service --all-targets -- -D warnings
```

This provides a bounded contribution to CR-PROJECT-02 and CR-PROJECT-07. The broader package upgrade/rollback, disk power-loss survival, legacy migration matrix, installer repair, and human-installed-device validation remain separate pending criteria.
