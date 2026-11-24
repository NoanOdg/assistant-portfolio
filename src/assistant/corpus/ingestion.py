"""Construit le corpus (JSONL) et la carte de contact. Relancable a volonte.

    assistant-ingerer --source mdx --contenu ../Carriere/portfolio/src/content
    assistant-ingerer --source site --site https://noan-ouedraogo.dev

Garde : si plus de 30 % des morceaux du corpus precedent disparaissent d'un
coup, rien n'est ecrit et la commande echoue (code 2). Controle bloquant : un
telephone, un courriel ou un code postal dans le corpus construit, rien n'est
ecrit non plus (code 3).

Decoupage : `[corpus] decoupage` des reglages, ou --decoupage (fiche 3.2 de l'etude).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from assistant.corpus.decoupage import decouper
from assistant.corpus.servi import CoordonneeTrouvee, controler_coordonnees, empreinte_corpus
from assistant.modeles import Morceau

PART_SUPPRESSION_MAX = 0.30


class GardeSuppression(RuntimeError):
    pass


def construire(source, decoupage: str = "mots360") -> tuple[list[Morceau], dict]:
    morceaux: list[Morceau] = []
    vus: Counter[str] = Counter()
    for page in source.pages():
        for m in decouper(page, mode=decoupage):
            vus[m.id] += 1
            if vus[m.id] > 1:  # deux sections homonymes : identifiant rendu unique
                m = Morceau(**{**m.vers_dict(), "id": f"{m.id}-{vus[m.id]}"})
            morceaux.append(m)
    return morceaux, source.coordonnees()


def lire(chemin: Path) -> list[Morceau]:
    if not chemin.exists():
        return []
    with chemin.open(encoding="utf-8") as f:
        return [Morceau.depuis_dict(json.loads(ligne)) for ligne in f if ligne.strip()]


def verifier_suppressions(
    anciens: list[Morceau], nouveaux: list[Morceau], part_max: float = PART_SUPPRESSION_MAX
) -> float:
    if not anciens:
        return 0.0
    restants = {m.id for m in nouveaux}
    part = sum(1 for m in anciens if m.id not in restants) / len(anciens)
    if part > part_max:
        raise GardeSuppression(
            f"{part:.0%} des morceaux disparaissent (maximum {part_max:.0%}) : rien n'est ecrit"
        )
    return part


def ecrire(morceaux: list[Morceau], coordonnees: dict, sortie: Path) -> None:
    sortie.parent.mkdir(parents=True, exist_ok=True)
    with sortie.open("w", encoding="utf-8", newline="\n") as f:
        for m in morceaux:
            f.write(json.dumps(m.vers_dict(), ensure_ascii=False) + "\n")
    (sortie.parent / "coordonnees.json").write_text(
        json.dumps(coordonnees, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--source", choices=["mdx", "site"], default="mdx")
    p.add_argument("--contenu", type=Path, help="dossier src/content du portfolio (source mdx)")
    p.add_argument("--site", default="https://noan-ouedraogo.dev")
    p.add_argument("--sortie", type=Path, default=Path("build/corpus.jsonl"))
    p.add_argument("--forcer", action="store_true", help="ignorer la garde des 30 %")
    p.add_argument(
        "--decoupage",
        choices=["mots360", "section", "page", "petit_grand"],
        help="defaut : [corpus] decoupage des reglages",
    )
    a = p.parse_args(argv)
    if a.decoupage is None:
        from assistant import config

        a.decoupage = config.charger().corpus.get("decoupage", "mots360")

    if a.source == "mdx":
        if not a.contenu:
            p.error("--contenu est requis pour la source mdx")
        from assistant.corpus.mdx import SourceMdx

        source = SourceMdx(a.contenu, a.site)
    else:
        from assistant.corpus.site import SourceSite

        source = SourceSite(a.site)

    morceaux, coord = construire(source, a.decoupage)
    try:
        controler_coordonnees(morceaux)
    except CoordonneeTrouvee as e:
        print(f"ECHEC : {e}", file=sys.stderr)
        return 3
    try:
        part = verifier_suppressions(
            lire(a.sortie), morceaux, 1.0 if a.forcer else PART_SUPPRESSION_MAX
        )
    except GardeSuppression as e:
        print(f"ECHEC : {e}", file=sys.stderr)
        return 2
    ecrire(morceaux, coord, a.sortie)
    par_langue = Counter(m.langue for m in morceaux)
    par_type = Counter(m.type for m in morceaux)
    print(
        f"{len(morceaux)} morceaux ({dict(par_langue)}, {dict(par_type)}), "
        f"decoupage {a.decoupage}, {part:.0%} supprimes, empreinte "
        f"{empreinte_corpus(morceaux)} -> {a.sortie}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
