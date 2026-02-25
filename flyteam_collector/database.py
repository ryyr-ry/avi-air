"""
FlyTeam Collector - データベース初期化・テーブル定義

責務:
  - PostgreSQLテーブルの生成
  - アーカイブテーブル（論理削除の完全廃止に伴う物理移動先）
"""
import asyncpg


async def init_db(pool: asyncpg.Pool):
    """データベースのテーブルを初期化する。"""
    async with pool.acquire() as conn:
        async with conn.transaction():
            # ══════════════════════════════════════
            # 1. 機体マスタテーブル
            # ══════════════════════════════════════
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS aircrafts (
                    registration_number TEXT PRIMARY KEY,
                    serial_number       TEXT,
                    hex_code            TEXT,
                    created_at          TIMESTAMPTZ DEFAULT NOW(),
                    updated_at          TIMESTAMPTZ DEFAULT NOW()
                )
            ''')

            # ══════════════════════════════════════
            # 2. 運用履歴テーブル
            #    複合ユニークキーで同一レジ・同一航空会社・同一開始時期の重複を防ぐ
            # ══════════════════════════════════════
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS aircraft_histories (
                    id                  SERIAL PRIMARY KEY,
                    registration_number TEXT    NOT NULL
                        REFERENCES aircrafts(registration_number) ON DELETE CASCADE,
                    airline_slug        TEXT    NOT NULL DEFAULT '',
                    airline_name        TEXT,
                    model               TEXT,
                    operating_status    TEXT,
                    term_start          TEXT    NOT NULL,
                    term_end            TEXT,
                    created_at          TIMESTAMPTZ DEFAULT NOW(),
                    updated_at          TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(registration_number, airline_slug, term_start)
                )
            ''')

            # ══════════════════════════════════════
            # 3. 別機体記号の紐付けテーブル
            #    レジ番を辞書順で正規化し、方向なしで格納する
            # ══════════════════════════════════════
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS aircraft_aliases (
                    id                  SERIAL PRIMARY KEY,
                    base_registration   TEXT NOT NULL
                        REFERENCES aircrafts(registration_number) ON DELETE CASCADE,
                    alias_registration  TEXT NOT NULL,
                    created_at          TIMESTAMPTZ DEFAULT NOW(),
                    updated_at          TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(base_registration, alias_registration)
                )
            ''')

            # ══════════════════════════════════════
            # 4. インデックス（検索高速化）
            # ══════════════════════════════════════
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_histories_reg
                    ON aircraft_histories(registration_number)
            ''')
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_aliases_base
                    ON aircraft_aliases(base_registration)
            ''')
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_aliases_alias
                    ON aircraft_aliases(alias_registration)
            ''')

            # ══════════════════════════════════════
            # 5. アーカイブテーブル群
            #    論理削除フラグを一切使わず、物理移動先として機能する
            #    archive_id をサロゲートPKにすることで
            #    同一レコードの複数回アーカイブにも対応
            # ══════════════════════════════════════
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS aircrafts_archive (
                    archive_id          SERIAL PRIMARY KEY,
                    registration_number TEXT    NOT NULL,
                    serial_number       TEXT,
                    hex_code            TEXT,
                    created_at          TIMESTAMPTZ,
                    updated_at          TIMESTAMPTZ,
                    archived_at         TIMESTAMPTZ DEFAULT NOW()
                )
            ''')

            await conn.execute('''
                CREATE TABLE IF NOT EXISTS aircraft_histories_archive (
                    archive_id          SERIAL PRIMARY KEY,
                    original_id         INTEGER NOT NULL,
                    registration_number TEXT,
                    airline_slug        TEXT,
                    airline_name        TEXT,
                    model               TEXT,
                    operating_status    TEXT,
                    term_start          TEXT,
                    term_end            TEXT,
                    created_at          TIMESTAMPTZ,
                    updated_at          TIMESTAMPTZ,
                    archived_at         TIMESTAMPTZ DEFAULT NOW()
                )
            ''')

            await conn.execute('''
                CREATE TABLE IF NOT EXISTS aircraft_aliases_archive (
                    archive_id          SERIAL PRIMARY KEY,
                    original_id         INTEGER NOT NULL,
                    base_registration   TEXT,
                    alias_registration  TEXT,
                    created_at          TIMESTAMPTZ,
                    updated_at          TIMESTAMPTZ,
                    archived_at         TIMESTAMPTZ DEFAULT NOW()
                )
            ''')
