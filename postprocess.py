import json
import math
import os
import re
import sys
from datetime import date

FIELDS = ["Last_name_ru", "First_name_ru", "Middle_name_ru", "Sex_ru", "Birth_date", "Birth_place_ru",
          "Issue_date", "Issue_organisation_code", "Licence_number", "Issue_organization_ru"]
NAMES = FIELDS[:3]
CHECK_DATE = date(2026, 10, 1)
MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model.json")
BINS = (0.5, 0.9, 0.95, 0.98, 0.99, 0.995, 0.998, 0.999, 0.9995)
DOUBT = {"web": 0.9, "mobile": 0.9, "mobile_v2": 0.99}
TOKEN = re.compile(r"[А-ЯЁA-Z0-9]+")
CYR = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"
RU2MRZ = dict(zip(CYR, "ABVGDE2JZIQKLMNOPRSTUFHC34WXY9678"))
MRZ2RU = {v: k for k, v in RU2MRZ.items()}
DIGIT_FIX = str.maketrans("ODQUILZASGTB", "000011245678")
SEX = {"M": "МУЖ.", "F": "ЖЕН."}


def check_digit(s):
    return str(sum((0 if c == "<" else int(c, 36)) * (7, 3, 1)[i % 3] for i, c in enumerate(s)) % 10)


def digits(s):
    s = s.translate(DIGIT_FIX)
    return s if s.isdigit() else None


def parse_line2(t):
    r = {}
    if len(t) >= 21:
        num, c1, bd, c2 = digits(t[0:9]), digits(t[9]), digits(t[13:19]), digits(t[19])
        if num and c1 == check_digit(num):
            r["num"] = num
        if bd and c2 == check_digit(bd):
            r["bd"] = bd
        if t[20] in "MF" and r:
            r["sex"] = t[20]
    if len(t) >= 16:
        opt, c3 = digits(t[-16:-3]), digits(t[-2])
        if opt and c3 == check_digit(opt + "<"):
            r["s4"], r["idt"], r["code"] = opt[0], opt[1:7], opt[7:]
    if {"num", "bd", "s4"} <= r.keys():
        opt = r["s4"] + r["idt"] + r["code"] + "<"
        line = r["num"] + check_digit(r["num"]) + "RUS" + r["bd"] + check_digit(r["bd"]) + "M<<<<<<<" + opt + check_digit(opt)
        r["trusted"] = digits(t[-1]) == check_digit(line[0:10] + line[13:20] + line[21:43])
    return r


def align(a, b):
    d = [[i + j if not i * j else 0 for j in range(len(b) + 1)] for i in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
    ops, i, j = [], len(a), len(b)
    while i or j:
        if i and j and d[i][j] == d[i - 1][j - 1] + (a[i - 1] != b[j - 1]):
            i, j = i - 1, j - 1
            ops.append(("equal" if a[i] == b[j] else "sub", i, j))
        elif i and d[i][j] == d[i - 1][j] + 1:
            i -= 1
            ops.append(("del", i, j))
        else:
            j -= 1
            ops.append(("ins", i, j))
    return ops[::-1]


def resolve_names(names, line1):
    exp, conf, owner = "", [], []
    for k, (t, c) in enumerate(names):
        if k == 1 or (k == 2 and t):
            sep = "<<" if k == 1 else "<"
            exp, conf, owner = exp + sep, conf + [1.0] * len(sep), owner + [None] * len(sep)
        exp += "".join(RU2MRZ.get(ch, "<") for ch in t)
        conf += c
        owner += [(k, i) for i in range(len(t))]
    text = line1["text"]
    start = min(range(3, 7), key=lambda k: (sum(op != "equal" for op, _, _ in align(text[:k], "PNRUS")), abs(k - 5)))
    zone = text[start:].split("<<<")[0][:39]
    zconf = line1["char_conf"][start:]
    out = [list(t) for t, _ in names]
    for op, i, j in align(exp[:39], zone):
        if op == "equal":
            continue
        if op != "sub" or owner[i] is None or zone[j] not in MRZ2RU or abs(zconf[j] - conf[i]) <= 0.1:
            return None, []
        if zconf[j] > conf[i]:
            k, pos = owner[i]
            out[k][pos] = MRZ2RU[zone[j]]
    return ["".join(x) for x in out], [o for o in owner[39:] if o]


def viz_text(doc, f):
    v = doc["viz"].get(f)
    if v is None:
        return None, None
    text, conf = [], []
    for i, line in enumerate(v["lines"]):
        if i:
            text.append(" ")
            conf.append(min(line["char_conf"][:1] + conf[-1:] or [1.0]))
        text.append(line["text"])
        conf += line["char_conf"]
    return "".join(text), conf


def lines_complete(doc, f):
    v = doc["viz"].get(f)
    if not v or not v["lines"]:
        return False
    return round(v["box"][3] * len(v["lines"]) / sum(line["box"][3] for line in v["lines"])) == len(v["lines"])


def parse_date(s):
    m = re.fullmatch(r"(\d\d)\.(\d\d)\.(\d{4})", s or "")
    try:
        return date(int(m[3]), int(m[2]), int(m[1])) if m else None
    except ValueError:
        return None


def norm_digits(text, n, fmt):
    d = re.sub(r"\D", "", text or "")
    return fmt(d) if len(d) == n else None


def norm_date(text):
    s = norm_digits(text, 8, lambda d: f"{d[:2]}.{d[2:4]}.{d[4:]}")
    return s if parse_date(s) else None


def mrz_date(yymmdd, issue_year=None):
    y, m, d = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:])
    if issue_year is None:
        y += 1900 if y >= 97 else 2000
    else:
        y += 2000 if 2000 + y <= issue_year - 14 else 1900
    try:
        v = date(y, m, d)
    except ValueError:
        return None
    if issue_year is None and not date(1997, 10, 1) <= v <= CHECK_DATE:
        return None
    return f"{d:02d}.{m:02d}.{y}"


def p(table, c, src):
    return table[src][sum(c >= b for b in BINS)]


def candidates(w, c, vocab):
    cand = {}
    for i in range(len(w) + 1):
        options = [(w[:i] + L + w[i:], min(c[max(i - 1, 0):i + 1])) for L in CYR]
        if i < len(w):
            options += [(w[:i] + L + w[i + 1:], c[i]) for L in CYR if L != w[i]]
            options.append((w[:i] + w[i + 1:], c[i] if 0 < i < len(w) - 1 else 1.0))
            if i and w[:i] in vocab and w[i:] in vocab:
                options.append((w[:i] + "." + w[i:], c[i]))
        for a, ec in options:
            if a in vocab or "." in a:
                cand.setdefault(a, ec)
    return cand


def correct(text, conf, src, m):
    vocab, low = m["vocab"], DOUBT[src]
    out, oc, fixed, near, last = [], [], [], 0, 0
    for t in TOKEN.finditer(text):
        w, c = t.group(), conf[t.start():t.end()]
        out.append(text[last:t.start()])
        oc += conf[last:t.start()]
        last = t.end()
        cand = {} if w in vocab or w.isdigit() else candidates(w, c, vocab)
        alt = None
        if len(cand) == 1:
            (a, ec), = cand.items()
            if "." not in a and ec < low and vocab[a] >= 5 and sum(x < low for x in c) <= 1:
                alt = a
        near += bool(cand) and alt is None
        if alt:
            start = sum(map(len, out))
            fixed.append((start, start + len(alt)))
            out.append(alt)
            oc += ([c[0]] + [1.0] * (len(alt) - 2) + [c[-1]])[:len(alt)]
        else:
            out.append(w)
            oc += c
    out.append(text[last:])
    oc += conf[last:]
    return "".join(out), oc, fixed, near


def features(text, conf, src, m, fixed=(), near=0, use_vocab=True):
    kind = ["other"] * len(text)
    for t in TOKEN.finditer(text):
        iv = use_vocab and (t.group() in m["vocab"] or t.group().isdigit())
        kind[t.start():t.end()] = ["inv" if iv else "oov"] * (t.end() - t.start())
    for a, b in fixed:
        kind[a:b] = ["fixed"] * (b - a)
    s = {"oov": 0.0, "inv": 0.0, "other": 0.0}
    max_sub = max_miss = 0.0
    for c, k in zip(conf, kind):
        max_miss = max(max_miss, p(m["miss"], c, src))
        if k != "fixed":
            s[k] += p(m["sub"], c, src)
            max_sub = max(max_sub, p(m["sub"], c, src))
    v2 = int(src == "mobile_v2")
    return [math.log(x + 1e-4) for x in (s["oov"], s["inv"], s["other"], max_sub, max_miss)] + [near, near * v2, v2]


def field_risk(m, kind, x):
    lr = m.get("lr", {}).get(kind)
    if lr is None:
        return 0.5
    return 1 / (1 + math.exp(-lr["b"] - sum(w * v for w, v in zip(lr["w"], x))))


def extract(doc, m):
    src = doc["source"]
    vt = {f: viz_text(doc, f) for f in FIELDS}
    complete = {f: lines_complete(doc, f) for f in FIELDS}
    mrz = (doc.get("mrz") or {}).get("lines") or []
    l2 = parse_line2(mrz[1]["text"]) if len(mrz) > 1 else {}
    val, risk, feats = {}, {}, {}

    def from_viz(f, kind, value, **kw):
        t, c = vt[f]
        val[f], risk[f] = (value if value is not None else t), None
        if t is not None and value is not None and complete[f]:
            feats[f] = (kind, features(t, c, src, m, **kw))
            risk[f] = field_risk(m, *feats[f])

    def from_mrz(f, mrz_value, viz_value):
        if mrz_value and (l2.get("trusted") or mrz_value == viz_value):
            val[f], risk[f] = mrz_value, 0.0
        else:
            from_viz(f, "num", viz_value)

    from_mrz("Issue_date", "idt" in l2 and mrz_date(l2["idt"]), norm_date(vt["Issue_date"][0]))
    issue = parse_date(val["Issue_date"])
    from_mrz("Birth_date", "bd" in l2 and mrz_date(l2["bd"], issue.year if issue else CHECK_DATE.year),
             norm_date(vt["Birth_date"][0]))
    code = norm_digits(vt["Issue_organisation_code"][0], 6, lambda d: f"{d[:3]}-{d[3:]}")
    from_mrz("Issue_organisation_code", "code" in l2 and f"{l2['code'][:3]}-{l2['code'][3:]}",
             code if code in m["codes"] else None)
    fmt_num = lambda d: f"{d[:2]} {d[2:4]} {d[4:]}"
    from_mrz("Licence_number", "num" in l2 and "s4" in l2 and fmt_num(l2["num"][:3] + l2["s4"] + l2["num"][3:]),
             norm_digits(vt["Licence_number"][0], 10, fmt_num))

    names = [vt[f] if vt[f][0] is not None else ("", []) for f in NAMES]
    if mrz and vt[NAMES[0]][0] is not None and vt[NAMES[1]][0] is not None:
        resolved, tail = resolve_names(names, mrz[0])
        for k, f in enumerate(NAMES):
            val[f] = resolved[k] if resolved else names[k][0]
            ok = resolved and (complete[f] or (k == 2 and vt[f][0] is None))
            risk[f] = sum(p(m["sub"], names[k][1][i], src) for kk, i in tail if kk == k) if ok else None
    else:
        for f in NAMES:
            from_viz(f, "name", vt[f][0], use_vocab=False)

    t = vt["Sex_ru"][0]
    mid = val["Middle_name_ru"] or ""
    viz_sex = t if t in SEX.values() else None
    pat_sex = SEX["M"] if re.search(r"(ВИЧ|ОГЛЫ)$", mid) else SEX["F"] if re.search(r"(ВНА|КЫЗЫ)$", mid) else None
    votes = [s for s in (viz_sex, SEX.get(l2.get("sex")), pat_sex) if s]
    if len(set(votes)) == 1 and len(votes) > 1:
        val["Sex_ru"], risk["Sex_ru"] = votes[0], 0.0
    elif votes == [viz_sex]:
        from_viz("Sex_ru", "num", viz_sex)
    else:
        val["Sex_ru"], risk["Sex_ru"] = (votes[0] if votes else t), (0.01 if len(votes) == 1 else None)

    for f, kind in (("Birth_place_ru", "bp"), ("Issue_organization_ru", "org")):
        t, c = vt[f]
        val[f], risk[f] = t, None
        if t is None or not complete[f]:
            continue
        val[f], c2, fixed, near = correct(t, c, src, m)
        if near and src == "mobile_v2":
            continue
        feats[f] = (kind, features(val[f], c2, src, m, fixed, near))
        risk[f] = field_risk(m, *feats[f])
    return val, risk, feats


def passport_valid(birth, issue):
    b, i = parse_date(birth), parse_date(issue)
    if not b or not i or i > CHECK_DATE:
        return False

    def bday(n):
        try:
            return b.replace(year=b.year + n)
        except ValueError:
            return date(b.year + n, 3, 1)

    for start, end in ((14, 20), (20, 45)):
        if bday(start) <= i < bday(end):
            return CHECK_DATE < bday(end)
    return i >= bday(45)


def doc_risk(risk):
    if any(risk.get(f) is None for f in FIELDS):
        return None
    return 1 - math.prod(1 - risk[f] for f in FIELDS)


def decide(doc, m):
    val, risk, _ = extract(doc, m)
    total = doc_risk(risk)
    accept = total is not None and total <= m["threshold"] and passport_valid(val["Birth_date"], val["Issue_date"])
    return accept, val


def load_model(path=MODEL):
    with open(path, encoding="utf-8") as fh:
        m = json.load(fh)
    m["codes"] = set(m["codes"])
    return m


def main(pred_path, out_path):
    m = load_model()
    n = n_acc = 0
    with open(pred_path, encoding="utf-8") as src, open(out_path, "w", encoding="utf-8") as out:
        for line in src:
            doc = json.loads(line)
            accept, val = decide(doc, m)
            row = {"doc_id": doc["doc_id"], "decision": "accept" if accept else "reject"}
            if accept:
                row["fields"] = {f: val[f] for f in FIELDS}
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            n, n_acc = n + 1, n_acc + accept
    print(f"документов: {n}, принято: {n_acc}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python postprocess.py data/test_predictions.jsonl answers.jsonl")
    main(sys.argv[1], sys.argv[2])
