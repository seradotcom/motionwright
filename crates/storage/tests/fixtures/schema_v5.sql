
            CREATE TABLE IF NOT EXISTS projects(
              id TEXT PRIMARY KEY,
              generation TEXT NOT NULL,
              revision INTEGER NOT NULL CHECK(revision >= 0),
              title TEXT NOT NULL,
              document_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS receipts(
              project_id TEXT NOT NULL,
              request_id TEXT NOT NULL,
              request_sha256 TEXT NOT NULL,
              result_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY(project_id, request_id),
              FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS events(
              event_id INTEGER PRIMARY KEY AUTOINCREMENT,
              project_id TEXT NOT NULL,
              revision INTEGER NOT NULL,
              kind TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(project_id, revision),
              FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS production_receipts(
              receipt_id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              generation TEXT NOT NULL,
              revision INTEGER NOT NULL CHECK(revision >= 0),
              request_id TEXT NOT NULL,
              request_sha256 TEXT NOT NULL,
              command TEXT NOT NULL,
              stage TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            -- Derived proxy metadata is disposable, and never grants render authority.
            CREATE TABLE IF NOT EXISTS render_window_cache_entries(
              project_id TEXT NOT NULL,
              generation TEXT NOT NULL,
              revision INTEGER NOT NULL CHECK(revision >= 0),
              deliverable_id TEXT NOT NULL,
              source_start INTEGER NOT NULL CHECK(source_start >= 0),
              source_end INTEGER NOT NULL CHECK(source_end > source_start),
              fingerprint_sha256 TEXT NOT NULL,
              PRIMARY KEY(project_id, generation, revision, deliverable_id,
                          source_start, source_end, fingerprint_sha256),
              FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS render_window_cache_by_owner
              ON render_window_cache_entries(project_id, generation, deliverable_id);

            -- A commit atomically revokes cache registrations. Recoverable
            -- eviction records retain only derived-artifact keys, never source.
            CREATE TABLE IF NOT EXISTS render_window_cache_evictions(
              project_id TEXT NOT NULL,
              generation TEXT NOT NULL,
              revision INTEGER NOT NULL CHECK(revision >= 0),
              deliverable_id TEXT NOT NULL,
              source_start INTEGER NOT NULL CHECK(source_start >= 0),
              source_end INTEGER NOT NULL CHECK(source_end > source_start),
              fingerprint_sha256 TEXT NOT NULL,
              PRIMARY KEY(project_id, generation, revision, deliverable_id,
                          source_start, source_end, fingerprint_sha256),
              FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            CREATE TRIGGER IF NOT EXISTS render_window_cache_invalidate_revision
            AFTER UPDATE OF generation, revision ON projects
            WHEN OLD.generation<>NEW.generation OR OLD.revision<>NEW.revision
            BEGIN
              INSERT OR IGNORE INTO render_window_cache_evictions(
                project_id,generation,revision,deliverable_id,
                source_start,source_end,fingerprint_sha256)
                SELECT project_id,generation,revision,deliverable_id,
                       source_start,source_end,fingerprint_sha256
                FROM render_window_cache_entries
                WHERE project_id=NEW.id
                  AND (generation<>NEW.generation OR revision<>NEW.revision);
              DELETE FROM render_window_cache_entries
              WHERE project_id=NEW.id
                AND (generation<>NEW.generation OR revision<>NEW.revision);
            END;

            CREATE INDEX IF NOT EXISTS events_by_project_revision
              ON events(project_id, revision);
            CREATE INDEX IF NOT EXISTS production_receipts_by_project
              ON production_receipts(project_id, receipt_id DESC);
            CREATE INDEX IF NOT EXISTS production_receipts_by_request
              ON production_receipts(project_id, request_id, receipt_id DESC);

CREATE TABLE production_receipt_order(
           sequence INTEGER PRIMARY KEY AUTOINCREMENT,
           receipt_id TEXT NOT NULL UNIQUE,
           project_id TEXT NOT NULL,
           FOREIGN KEY(receipt_id) REFERENCES production_receipts(receipt_id)
             ON UPDATE CASCADE ON DELETE CASCADE,
           FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
         );

CREATE INDEX production_receipt_order_by_project
           ON production_receipt_order(project_id, sequence DESC);

CREATE TRIGGER production_receipt_order_append
         AFTER INSERT ON production_receipts
         BEGIN
           INSERT INTO production_receipt_order(receipt_id,project_id)
             VALUES(NEW.receipt_id,NEW.project_id);
         END;

ALTER TABLE receipts ADD COLUMN apply_binding_version INTEGER NOT NULL DEFAULT 0
         CHECK (apply_binding_version IN (0, 1));

ALTER TABLE receipts ADD COLUMN apply_binding_json TEXT;

CREATE TABLE cas_store_identity (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            store_id TEXT NOT NULL UNIQUE
        );
        CREATE TABLE cas_import_commits (
            operation_id TEXT PRIMARY KEY NOT NULL,
            root_id TEXT NOT NULL,
            store_id TEXT NOT NULL,
            request_sha256 TEXT NOT NULL,
            project_id TEXT NOT NULL,
            generation TEXT NOT NULL
        );
