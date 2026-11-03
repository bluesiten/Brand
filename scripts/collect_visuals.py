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
BAD = ("logo", "store", "shop", "boutique", "building", "headquarter", "factory", "flagship", "sign",
       "billboard", "advert", "map", "chart", "svg", "icon", "portrait of", "grave", "museum", "street view")
OK_LIC = ("cc by", "cc-by", "cc0", "public domain", "pd", "attribution")


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
                   r"collection|couture|ready.to.wear|prêt|spring|summer|fall|autumn|winter|resort|cruise|"
                   r"\b(ss|fw|aw|pf)\s?\d{2}|\b(19|20)\d{2}\s?(ss|fw|aw)\b|menswear|womenswear|campaign)", re.I)
CAT_OK = re.compile(r"(fashion show|runway|catwalk|collection|fashion week|défilé|sfilata|couture|ready-to-wear|"
                    r"spring|summer|fall|autumn|winter|resort|cruise|clothing|garments|dresses|footwear|shoes|bags|"
                    r"products|campaign|lookbook)", re.I)


def imageinfo(titles):
    res = api({"action": "query", "titles": "|".join(titles), "prop": "imageinfo",
               "iiprop": "url|size|extmetadata", "iiurlwidth": 900})
    return res.get("query", {}).get("pages", [])


def members(cat, cmtype, limit=50):
    res = api({"action": "query", "list": "categorymembers", "cmtitle": cat, "cmtype": cmtype, "cmlimit": limit})
    return [m["title"] for m in res.get("query", {}).get("categorymembers", [])]


def accept(pages, nk, need_brand, limit, out):
    for p in pages:
        t = p["title"]; tl = t.lower()
        if any(b in tl for b in BAD) or not re.search(r"\.(jpe?g|png|webp)$", tl):
            continue
        if need_brand and (nk not in key(t) or not SHOWY.search(t)):
            continue
        ii = (p.get("imageinfo") or [{}])[0]
        if ii.get("width", 0) < 600 or ii.get("height", 0) < 600:
            continue
        md = ii.get("extmetadata", {})
        lic = strip(md.get("LicenseShortName", {}).get("value", ""))
        if not any(x in lic.lower() for x in OK_LIC):
            continue
        if any(o["w"] == ii.get("descriptionurl") for o in out):
            continue
        out.append({"u": ii.get("thumburl") or ii.get("url"), "w": ii.get("descriptionurl"),
                    "a": strip(md.get("Artist", {}).get("value", ""))[:80] or "Wikimedia Commons",
                    "l": lic, "t": re.sub(r"^File:|\.\w+$", "", t)[:120],
                    "v": 1 if ii.get("height", 0) > ii.get("width", 0) else 0})
        if len(out) >= limit:
            return


def from_category(cat, nk, limit, out):
    """브랜드 공식 분류 → 패션쇼·컬렉션 하위 분류(2단계까지)의 사진"""
    subs, frontier = [], [cat]
    for _ in range(2):
        nxt = []
        for c in frontier:
            for sc in members(c, "subcat", 100):
                if CAT_OK.search(sc) and not any(b in sc.lower() for b in ("store", "shop", "building", "logo", "advert")):
                    subs.append(sc); nxt.append(sc)
        frontier = nxt[:15]
    for sc in subs[:12]:
        files = members(sc, "file", 20)
        if files:
            accept(imageinfo(files[:20]), nk, False, limit, out)
        if len(out) >= limit:
            return
        time.sleep(0.1)


def search(q):
    res = api({"action": "query", "generator": "search", "gsrsearch": q, "gsrnamespace": 6, "gsrlimit": 30,
               "prop": "imageinfo", "iiprop": "url|size|extmetadata", "iiurlwidth": 900})
    return sorted(res.get("query", {}).get("pages", []), key=lambda p: p.get("index", 99))


def find(name, commons_cat=None, limit=8):
    out, nk = [], key(name)
    if commons_cat:
        from_category("Category:" + commons_cat, nk, limit, out)
    if len(out) < limit:
        for kw in ("runway", "fashion show", "fashion week", "collection"):
            accept(search(f'"{name}" {kw}'), nk, True, limit, out)
            if len(out) >= limit:
                break
            time.sleep(0.1)
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
    top = int(os.environ.get("TOP", "1800"))
    targets = [b for b in site[:top]] + [b for b in site[top:] if b.get("k")]
    if test:
        targets = targets[:int(test)]
    cats = commons_cats([b["i"] for b in targets if b.get("i")])
    print(f"공용 분류 보유 {len(cats)}개", flush=True)
    vis, hit = {}, 0
    for i, b in enumerate(targets):
        names = [n for n in (b.get("e"), b["n"]) if n]
        got = []
        for n in names:
            n = re.sub(r"\s+(AG|SE|S\.p\.A\.|Inc\.?|Ltd\.?|Group|Corporation|Company)$", "", n).strip()
            if len(key(n)) < 3:
                continue
            got = find(n, cats.get(b.get("i")))
            if got:
                break
            time.sleep(0.15)
        if got:
            vis[b.get("i") or "n-" + b["n"]] = got
            hit += 1
        if test:
            print(f"{b['n']} / {b.get('e','')}: {len(got)}  {[g['t'][:50] for g in got[:3]]}", flush=True)
        elif i % 100 == 0:
            print(f"  {i}/{len(targets)} — 사진 확보 {hit}", flush=True)
        time.sleep(0.15)
    with open(os.path.join(ROOT, "seed", "visuals.json"), "w", encoding="utf-8") as f:
        json.dump(vis, f, ensure_ascii=False, separators=(",", ":"))
    print(f"완료: {len(targets)}개 중 {hit}개 브랜드 사진 확보")


if __name__ == "__main__":
    main()
