"""
Medicine Identity Resolver
==========================
Parses a raw medicine name + salt into a *structured identity* so that two
records that are really the same product resolve to the same key — regardless
of trivial surface differences (case, "Tablet" vs "Tab" vs "tablets", pack-size
suffixes like "10s", hyphen-vs-space, unit spacing, reordered doses).

Identity = (brand, strength, form) with SALT as a guard.
  - "BRUCARE 600MG TABLETS"     ) both ->  brand='brucare' strength='600mg'
  - "Brucare 600mg Tablet 10s"  )          form='tablet'  -> SAME
  - "Brucare 600 Syrup"                 -> different form  -> NOT same
  - "Mucare 600mg Tablet"               -> different brand -> NOT same

Pure functions only — no DB, no I/O, no schema knowledge. Fully offline-testable.
The dose/form vocab mirrors the conventions already used by search_engine.py's
compute_relevance_score and the backend entity_normalizer, so identities line up
with the existing collection.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional


# ── Form vocabulary: many surface spellings -> one canonical form ──────────────
# Order matters only for readability; lookup is by exact token/phrase.
_FORM_CANON = {
    "tablet": "tablet", "tablets": "tablet", "tab": "tablet", "tabs": "tablet",
    "caplet": "tablet", "caplets": "tablet",
    "capsule": "capsule", "capsules": "capsule", "cap": "capsule", "caps": "capsule",
    "syrup": "syrup", "syp": "syrup",
    "suspension": "suspension", "susp": "suspension",
    "solution": "solution", "soln": "solution",
    "drop": "drops", "drops": "drops",
    "injection": "injection", "inj": "injection", "vial": "injection",
    "ampoule": "injection", "ampule": "injection",
    "cream": "cream", "ointment": "ointment", "gel": "gel", "lotion": "lotion",
    "spray": "spray", "powder": "powder", "sachet": "powder", "granules": "powder",
    "inhaler": "inhaler", "rotacaps": "rotacap", "rotacap": "rotacap",
    "respules": "respule", "respule": "respule",
    "eye drops": "eye drops", "ear drops": "ear drops",
}

# Non-identifying noise words that must never end up in the brand.
# IMPORTANT: formulation modifiers ("plus", "forte", "ds", "dsr", "sr", "xl",
# "od", "cr", "mr", "h", ...) are NOT noise — they distinguish genuinely
# different products (e.g. "Ibugesic Plus" = ibuprofen+paracetamol vs "Ibugesic"
# = ibuprofen alone; "Calpol Plus" vs "Calpol"). Dropping them causes false
# merges, so they are deliberately kept as part of the brand.
_NOISE = {
    "of", "the", "for", "with", "and", "&",
    "ip", "bp", "usp", "nf",                          # pharmacopeia
    "strip", "bottle", "pack", "box", "jar", "tube",  # packaging
    "oral", "sterile",
    "paediatric", "pediatric", "paed",                # age-segment descriptor
    "flavour", "flavor", "flavoured", "flavored",     # pure descriptor words
}

# Strength: number (optional decimal) + optional unit.  "600mg", "2.5 mg", "650",
# "5%", "40000 iu", "1.2g".  NOTE: ml/litre are deliberately NOT strength units —
# for a liquid, an ml figure is the pack VOLUME (60ml bottle), not the active
# dose (which is in mg/mcg/g/iu/%).  Volumes are stripped as packaging below so
# "...suspension 60ml" and "...suspension" resolve to the same identity.
# UNIT ORDER MATTERS: longest-first, so "15gm" matches "gm" (not "g", which would
# leave a stray "m" that pollutes the brand — the Tenovate-M false-merge bug).
_STRENGTH_RE = re.compile(
    r'(\d+\.?\d*)\s*(mcg|mg|gm|iu|lakh|lac|units?|g|%)?',
    re.IGNORECASE,
)
# Pack-size / count / volume noise: "10s", "15's", "strip of 10", "pack of 15",
# "x 10", "60ml", "100 ml", "1 litre".  Stripped BEFORE strength extraction so a
# bottle volume never masquerades as a dose.
# NOTE: the multipack alternation MUST come first. "5x2ml" / "5 x 2 ml" is a
# count-by-volume PACK grid (5 vials of 2ml each), pure packaging — neither the
# 5 nor the 2 is a dose. It is glued (no word boundary between "5" and "x", or
# "x" and "2"), so the plain volume and "x\d+" rules below never catch it, and
# its digits leak in as phantom strengths while "x"/"ml" pollute the brand —
# splitting "budaways ...2ml" from "budaways ...5x2ml" as different products.
_PACK_RE = re.compile(
    r"(?:\b\d+\s*[x×]\s*\d+\.?\d*\s*(?:ml|ltr|litre|litres|g|gm)\b)"
    r"|(?:\b(?:strip|pack|box|bottle)\s+of\s+\d+\b)"
    r"|(?:\b\d+\.?\d*\s*(?:ml|ltr|litre|litres)\b)"
    r"|(?:\b\d+\s*'?s\b)"
    r"|(?:\bx\s*\d+\b)",
    re.IGNORECASE,
)

_UNIT_CANON = {
    "mg": "mg", "mcg": "mcg", "g": "g", "gm": "g",
    "iu": "iu", "%": "%", "lac": "lac", "lakh": "lac",
    "unit": "units", "units": "units",
}


def _canon_unit(u: Optional[str]) -> str:
    if not u:
        return ""
    return _UNIT_CANON.get(u.lower(), u.lower())


def _canon_num(n: str) -> str:
    """'600' -> '600', '2.50' -> '2.5', '650.0' -> '650'."""
    if "." in n:
        n = n.rstrip("0").rstrip(".")
    return n or "0"


def _dose_sort(s: str):
    """Sort key for a stored dose token ('600' or '600:mg') by numeric value."""
    num = s.split(":")[0]
    try:
        return (float(num), s)
    except ValueError:
        return (float("inf"), s)


@dataclass
class MedicineIdentity:
    brand: str = ""            # e.g. "brucare"
    strength: List[str] = field(default_factory=list)  # e.g. ["600mg"]
    form: str = ""             # canonical form, e.g. "tablet" ("" if unknown)
    salt_key: str = ""         # dosage-stripped, sorted salt, e.g. "ibuprofen"

    @property
    def strength_key(self) -> str:
        # sorted by numeric value so "100+162" == "162+100"; keeps unit tag.
        return "+".join(sorted(self.strength, key=_dose_sort))

    @property
    def strength_nums(self) -> tuple:
        """Just the numeric doses, sorted — for unit-tolerant comparison
        ('600' ~ '600:mg')."""
        return tuple(sorted(s.split(":")[0] for s in self.strength))

    @property
    def brand_tokens(self) -> frozenset:
        """Brand as an unordered token set — so "crocin ds 240" and
        "crocin 240 ds" (dose reordered around the brand) resolve equal."""
        return frozenset(self.brand.split())

    @property
    def key(self) -> str:
        """Stable merge key: brand | strength | form.  Salt is a guard, not part
        of the key (a brand+strength+form pins the product; salt confirms it)."""
        return f"{self.brand}|{'+'.join(self.strength_nums)}|{self.form}"


# Words that betray a `salt` value as scraped prose / a dosage-form description
# rather than a real composition. If a salt_key contains any of these (or is
# implausibly long), it must not be trusted to confirm a molecule "echo" in the
# brand — its tokens are page boilerplate, not molecule names.
_SALT_PROSE_MARKERS = frozenset({
    "composition", "consists", "active", "component", "components",
    "contains", "includes", "including", "combination",
    "company", "order", "manufactured", "marketed", "packing", "packaging",
    "powder", "injection", "tablet", "tablets", "capsule", "capsules",
    "syrup", "suspension", "solution", "cream", "ointment", "gel", "lotion",
    "spray", "sachet", "vial", "drops", "about", "uses", "benefits", "works",
})


def _salt_is_molecular(salt_key: str) -> bool:
    """True if a normalized salt_key looks like a real composition (1-5 molecule
    tokens, no prose/form markers) rather than scraped webpage boilerplate. Used
    to decide whether a salt can be trusted to confirm a molecule echo."""
    toks = salt_key.split()
    if not (1 <= len(toks) <= 5):
        return False
    return not any(t in _SALT_PROSE_MARKERS for t in toks)


# Molecule synonyms: different accepted names for the SAME active, folded to one
# canonical spelling so a combo written "Paracetamol / Acetaminophen" matches a
# record that just says "Paracetamol". INN/BAN vs USAN pairs common in the Indian
# market. Extend as real duplicates surface — every entry must be a true synonym.
_SALT_SYNONYMS = {
    "acetaminophen": "paracetamol",
    "albuterol": "salbutamol",
    "rifampin": "rifampicin",
    "cetirizine hydrochloride": "cetirizine",
    "amoxycillin": "amoxicillin",
}


def _normalize_salt_key(salt: str) -> str:
    """Dosage-stripped, lowercased, sorted salt — matches the collection's
    `normalized_salt` convention (e.g. 'Ibuprofen (600mg)' -> 'ibuprofen').

    Robust to the surface noise real scraped salts carry: doses glued to the
    molecule by a hyphen ('CAFFEINE-32MG' -> 'caffeine', not 'caffeine-'), a
    '/'-joined synonym pair ('Paracetamol / Acetaminophen'), parenthesised doses,
    and INN/USAN synonyms — so the same composition written three different ways
    still yields one identical key.
    """
    if not salt:
        return ""
    clean = salt.lower()
    # Drop dose figures (number + optional unit) so only molecule names remain.
    clean = re.sub(r'\d+\.?\d*\s*(mg|ml|gm|mcg|g|%|iu)\b', ' ', clean)
    clean = re.sub(r'\d+\.?\d*', ' ', clean)  # any bare numbers left over
    # Normalise every combo separator to a comma.
    clean = clean.replace('(', ' ').replace(')', ' ')
    clean = clean.replace('+', ',').replace('/', ',').replace(' and ', ',')
    tokens = []
    for chunk in clean.split(','):
        # Trim non-alpha edges left by 'caffeine-' / '-neomycin' / stray dots.
        w = re.sub(r'^[^a-z]+|[^a-z]+$', '', chunk.strip())
        # Collapse internal whitespace left when an inter-molecule dose is
        # stripped: 'formoterol 0 budesonide 0' -> 'formoterol   budesonide'.
        # Without this the extra spaces survive into the grouping key and a
        # dose-leaked value forms its own phantom bucket instead of landing on
        # the clean 'formoterol budesonide' one. Order is preserved (no split of
        # real two-word molecules like 'clavulanic acid').
        w = re.sub(r'\s+', ' ', w)
        if len(w) <= 3:
            continue
        tokens.append(_SALT_SYNONYMS.get(w, w))
    return " ".join(sorted(set(tokens)))


def parse_identity(name: str, salt: str = "") -> MedicineIdentity:
    """Parse a raw product name (+ optional salt) into a structured identity."""
    ident = MedicineIdentity(salt_key=_normalize_salt_key(salt))
    if not name:
        return ident

    text = " " + name.lower().strip() + " "

    # 1) Strip pack-size / count noise first so counts don't masquerade as dose.
    text = _PACK_RE.sub(" ", text)

    # 2) Pull the form out (longest phrase first, e.g. "eye drops" before "drops").
    for phrase in sorted(_FORM_CANON, key=lambda p: -len(p)):
        pat = re.compile(r'\b' + re.escape(phrase) + r'\b')
        if pat.search(text):
            ident.form = _FORM_CANON[phrase]
            text = pat.sub(" ", text)
            break

    # 3) Extract strengths (number + optional unit). A bare number IS a strength
    #    here (pack counts already removed), e.g. Dolo "650". Stored as
    #    "num" or "num:unit" so comparison can treat "600" ~ "600mg".
    strengths = []
    def _grab(m):
        num = _canon_num(m.group(1))
        unit = _canon_unit(m.group(2))
        strengths.append(f"{num}:{unit}" if unit else num)
        return " "
    text = _STRENGTH_RE.sub(_grab, text)
    ident.strength = strengths

    # 4) Whatever alpha tokens remain, minus noise, are the brand.
    tokens = [t for t in re.split(r'[^a-z0-9]+', text) if t]
    brand_tokens = [t for t in tokens if t not in _NOISE and not t.isdigit()]
    # Drop a lone "n" used as the "'n'" connective ("cold n flu" == "cold and
    # flu") — but ONLY when it is INFIX (another brand word follows it). A
    # TRAILING single "n" is a real suffix modifier (e.g. Betnesol-N adds
    # neomycin, a genuinely different product) and must be kept.
    brand_tokens = [
        t for i, t in enumerate(brand_tokens)
        if not (t == "n" and i < len(brand_tokens) - 1)
    ]
    ident.brand = " ".join(brand_tokens).strip()

    return ident


def is_same_medicine(a: MedicineIdentity, b: MedicineIdentity) -> bool:
    """True if two identities denote the same product.

    Rule: brand AND strength must match. Brand is compared as an unordered token
    set (so a dose reordered around the brand doesn't split a product). Form must
    match UNLESS one side is unknown (many raw names omit the form). Salt is a
    guard: if BOTH have a salt and they don't overlap, reject the merge even if
    brand/strength agree.
    """
    if not a.brand or not b.brand:
        return False
    if a.brand_tokens != b.brand_tokens and not _brands_match_via_salt_echo(a, b):
        return False
    # Strength: compare numeric doses. Units are only a tie-breaker when BOTH
    # sides carry a unit AND numbers are equal, so "600" ~ "600mg" but
    # "600mg" != "600mcg".
    if a.strength_nums != b.strength_nums:
        return False
    if not _units_compatible(a, b):
        return False
    # Form: identical, or one unknown -> allowed.
    if a.form and b.form and a.form != b.form:
        return False
    # Salt guard: only rejects when both known AND disjoint.
    if a.salt_key and b.salt_key and not _salt_overlap(a.salt_key, b.salt_key):
        return False
    return True


def _brands_match_via_salt_echo(a: MedicineIdentity, b: MedicineIdentity) -> bool:
    """True if two brands differ ONLY by molecule names echoed from the title AND
    shared by both compositions.

    A molecule name often leaks into a product title on some platforms but not
    others ("Crocin Baby Paracetamol Drops" vs "Crocin Baby Drops"). That echo is
    composition, not brand — but only when BOTH records really are that molecule.
    So we ignore a differing brand token only if it appears in the *intersection*
    of both salt keys. This keeps the fix pairwise and safe:

      - "Crocin Baby Paracetamol" vs "Crocin Baby"  -> diff {paracetamol}, and
        paracetamol is in both salts -> SAME.
      - "Kriam Ambroxol ..." vs "Kriam Bromhexine ..." -> diff {ambroxol,
        bromhexine}; neither is shared by both salts -> NOT same (different drugs).
      - "Pantomay ..." vs "Pantafol ..." where the scraped salt is prose echoing
        the brand -> diff {pantomay,pantafol}; neither brand word is in the OTHER
        record's salt -> NOT same (different brands, not a molecule echo).

    Requires both salts to be present AND to look like a real composition (not
    scraped prose): a garbage salt like "Embeta XR 25 composition consists of
    metoprolol..." would otherwise leak brand words / release modifiers ("xr")
    into the shared set and license a false echo.
    """
    if not _salt_is_molecular(a.salt_key) or not _salt_is_molecular(b.salt_key):
        return False
    shared_salt = set(a.salt_key.split()) & set(b.salt_key.split())
    if not shared_salt:
        return False
    diff = a.brand_tokens ^ b.brand_tokens
    if not diff or not diff.issubset(shared_salt):
        return False
    # Every differing token is a shared molecule; the remaining cores must match.
    return (a.brand_tokens - shared_salt) == (b.brand_tokens - shared_salt)


def _units_compatible(a: MedicineIdentity, b: MedicineIdentity) -> bool:
    """Given equal numeric doses, reject only when the SAME number carries two
    different explicit units (600mg vs 600mcg). A missing unit on either side
    is treated as compatible ('600' ~ '600mg')."""
    def unit_map(strengths):
        m = {}
        for s in strengths:
            parts = s.split(":")
            if len(parts) == 2:
                m.setdefault(parts[0], set()).add(parts[1])
        return m
    ma, mb = unit_map(a.strength), unit_map(b.strength)
    for num, ua in ma.items():
        ub = mb.get(num)
        if ub and not (ua & ub):
            return False
    return True


def _salt_overlap(s1: str, s2: str) -> bool:
    """Subset or >=50% Jaccard overlap — tolerant of combo ordering / partial
    salt data while still separating genuinely different molecules."""
    w1, w2 = set(s1.split()), set(s2.split())
    if not w1 or not w2:
        return True
    if w1.issubset(w2) or w2.issubset(w1):
        return True
    inter, union = w1 & w2, w1 | w2
    return (len(inter) / len(union)) >= 0.5 if union else False


def brand_prefix(name: str) -> str:
    """First brand token — a cheap, index-friendly candidate key for DB lookups
    (fetch everything sharing this prefix, then confirm with is_same_medicine)."""
    ident = parse_identity(name)
    return ident.brand.split(" ")[0] if ident.brand else ""


_FORMULATION_MODIFIERS = {
    "sr", "cr", "er", "xr", "ds", "dsr", "plus", "forte", "mr", "od", "pr", "la", "tr", "dt"
}


def extract_formulation_modifier(name: str) -> str:
    """Extract recognized formulation variant (e.g. 'sr', 'ds', 'forte', 'plus')."""
    if not name:
        return "standard"
    tokens = re.findall(r'[a-zA-Z0-9]+', name.lower())
    found = [t for t in tokens if t in _FORMULATION_MODIFIERS]
    return "-".join(sorted(found)) if found else "standard"


def compute_resolution_signature(raw_name: str, raw_salt: str, raw_form: str = "", pack_info: str = "") -> str:
    """
    Deterministic 5-tuple resolution signature:
    SHA256(brand_tokens | salt_tokens | strength_tokens | form_token | formulation_token)
    """
    import hashlib
    ident = parse_identity(raw_name, raw_salt)
    brand_tok = "-".join(sorted(ident.brand_tokens)) if ident.brand_tokens else "nobrand"
    salt_tok = "-".join(sorted(_normalize_salt_key(raw_salt).split())) if raw_salt else "nosalt"
    strength_tok = "-".join(ident.strength_nums) if ident.strength_nums else "nostrength"
    form_tok = ident.form or (raw_form.lower().strip() if raw_form else "unspecified")
    formulation_tok = extract_formulation_modifier(raw_name)
    
    composite = f"{brand_tok}|{salt_tok}|{strength_tok}|{form_tok}|{formulation_tok}"
    return hashlib.sha256(composite.encode("utf-8")).hexdigest()

