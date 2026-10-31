"""Record the real working app as a short, clean product motion study."""
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
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workdir',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);a=p.parse_args()
    work=a.workdir.resolve();out=a.evidence.resolve();work.mkdir(parents=True,exist_ok=True);out.mkdir(parents=True,exist_ok=True)
    app=Path(__file__).resolve().parents[1]
    with (work/'server.log').open('w') as log:
        server=subprocess.Popen([sys.executable,'-B','-m','workbench.app','--port','8814','--projects',str(work/'projects')],cwd=app,env=dict(os.environ,DATA_WORKBENCH_RUNTIME=str(work/'runtime')),stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            for _ in range(100):
                try:urllib.request.urlopen('http://127.0.0.1:8814/api/session',timeout=1);break
                except OSError:time.sleep(.2)
            else:raise RuntimeError('Server did not start')
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel='msedge',headless=True)
                prepare=browser.new_page();prepare.goto('http://127.0.0.1:8814');prepare.get_by_role('button',name='Explore the demo').click();prepare.locator('.source-card').nth(3).wait_for();prepare.close()
                context=browser.new_context(viewport={'width':1440,'height':1000},record_video_dir=str(work/'video-cache'),record_video_size={'width':1440,'height':1000})
                page=context.new_page();start=time.perf_counter();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('console',lambda msg:errors.append(msg.text) if msg.type=='error' and ('Content Security Policy' in msg.text or 'Refused to' in msg.text) else None)
                page.goto('http://127.0.0.1:8814');page.locator('#projects button').first.click();page.locator('.source-card').nth(3).wait_for()
                chapters={'sources':round(time.perf_counter()-start,2)}
                page.wait_for_timeout(1100);page.locator('.source-card').first.hover();page.wait_for_timeout(700)
                page.get_by_role('button',name='Run project').click();page.locator('#run-banner').filter(has_text='6 need review').wait_for(timeout=30000)
                chapters['reconcile']=round(time.perf_counter()-start,2)
                page.locator('#notification').wait_for(state='hidden',timeout=10000)
                assert page.locator('.ring-value').text_content()=='70%'
                assert page.locator('.population-segment.accepted').get_attribute('width')=='700'
                page.locator('.metric').nth(2).hover();page.wait_for_timeout(700)
                page.screenshot(path=str(out/'workbench-art-direction.png'),full_page=True)
                page.get_by_role('tab',name='04 Export').click();page.get_by_role('link',name='Download package').wait_for()
                chapters['export']=round(time.perf_counter()-start,2)
                page.wait_for_timeout(1000);page.get_by_role('link',name='Download package').hover();page.wait_for_timeout(1300)
                assert not errors,errors
                video=page.video;context.close();video.save_as(str(out/'workbench-product-study.webm'))
                browser.close()
                record={'passed':True,'chapters_seconds':chapters,'input':20,'accepted':14,'review':6,'excluded':0,'accepted_share_display':'70%','javascript_or_csp_errors':errors,'recording':'Actual application, actual fictional demo, actual processing. No screenshot composites or invented metrics.','capture':'Desktop only; separate responsive and reduced-motion checks are retained.'}
                (out/'motion-study.json').write_text(json.dumps(record,indent=2),encoding='utf-8');print(json.dumps(record))
        finally:server.terminate();server.wait(timeout=15)


if __name__=='__main__':main()
