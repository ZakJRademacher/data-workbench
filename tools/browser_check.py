"""Headless Edge checks for the actual local app, including upload and review."""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--workdir',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    args=parser.parse_args()
    root=args.workdir.resolve();root.mkdir(parents=True,exist_ok=True)
    evidence=args.evidence.resolve();evidence.mkdir(parents=True,exist_ok=True)
    app_root=Path(__file__).resolve().parents[1]
    env=dict(os.environ,DATA_WORKBENCH_RUNTIME=str(root/'runtime'))
    server=subprocess.Popen([sys.executable,'-B','-m','workbench.app','--port','8811','--projects',str(root/'projects')],cwd=app_root,env=env,stdout=(root/'server.log').open('w'),stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        for _ in range(80):
            try: urllib.request.urlopen('http://127.0.0.1:8811/api/session',timeout=1);break
            except OSError: time.sleep(.1)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1)
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:8811')
            page.get_by_role('heading',name='Bring your data into focus.').wait_for()
            page.screenshot(path=str(evidence/'welcome.png'),full_page=True)
            page.get_by_role('button',name='Explore the demo').click()
            page.locator('.source-card').nth(3).wait_for()
            page.screenshot(path=str(evidence/'sources.png'),full_page=True)
            page.get_by_role('button',name='Set up recipe').click()
            page.locator('.table-editor').nth(2).wait_for()
            page.screenshot(path=str(evidence/'recipe.png'),full_page=True)
            page.get_by_role('button',name='Run project').click()
            page.locator('#run-banner').filter(has_text='20 parsed rows').wait_for(timeout=30000)
            page.wait_for_timeout(300)
            assert page.locator('#review-count').inner_text()=='6'
            page.locator('#result-table').select_option('orders')
            page.locator('#disposition').select_option('review')
            page.wait_for_timeout(300)
            assert page.locator('#result-grid tbody tr').count()==4
            page.screenshot(path=str(evidence/'review.png'),full_page=True)
            page.locator('#result-grid tbody tr').filter(has_text='O102').get_by_role('button').click()
            page.get_by_role('heading',name='Review source record').wait_for()
            assert 'invalid date' in page.locator('#modal-body .note').inner_text()
            page.screenshot(path=str(evidence/'record-review.png'),full_page=True)
            page.locator('#exclude-record').check()
            page.get_by_role('button',name='Save decision').click()
            page.locator('#run-banner').filter(has_text='stale').wait_for()
            page.get_by_role('button',name='Run project').click()
            page.locator('#run-banner').filter(has_text='5 need review').wait_for(timeout=30000)
            page.get_by_role('tab',name='04 Export').click()
            page.get_by_role('link',name='Download package').wait_for()
            page.screenshot(path=str(evidence/'export.png'),full_page=True)
            with page.expect_download() as download_info:
                page.get_by_role('link',name='Download package').click()
            downloaded=download_info.value
            downloaded.save_as(str(root/'browser-package.zip'))
            assert (root/'browser-package.zip').stat().st_size>1000
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path=str(evidence/'mobile.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.set_viewport_size({'width':1440,'height':1000})
            # Resolve a fresh demo through the visual controls, then approve its joins.
            page.goto('http://127.0.0.1:8811')
            page.get_by_role('button',name='Explore the demo').click()
            page.locator('.source-card').nth(3).wait_for()
            page.get_by_role('button',name='Run project').click()
            page.locator('#run-banner').filter(has_text='6 need review').wait_for(timeout=30000)
            page.locator('#result-table').select_option('orders')
            page.locator('#disposition').select_option('review')
            for order,field,value in [('O102',2,'2026-01-05'),('O104',3,'30.00')]:
                page.locator('#result-grid tbody tr').filter(has_text=order).get_by_role('button').click()
                page.locator(f'[data-correct-enable="{field}"]').check()
                page.locator(f'[data-correct="{field}"]').fill(value)
                page.get_by_role('button',name='Save decision').click()
                page.locator('#modal').wait_for(state='hidden')
            page.locator('#result-table').select_option('customers')
            page.locator('#result-grid tbody tr').filter(has_text='June B.').get_by_role('button').click()
            page.locator('#exclude-record').check()
            page.get_by_role('button',name='Save decision').click()
            page.get_by_role('tab',name='02 Recipe & relationships').click()
            page.locator('[data-dup="1"]').select_option('drop_exact')
            page.get_by_role('button',name='Run project').click()
            page.locator('#run-banner').filter(has_text='Project reconciled').wait_for(timeout=30000)
            page.get_by_role('tab',name='02 Recipe & relationships').click()
            for lookup in ['customers','payments']:
                page.locator('.suggestion').filter(has_text='orders → '+lookup).get_by_role('button').click()
            page.locator('[data-add-agg="0,1"]').click()
            page.locator('[data-agg-name="0,1,0"]').fill('paid_total')
            page.locator('[data-agg-name="0,1,0"]').press('Tab')
            page.get_by_role('button',name='Preview joins',exact=True).click()
            page.locator('.join-report-card').filter(has_text='7 → 7 rows').wait_for(timeout=30000)
            page.get_by_role('button',name='Approve these relationships').click()
            page.locator('.approved').nth(1).wait_for()
            page.screenshot(path=str(evidence/'approved-relationships.png'),full_page=True)
            page.get_by_role('button',name='Run project').click()
            page.locator('#run-banner').filter(has_text='Project reconciled').wait_for(timeout=30000)
            page.locator('#result-table').select_option('orders_combined')
            page.locator('#disposition').select_option('accepted')
            page.locator('#result-grid tbody tr').filter(has_text='O100').wait_for()
            assert page.locator('#result-grid tbody tr').count()==7
            page.screenshot(path=str(evidence/'combined-view.png'),full_page=True)
            page.get_by_role('button',name='New project').click()
            page.locator('#new-name').fill('Browser upload check')
            page.get_by_role('button',name='Create project',exact=True).click()
            page.locator('#project-name').filter(has_text='Browser upload check').wait_for()
            source=root/'browser.csv';source.write_text('id,amount\n001,19.9900\n',encoding='utf-8')
            page.locator('#files').set_input_files(str(source))
            page.locator('.source-card').wait_for()
            page.get_by_role('button',name='Set up recipe').click()
            page.locator('.table-editor').wait_for()
            page.get_by_role('button',name='Run project').click()
            page.locator('#run-banner').filter(has_text='Project reconciled').wait_for(timeout=30000)
            # Exercise the new rule controls without relying on advanced JSON editing.
            page.get_by_role('button',name='New project').click()
            page.locator('#new-name').fill('Source format controls')
            page.get_by_role('button',name='Create project',exact=True).click()
            page.locator('#project-name').filter(has_text='Source format controls').wait_for()
            a=root/'a.csv';b=root/'b.csv'
            a.write_text('id,ordered,shipped,amount\n001,2026-01-05,2026-01-04,12.34567\n',encoding='utf-8')
            b.write_text('id,ordered,shipped,amount\n002,01/05/2026,01/06/2026,2.5000\n',encoding='utf-8')
            page.locator('#files').set_input_files([str(a),str(b)])
            page.locator('.source-card').nth(1).wait_for()
            page.get_by_role('button',name='Set up recipe').click()
            page.locator('[data-rules="0,3"]').click()
            page.locator('#rule-places').fill('4');page.locator('#rule-precision').select_option('strict')
            page.get_by_role('button',name='Save',exact=True).click()
            page.locator('.source-mappings > .mapping-row').filter(has_text='b.csv').get_by_role('button',name='Source format').click()
            page.locator('#format-name').fill('US dates')
            for i in [0,1]:
                page.locator(f'[data-format-enable="{i}"]').check()
                page.locator(f'[data-format-date="{i}"]').select_option('%m/%d/%Y')
            page.get_by_role('button',name='Save',exact=True).click()
            page.get_by_role('button',name='Cross-field checks').click()
            page.get_by_role('button',name='Add comparison').click()
            page.locator('[data-check-left="0"]').select_option('shipped')
            page.locator('[data-check-right="0"]').select_option('ordered')
            page.get_by_role('button',name='Save',exact=True).click()
            page.get_by_role('button',name='Run project').click()
            page.locator('#run-banner').filter(has_text='2 parsed rows · 1 need review').wait_for(timeout=30000)
            page.screenshot(path=str(evidence/'v2-rule-results.png'),full_page=True)
            assert not errors,errors
            (evidence/'browser-check.json').write_text(json.dumps({'passed':True,'browser':'Microsoft Edge / Playwright headless','javascript_errors':errors,'checks':['demo intake','recipe controls','run reconciliation','review decision','stale warning','rerun','package download','mobile width','own-file upload','field corrections','duplicate policy','relationship suggestions','child aggregation','join preview and approval','combined view','source format controls','decimal precision controls','cross-field rule controls']},indent=2))
            print('Browser workflow passed; screenshots and evidence saved.')
            browser.close()
    finally:
        server.terminate();server.wait(timeout=15)


if __name__=='__main__': main()
