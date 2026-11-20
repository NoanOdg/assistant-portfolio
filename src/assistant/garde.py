"""Aiguillage par le code, avant toute recherche : donnees personnelles et contact.

Une regle simple et lisible, testee : elle n'a pas a etre parfaite, le seuil de
distance et la consigne couvrent le reste, et l'evaluation mesure l'ensemble.
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


def _cherche(motifs: list[str], texte: str) -> bool:
    return any(re.search(m, texte) for m in motifs)


def aiguiller(question: str) -> str:
    """'personnel', 'contact' ou 'recherche'."""
    t = _plat(question)
    if _cherche(PERSONNEL, t) and not _cherche(TRAVAIL, t):
        return "personnel"
    if _cherche(CONTACT, t) and not _cherche(TRAVAIL, t) and len(jetons(question)) <= 12:
        return "contact"
    return "recherche"
