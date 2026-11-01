"""
플랫폼 대조 체크리스트 — robots.txt가 허용한 브랜드 목록 페이지에서 '브랜드명만' 읽어
우리 데이터에 없는 브랜드를 찾는다. 결과는 검토용(data/checklist_missing.csv)이며 사이트에 쓰지 않는다.
"""
import csv, html, json, os, re, sys, time, urllib.request, urllib.robotparser
sys.path.insert(0, os.path.dirname(__file__))
from collect_brands import norm, clean

UA = "DS3-brand-archive/0.1 (+https://github.com/bluesiten/brand)"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "ignore")

def allowed(url):
    base = re.match(r"https://[^/]+", url).group(0)
    rp = urllib.robotparser.RobotFileParser(base + "/robots.txt"); rp.read()
    return rp.can_fetch(UA, url)

def ssense():
    names = {}
    for g in ("women", "men"):
        url = f"https://www.ssense.com/en-us/{g}/designers"
        if not allowed(url):
            print("  robots 불허:", url); continue
        page = get(url)
        for href, text in re.findall(r'href="(/en-us/(?:women|men)/designers/[a-z0-9\-]+)"[^>]*>\s*<span[^>]*>([^<]{1,80})</span>', page):
            t = html.unescape(text).strip()
            if t and not t.lower().startswith(("view", "shop")):
                names[t] = "https://www.ssense.com" + href
        print(f"  ssense {g}: 누적 {len(names)}개", flush=True)
        time.sleep(3)
    return names

def peek_29cm():
    txt = get("https://www.29cm.co.kr/robots.txt")
    maps = re.findall(r"(?im)^sitemap:\s*(\S+)", txt)
    print("  29cm sitemap:", maps)
    for m in maps[:2]:
        try:
            body = get(m)
            locs = re.findall(r"<loc>([^<]+)</loc>", body)
            print("   ", m, "→", len(locs), "개:", [l for l in locs if "item" not in l][:20])
        except Exception as e:
            print("   ", m, "실패", e)

def main():
    ours = json.load(open(os.path.join(ROOT, "data", "brands.json"), encoding="utf-8"))["brands"]
    have = set()
    for b in ours:
        for n in (b["name"], b["name_en"], b["name_ko"]):
            if n:
                have.add(norm(n))
    print("1) SSENSE 디자이너 목록")
    ss = ssense()
    rows = []
    for n, url in sorted(ss.items(), key=lambda x: x[0].lower()):
        rows.append({"플랫폼": "SSENSE", "브랜드명": n, "우리 데이터": "있음" if norm(n) in have else "없음", "링크": url})
    miss = [r for r in rows if r["우리 데이터"] == "없음"]
    with open(os.path.join(ROOT, "data", "checklist_missing.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["플랫폼", "브랜드명", "우리 데이터", "링크"])
        w.writeheader(); w.writerows(miss + [r for r in rows if r["우리 데이터"] == "있음"])
    print(f"완료: SSENSE {len(rows)}개 중 우리 데이터에 없는 브랜드 {len(miss)}개")

if __name__ == "__main__":
    main()
