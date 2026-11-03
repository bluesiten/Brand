"""
수집된 비주얼(seed/visuals.json)을 엄격한 기준으로 다시 거른다.
- 'runway'는 공항 활주로일 수 있어 패션 문맥(쇼·모델·시즌·컬렉션)이 같이 있을 때만 인정
- 항공기 등록부호(D-EDUN 등)·비행기·공항·그림 등 무관 사진 제외
- 일반 단어 같은 짧은 브랜드명(Express, Pink, Quick…)은 런웨이 근거가 확실한 사진만
"""
import json, os, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FASHION_CTX = re.compile(r"(fashion|catwalk|défilé|defile|sfilata|backstage|lookbook|couture|ready.to.wear|prêt|"
                         r"\b(ss|fw|aw|pf)\s?\d{2}\b|\b(19|20)\d{2}\s?(ss|fw|aw)\b|\b\d{2}(ss|fw|aw)\b|"
                         r"\b(spring|summer|fall|autumn|winter|resort|cruise|pre-fall)[\s\-/–]+(summer|winter|\d{2,4})|"
                         r"\bcollection\b|\bmodel(s|ing)?\b|show\b|week\b|menswear|womenswear|campaign)", re.I)
NOISE = re.compile(r"(library|temple|huile|ferrari|theatre|theater|campaign button|gerwing|\b[A-Z]{1,2}-[A-Z]{3,4}\b|\bpiper\b|cessna|airbus|boeing|aircraft|airport|taxiing|\braf\b|"
                   r"geograph|cloud|sunset|painting|fairy tale|postcard|islamic art|outlet|\bclub\b.*fashion show|"
                   r"repair|airfield|airstrip|aviation|helicopter|truck|bus\b|train\b|station|church|castle)", re.I)
PRODUCT = re.compile(r"\b(dress|gown|shoes?|sneakers?|boots?|bag|handbag|purse|coat|jacket|jeans|denim|scarf|watch|"
                     r"necklace|bracelet|ring|sunglasses|eyewear|trainers?|sandals?|heels|suit|blouse|shirt|knit|"
                     r"sweater|hat|cap|ensemble|outfit)\b", re.I)


def ok(photo, brand_name):
    t = photo.get("t", "")
    if NOISE.search(t):
        return False
    if photo.get("k") == "r":
        return bool(FASHION_CTX.search(t))
    return bool(PRODUCT.search(t))


def weak_name(n):
    """한 단어 & 6자 이하 영문명은 일반 단어와 겹치기 쉬움"""
    return bool(n) and " " not in n.strip() and len(re.sub(r"[^A-Za-z]", "", n)) <= 6


def main():
    p = os.path.join(ROOT, "seed", "visuals.json")
    site = {b.get("i") or "n-" + b["n"]: b for b in json.load(open(os.path.join(ROOT, "data", "site.json"), encoding="utf-8"))["b"]}
    vis = json.load(open(p, encoding="utf-8"))
    rb = os.path.join(ROOT, "seed", "visuals_reject_brands.txt")
    reject_brands = {l.strip() for l in open(rb, encoding="utf-8") if l.strip()} if os.path.exists(rb) else set()
    out, before, after = {}, 0, 0
    for k, photos in vis.items():
        b = site.get(k, {})
        name = b.get("e") or b.get("n", "")
        before += len(photos)
        if b.get("n") in reject_brands or b.get("e") in reject_brands:
            continue  # 이름이 일반 단어·지명과 겹쳐 사진이 맞지 않는 브랜드 (사람이 확인해 지정)
        keep = [x for x in photos if ok(x, name)]
        if weak_name(name):
            # 짧은 이름: 제목에 브랜드명이 대문자 그대로 있고 패션 문맥까지 있어야 인정
            keep = [x for x in keep if FASHION_CTX.search(x["t"]) and re.search(r"(?<![A-Za-z])" + re.escape(name.strip()) + r"(?![A-Za-z])", x["t"])]
        if keep:
            out[k] = keep
            after += len(keep)
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"정리: 브랜드 {len(vis)}→{len(out)}, 사진 {before}→{after}, 런웨이 보유 {sum(1 for v in out.values() if any(x['k']=='r' for x in v))}")


if __name__ == "__main__":
    main()
