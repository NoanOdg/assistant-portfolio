"""Scelle la reserve et la consigne du juge (empreintes dans evaluation/jeu/sceau.json).

Scellement complet, une seule fois, apres validation des questions par Noan.
Resceller ensuite revient a changer de reserve : le dire dans la page projet.

    assistant-sceller --motif "validation par Noan"

Nouvelle consigne du juge, la reserve restant intacte (son empreinte est
verifiee, jamais recalculee). Le motif et l'ancienne empreinte sont gardes dans
l'historique du sceau :

    assistant-sceller --consigne-juge --motif "pourquoi la regle change"
"""

from __future__ import annotations

import argparse
import datetime as dt
import json

from assistant.evaluation import jeu


def _ecrire(sceau: dict) -> None:
    jeu.SCEAU.write_text(json.dumps(sceau, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(sceau, indent=2, ensure_ascii=False))


def resceller_consigne(motif: str, le: str | None = None) -> dict:
    """Met a jour la seule empreinte de la consigne du juge, avec son motif."""
    if not motif.strip():
        raise ValueError("un motif ecrit est obligatoire")
    jeu.verifier_reserve()  # la reserve ne bouge pas : on le prouve avant d'ecrire
    sceau = jeu.lire_sceau()
    nouvelle = jeu.empreinte_fichier(jeu.CONSIGNE_JUGE)
    if nouvelle == sceau["consigne_juge_sha256"]:
        raise ValueError("la consigne du juge n'a pas change : rien a resceller")
    sceau.setdefault("historique_consigne_juge", []).append(
        {
            "le": le or dt.date.today().isoformat(),
            "motif": motif,
            "ancienne_sha256": sceau["consigne_juge_sha256"],
            "nouvelle_sha256": nouvelle,
        }
    )
    sceau["consigne_juge_sha256"] = nouvelle
    return sceau


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--motif", required=True)
    p.add_argument(
        "--consigne-juge",
        action="store_true",
        help="ne resceller que la consigne du juge (la reserve reste scellee)",
    )
    a = p.parse_args(argv)
    if a.consigne_juge:
        _ecrire(resceller_consigne(a.motif))
        return 0
    jeu.charger(jeu.RESERVE)  # refuse une reserve mal formee
    ancien = jeu.lire_sceau() if jeu.SCEAU.exists() else {}
    sceau = {
        "scelle_le": dt.date.today().isoformat(),
        "statut": a.motif,
        "reserve_sha256": jeu.empreinte_fichier(jeu.RESERVE),
        "consigne_juge_sha256": jeu.empreinte_fichier(jeu.CONSIGNE_JUGE),
    }
    if ancien.get("historique_consigne_juge"):
        sceau["historique_consigne_juge"] = ancien["historique_consigne_juge"]
    _ecrire(sceau)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
