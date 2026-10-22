"""Validate the product Showcase when its feature is present in the target frontend."""
from __future__ import annotations

import os
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

OUT = Path(__file__).resolve().parents[1] / 'artifacts' / 'showcase-ui'
OUT.mkdir(parents=True, exist_ok=True)
frontend = os.environ.get('VALIDATION_FRONTEND', 'http://127.0.0.1:13000')
with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    page.goto(f'{frontend}/showcase', wait_until='networkidle')
    expect(page.locator('.showcaseHero h1')).to_contain_text('Find the cause')
    expect(page.get_by_text('ACTUAL APPLICATION DEMO · READ ONLY', exact=True)).to_be_visible()
    expect(page.locator('.showcaseJourneyCard')).to_have_count(4)
    page.screenshot(path=str(OUT / 'product-showcase-desktop.png'), full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto(f'{frontend}/showcase', wait_until='networkidle')
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), 'product showcase mobile overflow'
    page.screenshot(path=str(OUT / 'product-showcase-mobile.png'), full_page=True)
    browser.close()
print('Showcase desktop and mobile assertions passed')
