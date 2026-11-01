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
    SELECT ?x ?sl ?en ?ko ?enD ?koD ?kow ?enw WHERE {{
      VALUES ?x {{ {values(ids)} }}
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
    SELECT ?x ?en ?ko WHERE {{
      VALUES ?x {{ {values(ids)} }}
      OPTIONAL {{ ?x rdfs:label ?en FILTER(lang(?en)="en") }}
      OPTIONAL {{ ?x rdfs:label ?ko FILTER(lang(?ko)="ko") }}
    }}"""
    return sparql(q)


# ---------------------------------------------------------------- 가공
def year_of(v):
    # "1854-01-01T00:00:00Z" → 1854, "-0500-..." 등은 무시
    if not v or v.startswith("-"):
        return None
    try:
        return int(v[:4])
    except ValueError:
        return None


def commons_url(v):
    # 위키데이터는 이미지를 Special:FilePath URL로 돌려줌
    return v if v.startswith("http") else "https://commons.wikimedia.org/wiki/Special:FilePath/" + quote(v)


def build(ids, props_rows, meta_rows, label_map, source_map):
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
        r["name_en"] = r.get("name_en") or g("en")
        r["name_ko"] = r.get("name_ko") or g("ko")
        r["desc_en"] = r.get("desc_en") or g("enD")
        r["desc_ko"] = r.get("desc_ko") or g("koD")
        r["kowiki"] = r.get("kowiki") or g("kow")
        r["enwiki"] = r.get("enwiki") or g("enw")

    def lab(q):
        l = label_map.get(q, {})
        return l.get("ko") or l.get("en") or q

    out = []
    for r in recs.values():
        name = r.get("name_ko") or r.get("name_en")
        if not name or name == r["id"]:
            continue  # 이름 없는 항목 제외
        inc = sorted(y for y in (year_of(v) for v in r["inception"]) if y)
        dis = sorted(y for y in (year_of(v) for v in r["dissolved"]) if y)
        countries = r["country"] or r["origin"]
        search_name = r.get("name_en") or name
        out.append({
            "id": r["id"],
            "name": name,
            "name_en": r.get("name_en") or "",
            "name_ko": r.get("name_ko") or "",
            "desc": r.get("desc_ko") or r.get("desc_en") or "",
            "country": [lab(c) for c in countries],
            "is_korean": SOUTH_KOREA in r["country"] or SOUTH_KOREA in r["origin"],
            "founded": inc[0] if inc else None,
            "dissolved": dis[-1] if dis else None,
            "status": "사라짐" if (r["dissolved"]) else "활동중",
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
        })
    out.sort(key=lambda d: (-d["fame"], d["name"]))
    return out


# ---------------------------------------------------------------- 저장
COLUMNS = [
    ("name", "브랜드명", 26), ("name_en", "영문명", 26), ("status", "상태", 8),
    ("founded", "설립", 7), ("dissolved", "사라진 해", 9), ("country", "국가", 14),
    ("parent", "모회사·소유", 22), ("founder", "창립자", 22), ("desc", "설명", 40),
    ("fame", "인지도 지수", 10), ("website", "공식 사이트", 30),
    ("naver_shopping", "네이버쇼핑", 14), ("instagram", "인스타그램", 14),
    ("kowiki", "한국어 위키백과", 14), ("enwiki", "영어 위키백과", 14),
    ("wikidata", "위키데이터", 14), ("image", "이미지(자유 라이선스)", 14),
    ("found_via", "수집 경로", 24), ("id", "ID", 10),
]
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


def save_xlsx(rows, path, stats):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()
    ws = wb.active
    ws.title = "요약"
    ws.append(["패션 브랜드 아카이브 — 위키데이터 수집 결과"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([f"수집일: {stats['collected_at']}"])
    ws.append([])
    for k, v in stats["summary"]:
        ws.append([k, v])
    ws.append([])
    ws.append(["※ 인지도 지수 = 그 브랜드 문서가 있는 위키백과 언어판 수 (높을수록 세계적으로 알려짐)"])
    ws.append(["※ '활동중'은 위키데이터에 해체일이 없다는 뜻 — 실제로 사라졌는데 기록이 없는 브랜드가 섞여 있을 수 있음"])
    ws.append(["※ 이미지는 위키미디어 공용(자유 라이선스) 파일만 연결됨"])
    ws.column_dimensions["A"].width = 40
    ws.column_dimensions["B"].width = 14

    sheets = [
        ("전체", rows),
        ("활동중", [r for r in rows if r["status"] == "활동중"]),
        ("사라진 브랜드", sorted([r for r in rows if r["status"] == "사라짐"],
                             key=lambda r: -(r["dissolved"] or 0))),
        ("국내 브랜드", [r for r in rows if r["is_korean"]]),
    ]
    head_fill = PatternFill("solid", fgColor="111111")
    for title, data in sheets:
        s = wb.create_sheet(title)
        s.append([c[1] for c in COLUMNS])
        for i, c in enumerate(COLUMNS, 1):
            h = s.cell(row=1, column=i)
            h.font = Font(bold=True, color="FFFFFF")
            h.fill = head_fill
            h.alignment = Alignment(vertical="center")
            s.column_dimensions[h.column_letter].width = c[2]
        for r in data:
            s.append([cell(r[c[0]]) for c in COLUMNS])
            row = s.max_row
            for i, c in enumerate(COLUMNS, 1):
                if c[0] in LINK_COLS and r[c[0]]:
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

    print("4) 국가·모회사·창립자 이름 붙이기", flush=True)
    ref = set()
    for b in props_rows:
        key = PROPS.get(qid(b["p"]["value"]))
        if key in ENTITY_PROPS:
            ref.add(qid(b["v"]["value"]))
    label_map = {}
    for part in chunks(sorted(ref), 300):
        for b in fetch_labels(part):
            x = qid(b["x"]["value"])
            d = label_map.setdefault(x, {})
            for lang in ("en", "ko"):
                if lang in b:
                    d[lang] = b[lang]["value"]
        time.sleep(0.5)

    rows = build(ids, props_rows, meta_rows, label_map, source_map)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    summary = [
        ("전체 브랜드", len(rows)),
        ("활동중", sum(r["status"] == "활동중" for r in rows)),
        ("사라진 브랜드", sum(r["status"] == "사라짐" for r in rows)),
        ("국내 브랜드", sum(r["is_korean"] for r in rows)),
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
    save_xlsx(rows, os.path.join(OUT, "brands.xlsx"), stats)

    print("완료")
    for k, v in summary:
        print(f"   {k}: {v}")


if __name__ == "__main__":
    main()
