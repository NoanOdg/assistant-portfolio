"""Decoupage par section, en-tete de contexte sur chaque morceau.

Une section ne deborde jamais sur la suivante : la citation pointe vers son ancre.
Une section trop longue est coupee entre paragraphes, puis entre phrases.
"""

from __future__ import annotations

import hashlib
import re

from assistant.modeles import Morceau, Page, empreinte

MOTS_MAX = 360  # environ 480 jetons


def _mots(texte: str) -> int:
    return len(texte.split())


def _phrases(paragraphe: str) -> list[str]:
    return [p for p in re.split(r"(?<=[.!?])\s+", paragraphe) if p]


def _blocs(texte: str, mots_max: int) -> list[str]:
    unites: list[str] = []
    for paragraphe in texte.split("\n\n"):
        if _mots(paragraphe) <= mots_max:
            unites.append(paragraphe)
        else:
            unites.extend(_phrases(paragraphe))
    blocs: list[str] = []
    courant: list[str] = []
    for unite in unites:
        if courant and _mots(" ".join([*courant, unite])) > mots_max:
            blocs.append("\n\n".join(courant))
            courant = []
        courant.append(unite)
    if courant:
        blocs.append("\n\n".join(courant))
    return blocs


def decouper(page: Page, mots_max: int = MOTS_MAX) -> list[Morceau]:
    morceaux: list[Morceau] = []
    for section in page.sections:
        contexte = " > ".join((page.titre, *[c for c in section.chemin if c]))
        for i, bloc in enumerate(_blocs(section.texte, mots_max)):
            cle = f"{page.url}#{section.ancre}|{'/'.join(section.chemin)}|{i}"
            ident = hashlib.sha256(cle.encode("utf-8")).hexdigest()[:24]
            morceaux.append(
                Morceau(
                    id=ident,
                    url=page.url,
                    ancre=section.ancre,
                    titre=page.titre,
                    contexte=contexte,
                    texte=bloc,
                    langue=page.langue,
                    projet=page.projet,
                    type=section.type,
                    empreinte=empreinte(f"{contexte}\n{bloc}"),
                )
            )
    return morceaux
