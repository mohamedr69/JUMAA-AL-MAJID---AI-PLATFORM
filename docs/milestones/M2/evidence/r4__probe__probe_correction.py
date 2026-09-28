"""Synthetic boundary probes on the submitted correction; no live data writes."""
import os,sys,pathlib,tempfile,json
from types import SimpleNamespace
from datetime import datetime,timezone
from unittest.mock import patch

ROOT=pathlib.Path(r'C:\Users\moham\Desktop\dev\dev\ep-platform')
OUT=pathlib.Path(__file__).parent
scratch=pathlib.Path(tempfile.mkdtemp(prefix='m2r2p-'))
os.environ.update(DATABASE_URL='sqlite:///:memory:',AI_ENABLED='false',EXTRACTION_PROMOTE_OBSERVATIONS='false',
    DOCUMENT_CLASSIFICATION_V2='false',DATASHEET_LIBRARIES='{}',ARCHIVE_DATASHEET_LIBRARIES='{}',ARCHIVE_SUBMITTAL_LIBRARY='',
    PROJECTS_ROOT='',PROJECTS_ROOT_AUTODETECT='false',CACHE_ROOT=str(scratch/'cache'),LIBRARY_ROOT=str(scratch/'library'),
    UPLOADS_ROOT=str(scratch/'uploads'),COMPLIANCE_KNOWLEDGE_SOURCE='',COMPLIANCE_KNOWLEDGE_AUTODETECT='false',
    COMPLIANCE_KNOWLEDGE_IMPORT_ON_START='false',SYNC_FILE_WORKERS='0')
sys.path.insert(0,str(ROOT/'backend'))
import pymupdf
from app.services import document_control as dc,document_sync as ds,document_processing as dp
from app.core.config import get_settings

NOW=datetime.now(timezone.utc)
result={}
def row(extracted):
    return SimpleNamespace(role='document',sha256='new-bytes',system_code='FAS',reference='GOOD-SDW-001',revision='R0',status='ANN',
        extracted=extracted,last_processed_at=NOW,index_version=ds.INDEX_VERSION,state='fresh',error=None)

bad=scratch/'bad.pdf';bad.write_bytes(b'not pdf')
legacy={'records':[{'reference':'GOOD-SDW-001'}],'notes':[]}
r=row(legacy)
outcome=ds.process(None,None,r,bad,scratch,user_id=None,ocr=False)
result['legacy_last_success_without_parser_version']={'outcome':outcome,'records_before':legacy['records'],'records_after':r.extracted['records'],
    'parser_version_before':legacy.get('parser_version'),'mirror_reference_after':r.reference}

limited=scratch/'limited.pdf'
late_text='SHOP DRAWING SUBMITTAL\nNo: ABC-XYZ-SPM-SD-MEP-FA-0054\nRev: 01\nsubmitting herewith\nDRAWING & DESIGN REF\nABC-XYZ-SPM-SD-MEP/FA-104\nGROUND FLOOR FIRE ALARM LAYOUT\nSubmitted By:\nReceived By:\n'
with pymupdf.open() as p:
    for i in range(12):p.new_page().insert_text((30,30),'Technical package separator and source material.\n' * 3)
    p.new_page().insert_text((30,30),late_text)
    p.save(limited)
r=row({'records':[{'reference':'ABC-XYZ-SPM-SD-MEP-FA-0054','page':13}],'parser_version':'last-success','read_sha256':'old-bytes','read_at':'old-time'})
r.reference='ABC-XYZ-SPM-SD-MEP-FA-0054'
outcome=ds.process(None,None,r,limited,scratch,user_id=None,ocr=False)
result['bounded_replaces_more_complete_success']={'outcome':outcome,'records_after':r.extracted['records'],
    'skipped':r.extracted['coverage']['pages_skipped'],'attempt_present':'attempt' in r.extracted,'mirror_reference_after':r.reference,
    'page13_direct_reference':[r.reference for r in dc.parse_page(late_text,str(limited),NOW,13)]}

cover='SHOP DRAWING SUBMITTAL\nNo: ABC-XYZ-SPM-SD-MEP-FA-0054\nRev: 01\nsubmitting herewith\nDRAWING & DESIGN REF\nABC-XYZ-SPM-SD-MEP/FA-104\nGROUND FLOOR FIRE ALARM LAYOUT\nSubmitted By:\nReceived By:\n'
options='Consultant Recommendation\nA - Approved\nB - Approved With Comments\nC - Revise & Re-Submit\nD - Rejected\n'
def make(p, text):
    page=p.new_page();page.insert_text((30,30),text,fontsize=8);return page
def annotate(page,phrase):
    a=page.add_rect_annot(page.search_for(phrase)[0]+(-2,-2,2,2));a.set_colors(stroke=(0,0.5,0));a.update()
def draw(page,phrase):page.draw_rect(page.search_for(phrase)[0]+(-2,-2,2,2),color=(0,0.5,0),width=3)
def summarize(reading):
    return {'records':[{'status':r.status,'flags':r.flags,'candidates':r.decision_candidates} for r in reading.records],
            'observation_kinds':[o['kind'] for o in reading.observations],'coverage':reading.coverage['outcome']}
with pymupdf.open() as p:
    page=make(p,cover+options);draw(page,'C - Revise & Re-Submit');annotate(page,'A - Approved')
    with patch.object(dc,'_image_regions',return_value=[1]),patch.object(dc,'_prefer_full_page',return_value=True),\
         patch.object(dc,'_ocr_text',return_value='Review status: (A) Approved'):
        reading=dc.read_open_pdf(p,'conflict.pdf',NOW,True,None,full=True,promote=False)
    result['ocr_overrides_collected_conflict']=summarize(reading)

with pymupdf.open() as p:
    page=make(p,cover+options+'Review status: (A) Approved\n');draw(page,'C - Revise & Re-Submit')
    reading=dc.read_open_pdf(p,'text-conflict.pdf',NOW,False,None,full=True,promote=True)
    result['text_decision_bypasses_mark_collection']=summarize(reading)

gate_file=scratch/'gate.pdf'
with pymupdf.open() as p:make(p,cover);p.save(gate_file)
sha=ds.sha256_of(gate_file)
r=row({'records':[{'reference':'GOOD-SDW-001','status':'approved'}],'parser_version':dc.PARSER_VERSION,'read_sha256':sha,
    'coverage':{'outcome':'complete','promoted':True}})
r.sha256=sha
previous=dp._previous_sha(r)
read=dp.read_task(str(gate_file),'gate.pdf',previous,False)
result['promoted_reading_reused_with_gate_off']={'current_gate':get_settings().extraction_promote_observations,
    'stored_gate':r.extracted['coverage']['promoted'],'parser_current':dp.parser_current(r),'previous_sha_reused':previous==sha,
    'task_unchanged':read.get('unchanged')}

(OUT/'independent_probes.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
