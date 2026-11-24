"""Filtres appliques au texte du modele, en flux, avant qu'il parte au navigateur.

- tout chiffre absent des passages est remplace par "(voir la source)" ;
- tout lien hors liste blanche est retire, liens Markdown ramenes a leur texte ;
- aucune image, aucune balise HTML ;
- aucun courriel : la carte de contact l'affiche, le modele ne l'ecrit jamais ;
- tirets longs et demi-longs remplaces par une virgule (regle du depot).

Le texte est retenu tant qu'un chiffre, un lien ou un crochet peut etre coupe.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

TIRETS = re.compile(r"\s*[\N{EN DASH}\N{EM DASH}]\s*")
# Un nombre : groupes de 3 chiffres separes par espace (fine ou insecable), point
# ou virgule, puis une partie decimale. Pas colle a une lettre (V2, 3D, H100).
NOMBRE = re.compile(r"(?<![\w\[.,])\d+(?:[   ,.]\d{3})*(?:[.,]\d+)?(?![\w\]])")
COURRIEL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
IMAGE_MD = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LIEN_MD = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
URL = re.compile(r"https?://[^\s)\]>]*[^\s)\]>.,;:!?]")
HTML = re.compile(r"</?[A-Za-z][^>]*>")
# Trois chiffres : en tout_le_corpus, un renvoi va jusqu'a [178] et plus (defaut D2).
CITATION = re.compile(r"\[(\d{1,3})\]")


def formes(nombre: str) -> set[str]:
    """Toutes les lectures possibles d'un nombre ecrit (virgule decimale ou de groupe)."""
    brut = re.sub(r"[   ]", "", nombre)
    lectures = set()
    for decimal in (",", "."):
        groupe = "." if decimal == "," else ","
        t = brut.replace(groupe, "")
        if t.count(decimal) > 1:
            continue
        t = t.replace(decimal, ".")
        if "." in t:
            t = t.rstrip("0").rstrip(".")
        t = t.lstrip("0") or "0"
        if t.startswith("."):
            t = "0" + t
        lectures.add(t)
    return lectures


def chiffres_autorises(textes: Iterable[str]) -> set[str]:
    autorises: set[str] = set()
    for texte in textes:
        for mo in NOMBRE.finditer(texte):
            autorises |= formes(mo.group(0))
    return autorises


@dataclass
class Compteurs:
    chiffres_remplaces: int = 0
    liens_retires: int = 0
    courriels_retires: int = 0


class FiltreFlux:
    RETENUE_MAX = 400

    def __init__(self, autorises: set[str], domaines: Iterable[str], langue: str) -> None:
        self.autorises = autorises
        self.domaines = tuple(domaines)
        self.langue = langue
        self.compteurs = Compteurs()
        self._tampon = ""

    @property
    def _voir(self) -> str:
        return "(voir la source)" if self.langue == "fr" else "(see the source)"

    def _domaine_ok(self, url: str) -> bool:
        hote = re.sub(r"^https?://", "", url).split("/")[0].lower()
        return any(hote == d or hote.endswith("." + d) for d in self.domaines)

    def _nettoyer(self, texte: str) -> str:
        # Une coupure de flux tombe toujours sur un blanc : en fin de segment, le
        # blanc qui suit le tiret ouvre le segment suivant.
        texte = TIRETS.sub(lambda mo: "," if mo.end() == len(texte) else ", ", texte)
        texte = IMAGE_MD.sub("", texte)
        texte = HTML.sub("", texte)

        def lien(mo: re.Match) -> str:
            if not self._domaine_ok(mo.group(2)):
                self.compteurs.liens_retires += 1
            return mo.group(1)

        texte = LIEN_MD.sub(lien, texte)

        def url(mo: re.Match) -> str:
            if self._domaine_ok(mo.group(0)):
                return mo.group(0)
            self.compteurs.liens_retires += 1
            return ""

        texte = URL.sub(url, texte)

        def courriel(_mo: re.Match) -> str:
            self.compteurs.courriels_retires += 1
            return "(voir la carte de contact)" if self.langue == "fr" else "(see the contact card)"

        texte = COURRIEL.sub(courriel, texte)

        def nombre(mo: re.Match) -> str:
            if formes(mo.group(0)) & self.autorises:
                return mo.group(0)
            self.compteurs.chiffres_remplaces += 1
            return self._voir

        return NOMBRE.sub(nombre, texte)

    def _coupure(self) -> int:
        """Dernier blanc ou l'on peut couper sans casser un chiffre, un lien ou un crochet."""
        t = self._tampon
        for i in range(len(t) - 1, 0, -1):
            if not t[i].isspace() or t[i - 1].isdigit():
                continue
            tete = t[:i]
            if tete.count("[") > tete.count("]") or tete.count("(") > tete.count(")"):
                continue
            if tete.count("<") > tete.count(">"):
                continue
            return i
        return 0

    def pousser(self, morceau: str) -> str:
        self._tampon += morceau
        i = self._coupure()
        if i == 0 and len(self._tampon) > self.RETENUE_MAX:
            i = len(self._tampon)
        sortie, self._tampon = self._tampon[:i], self._tampon[i:]
        return self._nettoyer(sortie) if sortie else ""

    def terminer(self) -> str:
        sortie, self._tampon = self._tampon, ""
        return self._nettoyer(sortie)


def chiffres_non_attribues(texte: str, passages_cites: Iterable[str]) -> int:
    """Chiffres affiches absents de tous les passages cites (mesure 10c).

    Le filtre en flux garantit qu'un chiffre affiche existe dans les passages
    transmis ; en tout_le_corpus, c'est tout le site (defaut D3). Cette mesure
    dit en plus si le chiffre vient bien d'un passage que la reponse cite.
    Non bloquante : le texte est deja affiche quand on la calcule."""
    autorises = chiffres_autorises(passages_cites)
    return sum(1 for mo in NOMBRE.finditer(texte) if not formes(mo.group(0)) & autorises)


def citations_valides(texte: str, nb_passages: int) -> list[int]:
    vus: list[int] = []
    for mo in CITATION.finditer(texte):
        n = int(mo.group(1))
        if 1 <= n <= nb_passages and n not in vus:
            vus.append(n)
    return vus
