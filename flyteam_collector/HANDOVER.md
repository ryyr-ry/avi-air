# FlyTeam Collector — 引継ぎ文書（WSL2移行用）

> この文書は、Windows環境からWSL2(Ubuntu)へプロジェクトを移行する際に、  
> AIアシスタントがチャット履歴なしに開発を継続できるよう記述したものです。

---

## 1. プロジェクト概要

**FlyTeam Collector** は、航空情報サイト [FlyTeam](https://flyteam.jp) から
日本国内の全航空会社の機体登録データを**自動収集（クローリング＋スクレイピング）**し、
SQLiteデータベースに蓄積するPythonスクリプトです。

### 最終目的

- 日本の全航空会社の全機体登録番号・運用履歴・別レジ番号の網羅的な収集

### WSL2上のパス

```
/projects/flydata/
```

---

## 2. 技術スタック

| 要素      | 技術                                        |
| --------- | ------------------------------------------- |
| 言語      | Python 3 (venv)                             |
| 非同期I/O | `asyncio` + `uvloop`(Linux専用高速化)       |
| HTTP      | `aiohttp` (200並列、指数バックオフリトライ) |
| HTML解析  | `BeautifulSoup4` + `lxml`                   |
| DB        | `SQLite` (WALモード) via `aiosqlite`        |

### 依存パッケージ（`requirements.txt`から再構築できる）

```
aiohttp, aiosqlite, beautifulsoup4, lxml, uvloop
```

---

## 3. ファイル構成と各ファイルの責務

```
/projects/flydata/
├── main.py           # クローラー本体（エントリポイント）
├── scraper.py        # HTMLパースのみ（HTTP通信/DB操作は一切しない）
├── repository.py     # DB CRUD操作の一元管理（UPSERT/アーカイブ）
├── database.py       # DB初期化・テーブル定義・PRAGMA設定
├── models.py         # データモデル（バリデーション付きdataclass）
├── test_scraper.py   # 統合テスト（実HTMLファイルに対するパース検証）
├── __init__.py       # パッケージ化用（空ファイル）
├── requirements.txt  # pip依存
├── .gitignore        # venv/, *.db, __pycache__/ 除外
└── flyteam_aircrafts.db  # 実行時に自動生成
```

### 責務の分離（疎結合・高凝集の原則）

```
main.py (オーケストレーション)
  ├── aiohttp で HTML取得
  ├── scraper.py で HTML解析（純粋関数）
  ├── asyncio.Queue にDB操作を投入
  └── db_writer_worker が直列にDB書き込み（SQLite単一ライター制約の解決策）
        └── repository.py のCRUDメソッドを呼び出し
              └── database.py で定義されたテーブルに保存
```

---

## 4. データベース設計

### 設計原則

- **論理削除フラグ（is_deleted等）は一切使用しない** ← ユーザーの絶対ルール
- 削除時は対象レコードを**アーカイブテーブルへ物理移動**してから元テーブルからDELETE

### テーブル一覧

#### `aircrafts`（機体マスタ）

| カラム                  | 型        | 備考                 |
| ----------------------- | --------- | -------------------- |
| registration_number     | TEXT PK   | 機体記号（JA31MC等） |
| serial_number           | TEXT      | 製造番号             |
| hex_code                | TEXT      | ICAOヘックスコード   |
| created_at / updated_at | TIMESTAMP |                      |

#### `aircraft_histories`（運用履歴）

| カラム              | 型                | 備考                                             |
| ------------------- | ----------------- | ------------------------------------------------ |
| id                  | INTEGER PK AUTO   |                                                  |
| registration_number | TEXT FK→aircrafts | ON DELETE CASCADE                                |
| airline_slug        | TEXT              | URLスラッグ（"starflyer"等、IATAコードではない） |
| airline_name        | TEXT              | 表示名（"スターフライヤー"等）                   |
| model               | TEXT              | 機種（"A320neo"等）                              |
| operating_status    | TEXT              | "運用中" / "抹消" / "改番" 等                    |
| term_start          | TEXT NOT NULL     | "2025/12"                                        |
| term_end            | TEXT              | NULLなら現在も運用中                             |
| **UNIQUE**          |                   | (registration_number, airline_slug, term_start)  |

#### `aircraft_aliases`（別レジ番の紐付け）

| カラム             | 型                | 備考                                    |
| ------------------ | ----------------- | --------------------------------------- |
| id                 | INTEGER PK AUTO   |                                         |
| base_registration  | TEXT FK→aircrafts | 元のレジ番                              |
| alias_registration | TEXT              | 関連する別レジ番                        |
| **UNIQUE**         |                   | (base_registration, alias_registration) |

#### アーカイブテーブル群

- `aircrafts_archive`, `aircraft_histories_archive`, `aircraft_aliases_archive`
- 全て `archive_id INTEGER PK AUTOINCREMENT` をサロゲートPKに持つ（同一レコードの複数回アーカイブ対応）
- `archived_at TIMESTAMP` を追加で保持

### PRAGMA設定

- `journal_mode=WAL`（読み取り並行性確保）
- `synchronous=NORMAL`（書き込みI/O最適化）
- `foreign_keys=ON`（FK制約有効化）

---

## 5. クローリング戦略

### 階層的クロール

```
/area/asia/japan/airline （国別航空会社一覧）
  └── /airline/skymark （各航空会社）
        └── /airline/skymark/aircrafts （機材一覧、ページネーション対応）
              └── /registration/JA73NX （個別機体）
                    └── 別レジ番リンク → 芋づる式巡回
```

### ページネーション対応

- 機材一覧ページに `?pageid=2` 形式のページネーションが存在
- `<div class="next"><a href="...">` を検出して全ページを追従

### 芋づる式クロール（Chain-Crawling）

- 個別機体ページ下部に「別の機体記号で登録があります」テーブルが存在
- そこから抽出した別レジ番URLを巡回キューに追加
- 階層クロールでは到達できない過去・海外レジもカバー

### 重複防止

- `_try_visit()` メソッドでURLの重複巡回を防止（TOCTOU安全設計）
- DBはUPSERT（`ON CONFLICT DO UPDATE`）で重複レコードを自動処理

---

## 6. 並列処理アーキテクチャ

### HTTP並列: `asyncio.Semaphore(200)`

- 200同時接続でHTTPリクエストを発行
- `aiohttp.TCPConnector(limit=200)` でコネクションプールも同数

### DB書き込み: 専用ワーカーパターン

```
クローラータスク（200並列）
  ↓ asyncio.Queue.put()
DB書き込みワーカー（1タスク、直列処理）
  ↓ 100件ごとにバッチcommit
SQLite（WALモード）
```

- **SQLiteは単一ライターモデル**のため、並列から直接書き込むと `database is locked` エラーが発生する
- 全DB操作を1つのワーカーに集約することで根本解決

### リトライ: 指数バックオフ + ジッター

- 最大5回リトライ
- 429 → `Retry-After` ヘッダを尊重
- 5xx → 指数バックオフ（1s, 2s, 4s, 8s, 16s + random）
- 4xx → リトライなし（スキップ）

### グレースフルシャットダウン

- `SIGINT`/`SIGTERM` で `_shutdown_event` をセット
- 進行中のfetchは空文字を返して穏やかに終了
- DBワーカーに `None`（終了シグナル）を送り、最終commitを実行

---

## 7. HTMLパースの要点

### 航空会社一覧 (`parse_airline_links`)

- `.contents-item__header a` セレクタでメインコンテンツ領域のリンクのみ取得
- フッター・サイドバーのリンクは自然に除外される

### 機材一覧 (`parse_aircraft_list`)

- `.regnumber-table__regnumber a` → 標準テーブル
- `div.regnumber-table-sm__info-regnumber a` → 年別一覧レイアウト
- `div.next a` → ページネーション

### 個別機体 (`parse_aircraft_detail`)

- `.regnumber-table tbody tr` の各行を処理
- 行内に `.regnumber-table__regnumber a` があれば**別レジ番の行** → AircraftAlias
- なければ**自レジ番の行** → AircraftHistory
- `.regnumber-table__condition` のステータス抽出は `stripped_strings` でフィルタ（`decompose()` は使用禁止 — soupの破壊的変更を避けるため）

---

## 8. テスト用HTMLファイル

Windows側 `C:\Users\famil\Desktop\Atchecks\` に以下のHTMLファイルが存在する。
テスト実行時はWSL2へコピーが必要:

```bash
cp /mnt/c/Users/famil/Desktop/Atchecks/tmp_*.html /projects/flydata/
```

| ファイル                     | 内容                                         |
| ---------------------------- | -------------------------------------------- |
| `tmp_area.html`              | 地域別一覧ページ                             |
| `tmp_japan_airlines.html`    | 日本の航空会社一覧                           |
| `tmp_skymark_aircrafts.html` | スカイマーク機材一覧（ページネーションあり） |
| `tmp_flyteam_2025.html`      | 2025年新規登録一覧                           |
| `tmp_ja31mc.html`            | JA31MC詳細（通常の1履歴）                    |
| `tmp_ja482a.html`            | JA482A詳細（別レジ番あり: N477SG）           |
| `tmp_ja23jj.html`            | JA23JJ詳細（複数履歴+複数別レジ番）          |

---

## 9. 実行方法

```bash
cd /projects/flydata
source venv/bin/activate

# テスト実行
python test_scraper.py

# 本番クロール（日本の全航空会社）
python main.py

# 特定航空会社のみテスト
# → main.py 末尾のSTART_URLを変更:
#   START_URL = "/airline/skymark/aircrafts"
```

---

## 10. 残課題・今後の拡張

- [ ] `uvloop` の `main.py` への組み込み（`uvloop.install()` を先頭に追加）
- [ ] `selectolax` への置き換え検討（lxmlの2〜3倍速いHTMLパーサー）
- [ ] `TCPConnector` のDNSキャッシュ最適化（`ttl_dns_cache=300`）
- [ ] ER図/アーキテクチャ図の Mermaid による作成
- [ ] 世界全域への拡張（`/area` のエリア一覧ページから全国別航空会社を巡回する起点を追加）
- [ ] 差分更新モード（前回クロール以降の変更のみ取得する仕組み）
- [ ] CLI引数対応（`argparse` で開始URL・並列数・DB名を指定可能に）

---

## 11. ユーザーの絶対ルール（必ず遵守すること）

1. **すべてのアウトプットは日本語で記述する**
2. **疎結合・高凝集・責務が明確で単一なコードを常に書く。妥協は不可。**
3. **論理削除フラグは絶対に使用せず、別テーブルへの物理移動を徹底する。**
