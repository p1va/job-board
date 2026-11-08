import pytest

from jobboard.classify import role_family, seniority, stack

FAMILIES = [
    ("Senior Software Engineer, Payments", "software_engineering"),
    ("Founding Engineer", "software_engineering"),
    ("Senior Engineer", "software_engineering"),
    ("Staff Engineer (Backend) - Platform", "software_engineering"),
    ("Frontend Developer (React)", "software_engineering"),
    ("Site Reliability Engineer", "software_engineering"),
    ("Software Quality Engineer", "software_engineering"),
    ("Engineering Manager", "engineering_management"),
    ("Head of Engineering", "engineering_management"),
    ("Director, Inference Engineering", "engineering_management"),
    ("Product Manager, Growth", "product_management"),
    ("Research Engineer, Pretraining", "research"),
    ("Member of Technical Staff", "research"),
    ("ML Scientist, Protein Design", "research"),
    ("Machine Learning Engineer", "data_ml_engineering"),
    ("Senior Data Engineer - Clinical Informatics", "data_ml_engineering"),   # discipline word, still data
    ("Analytics Engineer", "data_ml_engineering"),
    ("Data Scientist", "data_science"),
    ("Quantitative Researcher", "data_science"),
    # review round 1 (phase 3a)
    ("C++ Engineer", "software_engineering"), ("C# Developer", "software_engineering"),
    (".NET Developer", "software_engineering"), ("Software Engineering Intern", "software_engineering"),
    ("Werkstudent Software Engineering", "software_engineering"),
    ("Software Engineering Team Lead", "engineering_management"),
    ("Senior Software Engineer, Customer Identity", "software_engineering"),
    ("Software Engineer - Finance", "software_engineering"),
    ("Customer Success Engineer", None), ("Technical Support Engineer", None),
    ("Product Engineer", "software_engineering"), ("Product Cost & Sourcing Engineer", None),
    ("Growth Engineer", "software_engineering"), ("Release Engineer", "software_engineering"),
    # other languages (Personio, Workable): German compounds and gender endings, Spanish, French, Italian
    ("Senior Softwareentwickler*in (m/w/d) – C#", "software_engineering"),
    ("Senior Backend Entwickler:in (d/m/w)", "software_engineering"),
    ("Fullstack-Entwickler (m/w/d) – Schwerpunkt UI", "software_engineering"),
    ("TYPO3-Entwickler:in (d/m/w)", "software_engineering"), ("Software-Architekt*in (m/w/d)", "software_engineering"),
    ("Entwickler (m/w/d) Java", "software_engineering"), ("Senior SAP IS-U ABAP Entwickler*in (m/w/d)", "software_engineering"),
    ("Softwareentwicklung - .NET (C# / VB.NET) mit Kundenprojekten", "software_engineering"),
    ("Teamleiter Softwareentwicklung C++ - Mobile Security (w/m/d)", "engineering_management"),
    ("Teamleiter Mobile Development - Flutter (w/m/d)", "engineering_management"),
    ("Ingénieur logiciel", "software_engineering"), ("Ingénieur(e) Développement Java", "software_engineering"),
    ("Ingegnere del Software", "software_engineering"), ("Ingeniera de Software Senior", "software_engineering"),
    ("Desarrollador/a Senior Full Stack", "software_engineering"), ("Développeur Fullstack H/F", "software_engineering"),
    ("Développeur(se) logiciel principal", "software_engineering"), ("Sviluppatore Backend Java", "software_engineering"),
    ("Senior Developer m/w/d", "software_engineering"), ("Entwickler (m/w/d)", "software_engineering"),
    ("Hardwareentwickler - Elektronik", None), ("Entwickler Elektronik (m/w/d)", None),
    ("Leiter Standortentwicklung & Immobilienprojekte", None), ("Projektentwickler Immobilien (m/w/d)", None),
    # "Engineer" alone counts only as the whole noun, with levels and separators after it
    ("Engineer I, Payments Engineering", "software_engineering"), ("Senior Engineer II, Chromium", "software_engineering"),
    ("Engineer Fitting", None), ("Developer Success Engineer", None),
    # "systems" only right before the noun; safety engineering is another discipline, AI or trust safety isn't
    ("Systems Engineer", "software_engineering"), ("Senior System Architect", "software_engineering"),
    ("Senior Safety Engineer - Loitering Munition Systems", None), ("(Senior) Systems Safety Engineer - Air", None),
    ("Staff Engineer, Full Stack (Trust, Safety & Risk)", "software_engineering"),
    ("Senior Security Engineer (AI Safety)", "software_engineering"), ("Senior Systems HPC Engineer", "software_engineering"),
    ("IT-Netzwerk Engineer (m/w/d) – Netzwerk & Security", None),
    # development leadership only with a software word: bare "Director of Development" is often fundraising
    # or real estate
    ("Head of Software Development", "engineering_management"), ("Development Team Lead", "engineering_management"),
    ("Director of Development", None), ("Head of Development", None),
    ("Head of Development - Real Estate", None), ("VP Development - Fundraising", None),
    ("Director of Development (Philanthropy)", None),
    ("Director, Development Cost Management & Capital Planning EMEA", None),
    # never kept
    ("Sales Engineer", None), ("Solutions Engineer", None), ("Forward Deployed Engineer", None),
    ("Senior Mechanical Engineer", None), ("Controls Engineer II", None), ("Electrical Engineer Intern", None),
    ("Manufacturing Engineering Manager", None), ("Business Developer", None),
    ("Developer Advocate", None), ("Account Executive", None), ("", None),
]


@pytest.mark.parametrize("title,family", FAMILIES)
def test_role_family(title, family):
    assert role_family(title) == family


@pytest.mark.parametrize("title,level", [
    ("Working Student Software Engineering", "intern"), ("Senior Backend Engineer", "senior"),
    ("Staff Engineer", "principal"), ("Engineering Manager", "manager"), ("VP Engineering", "executive"),
    ("Junior Developer", "junior"), ("Backend Engineer", None),
    ("Ausbildung Fachinformatiker*in Anwendungsentwicklung (m/w/d)", "intern"),
    ("Werkstudentin Softwareentwicklung", "intern"), ("Stage Développeur Python", "intern"),
    ("Stage de fin d'études - Data Engineer", "intern"),
    ("Senior Software Engineer - Early-Stage Startup", "senior"), ("Stage Data Analyst", "intern"),
    ("Senior Software Engineer - Stage Lighting", "senior"), ("Staff Engineer, Multi-Stage Pipelines", "principal")])
def test_seniority(title, level):
    assert seniority(title) == level


@pytest.mark.parametrize("text,present,absent", [
    ("We use Java and Kotlin.", {"java", "kotlin"}, {"javascript"}),
    ("Strong JavaScript and TypeScript.", {"javascript", "typescript"}, {"java"}),
    ("Services in Go and Rust.", {"go", "rust"}, set()),
    ("Golang microservices", {"go"}, set()),
    ("We go the extra mile. Go Big. Go to market fast.", set(), {"go"}),
    ("We're Go.Compare, part of Future PLC.", set(), {"go"}),
    ("C++ and C# experience", {"c++", "c#"}, set()),
    ("React Native apps", {"react native"}, {"react"}),
    ("React and React Native", {"react", "react native"}, set()),
    ("PostgreSQL, Kafka on Kubernetes (k8s)", {"postgresql", "kafka", "kubernetes"}, {"sql"}),
    ("Spring Boot services; spring offsite", {"spring"}, set()),
    ("We react quickly to change", set(), {"react"}),
])
def test_stack(text, present, absent):
    tags = set(stack(text))
    assert present <= tags and not (absent & tags), tags
