"""
スクレイパー統合テスト

検証項目:
  1. 各パーサーが正しくデータを抽出できるか（単体検証）
  2. パーサー間の出力→入力が実際に繋がるか（連鎖検証）
  3. 抽出結果の具体的な値が正しいか（値検証）
  4. 不正入力に対して安全に処理できるか（異常系検証）
"""
import sys
sys.path.insert(0, '.')

from scraper import (
    parse_country_links,
    parse_airline_links,
    parse_aircraft_list,
    parse_aircraft_detail,
)

HTML_DIR = '../'

passed = 0
failed = 0


def run_test(name, func):
    global passed, failed
    try:
        func()
        passed += 1
        print(f"  ✅ {name}")
    except AssertionError as e:
        failed += 1
        print(f"  ❌ {name}: {e}")
    except Exception as e:
        failed += 1
        print(f"  ❌ {name}: 例外発生 {e.__class__.__name__}: {e}")


# ═══════════════════════════════════════
# 1. エリア一覧ページ (parse_country_links)
# ═══════════════════════════════════════
def test_area_country_count():
    """エリア一覧から200カ国以上の国リンクを抽出できる"""
    with open(HTML_DIR + 'tmp_area.html', encoding='utf-8') as f:
        urls = parse_country_links(f.read())
    assert len(urls) >= 200, f"国数が不足: {len(urls)}"

def test_area_url_format():
    """国リンクが /area/地域/国/airline 形式になっている"""
    with open(HTML_DIR + 'tmp_area.html', encoding='utf-8') as f:
        urls = parse_country_links(f.read())
    for url in urls:
        assert url.endswith('/airline'), f"末尾が/airlineでない: {url}"
        assert url.startswith('/area/'), f"先頭が/area/でない: {url}"
        parts = url.split('/')
        assert len(parts) >= 5, f"パスが浅すぎる: {url}"

def test_area_japan_included():
    """日本が含まれている"""
    with open(HTML_DIR + 'tmp_area.html', encoding='utf-8') as f:
        urls = parse_country_links(f.read())
    assert '/area/asia/japan/airline' in urls, "日本が含まれていない"

def test_area_no_duplicates():
    """重複がない"""
    with open(HTML_DIR + 'tmp_area.html', encoding='utf-8') as f:
        urls = parse_country_links(f.read())
    assert len(urls) == len(set(urls)), "重複がある"


# ═══════════════════════════════════════
# 2. 航空会社一覧ページ (parse_airline_links)
# ═══════════════════════════════════════
def test_airline_count():
    """日本から10社以上の航空会社リンクを抽出できる"""
    with open(HTML_DIR + 'tmp_japan_airlines.html', encoding='utf-8') as f:
        links = parse_airline_links(f.read())
    assert len(links) > 10, f"航空会社が少なすぎる: {len(links)}"

def test_airline_url_format():
    """航空会社リンクが /airline/slug 形式で、不要なサブパスが混入していない"""
    with open(HTML_DIR + 'tmp_japan_airlines.html', encoding='utf-8') as f:
        links = parse_airline_links(f.read())
    for link in links:
        assert link.startswith('/airline/'), f"先頭が不正: {link}"
        assert '/photo' not in link, f"フォトリンク混入: {link}"
        assert '/reviews' not in link, f"レビューリンク混入: {link}"
        assert '/aircrafts' not in link, f"機材リンク混入: {link}"

def test_airline_skymark_included():
    """スカイマークが含まれている"""
    with open(HTML_DIR + 'tmp_japan_airlines.html', encoding='utf-8') as f:
        links = parse_airline_links(f.read())
    assert '/airline/skymark' in links, "スカイマークが含まれていない"


# ═══════════════════════════════════════
# 3. 機材一覧ページ (parse_aircraft_list)
# ═══════════════════════════════════════
def test_list_link_count():
    """機材一覧から1件以上の機体リンクを抽出できる"""
    with open(HTML_DIR + 'tmp_skymark_aircrafts.html', encoding='utf-8') as f:
        links, _ = parse_aircraft_list(f.read())
    assert len(links) > 0, "機体リンクが0件"

def test_list_link_format():
    """機体リンクが /registration/REG 形式"""
    with open(HTML_DIR + 'tmp_skymark_aircrafts.html', encoding='utf-8') as f:
        links, _ = parse_aircraft_list(f.read())
    for link in links:
        assert link.startswith('/registration/'), f"先頭が不正: {link}"

def test_list_pagination():
    """ページネーションの次ページURLが取得できる"""
    with open(HTML_DIR + 'tmp_skymark_aircrafts.html', encoding='utf-8') as f:
        _, next_page = parse_aircraft_list(f.read())
    assert next_page is not None, "次ページが未検出"
    assert 'pageid=2' in next_page, f"次ページURLが不正: {next_page}"


# ═══════════════════════════════════════
# 4. 個別機体ページ 値検証
# ═══════════════════════════════════════
def test_detail_ja31mc_values():
    """JA31MCの具体値が正しい（単純な1履歴パターン）"""
    with open(HTML_DIR + 'tmp_ja31mc.html', encoding='utf-8') as f:
        ac, hist, aliases, links = parse_aircraft_detail(f.read(), 'JA31MC')
    assert ac.registration_number == 'JA31MC'
    assert ac.serial_number == '12963'
    assert ac.hex_code == '85134A'
    assert len(hist) == 1
    assert hist[0].airline_slug == 'starflyer'
    assert hist[0].airline_name == 'SFJ'
    assert hist[0].model == 'A320neo'
    assert hist[0].operating_status == '運用中'
    assert hist[0].term_start == '2025/12'
    assert hist[0].term_end is None
    assert len(aliases) == 0

def test_detail_ja482a_alias():
    """JA482Aの別レジ番(N477SG)が正しく抽出される"""
    with open(HTML_DIR + 'tmp_ja482a.html', encoding='utf-8') as f:
        ac, hist, aliases, links = parse_aircraft_detail(f.read(), 'JA482A')
    assert len(aliases) == 1
    assert aliases[0].base_registration == 'JA482A'
    assert aliases[0].alias_registration == 'N477SG'
    assert '/registration/N477SG' in links

def test_detail_ja23jj_multiple():
    """JA23JJの複数履歴・複数エイリアス・HEXコードが正しい"""
    with open(HTML_DIR + 'tmp_ja23jj.html', encoding='utf-8') as f:
        ac, hist, aliases, links = parse_aircraft_detail(f.read(), 'JA23JJ')
    assert ac.serial_number == '5940'
    assert ac.hex_code == '84C068'
    assert len(hist) == 2
    # 最新の履歴
    assert hist[0].term_end is None
    assert hist[0].operating_status == '運用中'
    # 過去の履歴
    assert hist[1].term_end == '2021/06'
    assert hist[1].operating_status == '抹消'
    # エイリアス
    alias_regs = {a.alias_registration for a in aliases}
    assert 'VH-YXT' in alias_regs
    assert 'F-WXAD' in alias_regs
    assert 'EC-LZF' in alias_regs


# ═══════════════════════════════════════
# 5. 連鎖テスト：パーサー間の出力→入力の連結検証
# ═══════════════════════════════════════
def test_chain_area_to_country():
    """エリア一覧の出力が航空会社一覧の入力URLとして有効"""
    with open(HTML_DIR + 'tmp_area.html', encoding='utf-8') as f:
        country_urls = parse_country_links(f.read())
    # tmp_japan_airlines.html は /area/asia/japan/airline に対応
    assert '/area/asia/japan/airline' in country_urls, \
        "エリア→国の連鎖: 日本の航空会社URL未生成"

def test_chain_country_to_airline():
    """航空会社一覧の出力が「/airline/slug/aircrafts」に変換可能"""
    with open(HTML_DIR + 'tmp_japan_airlines.html', encoding='utf-8') as f:
        airline_links = parse_airline_links(f.read())
    # main.pyでは各リンクに /aircrafts を付加する
    # スカイマークの機材一覧に到達できることを確認
    skymark = [l for l in airline_links if 'skymark' in l]
    assert len(skymark) == 1, f"スカイマークが検出されない: {airline_links[:10]}"
    aircrafts_url = skymark[0].rstrip('/') + '/aircrafts'
    assert aircrafts_url == '/airline/skymark/aircrafts', \
        f"機材一覧URLが不正: {aircrafts_url}"

def test_chain_list_to_detail():
    """機材一覧の出力が個別機体パーサーの入力として使える"""
    with open(HTML_DIR + 'tmp_skymark_aircrafts.html', encoding='utf-8') as f:
        detail_links, _ = parse_aircraft_list(f.read())
    assert len(detail_links) > 0
    # 各リンクからレジ番を抽出し、parse_aircraft_detailに渡せる形式か検証
    for link in detail_links:
        reg = link.rstrip('/').split('/')[-1]
        assert len(reg) > 0, f"レジ番が空: {link}"
        assert reg == reg.strip(), f"レジ番に空白: {link}"

def test_chain_detail_to_alias():
    """個別機体の芋づる式リンクが /registration/REG 形式"""
    with open(HTML_DIR + 'tmp_ja482a.html', encoding='utf-8') as f:
        _, _, _, alias_links = parse_aircraft_detail(f.read(), 'JA482A')
    assert len(alias_links) > 0
    for link in alias_links:
        assert link.startswith('/registration/'), \
            f"芋づるリンクが不正: {link}"

def test_chain_full_pipeline():
    """全階層を通しで連鎖させて、各段階の出力が次の入力として有効"""
    # 1. エリア → 国リスト
    with open(HTML_DIR + 'tmp_area.html', encoding='utf-8') as f:
        country_urls = parse_country_links(f.read())
    assert '/area/asia/japan/airline' in country_urls

    # 2. 国 → 航空会社リスト
    with open(HTML_DIR + 'tmp_japan_airlines.html', encoding='utf-8') as f:
        airline_links = parse_airline_links(f.read())
    assert any('skymark' in l for l in airline_links)

    # 3. 航空会社 → 機材一覧
    with open(HTML_DIR + 'tmp_skymark_aircrafts.html', encoding='utf-8') as f:
        detail_links, next_page = parse_aircraft_list(f.read())
    assert len(detail_links) > 0
    assert next_page is not None

    # 4. 機材一覧 → 個別機体（JA31MCのリンクがあると仮定してHTMLテスト）
    with open(HTML_DIR + 'tmp_ja31mc.html', encoding='utf-8') as f:
        ac, hist, aliases, alias_links = parse_aircraft_detail(f.read(), 'JA31MC')
    assert ac.registration_number == 'JA31MC'
    assert len(hist) >= 1

    # 5. 個別機体 → 芋づる式（JA482Aで検証）
    with open(HTML_DIR + 'tmp_ja482a.html', encoding='utf-8') as f:
        _, _, _, alias_links = parse_aircraft_detail(f.read(), 'JA482A')
    assert len(alias_links) >= 1
    assert all(l.startswith('/registration/') for l in alias_links)


# ═══════════════════════════════════════
# 6. 異常系テスト
# ═══════════════════════════════════════
def test_empty_html():
    """空HTMLでクラッシュしない"""
    assert parse_country_links('') == []
    assert parse_airline_links('') == []
    links, next_p = parse_aircraft_list('')
    assert links == [] and next_p is None
    ac, hist, aliases, alias_links = parse_aircraft_detail('', 'TEST')
    assert ac.registration_number == 'TEST'
    assert hist == []
    assert aliases == []

def test_garbage_html():
    """不正HTMLでクラッシュしない"""
    garbage = '<html><body><div class="broken"><a href="/airline/">test'
    assert parse_airline_links(garbage) == []
    links, _ = parse_aircraft_list(garbage)
    assert links == []

def test_detail_wrong_reg():
    """存在しないレジ番でパースしてもクラッシュしない"""
    with open(HTML_DIR + 'tmp_ja31mc.html', encoding='utf-8') as f:
        html = f.read()
    # regnumber-tableにregnumberタグがない行は自レジ番と判定されるため、
    # 指定したレジ番で履歴が返る（クラッシュしないことが重要）
    ac, hist, aliases, _ = parse_aircraft_detail(html, 'ZZZZZZ')
    assert ac.registration_number == 'ZZZZZZ'


# ═══════════════════════════════════════
# 実行
# ═══════════════════════════════════════
if __name__ == '__main__':
    sections = [
        ("エリア一覧 (parse_country_links)", [
            ("国数 >= 200", test_area_country_count),
            ("URL形式", test_area_url_format),
            ("日本を含む", test_area_japan_included),
            ("重複なし", test_area_no_duplicates),
        ]),
        ("航空会社一覧 (parse_airline_links)", [
            ("10社以上抽出", test_airline_count),
            ("URL形式・不要リンク除外", test_airline_url_format),
            ("スカイマークを含む", test_airline_skymark_included),
        ]),
        ("機材一覧 (parse_aircraft_list)", [
            ("リンク抽出", test_list_link_count),
            ("URL形式", test_list_link_format),
            ("ページネーション", test_list_pagination),
        ]),
        ("個別機体 値検証", [
            ("JA31MC 全フィールド", test_detail_ja31mc_values),
            ("JA482A 別レジ番", test_detail_ja482a_alias),
            ("JA23JJ 複数履歴+エイリアス+HEX", test_detail_ja23jj_multiple),
        ]),
        ("連鎖テスト（パーサー間の接続性）", [
            ("エリア → 国", test_chain_area_to_country),
            ("国 → 航空会社", test_chain_country_to_airline),
            ("機材一覧 → 個別機体", test_chain_list_to_detail),
            ("個別機体 → 芋づるリンク", test_chain_detail_to_alias),
            ("全階層パイプライン通し", test_chain_full_pipeline),
        ]),
        ("異常系", [
            ("空HTML", test_empty_html),
            ("不正HTML", test_garbage_html),
            ("存在しないレジ番", test_detail_wrong_reg),
        ]),
    ]

    for section_name, tests in sections:
        print(f"\n[{section_name}]")
        for test_name, test_func in tests:
            run_test(test_name, test_func)

    print(f"\n{'='*50}")
    print(f"結果: {passed} 件通過, {failed} 件失敗 / 全 {passed+failed} 件")
    if failed > 0:
        print("❌ テスト失敗あり")
        sys.exit(1)
    else:
        print("✅ 全テスト通過")
