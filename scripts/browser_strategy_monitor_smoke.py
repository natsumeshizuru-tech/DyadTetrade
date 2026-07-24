from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import ConsoleMessage, sync_playwright


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    screenshot = root / "reports" / "validation" / "strategy-monitor-browser.png"
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
        page.get_by_role("button", name="策略", exact=True).click()
        monitor = page.get_by_role("region", name="多策略运行监控")
        monitor.wait_for(state="visible")
        page.get_by_text("策略不能直连 XtTrader", exact=False).wait_for(state="visible")
        page.screenshot(path=str(screenshot), full_page=True)

        instance_buttons = monitor.locator(":scope > aside > button")
        instances = instance_buttons.count()
        if instances < 2:
            raise AssertionError(f"expected at least two strategy instances, got {instances}")
        if console_errors:
            raise AssertionError(f"browser console errors: {console_errors}")
        print(
            json.dumps(
                {
                    "status": "passed",
                    "strategy_instances": instances,
                    "live_order_notice_visible": True,
                    "console_errors": console_errors,
                    "screenshot": str(screenshot),
                },
                ensure_ascii=False,
            )
        )
        browser.close()


if __name__ == "__main__":
    main()
