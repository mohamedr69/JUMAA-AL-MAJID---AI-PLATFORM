-- Document Classification V2: the current assessment beside each indexed
-- document of a project, for inspection. Read-only. SQLite (the local
-- database); the same SQL runs on PostgreSQL.
--
--   sqlite3 ep_platform.db < scripts\classification_inspection.sql
--   (set the project id in the WHERE clause below; 2 is EP-30880)
--
-- freshness is judged as app.services.document_classification.freshness
-- does: the same rules, the same content hash and the same processing
-- state as when the assessment was made; the context fingerprint is
-- compared by the application, not here (it needs the project's identity).

SELECT
    d.id                                            AS document_id,
    d.filename,
    d.role                                          AS legacy_role,
    d.state                                         AS processing_state,
    c.primary_type,
    c.stage,
    c.evidence_strength,
    json_extract(c.assessment, '$.basis')           AS basis,
    c.evidence_sources,
    c.component_types,
    c.system_code,
    c.reason,
    c.rules_version,
    c.source,
    c.engineer_confirmed,
    c.created_at                                    AS assessed_at,
    CASE
        WHEN c.id IS NULL THEN 'unassessed'
        WHEN c.rules_version <> 'classify-2026-09-28.4' THEN 'rules_changed'
        WHEN IFNULL(c.content_sha256, '') <> IFNULL(d.sha256, '') THEN 'source_changed'
        WHEN IFNULL(json_extract(c.assessment, '$.source_state'), '') <> d.state THEN 'source_changed'
        ELSE 'current'
    END                                             AS freshness,
    (SELECT COUNT(*) FROM document_classifications h WHERE h.document_id = d.id) AS history_rows
FROM project_documents d
LEFT JOIN document_classifications c
       ON c.document_id = d.id AND c.superseded_at IS NULL
WHERE d.project_id = 2
  AND d.state <> 'removed'
ORDER BY d.relative_path;
