-- M1 review evidence: SELECT/PRAGMA only. Open SQLite with ?mode=ro and PRAGMA query_only=1.
-- Collected 2026-09-27T18:02:39+00:00; database backend/ep_platform.db (the DATABASE_URL of backend/.env, resolved from backend/).
-- Date-shaped reference: Python re.fullmatch of \d{1,2}[-/.](?:\d{1,2}|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[-/.]\d{2,4}  (fullmatch, case-insensitive, on the stripped value; the earlier M1 baseline's regex, build_baseline.py line 115)
-- The two date metrics are computed in Python (m1_evidence.py) over the two result sets below.

-- population
SELECT COUNT(*) AS indexed_documents, SUM(CASE WHEN extracted IS NOT NULL THEN 1 ELSE 0 END) AS extracted_json_present, SUM(CASE WHEN COALESCE(json_array_length(extracted,'$.records'),0)>0 THEN 1 ELSE 0 END) AS documents_with_records, SUM(COALESCE(json_array_length(extracted,'$.records'),0)) AS total_extracted_records FROM project_documents;

-- projects
SELECT id, ep_number, status FROM projects ORDER BY id;

-- table_counts
SELECT 'projects' AS entity, COUNT(*) AS n FROM projects UNION ALL SELECT 'project_documents', COUNT(*) FROM project_documents UNION ALL SELECT 'document_classifications_all', COUNT(*) FROM document_classifications UNION ALL SELECT 'document_classifications_current', COUNT(*) FROM document_classifications WHERE superseded_at IS NULL UNION ALL SELECT 'document_dependencies', COUNT(*) FROM document_dependencies UNION ALL SELECT 'project_shop_drawings', COUNT(*) FROM project_shop_drawings UNION ALL SELECT 'shop_drawing_revisions', COUNT(*) FROM shop_drawing_revisions UNION ALL SELECT 'shop_drawing_candidates', COUNT(*) FROM shop_drawing_candidates UNION ALL SELECT 'drawing_issues_open', COUNT(*) FROM drawing_issues WHERE resolved_at IS NULL UNION ALL SELECT 'project_submittals', COUNT(*) FROM project_submittals UNION ALL SELECT 'project_submittal_revisions', COUNT(*) FROM project_submittal_revisions UNION ALL SELECT 'project_submittal_status_history', COUNT(*) FROM project_submittal_status_history UNION ALL SELECT 'submittal_replies', COUNT(*) FROM submittal_replies UNION ALL SELECT 'project_actions', COUNT(*) FROM project_actions UNION ALL SELECT 'battery_panel_results', COUNT(*) FROM battery_panel_results UNION ALL SELECT 'project_designs', COUNT(*) FROM project_designs UNION ALL SELECT 'project_amplifier_design', COUNT(*) FROM project_amplifier_design UNION ALL SELECT 'project_floor_schedule', COUNT(*) FROM project_floor_schedule UNION ALL SELECT 'project_ifc_drawings', COUNT(*) FROM project_ifc_drawings UNION ALL SELECT 'project_building_floors', COUNT(*) FROM project_building_floors UNION ALL SELECT 'project_boq_items', COUNT(*) FROM project_boq_items UNION ALL SELECT 'project_boq_revisions', COUNT(*) FROM project_boq_revisions UNION ALL SELECT 'boq_candidates', COUNT(*) FROM boq_candidates UNION ALL SELECT 'project_proposed_materials', COUNT(*) FROM project_proposed_materials UNION ALL SELECT 'compliance_statements', COUNT(*) FROM compliance_statements UNION ALL SELECT 'compliance_audit', COUNT(*) FROM compliance_audit UNION ALL SELECT 'design_rules_current', COUNT(*) FROM design_rules WHERE superseded_at IS NULL UNION ALL SELECT 'equipment_currents', COUNT(*) FROM equipment_currents UNION ALL SELECT 'document_readings_submittal_map', COUNT(*) FROM document_readings WHERE kind='submittal_map' UNION ALL SELECT 'estimation_projects', COUNT(*) FROM estimation_projects UNION ALL SELECT 'division_projects', COUNT(*) FROM division_projects;

-- classification_coverage
SELECT p.id AS project_id, p.ep_number, COUNT(d.id) AS documents, COUNT(c.id) AS current_classifications FROM projects p LEFT JOIN project_documents d ON d.project_id=p.id LEFT JOIN document_classifications c ON c.document_id=d.id AND c.superseded_at IS NULL GROUP BY p.id ORDER BY p.id;

-- classification_rules_stages
SELECT rules_version, stage, COUNT(*) AS n FROM document_classifications WHERE superseded_at IS NULL GROUP BY rules_version, stage ORDER BY rules_version, stage;

-- parser_storage
SELECT COUNT(*) AS documents_with_parser_version FROM project_documents WHERE json_extract(extracted,'$.parser_version') IS NOT NULL;

-- evidence_storage
SELECT COUNT(*) AS documents_with_evidence FROM project_documents WHERE json_extract(extracted,'$.evidence') IS NOT NULL;

-- top_level_reference_date_shaped_candidates
SELECT id, reference FROM project_documents WHERE reference IS NOT NULL ORDER BY id;

-- embedded_record_references
SELECT d.id, j.value AS reference FROM project_documents d, json_each(d.extracted, '$.records') r, json_tree(r.value) j WHERE j.key='reference' AND j.value IS NOT NULL AND j.fullkey NOT LIKE '%.records[%].%.%' ORDER BY d.id;

-- embedded_paths_for_ids
SELECT id, relative_path, path FROM project_documents WHERE id IN ({ids});

-- example_687
SELECT id, project_id, reference AS top_level_reference, json_extract(extracted,'$.records[2].reference') AS third_record_reference FROM project_documents WHERE id=687;

-- generic_reference_top_level_exact
SELECT id FROM project_documents WHERE reference='ICC-DLRC-SPM-SD-MEP' ORDER BY id;

-- generic_reference_top_level_prefix
SELECT id FROM project_documents WHERE reference LIKE 'ICC-DLRC-SPM-SD-MEP%' ORDER BY id;

-- generic_reference_embedded_exact
SELECT DISTINCT d.id FROM project_documents d, json_each(d.extracted, '$.records') r WHERE json_extract(r.value, '$.reference')='ICC-DLRC-SPM-SD-MEP' ORDER BY d.id;

-- dependencies
SELECT dependent_type, stale, COUNT(*) AS n FROM document_dependencies GROUP BY dependent_type, stale ORDER BY dependent_type;

-- submittals
SELECT status, COUNT(*) AS n FROM project_submittals GROUP BY status;

-- submittal_status_history_sources
SELECT source, COUNT(*) AS n FROM project_submittal_status_history GROUP BY source;

-- submittal_revisions_manual_vs_ai
SELECT h.source, COUNT(DISTINCT h.revision_id) AS revisions FROM project_submittal_status_history h GROUP BY h.source;

-- drawing_revisions
SELECT status, source, COUNT(*) AS n FROM shop_drawing_revisions GROUP BY status, source ORDER BY status;

-- shop_drawings_confirmed
SELECT p.ep_number, COUNT(*) AS drawings, SUM(CASE WHEN d.confirmed_by_id IS NOT NULL THEN 1 ELSE 0 END) AS confirmed FROM project_shop_drawings d JOIN projects p ON p.id=d.project_id GROUP BY p.ep_number;

-- drawing_issues_open
SELECT kind, source, COUNT(*) AS n FROM drawing_issues WHERE resolved_at IS NULL GROUP BY kind, source;

-- actions
SELECT kind, CASE WHEN resolved_at IS NULL THEN 'open' ELSE 'resolved' END AS state, COUNT(*) AS n FROM project_actions GROUP BY kind, state;

-- battery_results
SELECT p.ep_number, r.state, COUNT(*) AS n FROM battery_panel_results r JOIN projects p ON p.id=r.project_id GROUP BY p.ep_number, r.state;

-- floors
SELECT p.ep_number, f.source, COUNT(*) AS n FROM project_building_floors f JOIN projects p ON p.id=f.project_id GROUP BY p.ep_number, f.source;

-- boq_items
SELECT p.ep_number, i.origin, COUNT(*) AS n FROM project_boq_items i JOIN projects p ON p.id=i.project_id GROUP BY p.ep_number, i.origin;

-- design_rule_categories
SELECT category, COUNT(*) AS current_rules, SUM(CASE WHEN json_extract(data,'$.auto')=1 THEN 1 ELSE 0 END) AS auto_written FROM design_rules WHERE superseded_at IS NULL GROUP BY category;

-- schema_tables
SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;

-- project_documents_columns
PRAGMA table_info(project_documents);

-- alembic
SELECT version_num FROM alembic_version;
