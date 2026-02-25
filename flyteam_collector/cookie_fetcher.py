"""
Playwright を使ってFlyTeamのJSチャレンジを突破し、Cookieを取得する。
"""
import logging
from typing import Dict

logger = logging.getLogger(__name__)

BASE_URL = "https://flyteam.jp"


async def fetch_cookies(region: str = "asia") -> Dict[str, str]:
    """
    Playwrightで /area/{region}/airline にアクセスし、Cookie を dict で返す。
    """
    from playwright.async_api import async_playwright

    cookies_dict: Dict[str, str] = {}
    url = f"{BASE_URL}/area/{region}/airline"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/119.0.0.0 Safari/537.36"
            ),
            locale="ja-JP",
        )
        page = await context.new_page()

        logger.info(f"Playwright: {url} にアクセス中...")

        # domcontentloaded で待機（networkidle は広告で永久に待つ）
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # コンテンツが描画されるまで最大15秒待機
        try:
            await page.wait_for_selector("a[href*='/airline/']", timeout=15000)
            logger.info("Playwright: コンテンツ確認済み（航空会社リンク検出）")
        except Exception:
            # セレクタが見つからなくてもCookieは取れている可能性がある
            logger.warning("Playwright: 航空会社リンク未検出（Cookieのみ取得を試行）")

        # Cookie 取得
        raw_cookies = await context.cookies()
        for c in raw_cookies:
            if c["domain"].endswith("flyteam.jp"):
                cookies_dict[c["name"]] = c["value"]

        logger.info(f"Playwright: Cookie {len(cookies_dict)}件取得 ({list(cookies_dict.keys())})")

        await browser.close()

    return cookies_dict
