"""Mise en forme des valeurs, comme le composant <Metrique> du site.

Le site formate avec Intl.NumberFormat (4 decimales au plus). On reproduit ce
rendu pour que le modele lise les chiffres tels qu'un visiteur les voit.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal

MOIS = {
    "fr": [
        "janvier",
        "février",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
    ],
    "en": [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ],
}


def _grouper(entier: str, sep: str) -> str:
    signe = "-" if entier.startswith("-") else ""
    chiffres = entier.lstrip("-")
    groupes = []
    while len(chiffres) > 3:
        groupes.insert(0, chiffres[-3:])
        chiffres = chiffres[:-3]
    groupes.insert(0, chiffres)
    return signe + sep.join(groupes)


def _arrondi(valeur: float, decimales: int) -> Decimal:
    """Arrondi de Intl.NumberFormat : au plus proche, moitie vers le haut, sur le double exact."""
    return Decimal(valeur).quantize(Decimal(1).scaleb(-decimales), rounding=ROUND_HALF_UP)


def nombre(valeur: float, langue: str) -> str:
    """4 decimales au plus, zeros de fin retires, separateurs de la langue."""
    texte = f"{_arrondi(valeur, 4):f}".rstrip("0").rstrip(".")
    entier, _, decimales = texte.partition(".")
    if langue == "fr":
        res = _grouper(entier, " ")
        return f"{res},{decimales}" if decimales else res
    res = _grouper(entier, ",")
    return f"{res}.{decimales}" if decimales else res


def compact(valeur: float, langue: str) -> str:
    for seuil, fr, en in ((1e9, " Md", "B"), (1e6, " M", "M"), (1e3, " k", "K")):
        if abs(valeur) >= seuil:
            court = nombre(float(_arrondi(valeur / seuil, 1)), langue)
            return f"{court}{fr if langue == 'fr' else en}"
    return nombre(valeur, langue)


def valeur_metrique(m: dict, langue: str) -> str:
    """Rendu affiche : valeur puis unite, avec la valeur exacte si compacte."""
    unite = m.get("unite", {})
    unite = unite.get(langue, "") if isinstance(unite, dict) else str(unite)
    affichage = m.get("affichage")
    v = float(m["valeur"])
    if affichage == "pourcentage":
        texte = nombre(v * 100, langue)
    elif affichage == "compact":
        texte = f"{compact(v, langue)} ({nombre(v, langue)})"
    else:
        texte = nombre(v, langue)
    return f"{texte} {unite}".strip()


def mois(aaaa_mm: str, langue: str) -> str:
    m = re.fullmatch(r"(\d{4})-(\d{2})", aaaa_mm.strip())
    if not m:
        return aaaa_mm.strip()
    return f"{MOIS[langue][int(m.group(2)) - 1]} {m.group(1)}"


def periode(brut: str, langue: str) -> str:
    morceaux = [p.strip() for p in re.split(r"→|->", str(brut)) if p.strip()]
    if len(morceaux) == 2:
        a, b = (mois(x, langue) for x in morceaux)
        return f"de {a} à {b}" if langue == "fr" else f"from {a} to {b}"
    return mois(morceaux[0], langue) if morceaux else ""


def ancre(titre: str) -> str:
    """Identifiant de titre a la maniere de github-slugger (utilise par Astro)."""
    t = unicodedata.normalize("NFC", titre).strip().lower()
    t = re.sub(r"[^\w\- ]", "", t)
    return t.replace(" ", "-")
