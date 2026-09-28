"""Manifest update for Review 03: the deterministic BOQ run of the corrected reader (multiline cells, unconfirmed
parts held), the fixture's printed-part annotation and the two adjudications, and the SAR sample reads for the
compatibility note. Earlier fields are kept."""
import json, sys, pathlib, datetime

S = pathlib.Path(sys.argv[1]); OUT = pathlib.Path(sys.argv[2])
manifest = json.load(open(OUT / "M2-GOLDEN-MANIFEST.json", encoding="utf-8"))
boq = json.load(open(S / "boq_run_r3.json", encoding="utf-8")); sar = json.load(open(S / "sar_sample_reads.json", encoding="utf-8"))
manifest["boq_case"]["current_reader_run_2026_09_28_review03"] = {
    "reader": boq["reader"], "model": boq["model"], "render_dpi": boq["render_dpi"], "golden_fixture": boq["golden_fixture"], "run_at": boq["run_at"],
    "fixture_change": "the SIGA-OSHD-FCN row carries printed_part_number SIGA-OSHD-FC (the sheet cuts the part at the column rule; FCN is the transcriber's completion) and the metrics accept the printed form; notes on 6538-G5's group added",
    "systems": {s: {"doc_id": v["doc_id"], "relative_path": v["relative_path"], "sha256": v["sha256"], "seconds": v["seconds"], "state": v["state"], "outcome": v["accounting"]["outcome"],
                    "accounting": v["accounting"], "coverage": v["coverage"], "notes": v["notes"],
                    "metrics": {k: v["metrics"][k] for k in ("golden_rows", "lines", "row_detection_recall", "part_number_accuracy", "quantity_accuracy", "pair_accuracy", "group_accuracy", "false_removal_rate")},
                    "missing_rows": v["metrics"]["missing_rows"], "wrong_quantity": v["metrics"]["wrong_quantity"], "false_auto_accepts": v["metrics"]["false_auto_accepts"], "wrong_group": v["metrics"]["wrong_group"],
                    "review_rows": [{"target": i_["target"], "reason_code": i_["detail"].get("reason_code"), "catalog_no": i_["detail"].get("catalog_no"), "quantity": i_["detail"].get("quantity"),
                                     "catalog_check": i_["detail"].get("catalog_check"), "catalog_cell": i_["detail"].get("catalog_cell"),
                                     "reason": (i_["detail"].get("reason") or (i_["detail"].get("quantity_parse") or {}).get("rule"))} for i_ in v["issues"]]} for s, v in boq["systems"].items()},
    "adjudications": {
        "SIGA-OSHD-FC": "source crop (scratch crops/fas-SIGA-OSHD-FC-context.png, hashed): the sheet prints SIGA-OSHD-FC with the next glyph cut by the column rule; the reader's literal value is correct as extraction and the identity is incomplete; the fixture's FCN is an unsupported completion, now recorded as printed_part_number",
        "6538-G5": "source crop (crops/fas-6538-G5-context.png): 'Call for Assistance Kit' is a stand-alone item (quantity 4) below the Remote power supply kit's components, under no heading of its own; the reader assigns the heading in force ('Booster Power Supply'), the transcriber left it ungrouped; the sheet prints no rule that settles it -- a group-layout judgement for the owner, not an extractor error"},
    "evidence": "evidence/r5__boq_run_r3.json, evidence/r5__boq_run_r3.log"}
manifest["sar_sample_reads_2026_09_28"] = {"parser_version": sar["parser_version"], "note": "BBY006-GME-SAR-EL-LI-0001 (documents 351, 352: R0; 353: R1) read by the corrected reader on both profiles: the ticked box (text) and the OCR stamp are candidates; where they disagree the record is UR with decision_conflict (before: the stamp won)", "docs": sar["docs"]}
manifest["rebuilt_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
manifest["label_policy"] += "; 2026-09-28 (review 03): the cut part SIGA-OSHD-FC and the 6538-G5 group adjudicated against source crops; the BOQ fixture annotated with the printed form, not corrected to the reader"
json.dump(manifest, open(OUT / "M2-GOLDEN-MANIFEST.json", "w", encoding="utf-8"), indent=1, default=str)
print("manifest updated")
