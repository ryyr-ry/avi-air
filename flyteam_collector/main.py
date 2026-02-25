"""
FlyTeam Collector - メインクローラー

責務:
  - 階層的なWebクローリングのオーケストレーション
  - 非同期HTTP通信（aiohttp）とリトライ制御
  - URLキュー + ワーカープール方式による並列巡回
  - PostgreSQLへの並列書き込み
  - グレースフルシャットダウン

階層構造:
  国別航空会社一覧 → 航空会社 → 機材一覧(ページネーション) → 個別機体
  └── 別レジ番リンクの芋づる式巡回
"""
import asyncio
import random
import signal
import logging
from typing import Set, Any
from enum import Enum, auto

import aiohttp

from db_config import create_pool
from database import init_db
from repository import AircraftRepository
from scraper import parse_country_links, parse_airline_links, parse_aircraft_list, parse_aircraft_detail

# ─────────────────────────────────
# 定数
# ─────────────────────────────────
BASE_URL = "https://flyteam.jp"
CRAWL_WORKERS = 100              # クロールワーカー数
CONCURRENCY_LIMIT = 300          # 同時HTTP接続数
MAX_ATTEMPTS = 5                 # 最大試行回数（初回含む）
RETRY_BASE_DELAY = 0.5           # リトライ基底遅延（秒）
REQUEST_TIMEOUT = 15             # HTTP要求タイムアウト（秒）

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/119.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Accept": "text/html",
}

# ─────────────────────────────────
# ロガー
# ─────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("flyteam_crawler")


# ─────────────────────────────────
# URL種別
# ─────────────────────────────────
class UrlType(Enum):
    AREA = auto()       # エリア一覧（全世界の国リンクを収集）
    COUNTRY = auto()    # 国別航空会社一覧
    AIRLINE = auto()    # 航空会社トップ → /aircrafts に変換
    LIST = auto()       # 機材一覧ページ
    DETAIL = auto()     # 個別機体ページ


# ═══════════════════════════════════════
# クローラー本体
# ═══════════════════════════════════════

class FlyTeamCrawler:
    def __init__(self, region: str | None = None):
        self._visited: Set[str] = set()
        self._session: aiohttp.ClientSession | None = None
        self._url_queue: asyncio.Queue = asyncio.Queue()
        self._shutdown_event = asyncio.Event()
        self._repo: AircraftRepository | None = None
        self._region = region
        self._stats = {
            "fetched": 0,
            "saved": 0,
            "skipped": 0,
            "fetch_errors": 0,
            "db_errors": 0,
        }

    # ─────────────────────────────────
    # visited管理（アトミック）
    # ─────────────────────────────────
    def _try_visit(self, url: str) -> bool:
        """
        visited判定と登録をアトミックに行う。
        awaitを一切挟まないため、asyncioタスク切替が発生しない。
        """
        if url in self._visited:
            return False
        self._visited.add(url)
        return True

    # ─────────────────────────────────
    # HTTP通信（リトライ付き）
    # ─────────────────────────────────
    async def _fetch(self, url: str) -> str:
        """
        指数バックオフ + ジッター付きリトライでHTTPフェッチを行う。
        - 200: HTMLを返却
        - 429: Retry-Afterを尊重して再試行
        - 5xx: リトライ対象
        - 4xx(404等): 空文字を返却（リトライしない）
        """
        if not url.startswith("http"):
            url = BASE_URL + url

        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)

        for attempt in range(MAX_ATTEMPTS):
            if self._shutdown_event.is_set():
                return ""

            try:
                async with self._session.get(
                    url, headers=HEADERS, timeout=timeout
                ) as resp:
                    if resp.status == 200:
                        self._stats["fetched"] += 1
                        return await resp.text()

                    if resp.status == 429:
                        retry_after = int(
                            resp.headers.get("Retry-After",
                                             RETRY_BASE_DELAY * (2 ** attempt))
                        )
                        logger.warning(
                            f"429 レート制限: {url} ({retry_after}秒待機)"
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    if resp.status >= 500:
                        delay = RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                        logger.warning(
                            f"{resp.status} サーバーエラー: {url} "
                            f"(リトライ {attempt+1}/{MAX_ATTEMPTS}, {delay:.1f}秒後)"
                        )
                        await asyncio.sleep(delay)
                        continue

                    # 4xx → リトライ不要
                    logger.warning(f"スキップ {url} (HTTP {resp.status})")
                    self._stats["skipped"] += 1
                    return ""

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                delay = RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                logger.warning(
                    f"通信エラー: {url} ({e.__class__.__name__}) "
                    f"リトライ {attempt+1}/{MAX_ATTEMPTS}, {delay:.1f}秒後"
                )
                await asyncio.sleep(delay)

        logger.error(f"全 {MAX_ATTEMPTS} 回の試行が失敗: {url}")
        self._stats["fetch_errors"] += 1
        return ""

    # ─────────────────────────────────
    # URLをキューに投入するヘルパー
    # ─────────────────────────────────
    def _enqueue(self, url: str, url_type: UrlType):
        """visitチェック + キュー投入をアトミックに行う。"""
        if self._try_visit(url):
            self._url_queue.put_nowait((url, url_type))

    # ─────────────────────────────────
    # 各ページ種別の処理
    # ─────────────────────────────────

    async def _process_area(self, url: str):
        """エリア一覧 → 各国の航空会社一覧URLをキューに投入"""
        logger.info(f"エリア一覧取得: {url}")
        html = await self._fetch(url)
        if not html:
            return

        country_urls = parse_country_links(html, region_filter=self._region)
        region_label = self._region or '全世界'
        logger.info(f"  → {len(country_urls)} カ国を検出 ({region_label})")
        for country_url in country_urls:
            self._enqueue(country_url, UrlType.COUNTRY)

    async def _process_country(self, url: str):
        """国別航空会社一覧 → 各航空会社をキューに投入"""
        logger.info(f"航空会社一覧取得: {url}")
        html = await self._fetch(url)
        if not html:
            return

        airline_links = parse_airline_links(html)
        logger.info(f"  → {len(airline_links)} 社の航空会社を検出")
        for link in airline_links:
            self._enqueue(link, UrlType.AIRLINE)

    async def _process_airline(self, url: str):
        """航空会社トップ → 機材一覧ページをキューに投入"""
        aircrafts_url = url.rstrip('/') + "/aircrafts"
        self._enqueue(aircrafts_url, UrlType.LIST)

    async def _process_list(self, url: str):
        """機材一覧ページ → 個別機体をキューに投入 + 次ページ追従"""
        logger.info(f"機材一覧取得: {url}")
        html = await self._fetch(url)
        if not html:
            return

        detail_links, next_page = parse_aircraft_list(html)
        for link in detail_links:
            self._enqueue(link, UrlType.DETAIL)

        if next_page:
            self._enqueue(next_page, UrlType.LIST)

    async def _process_detail(self, url: str):
        """個別機体ページ → パース → DB保存 → 別レジ芋づる巡回"""
        target_reg = url.rstrip('/').split('/')[-1]
        html = await self._fetch(url)
        if not html:
            return

        try:
            aircraft, histories, aliases, alias_links = parse_aircraft_detail(
                html, target_reg
            )

            # DB保存（トランザクションで一括）
            await self._repo.save_aircraft_data(aircraft, histories, aliases)

            self._stats["saved"] += 1
            if self._stats["saved"] % 100 == 0:
                logger.info(
                    f"進捗: 保存={self._stats['saved']}, "
                    f"取得={self._stats['fetched']}, "
                    f"エラー={self._stats['fetch_errors']}"
                )

            # 芋づる式: 別レジ番の詳細ページをキューに追加
            for link in alias_links:
                self._enqueue(link, UrlType.DETAIL)

        except Exception as e:
            self._stats["db_errors"] += 1
            logger.error(f"処理エラー ({url}): {e}")

    # ─────────────────────────────────
    # ワーカー（キューからURLを取り出して処理する）
    # ─────────────────────────────────

    async def _worker(self, worker_id: int):
        """URLキューからタスクを取り出し、種別に応じた処理を実行する。"""
        handlers = {
            UrlType.AREA: self._process_area,
            UrlType.COUNTRY: self._process_country,
            UrlType.AIRLINE: self._process_airline,
            UrlType.LIST: self._process_list,
            UrlType.DETAIL: self._process_detail,
        }

        while True:
            if self._shutdown_event.is_set():
                break

            try:
                url, url_type = await asyncio.wait_for(
                    self._url_queue.get(), timeout=5.0
                )
            except asyncio.TimeoutError:
                # キューが5秒間空 = 全ての巡回URLが処理済み
                # 他のワーカーがまだURLを追加する可能性があるので、
                # キューが空かつ全ワーカーが待機状態なら終了
                if self._url_queue.empty():
                    break
                continue

            try:
                handler = handlers.get(url_type)
                if handler:
                    await handler(url)
            except Exception as e:
                logger.error(f"ワーカー{worker_id} 処理エラー ({url}): {e}")
            finally:
                self._url_queue.task_done()

    # ─────────────────────────────────
    # エントリポイント
    # ─────────────────────────────────

    async def run(self, start_url: str):
        """クローラーのメインエントリポイント。"""
        # DB初期化
        pool = await create_pool()
        await init_db(pool)
        self._repo = AircraftRepository(pool)

        # グレースフルシャットダウンのシグナルハンドラ登録
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._handle_shutdown)
            except NotImplementedError:
                pass

        # HTTPセッション
        connector = aiohttp.TCPConnector(
            limit=CONCURRENCY_LIMIT,
            ttl_dns_cache=300,
            enable_cleanup_closed=True,
            keepalive_timeout=60,
        )
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with aiohttp.ClientSession(
            connector=connector, timeout=timeout
        ) as session:
            self._session = session
            logger.info(
                f"クローラー開始 | ワーカー数={CRAWL_WORKERS} | "
                f"同時接続={CONCURRENCY_LIMIT} | "
                f"最大試行={MAX_ATTEMPTS} | "
                f"タイムアウト={REQUEST_TIMEOUT}秒"
            )

            # 起点URLの種別を判定してキューに投入
            if start_url.rstrip('/') == '/area' or start_url.rstrip('/').endswith('/area'):
                start_type = UrlType.AREA
            elif '/airline/' in start_url and '/aircrafts' in start_url:
                start_type = UrlType.LIST
            elif '/airline/' in start_url:
                start_type = UrlType.AIRLINE
            else:
                start_type = UrlType.COUNTRY
            self._enqueue(start_url, start_type)

            # ワーカープール起動
            workers = [
                asyncio.create_task(self._worker(i))
                for i in range(CRAWL_WORKERS)
            ]

            # 全ワーカーの完了を待つ
            await asyncio.gather(*workers)

        # コネクションプール解放
        await pool.close()

        logger.info(
            f"クローラー完了 | "
            f"保存={self._stats['saved']} | "
            f"取得={self._stats['fetched']} | "
            f"スキップ={self._stats['skipped']} | "
            f"通信エラー={self._stats['fetch_errors']} | "
            f"DBエラー={self._stats['db_errors']}"
        )

    def _handle_shutdown(self):
        """シグナルハンドラ: グレースフルシャットダウンを要請する。"""
        logger.warning("停止シグナル受信。実行中の処理を完了して終了します...")
        self._shutdown_event.set()


# ─────────────────────────────────
# CLI
# ─────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="FlyTeam 航空機データ収集クローラー")
    parser.add_argument(
        "--start-url",
        default="/area",
        help="クロール開始URL (デフォルト: /area = 全世界)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=CRAWL_WORKERS,
        help=f"クロールワーカー数 (デフォルト: {CRAWL_WORKERS})",
    )
    parser.add_argument(
        "--region",
        default=None,
        help="地域フィルタ (例: asia, europe, north-america)",
    )
    args = parser.parse_args()
    CRAWL_WORKERS = args.workers

    try:
        import uvloop
        uvloop.install()
        logger.info("uvloop 有効化")
    except ImportError:
        pass

    crawler = FlyTeamCrawler(region=args.region)
    asyncio.run(crawler.run(start_url=args.start_url))
