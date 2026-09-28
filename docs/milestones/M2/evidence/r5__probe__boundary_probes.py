# Additional independent boundary probes; synthetic files and objects only.
exec((__import__('pathlib').Path(__file__).parent/'probe_correction.py').read_text().split('bad=scratch/')[0])
from dataclasses import asdict
coverage={'outcome':'bounded','pages_total':13,'pages_visited':list(range(1,13)),'pages_skipped':[{'page':13,'reason':'page scan limit'}],'pages_failed':[],'promoted':False}
p=scratch/'placeholder.pdf';p.write_bytes(b'stat only; reading supplied explicitly')
record={'reference':'PAGE-13','revision':'R0','status':'approved','page':13,'flags':[]}
legacy={'records':[record],'parser_version':'old-parser','profile':'default','read_sha256':'old-bytes','read_at':'original-time'}
r=row(legacy)
first=ds.process(None,None,r,p,scratch,user_id=None,ocr=False,read=([],()),coverage=coverage)
a=__import__('copy').deepcopy(r.extracted)
second=ds.process(None,None,r,p,scratch,user_id=None,ocr=False,read=([],()),coverage=coverage)
result['repeat_bounded']={'first_record':a['records'][0],'second_record':r.extracted['records'][0],'first_envelope_sha':a['read_sha256'],'second_envelope_sha':r.extracted['read_sha256'],'same_unvisited_page':13}
promoted={'records':[record],'parser_version':dc.PARSER_VERSION,'profile':'promoted','read_sha256':'new-bytes','read_at':'promoted-time'}
r=row(promoted)
ds.process(None,None,r,p,scratch,user_id=None,ocr=False,read=([],()),coverage=coverage)
result['promoted_carried_as_default']={'gate':get_settings().extraction_promote_observations,'previous_profile':'promoted','new_profile':r.extracted['profile'],'new_record':r.extracted['records'][0],'mirror_status':r.status,'parser_current':dp.parser_current(r),'reuse_sha':dp._previous_sha(r)}
(OUT/'boundary_probes.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))

