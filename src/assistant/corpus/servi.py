"""Ce qui est servi du corpus construit, et les controles qui le gardent.

Le fichier `build/corpus.jsonl` contient tout ce que l'ingestion a lu, page
Contact comprise (type "contact"). Les reglages choisissent ce qui est servi :
- `[corpus] inclure_contact` (option 1b) : la page Contact, sans coordonnees ;
- `[corpus] compact` (option 1c) : un chiffre deja ecrit dans la prose de sa page
  ne reparait pas dans une fiche "metrique" de la meme page.
Tout consommateur (service, indexation, calage, evaluation) passe par
`morceaux_servis` : le comportement par defaut reste celui d'avant.

Controle bloquant a l'ingestion (option 1d) : aucun telephone, courriel ni code
postal suivi d'une commune dans le corpus construit.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from assistant.corpus.pdf import CODE_POSTAL, COURRIEL, TELEPHONE
from assistant.filtres import NOMBRE, formes
from assistant.modeles import Morceau

TYPE_CONTACT = "contact"


class CoordonneeTrouvee(RuntimeError):
    pass


def controler_coordonnees(morceaux: Iterable[Morceau]) -> None:
    """Leve CoordonneeTrouvee si un morceau porte une coordonnee (motif, jamais le texte)."""
    for m in morceaux:
        for nom, motif in (
            ("telephone", TELEPHONE),
            ("courriel", COURRIEL),
            ("code postal", CODE_POSTAL),
        ):
            if motif.search(m.texte) or motif.search(m.contexte):
                raise CoordonneeTrouvee(
                    f"{nom} dans le morceau {m.id} ({m.url}#{m.ancre}) : rien n'est ecrit"
                )


def _chiffres(texte: str) -> set[str]:
    sortie: set[str] = set()
    for mo in NOMBRE.finditer(texte):
        sortie |= formes(mo.group(0))
    return sortie


def compacter(morceaux: list[Morceau]) -> list[Morceau]:
    """Retire une fiche metrique dont tous les chiffres sont deja dans la prose de sa page."""
    prose: dict[tuple[str, str], set[str]] = {}
    for m in morceaux:
        if m.type != "metrique":
            prose.setdefault((m.langue, m.url), set()).update(_chiffres(m.texte))
    sortie = []
    for m in morceaux:
        if m.type == "metrique":
            chiffres = _chiffres(m.texte)
            if chiffres and chiffres <= prose.get((m.langue, m.url), set()):
                continue
        sortie.append(m)
    return sortie


def morceaux_servis(
    morceaux: list[Morceau], inclure_contact: bool = False, compact: bool = False
) -> list[Morceau]:
    servis = [m for m in morceaux if inclure_contact or m.type != TYPE_CONTACT]
    return compacter(servis) if compact else servis


def selon_reglages(morceaux: list[Morceau], corpus: dict) -> list[Morceau]:
    return morceaux_servis(
        morceaux, bool(corpus.get("inclure_contact")), bool(corpus.get("compact"))
    )


def empreinte_corpus(morceaux: Iterable[Morceau]) -> str:
    """Empreinte d'un ensemble de morceaux : identifiants et empreintes de texte, dans l'ordre."""
    h = hashlib.sha256()
    for m in morceaux:
        h.update(f"{m.id}:{m.empreinte}\n".encode())
    return h.hexdigest()[:12]
