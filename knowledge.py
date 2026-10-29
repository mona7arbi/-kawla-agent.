"""KAWLA's knowledge base: the public PDF about the Complex's libraries + the (synthetic) service policy.

Pipeline: PDF → text (raw, or cleaned) → chunks with page IDs → BM25 keyword search with Arabic normalisation.
The only difference between the two index modes is ONE step: `clean_pdf_text()` (configuration A vs B):

    raw    the text exactly as pypdf extracts it (this PDF puts "ً" where spaces should be, so words stick together)
    clean  the same text after clean_pdf_text(), the fix found while speeding up the KAWLA chatbot (v2)

Used by mcp_server.py (search_knowledge over MCP). Standard library + pypdf only.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
PDF = DATA / "docs" / "kawla_libraries.pdf"
POLICY = DATA / "service_policy.md"
DOC_TITLE = "التعريف بمكتبات مجمع الملك عبدالعزيز للمكتبات الوقفية"

# ---------------------------------------------------------------- text cleaning (from KAWLA v2 textclean.py)
TASHKEEL = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")
TATWEEL = "ـ"


def clean_pdf_text(text: str) -> str:
    """This PDF (exported from Word) yields "ً" (U+064B) instead of every space: «مجمعًالملكًعبدًالعزيز»."""
    if not text:
        return ""
    t = text.replace("ً", " ")
    t = re.sub(r"[ \t ‎‏‪-‮]+", " ", t)
    t = re.sub(r"(?<=[ء-ي]) ا(?=[\s،.,:؛)]|$)", "ا", t)  # «انطلاق ا» → «انطلاقا»
    t = re.sub(r"ّ (?=[ء-ي])", "ّ", t)  # «المختصّ ين» → «المختصّين»
    t = t.replace(TATWEEL, "")
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


# ---------------------------------------------------------------- Arabic normalisation for search
AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
PREFIXES = ("وبال", "وال", "بال", "فال", "كال", "لل", "ال")
STOP = set("عدد في من على الى إلى عن مع هذه هذا التي الذي ما ماذا كم هل او أو ثم و ان أن كان كانت قد لم لا هو هي".split())


def normalize(text: str) -> str:
    t = TASHKEEL.sub("", text or "").replace(TATWEEL, "").translate(AR_DIGITS)
    t = re.sub("[إأآٱ]", "ا", t).replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    return t.lower()


def tokens(text: str) -> list[str]:
    out = []
    for w in re.findall(r"\w+", normalize(text)):
        if w in STOP:
            continue
        for p in PREFIXES:
            if w.startswith(p) and len(w) - len(p) >= 3:
                w = w[len(p):]
                break
        if len(w) > 4 and w.endswith(("ات", "ين", "ون")):
            w = w[:-2]
        elif len(w) > 4 and w.endswith("ا"):  # tanween alef: مخطوطا → مخطوط، كتابا → كتاب
            w = w[:-1]
        out.append(w)
    return out


# ---------------------------------------------------------------- chunks
HEADING = re.compile(r"^(?:\S+ا?):\s*مكتبة .+$|^(?:أولا|ثانيا|ثالثا|رابعا|خامسا|سادسا|سابعا|ثامنا|تاسعا|عاشرا)ا?:\s*(.+)$", re.M)


def pdf_pages(mode: str) -> list[tuple[int, str]]:
    from pypdf import PdfReader

    pages = []
    for i, page in enumerate(PdfReader(PDF).pages, start=1):
        raw = page.extract_text() or ""
        text = clean_pdf_text(raw) if mode == "clean" else raw  # ← the ONE difference between A and B
        if len(text.strip()) >= 60:
            pages.append((i, text))
    return pages


def split(text: str, size: int = 800, overlap: int = 200) -> list[str]:
    """Split on line boundaries into ~size-character chunks, repeating ~overlap characters between neighbours."""
    lines, chunks, cur = text.splitlines(), [], ""
    for line in lines:
        if cur and len(cur) + len(line) > size:
            chunks.append(cur.strip())
            cur = cur[-overlap:]
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


def build(mode: str) -> list[dict]:
    """All chunks for one index mode. Each chunk carries an ID to cite, a source and the library it is about."""
    out, library = [], ""
    for page, text in pdf_pages(mode):
        for j, chunk in enumerate(split(text), start=1):
            m = re.search(r"(?:أولا|ثانيا|ثالثا|رابعا|خامسا|سادسا|سابعا|ثامنا|تاسعا|عاشرا)ا?\s*:\s*(مكتبة[^\n]+)", chunk)
            about = library
            if m:  # a new library starts inside this chunk: the chunk is about the end of one and the start of the next
                library = m.group(1).strip()
                about = f"{about}؛ {library}" if about else library
            out.append({"id": f"DOC-p{page}-{j}", "source": f"{DOC_TITLE}، صفحة {page}",
                        "about": about, "text": chunk})
    for sec in re.split(r"\n(?=## )", POLICY.read_text(encoding="utf-8")):
        m = re.match(r"## (SP-\d+) · (.+)", sec)
        if m:
            out.append({"id": m.group(1), "source": f"سياسة خدمة الاطلاع (تجريبية) – {m.group(2)}", "about": "",
                        "text": sec.strip()})
    return out


# ---------------------------------------------------------------- BM25
class Index:
    def __init__(self, chunks: list[dict], k1: float = 1.5, b: float = 0.75):
        self.chunks, self.k1, self.b = chunks, k1, b
        self.docs = [Counter(tokens(c["about"] + " " + c["text"])) for c in chunks]
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.lens) / max(1, len(self.lens))
        df = Counter(t for d in self.docs for t in d)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, k: int = 4) -> list[dict]:
        q = tokens(query)
        scores = []
        for i, d in enumerate(self.docs):
            s = 0.0
            for t in q:
                if t in d:
                    f = d[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avg))
            if s > 0:
                scores.append((s, i))
        scores.sort(reverse=True)
        return [{**self.chunks[i], "score": round(s, 2)} for s, i in scores[:k]]


_cache: dict[str, Index] = {}


def index(mode: str = "clean") -> Index:
    """The index for a mode, built once and saved to data/index_<mode>.json (rebuilt when the PDF changes)."""
    if mode not in ("raw", "clean"):
        raise ValueError("mode must be 'raw' or 'clean'")
    if mode not in _cache:
        path = DATA / f"index_{mode}.json"
        stamp = f"{PDF.stat().st_mtime_ns}-{POLICY.stat().st_mtime_ns}"
        chunks = None
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            chunks = saved["chunks"] if saved.get("stamp") == stamp else None
        if chunks is None:
            chunks = build(mode)
            path.write_text(json.dumps({"stamp": stamp, "chunks": chunks}, ensure_ascii=False, indent=0), encoding="utf-8")
        _cache[mode] = Index(chunks)
    return _cache[mode]


if __name__ == "__main__":  # quick look: python knowledge.py "كم مكتبة يضم المجمع"
    import sys

    q = " ".join(sys.argv[1:]) or "كم مكتبة وقفية يضم المجمع"
    for mode in ("raw", "clean"):
        print(f"\n=== {mode}")
        for h in index(mode).search(q, 3):
            print(h["score"], h["id"], h["about"][:40], "|", h["text"][:120].replace("\n", " "))
