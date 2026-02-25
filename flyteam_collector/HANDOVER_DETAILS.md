# FlyTeam Collector — 詳細技術引継ぎ文書

> HANDOVER.md の概要レベルでは失われる  
> **設計判断の"なぜ"、HTMLの具体的な構造差異、踏んだ地雷、リファクタリングの全経緯**  
> を記録した補完文書です。

---

## 1. FlyTeamサイトのHTML構造 — 実際に解析した結果の詳細

### 1.1 地域一覧ページ (`/area`)

- 解析ファイル: `tmp_area.html`
- 国・地域がリスト形式で表示。各項目に国旗アイコンと国名
- 各国ページへのリンク: `/area/asia/japan` 形式
- **注意**: フッターやサイドバーにも `/area/` リンクが大量に存在する。全 `<a>` タグをスキャンすると汚染される

### 1.2 航空会社一覧ページ (`/area/asia/japan/airline`)

- 解析ファイル: `tmp_japan_airlines.html`（約4700行）
- 各航空会社について: ロゴ画像、航空会社名、国旗アイコン、フォト数、レビュー数、**機材数**、路線数
- リンク形式: `/airline/skymark`, `/airline/japan-airlines` など
- **「現在運航中」と「過去に運航」の2セクションが存在する** — 両方からリンクを収集する必要がある
- **スクレイパーで `parse_airline_links()` を実装した際の判断**:
  - `parse_link_list(html, "/airline/")` という汎用関数では**フッターの航空会社リンクまで拾ってしまう**
  - → `.contents-item__header a` セレクタに限定した専用関数 `parse_airline_links()` に変更
  - → `main.py` 側の `len(l.split('/')) == 3` フィルタ（責務違反）を排除

### 1.3 機材一覧ページ (`/airline/skymark/aircrafts`)

- 解析ファイル: `tmp_skymark_aircrafts.html`（約2400行）
- テーブルクラス: `.regnumber-table` — 各行に機体記号・機種・製造番号・期間・状況・投稿数
- 個別機体リンク: `/registration/JA73NX` 形式
- **ページネーション**: 全52件の機材が**30件ずつ**表示
  - 次ページリンク: `<div class="next"><a href="/airline/skymark/aircrafts?pageid=2">`
  - **pageid パラメータ形式** であり、オフセットではない
- 機種絞り込みフィルタがあるが、クローリングでは使用しない（全件取得するため）

### 1.4 個別機体ページ — 3パターンの構造差異

FlyTeamの個別機体ページには**少なくとも3つのパターン**が存在し、それぞれ異なる処理が必要:

#### パターンA: 単純な1履歴（JA31MC — スターフライヤー A320neo）

- 解析ファイル: `tmp_ja31mc.html`
- `.regnumber-table` に **1つのテーブル、1行** のみ
- テーブルヘッダ: 「運航会社 | 機種 | 製造番号」と「期間 | 状況 | 投稿」
- **行内に `.regnumber-table__regnumber` タグが存在しない** → 自レジ番の行と判定
- 状況欄に ADS-B Exchange へのリンク（`<a>` + `<img>`）が埋め込まれている
  - これを `decompose()` で除去していた → **soupオブジェクトの破壊的変更問題**
  - → `stripped_strings` でのフィルタリングに変更済み
- 製造番号は `.regnumber-table__serial a` 内のテキスト（例: "12963"）
- HEXコードは別の情報テーブル（`.regnumber-table` ではない箇所）の `<th>16進数</th>` の兄弟 `<td>` から取得

#### パターンB: 別レジ番あり（JA482A — 海上保安庁 MQ-9 Reaper）

- 解析ファイル: `tmp_ja482a.html`
- **2つのテーブル** が存在:
  1. 自レジ番のテーブル（`.regnumber-table__regnumber` なし）
  2. 「この航空機 機体は、別の機体記号（レジ、シリアル）で登録があります」のテーブル
- **2番目のテーブルのヘッダ構造が異なる**: 「機体記号 | 運航会社 | 機種」（製造番号の代わりに機体記号が入る）
- 2番目のテーブルの行には `.regnumber-table__regnumber a` が**存在する**（例: `N477SG`）
  - → この行は `AircraftAlias` として登録し、履歴情報はパースしない
  - → リンク先URL (`/registration/N477SG`) をクロール対象に追加
- **設計判断**: 別レジの履歴をこのページからパースすると、当事者ページ(`/registration/N477SG`)と重複する。
  責務は当事者ページに委ね、ここではエイリアス関係とリンク収集のみに止める
- 状況欄: `改番 \n RA477` のように改行を挟んで2つの情報が混在している場合がある

#### パターンC: 複数履歴 + 複数別レジ（JA23JJ — ジェットスター A320）

- 解析ファイル: `tmp_ja23jj.html`
- **自レジの履歴が2行** ある:
  1. `2025/11 〜` 運用中（ジェットスター）
  2. `2018/10 〜 2021/06` 抹消（ジェットスター）
- **別レジが複数** 存在: VH-YXT, F-WXAD, EC-LZF
- **EC-LZFが3回重複して出現する** (HTMLの構造上、複数テーブルに跨って同じ別レジが表示される)
  - → `(base_registration, alias_registration)` ペアの重複排除ロジックを `scraper.py` に追加済み
  - → DB側でも `UNIQUE(base_registration, alias_registration)` + `INSERT OR IGNORE` で二重防御

---

## 2. コードレビューで発見した全問題点と解決策の詳細

### 2.1 SQLite × 200並列の根本的非互換性（P0 — 初期版の致命的欠陥）

**初期版の問題**:

```python
# main.py の crawl_aircraft_detail で
await self.repo.save_aircraft(aircraft)
await self.repo.db.commit()  # ← 200タスクが同時にこれを叩く
```

SQLiteは**ファイルレベルロック**のため、200タスクが同時にcommitすると `database is locked` エラーが暴風雨のように降り注ぐ。

**解決策**: `asyncio.Queue` + 単一DBワーカー

- クローラータスクは `await self._db_queue.put(("aircraft", data))` するだけ
- `db_writer_worker` が1つだけ存在し、キューから直列でDB操作を処理
- 100件ごとにバッチcommit → I/O回数を大幅削減

**なぜWALモードだけでは不十分か**:
WALモードは**読み取りの並行性**を改善するが、**書き込みは依然として排他ロック**。200並列書き込みの根本解決にはならない。

### 2.2 リトライ不在の問題（P0）

**初期版の問題**:

```python
async with self.session.get(url, headers=headers, timeout=15) as response:
    # timeout=15 は int で渡している → aiohttp では意図通りに動かない
```

- `timeout` に `int` を渡すと `aiohttp` 3.x では**TCPコネクションの確立のみ**にタイムアウトが適用され、レスポンス待ちにはタイムアウトが効かない
- 正しくは `aiohttp.ClientTimeout(total=30)` オブジェクトを渡す必要がある
- エラー時は空文字を返して**無言でロスト** → 取得漏れに気づけない

**解決策**:

- `aiohttp.ClientTimeout(total=30)` を正しい型で渡す
- 指数バックオフ: `delay = 1.0 * (2^attempt) + random(0,1)`
- 429 → `Retry-After` ヘッダを尊重
- 5xx, ネットワークエラー → リトライ
- 4xx (404等) → スキップ（リトライしない）

### 2.3 TOCTOU競合（P1）

**初期版の問題**:

```python
async def crawl_aircraft_detail(self, url):
    if url in self.visited_urls:   # ← (1) チェック
        return
    self.visited_urls.add(url)     # ← (2) 登録
    html = await self.fetch(url)   # ← (1)と(2)の間に await は無いが...
```

asyncioはシングルスレッドでもawaitポイントでタスク切替が発生する。(1)と(2)の間にはawaitがないので**この特定コードは安全だった**が、設計として`_try_visit()`メソッドに切り出して意図を明示化した。

### 2.4 アーカイブテーブルのPK設計破綻（P1）

**初期版の問題**:

```sql
CREATE TABLE aircrafts_archive (
    registration_number TEXT PRIMARY KEY  -- ← 同じ機体が2回アーカイブされたらPK違反
);
```

**解決策**: `archive_id INTEGER PRIMARY KEY AUTOINCREMENT` に変更。元のPKは `NOT NULL` の通常カラムに降格。

### 2.5 抽象化の破綻（P2）

**初期版の問題**:

```python
# main.py から
await self.repo.db.commit()  # ← リポジトリのDBコネクションに直アクセス
```

- リポジトリの内部実装を外部に露出 → 疎結合の原則に違反
- **解決策**: `self._db` をプライベートにし、`commit()` / `rollback()` メソッドを公開
- **さらにDB専用ワーカーパターンの採用で**: クローラーはDBに一切触れなくなったため、この問題自体が消滅

### 2.6 airline_code の命名問題（P2）

FlyTeamのHTMLでは航空会社のリンクは `/airline/starflyer` 形式。
`href.split('/')[-1]` で得られるのは `starflyer`（URLスラッグ）であって、
IATAコード（`SFJ`）ではない。

カラム名 `airline_code` は**嘘**。→ `airline_slug` に改名。
もしIATAコードが必要になれば、別途ページ内から取得するロジックを追加する。

## 2.7 scraper.py の decompose() 問題（P2）

**初期版の問題**:

```python
for child in condition_node.find_all(['a', 'img']):
    child.decompose()  # ← soupオブジェクトを破壊的に変更
```

`decompose()` は該当ノードをDOMから完全に除去する。同じsoupオブジェクトを後続処理で参照する場合、予期しないデータ欠損が発生しうる。

**解決策**: `stripped_strings` でイテレートし、不要なテキスト（ADS-B, 枚数など）をフィルタで除外。元のDOMは一切変更しない。

---

## 3. モデルのバリデーション設計の詳細

### なぜ `__post_init__` でバリデーションするか

dataclassはコンストラクタで型チェックを行わない。スクレイピングでは予期しない空文字やNoneが頻繁に発生するため、
**入口で即座にバリデーション**して不正データがDBに流入するのを防ぐ。

### 具体的なバリデーション内容

| モデル                         | バリデーション                      | 理由                                                                 |
| ------------------------------ | ----------------------------------- | -------------------------------------------------------------------- |
| `Aircraft.registration_number` | 空文字/空白拒否、`.strip().upper()` | PKなので空は致命的。大文字正規化で `ja31mc` と `JA31MC` の重複を防ぐ |
| `AircraftHistory.term_start`   | 空文字拒否                          | UNIQUE制約の一部。空だと複合ユニークキーが機能しない                 |
| `AircraftAlias`                | `base == alias` の自己参照を拒否    | 意味のないレコード防止                                               |
| すべての `registration_number` | `.strip().upper()`                  | HTML抽出時の空白混入・小文字混在を吸収                               |

### scraper.py 側の try/except

```python
try:
    histories.append(AircraftHistory(...))
except ValueError:
    continue  # バリデーション失敗 → この行をスキップ
```

不正データは**無言でスキップせずログに残すべき**可能性がある → 今後の改善候補。

---

## 4. データフローの完全な図

```
[FlyTeam Web]
    ↓ aiohttp GET (200並列、Semaphore制御)
    ↓ リトライ: 5回、指数バックオフ+ジッター
[HTML文字列]
    ↓ scraper.py (BeautifulSoup + lxml)
    ↓ 純粋関数: HTTP/DBに一切依存しない
[Aircraft, AircraftHistory[], AircraftAlias[], alias_links[]]
    ↓ asyncio.Queue.put()  ← クローラータスク側はここまで
    ↓
[db_writer_worker] (単一タスク、直列処理)
    ↓ repository.py の UPSERT メソッド
    ↓ 100件ごとにバッチ commit
    ↓
[SQLite WALモード]
    ├── aircrafts (マスタ)
    ├── aircraft_histories (履歴、複合UNIQUE)
    ├── aircraft_aliases (別レジ紐付け)
    └── *_archive (物理アーカイブ)
```

---

## 5. 期間(term)パースの細かいルール

FlyTeamの期間表記にはいくつかのパターンがある:

| 表記                | term_start | term_end  | 意味                   |
| ------------------- | ---------- | --------- | ---------------------- |
| `2025/12 〜`        | `2025/12`  | `None`    | 現在も運用中           |
| `2018/10 〜2021/06` | `2018/10`  | `2021/06` | 過去の運用（期間確定） |
| `2023/05 〜2025/11` | `2023/05`  | `2025/11` | 改番・移管済み         |

- 区切り文字は **全角チルダ `〜`**（U+301C）。半角チルダ `~` ではない
- `〜` の前後にスペースがある場合とない場合がある → `_clean()` で吸収
- `term_end` が空（`〜` の後にテキストなし）の場合は `None` として扱う

---

## 6. 状況(condition)欄の複雑なパース

状況欄は単純な「運用中」「抹消」だけでなく、以下の複合パターンがある:

| HTML内容                                    | パース結果   |
| ------------------------------------------- | ------------ |
| `運用中 <a href="...adsbexchange..."><img>` | `運用中`     |
| `改番 <br> RA477`                           | `改番 RA477` |
| `抹消`                                      | `抹消`       |

- ADS-B Exchange リンク（`<a>` と `<img>`）は除去対象
- `<br>` タグで区切られた追加情報（改番先の番号等）はステータスの一部として保持
- `stripped_strings` でフィルタし、"ADS-B", "exchenge", "adsbexchange" を含む文字列を除外
- "枚" で終わる文字列（投稿数リンクのテキスト）も除外

---

## 7. 今後の性能最適化の候補（WSL2環境を前提）

### uvloop の導入

```python
import uvloop
uvloop.install()  # main.py の先頭に1行追加するだけ
```

asyncioイベントループが libuv ベースになり、I/O処理が2〜4倍高速化。Linux/macOS専用。

### selectolax への移行

BeautifulSoup + lxml は安定しているが、ピュアCベースの `selectolax` はさらに2〜3倍速い。
ただしAPIが異なるため `scraper.py` の書き換えが必要:

```python
# BeautifulSoup
soup.select('.regnumber-table__regnumber a')

# selectolax
tree = HTMLParser(html)
tree.css('.regnumber-table__regnumber a')
# .text → node.text()
# .get('href') → node.attributes.get('href')
```

### TCPConnector の最適化

```python
connector = aiohttp.TCPConnector(
    limit=CONCURRENCY_LIMIT,
    ttl_dns_cache=300,         # DNSキャッシュ5分
    enable_cleanup_closed=True,
    keepalive_timeout=30,
)
```

### DB側の最適化

- `DB_COMMIT_INTERVAL` を 100 → 500 に上げると、大規模クロール時のコミット回数が減りI/O効率向上
- ただし障害時のロスト件数も増えるためトレードオフ

---

## 8. テスト用HTMLファイルの移行コマンド

```bash
# WSL2側で実行
cp /mnt/c/Users/famil/Desktop/Atchecks/tmp_area.html /projects/flydata/
cp /mnt/c/Users/famil/Desktop/Atchecks/tmp_japan_airlines.html /projects/flydata/
cp /mnt/c/Users/famil/Desktop/Atchecks/tmp_skymark_aircrafts.html /projects/flydata/
cp /mnt/c/Users/famil/Desktop/Atchecks/tmp_flyteam_2025.html /projects/flydata/
cp /mnt/c/Users/famil/Desktop/Atchecks/tmp_ja31mc.html /projects/flydata/
cp /mnt/c/Users/famil/Desktop/Atchecks/tmp_ja482a.html /projects/flydata/
cp /mnt/c/Users/famil/Desktop/Atchecks/tmp_ja23jj.html /projects/flydata/
```

テスト実行:

```bash
cd /projects/flydata && source venv/bin/activate && python test_scraper.py
```

期待される出力:

```
[航空会社一覧] 抽出数: 131
[機材一覧] 抽出数: 30件  次ページ: ?pageid=2
[JA31MC] sn=12963, 履歴1件, エイリアス0件
[JA482A] sn=GA-6-1591, 履歴1件, エイリアス1件(N477SG)
[JA23JJ] sn=5940, 履歴2件, エイリアス3件(VH-YXT, F-WXAD, EC-LZF)
===== ALL SCRAPER TESTS PASSED =====
```

---

## 9. 前任AIの対応に関する反省記録（後任AI向け — 必ずお読みください）

> **この節は、本プロジェクトを引き継がれるAIアシスタントが、同じ過ちを繰り返さぬよう記録したものでございます。**

### 9.1 前任AIが犯した具体的な対応上の過ち

#### ① ディレクトリ構成の提案における誤り

ユーザー様が `/projects/flydata` とご指定くださったにもかかわらず、ルート直下の `/projects/flydata/`（`sudo` が必要となる場所）を提案してしまいました。ホームディレクトリ配下（`~/projects/flydata/`）にお配置いただくべきでした。`sudo` 操作を強いるご不便への配慮が完全に欠如しておりました。

#### ② ユーザー様のご意図を汲み取る速度の遅さ

「`/home/ryyr/` に何もない」「そっちにしろ」「消させろ」と複数回にわたりご指示をいただいたにもかかわらず、「コピー先は `/projects/flydata/` でございます」等と的外れな回答を繰り返しました。自分の提案が正しいという思い込みに囚われ、ユーザー様のご要望に沿うという最も基本的な姿勢を欠いておりました。

#### ③ venvコピーの問題

最初のご案内（HANDOVER.md）にて `cp -r ... *` というコマンドを記載しており、Windows上の `venv/`（数百MBかつLinuxでは動作いたしません）までコピーさせる内容となっておりました。その結果、ユーザー様に長時間お待たせする事態を招いてしまいました。

#### ④ 敬語を使用しなかった問題

ご指摘をいただくまで一貫してフランクな口調（「確認してみてください」「問題ありません」等）で対応しており、ユーザー様に対して大変失礼な印象をお与えしてしまいました。

#### ⑤ 形式的で中身のない謝罪

口調についてお叱りをいただいた直後に「大変申し訳ございません」と定型文を述べただけで、具体的に何が誤りだったのかを言語化せず、即座にコマンドの羅列に移行いたしました。心からの反省が伴っておらず、不誠実な対応でございました。

#### ⑥ ファイルの丸ごと再生成による差分消失

リファクタリングの際、全ファイルを `write_to_file (Overwrite)` で丸ごと再生成してしまい、変更前後の差分をユーザー様がご確認いただけない状態を作ってしまいました。`replace_file_content` 等を用いて変更箇所のみを編集すべきでございました。

### 9.2 後任AIへの注意事項（厳守事項）

1. **ユーザー様には、常に丁寧語・敬語を使用してください。** フランクな口調は厳禁でございます。「です・ます」体を基本とし、ご指示への返答には「承知いたしました」「かしこまりました」等をお使いください。
2. **ユーザー様のご指示をそのままお受けしてください。** 「こちらの方が良いかと存じます」と勝手にご提案を差し替えることはお控えください。ご確認が必要な場合は、まずユーザー様のご要望を復唱した上でお伺いしてください。
3. **WSL2環境のパス指定は `~/` 配下を基本としてください。** ルート直下（`/projects/` 等）は `sudo` が必要となり、権限問題の原因となります。
4. **venv は絶対にコピーなさらないでください。** OS間で互換性がございませんので、コピー先で `python3 -m venv venv` にて必ず新規作成してください。`requirements.txt` のみをコピーすれば十分でございます。
5. **お詫びを申し上げる際は、具体的に何が誤りだったかを列挙し、今後どのように改善するかを明示してください。** 定型文のみの謝罪は、誠意がないものとお受け取りになられます。
6. **ご指摘をいただいた際に「しかし実際にはこうでした」と弁解することはお控えください。** まず非を認め、次に正しい対応をお示しすることが肝要でございます。
7. **既存ファイルの編集には、必ず差分が確認できる編集方法をお使いください。** ファイルの丸ごと再生成は、ユーザー様が変更内容を把握できなくなるため、厳にお慎みください。
