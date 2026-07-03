"""Static word lists for medical categorization.

Supported languages: English (en), French (fr), German (de),
Swiss German (de-CH), Danish (da).
"""

from __future__ import annotations

# ── Number words ─────────────────────────────────────────────────────────────

ENGLISH_CARDINALS: frozenset[str] = frozenset({
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
    "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
    "hundred", "thousand", "million",
    "a", "an",
})

ENGLISH_ORDINALS: frozenset[str] = frozenset({
    "first", "second", "third", "fourth", "fifth", "sixth", "seventh",
    "eighth", "ninth", "tenth", "eleventh", "twelfth",
    "thirteenth", "fourteenth", "fifteenth", "sixteenth",
    "seventeenth", "eighteenth", "nineteenth", "twentieth",
    # list-style ordinal adverbs
    "firstly", "secondly", "thirdly", "fourthly", "fifthly", "sixthly",
    "seventhly", "eighthly", "ninthly", "tenthly",
})

FRENCH_CARDINALS: frozenset[str] = frozenset({
    "zéro", "zero", "un", "une", "deux", "trois", "quatre", "cinq", "six",
    "sept", "huit", "neuf", "dix", "onze", "douze", "treize", "quatorze",
    "quinze", "seize", "dix-sept", "dixsept", "dix-huit", "dixhuit",
    "dix-neuf", "dixneuf", "vingt", "trente", "quarante", "cinquante",
    "soixante", "cent", "mille", "million",
    # compound forms emitted as single tokens by some ASR systems
    "quatre-vingt", "quatrevingt", "quatre-vingts", "quatrevingts",
    "quatre-vingt-dix", "quatrevingtdix",
})

FRENCH_ORDINALS: frozenset[str] = frozenset({
    "premier", "première", "deuxième", "troisième", "quatrième", "cinquième",
    "sixième", "septième", "huitième", "neuvième", "dixième",
    "onzième", "douzième", "treizième", "quatorzième", "quinzième",
    "seizième", "vingtième",
    # list-style ordinal adverbs
    "premièrement", "deuxièmement", "troisièmement", "quatrièmement",
    "cinquièmement", "sixièmement", "septièmement", "huitièmement",
    "neuvièmement", "dixièmement",
})

GERMAN_CARDINALS: frozenset[str] = frozenset({
    "null", "eins", "ein", "eine", "einem", "einer", "einen",
    "zwei", "drei", "vier", "fünf", "sechs", "sieben",
    "acht", "neun", "zehn", "elf", "zwölf",
    "dreizehn", "vierzehn", "fünfzehn", "sechzehn", "siebzehn",
    "achtzehn", "neunzehn",
    "zwanzig", "dreißig", "vierzig", "fünfzig",
    "sechzig", "siebzig", "achtzig", "neunzig",
    "hundert", "tausend",
    # compound forms the ASR sometimes emits as a single token
    "zweitausend", "zweitausendfünfzehn", "zweitausendachtzehn",
})

GERMAN_ORDINALS: frozenset[str] = frozenset({
    "erstens", "zweitens", "drittens", "viertens", "fünftens",
    "sechstens", "siebtens", "siebentens", "achtens", "neuntens", "zehntens",
    "elftens", "zwölftens",
    "erste", "zweite", "dritte", "vierte", "fünfte",
    "sechste", "siebte", "achte", "neunte", "zehnte",
})

# Swiss German (de-CH) uses standard German vocabulary in formal/medical
# dictation. The only systematic difference is ß → ss (e.g. dreißig → dreissig).
# All ss-variants are included here; they are merged into ALL_CARDINALS below.
SWISS_GERMAN_CARDINALS: frozenset[str] = frozenset({
    "dreissig", "strasse",  # ß→ss forms of common German words
})

SWISS_GERMAN_ORDINALS: frozenset[str] = frozenset()  # identical to German

# Danish (da)
DANISH_CARDINALS: frozenset[str] = frozenset({
    "nul", "en", "et", "to", "tre", "fire", "fem", "seks", "syv", "otte",
    "ni", "ti", "elleve", "tolv", "tretten", "fjorten", "femten", "seksten",
    "sytten", "atten", "nitten", "tyve", "tredive", "tredve", "fyrre",
    "halvtreds", "tres", "halvfjerds", "firs", "halvfems",
    "hundrede", "tusind", "tusinde", "million",
})

DANISH_ORDINALS: frozenset[str] = frozenset({
    "første", "anden", "andet", "tredje", "fjerde", "femte", "sjette",
    "syvende", "ottende", "niende", "tiende", "ellevte", "tolvte",
})

# Unions used by _is_number_word() / rule_ordinal_format() in categorize.py
ALL_CARDINALS: frozenset[str] = (
    ENGLISH_CARDINALS | FRENCH_CARDINALS | GERMAN_CARDINALS
    | SWISS_GERMAN_CARDINALS | DANISH_CARDINALS
)

ALL_ORDINALS: frozenset[str] = (
    ENGLISH_ORDINALS | FRENCH_ORDINALS | GERMAN_ORDINALS
    | SWISS_GERMAN_ORDINALS | DANISH_ORDINALS
)

# ── Roman numerals ────────────────────────────────────────────────────────────

ROMAN_NUMERALS: frozenset[str] = frozenset({
    "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii",
})

ROMAN_ANCHORS: frozenset[str] = frozenset({
    # English
    "roman", "numeral", "type", "grade", "stage", "class", "level",
    # French
    "romain", "romaine", "stade",
    # German / Swiss German
    "römisch", "römische", "römischen",
    # Danish
    "romersk", "type", "grad", "trin",
})

# ── Formatting / punctuation markers ─────────────────────────────────────────

FORMATTING_MARKERS: frozenset[str] = frozenset({
    # English
    "period", "comma", "hyphen", "dash", "slash", "colon", "semicolon",
    "open", "close", "parenthesis", "bracket", "quote",
    "new", "paragraph", "line", "bullet", "point", "next", "stop",
    # French
    "virgule", "tiret", "trait", "deux-points", "deuxpoints",
    "parenthèse", "crochet", "guillemet", "nouveau", "paragraphe", "ligne",
    # German / Swiss German (de / de-CH)
    "punkt", "komma", "bindestrich", "schrägstrich", "doppelpunkt",
    "klammer", "anführungszeichen", "neuer", "absatz", "zeile",
    "aufzählungszeichen", "strichpunkt",
    # Danish (da)
    "punktum", "bindestreg", "skråstreg", "semikolon",
    "parentes", "nyt", "afsnit", "linje", "punkttegn",
})

# Medical abbreviation → expansion(s). Keys and values are token tuples
# (lowercase). Each key may map to multiple valid expansions.
ABBREVIATION_EXPANSIONS: dict[tuple[str, ...], tuple[tuple[str, ...], ...]] = {
    # Standard clinical abbreviations
    ("dm",):        (("diabetes", "mellitus"),),
    ("cad",):       (("coronary", "artery", "disease"),),
    ("tia",):       (("transient", "ischemic", "attack"),),
    ("htn",):       (("hypertension",),),
    ("chf",):       (("congestive", "heart", "failure"),),
    ("copd",):      (("chronic", "obstructive", "pulmonary", "disease"),),
    ("mi",):        (("myocardial", "infarction"),),
    ("cabg",):      (("coronary", "artery", "bypass", "graft"),),
    ("afib",):      (("atrial", "fibrillation"),),
    ("af",):        (("atrial", "fibrillation"),),
    ("dvt",):       (("deep", "vein", "thrombosis"),),
    ("pe",):        (("pulmonary", "embolism"),),
    ("uti",):       (("urinary", "tract", "infection"),),
    ("uri",):       (("upper", "respiratory", "infection"),),
    ("bp",):        (("blood", "pressure"),),
    ("hr",):        (("heart", "rate"),),
    ("rr",):        (("respiratory", "rate"),),
    ("temp",):      (("temperature",),),
    ("wt",):        (("weight",),),
    ("ht",):        (("height",),),
    ("bmi",):       (("body", "mass", "index"),),
    ("sob",):       (("shortness", "of", "breath"),),
    ("cp",):        (("chest", "pain"),),
    ("n/v",):       (("nausea", "vomiting"),),
    ("nv",):        (("nausea", "vomiting"),),
    ("hx",):        (("history",),),
    ("sx",):        (("symptoms",),),
    ("dx",):        (("diagnosis",),),
    ("tx",):        (("treatment",), ("transfer",)),
    ("rx",):        (("prescription",), ("treatment",)),
    ("fx",):        (("fracture",),),
    ("pt",):        (("patient",), ("physical", "therapy")),
    ("po",):        (("by", "mouth"), ("orally",)),
    ("iv",):        (("intravenous",), ("intravenously",)),
    ("im",):        (("intramuscular",), ("intramuscularly",)),
    ("sc",):        (("subcutaneous",), ("subcutaneously",)),
    ("subq",):      (("subcutaneous",),),
    ("sq",):        (("subcutaneous",),),
    ("bid",):       (("twice", "daily"), ("twice", "a", "day")),
    ("tid",):       (("three", "times", "daily"), ("three", "times", "a", "day")),
    ("qid",):       (("four", "times", "daily"), ("four", "times", "a", "day")),
    ("qd",):        (("once", "daily"), ("daily",)),
    ("prn",):       (("as", "needed"),),
    ("hs",):        (("at", "bedtime"),),
    ("ac",):        (("before", "meals"),),
    ("pc",):        (("after", "meals"),),
    ("stat",):      (("immediately",),),
    ("asap",):      (("as", "soon", "as", "possible"),),
    ("npo",):       (("nothing", "by", "mouth"),),
    ("ad",):        (("right", "ear"),),
    ("as",):        (("left", "ear"),),
    ("au",):        (("both", "ears"),),
    ("od",):        (("right", "eye"),),
    ("os",):        (("left", "eye"),),
    ("ou",):        (("both", "eyes"),),
    ("mri",):       (("magnetic", "resonance", "imaging"),),
    ("ct",):        (("computed", "tomography"), ("ct", "scan")),
    ("ekg",):       (("electrocardiogram",),),
    ("ecg",):       (("electrocardiogram",),),
    ("echo",):      (("echocardiogram",),),
    ("cxr",):       (("chest", "x-ray"), ("chest", "xray")),
    ("eeg",):       (("electroencephalogram",),),
    ("emg",):       (("electromyogram",),),
    ("us",):        (("ultrasound",),),
    ("cbc",):       (("complete", "blood", "count"),),
    ("bmp",):       (("basic", "metabolic", "panel"),),
    ("cmp",):       (("comprehensive", "metabolic", "panel"),),
    ("lfts",):      (("liver", "function", "tests"),),
    ("pfts",):      (("pulmonary", "function", "tests"),),
    ("bnp",):       (("brain", "natriuretic", "peptide"),),
    ("inr",):       (("international", "normalized", "ratio"),),
    ("ptt",):       (("partial", "thromboplastin", "time"),),
    ("pt",):        (("prothrombin", "time"), ("patient",)),
    ("wbc",):       (("white", "blood", "cell"), ("white", "blood", "count")),
    ("rbc",):       (("red", "blood", "cell"), ("red", "blood", "count")),
    ("hgb",):       (("hemoglobin",),),
    ("hct",):       (("hematocrit",),),
    ("plt",):       (("platelets",),),
    ("na",):        (("sodium",),),
    ("k",):         (("potassium",),),
    ("cl",):        (("chloride",),),
    ("co2",):       (("carbon", "dioxide"), ("bicarbonate",)),
    ("bun",):       (("blood", "urea", "nitrogen"),),
    ("cr",):        (("creatinine",),),
    ("glu",):       (("glucose",),),
    ("ca",):        (("calcium",),),
    ("mg",):        (("magnesium",),),
    ("phos",):      (("phosphorus",),),
    ("alt",):       (("alanine", "aminotransferase"),),
    ("ast",):       (("aspartate", "aminotransferase"),),
    ("alp",):       (("alkaline", "phosphatase"),),
    ("tbili",):     (("total", "bilirubin"),),
    ("alb",):       (("albumin",),),
    ("a1c",):       (("hemoglobin", "a1c"), ("hba1c",)),
    ("hba1c",):     (("hemoglobin", "a1c"),),
    ("tsh",):       (("thyroid", "stimulating", "hormone"),),
    ("t4",):        (("thyroxine",),),
    ("psa",):       (("prostate", "specific", "antigen"),),
    ("ua",):        (("urinalysis",),),
    ("c/s",):       (("culture", "and", "sensitivity"),),
    ("cs",):        (("culture", "and", "sensitivity"),),
    ("s/p",):       (("status", "post"),),
    ("sp",):        (("status", "post"),),
    ("h/o",):       (("history", "of"),),
    ("ho",):        (("history", "of"),),
    ("r/o",):       (("rule", "out"),),
    ("ro",):        (("rule", "out"),),
    ("w/u",):       (("workup",),),
    ("f/u",):       (("follow", "up"), ("follow-up",)),
    ("fu",):        (("follow", "up"), ("follow-up",)),
    ("yo",):        (("year", "old"), ("years", "old")),
    ("y/o",):       (("year", "old"), ("years", "old")),
    ("m",):         (("male",),),
    ("f",):         (("female",),),
    ("y",):         (("year",), ("years",)),
    ("mo",):        (("month",), ("months",)),
    ("wk",):        (("week",), ("weeks",)),
    ("d",):         (("day",), ("days",)),
    ("h",):         (("hour",), ("hours",)),
    ("min",):       (("minute",), ("minutes",)),
    ("sec",):       (("second",), ("seconds",)),
    # Units — English + French + German/Swiss German + Danish forms
    ("kg",):   (("kilograms",), ("kilogrammes",), ("kilogramm",),
                ("kilogram",)),                                        # da
    ("lbs",):  (("pounds",),),
    ("lb",):   (("pound",),),
    ("cm",):   (("centimeters",), ("centimètres",), ("centimètre",),
                ("zentimeter",), ("centimeter",)),                     # da
    ("mm",):   (("millimeters",), ("millimètres",), ("millimètre",),
                ("millimeter",)),                                       # de/da
    ("mg",):   (("milligrams",), ("milligrammes",), ("milligramme",),
                ("milligramm",), ("milligram",)),                       # da
    ("mcg",):  (("micrograms",), ("microgrammes",), ("microgramme",),
                ("mikrogramm",), ("mikrogram",)),                       # da
    ("ml",):   (("milliliters",), ("millilitres",), ("millilitre",),
                ("milliliter",)),                                       # de/da
    ("dl",):   (("deciliters",), ("décilitres",), ("décilitre",),
                ("deziliter",), ("deciliter",)),                        # da
    ("l",):    (("liters",), ("litres",), ("litre",), ("liter",)),
    ("meq",):  (("milliequivalents",), ("milliéquivalents",)),
    ("mmhg",): (("millimeters", "of", "mercury"),
                ("millimètres", "de", "mercure"),
                ("millimeter", "kviksølv")),                            # da
    ("bpm",):  (("beats", "per", "minute"),
                ("battements", "par", "minute"),
                ("schläge", "pro", "minute"),
                ("slag", "per", "minut")),                              # da
    ("lpm",):  (("liters", "per", "minute"), ("litres", "par", "minute"),
                ("liter", "per", "minut")),                             # da
}

SINGLE_TOKEN_ABBREVS: frozenset[str] = frozenset(
    k[0] for k in ABBREVIATION_EXPANSIONS if len(k) == 1
)

# Known medications, devices, and procedures frequently mangled by ASR
MEDICATIONS_AND_DEVICES: frozenset[str] = frozenset({
    # anticoagulants
    "warfarin", "coumadin", "xarelto", "rivaroxaban", "eliquis", "apixaban",
    "pradaxa", "dabigatran", "heparin", "lovenox", "enoxaparin",
    # antiplatelets
    "aspirin", "plavix", "clopidogrel", "brilinta", "ticagrelor",
    # antihypertensives
    "lisinopril", "metoprolol", "amlodipine", "losartan", "atenolol",
    "carvedilol", "valsartan", "hydrochlorothiazide", "furosemide", "lasix",
    "spironolactone", "diltiazem", "verapamil", "clonidine", "hydralazine",
    # statins
    "atorvastatin", "lipitor", "simvastatin", "zocor", "rosuvastatin",
    "crestor", "pravastatin",
    # diabetes
    "metformin", "glucophage", "insulin", "lantus", "glargine", "humalog",
    "lispro", "novolog", "aspart", "jardiance", "empagliflozin", "ozempic",
    "semaglutide", "trulicity", "dulaglutide", "victoza", "liraglutide",
    # analgesics
    "acetaminophen", "tylenol", "ibuprofen", "advil", "naproxen", "aleve",
    "oxycodone", "hydrocodone", "morphine", "tramadol", "gabapentin",
    # antibiotics
    "amoxicillin", "augmentin", "azithromycin", "zithromax", "ciprofloxacin",
    "cipro", "levofloxacin", "levaquin", "doxycycline", "cephalexin",
    "vancomycin", "piperacillin", "tazobactam", "zosyn", "meropenem",
    "ceftriaxone", "rocephin", "metronidazole", "flagyl", "clindamycin",
    # respiratory
    "albuterol", "ventolin", "proventil", "salmeterol", "fluticasone",
    "advair", "symbicort", "budesonide", "formoterol", "tiotropium", "spiriva",
    "montelukast", "singulair",
    # GI / acid suppression
    "omeprazole", "prilosec", "pantoprazole", "protonix", "famotidine",
    "pepcid", "ranitidine", "zantac", "ondansetron", "zofran",
    # psych
    "sertraline", "zoloft", "escitalopram", "lexapro", "fluoxetine", "prozac",
    "venlafaxine", "effexor", "duloxetine", "cymbalta", "bupropion", "wellbutrin",
    "quetiapine", "seroquel", "olanzapine", "zyprexa", "risperidone", "risperdal",
    "lorazepam", "ativan", "alprazolam", "xanax", "diazepam", "valium",
    "zolpidem", "ambien", "clonazepam", "klonopin",
    # steroids / immunosuppressants
    "prednisone", "methylprednisolone", "solumedrol", "dexamethasone",
    "hydrocortisone", "methotrexate", "hydroxychloroquine", "plaquenil",
    "azathioprine", "tacrolimus", "prograf", "mycophenolate", "cellcept",
    # oncology (common names)
    "tamoxifen", "letrozole", "anastrozole", "herceptin", "trastuzumab",
    "rituximab", "rituxan", "bevacizumab", "avastin",
    # devices / procedures
    "stent", "icd", "pacemaker", "defibrillator", "aicd",
    "lvad", "tavr", "tavi", "cabg", "ptca",
    "foley", "picc", "cvl", "arterial", "midline",
})

# Latin/Greek spelling variants common in English medical transcription
LATIN_GREEK_PAIRS: tuple[tuple[str, str], ...] = (
    ("ae", "e"),       # anaemia → anemia
    ("oe", "e"),       # oedema → edema
    ("c", "k"),        # cervical/kervical variants
    ("ph", "f"),       # pharmacy phonetic
    ("th", "t"),       # thrombus phonetic
    ("x", "ks"),       # prefix variants
    ("y", "i"),        # gynaecology → gynecology
)
