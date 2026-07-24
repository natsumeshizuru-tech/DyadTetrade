from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main() -> None:
    exercise_subscription = os.getenv("DYADTETRADE_BROWSER_TEST_XTDATA_SUBSCRIPTION") == "true"
    screenshot = Path("reports/validation/live_candidate_market_subscription_ui.png").resolve()
    system_screenshot = Path("reports/validation/live_candidate_system_ui.png").resolve()
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        console_errors: list[str] = []
        page.on(
            "console",
            lambda message: (
                console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.goto("http://127.0.0.1:5173", wait_until="networkidle")

        expect(page.get_by_role("navigation", name="一级导航").get_by_role("button")).to_have_count(
            5
        )
        page.get_by_role("heading", name="交易驾驶舱").wait_for()
        expect(page.get_by_text("真实资金交易默认关闭")).to_be_visible()
        expect(page.get_by_text("QMT 模拟柜台手工委托")).to_have_count(0)

        page.get_by_role("button", name="行情").click()
        page.get_by_role("heading", name="行情", exact=True).wait_for()
        subscription = page.get_by_role("region", name="XtData 行情订阅")
        subscription.wait_for()
        expect(subscription.get_by_label("单股订阅代码")).to_have_value("600519.SH")
        expect(subscription.get_by_role("button", name="启动单股订阅")).to_be_visible()
        expect(subscription.get_by_role("button", name="启动沪深全推")).to_be_visible()
        if exercise_subscription:
            subscription.get_by_role("button", name="启动单股订阅").click()
            subscription.get_by_text("已启动 1 只证券的 tick 订阅").wait_for()
            subscription.get_by_role("button", name="停止全部订阅").click()
            subscription.get_by_role("button", name="确认停止订阅").click()
            subscription.get_by_text("全部行情订阅已停止").wait_for()
        page.screenshot(path=str(screenshot), full_page=True)

        page.get_by_role("button", name="策略").click()
        page.get_by_text("策略监控中心").wait_for()
        page.get_by_role("button", name="系统").click()
        page.get_by_role("heading", name="系统", exact=True).wait_for()
        page.get_by_text("开发者诊断").wait_for()
        page.screenshot(path=str(system_screenshot), full_page=True)

        assert not console_errors, console_errors
        print(
            {
                "primary_navigation_count": 5,
                "default_page": "trading",
                "live_order_controls_hidden": True,
                "market_subscription_panel": True,
                "single_subscription_control": True,
                "whole_market_subscription_control": True,
                "subscription_start_stop_exercised": exercise_subscription,
                "console_errors": 0,
                "market_screenshot": str(screenshot),
                "system_screenshot": str(system_screenshot),
            }
        )
        browser.close()


if __name__ == "__main__":
    main()
