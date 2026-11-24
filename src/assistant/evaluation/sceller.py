"""Scelle la reserve, la consigne du juge et les regles de decision (evaluation/jeu/sceau.json).

Scellement complet, une seule fois, apres validation des questions par Noan.
Resceller ensuite revient a changer de reserve : le dire dans la page projet.

    assistant-sceller --motif "validation par Noan"

Nouvelle consigne du juge, la reserve restant intacte (son empreinte est
verifiee, jamais recalculee). Le motif et l'ancienne empreinte sont gardes dans
l'historique du sceau. Version 2 (reference fixe, etude fiche 3.11) :

    assistant-sceller --consigne-juge --motif "pourquoi la regle change"
    assistant-sceller --consigne-juge --version-juge v2 --motif "reference fixe 11b"

Regles de decision de la comparaison (evaluation/regles-decision.toml), gelees
avant le premier passage qu'elles jugent (etude, 5.1, point 4) :

    assistant-sceller --regles --motif "regles R0 avant l'etape 0.2"
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from assistant.evaluation import jeu


def _ecrire(sceau: dict) -> None:
    jeu.SCEAU.write_text(json.dumps(sceau, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(sceau, indent=2, ensure_ascii=False))


def _resceller(cle: str, fichier: Path, historique: str, motif: str, le: str | None) -> dict:
    if not motif.strip():
        raise ValueError("un motif ecrit est obligatoire")
    jeu.verifier_reserve()  # la reserve ne bouge pas : on le prouve avant d'ecrire
    sceau = jeu.lire_sceau()
    nouvelle = jeu.empreinte_fichier(fichier)
    ancienne = sceau.get(cle)
    if nouvelle == ancienne:
        raise ValueError(f"{fichier.name} n'a pas change : rien a resceller")
    sceau.setdefault(historique, []).append(
        {
            "le": le or dt.date.today().isoformat(),
            "motif": motif,
            "ancienne_sha256": ancienne,
            "nouvelle_sha256": nouvelle,
        }
    )
    sceau[cle] = nouvelle
    return sceau


def resceller_consigne(motif: str, le: str | None = None, version: str = "v1") -> dict:
    """Met a jour la seule empreinte d'une consigne du juge, avec son motif."""
    historique = "historique_consigne_juge" + ("" if version == "v1" else f"_{version}")
    return _resceller(
        jeu.CLES_CONSIGNE[version], jeu.fichier_consigne(version), historique, motif, le
    )


def sceller_regles(motif: str, le: str | None = None) -> dict:
    """Gele les regles de decision de la comparaison, avec leur motif."""
    return _resceller("regles_decision_sha256", jeu.REGLES, "historique_regles_decision", motif, le)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--motif", required=True)
    p.add_argument(
        "--consigne-juge",
        action="store_true",
        help="ne resceller que la consigne du juge (la reserve reste scellee)",
    )
    p.add_argument("--version-juge", choices=["v1", "v2"], default="v1")
    p.add_argument(
        "--regles",
        action="store_true",
        help="ne sceller que les regles de decision (la reserve reste scellee)",
    )
    a = p.parse_args(argv)
    if a.consigne_juge:
        _ecrire(resceller_consigne(a.motif, version=a.version_juge))
        return 0
    if a.regles:
        _ecrire(sceller_regles(a.motif))
        return 0
    jeu.charger(jeu.RESERVE)  # refuse une reserve mal formee
    ancien = jeu.lire_sceau() if jeu.SCEAU.exists() else {}
    sceau = {
        "scelle_le": dt.date.today().isoformat(),
        "statut": a.motif,
        "reserve_sha256": jeu.empreinte_fichier(jeu.RESERVE),
        "consigne_juge_sha256": jeu.empreinte_fichier(jeu.CONSIGNE_JUGE),
    }
    for cle, valeur in ancien.items():  # historiques, juge v2 et regles survivent
        if cle.startswith("historique_") or cle in {
            "consigne_juge_v2_sha256",
            "regles_decision_sha256",
        }:
            sceau[cle] = valeur
    _ecrire(sceau)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
