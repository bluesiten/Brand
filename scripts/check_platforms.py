"""플랫폼 브랜드 목록 페이지가 robots.txt상 허용되는지 확인 (수집은 하지 않음)"""
import urllib.robotparser, urllib.request, json, re
UA = "DS3-brand-archive/0.1 (+https://github.com/bluesiten/brand)"
TARGETS = {
    "musinsa": ["https://www.musinsa.com/brand", "https://www.musinsa.com/brands", "https://www.musinsa.com/app/brand/list"],
    "29cm": ["https://www.29cm.co.kr/store/brand-list", "https://www.29cm.co.kr/brand"],
    "wconcept": ["https://display.wconcept.co.kr/brand", "https://www.wconcept.co.kr/Brand"],
    "farfetch": ["https://www.farfetch.com/designers/women", "https://www.farfetch.com/designers/men"],
    "yoox": ["https://www.yoox.com/us/women/designers", "https://www.yoox.com/us/men/designers"],
    "ssense": ["https://www.ssense.com/en-us/women/designers", "https://www.ssense.com/en-us/men/designers"],
    "mytheresa": ["https://www.mytheresa.com/us/en/women/designers"],
}
def get(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read(400000).decode("utf-8", "ignore")
    except Exception as e:
        return getattr(e, "code", "ERR"), str(e)[:120]
for site, urls in TARGETS.items():
    base = re.match(r"https://[^/]+", urls[0]).group(0)
    code, txt = get(base + "/robots.txt")
    print(f"\n===== {site}  robots.txt HTTP {code}")
    rp = urllib.robotparser.RobotFileParser(); rp.parse(txt.splitlines() if code == 200 else [])
    # 전체 봇(*) 규칙 중 관련 줄만
    lines = [l for l in txt.splitlines() if re.search(r"(?i)^(user-agent|disallow|allow|crawl-delay)", l.strip())] if code == 200 else []
    print("  rules(head):", " | ".join(lines[:25]))
    for u in urls:
        ok = rp.can_fetch(UA, u) if code == 200 else None
        c, body = get(u) if ok else ("skip", "")
        n = len(re.findall(r"(?i)brand|designer", body)) if isinstance(body, str) else 0
        print(f"  {u}\n    robots허용={ok}  페이지HTTP={c}  크기={len(body) if isinstance(body,str) else 0}  brand단어수={n}")
