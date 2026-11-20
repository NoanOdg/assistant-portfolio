"""Scelle la reserve et la consigne du juge (empreintes dans evaluation/jeu/sceau.json).

A lancer une seule fois, apres validation des questions par Noan. Resceller
ensuite revient a changer de reserve : le dire dans la page projet.

    assistant-sceller --motif "validation par Noan"
"""

from __future__ import annotations

import argparse
import datetime as dt
import json

from assistant.evaluation import jeu


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--motif", required=True)
    a = p.parse_args(argv)
    jeu.charger(jeu.RESERVE)  # refuse une reserve mal formee
    sceau = {
        "scelle_le": dt.date.today().isoformat(),
        "statut": a.motif,
        "reserve_sha256": jeu.empreinte_fichier(jeu.RESERVE),
        "consigne_juge_sha256": jeu.empreinte_fichier(jeu.CONSIGNE_JUGE),
    }
    jeu.SCEAU.write_text(json.dumps(sceau, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(sceau, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
