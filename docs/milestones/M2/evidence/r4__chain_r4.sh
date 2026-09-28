#!/bin/bash
S="C:/Users/moham/AppData/Local/Temp/claude/c--Users-moham-Desktop-dev-dev/be7878a4-f3f7-49c7-8971-5873084fd122/scratchpad/m2r2"
cd "C:/Users/moham/Desktop/dev/dev/ep-platform"
until grep -q "^written" "$S/parser_population_r4_default.log"; do sleep 5; done
PYTHONIOENCODING=utf-8 backend/venv/Scripts/python "$S/run_parser_r4.py" "$S" parser_population_r4_promoted.json cache_r4p promoted > "$S/parser_population_r4_promoted.log" 2>&1
echo "promoted done"
( PYTHONIOENCODING=utf-8 backend/venv/Scripts/python "$S/r5_repair_r4.py" "$S" "$PWD" > "$S/r5_repair_r4.log" 2>&1; echo "repair done" ) &
( cd backend && TEMP=C:/t/m2r TMP=C:/t/m2r venv/Scripts/python -m pytest tests/test_ai_sheet_reader.py tests/test_battery_api.py tests/test_boq_corrections_v2.py tests/test_boq_extraction_v2.py tests/test_boq_geometry_v2.py tests/test_boq_selective_v2.py tests/test_boq_verification_v2.py tests/test_classification_evidence.py tests/test_document_classification_pilot.py tests/test_document_classification_v2.py tests/test_document_control.py tests/test_document_processing_v2.py tests/test_document_routing.py tests/test_document_sync.py tests/test_drawings_module.py tests/test_extraction_m2.py tests/test_extraction_repair.py tests/test_file_sync_v2.py tests/test_file_sync_v2_processing.py tests/test_project_state.py tests/test_repair_tool.py tests/test_submittal.py tests/test_submittal_ai.py tests/test_extraction_m2_review.py tests/test_extraction_m2_review02.py tests/test_design_sheet_extractor.py tests/test_job_thread_sessions.py -q -p no:cacheprovider --basetemp=C:/t/m2r/full_r4 --junitxml="$S/final_r4.xml" > "$S/final_r4.log" 2>&1; echo "suite done" ) &
wait
echo "chain done"
