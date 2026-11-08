"""Role family, seniority and stack from a job's title and description. Rules only, no model.

Order matters: the first matching family wins, so "Engineering Manager" is management, "Machine
Learning Engineer" is data & ML, and "Research Engineer" is research before the generic engineer rule.
"""
from __future__ import annotations

import re

FAMILIES = ("engineering_management", "product_management", "research", "data_ml_engineering",
            "data_science", "software_engineering")

_I = re.IGNORECASE
# Customer-facing and non-engineering roles: never kept. Role phrases, not product domains, so
# "Software Engineer, Customer Identity" or "Software Engineer - Finance" stay.
EXCLUDE = re.compile(
    r"\b(sales|pre-?sales|solutions? (engineer|architect|consultant|specialist|lead|manager)|"
    r"customer (success|support|service|experience|engineer)|support engineer|technical support|"
    r"forward[- ]deployed|implementation (engineer|consultant|specialist|manager|lead)|professional services|"
    r"business develop\w*|recruit(er|ing)|talent acquisition|account (executive|manager|director)|"
    r"marketing (manager|specialist|lead|director|executive)|legal counsel|people partner|hr (business )?partner|"
    r"developer (relations|advocate)|devrel|evangelist|technical writer)\b", _I)
# Other engineering disciplines: excluded unless the title also says software.
DISCIPLINE = re.compile(
    r"\b(field|service|mechanical|electrical|civil|chemical|process|manufacturing|industrial|structural|hvac|"
    r"facilit(y|ies)|maintenance|construction|biomedical|clinical|controls?|inspection|automotive|fuel|"
    r"commissioning|design release|power|energy|propulsion|aerospace|avionics|mining|petroleum|"
    r"hardware|electronics|rf|analog|pcb|asic|fpga|optical|photonics|validation|test technician|"
    r"network|telecom|production|plant|site(?! reliability)|environmental|geotechnical|hse|"
    r"(?<!ai )(?<!trust )(?<!trust & )(?<!trust and )safety (engineer|system|manager)|"
    r"(systems?|functional|product|flight|vehicle|battery|process|fire|occupational|machine) safety|"
    r"netzwerk|elektronik|elektrotechnik|mechanik|mechatronik|maschinenbau|konstruktion|fertigung|produktion|"
    r"verfahrenstechnik|versorgungstechnik|automatisierungstechnik|bau)\b", _I)
# German, Italian, French, Spanish and Dutch nouns too; gender endings (Entwickler*in, EntwicklerIn,
# Développeur·se, Desarrollador/a) leave the stem matched.
NOUNS = (r"engineers?|developers?|programmers?|swe|sdet|architects?|entwickler(in|innen)?|programmierer(in)?|"
         r"architekt(in)?|sviluppat(ore|rice|ori)|programmat(ore|rice)|d[ée]veloppeu(r|se)s?|programmeu(r|se)s?|"
         r"desarrollador(a|es)?|programador(a|es)?|ontwikkelaars?")
ENGINEER = re.compile(rf"\b({NOUNS})\b", _I)
SOFTWARE_WORD = re.compile(
    r"\b(software|backend|back[- ]end|frontend|front[- ]end|full[- ]?stack|web|mobile|ios|android|platform|"
    r"infrastructure|cloud|devops|devsecops|site reliability|reliability|sre|security|appsec|application|"
    r"embedded|firmware|game|gameplay|graphics|compiler|kernel|blockchain|smart contracts?|qa|test automation|"
    r"api|python|java|golang|go|rust|react|node(\.js)?|ruby|rails|php|sap|abap|typo3|flutter|logiciel|hpc|"
    r"typescript|javascript|scala|kotlin|swift|unity|unreal|distributed|devex|developer experience|"
    r"observability|ai|llm|founding)\b"
    # generic words count only right before the noun: "Product Engineer", not "Product Cost Engineer"
    r"|\b(product|growth|performance|tooling|build|release|integrations?) (engineer|developer)s?\b|\bsystems? (engineer|developer|architect)s?\b"
    r"|(?<![\w+#.])(c\+\+|c#|\.net)(?![\w+#])", _I)
# The noun alone, with seniority words, then the end or a separator: "Senior Engineer", "Engineer - Payments",
# "Entwickler (m/w/d)"; not "Engineer Fitting", where engineer qualifies another word.
BARE = re.compile(r"^\W*((senior|sr\.?|staff|principal|lead|junior|jr\.?|mid[- ]level|associate)\s+)*"
                  r"(engineer|developer|entwickler(in)?|sviluppatore|d[ée]veloppeur|desarrollador|ontwikkelaar)"
                  r"(s|\*in|:in|/in|_in|/a|\(in\)|·se|\(se\)|\.euse)?(\s+([ivx]{1,3}|\d))?"
                  r"\s*($|[,(\-–—|:/]|[mfwdhx]/[mfwdhx]\b)", _I)
# One-word German compounds: Softwareentwickler, Fullstack-Entwickler:in, Software-Architekt, Softwareentwicklung
COMPOUND = re.compile(
    r"\b(software|backend|back-end|frontend|front-end|full-?stack|web|app|apps|anwendungs|applikations|mobile|"
    r"ios|android|java|python|php|spiele|game|datenbank|embedded|cloud|sap|abap|typo3|ki|devops|plattform|"
    r"platform|net)-?(entwickler|architekt|ingenieur)|\b(software|anwendungs)-?entwicklung\b", _I)

RULES: list[tuple[str, re.Pattern]] = [
    ("engineering_management", re.compile(
        r"\b(engineering manager|manager,? (software )?engineering|head of (software )?engineering|"
        r"(vp|vice president|director|svp),? (of )?(software )?engineering|cto|chief technology officer|"
        r"head of (platform|infrastructure|data engineering|ml|ai|machine learning)|engineering lead|"
        r"(software|platform|data|ml|infrastructure) engineering (team )?(manager|lead|director)|"
        r"(director|head|vp|vice president)\b[^|]{0,40}\bengineering|"
        r"(head|director|vp|team ?lead|manager),? (of )?(software development|development(?=\s*($|[,(\-–—|:])))|"
        r"(development|developer|dev) team (lead|manager)|"
        r"(team ?leiter(in)?|team ?leitung|leiter(in)?|leitung|abteilungsleiter(in)?|bereichsleiter(in)?|head of)"
        r"\b[^|]{0,30}(software-?entwicklung|anwendungsentwicklung|(software|mobile|web|app|backend|frontend) "
        r"development|it-entwicklung))\b", _I)),
    ("data_science", re.compile(r"\b(quantitative|quant) (researcher|analyst|developer)\b", _I)),
    ("product_management", re.compile(
        r"\b(product manager|product owner|head of product|(vp|vice president|director|svp),? (of )?product|"
        r"chief product officer|cpo|group product manager|product lead|technical program manager|"
        r"product management)\b", _I)),
    ("research", re.compile(
        r"\b(research (scientist|engineer|lead)|researcher|applied scientist|member of technical staff|"
        r"research fellow|(ml|ai|machine learning) (research|scientist))\b", _I)),
    ("data_ml_engineering", re.compile(
        r"\b(data engineer|analytics engineer|(machine learning|ml|ai|llm|mlops|deep learning|nlp|"
        r"computer vision|genai|generative ai) (engineer|developer|ops)|mlops|data platform engineer|"
        r"big data|data architect|database (engineer|administrator)|dba)\b", _I)),
    ("data_science", re.compile(
        r"\b(data scientist|data science|decision scientist|quantitative (analyst|researcher)|"
        r"(product|data|business intelligence|bi) analyst|statistician|analytics (lead|manager))\b", _I)),
]
SOFTWARE_ONLY = re.compile(r"\b(sre|devops|software engineering|software development)\b", _I)
SOFTWARE_DE = re.compile(r"\bsoftware\b|software-?(entwickl|architekt|ingenieur)", _I)

SENIORITY: list[tuple[str, re.Pattern]] = [
    ("intern", re.compile(r"\b(intern|internship|praktikum|praktikant(in)?|working student|werkstudent(in)?|trainee|"
                         r"apprentice(ship)?|ausbildung|auszubildende[rn]?|azubi|stage|stagiaire|tirocinio|"
                         r"becario|pr[áa]cticas|stagiair)\b", _I)),
    ("executive", re.compile(r"\b(cto|cpo|chief|vp|vice president|svp|head of|director)\b", _I)),
    ("manager", re.compile(r"\b(manager|management)\b", _I)),
    ("principal", re.compile(r"\b(principal|staff|distinguished|fellow|architect)\b", _I)),
    ("lead", re.compile(r"\b(lead|tech lead)\b", _I)),
    ("senior", re.compile(r"\b(senior|sr\.?|snr)\b", _I)),
    ("junior", re.compile(r"\b(junior|jr\.?|graduate|entry[- ]level|associate)\b", _I)),
    ("mid", re.compile(r"\b(mid[- ]level|intermediate|medior|ii|iii)\b", _I)),
]


GUARDED = {"engineering_management"}   # generic enough that other disciplines can match them


def role_family(title: str) -> str | None:
    if not title or EXCLUDE.search(title):
        return None
    other_discipline = bool(DISCIPLINE.search(title)) and not SOFTWARE_DE.search(title)
    for family, pattern in RULES:
        if pattern.search(title):
            return None if family in GUARDED and other_discipline else family
    if other_discipline:
        return None
    if SOFTWARE_ONLY.search(title) or COMPOUND.search(title) or \
            (ENGINEER.search(title) and (SOFTWARE_WORD.search(title) or BARE.search(title))):
        return "software_engineering"
    return None


def seniority(title: str) -> str | None:
    for level, pattern in SENIORITY:
        if pattern.search(title or ""):
            return level
    return None


# Stack terms: (tag, pattern). Case-sensitive where the word is also ordinary English.
STACK: list[tuple[str, re.Pattern]] = [(tag, re.compile(p, flags)) for tag, p, flags in [
    ("python", r"\bpython\b", _I), ("java", r"\bjava\b(?!\s*script)", _I),
    ("javascript", r"\bjavascript\b|\becmascript\b", _I), ("typescript", r"\btypescript\b", _I),
    ("go", r"(?i:\bgolang\b)|(?<![\w.-])Go(?![\w-]|\.\w)(?!\s+(?i:to|for|beyond|ahead|further|live|big|the extra|fast|getter)\b)", 0),
    ("rust", r"(?<![\w-])Rust(?![\w-])", 0), ("c++", r"(?<![\w+])C\+\+(?!\w)", 0),
    ("c#", r"(?<![\w#])C#(?![\w#])", 0), (".net", r"(?<!\w)\.NET\b", 0),
    ("kotlin", r"\bkotlin\b", _I), ("swift", r"(?<![\w-])Swift(?![\w-])", 0), ("scala", r"\bscala\b", _I),
    ("ruby", r"\bruby\b", _I), ("rails", r"\bRuby on Rails\b|(?<![\w-])Rails(?![\w-])", 0),
    ("php", r"\bphp\b", _I), ("elixir", r"\belixir\b", _I), ("haskell", r"\bhaskell\b", _I),
    ("clojure", r"\bclojure\b", _I), ("r", r"\bR (programming|language)\b|\bRStudio\b|(?<=Python, )R\b|\bR(?=, Python)", 0),
    ("julia", r"(?<![\w-])Julia(?![\w-])(?= (language|programming|,))", 0), ("dart", r"\bdart\b", _I),
    ("solidity", r"\bsolidity\b", _I), ("sql", r"(?<![\w-])SQL(?![\w-])", 0),
    ("react", r"(?<![\w-])React(\.js)?(?![\w-])", 0), ("react native", r"\bReact Native\b", 0),
    ("vue", r"\bvue(\.js)?\b", _I), ("angular", r"\bangular(js)?\b", _I), ("svelte", r"\bsvelte\b", _I),
    ("next.js", r"\bnext\.js\b|\bnextjs\b", _I), ("node.js", r"\bnode(\.js|js)\b|\bNode\b", 0),
    ("django", r"\bdjango\b", _I), ("flask", r"(?<![\w-])Flask(?![\w-])", 0), ("fastapi", r"\bfastapi\b", _I),
    ("spring", r"\bspring (boot|framework)\b", _I), ("flutter", r"\bflutter\b", _I),
    ("pytorch", r"\bpytorch\b", _I), ("tensorflow", r"\btensorflow\b", _I), ("jax", r"(?<![\w-])JAX(?![\w-])", 0),
    ("spark", r"\b(apache )?spark\b|\bpyspark\b", _I), ("kafka", r"\bkafka\b", _I), ("airflow", r"\bairflow\b", _I),
    ("dbt", r"(?<![\w-])dbt(?![\w-])", 0), ("snowflake", r"(?<![\w-])Snowflake(?![\w-])", 0),
    ("bigquery", r"\bbigquery\b", _I), ("databricks", r"\bdatabricks\b", _I),
    ("aws", r"(?<![\w-])AWS(?![\w-])|\bamazon web services\b", _I), ("gcp", r"(?<![\w-])GCP(?![\w-])|\bgoogle cloud\b", _I),
    ("azure", r"\bazure\b", _I), ("kubernetes", r"\bkubernetes\b|\bk8s\b", _I), ("docker", r"\bdocker\b", _I),
    ("terraform", r"\bterraform\b", _I), ("postgresql", r"\bpostgres(ql)?\b", _I), ("mysql", r"\bmysql\b", _I),
    ("mongodb", r"\bmongo(db)?\b", _I), ("redis", r"\bredis\b", _I), ("elasticsearch", r"\belasticsearch\b", _I),
    ("graphql", r"\bgraphql\b", _I), ("grpc", r"\bgrpc\b", _I), ("linux", r"\blinux\b", _I),
    ("llm", r"\bLLMs?\b|\blarge language models?\b", 0),
]]


def stack(text: str) -> list[str]:
    tags = [tag for tag, pattern in STACK if pattern.search(text or "")]
    if "react native" in tags and not re.search(r"(?<![\w-])React(?! Native)(\.js)?(?![\w-])", text or ""):
        tags.remove("react")
    return tags
