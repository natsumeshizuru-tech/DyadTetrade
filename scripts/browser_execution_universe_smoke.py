from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import ConsoleMessage, sync_playwright


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    screenshot = root / "reports" / "validation" / "execution-universe-browser.png"
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 960})

        def capture_console(message: ConsoleMessage) -> None:
            if message.type == "error":
                console_errors.append(message.text)

        page.on("console", capture_console)
        page.goto("http://127.0.0.1:5173", wait_until="networkidle")
        page.get_by_role("button", name="系统", exact=True).click()
        page.get_by_text("全市场执行池", exact=True).wait_for(state="visible")
        page.get_by_text("6083 个标的", exact=False).wait_for(state="visible")
        page.get_by_text("数据日 20260713", exact=False).wait_for(state="visible")
        page.get_by_text("e8c675057a36", exact=True).wait_for(state="visible")
        page.screenshot(path=str(screenshot), full_page=True)
        if console_errors:
            raise AssertionError(f"browser console errors: {console_errors}")
        print(
            json.dumps(
                {
                    "status": "passed",
                    "universe_symbols": 6083,
                    "required_data_date": "20260713",
                    "snapshot_hash_prefix": "e8c675057a36",
                    "console_errors": console_errors,
                    "screenshot": str(screenshot),
                },
                ensure_ascii=False,
            )
        )
        browser.close()


if __name__ == "__main__":
    main()
