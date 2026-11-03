"""
사이트용 경량 데이터 생성: data/brands.json → data/site.json
브랜드만 추리고, 화면에 쓰는 항목만 짧은 키로 저장한다.
"""
import csv
import json
import os
import re
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def key(n):
    n = unicodedata.normalize("NFKD", n or "")
    n = "".join(c for c in n if not unicodedata.combining(c))
    return re.sub(r"[^0-9a-z가-힣]+", "", unicodedata.normalize("NFC", n).lower())


def load_overrides():
    """seed/overrides.csv — 위키데이터 값이 틀린 브랜드를 사람이 직접 바로잡는 표 (영문명 또는 한글명)"""
    path = os.path.join(ROOT, "seed", "overrides.csv")
    if not os.path.exists(path):
        return {}
    return {key(r["브랜드명"]): r for r in csv.DictReader(open(path, encoding="utf-8-sig")) if r.get("브랜드명")}


def main():
    src = json.load(open(os.path.join(ROOT, "data", "brands.json"), encoding="utf-8"))
    ov = load_overrides()
    vpath = os.path.join(ROOT, "seed", "visuals.json")
    visuals = json.load(open(vpath, encoding="utf-8")) if os.path.exists(vpath) else {}
    out = []
    for b in src["brands"]:
        if b.get("kind") != "브랜드":
            continue
        rec = {
            "i": b.get("id") or "",
            "n": b["name"],
            "e": b.get("name_en") or "",
            "c": b.get("category") or "미분류",
            "k": 1 if b.get("is_korean") else 0,
            "s": 1 if b.get("status") == "사라짐" else 0,
            "f": b.get("founded"),
            "x": b.get("dissolved"),
            "co": ", ".join(b.get("country") or []),
            "p": ", ".join(b.get("parent") or []),
            "fd": ", ".join(b.get("founder") or []),
            "d": (b.get("desc") or "")[:300],
            "w": b.get("website") or "",
            "ig": b.get("instagram") or "",
            "im": b.get("image") or "",      # 위키데이터 대표 사진(건물·매장 등) → 히스토리 섹션용
            "lo": b.get("logo") or "",
            "g": visuals.get(b.get("id") or "n-" + b["name"], []),  # 런웨이·제품 사진(자유 라이선스)
            "kw": b.get("kowiki") or "",
            "ew": b.get("enwiki") or "",
            "sl": b.get("sublines") or [],
            "r": b.get("fame") or 0,
        }
        o = ov.get(key(b.get("name_en"))) or ov.get(key(b["name"]))
        if o:
            if (o.get("설립") or "").strip().isdigit():
                rec["f"] = int(o["설립"])
            if (o.get("사라진 해") or "").strip().isdigit():
                rec["x"], rec["s"] = int(o["사라진 해"]), 1
            if (o.get("설명") or "").strip():
                rec["d"] = o["설명"].strip()
        # 빈 값은 빼서 용량 절약
        out.append({k: v for k, v in rec.items() if v not in ("", None, [], 0) or k in ("n", "c")})
    data = {"t": src.get("collected_at"), "b": out}
    path = os.path.join(ROOT, "data", "site.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"site.json: 브랜드 {len(out)}개, {os.path.getsize(path) / 1024:.0f}KB")


if __name__ == "__main__":
    main()
