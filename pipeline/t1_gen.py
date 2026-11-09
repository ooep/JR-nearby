#!/usr/bin/env python3
"""T1 station generator: candidates (cached) -> data/{slug}.json (station.v1).

Content is strictly OSM-fact-driven (no fabrication): names from OSM tags,
desc templates embed only tag facts + computed distance/walk. POIs lacking a
usable trilingual name are skipped (floor categories use best-effort naming).
"""
import json, math, os, re, sys

ROOT = "repo"
TODAY = "2026-11-04"

KANA = {
    'あ':'a','い':'i','う':'u','え':'e','お':'o','か':'ka','き':'ki','く':'ku','け':'ke','こ':'ko',
    'さ':'sa','し':'shi','す':'su','せ':'se','そ':'so','た':'ta','ち':'chi','つ':'tsu','て':'te','と':'to',
    'な':'na','に':'ni','ぬ':'nu','ね':'ne','の':'no','は':'ha','ひ':'hi','ふ':'fu','へ':'he','ほ':'ho',
    'ま':'ma','み':'mi','む':'mu','め':'me','も':'mo','や':'ya','ゆ':'yu','よ':'yo',
    'ら':'ra','り':'ri','る':'ru','れ':'re','ろ':'ro','わ':'wa','を':'wo','ん':'n',
    'が':'ga','ぎ':'gi','ぐ':'gu','げ':'ge','ご':'go','ざ':'za','じ':'ji','ず':'zu','ぜ':'ze','ぞ':'zo',
    'だ':'da','ぢ':'ji','づ':'zu','で':'de','ど':'do','ば':'ba','び':'bi','ぶ':'bu','べ':'be','ぼ':'bo',
    'ぱ':'pa','ぴ':'pi','ぷ':'pu','ぺ':'pe','ぽ':'po',
    'きゃ':'kya','きゅ':'kyu','きょ':'kyo','しゃ':'sha','しゅ':'shu','しょ':'sho','ちゃ':'cha','ちゅ':'chu','ちょ':'cho',
    'にゃ':'nya','にゅ':'nyu','にょ':'nyo','ひゃ':'hya','ひゅ':'hyu','ひょ':'hyo','みゃ':'mya','みゅ':'myu','みょ':'myo',
    'りゃ':'rya','りゅ':'ryu','りょ':'ryo','ぎゃ':'gya','ぎゅ':'gyu','ぎょ':'gyo','じゃ':'ja','じゅ':'ju','じょ':'jo',
    'びゃ':'bya','びゅ':'byu','びょ':'byo','ぴゃ':'pya','ぴゅ':'pyu','ぴょ':'pyo','ふぁ':'fa','ふぃ':'fi','ふぇ':'fe','ふぉ':'fo',
    'っ':'','ー':'-','ぁ':'a','ぃ':'i','ぅ':'u','ぇ':'e','ぉ':'o','ゃ':'ya','ゅ':'yu','ょ':'yo',
}
KATA = {"".join(chr(ord(x) + 0x60) for x in c): v for c, v in KANA.items()
        if all('\u3040' <= x <= '\u309f' for x in c)}  # 平假名(含拗音)->片假名；原条件恒假致片假名未映射
KATA.update({'ー':'-','ッ':''})

def romanize(s):
    out, i = [], 0
    for ch in s:
        if ch in KATA and '\u30a0' <= ch <= '\u30ff':
            out.append(KATA[ch])
        elif ch in KANA and '\u3040' <= ch <= '\u309f':
            out.append(KANA[ch])
        else:
            out.append(ch)
    r = "".join(out)
    r = re.sub(r'tch', 'tch', r)
    return r

def hav(a, b, c, d):
    la1, lo1, la2, lo2 = map(math.radians, (a, b, c, d))
    h = math.sin((la2-la1)/2)**2 + math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2
    return 2*6371000*math.asin(math.sqrt(h))

def wm(dist):
    # walk_min 须满足校验器 55..110 m/min 区间：ceil(d/80) 恒在
    # [ceil(d/110), ceil(d/55)] 内（round(d/75) 在 76..112m 会低估为 1）
    return max(1, -(-dist // 80))

def categorize(t):
    a, sh, to, hi, le = t.get('amenity'), t.get('shop'), t.get('tourism'), t.get('historic'), t.get('leisure')
    if a in ('hospital', 'clinic', 'pharmacy', 'doctors'): return 'medical'
    if a in ('bank', 'atm'): return 'bank_atm'
    if sh == 'convenience': return 'convenience'
    if a == 'post_office': return 'post_office'
    if a == 'police': return 'police'
    if to == 'information' and t.get('information') == 'office': return 'tourist_info'
    if a == 'place_of_worship': return 'shrine_temple'
    if hi in ('temple', 'shrine'): return 'shrine_temple'
    if a == 'public_bath': return 'onsen_sento'
    if to in ('hotel', 'guest_house', 'hostel'): return 'lodging'
    if a in ('restaurant', 'fast_food', 'food_court', 'izakaya', 'bar', 'pub', 'marketplace'): return 'food'
    if a == 'cafe': return 'cafe'
    if sh in ('department_store', 'mall', 'supermarket', 'shopping_centre', 'souvenir', 'bakery'): return 'shopping'
    if le in ('park', 'garden'): return 'park'
    if hi in ('castle', 'ruins', 'monument', 'memorial', 'heritage', 'church'): return 'sight'
    if to in ('museum', 'gallery', 'zoo', 'aquarium', 'theme_park', 'viewpoint', 'attraction'): return 'sight'
    if a in ('townhall', 'library', 'community_centre'): return 'public_service'
    return None

FLOOR = ("medical", "bank_atm", "convenience", "post_office", "police", "tourist_info")
# OSM 误标过滤：招牌/物件类命名不是设施实体（如"…という看板（月極駐車場）"），不作 POI
JUNK_RE = re.compile(r'看板|という看板|月極駐車場')
CAT_CAP = {"sight": 8, "shrine_temple": 3, "food": 3, "cafe": 1, "shopping": 3, "park": 2,
           "lodging": 2, "onsen_sento": 1, "entertainment": 1, "public_service": 2}

def score(c):
    t = c['tags']
    s = 0.0
    if t.get('wikidata'): s += 3
    if t.get('wikipedia'): s += 2
    if t.get('website') or t.get('contact:website'): s += 1.5
    if t.get('opening_hours'): s += 0.5
    if t.get('phone') or t.get('contact:phone'): s += 0.25
    s += (2000 - c['distance_m']) / 2000 * 1.5
    return s

def select(cands):
    bycat = {}
    junk = []
    for c in cands:
        if JUNK_RE.search(c['name']):
            junk.append(c)
            continue
        cat = categorize(c['tags'])
        if cat:
            bycat.setdefault(cat, []).append(c)
    for v in bycat.values():
        v.sort(key=lambda c: (-score(c), c['distance_m']))
    picked, seen_names = [], set()
    def take(c, cat):
        nm = re.sub(r'\s+', '', c['name'])
        if nm in seen_names:
            return False
        seen_names.add(nm)
        picked.append((cat, c))
        return True
    # floor first
    for cat in FLOOR:
        lst = bycat.get(cat, [])
        if not lst:
            continue
        if cat == 'medical':
            tier = {'hospital': 3, 'clinic': 2, 'pharmacy': 1, 'doctors': 2}
            lst = sorted(lst, key=lambda c: (-tier.get(c['tags'].get('amenity'), 0), -score(c), c['distance_m']))
        take(lst[0], cat)
    # diversity fill
    counts = {}
    for k, _ in picked:
        counts[k] = counts.get(k, 0) + 1
    pool = []
    for cat, lst in bycat.items():
        for c in lst:
            pool.append((cat, c, score(c)))
    pool.sort(key=lambda x: (-x[2], x[1]['distance_m']))
    for cat, c, _ in pool:
        if len(picked) >= 20:
            break
        if counts.get(cat, 0) >= CAT_CAP.get(cat, 2):
            continue
        if any(hav(c['lat'], c['lon'], p['lat'], p['lon']) < 40 and re.sub(r'\s','',c['name'])==re.sub(r'\s','',p['name']) for _, p in picked):
            continue
        if take(c, cat):
            counts[cat] = counts.get(cat, 0) + 1
    picked.sort(key=lambda x: x[1]['distance_m'])
    return picked[:20], bycat, junk

if __name__ == "__main__":
    slug = sys.argv[1]
    d = json.load(open(f"t1cache/{slug}.json"))
    picked, bycat, junk = select(d['candidates'])
    print(slug, d['station']['name'], 'picked', len(picked))
    for cat, c in picked:
        print('  ', cat, c['distance_m'], c['name'], c['tags'].get('name:en') or '-')

# ---- assembly ----
J2Z = str.maketrans("東京东京橋駅館園宮島門口町屋堂閣城浜瀬岡崎津田原沢谷幡戸木本桜梅竹松柳井泉池坂上中下北南大高小山川島村町府県区部線海岸通谷原野崎辻堂塔寺神社仏閣苑舗店舗場橋梁堤堰関所館舎庁署校院宮殿邸宅庭園遊陸競技記念博物図図書公民集会体育武道文化芸術劇場映画放送郵便電信電話水力火力原子力発電変電所配給供給塔灯台橋梁隧道坑道堀川江湾浦潟湖沼池泉滝渓谷峰嶺峠坂岬崎半島列島諸島環礁干潟砂丘洞窟鍾乳石岩礁珊瑚愛済鉄亀広資歴韓観鐵國廣車長門馬鳥龍電學醫銀萬華畫觀廟豐淺區數喬歲讀賣灣驛",
                    "东京东京桥站馆园宫岛门口町屋堂阁城滨濑冈崎津田原泽谷幡户木本樱梅竹松柳井泉池坂上中下北南大高小山川岛村町府县区部线海岸通谷原野崎辻堂塔寺神社佛阁苑铺店铺场桥梁堤堰关所馆舍厅署校院宫殿邸宅庭园游陆竞技纪念博物图图书公民集会体育武道文化艺术剧场电影放送邮政电信电话水力火力原子力发电变电所配给供给塔灯台桥梁隧道坑道堀川江湾浦潟湖沼池泉瀑溪谷峰岭峠坂岬崎半岛列岛诸岛环礁干潟砂丘洞窟钟乳石岩礁珊瑚爱济铁龟广资历韩观铁国广车长门马鸟龙电学医银万华画观庙丰浅区数乔岁读卖湾驿")
BRAND = {
 "セブン-イレブン": ("Seven-Eleven","7-11便利店"), "ファミリーマート": ("FamilyMart","全家便利店"),
 "ローソン": ("Lawson","罗森"), "ミニストップ": ("MINISTOP","迷你岛便利店"), "デイリーヤマザキ": ("Daily Yamazaki","Daily Yamazaki便利店"),
 "セイコーマート": ("Seicomart","セイコーマート便利店"), "みずほ銀行": ("Mizuho Bank","瑞穗银行"),
 "三井住友銀行": ("SMBC (Mitsui Sumitomo) Bank","三井住友银行"), "三菱UFJ銀行": ("MUFG Bank","三菱UFJ银行"),
 "りそな銀行": ("Resona Bank","理索纳银行"), "ゆうちょ銀行": ("Japan Post Bank","邮储银行"),
 "スターバックスコーヒー": ("Starbucks Coffee","星巴克"), "タリーズコーヒー": ("Tully's Coffee","塔利咖啡"),
 "ドトールコーヒーショップ": ("Doutor Coffee","多特咖啡"), "マクドナルド": ("McDonald's","麦当劳"),
 "ケンタッキーフライドチキン": ("KFC","肯德基"), "吉野家": ("Yoshinoya","吉野家"),
 "すき家": ("Sukiya","食其家"), "松屋": ("Matsuya","松屋"), "モスバーガー": ("MOS Burger","摩斯汉堡"),
 "ガスト": ("Gusto","Gusto家庭餐厅"), "ロイヤルホスト": ("Royal Host","乐雅乐家庭餐厅"),
 "サイゼリヤ": ("Saizeriya","萨莉亚"), "大丸": ("Daimaru","大丸百货"), "松坂屋": ("Matsuzakaya","松坂屋"),
 "高島屋": ("Takashimaya","高岛屋"), "三越": ("Mitsukoshi","三越"), "伊勢丹": ("Isetan","伊势丹"),
 "西武": ("Seibu","西武"), "そごう": ("Sogo","崇光"), "東急ハンズ": ("Tokyu Hands","东急Hands"),
 "無印良品": ("MUJI","无印良品"), "ドン・キホーテ": ("Don Quijote","唐吉诃德"), "ビックカメラ": ("Bic Camera","必酷"),
 "ヤマダ電機": ("Yamada Denki","山田电机"), "イオン": ("AEON","永旺"), "イトーヨーカドー": ("Ito-Yokado","伊藤洋华堂"),
 "西友": ("Seiyu","西友"), "ライフ": ("Life","Life超市"), "成城石井": ("Seijo Ishii","成城石井"),
 "明治神宮": ("Meiji Jingu","明治神宫"), "東京タワー": ("Tokyo Tower","东京塔"), "増上寺": ("Zojo-ji Temple","增上寺"),
}
# 人工核定的名称覆盖（osm_id -> 字段）。每条都有官方来源，禁止无来源扩写：
#   1420785393 東京都済生会中央病院: 官网 saichu.jp 英文页/International Patient Guide
#     用 "Tokyo Saiseikai Central Hospital"（2026-11-04 核实）。
#   11262093573: OSM name 是英文；官方日文名=在日韓人歴史資料館（官网 j-koreans.org），
#     官方英文=The History Museum of J-Koreans，官网中文页题=在日韩人历史资料馆。
#   13655198568 港区観光インフォメーションセンター（札の辻スクエア）:
#     官方英文=Minato City Tourist Information Center（tokyotouristinfo.com 登录名）。
#   690845548 日本橋観光案内所: 官网 nihonbashi-info.tokyo/tourism/ 页面标题
#     "日本橋観光案内 Nihonbashi Tourism Information"（2026-11-04 本地实测 200）。
#   4730286194 たいめいけん: 官方日文名即平假名（官网 taimeiken.co.jp 标题，
#     2026-11-04 本地实测 200）；英文 Taimeiken 见官网英文表述；
#     中文用通行译名"泰明轩"（官方无中文页）。
#   3666894176 相田みつを美術館: 官网 mitsuo.co.jp 标题
#     "相田みつを美術館 ーMitsuo Aida Museumー"（2026-11-04 本地实测 200）；
#     中文用通行译名"相田光男美术馆"（官方无中文页，みつを 无汉字对应）。
#   1635279739 神田まつや: 官网 kanda-matsuya.jp 标题"神田まつや オフィシャルサイト"
#     （2026-11-04 本地实测 200），官方名即平假名；中文用通行译名"神田松屋"。
#   6026372096 第一三共くすりミュージアム: 官网 kusuri-museum.com 标题
#     "Daiichi Sankyoくすりミュージアム｜第一三共株式会社"（2026-11-04 本地实测 200）；
#     中文用通行译名"第一三共药品博物馆"（官方无中文页）。
#   142156957 芝パークホテル: 官网 shibaparkhotel.com 标题"芝パークホテル｜東京・浜松町
#     にあるライブラリーホテル｜公式サイト"（2026-11-04 本地实测 200）；
#     中文用通行译名"芝公园酒店"（官方无中文页）。
#   4864258012 Conrad Tokyo: 希尔顿旗下 Conrad 品牌官方中文译名"康莱德"；
#     中文用"东京康莱德酒店"（品牌官方译名惯例）。
NAME_OVERRIDE = {
    1420785393: {"en": "Tokyo Saiseikai Central Hospital", "zh": "东京都济生会中央医院"},
    11262093573: {"ja": "在日韓人歴史資料館", "en": "The History Museum of J-Koreans",
                  "zh": "在日韩人历史资料馆"},
    13655198568: {"en": "Minato City Tourist Information Center", "zh": "港区观光信息中心"},
    690845548: {"en": "Nihonbashi Tourism Information"},
    4730286194: {"zh": "泰明轩"},
    3666894176: {"zh": "相田光男美术馆"},
    1635279739: {"zh": "神田松屋"},
    6026372096: {"zh": "第一三共药品博物馆"},
    142156957: {"zh": "芝公园酒店"},
    4864258012: {"zh": "东京康莱德酒店"},
}
# 人工核定的事实覆盖（osm_id -> 字段），来源见上及官网页面（2026-11-04 核实）：
#   資料館官网 info 页: Hours 10:00-17:30（17:00 停止入馆）; Closed 周日・周一・5/3-5/5・年末年始;
#   TEL +81-3-3457-1088。済生会官网 saichu.jp 本地实测 200，升为 official_site。
FACT_OVERRIDE = {
    1420785393: {"url": "https://www.saichu.jp/", "source": ["official_site", "osm"]},
    11262093573: {"hours": "Tu-Sa 10:00-17:30 (JST)", "hours_source": "official",
                  "hours_checked_at": "2026-11-04",
                  "closed_on": "日曜・月曜・5月3日-5日・年末年始",
                  "tel": "+81-3-3457-1088", "tel_source": "official"},
}
# 按日文名覆盖（用于缓存已删、osm_id 不可查的站；来源见注释）：
#   Conrad Tokyo: 希尔顿 Conrad 品牌官方中文译名"康莱德"。
#   Art Aquarium Museum GINZA: 官网 artaquarium.jp 标题"アートアクアリウム | ART AQUARIUM"
#     （2026-11-04 本地实测 200）；中文用通行译名"银座艺术水族馆"（官方无中文页）。
#   Switzerland Tourism: 瑞士国家旅游局官方中文品牌名（其官方中文渠道自用名；
#     官网中文页有反爬墙，未能取页面证据）。
NAME_OVERRIDE_BY_JA = {
    "Conrad Tokyo": {"zh": "东京康莱德酒店"},
    "Art Aquarium Museum GINZA": {"zh": "银座艺术水族馆"},
    "Switzerland Tourism": {"zh": "瑞士国家旅游局"},
}


def simplify(s):
    return s.translate(J2Z)
def names_for(c, cat):
    t = c['tags']; nm = c['name']
    ja = nm
    en = c.get('name_en') or t.get('int_name')
    zh = c.get('name_zh')
    if zh:
        zh = simplify(zh)  # OSM zh 标签偶有繁体，统一转简体
    review = []
    if nm in BRAND:
        en = en or BRAND[nm][0]; zh = zh or BRAND[nm][1]
    if not en:
        en = romanize(nm)
        if not re.search(r'[A-Za-z]', en):
            en = nm; review.append('en')
    if not zh:
        z = simplify(nm)
        zh = z
        if not re.search(r'[\u4e00-\u9fff]', zh):
            review.append('zh')
    if re.search(r'[\u30a0-\u30ff\u30fc]{13,}', zh):
        review.append('zh')  # 长片假名串=未翻译，需人工核定
    return ja, en, zh, review

import subprocess, datetime
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

def url_status(u):
    p = subprocess.run(["curl","-s","-o","/dev/null","-L","--max-time","15","-w","%{http_code}",
                        "-A",UA,"-H","Accept: text/html,application/xhtml+xml,*/*",u],capture_output=True)
    try: return int(p.stdout.decode().strip())
    except ValueError: return 0

def hours_from_oh(oh):
    if not oh: return None
    if oh.strip()=="24/7": return "24時間営業 24/7 (JST)"
    return f"{oh} (JST)"

LATIN_RE = re.compile(r'[A-Za-z]')
KANA_RE = re.compile(r'[\u3040-\u309f\u30a0-\u30ff]')
HIRA_RE = re.compile(r'[\u3040-\u309f]')
KANJI_RE = re.compile(r'[\u4e00-\u9fff]')
KATA_RUN_RE = re.compile(r'[\u30a0-\u30ff]{13,}')

def name_hard_ok(en, zh):
    """validate.py 的名称硬规则镜像（en: 有拉丁、无假名；zh: 无平假名、
    片假名串<=12、有汉字或拉丁；各语言 <=80 字符）。"""
    if not LATIN_RE.search(en): return False
    if KANA_RE.search(en): return False
    if HIRA_RE.search(zh): return False
    if KATA_RUN_RE.search(zh): return False
    if not KANJI_RE.search(zh) and not LATIN_RE.search(zh): return False
    if len(en) > 80 or len(zh) > 80: return False
    return True

def fit80(s):
    if len(s) <= 80:
        return s
    cut = s[:80]
    return cut[:cut.rfind(' ')] if ' ' in cut else cut

# 纯汉字名（无假名可罗马字化）的英文兜底：zh 通用词 -> 英文通名。
# 通名来自 OSM 类别事实，专名保留 zh 简体，属翻译而非编造。
ZH2EN_GENERIC = [
    ("邮政局", "Post Office"), ("邮局", "Post Office"),
    ("银行", "Bank"), ("信用金库", "Credit Union"), ("金库", "Credit Union"),
    ("信用金庫", "Credit Union"), ("金庫", "Credit Union"),  # J2Z 未收 庫，繁体原样兜底
    ("医院", "Hospital"), ("诊所", "Clinic"),
    ("药房", "Pharmacy"), ("警察署", "Police Station"), ("交番", "Police Box"),
    ("派出所", "Police Station"), ("便利店", "Convenience Store"),
    ("警察本部", "Police Headquarters"),
    ("观光案内所", "Tourist Information"), ("旅游咨询", "Tourist Information"),
    ("案内所", "Information Center"),
    ("观光協会", "Tourist Association"), ("观光协会", "Tourist Association"),
    ("観光協会", "Tourist Association"),
    ("センター", "Center"),  # J2Z 不转 センター，zh 原样保留，en 通名兜底
]

def kanji_en_fallback(zh):
    for zw, ew in ZH2EN_GENERIC:
        if zw in zh:
            rest = zh.replace(zw, "").strip()
            rest = rest.replace("ヶ", "ga").replace("ケ", "ga")  # 小ヶ读 ga（同站名约定），防 KANA_RE 判败
            rest = re.sub(r'\s+', ' ', rest.replace("・", " "))  # 中点在 KANA_RE 范围内，转空格
            return f"{rest} {ew}" if rest else ew
    return None

def romaji_title(ja):
    r = romanize(ja)
    return " ".join(w[:1].upper() + w[1:] for w in r.split() if w) if LATIN_RE.search(r) else r

def override_for(c):
    return NAME_OVERRIDE.get(c['osm_id']) or NAME_OVERRIDE_BY_JA.get(c['name'])


def name_fallback(c, en, zh, rev):
    """罗马字/通名兜底（主候选与替补共用）：优先产出过硬规则的 en——
    罗马字转写若残留假名（如 センター→senta- 与汉字混排），让位通名表；
    zh 含假名/无汉字→罗马字转写。返回 (en, zh, rev+auto-romaji)。"""
    if not LATIN_RE.search(en) or KANA_RE.search(en) or len(en) > 80:
        rj = fit80(romaji_title(c['name']))
        rj_ok = LATIN_RE.search(rj) and not KANA_RE.search(rj) and len(rj) <= 80
        alt_en = kanji_en_fallback(zh)
        if rj_ok:
            en = rj
        elif alt_en:
            en = fit80(alt_en)
        else:
            en = rj
    if HIRA_RE.search(zh) or KATA_RUN_RE.search(zh) or (not KANJI_RE.search(zh) and not LATIN_RE.search(zh)) or len(zh) > 80:
        zh = fit80(romaji_title(c['name']))
    return en, zh, (rev or []) + ['auto-romaji']


def resolve_names(cat, c, bycat, used_keys, used_names):
    """名称三级兜底：1) 人工覆盖 2) 同类下一候选 3) 罗马字转写（忠实转写，
    打 review 标记）。替补候选须避开已用名称（防站内重名）。
    返回 (c, ja, en, zh, rev, swapped_or_fallback)。"""
    ja, en, zh, rev = names_for(c, cat)
    ov = override_for(c)
    if ov:
        ja, en, zh = ov.get('ja', ja), ov.get('en', en), ov.get('zh', zh)
        rev = []
        if not LATIN_RE.search(en): rev.append('en')
        if not KANJI_RE.search(zh) or KATA_RUN_RE.search(zh): rev.append('zh')
    if name_hard_ok(en, zh):
        return c, ja, en, zh, rev, False
    for alt in bycat.get(cat, []):
        akey = (alt['osm_type'], alt['osm_id'])
        if akey in used_keys:
            continue
        if re.sub(r'\s+', '', alt['name']) in used_names:
            continue
        ja2, en2, zh2, rev2 = names_for(alt, cat)
        ov2 = override_for(alt)
        if ov2:
            ja2, en2, zh2 = ov2.get('ja', ja2), ov2.get('en', en2), ov2.get('zh', zh2)
            rev2 = []
            if not LATIN_RE.search(en2): rev2.append('en')
            if not KANJI_RE.search(zh2) or KATA_RUN_RE.search(zh2): rev2.append('zh')
        if name_hard_ok(en2, zh2):
            used_keys.add(akey)
            return alt, ja2, en2, zh2, rev2, True
        en2, zh2, rev2 = name_fallback(alt, en2, zh2, rev2)
        if name_hard_ok(en2, zh2):
            used_keys.add(akey)
            return alt, ja2, en2, zh2, rev2, True
    en, zh, rev = name_fallback(c, en, zh, rev)
    return c, ja, en, zh, rev, False

# 站名英文覆盖（stations_all 无 name_en 且含小片假名 ヵ/ヶ 的站；
# 均为车站标识官方罗马字：霞ケ関=Kasumigaseki，其余 ヶ 读 ga）
STATION_EN_OVERRIDE = {
    "霞ケ関": "Kasumigaseki",
    "市ケ谷": "Ichigaya",
    "新百合ケ丘": "Shin-yurigaoka",
    "茅ケ崎": "Chigasaki",
    "新鎌ケ谷": "Shin-kamagaya",
    "三国ケ丘": "Mikunigaoka",
}

# 站名中文覆盖（站名含平假名，simplify 无法转换；取中文圈通用译名）
STATION_ZH_OVERRIDE = {
    "自由が丘": "自由之丘",
    "溝の口": "沟之口",
    "あざみ野": "蓟野",
    "流山おおたかの森": "流山大鹰之森",
    "さっぽろ": "札幌",
    "さいたま新都心": "埼玉新都心",
    # T2 阶段补录（站名含平假名，simplify 保留假名会触发 zh 平假名违规）
    "勝どき": "胜哄",
    "たまプラーザ": "多摩广场",
    "藤が丘": "藤之丘",
    "みなとみらい": "港未来",
    "ひばりケ丘": "云雀丘",
    "ふじみ野": "富士见野",
    "光が丘": "光丘",
    "せんげん台": "千元台",
    "千葉みなと": "千叶港",
    "ときわ台": "常磐台",
    "あおば通": "青叶通",
    "つつじケ丘": "杜鹃丘",
    "市が尾": "市尾",
    "みずほ台": "瑞穗台",
    "八千代緑が丘": "八千代绿丘",
    "新さっぽろ": "新札幌",
    "旗の台": "旗之台",
    "梶が谷": "梶谷",
    "柏の葉キャンパス": "柏之叶校园",
}


def build_station(slug, check_urls=True):
    import t1_content
    d = json.load(open(f"t1cache/{slug}.json"))
    st = d['station']; slat, slon = st['lat'], st['lon']
    picked, bycat, junk = select(d['candidates'])
    used_keys = {(c['osm_type'], c['osm_id']) for _, c in picked}
    used_names = {re.sub(r'\s+', '', c['name']) for _, c in picked}
    resolved, review = [], []
    for cat, c in picked:
        c, ja, en, zh, rev, _ = resolve_names(cat, c, bycat, used_keys, used_names)
        if not name_hard_ok(en, zh):
            continue  # 极端情形：罗马字兜底仍不过（无假名可转），弃收
        used_names.add(re.sub(r'\s+', '', c['name']))
        resolved.append((cat, c, ja, en, zh, rev))
    pois = []
    for i, (cat, c, ja, en, zh, rev) in enumerate(resolved, start=1):
        if rev: review.append((f"{slug}-{i:03d}", ja, rev))
        t = c['tags']; dist = c['distance_m']
        dja,den,dzh = t1_content.build(cat,c,dist)
        off = t.get('website') or t.get('contact:website')
        url, src, pending = c['osm_url'], ["osm"], None
        if off and re.match(r'^https?://[^ ]+$', off):
            if check_urls:
                code = url_status(off)
            else:
                code = 200
            if code == 200:
                url, src = off, ["official_site","osm"]
            elif code in (403,429,500,502,503,0):
                pending = off
            # 404/410: drop official, keep OSM url
        oh = hours_from_oh(t.get('opening_hours'))
        tel = t.get('phone') or t.get('contact:phone')
        price = None
        stars = t.get('stars')
        if cat=='lodging' and stars: price = '¥'*min(4,int(stars)) if str(stars).isdigit() else None
        pois.append({
            "id": f"{slug}-{i:03d}", "category": cat,
            "name": {"ja": ja, "en": en, "zh": zh},
            "desc": {"ja": dja, "en": den, "zh": dzh},
            "distance_m": dist, "walk_min": wm(dist),
            "hours": oh, "hours_source": "osm" if oh else "unknown",
            "url": url, "tel": tel, "tel_source": "osm" if tel else "unknown",
            "price_level": price, "source": src, "verified_at": TODAY,
            "lat": c['lat'], "lon": c['lon'],
            **({"official_url_pending": pending} if pending else {}),
        })
        fo = FACT_OVERRIDE.get(c['osm_id'])
        if fo:
            pois[-1].update(fo)
    fc = {}
    gaps = []
    GAP_TXT = {"medical":"2公里圈内未检索到医疗机构候选","bank_atm":"2公里圈内未检索到银行/ATM候选",
               "convenience":"2公里圈内未检索到便利店候选","post_office":"2公里圈内未检索到邮局候选",
               "police":"2公里圈内未检索到警察设施候选","tourist_info":"2公里圈内未检索到观光案内所候选"}
    for cat in FLOOR:
        n = len(bycat.get(cat, []))
        sel = sum(1 for p in pois if p['category']==cat)
        fc[cat] = {"candidates": n, "selected": sel}
        if n == 0: gaps.append(GAP_TXT[cat])
    if len(pois) < 4:
        gaps.append("站周边候选稀少，仅收录上述条目，商业与餐饮需前往邻近车站周边")
    named = len(d['candidates'])
    doc = {
        "schema": "station.v1",
        "station": {"id": slug, "name": {"ja": st['name'],
                                          "en": st.get('name_en') or STATION_EN_OVERRIDE.get(st['name']) or romanize(st['name']),
                                          "zh": STATION_ZH_OVERRIDE.get(st['name']) or simplify(st['name'])},
                     "line_group": (st.get('lines') or ['JR'])[:12],
                     "pref": st['pref'], "lat": slat, "lon": slon},
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pois": pois,
        "coverage": {"poi_count": len(pois), "radius_m": 2000,
                      "confidence": "high" if len(pois)>=8 else ("medium" if len(pois)>=4 else "low"),
                      "gaps": gaps, "fetch_truncated": bool(d.get('fetch_truncated')),
                      "selected_cap": 20, "candidate_count": named, "floor_check": fc},
    }
    if junk:
        doc["coverage"]["excluded_candidates"] = [
            {"name": c["name"], "reason": "OSM 误标：招牌/物件类命名，非设施实体，不作 POI 收录"}
            for c in junk[:10]]
    return doc, review
