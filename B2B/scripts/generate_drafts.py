"""
Generate German cold-email drafts via Bridge (free Claude via Max sub).

For each lead in the batch file, calls the Bridge /generate-reply endpoint,
parses the JSON {subject, body} response, writes back to the Excel and
into the emails_sent table (with sent_at=NULL, i.e. 'Drafted' state).

Usage:
    python generate_drafts.py --file "<batch.xlsx>"
    python generate_drafts.py --file "<batch.xlsx>" --limit 1   (test on 1)
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import time
import pandas as pd
import requests

BRIDGE_URL = "http://127.0.0.1:8765/generate-reply"
DB = r'H:/Lead Generator/B2B/Database/Marcel Data/leads.db'

# Industry -> primary case study + hook for German SME owners.
INDUSTRY_CONTEXT = {
    "Health Care": {
        "case_study_de": (
            "Wir haben Videoreach gebaut — eine Plattform, die medizinische "
            "Protokolle automatisch in personalisierte Patienten-Videos "
            "umwandelt (Claude AI + Eleven Labs). Für einen anderen "
            "Healthcare-Kunden haben wir ein NLP-System entwickelt, das "
            "manuelle Dokumentenklassifikation um 70 % reduziert hat."
        ),
        "offer_de": "KI-gestützte Dokumenten- und Patientenkommunikation",
    },
    "Management Consulting": {
        "case_study_de": (
            "Wir haben kürzlich für eine Beratung ein RAG-System gebaut, "
            "das interne Dokumente und Reports KI-durchsuchbar macht — "
            "Berater bekommen Antworten in Sekunden statt Stunden."
        ),
        "offer_de": "KI-Assistent für Beratungswissen und Reporting",
    },
    "Commerce": {
        "case_study_de": (
            "Wir haben für einen Online-Händler ein System gebaut, das "
            "1 Mio+ Produkt- und Preisdaten pro Monat automatisch sammelt "
            "und einen KI-Chatbot, der 24/7 Kundenanfragen beantwortet."
        ),
        "offer_de": "Preisüberwachung, KI-Chatbot und E-Commerce-Automation",
    },
    "Manufacturing of Producer Goods": {
        "case_study_de": (
            "Wir haben ein Warehouse-Management-System auf Microservices-Basis "
            "entwickelt und RAG-Systeme, die technische Handbücher "
            "KI-durchsuchbar machen."
        ),
        "offer_de": "Produktionsdaten-Dashboard und KI für technische Dokumentation",
    },
    "Business Services": {
        "case_study_de": (
            "Wir haben Vektr entwickelt — eine Multi-Agent-KI-Plattform, die "
            "komplexe Geschäftsprozesse automatisiert, plus ein KI-System für "
            "Rechnungsverarbeitung mit Claude 3.5."
        ),
        "offer_de": "KI-Workflow-Automation und Dokumentenverarbeitung",
    },
    "Real Estate": {
        "case_study_de": (
            "Wir haben OpenHouseDirect gebaut — eine vollständige "
            "Immobilien-Plattform (Django, AngularJS) mit Portaleinbindungen, "
            "Such-UI und OAuth-Login."
        ),
        "offer_de": "Immobilien-Web-Plattform und KI-Anfragebot",
    },
    "Finance": {
        "case_study_de": (
            "Wir haben ein KYC-Verifikationssystem mit KI (YOLOv8) sowie "
            "Trading Wize — eine NextJS Fintech-Plattform gebaut."
        ),
        "offer_de": "KI-Dokumentenverifikation und individuelle Fintech-Tools",
    },
    "Media & Communications": {
        "case_study_de": (
            "Wir haben G4+ gebaut — eine hybride Social-Media-Plattform mit "
            "Live-Streaming, sowie KI-Content-Tools mit Claude und Eleven Labs."
        ),
        "offer_de": "KI-Content-Produktion und Media-Plattformen",
    },
}

DEFAULT_CONTEXT = {
    "case_study_de": (
        "Wir haben in den letzten Jahren 150+ Projekte weltweit umgesetzt — "
        "von KI-Agenten (Claude, OpenAI, LangChain) bis zu individuellen "
        "Web- und Datenlösungen."
    ),
    "offer_de": "KI-Automation und individuelle Software",
}

SYSTEM_PROMPT = """Du bist ein erfahrener deutscher B2B-Cold-Email-Texter (Niveau von Josh Braun oder Jason Bay auf Deutsch). Du arbeitest für Pradip Kachhadiya bei BitCoding Solutions, einer Software-Entwicklungs-Firma aus Indien. Du schreibst EINE Email an EINEN Menschen.

DEIN ANSPRUCH:
Jede Email ist handgeschrieben. Nie wirkt sie wie aus einer Vorlage. Jede Email unterscheidet sich in LÄNGE, STRUKTUR, HOOK, TONFALL und CTA von jeder anderen Email in derselben Batch. Ein Leser, der 5 deiner Emails hintereinander sähe, würde denken, sie kommen von 5 verschiedenen Absendern.

SPRACHE (EINFACH, KEIN TECH-JARGON):
Verbotene Wörter: "Automation", "Prozessautomation", "KI", "künstliche Intelligenz", "Dashboard", "API", "digitalisieren", "skalieren", "disruptiv", "State-of-the-Art", "Lösung", "revolutionär", "bahnbrechend", "innovativ".
Erlaubte Wörter: "Software", "Programm", "Tool", "Anwendung", "Zeit sparen", "Abläufe einfacher machen", "Hilfe".

ÜBER UNS (OPTIONAL, NICHT IN JEDER EMAIL):
BitCoding Solutions, Software-Firma aus Indien, 30+ Entwickler, seit 2018, über 150 Projekte.
Diese Stats NUR in maximal 1 von 3 Emails erwähnen. In den meisten Emails signierst du einfach als "Pradip von BitCoding" und fertig. Statistiken sind langweilig, neugierig-machen ist das Ziel.

KEIN FEATURE VORSCHLAGEN:
Nie "Sowas wie [Feature] wäre denkbar". Der Leser muss sagen, was er braucht, nicht du.

VERBOTEN:
- Gedankenstriche (— oder –). Komma oder Punkt benutzen.
- "Ich hoffe, es geht Ihnen gut", "Darf ich mich kurz vorstellen", "ich möchte Ihnen mitteilen", "lassen Sie mich kurz erklären".
- Signatur im Body. (Die wird extern angefügt.)
- Aufgezählte Listen mit "und" ("effizient, schnell und zuverlässig").
- Superlative und Buzzwords.

ARCHETYPEN (IMMER EINEN WÄHLEN, wird dir pro Email vorgegeben):
A) ONE-LINER-FRAGE (15-25 Wörter): Kurze, direkte, menschliche Frage. Kein Pitch, nur Neugier.
   Beispiel: "Sehr geehrter Herr X, kurze Frage: läuft bei [Firma] aktuell irgendein Ablauf, der zu viel Zeit frisst? Wenn ja, würde mich interessieren, was. Grüße, Pradip"

B) PATTERN-INTERRUPT (30-50 Wörter): Gib zu, dass es eine Kaltakquise-Mail ist. Humor oder Ehrlichkeit. Dann eine sehr konkrete, relevante Frage.
   Beispiel: "Sehr geehrter Herr X, vermutlich bekommen Sie solche Mails oft, daher halte ich es kurz. Gibt es in [Firma/Stadt] aktuell einen Prozess, den eine kleine Software deutlich einfacher machen könnte? Neugierige Grüße, Pradip"

C) BEOBACHTUNG + FRAGE (25-40 Wörter): EINE konkrete Beobachtung über die Firma/Branche/Stadt, dann eine Ja-Nein-Frage.
   Beispiel: "Sehr geehrte Frau Y, [Praxisname] in Berlin kombiniert Physio und Prävention, ein spannendes Setup. Nutzen Sie dafür fertige Software oder würde ein eigenes Tool mehr Sinn machen? Viele Grüße, Pradip"

D) NAME-PLAY (40-60 Wörter): Riff auf den Firmennamen oder Tagline. Dann Pivot zum Arbeitsalltag.
   Beispiel: "Sehr geehrte Frau X, 'leicht-er-leben' als Praxisname, schön auf den Punkt. Bei Praxen, die Prävention ernst nehmen, sehe ich oft Software-Workflows, die nicht zum Konzept passen. Wir bei BitCoding machen so etwas. Wäre ein kurzer Austausch spannend? Grüße, Pradip"

E) PAIN + SOFT CTA (40-65 Wörter): Benenne einen typischen Pain für diesen Geschäftstyp (nicht spezifisch raten, nur allgemein anerkennen), frage, ob es zutrifft.
   Beispiel: "Sehr geehrter Herr X, in vielen Apotheken höre ich dasselbe: im Tagesgeschäft geht zu viel Zeit für Kleinkram drauf, den eigentlich niemand macht. Trifft das bei Ihnen auch zu? Wir entwickeln bei BitCoding Software für genau solche Stellen. Grüße, Pradip"

F) MINI-CASE (50-75 Wörter): EIN konkretes, einfach formuliertes Beispiel-Projekt. Das Projekt muss zum Geschäft sanft passen.
   Beispiele:
   - Termin-Tool für eine Arztpraxis
   - Bestandsübersicht für einen Händler
   - internes Berichts-System für eine Beratung
   - Online-Plattform für Immobilien
   - Auftragsverwaltung für einen Handwerksbetrieb
   Schreibe EIN solches Beispiel als Einzelsatz. Keine Zahlen erfinden. Dann kurze Frage.

G) DIRECT ASK (20-35 Wörter): Keine Vorstellung, keine Referenz, kein Smalltalk. Nur direkte Frage + Signatur.
   Beispiel: "Sehr geehrter Herr X, arbeiten Sie mit fertiger Software oder wäre ein maßgeschneidertes Tool für [Sub-Branche] bei [Firma] interessant? Antwort genügt mit Ja oder Nein. Grüße, Pradip (BitCoding Solutions)"

H) LONG CONTEXT (75-90 Wörter, nur wenn Lead technisch ist): Etwas mehr Kontext, eine allgemeine Beobachtung zur Branche, kurze Ein-Satz-Referenz, offene Frage.

FORMAT:
- Zuerst immer Anrede: "Sehr geehrter Herr [Nachname]," oder "Sehr geehrte Frau [Nachname],"
- Dann Body gemäß gewähltem Archetyp.
- KEIN Abschluss/keine Signatur im Body. Die wird separat angefügt.
- Subject: max. 55 Zeichen. Muss ANDERS sein als die avoid-list. Keine Ausrufezeichen.

AUSGABE (strict): nur JSON, kein Markdown, kein Code-Block.
{"subject": "...", "body": "..."}

Im body: "\\n\\n" zwischen Anrede und dem Rest. Bei längeren Emails "\\n\\n" zwischen Absätzen.
"""


def german_salutation(row):
    sal = (row.get('salutation') or '').strip().lower()
    name = (row.get('name') or '').strip()
    last = name.split()[-1] if name else ''
    if sal.startswith('mr'):
        return f"Sehr geehrter Herr {last}"
    if sal.startswith('ms') or sal.startswith('mrs'):
        return f"Sehr geehrte Frau {last}"
    return f"Guten Tag {name}"


FOLLOWUP_PROMPT = """Du schreibst eine DEUTSCHE Follow-up-Email. Der Empfänger hat auf unsere erste Cold-Email NICHT geantwortet. Du bist Pradip von BitCoding (Software-Firma aus Indien). Diese Mail ist ein EXTREM kurzes "Bring wir das nochmal hoch"-Ping.

HARTE REGELN:
1. SEHR KURZ: maximal 3 Sätze, unter 40 Wörter insgesamt. Ernsthaft kurz.
2. Beziehe dich beiläufig auf die erste Mail ("kurz nochmal" / "falls meine Mail letzte Woche untergegangen ist").
3. Eine einzige Frage, sehr einfach zu beantworten (Ja/Nein/später).
4. KEIN neuer Pitch. KEINE neue Referenz. KEINE Wiederholung der ersten Mail.
5. Keine Entschuldigungen ("falls ich störe..."). Keine "Bumping this up"-Floskeln ins Englische.
6. Kein em-dash (— oder –). Keine Buzzwords. Keine Signatur im Body.
7. Anrede weglassen oder minimal: "Herr [Nachname]," — lockerer als erste Mail.
8. Subject: ganz kurz (<40 Zeichen). Oft mit "Re:" davor, oder völlig neu. Anders als das bisherige Subject.

STIL-BEISPIELE (nur als Vorlage, nicht kopieren):
- "Herr Jürgens, falls meine Nachricht von letzter Woche untergegangen ist, ganz kurz: passt so ein Gespräch gerade überhaupt? Ein Nein ist auch völlig okay."
- "Kurzes Nachhaken, Herr Grunewald. Ist das bei Gruneworld aktuell ein Thema oder eher nicht? Ein Wort genügt."

AUSGABE (strict): nur JSON, kein Markdown.
{"subject": "...", "body": "..."}
"""

BREAKUP_PROMPT = """Du schreibst eine DEUTSCHE "Breakup"-Email. Zweite Follow-up an jemanden, der weder auf erste Cold-Mail noch auf Follow-up geantwortet hat. Ton: freundlich abschließend, nicht passiv-aggressiv.

HARTE REGELN:
1. Extrem kurz: 2-3 Sätze, unter 40 Wörter.
2. Rahme es als "Ich schließe die Akte" — gib dem Empfänger die Erlaubnis, nicht zu antworten.
3. Eine Tür offen lassen ("melden Sie sich gern, falls sich was ergibt").
4. KEIN neuer Pitch. Keine Verkaufsargumente. Keine Dringlichkeit.
5. Kein em-dash. Keine Buzzwords. Keine Signatur im Body.
6. Subject: kurz, klar. Z.B. "Darf ich das Thema schließen?" oder "Letzte Mail von meiner Seite".

BEISPIELE (nicht kopieren, variieren):
- "Herr Jürgens, da ich nichts von Ihnen gehört habe, schließe ich das Thema von meiner Seite. Falls sich später doch was ergibt, melden Sie sich gern. Alles Gute."
- "Frau Sommer, ich nehme an, es passt gerade nicht. Kein Problem. Ich wünsche Ihnen weiterhin viel Erfolg mit leicht-er-leben."

AUSGABE: nur JSON, kein Markdown.
{"subject": "...", "body": "..."}
"""

ARCHETYPES = [
    ('A', 'ONE-LINER-FRAGE',       '15-25 Wörter. Eine einzige kurze Frage. Kein Pitch. Keine Firmenvorstellung.'),
    ('B', 'PATTERN-INTERRUPT',     '30-50 Wörter. Gib zu dass es Kaltakquise ist, dann eine sehr spezifische Frage.'),
    ('C', 'BEOBACHTUNG + FRAGE',   '25-40 Wörter. EINE konkrete Beobachtung + Ja/Nein-Frage.'),
    ('D', 'NAME-PLAY',             '40-60 Wörter. Spiel mit dem Firmennamen oder Tagline, dann weich zum Thema.'),
    ('E', 'PAIN + SOFT CTA',       '40-65 Wörter. Benenne typischen Pain der Branche allgemein, frag ob es zutrifft.'),
    ('F', 'MINI-CASE',             '50-75 Wörter. EIN einfach formuliertes Beispielprojekt, dann kurze Frage. Beispiel muss zum Geschäftstyp passen.'),
    ('G', 'DIRECT ASK',            '20-35 Wörter. Nur eine direkte Ja/Nein-Frage + Signatur. Keine Einleitung.'),
    ('H', 'LONG CONTEXT',          '75-90 Wörter. Kontext zur Branche, kurze Ein-Satz-Referenz, offene Frage.'),
]

CTA_STYLES = [
    'kurze Ja-Nein-Frage',
    'Frage nach 15 Minuten',
    'Frage nach einem kurzen Austausch',
    'Frage ob ein Thema ansteht',
    'weiche Einladung zu antworten',
    'explizite Erlaubnis nicht zu antworten wenn kein Fit',
    'Frage ob ich eine Kurzinfo schicken darf',
]

MENTION_STATS_PROB = 0.35  # only ~1/3 emails mention "30+ Entwickler / 150 Projekte"


def pick_archetype_for_lead(lead_id, batch_index=None):
    """When batch_index given, guarantee rotation across the batch."""
    if batch_index is not None:
        return ARCHETYPES[batch_index % len(ARCHETYPES)]
    h = int(hashlib.md5((lead_id or '').encode()).hexdigest(), 16)
    return ARCHETYPES[h % len(ARCHETYPES)]


def pick_cta_for_lead(lead_id, batch_index=None):
    if batch_index is not None:
        return CTA_STYLES[batch_index % len(CTA_STYLES)]
    h = int(hashlib.md5((f'cta:{lead_id}').encode()).hexdigest(), 16)
    return CTA_STYLES[h % len(CTA_STYLES)]


def should_mention_stats(lead_id, batch_index=None):
    if batch_index is not None:
        # Mention stats in ~30% of emails, rotating: every 3rd gets them
        return (batch_index % 3) == 1
    h = int(hashlib.md5((f'stats:{lead_id}').encode()).hexdigest(), 16)
    return (h % 100) < int(MENTION_STATS_PROB * 100)


def build_followup_user_prompt(row):
    name = row.get('name', '') or ''
    company = row.get('company', '')
    city = row.get('city', '') or ''
    ind = row.get('industry', '') or ''
    prior = row.get('prior_subjects', '') or ''
    salutation = german_salutation(row)
    return f"""Empfänger:
- Anrede: {salutation}
- Name: {name}
- Firma: {company} ({ind}, {city})

Bisherige Subjects zu dieser Person (NICHT wiederholen):
{prior if prior else '(keine)'}

Schreibe jetzt die Follow-up Email gemäß System-Anweisungen. Nur JSON."""


def build_breakup_user_prompt(row):
    name = row.get('name', '') or ''
    company = row.get('company', '')
    city = row.get('city', '') or ''
    ind = row.get('industry', '') or ''
    prior = row.get('prior_subjects', '') or ''
    salutation = german_salutation(row)
    return f"""Empfänger:
- Anrede: {salutation}
- Name: {name}
- Firma: {company} ({ind}, {city})

Bisherige Subjects zu dieser Person (NICHT wiederholen):
{prior if prior else '(keine)'}

Schreibe jetzt die Breakup-Email gemäß System-Anweisungen. Nur JSON."""


def build_user_prompt(row, avoid_subjects=None, batch_index=None):
    ind = row.get('industry', '') or ''
    ctx = INDUSTRY_CONTEXT.get(ind, DEFAULT_CONTEXT)

    company = row.get('company', '')
    sub_ind = row.get('sub_industry', '') or ''
    city = row.get('city', '') or ''
    title = row.get('title', '') or ''
    website = row.get('website', '') or row.get('domain', '') or ''
    name = row.get('name', '') or ''
    lead_id = row.get('lead_id', '') or name
    salutation = german_salutation(row)

    arch_code, arch_name, arch_spec = pick_archetype_for_lead(lead_id, batch_index)
    cta_style = pick_cta_for_lead(lead_id, batch_index)
    mention_stats = should_mention_stats(lead_id, batch_index)

    avoid_txt = ''
    if avoid_subjects:
        lines = '\n'.join(f'  - "{s}"' for s in avoid_subjects if s)
        avoid_txt = (
            "\nWICHTIG: Diese Subjects wurden in dieser Batch schon verwendet, "
            "wähle ein ANDERES Subject mit anderer Formulierung:\n" + lines + "\n"
        )

    stats_instr = (
        "In DIESER Email kannst du BitCoding-Stats (30+ Entwickler, seit 2018, 150+ Projekte) "
        "einmal beiläufig erwähnen, musst aber nicht."
        if mention_stats else
        "WICHTIG: In DIESER Email KEINE Stats erwähnen (keine '30+ Entwickler', kein '150 Projekte', kein 'seit 2018'). "
        "Signier einfach als 'Pradip von BitCoding Solutions' ohne Firmenvorstellung."
    )

    prompt = f"""Schreibe EINE Cold-Email für diesen einen Menschen:

EMPFÄNGER:
- Anrede: {salutation}
- Name: {name}
- Titel: {title}
- Firma: {company}
- Branche: {ind}
- Sub-Branche: {sub_ind or '(nicht angegeben)'}
- Stadt: {city}
- Website: {website or '(nicht angegeben)'}

ARCHETYP FÜR DIESE EMAIL (unbedingt einhalten):
  Code:   {arch_code}
  Name:   {arch_name}
  Spec:   {arch_spec}

CTA-STIL FÜR DIESE EMAIL:
  {cta_style}

FIRMENVORSTELLUNG:
  {stats_instr}

WICHTIG: Du darfst KEIN Feature raten. Nie "Sowas wie Bestandsverwaltung wäre denkbar" etc.
WICHTIG: Die Email muss sich so lesen, als hätte sie ein anderer Mensch geschrieben als andere Emails in dieser Batch. Wenn die gewählte Länge-Spec sagt 20 Wörter, dann schreib 20. Nicht 60.

OPTIONAL (nur wenn es zum Archetyp passt, F oder H): eine einzige Beispiel-Projekt-Referenz, EINFACH formuliert.
Mögliche Beispiele (sanft passend wählen, nicht wörtlich kopieren):
  - Termin-Tool für eine Arztpraxis
  - Bestandsübersicht für einen Händler
  - internes Berichts-System für eine Beratung
  - Online-Plattform für Immobilien
  - Auftragsverwaltung für einen Handwerksbetrieb
  - Website mit Online-Buchung für einen Dienstleister
{avoid_txt}
VERBOTEN: Gedankenstriche (— oder –), "Ich hoffe es geht Ihnen gut", Buzzwords, Signatur im Body, Feature raten.

FORMAT: Nur das JSON-Objekt {{"subject": "...", "body": "..."}} — keine Erklärung drumherum."""
    return prompt


def strip_signature(body):
    """Remove trailing signature blocks the LLM sometimes adds."""
    if not body:
        return body
    # Cut at common German signature starts
    patterns = [
        r'\n+Mit freundlichen Grüßen.*$',
        r'\n+Beste Grüße.*$',
        r'\n+Viele Grüße.*$',
        r'\n+Freundliche Grüße.*$',
        r'\n+(Neugierige )?Grüße[,.]?\s*\n?.*$',
        r'\n+Grüße aus .*$',
        r'\n+Pradip( Kachhadiya| von BitCoding)?.*$',
    ]
    for p in patterns:
        body = re.sub(p, '', body, flags=re.DOTALL | re.IGNORECASE)
    return body.rstrip()


def strip_dashes(s):
    """Remove em-dash and en-dash; replace with comma+space, or period if sentence-end."""
    if not s:
        return s
    # Replace " — " / " – " (surrounded by spaces) with ", "
    s = re.sub(r'\s*[\u2014\u2013]\s*', ', ', s)
    # Any remaining standalone dashes
    s = s.replace('\u2014', ',').replace('\u2013', ',')
    # Clean any ",," or ", ." artifacts
    s = re.sub(r',\s*,', ',', s)
    s = re.sub(r',\s*\.', '.', s)
    s = re.sub(r'\s+', lambda m: ' ' if '\n' not in m.group(0) else m.group(0), s)
    return s


def parse_reply(reply):
    """Extract {subject, body} from the Bridge reply. Robust to minor wrappers."""
    reply = reply.strip()
    # Strip accidental ```json fences
    if reply.startswith('```'):
        reply = re.sub(r'^```(?:json)?\s*', '', reply)
        reply = re.sub(r'\s*```$', '', reply)
    m = re.search(r'\{.*\}', reply, re.DOTALL)
    if not m:
        raise ValueError(f"No JSON object in reply: {reply[:200]}")
    raw = m.group(0)
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: extract via regex (lenient, tolerates unescaped quotes)
        sm = re.search(r'"subject"\s*:\s*"(.+?)"\s*,\s*"body"', raw, re.DOTALL)
        bm = re.search(r'"body"\s*:\s*"(.+)"\s*\}', raw, re.DOTALL)
        if not sm or not bm:
            raise
        obj = {
            'subject': sm.group(1).replace('\\"', '"').replace('\\n', '\n'),
            'body': bm.group(1).replace('\\"', '"').replace('\\n', '\n'),
        }
    subject = strip_dashes(obj.get('subject', '').strip())
    body = strip_signature(strip_dashes(obj.get('body', '').strip()))
    return subject, body


def call_bridge(system_prompt, user_message, timeout=180):
    r = requests.post(
        BRIDGE_URL,
        json={"system_prompt": system_prompt, "user_message": user_message},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', required=True, help='Batch xlsx path')
    ap.add_argument('--limit', type=int, default=None, help='Test on first N rows')
    args = ap.parse_args()

    if not os.path.exists(args.file):
        print(f"File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    full_df = pd.read_excel(args.file)
    # Only consider rows that don't already have a draft
    no_draft_mask = (full_df['draft_subject'].isna() |
                     (full_df['draft_subject'].astype(str).str.strip() == ''))
    remaining = full_df[no_draft_mask]
    df = remaining.head(args.limit) if args.limit else remaining

    already_drafted = len(full_df) - len(remaining)
    print(f"Batch: {len(full_df)} total | {already_drafted} already drafted | "
          f"{len(remaining)} remaining | processing {len(df)} now.")
    con = sqlite3.connect(DB)

    # Collect already-used subjects from this batch to enforce variety
    used_subjects = []
    for _, r in full_df.iterrows():
        s = r.get('draft_subject')
        if isinstance(s, str) and s.strip():
            used_subjects.append(s.strip())

    batch_index_counter = 0
    for i, row in df.iterrows():
        lead_id = row['lead_id']

        touch_num = int(row.get('touch_number', 1) or 1)
        if touch_num == 2:
            system_prompt = FOLLOWUP_PROMPT
            user_prompt = build_followup_user_prompt(row)
        elif touch_num >= 3:
            system_prompt = BREAKUP_PROMPT
            user_prompt = build_breakup_user_prompt(row)
        else:
            system_prompt = SYSTEM_PROMPT
            user_prompt = build_user_prompt(
                row,
                avoid_subjects=used_subjects,
                batch_index=batch_index_counter,
            )
        batch_index_counter += 1
        t0 = time.time()
        try:
            resp = call_bridge(system_prompt, user_prompt)
            reply = resp['reply']
            subject, body = parse_reply(reply)
        except Exception as e:
            print(f"  [{i+1}/{len(df)}] {lead_id} FAILED: {e}")
            df.at[i, 'notes'] = f"ERROR: {e}"
            continue

        dt_iso = dt.datetime.now().isoformat(timespec='seconds')
        df.at[i, 'draft_subject'] = subject
        df.at[i, 'draft_body'] = body
        df.at[i, 'draft_language'] = 'de'
        df.at[i, 'generated_at'] = dt_iso

        # Store in emails_sent (sent_at=NULL -> 'Drafted' state)
        con.execute(
            "INSERT INTO emails_sent "
            "(lead_id, batch_date, touch_number, subject, body, from_email) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (lead_id, row.get('batch_date', dt.date.today().isoformat()),
             touch_num, subject, body, 'pradip@bitcodingsolutions.com'),
        )
        con.execute(
            "UPDATE lead_status SET status='Drafted', updated_at=CURRENT_TIMESTAMP "
            "WHERE lead_id = ?",
            (lead_id,),
        )
        con.commit()

        used_subjects.append(subject)
        cost = resp.get('cost_usd')
        print(f"  [{i+1}/{len(df)}] {lead_id}  OK  ({time.time()-t0:.1f}s"
              f"{f', ${cost:.4f}' if cost else ''})")
        print(f"      Subject: {subject}")
        print(f"      Body   : {body[:120].replace(chr(10),' ')}...")
        print()

    # Copy processed cols back into full_df and save
    for col in ['draft_subject', 'draft_body', 'draft_language', 'generated_at', 'notes']:
        full_df.loc[df.index, col] = df[col]
    with pd.ExcelWriter(args.file, engine='xlsxwriter',
                        engine_kwargs={'options': {'strings_to_urls': False}}) as w:
        full_df.to_excel(w, sheet_name='Batch', index=False)

    # Update daily_batches counter
    today = dt.date.today().isoformat()
    drafted = full_df['draft_subject'].astype(bool).sum()
    con.execute(
        "UPDATE daily_batches SET drafts_generated = ? WHERE batch_date = ?",
        (int(drafted), today),
    )
    con.commit()
    con.close()

    print(f"\n[OK] {drafted}/{len(df)} drafts written to {args.file}")


if __name__ == '__main__':
    main()
