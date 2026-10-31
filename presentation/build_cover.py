"""Build a portfolio cover from a genuine rendered interface or report preview."""
from pathlib import Path
import argparse,base64,html,json
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser();p.add_argument('--motion',action='store_true');p.add_argument('--workdir',type=Path);a=p.parse_args()
    config=json.loads((ROOT/'presentation/cover.json').read_text(encoding='utf-8'))
    asset=ROOT/config['image'];assert asset.is_file(),asset
    tokens=json.loads((ROOT/config['tokens']).read_text(encoding='utf-8'))
    raw=tokens.get('colors',tokens.get('css_variables',{}))
    colors={key:raw[value] for key,value in config['color_roles'].items()}
    template=(ROOT/'presentation/cover-template.html').read_text(encoding='utf-8')
    for key,value in {**config,**colors}.items():
        if isinstance(value,str):template=template.replace('@@'+key+'@@',html.escape(value))
    template=template.replace('@@image_data@@','data:image/png;base64,'+base64.b64encode(asset.read_bytes()).decode())
    output=ROOT/'deliverables';output.mkdir(exist_ok=True)
    scratch=(a.workdir or ROOT/'presentation/render-cache').resolve();scratch.mkdir(parents=True,exist_ok=True)
    pagefile=scratch/'cover.html';pagefile.write_text(template,encoding='utf-8')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        context=browser.new_context(viewport={'width':1280,'height':960},record_video_dir=str(scratch/'video') if a.motion else None)
        page=context.new_page();page.goto(pagefile.as_uri());page.wait_for_timeout(1250)
        assert page.locator('img').evaluate('(e)=>e.complete && e.naturalWidth>0')
        assert not page.evaluate('()=>document.documentElement.scrollWidth>innerWidth')
        page.screenshot(path=str(output/'portfolio-cover.png'))
        if a.motion:page.wait_for_timeout(3500)
        video=page.video;context.close()
        if video:video.save_as(str(output/'portfolio-motion.webm'))
        context=browser.new_context(viewport={'width':1280,'height':720},reduced_motion='reduce')
        page=context.new_page();page.goto(pagefile.as_uri());page.screenshot(path=str(output/'portfolio-cover-wide.png'));context.close();browser.close()
    print(config['title']+': covers rendered from '+config['image'])
if __name__=='__main__':main()
