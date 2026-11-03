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


def find(name, limit=6):
    q = f'"{name}" ({" OR ".join(chr(34) + k + chr(34) for k in KEYS[:6])}) filetype:bitmap'
    res = api({"action": "query", "generator": "search", "gsrsearch": q, "gsrnamespace": 6, "gsrlimit": 30,
               "prop": "imageinfo", "iiprop": "url|size|extmetadata", "iiurlwidth": 900})
    pages = sorted(res.get("query", {}).get("pages", []), key=lambda p: p.get("index", 99))
    out, nk = [], key(name)
    for p in pages:
        t = p["title"]
        tl = t.lower()
        if nk not in key(t) or any(b in tl for b in BAD):
            continue
        ii = (p.get("imageinfo") or [{}])[0]
        if ii.get("width", 0) < 600 or ii.get("height", 0) < 600:
            continue
        md = ii.get("extmetadata", {})
        lic = strip(md.get("LicenseShortName", {}).get("value", ""))
        if not any(x in lic.lower() for x in OK_LIC):
            continue
        out.append({"u": ii.get("thumburl") or ii.get("url"), "w": ii.get("descriptionurl"),
                    "a": strip(md.get("Artist", {}).get("value", ""))[:80] or "Wikimedia Commons",
                    "l": lic, "t": re.sub(r"^File:|\.\w+$", "", t)[:120],
                    "v": 1 if ii.get("height", 0) > ii.get("width", 0) else 0})
        if len(out) >= limit:
            break
    return out


def main():
    test = os.environ.get("TEST")
    site = json.load(open(os.path.join(ROOT, "data", "site.json"), encoding="utf-8"))["b"]
    site.sort(key=lambda b: -(b.get("r") or 0))
    top = int(os.environ.get("TOP", "1800"))
    targets = [b for b in site[:top]] + [b for b in site[top:] if b.get("k")]
    if test:
        targets = targets[:int(test)]
    vis, hit = {}, 0
    for i, b in enumerate(targets):
        names = [n for n in (b.get("e"), b["n"]) if n]
        got = []
        for n in names:
            n = re.sub(r"\s+(AG|SE|S\.p\.A\.|Inc\.?|Ltd\.?|Group|Corporation|Company)$", "", n).strip()
            if len(key(n)) < 3:
                continue
            got = find(n)
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
