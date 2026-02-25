"""
FlyTeam Collector - HTMLスクレイピング層

責務:
  - 各種HTMLページのパースとデータ抽出のみ
  - HTTP通信やDB操作は一切行わない（疎結合）

対象ページ種別:
  - 航空会社一覧ページ (parse_airline_links)
  - 機材一覧ページ     (parse_aircraft_list)
  - 個別機体ページ     (parse_aircraft_detail)
"""
import re
from typing import List, Tuple, Optional
from bs4 import BeautifulSoup, Tag
from models import Aircraft, AircraftHistory, AircraftAlias


def _clean(text: str) -> str:
    """文字列内の余分な空白・改行を取り除いてクリーンにする。"""
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()


# ═══════════════════════════════════════
# エリア一覧ページ（全世界の国リンクを収集）
# ═══════════════════════════════════════

def parse_country_links(html: str, region_filter: Optional[str] = None) -> List[str]:
    """
    エリア一覧ページ (/area) のHTMLから、
    各国の航空会社一覧ページへのURLパスを抽出する。

    国リンク: /area/asia/japan 形式
    → /area/asia/japan/airline に変換して返す

    region_filter: 指定時はその地域のみ抽出（例: "asia", "europe"）
    """
    soup = BeautifulSoup(html, 'lxml')
    country_urls: List[str] = []
    for a_tag in soup.find_all('a', href=True):
        href = a_tag.get('href', '')
        # /area/地域/国 の形式（スラッシュが3つ以上）
        if href.startswith('/area/') and href.count('/') >= 3:
            # 地域フィルタ
            if region_filter:
                region = href.split('/')[2]
                if region != region_filter:
                    continue
            airline_url = href.rstrip('/') + '/airline'
            country_urls.append(airline_url)
    return list(dict.fromkeys(country_urls))


# ═══════════════════════════════════════
# 航空会社一覧ページ
# ═══════════════════════════════════════

def parse_airline_links(html: str) -> List[str]:
    """
    航空会社一覧ページ (/area/.../airline) のHTMLから、
    各航空会社トップへのリンクパスを抽出する。

    メインコンテンツ領域の .contents-item__header 内のリンクのみ対象とし、
    フッター・サイドバーのリンクは除外する。
    """
    soup = BeautifulSoup(html, 'lxml')
    links: List[str] = []
    for item in soup.select('.contents-item__header a'):
        href = item.get('href', '')
        if href.startswith('/airline/'):
            links.append(href)
    return list(dict.fromkeys(links))


# ═══════════════════════════════════════
# 機材一覧ページ
# ═══════════════════════════════════════

def parse_aircraft_list(html: str) -> Tuple[List[str], Optional[str]]:
    """
    航空会社別・機材一覧ページをパースし、以下を返す:
      1) 個別機体ページへのパスのリスト (/registration/JAxxxx)
      2) ページネーションの次ページURL（なければNone）
    """
    soup = BeautifulSoup(html, 'lxml')
    registration_links: List[str] = []

    # パターン1: 機材一覧の標準テーブル (.regnumber-table)
    for a_tag in soup.select('.regnumber-table__regnumber a'):
        href = a_tag.get('href', '')
        if href.startswith('/registration/'):
            registration_links.append(href)

    # パターン2: 年別一覧等の別レイアウト (.regnumber-table-sm)
    for a_tag in soup.select('div.regnumber-table-sm__info-regnumber a'):
        href = a_tag.get('href', '')
        if href.startswith('/registration/'):
            registration_links.append(href)

    # ページネーション: "次" リンクの取得
    next_page_url: Optional[str] = None
    next_div = soup.select_one('div.next a')
    if next_div:
        href = next_div.get('href')
        if href:
            next_page_url = href

    return list(dict.fromkeys(registration_links)), next_page_url


# ═══════════════════════════════════════
# 個別機体ページ
# ═══════════════════════════════════════

# ADS-B関連テキストを除外するためのパターン
# FlyTeam側に "exchenge" というタイポがあるため、正式な "exchange" と両方をカバー
_ADSB_PATTERN = re.compile(r'ADS-B|adsbexchange|exchenge|exchange', re.IGNORECASE)


def _extract_condition_text(td: Tag) -> str:
    """
    状況欄(.regnumber-table__condition)からクリーンなステータス文字列を取得する。
    ADS-Bトラッカーリンクやアイコン画像のテキストを除外する。

    【注意】元のsoupを破壊するdecompose()は使用しない。
    stripped_stringsからフィルタリングで安全に抽出する。
    """
    condition_node = td.select_one('.regnumber-table__condition')
    if not condition_node:
        return ""

    texts: List[str] = []
    for s in condition_node.stripped_strings:
        # ADS-B関連テキストを正規表現で除外
        if _ADSB_PATTERN.search(s):
            continue
        # 投稿数リンクのテキスト（例: "24枚"）を除外
        if re.match(r'^\d+枚$', s):
            continue
        texts.append(s)

    return _clean(' '.join(texts))


def _extract_serial_from_row(tr: Tag) -> Optional[str]:
    """テーブル行から製造番号を取得する。"""
    sn_node = tr.select_one('.regnumber-table__serial a')
    if sn_node:
        sn = _clean(sn_node.text)
        if sn:
            return sn
    return None


def _extract_hex_code(soup: BeautifulSoup) -> Optional[str]:
    """
    ページ上部の情報セクションからHEXコード(ICAOコード)を取得する。

    HTML構造:
      <dt>HexCode<span class="is-sp-hidden">(ICAOcode)</span></dt>
      <dd>運用中 | <a href="...adsbexchange...">84C068 ...</a></dd>

    <a>タグ内のテキストの先頭部分がHEXコード値。
    """
    # string= は子要素を含むノードでは機能しないため、
    # 全dtを走査してテキスト内容で判定する
    hex_dt = None
    for dt in soup.find_all('dt'):
        if 'HexCode' in dt.get_text():
            hex_dt = dt
            break
    if not hex_dt:
        return None

    hex_dd = hex_dt.find_next_sibling('dd')
    if not hex_dd:
        return None

    # <a>タグがある場合、そのテキストからHEXコードを抽出
    hex_link = hex_dd.find('a')
    if hex_link:
        raw = _clean(hex_link.text)
        # "84C068" のような16進数文字列を先頭から取得
        match = re.match(r'([0-9A-Fa-f]+)', raw)
        if match:
            return match.group(1).upper()

    # <a>がない場合、<dd>のテキスト全体から抽出を試みる
    raw = _clean(hex_dd.get_text())
    match = re.search(r'([0-9A-Fa-f]{6})', raw)
    if match:
        return match.group(1).upper()

    return None



def parse_aircraft_detail(
    html: str,
    target_reg: str,
) -> Tuple[Aircraft, List[AircraftHistory], List[AircraftAlias], List[str]]:
    """
    個別機体ページをパースし、以下を抽出する:
      1) Aircraft         - 機体の基本情報（マスタ）
      2) AircraftHistory[] - 運用履歴のリスト（複数存在しうる）
      3) AircraftAlias[]  - 別機体記号の紐付けリスト
      4) str[]            - 芋づる式に巡回すべき別レジ番のURLパス

    設計原則:
      - 自レジ番の行 → AircraftHistory として保存
      - 別レジ番の行 → AircraftAlias + リンク収集のみ（履歴は当事者ページで取得）
    """
    soup = BeautifulSoup(html, 'lxml')
    target_reg = target_reg.strip().upper()

    histories: List[AircraftHistory] = []
    aliases: List[AircraftAlias] = []
    alias_links: List[str] = []
    serial_number: Optional[str] = None

    # ── HEXコードの取得（ページ上部の情報セクションから） ──
    hex_code = _extract_hex_code(soup)

    # ── .regnumber-table 群からデータを抽出 ──
    for table in soup.select('.regnumber-table'):
        for tr in table.select('tbody tr'):
            td_data = tr.select_one('.regnumber-table__data')
            if not td_data:
                continue

            # レジ番の判定: 行内にレジ番タグがあれば別レジ、なければ自レジ
            reg_node = td_data.select_one('.regnumber-table__regnumber a')
            row_reg = _clean(reg_node.text).upper() if reg_node else target_reg

            # ── 別レジ番の行 → AircraftAlias ──
            if row_reg != target_reg:
                try:
                    aliases.append(AircraftAlias(
                        base_registration=target_reg,
                        alias_registration=row_reg,
                    ))
                except ValueError:
                    pass  # バリデーション失敗時はスキップ
                if reg_node and reg_node.get('href'):
                    alias_links.append(reg_node.get('href'))
                continue

            # ── 自レジ番の行 → AircraftHistory ──

            # 製造番号（行内の .regnumber-table__serial から取得）
            if serial_number is None:
                serial_number = _extract_serial_from_row(tr)

            # 航空会社（URLスラッグ + 表示名）
            airline_node = td_data.select_one('.regnumber-table__airline a')
            airline_slug = ""
            airline_name = ""
            if airline_node:
                airline_name = _clean(airline_node.text)
                href = airline_node.get('href', '')
                if href:
                    airline_slug = href.rstrip('/').split('/')[-1]
            else:
                al_div = td_data.select_one('.regnumber-table__airline')
                if al_div:
                    airline_name = _clean(al_div.get_text())

            # 機種
            model_node = td_data.select_one('.regnumber-table__model a')
            model = _clean(model_node.text) if model_node else ""

            # 期間 (term_start / term_end)
            term_node = td_data.select_one('.regnumber-table__term')
            term_text = _clean(term_node.get_text()) if term_node else ""
            term_start = term_text
            term_end: Optional[str] = None
            if '〜' in term_text:
                parts = term_text.split('〜', 1)
                term_start = _clean(parts[0])
                raw_end = _clean(parts[1]) if len(parts) > 1 else ""
                term_end = raw_end if raw_end else None

            # 状況
            condition_text = _extract_condition_text(td_data)

            # term_startが空の場合はバリデーションで弾かれるのでスキップ
            if not term_start:
                continue

            try:
                histories.append(AircraftHistory(
                    registration_number=target_reg,
                    airline_slug=airline_slug,
                    airline_name=airline_name,
                    model=model,
                    operating_status=condition_text,
                    term_start=term_start,
                    term_end=term_end,
                ))
            except ValueError:
                # バリデーション失敗（空のterm_start等）→ スキップ
                continue

    # ── Aircraftオブジェクトの組み立て ──
    aircraft = Aircraft(
        registration_number=target_reg,
        serial_number=serial_number,
        hex_code=hex_code,
    )

    # ── エイリアスの重複排除 ──
    seen_alias_pairs: set = set()
    unique_aliases: List[AircraftAlias] = []
    for a in aliases:
        pair = (a.base_registration, a.alias_registration)
        if pair not in seen_alias_pairs:
            seen_alias_pairs.add(pair)
            unique_aliases.append(a)

    return aircraft, histories, unique_aliases, list(dict.fromkeys(alias_links))
