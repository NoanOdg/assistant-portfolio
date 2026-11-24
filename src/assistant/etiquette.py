"""Etiquette d'intention donnee par le modele, en sortie structuree (fiche 3.6, option 6c).

Le modele rend un objet JSON {"intention": ..., "reponse": ...}. Le code lit
l'intention des qu'elle arrive dans le flux :
- "contact" : le code affiche la carte de contact ;
- "personnel" : le code affiche le refus ecrit d'avance des questions personnelles ;
- "hors_sujet" : le code affiche le refus ecrit d'avance du modele ;
- "question" : le texte de "reponse" part en flux, decode au fil de l'eau.
Le modele n'ecrit donc jamais lui-meme ni la carte ni un refus.

En flux, le JSON arrive en fragments partiels : `LecteurEtiquette` les recolle
et decode la chaine "reponse" (echappements compris) sans attendre la fin.
"""

from __future__ import annotations

import json
import re

INTENTIONS = ("contact", "personnel", "question", "hors_sujet")
INCONNUE = "inconnue"

# Schema de google-genai (types en majuscules). L'intention vient en premier :
# le code decide avant le premier mot de la reponse.
SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "intention": {"type": "STRING", "enum": list(INTENTIONS)},
        "reponse": {"type": "STRING"},
    },
    "required": ["intention", "reponse"],
    "propertyOrdering": ["intention", "reponse"],
}

_INTENTION = re.compile(r'"intention"\s*:\s*"([^"\\]*)"')
_REPONSE = re.compile(r'"reponse"\s*:\s*"')
_ECHAPPEMENTS = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "/": "/"}


def normaliser(intention: str) -> str:
    t = intention.strip().lower().replace(" ", "_").replace("-", "_")
    return t if t in INTENTIONS else INCONNUE


class LecteurEtiquette:
    """Recolle le JSON du flux. `pousser` ne rend du texte qu'une fois l'intention lue."""

    def __init__(self) -> None:
        self.brut = ""
        self.intention: str | None = None
        self._debut: int | None = None  # debut du contenu de la chaine "reponse"
        self._lu = 0
        self._fini = False
        self._tampon = ""

    def _decoder(self) -> None:
        s, i, sortie = self.brut, self._lu, []
        while i < len(s):
            c = s[i]
            if c == '"':
                self._fini = True
                i += 1
                break
            if c != "\\":
                sortie.append(c)
                i += 1
                continue
            if i + 1 >= len(s):
                break  # echappement coupe : on attend la suite
            e = s[i + 1]
            if e != "u":
                sortie.append(_ECHAPPEMENTS.get(e, e))
                i += 2
                continue
            if i + 6 > len(s):
                break
            code = int(s[i + 2 : i + 6], 16)
            if 0xD800 <= code < 0xDC00:  # paire de substitution : deux \uXXXX
                if i + 12 > len(s):
                    break
                bas = int(s[i + 8 : i + 12], 16)
                sortie.append(chr(0x10000 + ((code - 0xD800) << 10) + (bas - 0xDC00)))
                i += 12
                continue
            sortie.append(chr(code))
            i += 6
        self._lu = i
        self._tampon += "".join(sortie)

    def pousser(self, fragment: str) -> str:
        self.brut += fragment
        if self.intention is None:
            mo = _INTENTION.search(self.brut)
            if mo:
                self.intention = normaliser(mo.group(1))
        if self._debut is None:
            mo = _REPONSE.search(self.brut)
            if mo:
                self._debut = self._lu = mo.end()
        if self._debut is not None and not self._fini:
            self._decoder()
        if self.intention is None:
            return ""
        sortie, self._tampon = self._tampon, ""
        return sortie

    def terminer(self) -> str:
        """Fin du flux : dernier texte decode. Si le flux n'a pas pu etre lu au fil
        de l'eau, on tente l'objet entier ; sinon l'intention reste inconnue."""
        if self.intention is None or self._debut is None:
            try:
                d = json.loads(self.brut)
            except (json.JSONDecodeError, TypeError):
                d = None
            if isinstance(d, dict):
                if self.intention is None:
                    self.intention = normaliser(str(d.get("intention", "")))
                if self._debut is None:
                    self._tampon += str(d.get("reponse", ""))
        if self.intention is None:
            self.intention = INCONNUE
        sortie, self._tampon = self._tampon, ""
        return sortie
