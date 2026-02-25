"""
FlyTeam Collector - リポジトリ層

責務:
  - データベースに対するCRUD操作の一元管理
  - UPSERT（重複排除付き挿入/更新）
  - アーカイブ処理（論理削除フラグの完全排除、物理移動）

設計方針:
  - asyncpg コネクションプールから接続を取得して操作する
  - アーカイブ処理はトランザクションで原子性を保証する
"""
import asyncpg


class AircraftRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    # ─────────────────────────────────
    # UPSERT: 機体マスタ
    # ─────────────────────────────────
    async def save_aircraft(self, conn: asyncpg.Connection, aircraft):
        """
        機体マスタ情報をUPSERTする。
        既存レコードがある場合は、非NULLの製造番号・HEXコードのみ上書きする。
        """
        await conn.execute(
            '''
            INSERT INTO aircrafts (registration_number, serial_number, hex_code)
            VALUES ($1, $2, $3)
            ON CONFLICT(registration_number) DO UPDATE SET
                serial_number = COALESCE(EXCLUDED.serial_number, aircrafts.serial_number),
                hex_code      = COALESCE(EXCLUDED.hex_code,      aircrafts.hex_code),
                updated_at    = NOW()
            ''',
            aircraft.registration_number, aircraft.serial_number, aircraft.hex_code,
        )

    # ─────────────────────────────────
    # UPSERT: 運用履歴
    # ─────────────────────────────────
    async def save_aircraft_history(self, conn: asyncpg.Connection, history):
        """
        運用履歴をUPSERTする。
        同一の(registration_number, airline_slug, term_start)が存在すれば更新。
        """
        await conn.execute(
            '''
            INSERT INTO aircraft_histories (
                registration_number, airline_slug, airline_name,
                model, operating_status, term_start, term_end
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT(registration_number, airline_slug, term_start) DO UPDATE SET
                airline_name     = EXCLUDED.airline_name,
                model            = EXCLUDED.model,
                operating_status = EXCLUDED.operating_status,
                term_end         = EXCLUDED.term_end,
                updated_at       = NOW()
            ''',
            history.registration_number,
            history.airline_slug,
            history.airline_name,
            history.model,
            history.operating_status,
            history.term_start,
            history.term_end,
        )

    # ─────────────────────────────────
    # UPSERT: 別機体記号
    # ─────────────────────────────────
    async def save_aircraft_alias(self, conn: asyncpg.Connection, alias):
        """別機体記号の関連付け情報をINSERT（既存なら無視）する。"""
        await conn.execute(
            '''
            INSERT INTO aircraft_aliases (base_registration, alias_registration)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            ''',
            alias.base_registration, alias.alias_registration,
        )

    # ─────────────────────────────────
    # 一括保存（1機体分のデータをトランザクションで保存）
    # ─────────────────────────────────
    async def save_aircraft_data(self, aircraft, histories, aliases):
        """
        1機体の全データ（マスタ + 履歴 + エイリアス）を
        単一トランザクションで保存する。

        航空機マスタが先にINSERTされないと履歴やエイリアスの
        FK制約が満たせないため、順序を保証している。
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await self.save_aircraft(conn, aircraft)
                for h in histories:
                    await self.save_aircraft_history(conn, h)
                for a in aliases:
                    await self.save_aircraft_alias(conn, a)

    # ═════════════════════════════════
    # アーカイブ処理
    #   論理削除フラグ（is_deleted等）は一切使用しない。
    #   対象レコードをアーカイブテーブルへINSERT SELECTし、元テーブルからDELETEする。
    #   全操作をトランザクションで囲み、原子性を保証する。
    # ═════════════════════════════════

    async def archive_aircraft(self, registration_number: str):
        """
        機体マスタをアーカイブテーブルへ移動する。
        FK ON DELETE CASCADEにより、関連する履歴とエイリアスも連鎖削除される。
        連鎖削除される前に履歴・エイリアスも個別にアーカイブすべき場合は、
        この関数の呼び出し前に archive_histories_by_reg / archive_aliases_by_reg を呼ぶこと。
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    '''
                    INSERT INTO aircrafts_archive (
                        registration_number, serial_number, hex_code,
                        created_at, updated_at
                    )
                    SELECT registration_number, serial_number, hex_code,
                           created_at, updated_at
                    FROM aircrafts
                    WHERE registration_number = $1
                    ''',
                    registration_number,
                )
                await conn.execute(
                    'DELETE FROM aircrafts WHERE registration_number = $1',
                    registration_number,
                )

    async def archive_history(self, history_id: int):
        """運用履歴を1件アーカイブテーブルへ移動する。"""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    '''
                    INSERT INTO aircraft_histories_archive (
                        original_id, registration_number, airline_slug, airline_name,
                        model, operating_status, term_start, term_end,
                        created_at, updated_at
                    )
                    SELECT id, registration_number, airline_slug, airline_name,
                           model, operating_status, term_start, term_end,
                           created_at, updated_at
                    FROM aircraft_histories
                    WHERE id = $1
                    ''',
                    history_id,
                )
                await conn.execute(
                    'DELETE FROM aircraft_histories WHERE id = $1',
                    history_id,
                )

    async def archive_alias(self, alias_id: int):
        """エイリアスを1件アーカイブテーブルへ移動する。"""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    '''
                    INSERT INTO aircraft_aliases_archive (
                        original_id, base_registration, alias_registration,
                        created_at, updated_at
                    )
                    SELECT id, base_registration, alias_registration,
                           created_at, updated_at
                    FROM aircraft_aliases
                    WHERE id = $1
                    ''',
                    alias_id,
                )
                await conn.execute(
                    'DELETE FROM aircraft_aliases WHERE id = $1',
                    alias_id,
                )
