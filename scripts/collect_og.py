"""
브랜드 공식 사이트의 대표 이미지(og:image · twitter:image)를 모은다.
이미지는 저장하지 않고 원본 주소만 기록해 사이트에서 출처(공식 사이트)와 함께 보여준다.
결과: seed/og_images.json  {브랜드키: {"u": 이미지 주소, "s": 사이트 주소}}
"""
import html, json, os, re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36 DS3-brand-archive"}
SKIP = re.compile(r"(logo|favicon|icon|sprite|placeholder|default|blank|share[-_]?default|apple-touch|avatar|"
                  r"badge|spinner|loader|1x1|pixel|og[-_]?default|no[-_]?image|\.svg|\.gif)", re.I)


def meta(page, key):
    for pat in (r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]*content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']%s["\']'):
        m = re.search(pat % re.escape(key), page, re.I)
        if m:
            return html.unescape(m.group(1)).strip()
    return ""


def fetch(b):
    site = b.get("w")
    if not site:
        return None
    try:
        r = requests.get(site, headers=UA, timeout=12, allow_redirects=True)
        if r.status_code >= 400:
            return None
        page = r.text[:400000]
        img = meta(page, "og:image") or meta(page, "og:image:secure_url") or meta(page, "twitter:image")
        if not img:
            return None
        img = urljoin(r.url, img)
        if not img.startswith("http") or SKIP.search(img):
            return None
        # 실제 이미지인지, 너무 작지 않은지 확인
        h = requests.get(img, headers={**UA, "Referer": r.url}, timeout=12, stream=True)
        ctype = h.headers.get("content-type", "")
        size = int(h.headers.get("content-length") or 0)
        h.close()
        if h.status_code >= 400 or not ctype.startswith("image/") or "svg" in ctype:
            return None
        if size and size < 15000:   # 15KB 미만은 대개 로고·아이콘
            return None
        w = meta(page, "og:image:width")
        if w.isdigit() and int(w) < 400:
            return None
        return {"u": img.replace("http://", "https://", 1), "s": r.url.split("?")[0]}
    except Exception:
        return None


def main():
    site = json.load(open(os.path.join(ROOT, "data", "site.json"), encoding="utf-8"))["b"]
    targets = [b for b in site if b.get("w")]
    print(f"공식 사이트 보유 {len(targets)}개", flush=True)
    with ThreadPoolExecutor(32) as ex:
        res = list(ex.map(fetch, targets))
    out = {(b.get("i") or "n-" + b["n"]): r for b, r in zip(targets, res) if r}
    # 같은 이미지를 여러 브랜드가 쓰면(그룹 공용 배너 등) 제외
    from collections import Counter
    dup = {u for u, c in Counter(v["u"] for v in out.values()).items() if c > 1}
    out = {k: v for k, v in out.items() if v["u"] not in dup}
    json.dump(out, open(os.path.join(ROOT, "seed", "og_images.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print(f"완료: 대표 이미지 확보 {len(out)}개 (중복 제외 {len(dup)}종)")


if __name__ == "__main__":
    main()
