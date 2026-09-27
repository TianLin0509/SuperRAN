"""Isolated browser acceptance for the kickoff deliverable."""
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import zipfile

from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parent
OUT=ROOT.parents[1]/'output/playwright/kickoff-20260925'
OUT.mkdir(parents=True,exist_ok=True)

def run():
    script=re.search(r'<script>(.*?)</script>',(ROOT/'index.html').read_text(encoding='utf-8'),re.S)[1]
    (OUT/'script.js').write_text(script,encoding='utf-8')
    subprocess.run(['node','--check',str(OUT/'script.js')],check=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=subprocess.Popen([sys.executable,'-m','http.server',str(port),'--bind','127.0.0.1','--directory',str(ROOT)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    checks=[]
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='msedge',headless=True)
            context=browser.new_context(viewport={'width':1440,'height':1050},permissions=['clipboard-read','clipboard-write'])
            page=context.new_page();errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{port}/',wait_until='networkidle')
            expect(page.locator('.person')).to_have_count(8)
            assert page.locator('body').inner_text().count('???')==0
            checks.append('Eight complete team assignments, no replacement-character placeholders')
            page.screenshot(path=str(OUT/'desktop.png'),full_page=True)
            page.get_by_role('button',name='任荣',exact=True).click()
            expect(page.locator('.person:visible')).to_have_count(1)
            expect(page.locator('.person:visible')).to_contain_text('DHBF')
            page.locator('#person-ren summary').click()
            expect(page.locator('#person-ren .detail-grid')).to_be_visible()
            checks.append('Member filter and task details work')
            page.locator('#person-ren [data-prompt]').click()
            expect(page.locator('#promptDialog')).to_be_visible()
            expect(page.locator('#promptText')).to_have_value(re.compile('我是任荣'))
            page.locator('#copyPrompt').click()
            assert '我是任荣' in page.evaluate('navigator.clipboard.readText()')
            checks.append('Personalized prompt copies to clipboard')
            with page.expect_download() as info:
                page.locator('#downloadPrompt').click()
            download=info.value;download.save_as(str(OUT/'prompt.txt'))
            assert '我是任荣' in (OUT/'prompt.txt').read_text(encoding='utf-8')
            page.keyboard.press('Escape')
            expect(page.locator('#promptDialog')).not_to_be_visible()
            checks.append('Prompt text download and keyboard dialog dismissal work')
            page.get_by_role('button',name='全队',exact=True).click()
            expect(page.locator('.person:visible')).to_have_count(8)
            for link in page.locator('a[download]').all():
                response=page.request.get(f'http://127.0.0.1:{port}/'+link.get_attribute('href'))
                assert response.status==200,link.get_attribute('href')
            checks.append('All eight task files, guide, handoff and package links resolve')
            with page.expect_download() as info:
                page.get_by_role('link',name='下载完整开工包',exact=True).click()
            info.value.save_as(str(OUT/'pack.zip'))
            with zipfile.ZipFile(OUT/'pack.zip') as archive:
                assert len([n for n in archive.namelist() if n.startswith('tasks/')])==8
                assert 'report.py' in archive.namelist() and 'AGENT-INSTRUCTIONS.md' in archive.namelist()
                assert not any(n.endswith('.sqlite') or n=='leader.json' for n in archive.namelist())
            checks.append('Downloaded onboarding package has eight briefs and reporter, no private runtime files')
            page.locator('[data-check=tasks]').check()
            page.reload()
            expect(page.locator('[data-check=tasks]')).to_be_checked()
            page.locator('#resetChecks').click()
            expect(page.locator('[data-check=tasks]')).not_to_be_checked()
            checks.append('Explicitly local meeting checklist persists and resets')
            page.locator('#present').click()
            expect(page.locator('body')).to_have_class('presentation')
            page.locator('#present').click()
            checks.append('Projection font toggle works')
            for width in (375,768,1024,1440):
                page.set_viewport_size({'width':width,'height':950})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
                if width==375:
                    page.screenshot(path=str(OUT/'mobile.png'),full_page=True)
            checks.append('No horizontal overflow at 375/768/1024/1440 pixels')
            page.get_by_role('button',name='张燕',exact=True).click()
            page.emulate_media(media='print')
            expect(page.locator('.person:visible')).to_have_count(8)
            page.pdf(path=str(ROOT/'superran-kickoff.pdf'),format='A4',print_background=True,prefer_css_page_size=True)
            page.emulate_media(media='screen')
            assert not errors,errors
            checks.append('Print includes all eight people even when screen is filtered; no JavaScript errors')
            # The self-contained HTML must also work when opened directly from disk.
            offline=context.new_page();offline.goto((ROOT/'index.html').as_uri())
            expect(offline.locator('.person')).to_have_count(8)
            offline.get_by_role('button',name='张燕',exact=True).click()
            expect(offline.locator('.person:visible')).to_have_count(1)
            checks.append('Standalone HTML opens and filters offline')
            browser.close()
    finally:
        server.terminate();server.wait(timeout=10)
    (OUT/'checks.json').write_text(json.dumps({'count':len(checks),'checks':checks},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':len(checks),'output':str(OUT)},ensure_ascii=False))

if __name__=='__main__':
    run()
