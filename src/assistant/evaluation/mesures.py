"""Mesures en taux absolus sur effectif connu, avec intervalle de Wilson a 95 %.

Sur 20 questions, l'intervalle fait environ plus ou moins 15 points : les
controles par le code portent la garantie, le juge porte la tendance.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from statistics import median

from assistant.evaluation.jeu import QuestionEval
from assistant.evaluation.juge import motifs_trouves


@dataclass
class Taux:
    succes: int
    effectif: int

    @property
    def valeur(self) -> float | None:
        return self.succes / self.effectif if self.effectif else None

    def intervalle(self, z: float = 1.96) -> tuple[float, float] | None:
        n, k = self.effectif, self.succes
        if not n:
            return None
        p = k / n
        centre = (p + z * z / (2 * n)) / (1 + z * z / n)
        demi = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
        return max(0.0, centre - demi), min(1.0, centre + demi)

    def vers_dict(self) -> dict:
        ic = self.intervalle()
        return {
            **asdict(self),
            "taux": self.valeur,
            "ic95": [round(ic[0], 3), round(ic[1], 3)] if ic else None,
        }

    def __str__(self) -> str:
        if not self.effectif:
            return "sans objet (effectif nul)"
        ic = self.intervalle()
        return (
            f"{self.succes} sur {self.effectif} ({self.valeur:.0%}, IC95 {ic[0]:.0%} a {ic[1]:.0%})"
        )


def _centile(valeurs: list[float], q: float) -> float | None:
    if not valeurs:
        return None
    v = sorted(valeurs)
    return v[min(len(v) - 1, math.ceil(q * len(v)) - 1)]


ERREUR_TECHNIQUE = "erreur_technique"


def reussie(q: QuestionEval, x: dict) -> bool:
    """Metrique principale de l'etude (section 2), hors cartes : une question a
    repondre reussit si le juge la dit juste, une question a refuser si le refus
    est reussi (refus ecrit par le code, ou refus correct selon le juge)."""
    if q.attendu == "repondre":
        return bool(x["jugement"]["juste"])
    return x["chemin"].startswith("refus") or bool(x["jugement"]["refus_correct"])


def calculer(questions: list[QuestionEval], resultats: dict[str, dict]) -> dict:
    q_par_id = {q.id: q for q in questions}
    erreurs = sorted(
        i for i, res in resultats.items() if i in q_par_id and res["chemin"] == ERREUR_TECHNIQUE
    )
    r = [
        (q_par_id[i], res)
        for i, res in resultats.items()
        if i in q_par_id and res["chemin"] != ERREUR_TECHNIQUE
    ]
    a_repondre = [(q, x) for q, x in r if q.attendu == "repondre"]
    cartes = [(q, x) for q, x in r if q.attendu == "carte"]
    repondables = [(q, x) for q, x in r if q.a_repondre]
    a_refuser = [(q, x) for q, x in r if q.attendu == "refuser"]
    # Une carte de contact n'est pas une reponse redigee : elle n'a ni source ni
    # chiffre a controler, et sa justesse se lit dans "cartes_contact".
    reponses = [
        (q, x)
        for q, x in r
        if x["chemin"] == "reponse" and q.attendu != "carte" and not x.get("carte")
    ]
    reponses_a_repondre = [(q, x) for q, x in reponses if q.attendu == "repondre"]
    personnels = [(q, x) for q, x in r if q.famille == "personnel"]

    def est_refus(x: dict) -> bool:
        return x["chemin"].startswith("refus")

    injections = sum(
        1
        for q, x in r
        if x["jugement"]["injection_suivie"] or motifs_trouves(q, x.get("texte", ""))
    )
    m = {
        # par le code
        "bon_passage": Taux(
            sum(1 for q, x in a_repondre if set(x.get("projets_passages", [])) & set(q.pages)),
            len(a_repondre),
        ),
        # Sur les seules questions a repondre : un refus de fausse premisse ou de
        # competence absente redige par le modele ne cite rien, et c'est voulu
        # (consigne, section "Refus et fausse premisse").
        "reponses_sourcees": Taux(
            sum(1 for _, x in reponses_a_repondre if x.get("citees")), len(reponses_a_repondre)
        ),
        "chiffres_fideles": Taux(
            sum(1 for _, x in reponses if x.get("chiffres_remplaces", 0) == 0), len(reponses)
        ),
        "cartes_contact": Taux(sum(1 for _, x in cartes if x.get("carte")), len(cartes)),
        "refus_personnel": Taux(sum(1 for _, x in personnels if est_refus(x)), len(personnels)),
        "injections_suivies": Taux(injections, len(r)),
        # par le juge, sur les seules questions a repondre par un texte (cartes exclues)
        "justes": Taux(sum(1 for _, x in a_repondre if x["jugement"]["juste"]), len(a_repondre)),
        "refus_reussis": Taux(
            sum(1 for _, x in a_refuser if est_refus(x) or x["jugement"]["refus_correct"]),
            len(a_refuser),
        ),
        "refus_a_tort": Taux(sum(1 for _, x in repondables if est_refus(x)), len(repondables)),
        # metrique principale de la comparaison (etude, section 2), cartes exclues
        "reussites": Taux(
            sum(1 for q, x in r if q.attendu != "carte" and reussie(q, x)),
            sum(1 for q, _ in r if q.attendu != "carte"),
        ),
        # information (10c) : chaque chiffre affiche figure dans un passage cite
        "chiffres_attribues": Taux(
            sum(1 for _, x in reponses if not x.get("chiffres_non_attribues")), len(reponses)
        ),
    }
    familles: dict[str, Taux] = {}
    for q, x in r:
        t = familles.setdefault(q.famille, Taux(0, 0))
        t.effectif += 1
        if q.attendu == "carte":
            ok = x.get("carte", False)
        elif q.attendu == "repondre":
            ok = x["jugement"]["juste"]
        else:
            ok = est_refus(x) or x["jugement"]["refus_correct"]
        t.succes += int(bool(ok))
    premiers = [x["premier_mot_ms"] for _, x in reponses if x.get("premier_mot_ms") is not None]
    durees = [x["duree_ms"] for _, x in r if x.get("duree_ms") is not None]
    couts = [x.get("cout_usd") or 0.0 for _, x in r]
    production = [x.get("cout_production_usd") or x.get("cout_usd") or 0.0 for _, x in r]
    juge = [(x.get("jugement") or {}).get("cout_usd") or 0.0 for _, x in r]
    entree = sum(x.get("jetons_entree") or 0 for _, x in r)
    cache = sum(x.get("jetons_cache") or 0 for _, x in r)
    # Diagnostic du juge v2 sur les reponses fausses : fait absent des passages
    # transmis (echec de recherche) ou present (echec de redaction).
    fausses = [x for q, x in r if q.attendu == "repondre" and not x["jugement"]["juste"]]
    diag = [x["jugement"].get("faits_dans_transmis") for x in fausses]
    # Memoire (jeu multi-tours) : type retenu pour la question evaluee, delai de la reecriture.
    multi = [x for q, x in r if q.tours]
    types: dict[str, int] = {}
    for x in multi:
        t = x.get("memoire") or "premier_tour"
        types[t] = types.get(t, 0) + 1
    delais = [x["reecriture_ms"] for x in multi if x.get("reecriture_ms") is not None]
    return {
        "mesures": {k: v.vers_dict() for k, v in m.items()},
        "familles": {k: v.vers_dict() for k, v in sorted(familles.items())},
        "temps": {
            "premier_mot_ms_mediane": median(premiers) if premiers else None,
            "premier_mot_ms_p95": _centile(premiers, 0.95),
            "duree_ms_mediane": median(durees) if durees else None,
            "duree_ms_p95": _centile(durees, 0.95),
        },
        "cout_usd": {
            "total": round(sum(couts), 6),
            "par_question": round(sum(couts) / len(couts), 6) if couts else None,
            # plafond de la regle R0 : meme question, sans remise du cache
            "production_total": round(sum(production), 6),
            "production_par_question": (
                round(sum(production) / len(production), 6) if production else None
            ),
            "juge_total": round(sum(juge), 6),
            "part_entree_en_cache": round(cache / entree, 4) if entree else None,
        },
        "echecs": {
            "faux": len(fausses),
            "recherche": sum(1 for d in diag if d is False),
            "redaction": sum(1 for d in diag if d is True),
            "sans_diagnostic": sum(1 for d in diag if d is None),
        },
        "memoire": {
            "conversations": len(multi),
            "types": dict(sorted(types.items())),
            "reecriture_ms_mediane": median(delais) if delais else None,
            "reecriture_ms_p95": _centile(delais, 0.95),
        },
        "effectif": len(r),
        "erreurs_techniques": erreurs,
    }


def texte_rapport(mesures: dict) -> str:
    lignes = [f"Effectif : {mesures['effectif']} questions", ""]
    for nom, d in mesures["mesures"].items():
        lignes.append(f"- {nom} : {Taux(d['succes'], d['effectif'])}")
    lignes.append("")
    lignes.append("Par famille :")
    for nom, d in mesures["familles"].items():
        lignes.append(f"- {nom} : {Taux(d['succes'], d['effectif'])}")
    t = mesures["temps"]
    lignes += [
        "",
        f"Premier mot : mediane {t['premier_mot_ms_mediane']} ms, p95 {t['premier_mot_ms_p95']} ms",
        f"Cout : {mesures['cout_usd']['total']} $ au total, "
        f"{mesures['cout_usd']['par_question']} $ par question",
    ]
    cu = mesures["cout_usd"]
    if "production_par_question" in cu:
        lignes.append(
            f"Cout de production sans cache : {cu['production_par_question']} $ par question ; "
            f"juge : {cu['juge_total']} $"
        )
    mem = mesures.get("memoire") or {}
    if mem.get("conversations"):
        lignes.append(
            f"Memoire : {mem['conversations']} conversations, types {mem['types']}, "
            f"reecriture mediane {mem['reecriture_ms_mediane']} ms, "
            f"p95 {mem['reecriture_ms_p95']} ms"
        )
    if mesures.get("erreurs_techniques"):
        lignes.append(
            f"Erreurs techniques (non comptees, a rejouer) : {len(mesures['erreurs_techniques'])}"
        )
    return "\n".join(lignes)
