"""
패션 브랜드 아카이브 — 위키데이터 수집 스크립트

GitHub Actions(또는 위키데이터에 접속 가능한 PC)에서 실행:
    pip install requests openpyxl
    python scripts/collect_brands.py

결과물 (data/ 폴더):
    brands.json   사이트에서 바로 쓰는 데이터
    brands.csv    엑셀/구글시트용
    brands.xlsx   검토용 엑셀 (요약 / 전체 / 활동중 / 사라진 브랜드 / 국내 브랜드)
"""

import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests

ENDPOINT = "https://query.wikidata.org/sparql"
UA = "DS3-brand-archive/0.1 (https://github.com/bluesiten; data collection for a fashion brand archive)"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# 1) '브랜드' 자체로 분류된 항목 — 영어 이름으로 클래스를 찾는다 (Q번호 하드코딩 X)
BRAND_CLASS_LABELS = [
    "fashion label", "fashion brand", "clothing brand", "fashion house",
    "luxury brand", "streetwear brand", "sportswear brand", "footwear brand",
    "haute couture house", "fashion designer brand",
]
# 2) 업종(P452)이 패션 계열인 회사
INDUSTRY_LABELS = [
    "fashion industry", "fashion", "clothing industry", "garment industry",
    "apparel industry", "footwear industry", "luxury goods", "fashion design",
]

SOUTH_KOREA = "Q884"
BATCH = 150

PROPS = {
    "P17": "country", "P495": "origin", "P571": "inception", "P576": "dissolved",
    "P749": "parent", "P127": "owner", "P112": "founder", "P856": "website",
    "P154": "logo", "P18": "image", "P2003": "instagram", "P159": "hq",
    "P31": "type", "P452": "industry", "P1448": "official_name",
}
ENTITY_PROPS = {"country", "origin", "parent", "owner", "founder", "hq", "type", "industry"}


# ---------------------------------------------------------------- SPARQL
session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept": "application/sparql-results+json"})


def sparql(query, tries=5):
    for i in range(tries):
        try:
            r = session.post(ENDPOINT, data={"query": query}, timeout=90)
            if r.status_code == 200:
                return r.json()["results"]["bindings"]
            if r.status_code in (429, 500, 502, 503, 504):
                wait = int(r.headers.get("Retry-After", 0)) or 10 * (i + 1)
                print(f"  ! HTTP {r.status_code}, {wait}s 후 재시도", flush=True)
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        except requests.exceptions.RequestException as e:
            print(f"  ! 네트워크 오류 {e}, 재시도", flush=True)
            time.sleep(10 * (i + 1))
    raise RuntimeError("SPARQL 요청 반복 실패")


def qid(uri):
    return uri.rsplit("/", 1)[-1]


def values(ids):
    return " ".join(f"wd:{i}" for i in ids)


def chunks(seq, n):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ---------------------------------------------------------------- 1. 클래스 찾기
def resolve_labels(labels):
    vals = " ".join(f'"{l}"@en' for l in labels)
    q = f"""
    SELECT DISTINCT ?c ?l WHERE {{
      VALUES ?l {{ {vals} }}
      ?c rdfs:label ?l .
      FILTER EXISTS {{ ?any wdt:P31|wdt:P452|wdt:P279 ?c }}
    }}"""
    found = {}
    for b in sparql(q):
        found[qid(b["c"]["value"])] = b["l"]["value"]
    return found


# ---------------------------------------------------------------- 2. 브랜드 ID 모으기
def ids_by_class(cls):
    q = f"""
    SELECT DISTINCT ?x WHERE {{
      ?t wdt:P279* wd:{cls} .
      ?x wdt:P31 ?t .
    }}"""
    return {qid(b["x"]["value"]) for b in sparql(q)}


def ids_by_industry(ind):
    q = f"""
    SELECT DISTINCT ?x WHERE {{
      ?i wdt:P279* wd:{ind} .
      ?x wdt:P452 ?i .
    }}"""
    return {qid(b["x"]["value"]) for b in sparql(q)}


# ---------------------------------------------------------------- 3. 상세 정보
def fetch_props(ids):
    pv = " ".join(f"wdt:{p}" for p in PROPS)
    q = f"""
    SELECT ?x ?p ?v WHERE {{
      VALUES ?x {{ {values(ids)} }}
      VALUES ?p {{ {pv} }}
      ?x ?p ?v .
    }}"""
    return sparql(q)


def fetch_meta(ids):
    q = f"""
    SELECT ?x ?sl ?en ?ko ?enD ?koD ?kow ?enw ?isClass WHERE {{
      VALUES ?x {{ {values(ids)} }}
      BIND(EXISTS {{ ?x wdt:P279 ?sup }} AS ?isClass)
      OPTIONAL {{ ?x wikibase:sitelinks ?sl }}
      OPTIONAL {{ ?x rdfs:label ?en FILTER(lang(?en)="en") }}
      OPTIONAL {{ ?x rdfs:label ?ko FILTER(lang(?ko)="ko") }}
      OPTIONAL {{ ?x schema:description ?enD FILTER(lang(?enD)="en") }}
      OPTIONAL {{ ?x schema:description ?koD FILTER(lang(?koD)="ko") }}
      OPTIONAL {{ ?kow schema:about ?x ; schema:isPartOf <https://ko.wikipedia.org/> }}
      OPTIONAL {{ ?enw schema:about ?x ; schema:isPartOf <https://en.wikipedia.org/> }}
    }}"""
    return sparql(q)


def fetch_labels(ids):
    q = f"""
    SELECT ?x ?en ?ko ?cty WHERE {{
      VALUES ?x {{ {values(ids)} }}
      OPTIONAL {{ ?x rdfs:label ?en FILTER(lang(?en)="en") }}
      OPTIONAL {{ ?x rdfs:label ?ko FILTER(lang(?ko)="ko") }}
      OPTIONAL {{ ?x wdt:P17 ?cty }}
    }}"""
    return sparql(q)


# ---------------------------------------------------------------- 국내 브랜드 보강
KOWIKI_API = "https://ko.wikipedia.org/w/api.php"
SEED_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "seed", "korean_brands.csv")
KO_CAT_SEARCH = ["대한민국의 의류 브랜드", "대한민국의 패션 브랜드", "대한민국의 의류 회사",
                 "대한민국의 패션 회사", "대한민국의 신발 브랜드", "대한민국의 스포츠웨어", "대한민국의 패션"]
KO_CAT_MUST = ("대한민국", "한국")
KO_CAT_ANY = ("의류", "패션", "브랜드", "신발", "스포츠웨어", "아웃도어", "잡화")
KO_CAT_BAN = ("인물", "디자이너", "모델", "잡지", "방송", "음악", "가수", "배우", "식품", "화장품", "자동차", "전자")


def kowiki(params):
    params = {**params, "format": "json", "formatversion": 2}
    for i in range(4):
        try:
            r = session.get(KOWIKI_API, params=params, timeout=60)
            if r.status_code == 200:
                return r.json()
        except requests.exceptions.RequestException:
            pass
        time.sleep(5 * (i + 1))
    return {}


def korean_categories():
    found = set()
    for kw in KO_CAT_SEARCH:
        res = kowiki({"action": "query", "list": "search", "srsearch": kw,
                      "srnamespace": 14, "srlimit": 50})
        for h in res.get("query", {}).get("search", []):
            t = h["title"]
            if any(m in t for m in KO_CAT_MUST) and any(a in t for a in KO_CAT_ANY) \
                    and not any(b in t for b in KO_CAT_BAN):
                found.add(t)
    return sorted(found)


def category_pages(cats, depth=2):
    pages, seen, frontier = set(), set(), list(cats)
    for _ in range(depth + 1):
        nxt = []
        for c in frontier:
            if c in seen:
                continue
            seen.add(c)
            cont = {}
            while True:
                res = kowiki({"action": "query", "list": "categorymembers", "cmtitle": c,
                              "cmlimit": 500, "cmnamespace": "0|14", **cont})
                for m in res.get("query", {}).get("categorymembers", []):
                    if m["ns"] == 0:
                        pages.add(m["title"])
                    elif not any(b in m["title"] for b in KO_CAT_BAN):
                        nxt.append(m["title"])
                if "continue" not in res:
                    break
                cont = res["continue"]
        frontier = nxt
    return pages


def titles_to_qids(titles):
    out = {}
    for part in chunks(sorted(titles), 50):
        res = kowiki({"action": "query", "prop": "pageprops", "ppprop": "wikibase_item",
                      "titles": "|".join(part), "redirects": 1})
        for p in res.get("query", {}).get("pages", []):
            q = p.get("pageprops", {}).get("wikibase_item")
            if q:
                out[p["title"]] = q
        time.sleep(0.3)
    return out


GLOBAL_SEED_FILE = os.path.join(os.path.dirname(SEED_FILE), "global_brands.csv")


def titles_extracts(titles):
    """한국어 위키백과 문서 첫 문장(요약) — 위키데이터 설명이 없는 국내 항목 판별용"""
    out = {}
    for part in chunks(sorted(titles), 20):
        res = kowiki({"action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1,
                      "exsentences": 2, "titles": "|".join(part), "redirects": 1})
        for p in res.get("query", {}).get("pages", []):
            if p.get("extract"):
                out[p["title"]] = p["extract"].strip()[:300]
        time.sleep(0.3)
    return out


def read_seed():
    rows = []
    if os.path.exists(SEED_FILE):
        with open(SEED_FILE, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if (r.get("브랜드명") or "").strip():
                    rows.append({**r, "_korean": True})
    if os.path.exists(GLOBAL_SEED_FILE):
        with open(GLOBAL_SEED_FILE, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                n = (r.get("영문명") or "").strip()
                if n:
                    rows.append({"브랜드명": n, "영문명": n, "카테고리": r.get("카테고리", ""), "_korean": False})
    return rows


def norm(name):
    """브랜드명 비교용: 대소문자·악센트·기호·회사 접미사 제거"""
    import unicodedata
    n = unicodedata.normalize("NFKD", name or "")
    n = "".join(c for c in n if not unicodedata.combining(c)).lower()
    n = n.replace("&", " and ").replace("’", "'")
    n = re.sub(r"\([^)]*\)", " ", n)
    n = re.sub(r"[^0-9a-z가-힣]+", " ", n)
    drop = {"inc", "corporation", "corp", "company", "co", "ltd", "limited", "plc", "group", "holding",
            "holdings", "bv", "b", "v", "spa", "s", "p", "a", "ag", "se", "sa", "llc", "gmbh", "the", "ab", "nv"}
    toks = [t for t in n.split() if t not in drop]
    return " ".join(toks) or n.strip()


def match_seed(seed):
    """시드 브랜드명을 위키데이터 한국어/영어 이름과 정확히 맞춰본다."""
    names = set()
    for r in seed:
        names.add((r["브랜드명"].strip(), "ko"))
        if (r.get("영문명") or "").strip():
            names.add((r["영문명"].strip(), "en"))
    hits = {}
    for part in chunks(sorted(names), 80):
        vals = " ".join('"{}"@{}'.format(n.replace('"', ''), l) for n, l in part)
        q = f"""
        SELECT DISTINCT ?x ?l WHERE {{
          VALUES ?l {{ {vals} }}
          ?x rdfs:label ?l .
          ?x wdt:P31 ?t .
          FILTER NOT EXISTS {{ ?x wdt:P31 wd:Q5 }}
          FILTER NOT EXISTS {{ ?x wdt:P31 wd:Q4167410 }}
        }}"""
        for b in sparql(q):
            hits.setdefault(b["l"]["value"], set()).add(qid(b["x"]["value"]))
        time.sleep(0.5)
    matched, unmatched = {}, []
    for r in seed:
        cand = hits.get(r["브랜드명"].strip(), set()) | hits.get((r.get("영문명") or "").strip(), set())
        if len(cand) == 1:
            matched[next(iter(cand))] = r
        elif cand:
            # 이름이 같은 항목이 여럿이면 한국 관련 후보를 나중에 고르도록 모두 넘김
            for c in cand:
                matched.setdefault(c, {**r, "_ambiguous": True})
        else:
            unmatched.append(r)
    return matched, unmatched


# ---------------------------------------------------------------- 가공
def year_of(v):
    # "1854-01-01T00:00:00Z" → 1854, "-0500-..." 등은 무시
    if not v or v.startswith("-"):
        return None
    try:
        return int(v[:4])
    except ValueError:
        return None


def clean(name):
    # '탑텐 (브랜드)' → '탑텐' : 위키백과식 괄호 구분자 제거
    return re.sub(r"\s*\([^)]*\)$", "", name).strip() or name


def commons_url(v):
    # 위키데이터는 이미지를 Special:FilePath URL로 돌려줌
    return v if v.startswith("http") else "https://commons.wikimedia.org/wiki/Special:FilePath/" + quote(v)


# 유형(P31) 영어 이름에 이런 말이 있으면 '브랜드 아님'으로 제외
EXCLUDE_TYPE_WORDS = [
    "agency", "magazine", "periodical", "newspaper", "publisher", "school", "university",
    "college", "academy", "museum", "award", "prize", "festival", "fashion week", "trade fair",
    "exhibition", "television", "film", "human", "shopping mall", "shopping center",
    "shopping centre", "department store", "market", "academic", "profession", "occupation",
    "association", "trade union", "nonprofit", "non-profit", "foundation", "competition",
    "event", "website", "blog", "photograph", "artwork", "collection", "style", "movement",
    "subculture", "garment", "clothing", "textile fiber", "family name", "given name",
    "sports club", "football club", "team", "song", "album", "musical group", "video game",
    "industry", "discipline", "concept", "activity", "field of study", "art form", "product category",
]
# 이 말이 있으면 위 단어가 있어도 살림 (예: 'clothing brand', 'clothing company')
KEEP_TYPE_WORDS = ["brand", "label", "company", "business", "enterprise", "fashion house",
                   "retail", "manufacturer", "chain", "corporation"]
FASHION_WORDS = ["fashion", "cloth", "apparel", "wear", "brand", "shoe", "footwear", "eyewear",
                 "jewel", "lingerie", "handbag", "leather goods", "luggage", "denim", "jeans",
                 "garment", "sporting goods", "couture", "designer", "watchmaker", "sneaker",
                 "패션", "의류", "브랜드", "옷", "신발", "안경", "아웃도어"]
COMPANY = "기업(운영사)"
# 브랜드가 아니라 브랜드를 운영·유통하는 회사로 보는 이름·설명 (국내 항목에 적용)
KO_COMPANY_NAME = ("홀딩스", "그룹", "컴퍼니", "코퍼레이션", "인터내셔날", "인터내셔널", "엔터프라이즈",
                   "네트웍스", "상사", "통상", "아이앤씨", "브랜즈", "(기업)", "산업", "패션부문", "FnC")
KO_COMPANY_DESC = ("운영 기업", "운영사", "의류 기업", "패션 기업", "패션 회사", "의류 회사", "지주",
                   "복합기업", "컨소시엄", "fashion company", "conglomerate", "holding")
GROUP_WORDS = ["holding", "conglomerate", "chaebol", "business group", "corporate group"]
# 여러 브랜드를 파는 판매처·플랫폼 → 제외 (자체 브랜드 체인인 갭·자라 등은 해당 없음)
PLATFORM_WORDS = ["online shop", "online store", "online retailer", "online platform", "e-commerce",
                  "ecommerce", "marketplace", "open market", "department store", "online fashion",
                  "온라인 상점", "온라인 쇼핑", "쇼핑몰", "오픈 마켓", "백화점", "편집숍", "multi-brand",
                  "outlet", "duty-free", "duty free", "mail order", "off-price", "sporting goods retailer",
                  "shoe retailer", "footwear retailer", "department-store", "중고 거래", "중고거래",
                  "거래 앱", "거래 플랫폼", "모델 에이전시", "secondhand", "second-hand", "resale"]
CATEGORIES = ["럭셔리", "컨템포러리·디자이너", "캐주얼", "SPA·패스트패션", "스트리트", "스포츠", "아웃도어",
              "클래식·테일러링", "데님·워크웨어", "신발", "잡화·주얼리", "언더웨어·스윔", "키즈"]
# 수동 지정이 없는 브랜드는 유형·업종·설명의 단어로 추정 (위에서부터 먼저 걸리는 것)
CATEGORY_RULES = [
    ("언더웨어·스윔", ["lingerie", "underwear", "swimwear", "hosiery", "intimate", "bra ", "속옷", "란제리", "수영복"]),
    ("키즈", ["children's", "childrenswear", "kids", "baby", "infant", "아동", "유아", "키즈"]),
    ("아웃도어", ["outdoor", "mountaineering", "climbing", "hiking", "ski ", "skiwear", "아웃도어", "등산"]),
    ("스포츠", ["sportswear", "sporting goods", "athletic", "sports equipment", "football kit", "cycling",
              "running", "yoga", "activewear", "스포츠"]),
    ("스트리트", ["streetwear", "skate", "surf", "hip hop", "스트리트"]),
    ("데님·워크웨어", ["denim", "jeans", "workwear", "work wear", "청바지"]),
    ("신발", ["footwear", "shoe", "sneaker", "boot", "sandal", "신발", "구두"]),
    ("잡화·주얼리", ["jewel", "watch", "handbag", "leather goods", "eyewear", "sunglasses", "luggage", " hat", "hats",
                 "millinery", "accessor", "bag", "주얼리", "시계", "가방", "안경", "잡화"]),
    ("럭셔리", ["luxury", "haute couture", "couture house", "fashion house", "명품", "럭셔리"]),
    ("클래식·테일러링", ["tailor", "menswear", "shirtmaker", "bespoke", "suits", "남성복", "정장"]),
    ("SPA·패스트패션", ["fast fashion", "fast-fashion", "spa brand", "clothing retailer", "clothing store chain",
                    "패스트 패션", "spa 브랜드"]),
    ("컨템포러리·디자이너", ["designer", "contemporary", "디자이너"]),
]


def classify(type_labels_en, desc=""):
    t = [s.lower() for s in type_labels_en]
    joined = " | ".join(t)
    if t:
        bad = [w for w in EXCLUDE_TYPE_WORDS if w in joined]
        good = any(k in joined for k in KEEP_TYPE_WORDS)
        if bad and not good:
            return None, "유형이 브랜드 아님: " + ", ".join(bad[:3])
    plat = [w for w in PLATFORM_WORDS if w in joined or w in desc]
    if plat:
        return None, "판매처·플랫폼: " + plat[0]
    if any(w in joined for w in ["brand", "label", "fashion house"]):
        return "브랜드", ""
    if any(w in joined for w in GROUP_WORDS):
        return COMPANY, ""
    return "브랜드", ""


def guess_category(text):
    text = " " + text.lower() + " "
    for cat, words in CATEGORY_RULES:
        if any(w.lower() in text for w in words):
            return cat
    return "미분류"


def build(ids, props_rows, meta_rows, label_map, source_map, seed_rows=None,
          ambiguous=frozenset(), forced_korean=frozenset(), curated=None, ko_extract=None):
    seed_rows = seed_rows or {}
    curated = curated or {}
    recs = {i: {"id": i, **{k: [] for k in PROPS.values()}} for i in ids}

    for b in props_rows:
        x = qid(b["x"]["value"])
        key = PROPS.get(qid(b["p"]["value"]))
        if x not in recs or not key:
            continue
        v = b["v"]["value"]
        if key in ENTITY_PROPS:
            v = qid(v)
        if v not in recs[x][key]:
            recs[x][key].append(v)

    for b in meta_rows:
        x = qid(b["x"]["value"])
        if x not in recs:
            continue
        r = recs[x]
        g = lambda k: b.get(k, {}).get("value")
        r["sitelinks"] = int(g("sl") or 0)
        r["is_class"] = r.get("is_class") or g("isClass") == "true"
        for k, src in (("name_en", "en"), ("name_ko", "ko"), ("desc_en", "enD"),
                       ("desc_ko", "koD"), ("kowiki", "kow"), ("enwiki", "enw")):
            r[k] = r.get(k) or g(src)

    def lab(q):
        l = label_map.get(q, {})
        return l.get("ko") or l.get("en") or q

    for q, ex in (ko_extract or {}).items():
        if q in recs and not recs[q].get("desc_ko"):
            recs[q]["desc_ko"] = ex

    kept, dropped = [], []
    for r in recs.values():
        name = r.get("name_ko") or r.get("name_en")
        if not name or name == r["id"]:
            continue  # 이름 없는 항목은 아예 버림
        name = clean(name)
        if r.get("name_ko"):
            r["name_ko"] = clean(r["name_ko"])
        type_en = [label_map.get(t, {}).get("en", "") for t in r["type"]]
        hq_korean = any(SOUTH_KOREA in label_map.get(h, {}).get("cty", ()) for h in r["hq"])
        is_korean = (SOUTH_KOREA in r["country"] or SOUTH_KOREA in r["origin"]
                     or hq_korean or r["id"] in forced_korean)

        ind_en = [label_map.get(t, {}).get("en", "") for t in r["industry"]]
        desc_all = ((r.get("desc_en") or "") + " | " + (r.get("desc_ko") or "")).lower()
        kind, reason = classify(type_en, desc_all)
        brandish = any(w in " ".join(type_en).lower() for w in ("brand", "label", "company", "fashion house"))
        if r.get("is_class") and not brandish:
            kind, reason = None, "개념·분류 항목(브랜드 아님)"
        elif not r["type"]:
            kind, reason = None, "유형 정보 없음"
        if r["id"] in ambiguous:
            # 수동 목록 이름으로 찾은 항목: 다른 경로로도 잡혔거나, 한국 항목이거나, 설명이 패션 관련이어야 인정
            other_src = any(s != "수동 목록" for s in source_map.get(r["id"], ()))
            d = ((r.get("desc_en") or "") + " " + (r.get("desc_ko") or "")).lower()
            fashionish = any(w in d for w in FASHION_WORDS)
            seed_korean = (seed_rows.get(r["id"]) or {}).get("_korean")
            if other_src or fashionish:
                if seed_korean:
                    is_korean = True
            else:
                kind, reason = None, "수동 목록과 이름만 같은 항목(패션 아님)"

        # 카테고리: ① 수동 지정(목록) ② 유형·업종·설명 단어로 추정
        cur = ((seed_rows.get(r["id"]) or {}).get("카테고리") or curated.get(norm(r.get("name_en") or ""))
               or curated.get(clean(r.get("name_ko") or "")) or "").strip()
        category = ""
        if cur == "제외":
            kind, reason = None, "수동 지정 제외(판매처·비패션)"
        elif cur in ("그룹", "기업"):
            if kind or reason.startswith("판매처"):
                kind, reason = COMPANY, ""
            category = ""
        elif cur:
            if reason.startswith("판매처"):
                kind, reason = "브랜드", ""  # 수동 지정이 규칙보다 우선
            category = cur
        if cur not in ("그룹", "기업") and not cur:
            category = guess_category(" | ".join(type_en + ind_en) + " | " + desc_all)
        if is_korean and not cur and kind == "브랜드":
            raw_name = r.get("name_ko") or name
            brand_word = "브랜드" in desc_all or "brand" in desc_all or "brand" in " ".join(type_en).lower()
            if any(w in raw_name for w in KO_COMPANY_NAME) or \
                    (not brand_word and any(w in desc_all for w in KO_COMPANY_DESC)) or \
                    (not brand_word and any(w in " ".join(type_en).lower() for w in ("holding", "conglomerate", "consortium", "public company"))):
                kind = COMPANY
        if kind == COMPANY:
            category = ""

        inc = sorted(y for y in (year_of(v) for v in r["inception"]) if y)
        dis = sorted(y for y in (year_of(v) for v in r["dissolved"]) if y)
        countries = r["country"] or r["origin"]
        search_name = r.get("name_ko") if is_korean and r.get("name_ko") else (r.get("name_en") or name)
        seed = seed_rows.get(r["id"])
        rec = {
            "id": r["id"],
            "name": name,
            "name_en": r.get("name_en") or "",
            "name_ko": r.get("name_ko") or "",
            "kind": kind or "",
            "category": category,
            "category_source": "수동" if cur and cur not in ("제외", "그룹", "기업") else ("" if kind == COMPANY else "추정"),
            "desc": r.get("desc_ko") or r.get("desc_en") or (seed or {}).get("메모", ""),
            "country": [lab(c) for c in countries] or (["대한민국"] if is_korean else []),
            "is_korean": is_korean,
            "founded": inc[0] if inc else None,
            "dissolved": dis[-1] if dis else None,
            "status": "사라짐" if r["dissolved"] else "활동중",
            "parent": [lab(p) for p in (r["parent"] or r["owner"])],
            "founder": [lab(f) for f in r["founder"]],
            "hq": [lab(h) for h in r["hq"]],
            "type": [lab(t) for t in r["type"]],
            "website": r["website"][0] if r["website"] else "",
            "instagram": ("https://www.instagram.com/" + r["instagram"][0]) if r["instagram"] else "",
            "logo": commons_url(r["logo"][0]) if r["logo"] else "",
            "image": commons_url(r["image"][0]) if r["image"] else "",
            "kowiki": r.get("kowiki") or "",
            "enwiki": r.get("enwiki") or "",
            "wikidata": f"https://www.wikidata.org/wiki/{r['id']}",
            "naver_shopping": "https://search.shopping.naver.com/search/all?query=" + quote(search_name),
            "fame": r.get("sitelinks", 0),  # 위키백과 언어판 수 = 인지도 지수
            "found_via": sorted(source_map.get(r["id"], [])),
            "source": "위키데이터",
        }
        if kind:
            kept.append(rec)
        else:
            rec["drop_reason"] = reason
            dropped.append(rec)
    kept.sort(key=lambda d: (-d["fame"], d["name"]))
    dropped.sort(key=lambda d: (-d["fame"], d["name"]))
    return kept, dropped


def manual_record(r, kowiki_url="", curated=None):
    raw = r["브랜드명"].strip()
    name = clean(raw)
    en = (r.get("영문명") or "").strip()
    cat = (r.get("카테고리") or (curated or {}).get(name) or "").strip()
    memo = (r.get("메모") or "").lower()
    brand_word = "브랜드" in memo or "brand" in memo
    is_company = cat in ("그룹", "기업") or (not cat and (any(w in raw for w in KO_COMPANY_NAME) or
                                                       (not brand_word and any(w in memo for w in KO_COMPANY_DESC))))
    if not cat and not is_company and memo:
        g = guess_category(memo)
        cat = "" if g == "미분류" else g
    return {
        "id": "", "name": name, "name_en": en, "name_ko": name,
        "kind": COMPANY if is_company else "브랜드",
        "category": "" if is_company else (cat or "미분류"),
        "category_source": ("수동" if (r.get("카테고리") or (curated or {}).get(name)) else "추정") if cat and not is_company else "",
        "desc": (r.get("메모") or "").strip(), "country": ["대한민국"], "is_korean": True,
        "founded": int(r["설립"]) if (r.get("설립") or "").strip().isdigit() else None,
        "dissolved": int(r["사라진 해"]) if (r.get("사라진 해") or "").strip().isdigit() else None,
        "status": "사라짐" if (r.get("사라진 해") or "").strip() else "활동중",
        "parent": [], "founder": [], "hq": [], "type": [],
        "website": (r.get("공식 사이트") or "").strip(), "instagram": "", "logo": "", "image": "",
        "kowiki": kowiki_url, "enwiki": "", "wikidata": "",
        "naver_shopping": "https://search.shopping.naver.com/search/all?query=" + quote(name),
        "fame": 0, "found_via": ["수동 목록"] if not kowiki_url else ["한국어 위키백과"],
        "source": "수동 목록" if not kowiki_url else "한국어 위키백과",
    }


# ---------------------------------------------------------------- 저장
COLUMNS = [
    ("name", "브랜드명", 26), ("name_en", "영문명", 26), ("kind", "구분", 12),
    ("category", "카테고리", 16), ("category_source", "카테고리 근거", 10),
    ("status", "상태", 8), ("founded", "설립", 7), ("dissolved", "사라진 해", 9),
    ("country", "국가", 14), ("parent", "모회사·소유", 22), ("founder", "창립자", 22),
    ("desc", "설명", 40), ("fame", "인지도 지수", 10), ("website", "공식 사이트", 30),
    ("naver_shopping", "네이버쇼핑", 14), ("instagram", "인스타그램", 14),
    ("kowiki", "한국어 위키백과", 14), ("enwiki", "영어 위키백과", 14),
    ("wikidata", "위키데이터", 14), ("image", "이미지(자유 라이선스)", 14),
    ("source", "출처", 12), ("found_via", "수집 경로", 24), ("id", "ID", 10),
]
DROP_COLUMNS = [("drop_reason", "제외 이유", 30), ("type", "위키데이터 유형", 30)] + COLUMNS
LINK_COLS = {"website", "naver_shopping", "instagram", "kowiki", "enwiki", "wikidata", "image"}


def cell(v):
    if isinstance(v, list):
        return ", ".join(v)
    return "" if v is None else v


def save_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([c[1] for c in COLUMNS])
        for r in rows:
            w.writerow([cell(r[c[0]]) for c in COLUMNS])


def save_xlsx(rows, dropped, path, stats):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()
    ws = wb.active
    ws.title = "요약"
    ws.append(["패션 브랜드 아카이브 — 수집 결과"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([f"수집일: {stats['collected_at']}"])
    ws.append([])
    for k, v in stats["summary"]:
        ws.append([k, v])
    ws.append([])
    ws.append(["※ 인지도 지수 = 그 브랜드 문서가 있는 위키백과 언어판 수 (높을수록 세계적으로 알려짐)"])
    ws.append(["※ '활동중'은 해체일 기록이 없다는 뜻 — 실제로 사라졌는데 기록이 없는 브랜드가 섞여 있을 수 있음"])
    ws.append(["※ '기업(운영사)' 시트: F&F·한섬처럼 브랜드를 운영·유통하는 회사 — 브랜드 목록과 분리"])
    ws.append(["※ '제외됨' 시트: 브랜드가 아니라고 판단해 뺀 항목 — 잘못 빠진 게 있으면 알려주세요"])
    ws.append(["※ 국내 브랜드 추가는 저장소의 seed/korean_brands.csv 에 한 줄씩 적으면 다음 수집 때 반영"])
    ws.append(["※ 이미지는 위키미디어 공용(자유 라이선스) 파일만 연결됨"])
    ws.column_dimensions["A"].width = 40
    ws.column_dimensions["B"].width = 14

    brands = [r for r in rows if r["kind"] == "브랜드"]
    companies = [r for r in rows if r["kind"] == COMPANY]
    sheets = [
        ("전체 브랜드", brands, COLUMNS),
        ("국내 브랜드", [r for r in brands if r["is_korean"]], COLUMNS),
        ("활동중", [r for r in brands if r["status"] == "활동중"], COLUMNS),
        ("사라진 브랜드", sorted([r for r in brands if r["status"] == "사라짐"],
                             key=lambda r: -(r["dissolved"] or 0)), COLUMNS),
        *[(c.replace("·", "_")[:31], [r for r in brands if r["category"] == c], COLUMNS) for c in CATEGORIES],
        ("미분류", [r for r in brands if r["category"] == "미분류"], COLUMNS),
        ("기업(운영사)", companies, COLUMNS),
        ("제외됨", dropped, DROP_COLUMNS),
    ]
    head_fill = PatternFill("solid", fgColor="111111")
    for title, data, cols in sheets:
        s = wb.create_sheet(title)
        s.append([c[1] for c in cols])
        for i, c in enumerate(cols, 1):
            h = s.cell(row=1, column=i)
            h.font = Font(bold=True, color="FFFFFF")
            h.fill = head_fill
            h.alignment = Alignment(vertical="center")
            s.column_dimensions[h.column_letter].width = c[2]
        for r in data:
            s.append([cell(r.get(c[0])) for c in cols])
            row = s.max_row
            for i, c in enumerate(cols, 1):
                if c[0] in LINK_COLS and r.get(c[0]):
                    x = s.cell(row=row, column=i)
                    x.hyperlink = r[c[0]]
                    x.value = "열기"
                    x.font = Font(color="0563C1", underline="single")
        s.freeze_panes = "B2"
        s.auto_filter.ref = s.dimensions
    wb.save(path)


# ---------------------------------------------------------------- 실행
def main():
    os.makedirs(OUT, exist_ok=True)
    print("1) 브랜드 클래스·업종 찾는 중", flush=True)
    classes = resolve_labels(BRAND_CLASS_LABELS)
    industries = resolve_labels(INDUSTRY_LABELS)
    print("   클래스:", classes)
    print("   업종:", industries)
    if not classes and not industries:
        sys.exit("클래스를 하나도 못 찾음 — 쿼리 확인 필요")

    print("2) 브랜드 ID 수집", flush=True)
    source_map = {}
    for c, l in classes.items():
        try:
            got = ids_by_class(c)
        except RuntimeError as e:
            print(f"   - {l}: 실패 {e}")
            continue
        print(f"   - [분류] {l} ({c}): {len(got)}개", flush=True)
        for i in got:
            source_map.setdefault(i, set()).add(l)
        time.sleep(1)
    for c, l in industries.items():
        try:
            got = ids_by_industry(c)
        except RuntimeError as e:
            print(f"   - {l}: 실패 {e}")
            continue
        print(f"   - [업종] {l} ({c}): {len(got)}개", flush=True)
        for i in got:
            source_map.setdefault(i, set()).add("업종:" + l)
        time.sleep(1)

    print("2-1) 국내 브랜드 보강 — 한국어 위키백과 분류", flush=True)
    forced_korean = set()
    kowiki_no_qid = []
    extracts, ko_extract = {}, {}
    try:
        cats = korean_categories()
        print("   찾은 분류:", cats, flush=True)
        titles = category_pages(cats)
        t2q = titles_to_qids(titles)
        print(f"   문서 {len(titles)}개 → 위키데이터 연결 {len(t2q)}개", flush=True)
        for t, q in t2q.items():
            source_map.setdefault(q, set()).add("한국어 위키백과 분류")
            forced_korean.add(q)
        kowiki_no_qid = sorted(set(titles) - set(t2q))
        extracts = titles_extracts(titles)
        print(f"   위키백과 요약 {len(extracts)}개 확보", flush=True)
        for t, q in t2q.items():
            if t in extracts:
                ko_extract[q] = extracts[t]
    except Exception as e:  # 보강 실패해도 본 수집은 계속
        print("   ! 한국어 위키백과 보강 실패:", e)

    print("2-2) 국내 브랜드 보강 — 수동 목록(seed/korean_brands.csv)", flush=True)
    seed = read_seed()
    seed_rows, ambiguous, unmatched = {}, set(), []
    if seed:
        matched, unmatched = match_seed(seed)
        for q, r in matched.items():
            source_map.setdefault(q, set()).add("수동 목록")
            seed_rows[q] = r
            ambiguous.add(q)  # 진짜 그 브랜드인지 build()에서 확인
        print(f"   목록 {len(seed)}개 → 위키데이터 일치 {len(seed) - len(unmatched)}개, 미일치 {len(unmatched)}개")
    curated = {}
    for r in seed:
        c = (r.get("카테고리") or "").strip()
        if c:
            if r["_korean"]:
                curated.setdefault(clean(r["브랜드명"].strip()), c)
            if (r.get("영문명") or "").strip():
                curated.setdefault(norm(r["영문명"]), c)
    ids = sorted(source_map)
    print(f"   합계(중복 제거): {len(ids)}개", flush=True)

    print("3) 상세 정보 수집", flush=True)
    props_rows, meta_rows = [], []
    for n, part in enumerate(chunks(ids, BATCH), 1):
        props_rows += fetch_props(part)
        meta_rows += fetch_meta(part)
        if n % 10 == 0:
            print(f"   {n * BATCH}/{len(ids)}", flush=True)
        time.sleep(0.5)

    print("4) 국가·모회사·창립자·유형 이름 붙이기", flush=True)
    ref = set()
    for b in props_rows:
        key = PROPS.get(qid(b["p"]["value"]))
        if key in ENTITY_PROPS:
            ref.add(qid(b["v"]["value"]))
    label_map = {}
    for part in chunks(sorted(ref), 300):
        for b in fetch_labels(part):
            x = qid(b["x"]["value"])
            d = label_map.setdefault(x, {"cty": set()})
            for lang in ("en", "ko"):
                if lang in b:
                    d[lang] = b[lang]["value"]
            if "cty" in b:
                d["cty"].add(qid(b["cty"]["value"]))
        time.sleep(0.5)

    rows, dropped = build(ids, props_rows, meta_rows, label_map, source_map,
                          seed_rows, frozenset(ambiguous), frozenset(forced_korean), curated, ko_extract)

    # 같은 브랜드가 여러 항목으로 잡힌 경우(예: 샤넬 회사 / 샤넬 향수 브랜드) 인지도 높은 하나만 남김
    best = {}
    for r in rows:  # rows는 인지도 내림차순
        key = norm(r["name_en"]) if r["name_en"] else r["name"]
        if key in best:
            r["drop_reason"] = f"중복({best[key]['name']}와 같은 브랜드)"
            dropped.append(r)
        else:
            best[key] = r
    rows = list(best.values())
    # 수동 목록 이름으로 찾았다가 패션이 아니어서 버린 동명 항목은 너무 많아 시트에서 생략
    dropped = [d for d in dropped if not d["drop_reason"].startswith("수동 목록과 이름만")]

    # 위키데이터에 없는 국내 브랜드: 한국어 위키백과 문서만 있는 것 + 수동 목록 미일치
    have = {r["name_ko"] for r in rows} | {r["name"] for r in rows}
    for t in kowiki_no_qid:
        ex = extracts.get(t, "").lower()
        if clean(t) not in have and curated.get(clean(t)) != "제외" and not any(w in ex for w in PLATFORM_WORDS):
            rows.append(manual_record({"브랜드명": t, "메모": extracts.get(t, "")},
                                      "https://ko.wikipedia.org/wiki/" + quote(t), curated))
            have.add(clean(t))
    seed_unmatched_added = 0
    for r in seed:  # 위키데이터에서 확인 못 한 국내 수동 목록 브랜드는 그대로 추가
        if not r["_korean"] or (r.get("카테고리") or "").strip() == "제외":
            continue
        en = (r.get("영문명") or "").strip()
        if clean(r["브랜드명"].strip()) not in have and not (en and en in {x["name_en"] for x in rows if x["is_korean"]}):
            rows.append(manual_record(r))
            have.add(clean(r["브랜드명"].strip()))
            seed_unmatched_added += 1

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    summary = [
        ("브랜드", sum(r["kind"] == "브랜드" for r in rows)),
        ("  └ 국내 브랜드", sum(r["is_korean"] and r["kind"] == "브랜드" for r in rows)),
        ("기업(운영사) — 별도 시트", sum(r["kind"] == COMPANY for r in rows)),
        ("  └ 수동 목록에서만 추가(위키 정보 없음)", seed_unmatched_added),
        *[(f"카테고리 · {c}", sum(r["category"] == c for r in rows)) for c in CATEGORIES + ["미분류"]],
        ("  └ 카테고리 수동 지정", sum(r.get("category_source") == "수동" for r in rows)),
        ("활동중", sum(r["status"] == "활동중" for r in rows)),
        ("사라진 브랜드", sum(r["status"] == "사라짐" for r in rows)),
        ("제외됨(브랜드 아님)", len(dropped)),
        ("한국어 이름 있음", sum(bool(r["name_ko"]) for r in rows)),
        ("설립연도 있음", sum(bool(r["founded"]) for r in rows)),
        ("공식 사이트 있음", sum(bool(r["website"]) for r in rows)),
        ("이미지 있음", sum(bool(r["image"] or r["logo"]) for r in rows)),
    ]
    stats = {"collected_at": now, "summary": summary,
             "classes": classes, "industries": industries}

    with open(os.path.join(OUT, "brands.json"), "w", encoding="utf-8") as f:
        json.dump({"collected_at": now, "count": len(rows), "brands": rows}, f, ensure_ascii=False)
    with open(os.path.join(OUT, "stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    save_csv(rows, os.path.join(OUT, "brands.csv"))
    save_xlsx(rows, dropped, os.path.join(OUT, "brands.xlsx"), stats)

    print("완료")
    for k, v in summary:
        print(f"   {k}: {v}")


if __name__ == "__main__":
    main()
