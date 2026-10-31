"""Exercise rapid navigation, exclusion graphics, zero rows and reduced motion."""
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
    with (work/'server.log').open('w') as log:
        server=subprocess.Popen([sys.executable,'-B','-m','workbench.app','--port','8816','--projects',str(work/'projects')],cwd=Path(__file__).resolve().parents[1],env=dict(os.environ,DATA_WORKBENCH_RUNTIME=str(work/'runtime')),stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            for _ in range(100):
                try:urllib.request.urlopen('http://127.0.0.1:8816/api/session',timeout=1);break
                except OSError:time.sleep(.2)
            else:raise RuntimeError('Server did not start')
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel='msedge',headless=True);context=browser.new_context(viewport={'width':1440,'height':1000})
                page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto('http://127.0.0.1:8816');page.get_by_role('button',name='Explore the demo').click();page.locator('.source-card').nth(3).wait_for()
                page.get_by_role('button',name='Run project').click();page.locator('#run-banner').filter(has_text='6 need review').wait_for(timeout=30000)
                for tab in ['recipe','export','sources','review']:page.locator('[data-tab="'+tab+'"]').dispatch_event('click')
                page.wait_for_timeout(1600)
                assert page.locator('[data-tab="review"]').get_attribute('aria-selected')=='true'
                assert page.locator('#review-panel').is_visible()
                assert page.locator('.ring-value').text_content()=='70%'
                page.locator('#result-table').select_option('orders');page.locator('#disposition').select_option('review')
                page.locator('#result-grid tbody tr').filter(has_text='O102').get_by_role('button').click();page.locator('#exclude-record').check();page.get_by_role('button',name='Save decision').click()
                page.get_by_role('button',name='Run project').click();page.locator('#run-banner').filter(has_text='5 need review').wait_for(timeout=30000)
                assert page.locator('.population-segment.excluded').get_attribute('width')=='50'
                assert page.locator('.population-arc').count()==3
                context.close();context=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce')
                page=context.new_page();page.goto('http://127.0.0.1:8816');page.locator('#projects button').first.click()
                page.locator('#run-banner').filter(has_text='5 need review').wait_for();page.get_by_role('tab',name='03 Results & review').click();page.locator('.population-arc').first.wait_for()
                assert page.locator('.population-arc').first.evaluate('(e)=>getComputedStyle(e).animationName')=='none'
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                page.get_by_role('button',name='New project').click();page.locator('#new-name').fill('Zero-row visual check');page.get_by_role('button',name='Create project',exact=True).click()
                page.locator('#project-name').filter(has_text='Zero-row visual check').wait_for()
                fixture=work/'zero.csv';fixture.write_text('id,amount\n',encoding='utf-8');page.locator('#files').set_input_files(str(fixture));page.locator('.source-card').wait_for()
                page.get_by_role('button',name='Set up recipe').click();page.locator('.table-editor').wait_for()
                page.get_by_role('button',name='Run project').click();page.locator('#run-banner').filter(has_text='0 parsed rows').wait_for(timeout=30000)
                assert page.locator('.ring-value').text_content()=='—'
                assert page.locator('.ring-caption').text_content()=='NO PARSED ROWS'
                assert page.locator('.population-arc').count()==0
                assert not errors,errors;context.close();browser.close()
            record={'passed':True,'rapid_navigation_preserves_last_requested_tab':True,'excluded_population_geometry_matches_known_fixture':True,'all_three_status_arcs_render':True,'reduced_motion_disables_donut_animation':True,'zero_rows_show_no_percentage_instead_of_zero_over_zero':True,'mobile_width':390,'mobile_overflow':False,'javascript_errors':errors}
            (out/'edge-verification.json').write_text(json.dumps(record,indent=2),encoding='utf-8');print(json.dumps(record))
        finally:server.terminate();server.wait(timeout=15)


if __name__=='__main__':main()
