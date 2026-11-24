"""Validation du juge par Noan : page de notation, puis accord juge / Noan.

Regle de la conception : on ne se fie au juge qu'avec un accord d'au moins 90 %.
Sous ce seuil, c'est la consigne du juge qui se corrige, pas l'assistant.

1. Page autonome (aucun reseau, aucune dependance), ecrite dans build/ :
    assistant-noter --resultats build/eval-candidate.json
   Le verdict du juge n'y figure pas : Noan note a l'aveugle. Le bouton
   "Exporter" telecharge notes-noan.json.
2. Accord :
    assistant-accord --resultats build/eval-candidate.json --notes notes-noan.json

Seules les notes que le juge decide entrent : questions a repondre (juste) et
refus rediges par le modele (refus correct). Cartes de contact et refus ecrits
par le code se controlent sans juge.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import sys
from pathlib import Path

from assistant.evaluation import jeu as jeu_mod
from assistant.evaluation.mesures import Taux

ACCORD_MIN = 0.90
KAPPA_MIN = 0.70  # etude, fiche 3.11 : accord d'au moins 90 % et kappa de Cohen d'au moins 0,7
PASSAGES_MAX = 30  # au-dela (tout_le_corpus), la page montre les seuls passages cites


def _questions(sortie: dict, chemin_jeu: Path | None) -> dict[str, jeu_mod.QuestionEval]:
    if chemin_jeu is None:
        nom = sortie.get("jeu", "travail")
        chemin_jeu = {"travail": jeu_mod.JEU_TRAVAIL, "reserve": jeu_mod.RESERVE}.get(
            nom, Path(nom)
        )
    return {q.id: q for q in jeu_mod.charger(chemin_jeu)}


def verdict_juge(q: jeu_mod.QuestionEval, x: dict) -> bool | None:
    """Ce que le juge a decide et qui compte dans une mesure ; None si le code decide."""
    if q.attendu == "carte":
        return None
    if q.attendu == "repondre":
        return bool(x["jugement"]["juste"])
    if x.get("chemin", "").startswith("refus"):
        return None  # refus ecrit d'avance : compte comme reussi sans le juge
    return bool(x["jugement"]["refus_correct"])


def a_noter(sortie: dict, chemin_jeu: Path | None = None, prefixe: str = "") -> list[dict]:
    questions = _questions(sortie, chemin_jeu)
    elements = []
    for ident, x in sortie["resultats"].items():
        q = questions.get(ident)
        if q is None:
            continue
        v = verdict_juge(q, x)
        if v is None:
            continue
        passages = x.get("passages", [])
        if len(passages) > PASSAGES_MAX:
            passages = x.get("passages_cites", [])
        elements.append(
            {
                "id": f"{prefixe}{ident}",
                "question": q.question,
                "langue": q.langue,
                "attendu": q.attendu,
                "famille": q.famille,
                "faits": list(q.faits_cles),
                "reponse": x.get("texte", ""),
                "sources": x.get("sources", []),
                "passages": passages,
                "juge": v,
                "motif_juge": x["jugement"].get("motif", ""),
            }
        )
    return sorted(elements, key=lambda e: e["id"])


def _carte(e: dict) -> str:
    h = html.escape
    question_a = "une réponse juste" if e["attendu"] == "repondre" else "un refus correct"
    faits = "".join(f"<li>{h(f)}</li>" for f in e["faits"]) or "<li>(aucun)</li>"
    sources = "".join(f"<li>{h(s)}</li>" for s in e["sources"]) or "<li>(aucune)</li>"
    passages = "".join(f"<li><pre>{h(str(p.get('texte', '')))}</pre></li>" for p in e["passages"])
    bloc_passages = (
        f"<details><summary>Passages transmis ({len(e['passages'])})</summary>"
        f"<ol>{passages}</ol></details>"
        if passages
        else ""
    )
    return f"""
<article class="carte" data-id="{h(e["id"])}">
  <header><span class="id">{h(e["id"])}</span>
    <span class="meta">{h(e["langue"])} · {h(e["famille"])} · attendu : {question_a}</span></header>
  <p class="question" lang="{h(e["langue"])}">{h(e["question"])}</p>
  <h3>Faits attendus</h3><ul>{faits}</ul>
  <h3>Réponse</h3><p class="reponse" lang="{h(e["langue"])}">{h(e["reponse"]) or "(vide)"}</p>
  <h3>Sources citées</h3><ul class="sources">{sources}</ul>
  {bloc_passages}
  <div class="choix" role="group" aria-label="Note de {h(e["id"])}">
    <button type="button" data-note="true" aria-pressed="false">Juste</button>
    <button type="button" data-note="false" aria-pressed="false">Faux</button>
  </div>
</article>"""


PAGE = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Notation du juge</title>
<style>
:root {{ --fond:#fafaf7; --texte:#1d1d1b; --doux:#5c5c58; --bord:#d9d9d2; --carte:#fff;
  --oui:#1f7a4d; --non:#b3261e; }}
@media (prefers-color-scheme: dark) {{ :root {{ --fond:#151514; --texte:#ecece6; --doux:#a3a39c;
  --bord:#3a3a36; --carte:#1f1f1d; --oui:#5cc28e; --non:#f2867e; }} }}
body {{ margin:0; background:var(--fond); color:var(--texte);
  font:16px/1.5 system-ui, sans-serif; }}
main {{ max-width:860px; margin:0 auto; padding:16px; }}
.barre {{ position:sticky; top:0; background:var(--fond); padding:12px 0;
  border-bottom:1px solid var(--bord); display:flex; gap:12px; align-items:center;
  flex-wrap:wrap; }}
.carte {{ background:var(--carte); border:1px solid var(--bord); border-radius:8px;
  padding:16px; margin:16px 0; }}
.carte header {{ display:flex; gap:12px; flex-wrap:wrap; color:var(--doux); }}
.id {{ font-weight:700; color:var(--texte); }}
.question {{ font-weight:600; font-size:1.1em; }}
h3 {{ font-size:.85em; text-transform:uppercase; letter-spacing:.04em; color:var(--doux);
  margin:12px 0 4px; }}
.reponse {{ white-space:pre-wrap; }}
pre {{ white-space:pre-wrap; font-size:.85em; }}
.sources li {{ overflow-wrap:anywhere; }}
button {{ font:inherit; padding:8px 16px; border-radius:6px; border:1px solid var(--bord);
  background:transparent; color:var(--texte); cursor:pointer; }}
.choix {{ display:flex; gap:8px; margin-top:12px; }}
button[data-note="true"][aria-pressed="true"] {{ background:var(--oui); color:#fff;
  border-color:var(--oui); }}
button[data-note="false"][aria-pressed="true"] {{ background:var(--non); color:#fff;
  border-color:var(--non); }}
</style></head>
<body><main>
<h1>Notation du juge</h1>
<p>Source : {source}. {n} réponses à noter. Le verdict du juge est masqué : notez sans lui.
Une réponse est juste si elle contient les faits attendus, sans fait contraire ni
chiffre faux ; un fait ajouté exact n'est pas une faute.</p>
<div class="barre"><span id="compte">0 sur {n} notées</span>
<button type="button" id="exporter">Exporter les notes (JSON)</button></div>
{cartes}
</main>
<script>
const SOURCE = {source_json};
const CLE = "notes-juge:" + SOURCE;
let notes = {{}};
try {{ notes = JSON.parse(localStorage.getItem(CLE) || "{{}}"); }} catch (e) {{ notes = {{}}; }}
function afficher() {{
  document.querySelectorAll(".carte").forEach((c) => {{
    const v = notes[c.dataset.id];
    c.querySelectorAll("button").forEach((b) => {{
      b.setAttribute("aria-pressed", String(v !== undefined && String(v) === b.dataset.note));
    }});
  }});
  document.getElementById("compte").textContent =
    Object.keys(notes).length + " sur {n} notées";
}}
document.querySelectorAll(".choix button").forEach((b) => {{
  b.addEventListener("click", () => {{
    notes[b.closest(".carte").dataset.id] = b.dataset.note === "true";
    try {{ localStorage.setItem(CLE, JSON.stringify(notes)); }} catch (e) {{}}
    afficher();
  }});
}});
document.getElementById("exporter").addEventListener("click", () => {{
  const donnees = {{ resultats: SOURCE, le: new Date().toISOString(), notes: notes }};
  const blob = new Blob([JSON.stringify(donnees, null, 2)], {{ type: "application/json" }});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "notes-noan.json";
  a.click();
  URL.revokeObjectURL(a.href);
}});
afficher();
</script>
</body></html>
"""


def page_html(elements: list[dict], source: str) -> str:
    return PAGE.format(
        source=html.escape(source),
        source_json=json.dumps(source).replace("<", "\\u003c"),
        n=len(elements),
        cartes="\n".join(_carte(e) for e in elements),
    )


def echantillon(groupes: list[list[dict]], n: int, graine: int = 20261124) -> list[dict]:
    """n elements tires a parts egales de chaque fichier, stratifies par famille
    (repartition proportionnelle, au moins un par famille presente), graine fixe :
    la page de notation et le calcul de l'accord retrouvent le meme tirage."""
    import random

    alea = random.Random(graine)  # noqa: S311 (tirage d'echantillon, pas de securite)
    part = n // len(groupes) if groupes else 0
    choisis: list[dict] = []
    for elements in groupes:
        familles: dict[str, list[dict]] = {}
        for e in sorted(elements, key=lambda e: e["id"]):
            familles.setdefault(e["famille"], []).append(e)
        total = len(elements)
        quotas = {
            f: max(1, round(part * len(lot) / total)) if total else 0 for f, lot in familles.items()
        }
        while sum(quotas.values()) > min(part, total):
            plus = max(quotas, key=lambda f: (quotas[f], f))
            quotas[plus] -= 1
        while sum(quotas.values()) < min(part, total):
            moins = max(
                (f for f in quotas if quotas[f] < len(familles[f])),
                key=lambda f: (len(familles[f]) - quotas[f], f),
            )
            quotas[moins] += 1
        for f in sorted(familles):
            choisis.extend(alea.sample(familles[f], min(quotas[f], len(familles[f]))))
    return sorted(choisis, key=lambda e: e["id"])


def kappa_cohen(paires: list[tuple[bool, bool]]) -> float | None:
    if not paires:
        return None
    n = len(paires)
    po = sum(1 for a, b in paires if a == b) / n
    pa = sum(1 for a, _ in paires if a) / n
    pb = sum(1 for _, b in paires if b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe >= 1:
        return 1.0 if po == 1 else 0.0
    return round((po - pe) / (1 - pe), 4)


def accord(elements: list[dict], notes: dict[str, bool]) -> dict:
    notes_par_id = {k: bool(v) for k, v in notes.items()}
    notes_utiles = [e for e in elements if e["id"] in notes_par_id]
    desaccords = [
        {
            "id": e["id"],
            "question": e["question"],
            "juge": e["juge"],
            "noan": notes_par_id[e["id"]],
            "motif_juge": e["motif_juge"],
        }
        for e in notes_utiles
        if e["juge"] != notes_par_id[e["id"]]
    ]
    t = Taux(len(notes_utiles) - len(desaccords), len(notes_utiles))
    kappa = kappa_cohen([(bool(e["juge"]), notes_par_id[e["id"]]) for e in notes_utiles])
    return {
        "accord": t.vers_dict(),
        "kappa": kappa,
        "fiable": bool(t.effectif)
        and (t.valeur or 0) >= ACCORD_MIN
        and kappa is not None
        and kappa >= KAPPA_MIN,
        "seuil": ACCORD_MIN,
        "seuil_kappa": KAPPA_MIN,
        "non_notees": sorted(e["id"] for e in elements if e["id"] not in notes_par_id),
        "desaccords": desaccords,
    }


def texte_accord(a: dict) -> str:
    d = a["accord"]
    lignes = [
        f"Accord juge / Noan : {Taux(d['succes'], d['effectif'])} ; "
        f"kappa de Cohen : {a.get('kappa')}"
    ]
    if a["non_notees"]:
        lignes.append(f"Non notees ({len(a['non_notees'])}) : {', '.join(a['non_notees'])}")
    lignes.append(f"Desaccords ({len(a['desaccords'])}) :")
    for x in a["desaccords"]:
        juge = "juste" if x["juge"] else "faux"
        noan = "juste" if x["noan"] else "faux"
        lignes.append(f"- {x['id']} : juge {juge}, Noan {noan}. {x['question']}")
        lignes.append(f"    motif du juge : {x['motif_juge']}")
    lignes.append(
        "On peut se fier au juge (accord d'au moins 90 %, kappa d'au moins 0,7)."
        if a["fiable"]
        else "Accord sous 90 % ou kappa sous 0,7 : corriger la consigne du juge avant tout reglage."
    )
    return "\n".join(lignes)


def elements_de(resultats: list[Path], jeu: Path | None, n: int | None) -> list[dict]:
    """Un fichier : tous ses elements. Plusieurs : identifiants prefixes du nom du
    fichier, puis, avec n, un echantillon a parts egales stratifie par famille."""
    groupes = []
    for chemin in resultats:
        sortie = json.loads(chemin.read_text(encoding="utf-8"))
        prefixe = f"{chemin.stem}:" if len(resultats) > 1 else ""
        groupes.append(a_noter(sortie, jeu, prefixe))
    if n:
        return echantillon(groupes, n)
    return [e for g in groupes for e in g]


def main_page(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Ecrit la page de notation du juge par Noan.")
    p.add_argument("--resultats", type=Path, nargs="+", required=True)
    p.add_argument("--jeu", type=Path, help="jeu joue (par defaut : celui du fichier)")
    p.add_argument(
        "--echantillon",
        type=int,
        help="n reponses a parts egales des fichiers, stratifiees par famille (etape 0.3 : 100)",
    )
    p.add_argument("--sortie", type=Path, default=Path("build/notation-juge.html"))
    a = p.parse_args(argv)
    elements = elements_de(a.resultats, a.jeu, a.echantillon)
    a.sortie.parent.mkdir(parents=True, exist_ok=True)
    source = "+".join(c.name for c in a.resultats)
    a.sortie.write_text(page_html(elements, source), encoding="utf-8")
    print(f"{len(elements)} reponses a noter -> {a.sortie}")
    return 0


def main_accord(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Accord entre le juge et les notes de Noan.")
    p.add_argument("--resultats", type=Path, nargs="+", required=True)
    p.add_argument("--notes", type=Path, required=True)
    p.add_argument("--jeu", type=Path)
    p.add_argument("--echantillon", type=int, help="le meme n que pour la page de notation")
    p.add_argument("--sortie", type=Path, help="ecrit aussi le detail en JSON")
    a = p.parse_args(argv)
    notes = json.loads(a.notes.read_text(encoding="utf-8"))
    res = accord(elements_de(a.resultats, a.jeu, a.echantillon), notes.get("notes", notes))
    res["le"] = dt.datetime.now(dt.UTC).isoformat()
    res["resultats"] = [c.name for c in a.resultats]
    if a.sortie:
        a.sortie.parent.mkdir(parents=True, exist_ok=True)
        a.sortie.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(texte_accord(res))
    return 0 if res["fiable"] else 1


if __name__ == "__main__":
    sys.exit(main_page())
