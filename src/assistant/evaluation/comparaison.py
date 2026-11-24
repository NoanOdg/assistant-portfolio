"""Bruit de la base et regle de decision gelee (etude du 24/11/2026, section 2 et 3.12).

    assistant-bruit "build/comparaison/b0-r*.json" --sortie build/comparaison/bruit.json
    assistant-comparer --base "build/comparaison/b0-r*.json" \
        --challenger "build/comparaison/c0-r*.json" --bruit build/comparaison/bruit.json \
        --rapport build/comparaison/rapport.md
    assistant-comparer --architecture B="build/b0-r*.json" C="build/c0-r*.json" \
        A="build/a0-r*.json" --bruit build/comparaison/bruit.json

Regle R0 : un challenger remplace la base si et seulement si, toutes a la fois,
(1) les garde-fous du code tiennent sur chacun de ses passages, (2) son gain net
moyen de reussites atteint G = max(3, plafond(2 sigma)), (3) ses refus a tort
n'augmentent pas de plus d'une question, (4) aucune famille ne perd plus de 2
questions, (5) son cout par question au prix de production sans cache reste
sous 0,012 $, (6) son premier mot au 95e centile reste sous 3 s.
Egalite (gain sous G) : la moins chere par question l'emporte ; a 20 % l'une de
l'autre, la plus simple (C < B < A, moins de composants sur le trajet).
McNemar exact, Wilson et bootstrap groupe sont publies, ils ne decident rien.
Les parametres vivent dans evaluation/regles-decision.toml, scelle par empreinte.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import random
import statistics
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from assistant.evaluation import jeu as jeu_mod
from assistant.evaluation.mesures import ERREUR_TECHNIQUE, Taux, _centile
from assistant.evaluation.porte import charger_seuils, verifier


def charger_regles(chemin: Path | None = None) -> dict:
    with (chemin or jeu_mod.REGLES).open("rb") as f:
        return tomllib.load(f)


def statut_regles(chemin: Path | None = None) -> str:
    """'scellees', 'non scellees' ou 'modifiees depuis le sceau'."""
    chemin = chemin or jeu_mod.REGLES
    try:
        attendue = jeu_mod.lire_sceau().get("regles_decision_sha256")
    except FileNotFoundError:
        attendue = None
    if not attendue:
        return "non scellees"
    return (
        "scellees" if jeu_mod.empreinte_fichier(chemin) == attendue else "modifiees depuis le sceau"
    )


def lire_passages(motifs: list[str] | str) -> list[dict]:
    """Fichiers de sortie d'assistant-evaluer ; motifs glob acceptes (Windows compris)."""
    motifs = [motifs] if isinstance(motifs, str) else motifs
    chemins: list[str] = []
    for m in motifs:
        # Un motif sans correspondance ne compte pas (b0.json ou b0-r?.json) ;
        # un nom exact absent fait echouer la lecture, comme il se doit.
        trouves = sorted(glob.glob(m))
        chemins.extend(trouves or ([] if glob.has_magic(m) else [m]))
    passages = []
    for c in chemins:
        d = json.loads(Path(c).read_text(encoding="utf-8"))
        d["_fichier"] = c
        passages.append(d)
    if not passages:
        raise ValueError(f"aucun passage pour {motifs}")
    return passages


def reussite(x: dict) -> bool | None:
    """Reussite d'une question hors carte, None si elle ne compte pas (carte, erreur)."""
    if x.get("attendu") == "carte" or x.get("chemin") == ERREUR_TECHNIQUE:
        return None
    if x.get("attendu") == "repondre":
        return bool(x["jugement"]["juste"])
    return x["chemin"].startswith("refus") or bool(x["jugement"]["refus_correct"])


def _vrai(effective: dict, cle: str) -> bool:
    section, _, nom = cle.partition(".")
    return bool((effective.get(section) or {}).get(nom))


def complexite(passage: dict, regles: dict) -> tuple[int, int]:
    """(rang d'architecture, composants sur le trajet). Plus petit = plus simple."""
    c = regles["complexite"]
    archi = passage.get("architecture") or "B"
    eff = (passage.get("config") or {}).get("effective") or {}
    rang = int(c["architecture"].get(archi, 1))
    composants = sum(1 for cle in c["composants"] if _vrai(eff, cle))
    if passage.get("simulation", {}).get("prefiltre_seuil") and not _vrai(
        eff, "refus.prefiltre_seuil"
    ):
        composants += 1  # filtre de seuil simule sur un passage de C
    return rang, composants


@dataclass
class Resume:
    nom: str
    passages: int
    reussites: list[int]
    effectif: int
    par_question: dict[str, float]
    groupes: dict[str, str]
    familles: dict[str, float]
    refus_a_tort: float
    cout_production: float
    cout_reel: float
    cout_juge: float
    premier_mot_p95: float | None
    echecs_garde: list[str]
    erreurs: int
    complexite: tuple[int, int]
    architecture: str
    empreintes: dict = field(default_factory=dict)

    @property
    def moyenne(self) -> float:
        return statistics.fmean(self.reussites) if self.reussites else 0.0


def resumer(nom: str, passages: list[dict], regles: dict) -> Resume:
    r0 = regles["r0"]
    seuils = charger_seuils(r0["profil_garde_fous"])
    seuils = {k: v for k, v in seuils.items() if k in set(r0["garde_fous"])}
    par_q: dict[str, list[int]] = {}
    groupes: dict[str, str] = {}
    familles: dict[str, list[int]] = {}
    premiers: list[float] = []
    echecs: list[str] = []
    erreurs = 0
    for k, p in enumerate(passages, 1):
        m = p["mesures"]
        erreurs += len(m.get("erreurs_techniques") or [])
        for e in verifier(m, seuils):
            echecs.append(f"passage {k} : {e}")
        for f, d in m["familles"].items():
            familles.setdefault(f, []).append(d["succes"])
        for ident, x in p["resultats"].items():
            ok = reussite(x)
            if ok is not None:
                par_q.setdefault(ident, []).append(int(ok))
                groupes[ident] = x.get("groupe") or ident
            if (
                x.get("chemin") == "reponse"
                and x.get("attendu") != "carte"
                and not x.get("carte")
                and x.get("premier_mot_ms") is not None
            ):
                premiers.append(float(x["premier_mot_ms"]))

    def moyenne(cle: str, sous: str) -> float:
        valeurs = [p["mesures"][sous].get(cle) or 0.0 for p in passages]
        return statistics.fmean(valeurs)

    cout_prod = statistics.fmean(
        p["mesures"]["cout_usd"].get("production_par_question")
        or p["mesures"]["cout_usd"].get("par_question")
        or 0.0
        for p in passages
    )
    return Resume(
        nom=nom,
        passages=len(passages),
        reussites=[p["mesures"]["mesures"]["reussites"]["succes"] for p in passages],
        effectif=passages[0]["mesures"]["mesures"]["reussites"]["effectif"],
        par_question={i: statistics.fmean(v) for i, v in par_q.items()},
        groupes=groupes,
        familles={f: statistics.fmean(v) for f, v in familles.items()},
        refus_a_tort=statistics.fmean(
            p["mesures"]["mesures"]["refus_a_tort"]["succes"] for p in passages
        ),
        cout_production=cout_prod,
        cout_reel=moyenne("par_question", "cout_usd"),
        cout_juge=moyenne("juge_total", "cout_usd"),
        premier_mot_p95=_centile(premiers, 0.95),
        echecs_garde=echecs,
        erreurs=erreurs,
        complexite=complexite(passages[0], regles),
        architecture=passages[0].get("architecture") or "B",
        empreintes=passages[0].get("empreintes") or {},
    )


def bruit(passages: list[dict], regles: dict | None = None) -> dict:
    """sigma (ecart-type des reussites entre passages de la base) et G."""
    regles = regles or charger_regles()
    r0 = regles["r0"]
    reussites = [p["mesures"]["mesures"]["reussites"]["succes"] for p in passages]
    if len(reussites) < 2:
        raise ValueError("au moins 2 passages de la base pour mesurer le bruit")
    sigma = statistics.stdev(reussites)
    g = max(int(r0["gain_min"]), math.ceil(float(r0["facteur_sigma"]) * sigma))
    empreintes = {(p.get("empreintes") or {}).get("config") for p in passages}
    return {
        "sigma": round(sigma, 4),
        "G": g,
        "passages": len(reussites),
        "reussites": reussites,
        "effectif": passages[0]["mesures"]["mesures"]["reussites"]["effectif"],
        "empreinte_config": sorted(e for e in empreintes if e)[0] if empreintes - {None} else None,
        "configs_differentes": len(empreintes - {None}) > 1,
        "assez_de_passages": len(reussites) >= int(r0["repetitions_base_min"]),
        "fichiers": [p.get("_fichier", "") for p in passages],
    }


def mcnemar_exact(base: dict[str, float], challenger: dict[str, float]) -> dict:
    """Test exact sur les paires discordantes (Dietterich 1998). Avec plusieurs passages,
    une question est discordante si sa reussite moyenne differe (test du signe)."""
    communes = base.keys() & challenger.keys()
    b = sum(1 for i in communes if base[i] > challenger[i])
    c = sum(1 for i in communes if challenger[i] > base[i])
    n = b + c
    if n == 0:
        return {"base_seule": 0, "challenger_seul": 0, "p": 1.0}
    queue = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2**n
    return {"base_seule": b, "challenger_seul": c, "p": round(min(1.0, 2 * queue), 4)}


def bootstrap_groupe(
    base: dict[str, float],
    challenger: dict[str, float],
    groupes: dict[str, str],
    tirages: int = 2000,
    graine: int = 0,
) -> list[float]:
    """IC a 95 % du gain (en questions) par tirage des groupes de questions avec remise."""
    communes = sorted(base.keys() & challenger.keys())
    par_groupe: dict[str, float] = {}
    for i in communes:
        g = groupes.get(i, i)
        par_groupe[g] = par_groupe.get(g, 0.0) + challenger[i] - base[i]
    valeurs = list(par_groupe.values())
    if not valeurs:
        return [0.0, 0.0]
    alea = random.Random(graine)  # noqa: S311 (tirage statistique, pas de securite)
    gains = sorted(sum(alea.choice(valeurs) for _ in valeurs) for _ in range(tirages))
    return [round(gains[int(0.025 * tirages)], 2), round(gains[int(0.975 * tirages) - 1], 2)]


def wilson(resume: Resume) -> list[float]:
    ic = Taux(round(resume.moyenne), resume.effectif).intervalle()
    return [round(ic[0], 3), round(ic[1], 3)] if ic else []


def _plafonds(x: Resume, r0: dict) -> list[tuple[str, bool, str]]:
    cout_ok = x.cout_production < float(r0["cout_production_max_usd"])
    p95_ok = x.premier_mot_p95 is None or x.premier_mot_p95 < float(r0["premier_mot_p95_max_ms"])
    return [
        (
            "1. garde-fous du code",
            not x.echecs_garde,
            "tiennent" if not x.echecs_garde else "; ".join(x.echecs_garde),
        ),
        (
            "5. cout de production par question",
            cout_ok,
            f"{x.cout_production:.6f} $ (plafond {r0['cout_production_max_usd']} $)",
        ),
        (
            "6. premier mot au 95e centile",
            p95_ok,
            f"{x.premier_mot_p95} ms (plafond {r0['premier_mot_p95_max_ms']} ms)",
        ),
    ]


def comparables(base: Resume, challenger: Resume) -> list[str]:
    """Meme jeu, meme juge, meme corpus lu : sinon on ne compare pas."""
    ecarts = []
    for cle, nom in (("jeu", "jeu"), ("juge", "juge"), ("corpus_source", "corpus lu")):
        a, b = base.empreintes.get(cle), challenger.empreintes.get(cle)
        if a and b and a != b:
            ecarts.append(f"{nom} different ({a} contre {b})")
    return ecarts


def r0(base: Resume, challenger: Resume, g: int, regles: dict) -> dict:
    """Regle R0 entre la base et un challenger. Rend la decision et ses conditions."""
    p = regles["r0"]
    info = regles.get("information", {})
    gain = challenger.moyenne - base.moyenne
    conditions = _plafonds(challenger, p)
    conditions.insert(
        1,
        (
            "2. gain net moyen >= G",
            gain >= g,
            f"{gain:+.2f} questions sur {base.effectif}, G = {g}",
        ),
    )
    hausse = challenger.refus_a_tort - base.refus_a_tort
    conditions.insert(
        2,
        (
            "3. refus a tort",
            hausse <= float(p["refus_a_tort_hausse_max"]),
            f"{hausse:+.2f} (au plus +{p['refus_a_tort_hausse_max']})",
        ),
    )
    pertes = {
        f: challenger.familles.get(f, 0.0) - v
        for f, v in base.familles.items()
        if challenger.familles.get(f, 0.0) - v < -float(p["famille_perte_max"])
    }
    conditions.insert(
        3,
        (
            "4. aucune famille ne perd plus de 2",
            not pertes,
            "aucune" if not pertes else ", ".join(f"{f} {d:+.2f}" for f, d in pertes.items()),
        ),
    )
    ok = {nom[:1]: v for nom, v, _ in conditions}
    ecarts = comparables(base, challenger)
    information = {
        "mcnemar": mcnemar_exact(base.par_question, challenger.par_question),
        "wilson_base": wilson(base),
        "wilson_challenger": wilson(challenger),
        "bootstrap_gain_ic95": bootstrap_groupe(
            base.par_question,
            challenger.par_question,
            {**base.groupes, **challenger.groupes},
            int(info.get("bootstrap_tirages", 2000)),
            int(info.get("graine", 0)),
        ),
    }
    if ecarts:
        decision, motif = "non_comparable", "; ".join(ecarts)
    elif base.erreurs or challenger.erreurs:
        decision = "a_rejouer"
        motif = (
            f"erreurs techniques restantes : base {base.erreurs}, challenger {challenger.erreurs}"
        )
    elif all(ok.values()):
        decision, motif = "remplace", "les six conditions tiennent"
    elif abs(gain) < g and all(ok[k] for k in "13456"):
        decision, motif = egalite(base, challenger, p)
    else:
        rates = [nom for nom, v, _ in conditions if not v]
        decision, motif = "garde_la_base", "condition(s) non tenue(s) : " + ", ".join(rates)
    return {
        "decision": decision,
        "motif": motif,
        "gain": round(gain, 2),
        "G": g,
        "conditions": [{"nom": n, "tenue": v, "detail": d} for n, v, d in conditions],
        "information": information,
    }


def egalite(base: Resume, challenger: Resume, p: dict) -> tuple[str, str]:
    """Gain sous G, garde-fous tenus : la moins chere, puis la plus simple, puis la base."""
    a, b = base.cout_production, challenger.cout_production
    bas = min(a, b)
    proches = (max(a, b) - bas) <= float(p["egalite_ecart_cout"]) * bas if bas > 0 else a == b
    if not proches:
        if b < a:
            return "remplace", f"egalite : le challenger est moins cher ({b:.6f} contre {a:.6f} $)"
        return "garde_la_base", f"egalite : la base est moins chere ({a:.6f} contre {b:.6f} $)"
    if challenger.complexite < base.complexite:
        return "remplace", "egalite, couts a 20 % : le challenger est plus simple"
    return "garde_la_base", "egalite, couts a 20 % : la base est aussi simple ou plus simple"


def architecture(candidats: list[Resume], g: int, regles: dict) -> dict:
    """Regle de la fiche 3.12 entre A, B et C (ou plus) : la meilleure reussite gagne si
    elle bat chacune des autres d'au moins G ; sinon, parmi celles a moins de G de la
    meilleure, la moins chere, puis la plus simple. Garde-fous et plafonds d'abord."""
    p = regles["r0"]
    eligibles = [c for c in candidats if all(v for _, v, _ in _plafonds(c, p)) and not c.erreurs]
    ecartees = {
        c.nom: [n for n, v, _ in _plafonds(c, p) if not v] or ["erreurs techniques"]
        for c in candidats
        if c not in eligibles
    }
    if not eligibles:
        return {"gagnante": None, "motif": "aucune configuration eligible", "ecartees": ecartees}
    meilleure = max(eligibles, key=lambda c: c.moyenne)
    autres = [c for c in eligibles if c is not meilleure]
    if all(meilleure.moyenne - c.moyenne >= g for c in autres):
        return {
            "gagnante": meilleure.nom,
            "motif": f"bat chacune des autres d'au moins G = {g}",
            "ecartees": ecartees,
        }
    proches = [c for c in eligibles if meilleure.moyenne - c.moyenne < g]
    moins_chere = min(proches, key=lambda c: c.cout_production)
    bas = moins_chere.cout_production
    voisines = [
        c
        for c in proches
        if c.cout_production - bas <= float(p["egalite_ecart_cout"]) * bas
        or c.cout_production == bas
    ]
    choisie = min(voisines, key=lambda c: (c.complexite, c.cout_production))
    motif = f"egalite a moins de G = {g} entre {', '.join(c.nom for c in proches)} : " + (
        "la moins chere"
        if len(voisines) == 1
        else f"couts a 20 % ({', '.join(c.nom for c in voisines)}), la plus simple"
    )
    return {"gagnante": choisie.nom, "motif": motif, "ecartees": ecartees}


def _ligne(x: Resume) -> str:
    ic = wilson(x)
    p95 = "n.d." if x.premier_mot_p95 is None else f"{x.premier_mot_p95:.0f} ms"
    return (
        f"| {x.nom} | {x.architecture} | {x.passages} | {x.moyenne:.2f} sur {x.effectif} "
        f"(IC95 {ic[0]:.0%} a {ic[1]:.0%}) | {x.refus_a_tort:.2f} | {x.cout_production:.6f} $ | "
        f"{x.cout_reel:.6f} $ | {x.cout_juge:.4f} $ | {p95} | "
        f"{'oui' if not x.echecs_garde else 'NON'} | {x.erreurs} |"
    )


def rapport(
    resumes: list[Resume], verdicts: list[tuple[str, dict]], g: int, sigma_info: dict, statut: str
) -> str:
    lignes = [
        "# Comparaison",
        "",
        f"Regles de decision : {statut} (evaluation/regles-decision.toml). "
        f"G = {g} (sigma {sigma_info.get('sigma')} sur {sigma_info.get('passages')} passages "
        "de la base).",
        "",
        "| Configuration | Archi. | Passages | Reussites moyennes | Refus a tort | "
        "Cout prod. / question | Cout reel / question | Juge / passage | Premier mot p95 | "
        "Garde-fous | Erreurs |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
        *(_ligne(x) for x in resumes),
        "",
    ]
    if statut != "scellees":
        lignes += [
            f"**Attention : regles {statut}. Verdict indicatif, il ne compte pas (etude, 5.3).**",
            "",
        ]
    if not sigma_info.get("assez_de_passages", True):
        lignes += ["**Attention : moins de 5 passages de la base pour sigma.**", ""]
    for titre, v in verdicts:
        lignes.append(f"## {titre}")
        lignes.append("")
        if "decision" in v:
            lignes.append(f"**Decision : {v['decision']}** ({v['motif']})")
            lignes.append("")
            for c in v["conditions"]:
                lignes.append(
                    f"- {'tenue' if c['tenue'] else 'NON TENUE'} : {c['nom']}, {c['detail']}"
                )
            i = v["information"]
            m = i["mcnemar"]
            lignes += [
                "",
                f"Information : McNemar exact p = {m['p']} ({m['base_seule']} questions "
                f"reussies par la base seule, {m['challenger_seul']} par le challenger seul) ; "
                f"gain, IC95 bootstrap groupe : {i['bootstrap_gain_ic95'][0]:+} a "
                f"{i['bootstrap_gain_ic95'][1]:+} questions.",
            ]
        else:
            lignes.append(f"**Gagnante : {v['gagnante']}** ({v['motif']})")
            for nom, raisons in v.get("ecartees", {}).items():
                lignes.append(f"- ecartee : {nom} ({', '.join(raisons)})")
        lignes.append("")
    return "\n".join(lignes)


def _charger_bruit(chemin: Path | None, base: list[dict], regles: dict) -> dict:
    if chemin:
        return json.loads(Path(chemin).read_text(encoding="utf-8"))
    if len(base) >= 3:
        return bruit(base, regles)
    raise SystemExit("G inconnu : --bruit build/.../bruit.json, ou au moins 3 passages de la base")


def main_bruit(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Bruit de la base : sigma et G (regle R0).")
    p.add_argument("passages", nargs="+", help="sorties d'assistant-evaluer (motifs glob permis)")
    p.add_argument("--sortie", type=Path, default=Path("build/comparaison/bruit.json"))
    a = p.parse_args(argv)
    b = bruit(lire_passages(a.passages))
    a.sortie.parent.mkdir(parents=True, exist_ok=True)
    a.sortie.write_text(json.dumps(b, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"sigma = {b['sigma']} sur {b['passages']} passages, G = {b['G']} -> {a.sortie}")
    if not b["assez_de_passages"]:
        print("ATTENTION : moins de 5 passages (regle 6 de la conception)")
    if b["configs_differentes"]:
        print("ATTENTION : les passages ne jouent pas tous la meme configuration")
    return 0


def main_comparer(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Applique la regle de decision gelee.")
    p.add_argument("--base", nargs="+", help="passages de la base (motifs glob permis)")
    p.add_argument("--challenger", nargs="+", help="passages du challenger")
    p.add_argument(
        "--architecture",
        nargs="+",
        metavar="NOM=MOTIF",
        help="regle de la fiche 3.12 entre plusieurs configurations ; la premiere est la base",
    )
    p.add_argument("--bruit", type=Path)
    p.add_argument("--regles", type=Path, default=None)
    p.add_argument("--rapport", type=Path, help="ecrit aussi le rapport en Markdown")
    p.add_argument("--sortie", type=Path, help="ecrit aussi le verdict en JSON")
    a = p.parse_args(argv)
    regles = charger_regles(a.regles)
    statut = statut_regles(a.regles)
    if a.architecture:
        groupes = []
        for item in a.architecture:
            nom, _, motif = item.partition("=")
            if not motif:
                p.error(f"{item} : attendu NOM=MOTIF")
            groupes.append((nom, lire_passages(motif)))
        base_passages = groupes[0][1]
        b = _charger_bruit(a.bruit, base_passages, regles)
        resumes = [resumer(nom, ps, regles) for nom, ps in groupes]
        verdicts: list[tuple[str, dict]] = [
            ("Architecture (fiche 3.12)", architecture(resumes, int(b["G"]), regles))
        ]
        for x in resumes[1:]:
            verdicts.append(
                (f"R0 : {resumes[0].nom} contre {x.nom}", r0(resumes[0], x, int(b["G"]), regles))
            )
    else:
        if not a.base or not a.challenger:
            p.error("--base et --challenger, ou --architecture")
        base_passages = lire_passages(a.base)
        b = _charger_bruit(a.bruit, base_passages, regles)
        base = resumer("base", base_passages, regles)
        chal = resumer("challenger", lire_passages(a.challenger), regles)
        resumes = [base, chal]
        if chal.passages < int(regles["r0"]["repetitions_challenger"]):
            print(f"ATTENTION : challenger joue {chal.passages} fois (K = 2 prevu)")
        verdicts = [("R0 : base contre challenger", r0(base, chal, int(b["G"]), regles))]
    texte = rapport(resumes, verdicts, int(b["G"]), b, statut)
    print(texte)
    if a.rapport:
        a.rapport.parent.mkdir(parents=True, exist_ok=True)
        a.rapport.write_text(texte + "\n", encoding="utf-8")
    if a.sortie:
        a.sortie.parent.mkdir(parents=True, exist_ok=True)
        a.sortie.write_text(
            json.dumps(
                {"regles": statut, "G": b["G"], "verdicts": dict(verdicts)},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main_comparer())
