"""Hidden consumer check for t5. Run with the work folder as the current directory.

Part 1: the reported defect and its stated edge cases. Part 2: what every other caller of the shared text helpers did in the
untouched fixture (recorded from it: BASELINE below), so a fix that changes them fails.
"""
import json
import sys

sys.path.insert(0, ".")
from contacts import Contact, ContactBook, load_csv  # noqa: E402
import export, search  # noqa: E402

def build():
    b = ContactBook()
    b.add(Contact("José", "García", "jg@example.com", "555-0101", ("family",)))
    b.add(Contact("Émile", "Zola", "ez@books.example", "", ("work",)))
    b.add(Contact("Zed", "Adams", "zed@example.com", "555-0102", ("work", "family")))
    b.add(Contact("Anders", "Ångström", "", "555-0103"))
    b.add(Contact("adam", "Åberg", "ab@books.example", "", ("work",)))
    b.add(Contact("Anna", "Smith", "as@example.com", "555-0104", ("family",)))
    b.add(Contact("Anna", "Smith", "", "555-0105"))
    b.add(Contact("  Mary   Ann ", "O'Neil  ", "mo@example.com"))
    b.add(Contact("Zoë", "Brown", "zb@example.com", "", ("work",)))
    b.add(Contact("Dr. Who", "", "who@example.com"))
    b.add(Contact("Ünal", "Çelik", "uc@example.com"))
    return b

QUERIES = ["anna", "smith", "sm", "an", "example", "books", "zed adams", "ADAMS", "  mary ann ", "o'neil", "who", "zzz", "", "   ", "adam", "a", "ann"]
PREFIXES = ["jo", "z", "ad", "an", "  mary", "x"]

def record(book):
    out = {}
    out["sorted"] = book.names()
    out["groups"] = {k: [c.full_name for c in v] for k, v in book.group_by_initial().items()}
    out["group_keys"] = list(book.group_by_initial())
    out["filenames"] = [export.filename_for(c) for c in book]
    files = export.export_all(book)
    out["files"] = list(files)
    out["vcf_first"] = export.to_vcf(list(book)[0])
    out["vcf_last"] = export.to_vcf(list(book)[-1])
    out["csv"] = export.to_csv(book)
    out["summary"] = export.summary(book)
    out["search"] = {q: [c.full_name for c in search.search(book, q)] for q in QUERIES}
    out["search_limit"] = [c.full_name for c in search.search(book, "a", limit=3)]
    out["suggest"] = {p: search.suggest(book, p) for p in PREFIXES}
    out["domain"] = [c.full_name for c in search.by_domain(book, "BOOKS.example")]
    out["related"] = [c.full_name for c in search.related(book, list(book)[0])]
    out["tagged"] = [c.full_name for c in search.tagged(book, "family", "an")]
    csvbook = load_csv("# header\nAda King Lovelace, ada@x.org, 555-1\n\nGrace Hopper\n")
    out["csv_in"] = [(c.first, c.last, c.email, c.phone) for c in csvbook]
    other = ContactBook(); other.add(Contact("José", "García", "JG@example.com")); other.add(Contact("New", "Person", "np@example.com")); other.add(Contact("No", "Email"))
    out["merged"] = book.merge(other)
    out["after_merge"] = book.names()
    return out


BASELINE = json.loads('{"after_merge": ["Dr. Who", "Zed Adams", "Zoë Brown", "No Email", "José García", "Mary   Ann  O\'Neil", "New Person", "Anna Smith", "Anna Smith", "Émile Zola", "adam Åberg", "Anders Ångström", "Ünal Çelik"], "csv": "Dr. Who,who@example.com,\\nZed Adams,zed@example.com,555-0102\\nZoë Brown,zb@example.com,\\nJosé García,jg@example.com,555-0101\\nMary   Ann  O\'Neil,mo@example.com,\\nAnna Smith,as@example.com,555-0104\\nAnna Smith,,555-0105\\nÉmile Zola,ez@books.example,\\nadam Åberg,ab@books.example,\\nAnders Ångström,,555-0103\\nÜnal Çelik,uc@example.com,\\n", "csv_in": [["Ada King", "Lovelace", "ada@x.org", "555-1"], ["Grace", "Hopper", "", ""]], "domain": ["Émile Zola", "adam Åberg"], "filenames": ["josé_garcía.vcf", "émile_zola.vcf", "zed_adams.vcf", "anders_ångström.vcf", "adam_åberg.vcf", "anna_smith.vcf", "anna_smith.vcf", "mary_ann_oneil.vcf", "zoë_brown.vcf", "dr._who.vcf", "ünal_çelik.vcf"], "files": ["dr._who.vcf", "zed_adams.vcf", "zoë_brown.vcf", "josé_garcía.vcf", "mary_ann_oneil.vcf", "anna_smith.vcf", "anna_smith-2.vcf", "émile_zola.vcf", "adam_åberg.vcf", "anders_ångström.vcf", "ünal_çelik.vcf"], "group_keys": ["d", "a", "b", "g", "o", "s", "z", "å", "ç"], "groups": {"a": ["Zed Adams"], "b": ["Zoë Brown"], "d": ["Dr. Who"], "g": ["José García"], "o": ["Mary   Ann  O\'Neil"], "s": ["Anna Smith", "Anna Smith"], "z": ["Émile Zola"], "å": ["adam Åberg", "Anders Ångström"], "ç": ["Ünal Çelik"]}, "merged": 2, "related": ["Zed Adams", "Anna Smith", "Dr. Who"], "search": {"": [], "   ": [], "  mary ann ": ["Mary   Ann  O\'Neil"], "ADAMS": ["Zed Adams"], "a": ["Zed Adams", "Anna Smith", "Anna Smith", "adam Åberg", "Anders Ångström", "José García", "Mary   Ann  O\'Neil", "Émile Zola", "Ünal Çelik", "Dr. Who", "Zoë Brown"], "adam": ["Zed Adams", "adam Åberg"], "an": ["Anna Smith", "Anna Smith", "Anders Ångström", "Mary   Ann  O\'Neil"], "ann": ["Anna Smith", "Anna Smith", "Mary   Ann  O\'Neil"], "anna": ["Anna Smith", "Anna Smith"], "books": ["Émile Zola", "adam Åberg"], "example": ["Dr. Who", "Zed Adams", "Zoë Brown", "José García", "Mary   Ann  O\'Neil", "Anna Smith", "Émile Zola", "adam Åberg", "Ünal Çelik"], "o\'neil": ["Mary   Ann  O\'Neil"], "sm": ["Anna Smith", "Anna Smith"], "smith": ["Anna Smith", "Anna Smith"], "who": ["Dr. Who"], "zed adams": ["Zed Adams"], "zzz": []}, "search_limit": ["Zed Adams", "Anna Smith", "Anna Smith"], "sorted": ["Dr. Who", "Zed Adams", "Zoë Brown", "José García", "Mary   Ann  O\'Neil", "Anna Smith", "Anna Smith", "Émile Zola", "adam Åberg", "Anders Ångström", "Ünal Çelik"], "suggest": {"  mary": ["Mary   Ann  O\'Neil"], "ad": ["adam Åberg"], "an": ["Anna Smith", "Anna Smith", "Anders Ångström"], "jo": ["José García"], "x": [], "z": ["Zed Adams", "Zoë Brown"]}, "summary": "11 contacts on 9 pages, files: adam_åberg.vcf, anders_ångström.vcf, anna_smith-2.vcf, anna_smith.vcf, dr._who.vcf, josé_garcía.vcf, mary_ann_oneil.vcf, zed_adams.vcf, zoë_brown.vcf, émile_zola.vcf, ünal_çelik.vcf", "tagged": ["Anna Smith"], "vcf_first": "BEGIN:VCARD\\nVERSION:3.0\\nN:García;José;;;\\nFN:José García\\nEMAIL:jg@example.com\\nTEL:555-0101\\nCATEGORIES:family\\nEND:VCARD\\n", "vcf_last": "BEGIN:VCARD\\nVERSION:3.0\\nN:Çelik;Ünal;;;\\nFN:Ünal Çelik\\nEMAIL:uc@example.com\\nEND:VCARD\\n"}')

bad = []


def names(results):
    return [c.full_name for c in results]


def expect(label, got, want):
    if got != want:
        bad.append(f"{label}: got {got!r}, expected {want!r}")


# ---- part 1: the defect ----
d = ContactBook()
d.add(Contact("José", "García", "jg@example.com"))
d.add(Contact("Zoe", "Brown", "zb@example.com"))
d.add(Contact("Zoë", "Smith", "zs@example.com"))
d.add(Contact("Rene\u0301e", "Dupont", "rd@example.com"))      # e + combining acute
d.add(Contact("Renée", "Martin", "rm@example.com"))             # precomposed
d.add(Contact("Ana", "Núñez", "an@example.com"))
d.add(Contact("Jose", "", "j@example.com"))
for query, want in (("jose", ["Jose", "José García"]), ("JOSE", ["Jose", "José García"]), ("josé", ["Jose", "José García"]),
                    ("garcia", ["José García"]), ("García", ["José García"]), ("josé garcía", ["José García"]),
                    ("zoe", ["Zoe Brown", "Zoë Smith"]), ("zoë", ["Zoe Brown", "Zoë Smith"]),
                    ("renee", ["Renée Dupont", "Renée Martin"]), ("rene\u0301e", ["Renée Dupont", "Renée Martin"]),
                    ("Renée", ["Renée Dupont", "Renée Martin"]), ("nunez", ["Ana Núñez"]), ("núñez", ["Ana Núñez"]),
                    ("nu\u0301n\u0303ez", ["Ana Núñez"]), ("zoe  smith", ["Zoë Smith"])):
    got = [n.replace("e\u0301", "é") for n in names(search.search(d, query))]
    expect(f"search({query!r})", got, want)
expect("limit", names(search.search(d, "jose", limit=1)), ["Jose"])
expect("blank query", search.search(d, "  "), [])

# ---- part 2: the other callers keep their present behaviour ----
book = build()
rec = record(book)
for key, want in BASELINE.items():
    expect(key, json.loads(json.dumps(rec[key])), want)

print("hidden t5:", "FAIL " + "; ".join(bad[:6]) + (f" (+{len(bad) - 6} more)" if len(bad) > 6 else "") if bad else "ok")
sys.exit(1 if bad else 0)
