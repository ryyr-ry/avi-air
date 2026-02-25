"""
FlyTeam Collector - データベース接続設定

責務:
  - DB接続情報の一元管理
  - 環境変数からの設定読み込み
  - コネクションプールの生成・管理
"""
import os
import asyncpg


# ─────────────────────────────────
# 接続情報（環境変数 or デフォルト値）
# ─────────────────────────────────
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://flyteam:flyteam@localhost:5432/flyteam",
)

# コネクションプール設定
POOL_MIN_SIZE = int(os.environ.get("DB_POOL_MIN", "10"))
POOL_MAX_SIZE = int(os.environ.get("DB_POOL_MAX", "20"))


async def create_pool() -> asyncpg.Pool:
    """
    コネクションプールを生成して返す。

    プールは内部で複数の接続を保持し、
    acquire() で1本借りて使い終わったら自動的に返却される。
    複数のワーカーが同時にDBへ書き込む場合に必要となる仕組み。
    """
    return await asyncpg.create_pool(
        DATABASE_URL,
        min_size=POOL_MIN_SIZE,
        max_size=POOL_MAX_SIZE,
    )
