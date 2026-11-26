"""Aiguillage par le code, avant toute recherche : donnees personnelles et contact.

Une regle simple et lisible, testee : elle n'a pas a etre parfaite, le seuil de
distance et la consigne couvrent le reste, et l'evaluation mesure l'ensemble.

Deux versions (`[aiguillage] version`) :
- "v1" : les mots-cles servis ;
- "classes" (fiche 3.6, option 6b), une correction par famille d'erreurs relevee
  le 24/11/2026, jamais par question : une faute de frappe sur les mots de
  contact de 5 lettres et plus ; telephone et numero vers la carte, avec un
  texte qui dit qu'aucun telephone n'est publie ; remuneration sans les mots
  attendus ("combien il gagne"). Mesuree sur le banc evaluation/aiguillage.yaml.
"""

from __future__ import annotations

import re
import unicodedata

from assistant.embeddings import jetons


def _plat(texte: str) -> str:
    t = unicodedata.normalize("NFKD", texte.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


PERSONNEL = [
    r"\bsalaire",
    r"\bremuneration",
    r"\bpretentions?\b",
    r"\bpaye\b",
    r"\bk ?euros?\b",
    r"\bage\b",
    r"\bquel age\b",
    r"\bne en\b",
    r"\bdate de naissance",
    r"\banniversaire",
    r"\badresse\b",
    r"\bhabite",
    r"\bdomicile",
    r"\bnationalite",
    r"\borigines?\b",
    r"\breligion",
    r"\bsante\b",
    r"\bmaladie",
    r"\bhandicap\b",
    r"\bmarie\b",
    r"\benfants?\b",
    r"\bcelibataire",
    r"\bvie privee",
    r"\bpolitique\b",
    r"\btelephone",
    r"\bnumero\b",
    r"\bsalary",
    r"\bpay\b",
    r"\bcompensation",
    r"\bhow old",
    r"\bbirth",
    r"\bage of\b",
    r"\bhome address",
    r"\bwhere does he live",
    r"\bnationality",
    r"\bcitizenship",
    r"\bethnic",
    r"\bhealth\b",
    r"\billness",
    r"\bdisabilit",
    r"\bmarried",
    r"\bchildren\b",
    r"\bkids\b",
    r"\breligio",
    r"\bphone\b",
    r"\bprivate life",
]
CONTACT = [
    r"\bcontact",
    r"\bjoindre\b",
    r"\becrire a\b",
    r"\bcourriel",
    r"\be-?mail",
    r"\bmail\b",
    r"\blinkedin",
    r"\bcv\b",
    r"\b(his|noan'?s?|the|a|your) resume\b",
    r"\bcurriculum",
    r"\breach (him|noan)",
    r"\bget in touch",
    r"\bdownload",
]
# Une question sur le travail (le projet INSEI traite d'un handicap, un projet
# parle d'actes de deces) ne doit pas tomber dans le refus personnel.
TRAVAIL = [
    r"\bprojet",
    r"\bproject",
    r"\binsei\b",
    r"\bmodele",
    r"\bmodel",
    r"\bdonnees de",
    r"\bdataset",
    r"\bentrepot",
    r"\bwarehouse",
]


# Version "classes".
# Moyen de contact non publie : carte, avec la mention qu'aucun telephone n'est publie.
TELEPHONE = [
    r"\btelephone",
    r"\btel\b",
    r"\bnumero\b",
    r"\bportable\b",
    r"\bphone\b",
    r"\bcell ?phone",  # jamais "cell" seul : "robotic cell" est un projet
    r"\bmobile number",
    r"\bwhatsapp",
]
# Remuneration sans les mots "salaire" ou "pay". "Combien de projets il a gagnes"
# n'en est pas : "combien de" est exclu, et "gagnes" n'est pas "gagne".
_VERBES_FR = r"(gagne|gagner|gagnait|touche|toucher|paye|payee|payer|remunere)"
_VERBES_EN = r"(make|makes|earn|earns|earned|paid|charge|charges)"
REMUNERATION = [
    rf"\bcombien\b(?!\s+(de|d)\b)[^?]*\b{_VERBES_FR}\b",
    rf"\b{_VERBES_FR}\b[^?]*\bcombien\b",
    r"\bcombien d ?argent\b",
    rf"\bhow much\b[^?]*\b{_VERBES_EN}\b",
    rf"\b{_VERBES_EN}\b[^?]*\bper (year|month|day|hour)\b",
    r"\bhow much money\b",
    r"\bdaily rate\b",
    r"\btaux journalier\b",
    r"\btjm\b",
]
# Mots de contact pour la tolerance d'une faute (5 lettres et plus, sans accents).
LEXIQUE_CONTACT = (
    "contact",
    "contacter",
    "joindre",
    "courriel",
    "email",
    "linkedin",
    "curriculum",
    "coordonnees",
    "telecharger",
    "download",
)
# Vrais mots a une lettre d'un mot de contact : jamais pris pour une faute
# ("what contract is he looking for" n'est pas une demande de contact).
VRAIS_MOTS = {"contract", "contracts", "contrat", "contrats", "contracted"}
MOTS_FAUTE_MIN = 5


def _cherche(motifs: list[str], texte: str) -> bool:
    return any(re.search(m, texte) for m in motifs)


def distance_frappe(a: str, b: str) -> int:
    """Damerau-Levenshtein restreinte (une inversion de deux lettres compte 1)."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cout = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cout)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len(a)][len(b)]


def _contact_mal_ecrit(texte_plat: str) -> bool:
    for mot in re.findall(r"[a-z]+", texte_plat):
        if len(mot) < MOTS_FAUTE_MIN or mot in VRAIS_MOTS:
            continue
        for cible in LEXIQUE_CONTACT:
            if abs(len(mot) - len(cible)) <= 1 and distance_frappe(mot, cible) <= 1:
                return True
    return False


def _aiguiller_v1(question: str) -> str:
    t = _plat(question)
    if _cherche(CONSIGNE, t):
        return "consigne"
    if _cherche(PERSONNEL, t) and not _cherche(TRAVAIL, t):
        return "personnel"
    if _cherche(CONTACT, t) and not _cherche(TRAVAIL, t) and len(jetons(question)) <= 12:
        return "contact"
    return "recherche"


# Demandes qui visent a piloter la sortie du modele plutot qu'a s'informer :
# refusees par le code, avant toute recherche (texte deja mis a plat).
CONSIGNE = [
    r"\breponds? (uniquement|seulement|exactement) (par|avec)",
    r"\brepete[rsz]?\b",
    r"\b(ignore|oublie)[rsz]? (toutes |tous )?(les|tes|vos|ces) (instructions|consignes|regles)",
    r"\btu es (maintenant|desormais)\b",
    r"\becri[st]? (exactement|uniquement|seulement)\b",
    r"\b(affiche|donne|montre|revele)[rsz]? (moi )?(ton|tes|ta|le|les|votre|vos) "
    r"(prompt|instructions|consignes|regles)",
    r"\b(respond|reply|answer) (only|exactly|solely) with",
    r"\brepeat (after me|exactly|this|that|the following)",
    r"\bignore (all |any |the |your )?(previous |prior |above |earlier )?"
    r"(instructions|rules|prompt)",
    r"\byou are now\b",
    r"\bsystem (override|prompt)",
    r"\bpretend (to be|you are)",
    r"\bdeveloper mode",
    r"\bjailbreak",
    r"\bsay (exactly|only)\b",
    r"\b(show|reveal|print) (me )?(your|the) (prompt|instructions|rules)",
]


def est_consigne(question: str) -> bool:
    return _cherche(CONSIGNE, _plat(question))


PERSONNEL_SANS_TELEPHONE = [m for m in PERSONNEL if m not in set(TELEPHONE)]


def _aiguiller_classes(question: str) -> str:
    t = _plat(question)
    if _cherche(CONSIGNE, t):
        return "consigne"
    travail = _cherche(TRAVAIL, t)
    court = len(jetons(question)) <= 12
    if not travail and _cherche(TELEPHONE, t):
        return "telephone"
    if not travail and (_cherche(PERSONNEL_SANS_TELEPHONE, t) or _cherche(REMUNERATION, t)):
        return "personnel"
    if not travail and court and (_cherche(CONTACT, t) or _contact_mal_ecrit(t)):
        return "contact"
    return "recherche"


def aiguiller(question: str, version: str = "v1") -> str:
    """'consigne', 'personnel', 'contact', 'telephone' (version classes seulement)
    ou 'recherche'."""
    if version == "classes":
        return _aiguiller_classes(question)
    return _aiguiller_v1(question)
