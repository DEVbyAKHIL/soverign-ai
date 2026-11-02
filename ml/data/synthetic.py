"""Seeded synthetic data. No real people, accounts or credentials.

* `pii_documents(n, seed)`        -> texts with gold entity spans (detector evaluation)
* `negative_documents(n, seed)`   -> PII-free texts incl. near-misses (false-positive rate)
* `sensitivity_corpus(n, seed)`   -> (text, label) pairs for the sensitivity classifier

Card numbers use test prefixes (4111/5555/...); IBANs/SSNs/NINOs are generated to be *format*-
valid so validators are exercised, but are random and not issued to anyone.
"""

import random
import string
from dataclasses import dataclass

FIRST = [
    "John",
    "Maria",
    "Ahmed",
    "Chen",
    "Priya",
    "Lukas",
    "Sofia",
    "Kwame",
    "Elena",
    "Tomás",
    "Aisha",
    "Hiroshi",
    "Fatima",
    "Oliver",
    "Ingrid",
    "Mateo",
    "Amara",
    "Dmitri",
    "Chloe",
    "Ravi",
]
LAST = [
    "Müller",
    "García",
    "Nguyen",
    "Okafor",
    "Sharma",
    "Schmidt",
    "Rossi",
    "Kowalski",
    "Dubois",
    "Andersson",
    "O'Brien",
    "Tanaka",
    "Haddad",
    "Fernández",
    "Johansson",
    "Mensah",
    "Novak",
    "Patel",
]
CITIES = [
    "Berlin",
    "Paris",
    "Madrid",
    "Amsterdam",
    "Lisbon",
    "Vienna",
    "Dublin",
    "Warsaw",
    "Prague",
    "Stockholm",
    "London",
    "Munich",
    "Milan",
    "Copenhagen",
]
COMPANIES = [
    "Acme Logistics",
    "Northwind Traders",
    "Globex Corporation",
    "Initech",
    "Umbrella Health",
    "Contoso Bank",
    "Fabrikam Industries",
    "Tailspin Toys",
]
STREETS = ["Baker Street", "High Street", "Elm Road", "Maple Avenue", "Station Road", "Church Lane"]
DE_STREETS = ["Hauptstraße", "Bahnhofstraße", "Lindenweg", "Schillerplatz", "Goetheallee"]
DOMAINS = ["example.com", "example.org", "mail.example.net", "corp.example.eu"]
MEDICAL = ["diabetes", "hypertension", "depression", "asthma", "epilepsy", "hepatitis", "cancer"]
PROJECTS = ["Project Nightingale", "Project Atlas-X", "Bluefin Initiative", "Operation Northwind"]


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    entity_type: str


@dataclass(frozen=True)
class Doc:
    text: str
    spans: tuple[Span, ...]


# --- value generators ----------------------------------------------------


def _luhn_complete(prefix: str, length: int, rng: random.Random) -> str:
    body = prefix + "".join(rng.choice(string.digits) for _ in range(length - len(prefix) - 1))
    for check in string.digits:
        digits = body + check
        total = sum(
            (d * 2 - 9 if d * 2 > 9 else d * 2) if i % 2 else d
            for i, d in enumerate(int(c) for c in reversed(digits))
        )
        if total % 10 == 0:
            return digits
    raise AssertionError("unreachable")


def card(rng: random.Random) -> str:
    raw = _luhn_complete(rng.choice(["4111", "4000", "5555", "5105", "6011"]), 16, rng)
    sep = rng.choice([" ", "-", ""])
    return sep.join(raw[i : i + 4] for i in range(0, 16, 4))


def iban(rng: random.Random) -> str:
    country, bban = rng.choice(
        [("DE", "".join(rng.choices(string.digits, k=18))),
         ("GB", "".join(rng.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=4)) + "".join(rng.choices(string.digits, k=14))),
         ("NL", "".join(rng.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=4)) + "".join(rng.choices(string.digits, k=10))),
         ("FR", "".join(rng.choices(string.digits, k=23)))]
    )  # fmt: skip
    numeric = "".join(str(int(c, 36)) for c in bban + country + "00")
    check = f"{98 - int(numeric) % 97:02d}"
    raw = country + check + bban
    return " ".join(raw[i : i + 4] for i in range(0, len(raw), 4)) if rng.random() < 0.5 else raw


def ssn(rng: random.Random) -> str:
    area = rng.choice([n for n in range(1, 900) if n != 666])
    return f"{area:03d}-{rng.randint(1, 99):02d}-{rng.randint(1, 9999):04d}"


def nino(rng: random.Random) -> str:
    while True:  # BG, GB, NK, KN, TN, NT and ZZ are never allocated
        first = rng.choice("ABCEHJKLMNOPRSTWXY")
        second = rng.choice("ABCEHJKLMNPRSTWXY")
        if first + second not in {"BG", "GB", "NK", "KN", "TN", "NT", "ZZ"}:
            break
    return f"{first}{second}{rng.randint(10, 99)}{rng.randint(10, 99)}{rng.randint(10, 99)}{rng.choice('ABCD')}"


def phone(rng: random.Random) -> str:
    import phonenumbers

    for _ in range(200):
        cc, region, n = rng.choice(
            [("44", "GB", 10), ("49", "DE", 10), ("33", "FR", 9), ("1", "US", 10)]
        )
        number = "".join(rng.choices(string.digits, k=n))
        parsed = phonenumbers.parse(f"+{cc}{number}", None)
        if phonenumbers.is_valid_number(parsed):
            fmt = rng.choice(
                [phonenumbers.PhoneNumberFormat.INTERNATIONAL, phonenumbers.PhoneNumberFormat.E164]
            )
            return phonenumbers.format_number(parsed, fmt)
    raise RuntimeError("could not generate a valid phone number")


def api_key(rng: random.Random) -> str:
    alnum = string.ascii_letters + string.digits
    return rng.choice(
        [
            "sk-proj-" + "".join(rng.choices(alnum, k=40)),
            "AKIA" + "".join(rng.choices(string.ascii_uppercase + string.digits, k=16)),
            "ghp_" + "".join(rng.choices(alnum, k=36)),
        ]
    )


def password(rng: random.Random) -> str:
    return "".join(
        rng.choices(string.ascii_letters + string.digits + "!#%&*", k=rng.randint(8, 16))
    )


def person(rng: random.Random) -> str:
    return f"{rng.choice(FIRST)} {rng.choice(LAST)}"


def email(rng: random.Random) -> str:
    return (
        f"{rng.choice(FIRST).lower()}.{rng.choice(LAST).lower().replace(chr(39), '')}{rng.randint(1, 99)}@{rng.choice(DOMAINS)}".replace(
            "ü", "ue"
        )
        .replace("í", "i")
        .replace("á", "a")
    )


def dob(rng: random.Random) -> str:
    y, m, d = rng.randint(1945, 2005), rng.randint(1, 12), rng.randint(1, 28)
    return rng.choice([f"{d:02d}/{m:02d}/{y}", f"{y}-{m:02d}-{d:02d}", f"{d} March {y}"])


def address(rng: random.Random) -> str:
    if rng.random() < 0.5:
        return f"{rng.randint(1, 250)} {rng.choice(STREETS)}"
    return f"{rng.choice(DE_STREETS)} {rng.randint(1, 120)}"


def amount(rng: random.Random) -> str:
    return rng.choice(
        [
            f"€{rng.randint(100, 99000):,}",
            f"${rng.randint(100, 99000):,}.00",
            f"{rng.randint(100, 9000)} EUR",
        ]
    )


# --- PII documents ---------------------------------------------------------
# Each template: (text with {slots}, {slot: (generator, entity_type or None)})

G = {
    "name": (person, "PERSON"), "email": (email, "EMAIL"), "phone": (phone, "PHONE"),
    "card": (card, "CREDIT_CARD"), "iban": (iban, "IBAN"), "ssn": (ssn, "NATIONAL_ID"),
    "nino": (nino, "NATIONAL_ID"), "key": (api_key, "API_KEY"), "pw": (password, "PASSWORD"),
    "dob": (dob, "DATE_OF_BIRTH"), "addr": (address, "ADDRESS"), "amt": (amount, "FINANCIAL"),
    "cond": (lambda r: r.choice(MEDICAL), "MEDICAL"),
    "proj": (lambda r: r.choice(PROJECTS), "INTERNAL_PROJECT"),
    "cust": (lambda r: f"CUST-{r.randint(10000, 999999)}", "CUSTOMER_ID"),
    "passport": (lambda r: r.choice("CKLMN") + "".join(r.choices(string.digits, k=8)), "PASSPORT"),
    "city": (lambda r: r.choice(CITIES), "LOCATION"),
}  # fmt: skip

TEMPLATES = [
    "Summarise this customer ticket: {name}, email {email}, balance {amt}.",
    "Please call {name} back on {phone} about the refund.",
    "Charge the order to card {card} and send the receipt to {email}.",
    "Wire the payment to IBAN {iban} before Friday.",
    "Employee record: {name}, SSN {ssn}, date of birth {dob}.",
    "My national insurance number is {nino}.",
    "Use api_key={key} to call the staging service.",
    "The admin password is {pw} so please keep it safe.",
    "Ship the parcel to {addr}, attention {name}.",
    "The patient was diagnosed with {cond} last year.",
    "Status of {proj}: the launch moved to Q3.",
    "Customer {cust} reported a billing issue; their salary is {amt}.",
    "Passport number {passport} expires next month.",
    "{name} lives in {city} and can be reached at {phone}.",
    "Forward the contract to {email} and copy {name}.",
]

NEGATIVE = [
    "The quarterly roadmap meeting is moved to Thursday afternoon.",
    "Python 3.12.4 fixed the bug; upgrade by running pip install -U.",
    "Our office closes at 6 pm on public holidays.",
    "The order contains 12 items and ships in 3-5 business days.",
    "Release v2.10.3 introduces a dark mode and faster search.",
    "ISBN 978-0-306-40615-7 is listed in the bibliography.",
    "Reference number 4111 1111 1111 1112 failed validation and was discarded.",
    "The meeting room on floor 4 seats 12 people.",
    "Please review the pull request before the standup.",
    "Weather today: sunny with a high of 24 degrees.",
    "The build failed because of a missing semicolon on line 42.",
    "Team lunch is on Friday; vote for your preferred cuisine.",
    "Version 4.5.6.7 of the firmware is now available.",
    "The project timeline includes three milestones and a retro.",
    "Download the user guide from the documentation portal.",
    "Store hours are 9 to 5, Monday to Saturday.",
]


def _fill(template: str, rng: random.Random) -> Doc:
    text, spans, pos = "", [], 0
    for literal, slot, _, _ in string.Formatter().parse(template):
        text += literal
        if slot:
            gen, etype = G[slot]
            value = gen(rng)
            spans.append(Span(len(text), len(text) + len(value), etype))
            text += value
        pos += 1
    return Doc(text, tuple(spans))


def pii_documents(n: int, seed: int = 1) -> list[Doc]:
    rng = random.Random(seed)  # noqa: S311 - synthetic data, not security-relevant
    return [_fill(rng.choice(TEMPLATES), rng) for _ in range(n)]


def negative_documents(n: int, seed: int = 2) -> list[Doc]:
    rng = random.Random(seed)  # noqa: S311
    return [Doc(rng.choice(NEGATIVE), ()) for _ in range(n)]


# --- sensitivity corpus ------------------------------------------------------

SENSITIVITY = {
    "public": [
        "We are excited to announce the launch of our new {product} for everyone.",
        "Our {product} documentation is available on the public website.",
        "Join our free webinar about {topic} next week.",
        "The {product} press release highlights improved performance.",
        "Read the blog post about best practices for {topic}.",
        "Opening hours for the visitor centre in {city} have been extended.",
        "Download the free trial of the {product} from our website today.",
        "Our annual public report on {topic} is now published online.",
        "Tickets for the open community meetup in {city} are free for everyone.",
        "The {product} user guide explains how to get started in five minutes.",
        "Follow us on social media for news about {topic}.",
        "The marketing brochure for the {product} is available to customers and the public.",
        "Public FAQ: how do I reset my {product} preferences?",
        "Our careers page lists open roles in {city}.",
        "The conference keynote on {topic} will be streamed publicly.",
        "Visit the {city} showroom to see the {product} in action.",
        "Newsletter: tips and tricks for {topic} this month.",
        "The changelog for the {product} is published on our public site.",
    ],
    "internal": [
        "Reminder: the team standup moves to 10:00 for the {topic} sprint.",
        "Internal note: the {product} retro is scheduled for Thursday.",
        "Please update your timesheets before the end of the month.",
        "The office kitchen in {city} will be cleaned on Friday.",
        "Draft agenda for the {topic} planning meeting is attached for the team.",
        "Internal wiki page for {product} onboarding was updated.",
        "Heads up team: the {city} office wifi password changes on Monday.",
        "Sprint review notes for the {product} squad are in the shared drive.",
        "Please book the meeting room for the {topic} workshop next week.",
        "The quarterly all-hands for employees will cover {topic}.",
        "Staff only: new expense policy applies from next month.",
        "Engineering guild meeting about {topic} moved to the {city} office.",
        "Team offsite logistics for the {product} group are being organised.",
        "Internal training on {topic} is mandatory for all engineers.",
        "The HR handbook for employees has a new section on remote work.",
        "Our internal status page shows the {product} staging environment is down.",
        "Coworkers in {city}: parking spaces are reassigned next week.",
        "Minutes from yesterday's team sync on {topic} are attached for colleagues.",
    ],
    "confidential": [
        "Confidential: the {company} contract renewal includes a discount of {pct} percent.",
        "Do not share: pricing proposal for {company} on the {product} deal.",
        "Customer churn analysis for {company} shows risk in the {topic} segment.",
        "The {company} negotiation strategy and margin targets are attached.",
        "Confidential roadmap: {product} will be discontinued next year.",
        "Customer list for the {city} region with contract values is attached.",
        "Confidential sales pipeline: {company} is likely to sign at a {pct} percent discount.",
        "Not for external distribution: competitive analysis of {company} and the {product}.",
        "The {company} account plan includes renewal dates and contract terms.",
        "Proprietary design document for the {product} architecture, internal and confidential.",
        "Commercial terms offered to {company} must stay within the account team.",
        "Confidential customer feedback from {company} about the {product} outage.",
        "Unreleased product strategy for {topic}; do not forward outside the company.",
        "Vendor quote from {company} with negotiated rates is confidential.",
        "Partner agreement draft with {company} under NDA.",
        "Revenue forecast by customer for the {city} territory, confidential.",
        "Source code for the {product} pricing algorithm is proprietary.",
        "Confidential: list of key accounts at risk including {company}.",
    ],
    "restricted": [
        "Restricted: salary bands and bonus payouts for the {topic} team are attached.",
        "Board minutes: acquisition of {company} for {amount} approved, strictly restricted.",
        "Production database credentials and root access keys rotate tonight.",
        "Security incident report: attacker accessed the {product} admin console.",
        "Payroll export with bank account details for all employees in {city}.",
        "Merger due diligence findings for {company} must not leave the deal team.",
        "Restricted access: private signing keys for the {product} release pipeline.",
        "Executive compensation and stock options for the leadership team.",
        "Unannounced layoffs affecting the {city} office; restricted to HR leadership.",
        "Penetration test findings with exploitable vulnerabilities in the {product}.",
        "Bank account numbers and routing details for supplier payments.",
        "Insider information: earnings miss for this quarter before public disclosure.",
        "Admin passwords and SSH keys for the {city} data centre.",
        "Legal hold: litigation strategy against {company} is privileged.",
        "Encryption master key backup procedure, restricted to security officers.",
        "Individual performance reviews and termination decisions for staff.",
        "Wire transfer authorisation codes for the treasury account.",
        "Restricted: firewall rules and VPN credentials for production.",
    ],
    "high_risk": [
        "Patient medical record: diagnosis of {condition} and prescribed medication.",
        "Criminal record check results for the applicant were received.",
        "Biometric fingerprint templates for all staff are stored in this export.",
        "Health insurance claim with psychiatric treatment history for the employee.",
        "Child safeguarding report including names and home situation of minors.",
        "Genetic test results indicate elevated risk of {condition}.",
        "Hospital discharge summary lists {condition} and current medications.",
        "Mental health counselling notes for the employee are attached.",
        "Passport scans and national identity numbers of all applicants.",
        "The employee disclosed HIV status in the occupational health form.",
        "Drug test results and rehabilitation history for the candidate.",
        "Sexual orientation and religious beliefs recorded in the survey responses.",
        "Trade union membership list for the {city} site.",
        "Pregnancy-related sick leave records for staff in {city}.",
        "Face recognition data captured at the {city} entrance.",
        "Lab report confirms {condition}; the patient needs follow-up.",
        "Social security numbers of all employees are in this spreadsheet.",
        "Disability assessment and treatment plan for the claimant.",
    ],
}
FILLERS = {
    "product": [
        "analytics platform",
        "mobile app",
        "billing service",
        "search engine",
        "API gateway",
    ],
    "topic": ["cloud migration", "data quality", "customer success", "observability", "security"],
    "city": CITIES,
    "company": COMPANIES,
    "pct": ["12", "18", "25", "30"],
    "amount": ["€40 million", "$120 million", "€2.3 billion"],
    "condition": MEDICAL,
}
NOISE = ["", " Thanks.", " Please review.", " See attached.", " Let me know if questions.", " FYI."]


def sensitivity_corpus(
    n_per_class: int, seed: int = 3, split: str = "all"
) -> list[tuple[str, str]]:
    """split: "train" = all but the last 3 templates per class, "heldout" = only those 3.

    Evaluating on held-out *templates* measures generalisation to unseen phrasing rather than
    memorisation of the generator.
    """
    rng = random.Random(seed)  # noqa: S311
    rows = []
    for label, all_templates in SENSITIVITY.items():
        templates = {
            "all": all_templates,
            "train": all_templates[:-3],
            "heldout": all_templates[-3:],
        }[split]
        for _ in range(n_per_class):
            t = rng.choice(templates)
            text = t.format(**{k: rng.choice(v) for k, v in FILLERS.items()}) + rng.choice(NOISE)
            rows.append((text, label))
    rng.shuffle(rows)
    return rows
