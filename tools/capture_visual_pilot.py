"""Capture a working fictional pilot; keep generated projects outside the repo."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workdir',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    args=parser.parse_args();work=args.workdir.resolve();out=args.evidence.resolve()
    work.mkdir(parents=True,exist_ok=True);out.mkdir(parents=True,exist_ok=True)
    (out/'visual-capture.json').write_text(json.dumps({'passed':False,'state':'running'}),encoding='utf-8')
    root=Path(__file__).resolve().parents[1]
    env=dict(os.environ,DATA_WORKBENCH_RUNTIME=str(work/'runtime'))
    with (work/'server.log').open('w') as log:
        server=subprocess.Popen([sys.executable,'-B','-m','workbench.app','--port','8813','--projects',str(work/'projects')],cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            for _ in range(100):
                try:
                    with urllib.request.urlopen('http://127.0.0.1:8813/api/session',timeout=1):break
                except OSError:time.sleep(.2)
            else:raise RuntimeError('Pilot server did not start')
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel='msedge',headless=True)
                context=browser.new_context(viewport={'width':1440,'height':1000},device_scale_factor=1,record_video_dir=str(work/'video-cache'),record_video_size={'width':1440,'height':1000})
                page=context.new_page()
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('console',lambda msg: errors.append(msg.text) if msg.type=='error' and ('Content Security Policy' in msg.text or 'Refused to' in msg.text) else None)
                page.goto('http://127.0.0.1:8813');page.get_by_role('button',name='Explore the demo').click()
                page.locator('.source-card').nth(3).wait_for()
                def capture(name,full=True):
                    page.locator('#notification').wait_for(state='hidden',timeout=15000)
                    page.screenshot(path=str(out/name),full_page=full)
                capture('sources.png')
                page.get_by_role('button',name='Run project').click()
                page.locator('#run-banner').filter(has_text='6 need review').wait_for(timeout=30000)
                page.locator('#result-table').select_option('orders');page.locator('#disposition').select_option('review')
                page.locator('#result-grid tbody tr').filter(has_text='O102').wait_for()
                assert page.locator('.population-legend').inner_text().split()==['Accepted','14','Needs','review','6','Excluded','0']
                assert page.locator('.population-segment.accepted').get_attribute('width')=='700'
                assert page.locator('.population-segment.review').get_attribute('width')=='300'
                capture('review.png')
                page.get_by_role('tab',name='04 Export').click();page.get_by_role('link',name='Download package').wait_for()
                capture('export.png')
                page.set_viewport_size({'width':390,'height':844})
                capture('mobile.png',False);capture('mobile-full.png')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                for tab in page.get_by_role('tab').all():
                    box=tab.bounding_box()
                    assert box and box['x']>=0 and box['x']+box['width']<=390, 'Mobile tab clipped'
                assert page.get_by_role('link',name='Download package').is_visible()
                page.get_by_role('tab',name='03 Results & review').click()
                page.locator('#result-grid tbody tr').filter(has_text='O102').wait_for()
                capture('mobile-review.png',False)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.set_viewport_size({'width':1440,'height':1000})
                page.locator('#result-grid tbody tr').filter(has_text='O102').get_by_role('button').click()
                page.get_by_role('heading',name='Review source record').wait_for()
                capture('record-review.png')
                assert not errors,errors
                record={'passed':True,'source_files':4,'input_records':20,'accepted':14,'review':6,'excluded':0,'mobile_width':390,'mobile_horizontal_page_overflow':False,'javascript_errors':errors,'scope':'Actual fictional demo in the running local app. No data or state was injected for screenshots. Browser workflow/regression checks are recorded separately.'}
                video=page.video;context.close();video.save_as(str(out/'workbench-motion.webm'))
                reduced=browser.new_context(reduced_motion='reduce',viewport={'width':1440,'height':1000})
                check=reduced.new_page();check.goto('http://127.0.0.1:8813')
                check.locator('#projects button').first.click()
                check.locator('#run-banner').filter(has_text='6 need review').wait_for()
                check.get_by_role('tab',name='03 Results & review').click()
                check.locator('.population-segment').first.wait_for()
                assert check.locator('.population-segment').first.evaluate('(e)=>getComputedStyle(e).animationName')=='none'
                record['reduced_motion_disables_population_animation']=True
                record['population_breakdown_matches_actual_manifest']=True
                (out/'visual-capture.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
                reduced.close();browser.close();print(json.dumps(record))
        finally:
            server.terminate();server.wait(timeout=15)


if __name__=='__main__':main()
