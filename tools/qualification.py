"""Fail closed unless the documented release evidence passes."""
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from workbench.common import digest,file_hash

def read(path):return json.loads((ROOT/path).read_text(encoding='utf-8'))

def main():
    build=digest({p.name:file_hash(p) for p in (ROOT/'workbench').glob('*.py')})
    suites=ET.parse(ROOT/'evidence/tests.xml').getroot().findall('testsuite')
    assert sum(int(s.attrib['tests']) for s in suites)==22
    assert all(int(s.attrib.get('failures',0))+int(s.attrib.get('errors',0))==0 for s in suites)
    for name,rows in [('uci',541909),('chicago',50000),('nyc',50000),('superstore',9994),('olist',448440)]:
        evidence=read('evidence/external/'+name+'.json')
        assert evidence['passed'] and evidence['engine_build']==build and not evidence['file_issues']
        assert evidence['source_fingerprint_verified_against_frozen_manifest']
        assert sum(t['input_rows'] for t in evidence['tables'])==rows
        assert all(t['every_field_and_disposition_matches'] for t in evidence['tables'])
    for name in ['original','superstore','seeded']:
        record=read('evidence/compatibility-'+name+'.json')
        assert record['passed'] and record['all_record_dispositions_match'] and record['engine_build']==build
    original=read('evidence/compatibility-original.json')['new_workbench']
    assert (original['accepted'],original['review'],original['excluded'])==(291,10,8)
    assert original['totals']['sales']['sum']=='57507.719800000000'
    assert read('evidence/compatibility-seeded.json')['injected_ground_truth']['all_labels_match']
    assert read('evidence/external/olist.json')['independent_join']['every_order_and_aggregated_value_matches']
    assert all(read('evidence/compatibility-'+name+'.json')['native_excel']['all_clean_values_match'] for name in ['original','superstore'])
    for path in ['evidence/browser/browser-check.json','evidence/clean-install.json','evidence/million-row-check.json','evidence/installed-upgrade.json','evidence/legacy-recipe-rejection.json']:
        assert read(path)['passed'],path
    assert len(read('evidence/external-source-manifest.json')['sources'])==5
    record={'passed':True,'engine_build':build,'workbench_tests':22,'external_sources':5,
        'original_fixture_exact_parity':True,'native_excel_comparisons':2,'seed_42_all_320_labels':True,
        'browser_and_clean_install':True,'historical_project_preservation':True,
        'judgment':'Qualified as the featured portfolio replacement within the documented data contracts and local application limits.'}
    (ROOT/'evidence/qualification.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps(record))

if __name__=='__main__':main()
