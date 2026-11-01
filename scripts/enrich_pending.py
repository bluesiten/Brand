"""
'보강 필요' 브랜드의 공식 사이트를 찾는다.
브랜드명으로 도메인을 추정해 접속 → 페이지 제목/사이트명에 브랜드명이 있고 패션 관련 단어가 있으면 공식 사이트로 인정.
그 사이트(브랜드 본인 사이트)에 걸린 인스타그램 링크와 소개 문구(meta description)를 가져온다.
"""
import csv, html, json, os, re, sys, unicodedata
from concurrent.futures import ThreadPoolExecutor
import requests
sys.path.insert(0, os.path.dirname(__file__))
from collect_brands import norm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = {"User-Agent": "Mozilla/5.0 (compatible; DS3-brand-archive/0.1; +https://github.com/bluesiten/brand)"}
TLDS = [".com", ".co", ".studio", ".net", ".store", ".shop", ".co.uk", ".it", ".fr", ".de", ".jp",
        ".co.kr", ".kr", ".com.au", ".dk", ".se", ".us", ".world", ".online", ".paris", ".nyc"]
# '쇼핑몰'만 같은 사이트를 거르기 위해 shop/store 같은 일반어는 빼고 패션 전용 단어만 셈
FASHION = ("clothing", "apparel", "fashion", "ready-to-wear", "ready to wear", "menswear", "womenswear",
           "collection", "lookbook", "footwear", "shoes", "sneaker", "jewelry", "jewellery", "eyewear",
           "sunglasses", "handbag", "bags", "accessories", "knitwear", "denim", "outerwear", "swimwear",
           "lingerie", "garment", "atelier", "runway", "dresses", "trousers", "jackets", "t-shirts", "의류", "컬렉션")
REJECT_HOST_LIST = {x.strip() for x in open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "seed", "official_sites_reject.txt"), encoding="utf-8").read().split("\n") if x.strip()} \
    if os.path.exists(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "seed", "official_sites_reject.txt")) else set()
SUFFIX = ["", "studio", "official", "world", "paris", "nyc", "london", "tokyo", "milano", "design", "store", "online"]
PARKED = ("domain for sale", "buy this domain", "is for sale", "godaddy", "hugedomains", "sedo", "parked",
          "domain name", "dan.com", "afternic", "this domain")
IG_SKIP = {"p", "explore", "accounts", "reel", "stories", "tv", "about", "developer", "legal"}

def slugs(name):
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c)).lower().replace("&", "and")
    words = re.findall(r"[a-z0-9]+", n)
    out = []
    for s in ("".join(words), "-".join(words)):
        if s and s not in out:
            out.append(s)
    return out

def probe(url):
    try:
        r = requests.get(url, headers=UA, timeout=8, allow_redirects=True)
        if r.status_code >= 400 or "text/html" not in r.headers.get("content-type", ""):
            return None
        return r.url, r.text[:300000]
    except Exception:
        return None

def meta(page, key):
    m = re.search(r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]+content=["\']([^"\']*)' % re.escape(key), page, re.I) \
        or re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']%s["\']' % re.escape(key), page, re.I)
    return html.unescape(m.group(1)).strip() if m else ""

def check(name):
    target = norm(name).replace(" ", "")
    if len(target) < 2:
        return None
    hosts_tried = set()
    for s0 in slugs(name):
      for suf in SUFFIX:
        s = s0 + ("-" if "-" in s0 and suf else "") + suf
        for t in (TLDS if not suf else [".com"]):
            for host in (f"https://www.{s}{t}", f"https://{s}{t}"):
                got = probe(host)
                if not got:
                    continue
                final, page = got
                title = html.unescape((re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S) or [None, ""])[1]).strip()
                site = meta(page, "og:site_name")
                head = (title + " " + site).lower()
                low = page.lower()
                if any(p in head or p in low[:5000] for p in PARKED):
                    break
                host_only = re.sub(r"https?://(www\.)?", "", final).split("/")[0]
                if host_only in hosts_tried or host_only in REJECT_HOSTS:
                    break
                hosts_tried.add(host_only)
                # 페이지 제목·사이트명에 브랜드명이 들어 있어야 함 (도메인만 같은 건 불인정)
                ok_name = target in norm(title + " " + site).replace(" ", "")
                ok_fashion = sum(w in low for w in FASHION) >= 3
                if ok_name and ok_fashion:
                    igs = [h for h in re.findall(r"instagram\.com/([A-Za-z0-9_.]{2,30})", page) if h.lower() not in IG_SKIP]
                    desc = meta(page, "og:description") or meta(page, "description")
                    return {"브랜드명": name, "공식 사이트": final.split("?")[0], "인스타그램": ("https://www.instagram.com/" + igs[0]) if igs else "",
                            "사이트 제목": title[:120], "소개": desc[:300]}
                break  # 같은 도메인 www/비www 중복 확인 방지
    return None

REJECT_HOSTS = REJECT_HOST_LIST
SUB_FILE = os.path.join(ROOT, "seed", "sublines.csv")
SUBLINE = {norm(r["서브라인"]) for r in csv.DictReader(open(SUB_FILE, encoding="utf-8-sig"))} if os.path.exists(SUB_FILE) else set()


def main():
    import openpyxl
    wb = openpyxl.load_workbook(os.path.join(ROOT, "data", "brands.xlsx"), read_only=True)
    names = [r[0] for r in wb["보강 필요(이름만 확인)"].iter_rows(min_row=2, values_only=True) if r and r[0]]
    names = [n for n in names if norm(n) not in SUBLINE]
    print(f"대상 {len(names)}개", flush=True)
    with ThreadPoolExecutor(24) as ex:
        res = list(ex.map(check, names))
    found = [r for r in res if r]
    with open(os.path.join(ROOT, "seed", "official_sites.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["브랜드명", "공식 사이트", "인스타그램", "사이트 제목", "소개"])
        w.writeheader(); w.writerows(found)
    missing = [n for n, r in zip(names, res) if not r]
    with open(os.path.join(ROOT, "data", "pending_not_found.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(missing))
    print(f"공식 사이트 확인 {len(found)}개 / 인스타 {sum(bool(r['인스타그램']) for r in found)}개 / 못 찾음 {len(missing)}개")

if __name__ == "__main__":
    main()
