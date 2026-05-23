#!/usr/bin/env python3
"""
One-time migration: patch existing archive HTML files to use external
assets/style.css and assets/app.js instead of inline CSS/JS.

Run once after deploying the refactored fetch.py:
    python reskin.py
"""

import os
import re
import sys

ARCHIVE_DIR = "archive"
ASSETS_CSS  = "/assets/style.css"
ASSETS_JS   = "/assets/app.js"

ALREADY_MIGRATED = f'href="{ASSETS_CSS}"'


def patch(html: str, date: str) -> str:
    if ALREADY_MIGRATED in html:
        return html

    # 1. Replace <style>…</style> with <link>
    html = re.sub(
        r"<style>.*?</style>",
        f'<link rel="stylesheet" href="{ASSETS_CSS}">',
        html,
        count=1,
        flags=re.DOTALL,
    )

    # 2. Strip ALL existing <script> blocks (old JS, service worker, old config)
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)

    # 3. Inject config vars + external JS just before </body>
    injection = (
        f"<script>\n"
        f"var SUPA_URL='';\n"
        f"var SUPA_KEY='';\n"
        f"var TODAY='{date}';\n"
        f"</script>\n"
        f'<script src="{ASSETS_JS}"></script>\n'
    )
    html = html.replace("</body>", injection + "</body>", 1)

    return html


def main():
    if not os.path.isdir(ARCHIVE_DIR):
        print(f"✗ no {ARCHIVE_DIR}/ directory found — run from repo root", file=sys.stderr)
        sys.exit(1)

    files = sorted(f for f in os.listdir(ARCHIVE_DIR) if f.endswith(".html"))
    patched = skipped = 0

    for fname in files:
        date = fname[:-5]  # YYYY-MM-DD
        path = os.path.join(ARCHIVE_DIR, fname)
        with open(path, "r", encoding="utf-8") as f:
            original = f.read()
        updated = patch(original, date)
        if updated == original:
            print(f"  skip  {fname}")
            skipped += 1
        else:
            with open(path, "w", encoding="utf-8") as f:
                f.write(updated)
            print(f"  patch {fname}")
            patched += 1

    # Also patch index.html
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            original = f.read()
        date_m = re.search(r"var TODAY='(\d{4}-\d{2}-\d{2})'", original)
        date = date_m.group(1) if date_m else "2026-01-01"
        updated = patch(original, date)
        if updated != original:
            with open("index.html", "w", encoding="utf-8") as f:
                f.write(updated)
            print(f"  patch index.html")
            patched += 1
        else:
            print(f"  skip  index.html")
            skipped += 1

    print(f"\n✓ done — {patched} patched, {skipped} skipped")


if __name__ == "__main__":
    main()
