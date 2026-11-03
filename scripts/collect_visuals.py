"""
브랜드 비주얼 수집 — 위키미디어 공용(Wikimedia Commons)의 자유 라이선스 사진 중
런웨이·패션쇼·컬렉션·룩북 사진을 브랜드별로 찾는다. 촬영자·라이선스를 함께 저장해 사이트에 표기한다.
결과: seed/visuals.json  {브랜드키: [{u: 썸네일, w: 원본페이지, a: 촬영자, l: 라이선스, t: 제목}]}
"""
import html, json, os, re, sys, time, unicodedata
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://commons.wikimedia.org/w/api.php"
S = requests.Session()
S.headers["User-Agent"] = "DS3-brand-archive/0.1 (https://github.com/bluesiten/Brand)"
KEYS = ["runway", "fashion show", "fashion week", "collection", "catwalk", "défilé", "sfilata", "lookbook"]
BAD = ("naked", "nude", "crotch", "topless", "nsfw", "breast", "erotic", "sexy", "upskirt", "bikini contest",
       "helicopter", "aircraft", "missile", "army", "navy", "air force", "rocket", "locomotive", "warship",
       "lighter", "stadium", "match", " vs ", "logo", "store", "shop", "boutique", "building", "headquarter", "factory", "flagship", "sign",
       "billboard", "advert", "map", "chart", "svg", "icon", "portrait of", "grave", "museum", "street view")
OK_LIC = ("cc by", "cc-by", "cc0", "public domain", "pd", "attribution")


REJ_FILE = os.path.join(ROOT, "seed", "visuals_reject.txt")
REJECT = {l.strip() for l in open(REJ_FILE, encoding="utf-8")} if os.path.exists(REJ_FILE) else set()


def api(params):
    params = {**params, "format": "json", "formatversion": 2}
    for i in range(4):
        try:
            r = S.get(API, params=params, timeout=40)
            if r.status_code == 200:
                return r.json()
            time.sleep(3 * (i + 1))
        except requests.RequestException:
            time.sleep(3 * (i + 1))
    return {}


def key(n):
    n = unicodedata.normalize("NFKD", n or "")
    n = "".join(c for c in n if not unicodedata.combining(c))
    return re.sub(r"[^0-9a-z가-힣]+", "", unicodedata.normalize("NFC", n).lower())


def strip(h):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", h or ""))).strip()


SHOWY = re.compile(r"(runway|catwalk|fashion show|fashion week|défilé|defile|sfilata|backstage|lookbook|look book|"
                   r"(spring|summer|fall|autumn|winter|resort|cruise|couture|\d{4}).{0,25}collection|collection.{0,25}(\d{4}|\bss\b|\bfw\b|\baw\b)|"
                   r"couture|ready.to.wear|prêt|\b(spring|summer|fall|autumn|winter|resort|cruise)\b.{0,12}(\d{4}|\d{2}\b)|"
                   r"\b(ss|fw|aw|pf)\s?\d{2}|\b(19|20)\d{2}\s?(ss|fw|aw)\b|menswear|womenswear|campaign)", re.I)
CAT_SHOW = re.compile(r"(fashion show|runway|catwalk|fashion week|défilé|sfilata|haute couture|ready-to-wear|"
                      r"\b(spring|summer|fall|autumn|winter|resort|cruise|pre-fall)\b.*\d{4}|\d{4}.*\b(spring|summer|fall|autumn|winter)\b|lookbook)", re.I)
CAT_PROD = re.compile(r"(footwear|shoes|sneakers|boots|handbags|bags|products|garments|dresses|clothing by|"
                      r"watches|eyewear|jewellery|jewelry|coats|jackets|jeans)", re.I)


def imageinfo(titles):
    res = api({"action": "query", "titles": "|".join(titles), "prop": "imageinfo",
               "iiprop": "url|size|extmetadata", "iiurlwidth": 900})
    return res.get("query", {}).get("pages", [])


def members(cat, cmtype, limit=50):
    res = api({"action": "query", "list": "categorymembers", "cmtitle": cat, "cmtype": cmtype, "cmlimit": limit})
    return [m["title"] for m in res.get("query", {}).get("categorymembers", [])]


PRODUCT = re.compile(r"\b(dress|gown|shoes?|sneakers?|boots?|bag|handbag|purse|coat|jacket|jeans|denim|scarf|"
                     r"watch|necklace|bracelet|ring|sunglasses|eyewear|trainers?|sandals?|heels|suit|blouse|shirt|"
                     r"knit|sweater|hat|cap|ensemble|outfit)\b", re.I)


def brand_rx(name):
    """브랜드명을 단어 경계로 찾는 정규식 (예: Bulgari ≠ Bulgarian, Nike ≠ Nike Hercules 미사일은 제품어로 거름)"""
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c))
    words = re.findall(r"[A-Za-z0-9]+", n)
    if not words:
        return None
    return re.compile(r"(?<![A-Za-z0-9])" + r"[\W_]*".join(map(re.escape, words)) + r"(?![A-Za-z0-9])", re.I)


def accept(pages, nk, need_brand, limit, out, kind="r", brx=None):
    for p in pages:
        t = p["title"]; tl = t.lower()
        plain = "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))
        if any(b in tl for b in BAD) or not re.search(r"\.(jpe?g|png|webp)$", tl):
            continue
        if brx is None or not brx.search(plain):
            continue
        if kind == "r" and not SHOWY.search(t):
            continue
        if kind == "p" and not PRODUCT.search(t):
            continue
        ii = (p.get("imageinfo") or [{}])[0]
        if ii.get("width", 0) < 600 or ii.get("height", 0) < 600:
            continue
        md = ii.get("extmetadata", {})
        lic = strip(md.get("LicenseShortName", {}).get("value", ""))
        if not any(x in lic.lower() for x in OK_LIC):
            continue
        if ii.get("descriptionurl") in REJECT or t in REJECT:
            continue
        if any(o["w"] == ii.get("descriptionurl") for o in out):
            continue
        out.append({"u": ii.get("thumburl") or ii.get("url"), "w": ii.get("descriptionurl"),
                    "a": strip(md.get("Artist", {}).get("value", ""))[:80] or "Wikimedia Commons",
                    "l": lic, "t": re.sub(r"^File:|\.\w+$", "", t)[:120],
                    "v": 1 if ii.get("height", 0) > ii.get("width", 0) else 0, "k": kind})
        if len(out) >= limit:
            return


def from_category(cat, nk, limit, out, rx, kind, brx=None):
    """브랜드 공식 분류 → 조건(rx)에 맞는 하위 분류(2단계까지)의 사진"""
    subs, frontier = [], [cat]
    for _ in range(2):
        nxt = []
        for c in frontier:
            for sc in members(c, "subcat", 100):
                if any(b in sc.lower() for b in ("store", "shop", "building", "logo", "advert", "people", "sponsor", "athlete", "players")):
                    continue
                nxt.append(sc)
                if rx.search(sc):
                    subs.append(sc)
        frontier = nxt[:20]
    for sc in subs[:12]:
        files = members(sc, "file", 20)
        if files:
            accept(imageinfo(files[:20]), nk, False, limit, out, kind, brx)
        if len(out) >= limit:
            return
        time.sleep(0.1)


def search(q):
    res = api({"action": "query", "generator": "search", "gsrsearch": q, "gsrnamespace": 6, "gsrlimit": 30,
               "prop": "imageinfo", "iiprop": "url|size|extmetadata", "iiurlwidth": 900})
    return sorted(res.get("query", {}).get("pages", []), key=lambda p: p.get("index", 99))


def find(name, commons_cat=None, limit=8):
    out, nk = [], key(name)
    brx = brand_rx(name)
    # ① 공용 분류 안의 패션쇼·시즌 컬렉션 ② 제목에 런웨이·시즌 표기가 있는 검색 결과 ③ 공용 분류 안의 제품 사진
    if commons_cat:
        from_category("Category:" + commons_cat, nk, limit, out, CAT_SHOW, "r", brx)
    if len(out) < limit:
        for kw in ("runway", "fashion show", "fashion week", "collection"):
            accept(search(f'"{name}" {kw}'), nk, True, limit, out, "r", brx)
            if len(out) >= limit:
                break
            time.sleep(0.1)
    if commons_cat and len(out) < 4:
        from_category("Category:" + commons_cat, nk, min(limit, len(out) + 4), out, CAT_PROD, "p", brx)
    if len(out) < 3:
        for kw in ("dress", "shoes", "bag", "jacket"):
            accept(search(f'"{name}" {kw}'), nk, True, min(limit, len(out) + 3), out, "p", brx)
            if len(out) >= 3:
                break
    return out


def commons_cats(qids):
    """위키데이터 P373(공용 분류) 일괄 조회"""
    out = {}
    for i in range(0, len(qids), 50):
        part = qids[i:i + 50]
        try:
            r = S.get("https://www.wikidata.org/w/api.php", params={"action": "wbgetentities", "ids": "|".join(part),
                      "props": "claims", "format": "json"}, timeout=60).json()
        except Exception:
            continue
        for q, e in r.get("entities", {}).items():
            c = e.get("claims", {}).get("P373")
            if c:
                try:
                    out[q] = c[0]["mainsnak"]["datavalue"]["value"]
                except (KeyError, IndexError):
                    pass
        time.sleep(0.3)
    return out


def main():
    test = os.environ.get("TEST")
    site = json.load(open(os.path.join(ROOT, "data", "site.json"), encoding="utf-8"))["b"]
    site.sort(key=lambda b: -(b.get("r") or 0))
    top = int(os.environ.get("TOP", "1500"))
    targets = [b for b in site[:top]] + [b for b in site[top:] if b.get("k")]
    if test:
        targets = targets[:int(test)]
    cats = commons_cats([b["i"] for b in targets if b.get("i")])
    print(f"공용 분류 보유 {len(cats)}개", flush=True)
    def work(b):
        for n in [n for n in (b.get("e"), b["n"]) if n]:
            n = re.sub(r"\s+(AG|SE|S\.p\.A\.|Inc\.?|Ltd\.?|Group|Corporation|Company)$", "", n).strip()
            if len(key(n)) < 3:
                continue
            got = find(n, cats.get(b.get("i")))
            if got:
                return got
        return []
    from concurrent.futures import ThreadPoolExecutor
    vis, hit = {}, 0
    with ThreadPoolExecutor(4) as ex:
        for i, (b, got) in enumerate(zip(targets, ex.map(work, targets))):
            if got:
                vis[b.get("i") or "n-" + b["n"]] = got
                hit += 1
            if test:
                print(f"{b['n']} / {b.get('e','')}: {len(got)}  {[g['k'] + ':' + g['t'][:45] for g in got[:3]]}", flush=True)
            elif i % 100 == 0:
                print(f"  {i}/{len(targets)} — 사진 확보 {hit}", flush=True)
    with open(os.path.join(ROOT, "seed", "visuals.json"), "w", encoding="utf-8") as f:
        json.dump(vis, f, ensure_ascii=False, separators=(",", ":"))
    print(f"완료: {len(targets)}개 중 {hit}개 브랜드 사진 확보")


if __name__ == "__main__":
    main()
