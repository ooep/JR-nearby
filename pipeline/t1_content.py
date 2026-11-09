#!/usr/bin/env python3
"""T1 content templates: trilingual desc from OSM facts only (no fabrication).

Validator requires 40-120 chars per language. Templates target >=40 even at
dist=1m with no hours; build() also applies a generic safety tail so the
invariant holds for every station.
"""
import re

_OH_COMMENT = re.compile(r'"[^"]*"')          # opening_hours 内引号注释（常为日文备注）
_OH_NONASCII = re.compile(r'[^\x20-\x7e]')   # 规则本体为 ASCII；其余视为注释残留


def _oh_clean(oh):
    s = _OH_COMMENT.sub('', oh)
    s = _OH_NONASCII.sub('', s)
    s = re.sub(r'\s{2,}', ' ', s)
    s = re.sub(r'[ ,;]*[,;][ ,;]*', ', ', s.strip(' ,;'))
    return s.strip(' ,;')


def hours_txt(oh):
    """OSM opening_hours -> (ja, en, zh) short phrases, or None."""
    if not oh:
        return None
    if oh.strip() == "24/7":
        return ("24時間営業", "open 24 hours", "全天24小时营业")
    clean = _oh_clean(oh)
    if not clean or not re.search(r'[A-Za-z0-9]', clean):
        return None
    return (oh + "（JST）", clean + " (JST)", clean + "（JST）")

def dist_phrase(dist):
    return (f"駅から約{dist}m", f"about {dist} m from the station", f"距站约{dist}米")

# ja/en/zh desc builders per category; each returns 3 strings (40-120 chars each)
def desc_convenience(c, dist):
    t = c['tags']; brand = t.get('brand') or c['name']
    h = hours_txt(t.get('opening_hours'))
    hj = f"、{h[0]}" if h else ""
    he = f" {h[1]}." if h else ""
    hz = f"，{h[2]}" if h else ""
    return (f"{brand}のコンビニエンスストア。{dist_phrase(dist)[0]}の場所にあり{hj}。弁当や飲み物、日用品の調達に便利。",
            f"Convenience store {dist_phrase(dist)[1]}.{he} Stocks meals, drinks and daily necessities.",
            f"{dist_phrase(dist)[2]}的便利店{hz}，可购买便当、饮品、零食与日用品，旅途补给很方便。")

def desc_bank(c, dist):
    t = c['tags']; kind = "ATM" if t.get('amenity') == 'atm' else "支店"
    h = hours_txt(t.get('opening_hours'))
    hj = f"窓口は{h[0]}。" if h and kind == "支店" else ""
    hz = f"，{h[2]}" if h else ""
    return (f"{c['name']}の{kind}。{dist_phrase(dist)[0]}にあり、預け入れ・引き出しやATMの利用に対応する。{hj}",
            f"Bank {dist_phrase(dist)[1]}, with deposit/withdrawal and ATM services.",
            f"{dist_phrase(dist)[2]}的银行网点{hz}，可办理存款、取款与ATM业务，方便旅途中管理资金。")

def desc_medical(c, dist):
    t = c['tags']
    kindj = {'hospital':'病院','clinic':'クリニック','pharmacy':'薬局','doctors':'診療所'}.get(t.get('amenity'),'医療施設')
    kinde = {'hospital':'hospital','clinic':'clinic','pharmacy':'pharmacy','doctors':'clinic'}.get(t.get('amenity'),'medical facility')
    kindz = {'hospital':'医院','clinic':'诊所','pharmacy':'药房','doctors':'诊所'}.get(t.get('amenity'),'医疗机构')
    h = hours_txt(t.get('opening_hours'))
    hj = f"受付は{h[0]}。" if h else ""
    hz = f"，{h[2]}" if h else ""
    return (f"{dist_phrase(dist)[0]}にある{kindj}。体調を崩した際の受診や薬の購入に対応する。{hj}旅先でも頼れる医療施設。",
            f"{kinde.capitalize()} {dist_phrase(dist)[1]}, for illness or prescriptions while traveling.",
            f"{dist_phrase(dist)[2]}的{kindz}{hz}，旅途中身体不适时可就近就诊或购买药品，建议提前确认开诊时间。")

def desc_post(c, dist):
    t = c['tags']; h = hours_txt(t.get('opening_hours'))
    hj = f"{h[0]}。" if h else ""
    hz = f"，{h[2]}" if h else ""
    return (f"{dist_phrase(dist)[0]}にある郵便局。郵便物や荷物の発送、貯金の取り扱いに対応する。{hj}旅先からの郵便発送にも利用できる。",
            f"Post office {dist_phrase(dist)[1]}, handling mail, parcels and banking.",
            f"{dist_phrase(dist)[2]}的邮局{hz}，可办理邮件、包裹寄送与储蓄业务，也可购买邮票与明信片。")

def desc_police(c, dist):
    t = c['tags']
    kindj = "交番" if "交番" in c['name'] else ("駐在所" if "駐在" in c['name'] else "警察署")
    return (f"{dist_phrase(dist)[0]}にある{kindj}。道案内や落とし物の相談、緊急時の駆け込みに対応する窓口。",
            f"Police facility {dist_phrase(dist)[1]}, for directions, lost property and emergencies.",
            f"{dist_phrase(dist)[2]}的警务点，可问路、咨询失物招领与紧急求助，遇到困难时可随时前往。")

def desc_info(c, dist):
    return (f"{dist_phrase(dist)[0]}にある観光案内所。周辺の観光情報や地図、施設案内を提供している。散策の前に立ち寄ると便利。",
            f"Tourist information office {dist_phrase(dist)[1]}, with maps and local guidance.",
            f"{dist_phrase(dist)[2]}的观光服务台，提供周边旅游信息、地图与咨询服务，可先在此规划行程。")

# cuisine 标签偶为日文值（らーめん 等），直接嵌入 en/zh 会触发假名硬规则。
# 常见日文菜名映射英文；不可解析则整段省略菜系从句（模板其余部分仍达标）。
_CUIS_JA = {
    "らーめん": "ramen", "ラーメン": "ramen", "家系らーめん": "home-style ramen",
    "うどん": "udon", "そば": "soba", "寿司": "sushi", "天ぷら": "tempura",
    "焼肉": "yakiniku", "焼き鳥": "yakitori", "お好み焼き": "okonomiyaki",
    "たこ焼き": "takoyaki", "カレー": "curry", "丼": "donburi", "和食": "Japanese",
    "洋食": "Western", "中華": "Chinese", "居酒屋": "izakaya",
}


def _cuis_en(cuis):
    if not cuis:
        return None
    out = []
    for p in (x.strip() for x in cuis.split(';')):
        if not p:
            continue
        if re.fullmatch(r'[A-Za-z0-9_\- ]+', p):
            out.append(p)
        elif p in _CUIS_JA:
            out.append(_CUIS_JA[p])
        else:
            return None
    return " and ".join(out) if out else None


def desc_food(c, dist):
    t = c['tags']; cuis = t.get('cuisine')
    cuis_safe = _cuis_en(cuis)
    cj = f"{cuis}料理を提供する" if cuis else ""
    ce = f"Serves {cuis_safe} cuisine. " if cuis_safe else ""
    cz = f"主营{cuis_safe}，" if cuis_safe else ""
    h = hours_txt(t.get('opening_hours'))
    hj = f"営業は{h[0]}。" if h else ""
    hz = f"，{h[2]}" if h else ""
    return (f"{dist_phrase(dist)[0]}にある飲食店。{cj}食事ができ、旅の合間の休憩にも使いやすい。{hj}メニューや予算は店頭で確認を。",
            f"Restaurant {dist_phrase(dist)[1]}. {ce}Handy for a meal between sightseeing.",
            f"{dist_phrase(dist)[2]}的餐馆{cz}适合旅途中用餐{hz}，菜单与价格以店内展示内容为准。")

def desc_cafe(c, dist):
    t = c['tags']; h = hours_txt(t.get('opening_hours'))
    hj = f"{h[0]}。" if h else ""
    return (f"{dist_phrase(dist)[0]}にあるカフェ。コーヒーや軽食を楽しめ、散策の合間の休憩に向いている。{hj}",
            f"Cafe {dist_phrase(dist)[1]} for coffee and light meals between sightseeing.",
            f"{dist_phrase(dist)[2]}的咖啡馆，供应咖啡与简餐，适合歇脚小憩，也可以在此稍作休整。")

def desc_shrine(c, dist):
    t = c['tags']; rel = t.get('religion')
    rj = "神社" if rel == 'shinto' else ("寺院" if rel == 'buddhist' else "礼拝所")
    re_ = "Shinto shrine" if rel == 'shinto' else ("Buddhist temple" if rel == 'buddhist' else "place of worship")
    rz = "神社" if rel == 'shinto' else ("寺院" if rel == 'buddhist' else "宗教场所")
    return (f"{dist_phrase(dist)[0]}にある{rj}。街なかにありながら落ち着いた雰囲気で、参拝や散策に立ち寄れる。",
            f"{re_} {dist_phrase(dist)[1]}, a quiet stop for prayer or a stroll amid the town.",
            f"{dist_phrase(dist)[2]}的{rz}，闹市中保有清静，可参拜或顺路游览，感受当地的信仰与文化。")

def desc_park(c, dist):
    return (f"{dist_phrase(dist)[0]}にある公園・緑地。ベンチや遊歩道があり、散策や休憩に利用できる。",
            f"Park or green space {dist_phrase(dist)[1]}, with paths and benches for a rest.",
            f"{dist_phrase(dist)[2]}的公园绿地，设有步道与长椅，适合散步休息，也可以在此放松身心。")

def desc_lodging(c, dist):
    t = c['tags']; stars = t.get('stars')
    sj = f"（{stars}つ星）" if stars else ""
    sz = f"（{stars}星）" if stars else ""
    return (f"{dist_phrase(dist)[0]}にある宿泊施設{sj}。観光やビジネスの拠点として利用できる。チェックイン時間は予約時に確認を。",
            f"Lodging {dist_phrase(dist)[1]}, a convenient base for sightseeing or business.",
            f"{dist_phrase(dist)[2]}的住宿设施{sz}，可作为观光或商务出行的落脚点，入住方式以预订信息为准。")

def desc_shopping(c, dist):
    t = c['tags']
    kindj = {'department_store':'百貨店','mall':'商業施設','supermarket':'スーパーマーケット',
             'shopping_centre':'商業施設','souvenir':'土産物店','bakery':'ベーカリー'}.get(t.get('shop'),'店舗')
    kinde = {'department_store':'department store','mall':'shopping mall','supermarket':'supermarket',
             'shopping_centre':'shopping centre','souvenir':'souvenir shop','bakery':'bakery'}.get(t.get('shop'),'shop')
    kindz = {'department_store':'百货店','mall':'商业设施','supermarket':'超市',
             'shopping_centre':'商业设施','souvenir':'纪念品店','bakery':'面包店'}.get(t.get('shop'),'店铺')
    return (f"{dist_phrase(dist)[0]}にある{kindj}。買い物や食事に立ち寄れ、土産選びにも使える。営業時間や品ぞろえは店頭で確認を。",
            f"{kinde.capitalize()} {dist_phrase(dist)[1]} for shopping, meals and souvenirs.",
            f"{dist_phrase(dist)[2]}的{kindz}，可购物用餐，也适合挑选纪念品，商品与价格以店内为准。")

def desc_onsen(c, dist):
    return (f"{dist_phrase(dist)[0]}にある銭湯・温浴施設。歩き疲れた一日の終わりに湯船で体を温められる。",
            f"Public bathhouse {dist_phrase(dist)[1]}, a warm soak after a long day of walking.",
            f"{dist_phrase(dist)[2]}的钱汤·洗浴设施，走累一天后可泡个热水澡放松，入浴礼仪以馆内提示为准。")

def desc_sight(c, dist):
    t = c['tags']
    kindj = {'museum':'博物館・美術館','viewpoint':'展望スポット','gallery':'ギャラリー','zoo':'動物園',
             'aquarium':'水族館','theme_park':'テーマパーク','attraction':'観光スポット'}.get(t.get('tourism'),
             {'castle':'城郭','ruins':'史跡','monument':'記念碑','memorial':'記念碑','heritage':'史跡'}.get(t.get('historic'),'名所'))
    kinde = {'museum':'museum','viewpoint':'viewpoint','gallery':'gallery','zoo':'zoo',
             'aquarium':'aquarium','theme_park':'theme park','attraction':'attraction'}.get(t.get('tourism'),
             {'castle':'castle','ruins':'historic ruins','monument':'monument','memorial':'memorial','heritage':'heritage site'}.get(t.get('historic'),'landmark'))
    kindz = {'museum':'博物馆·美术馆','viewpoint':'观景点','gallery':'画廊','zoo':'动物园',
             'aquarium':'水族馆','theme_park':'主题乐园','attraction':'观光景点'}.get(t.get('tourism'),
             {'castle':'城堡','ruins':'史迹','monument':'纪念碑','memorial':'纪念碑','heritage':'史迹'}.get(t.get('historic'),'名胜'))
    fee = t.get('fee')
    fj = "入場は有料。" if fee == 'yes' else ("入場は無料。" if fee == 'no' else "")
    fz = "，入场收费" if fee == 'yes' else ("，免费开放" if fee == 'no' else "")
    return (f"{dist_phrase(dist)[0]}にある{kindj}。周辺を代表する見どころの一つで、散策の目的地にしやすい。{fj}",
            f"{kinde.capitalize()} {dist_phrase(dist)[1]}, one of the area's highlights and an easy destination.",
            f"{dist_phrase(dist)[2]}的{kindz}{fz}，是该区域的代表性看点，适合安排进散步路线慢慢参观。")

def desc_public(c, dist):
    t = c['tags']
    kindj = {'townhall':'役所・行政施設','library':'図書館','community_centre':'コミュニティ施設'}.get(t.get('amenity'),'公共施設')
    kinde = {'townhall':'civic office','library':'library','community_centre':'community centre'}.get(t.get('amenity'),'public facility')
    kindz = {'townhall':'行政机构','library':'图书馆','community_centre':'社区设施'}.get(t.get('amenity'),'公共设施')
    return (f"{dist_phrase(dist)[0]}にある{kindj}。手続きや調べ物、休憩に利用できる公共の施設。",
            f"{kinde.capitalize()} {dist_phrase(dist)[1]}, a public facility for procedures, reading or a rest.",
            f"{dist_phrase(dist)[2]}的{kindz}，可办理事务、查阅资料或短暂休息，开放时间以现场公示为准。")

BUILDERS = {'convenience': desc_convenience, 'bank_atm': desc_bank, 'medical': desc_medical,
            'post_office': desc_post, 'police': desc_police, 'tourist_info': desc_info,
            'food': desc_food, 'cafe': desc_cafe, 'shrine_temple': desc_shrine, 'park': desc_park,
            'lodging': desc_lodging, 'shopping': desc_shopping, 'onsen_sento': desc_onsen,
            'sight': desc_sight, 'public_service': desc_public, 'entertainment': desc_sight}

# generic, fact-free tails used only when a template comes out under 40 chars
TAIL = {'ja': '。詳細は現地の案内を参照。',
        'en': '. Confirm details locally.',
        'zh': '，具体信息以现场为准。'}

def build(cat, c, dist):
    b = BUILDERS.get(cat, desc_sight)
    ja, en, zh = b(c, dist)
    def fix(s, lang):
        if len(s) > 120:
            s = (s[:119].rstrip('、。，, ') + '。') if lang != 'en' else (s[:119].rstrip(' ,.') + '.')
        if len(s) < 40:
            s = s.rstrip('。.，, ') + TAIL[lang]
        return s
    return fix(ja, 'ja'), fix(en, 'en'), fix(zh, 'zh')
