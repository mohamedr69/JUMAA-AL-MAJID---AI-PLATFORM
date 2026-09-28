import pathlib
p = pathlib.Path(r"C:\Users\moham\Desktop\dev\dev\ep-platform\backend\app\services\document_control.py"); s = p.read_text(encoding="utf-8")
old1 = 'observations.append({"page": number, "kind": "transmittal", "records": [asdict(r) for r in sent]})'
new1 = 'observations.append({"page": number, "kind": "transmittal", "records": [observed_record(r) for r in sent]})'
old2 = 'observations.extend({"page": number, "kind": "cover_untracked", "record": asdict(row)} for row in held)'
new2 = 'observations.extend({"page": number, "kind": "cover_untracked", "record": observed_record(row)} for row in held)'
assert old1 in s and old2 in s
s = s.replace(old1, new1).replace(old2, new2)
anchor = "def untracked_sheet_observation(text: str) -> dict | None:"
helper = '''def observed_record(row: "ControlledDocument") -> dict:
    """A record held as an observation, as JSON stores it: the stored-record
    shape (document_sync._record_dict) with the datetime and the path as
    text. `asdict` alone left `modified` a datetime and the row's JSON column
    refused it -- 35 of 878 rows of the M2 review-01 clone repair failed on
    exactly that (every untracked-discipline cover and scanned transmittal),
    and the ordinary writer would have failed the same rows."""
    data = asdict(row)
    modified = data.get("modified")
    if hasattr(modified, "isoformat"):
        data["modified"] = modified.isoformat()
    if data.get("path") is not None:
        data["path"] = str(data["path"])
    return data


'''
assert anchor in s
s = s.replace(anchor, helper + anchor, 1)
p.write_text(s, encoding="utf-8"); print("document_control patched")
