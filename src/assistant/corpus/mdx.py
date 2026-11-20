"""Source locale : les MDX du portfolio, leurs faits.yml et identite.yml.

La prose des pages ne contient aucun chiffre : ils sont injectes au rendu par
<Metrique id="...">. On les resout ici depuis faits.yml, sur une liste blanche
de champs affiches. Jamais `source`, `verification` ni les commentaires YAML.

Le contenu du portfolio sera reecrit : rien ici ne depend d'un texte precis,
seulement des conventions des composants. Relancer l'ingestion suffit.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from assistant.corpus import rendu
from assistant.modeles import Page, Section

CHAMPS_PUBLICS = (
    "libelle",
    "valeur",
    "unite",
    "affichage",
    "jeu",
    "statut",
    "effectif",
    "methode",
    "glose",
    "limite",
)

LIBELLES = {
    "fr": {
        "quoi": "Ce que c'est",
        "dur": "Ce qui était dur",
        "prouve": "Ce que ça prouve",
        "fiche": "Fiche du projet",
        "chiffres": "Chiffres publiés",
        "periode": "Période",
        "equipe": "Équipe",
        "personne": "personne",
        "personnes": "personnes",
        "cadre": "Cadre",
        "stack": "Technologies",
        "methode": "Méthode",
        "limite": "Limite",
        "jeu": "Jeu de mesure",
        "effectif": "Effectif",
        "accroche": "En bref",
        "professionnel": "projet professionnel",
        "academique": "projet d'école",
        "personnel": "projet personnel",
        "parcours": "Parcours",
    },
    "en": {
        "quoi": "What it is",
        "dur": "What was hard",
        "prouve": "What it proves",
        "fiche": "Project sheet",
        "chiffres": "Published figures",
        "periode": "Period",
        "equipe": "Team",
        "personne": "person",
        "personnes": "people",
        "cadre": "Setting",
        "stack": "Technologies",
        "methode": "Method",
        "limite": "Limit",
        "jeu": "Measurement set",
        "effectif": "Sample size",
        "accroche": "In short",
        "professionnel": "professional project",
        "academique": "school project",
        "personnel": "personal project",
        "parcours": "Background",
    },
}

RE_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
RE_IDS = re.compile(r"ids=\{\[(.*?)\]\}", re.S)
RE_ATTR = re.compile(r'(\w+)="([^"]*)"')


def _txt(champ, langue: str) -> str:
    if isinstance(champ, dict):
        return str(champ.get(langue, "")).strip()
    return "" if champ is None else str(champ).strip()


def _metrique_publique(m: dict) -> dict:
    return {k: m[k] for k in CHAMPS_PUBLICS if k in m}


def _phrase_metrique(m: dict, langue: str, detaillee: bool) -> str:
    lib = LIBELLES[langue]
    base = f"{_txt(m.get('libelle'), langue)} : {rendu.valeur_metrique(m, langue)}."
    if not detaillee:
        return base
    morceaux = [base]
    if m.get("effectif") not in (None, "INCONNU"):
        morceaux.append(f"{lib['effectif']} : {rendu.nombre(float(m['effectif']), langue)}.")
    if m.get("methode"):
        morceaux.append(f"{lib['methode']} : {_txt(m['methode'], langue)}")
    if m.get("glose"):
        morceaux.append(_txt(m["glose"], langue))
    if m.get("limite"):
        morceaux.append(f"{lib['limite']} : {_txt(m['limite'], langue)}")
    return " ".join(morceaux)


class ResolveurComposants:
    """Remplace les composants MDX par le texte qu'un visiteur lit."""

    def __init__(self, faits: dict, langue: str) -> None:
        self.langue = langue
        self.metriques = {m["id"]: _metrique_publique(m) for m in faits.get("metriques", [])}
        self.references = {r["id"]: r for r in faits.get("references", []) or []}

    def _metrique(self, ident: str) -> str:
        m = self.metriques.get(ident)
        if m is None:
            raise KeyError(f"metrique inconnue : {ident}")
        return rendu.valeur_metrique(m, self.langue)

    def _liste(self, ids_brut: str) -> list[str]:
        return re.findall(r'"([^"]+)"', ids_brut)

    def resoudre(self, corps: str) -> str:
        lib = LIBELLES[self.langue]
        corps = re.sub(r"\{/\*.*?\*/\}", "", corps, flags=re.S)
        corps = re.sub(r"^(import|export) .*$", "", corps, flags=re.M)

        def verdict(mo: re.Match) -> str:
            interieur = mo.group(2)
            lignes = []
            for slot, texte in re.findall(
                r'<Fragment slot="(\w+)">(.*?)</Fragment>', interieur, flags=re.S
            ):
                lignes.append(f"**{lib.get(slot, slot)}.** {' '.join(texte.split())}")
            ids = RE_IDS.search(mo.group(1))
            for ident in self._liste(ids.group(1)) if ids else []:
                lignes.append(_phrase_metrique(self.metriques[ident], self.langue, False))
            return "\n\n".join(lignes)

        corps = re.sub(r"<Verdict([^>]*)>(.*?)</Verdict>", verdict, corps, flags=re.S)

        def encadre(mo: re.Match) -> str:
            attrs = dict(RE_ATTR.findall(mo.group(1)))
            ids = RE_IDS.search(mo.group(1))
            lignes = [attrs.get("titre", "").strip()] if attrs.get("titre") else []
            for ident in self._liste(ids.group(1)) if ids else []:
                lignes.append(_phrase_metrique(self.metriques[ident], self.langue, False))
            return "\n\n".join(lignes)

        corps = re.sub(r"<Encadre\b(.*?)/>", encadre, corps, flags=re.S)
        corps = re.sub(
            r'<Metrique\b[^>]*?id="([^"]+)"[^>]*?/>',
            lambda mo: self._metrique(mo.group(1)),
            corps,
            flags=re.S,
        )

        def reference(mo: re.Match) -> str:
            r = self.references.get(mo.group(1))
            if not r:
                return ""
            return f"{_txt(r.get('auteur'), self.langue)} ({r.get('annee')})"

        corps = re.sub(r'<Reference\b[^>]*?id="([^"]+)"[^>]*?/>', reference, corps, flags=re.S)
        # Composants visuels sans texte : retires.
        corps = re.sub(
            r"<(Schema|Photo|ControleArchi|ConsoleLeyenda)\b.*?/>", "", corps, flags=re.S
        )
        # Composants qui encadrent du texte (Preuve, Exergue, a, ...) : on garde le texte,
        # jamais les attributs (une Preuve porte le chemin de sa source interne).
        corps = re.sub(r"</?[A-Za-z][\w.]*\b[^>]*?/?>", "", corps, flags=re.S)
        return corps


def nettoyer_markdown(texte: str) -> str:
    texte = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", texte)
    texte = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", texte)
    texte = re.sub(r"\*\*(.+?)\*\*", r"\1", texte, flags=re.S)
    texte = re.sub(r"(?<!\w)[*_](\S(?:.*?\S)?)[*_](?!\w)", r"\1", texte)
    texte = texte.replace("`", "")
    paragraphes = [" ".join(p.split()) for p in re.split(r"\n\s*\n", texte)]
    return "\n\n".join(p for p in paragraphes if p)


def decouper_sections(corps: str, titre_page: str) -> list[Section]:
    sections: list[Section] = []
    h2 = ""
    chemin: tuple[str, ...] = ()
    ancre = ""
    tampon: list[str] = []

    def vider() -> None:
        texte = nettoyer_markdown("\n".join(tampon))
        if texte:
            sections.append(Section(titre_page, chemin, ancre, texte))
        tampon.clear()

    for ligne in corps.splitlines():
        mo = re.match(r"^(#{2,3})\s+(.+?)\s*$", ligne)
        if mo:
            vider()
            titre = nettoyer_markdown(mo.group(2))
            if len(mo.group(1)) == 2:
                h2 = titre
                chemin = (h2,)
            else:
                chemin = (h2, titre) if h2 else (titre,)
            ancre = rendu.ancre(titre)
            continue
        tampon.append(ligne)
    vider()
    return sections


def _url(langue: str, chemin: str) -> str:
    return f"/{chemin}" if langue == "fr" else f"/en/{chemin}"


def page_projet(dossier: Path, langue: str) -> Page | None:
    fichier = dossier / f"{langue}.mdx"
    if not fichier.exists():
        return None
    faits = yaml.safe_load((dossier / "faits.yml").read_text(encoding="utf-8")) or {}
    brut = fichier.read_text(encoding="utf-8")
    mo = RE_FRONTMATTER.match(brut)
    entete = yaml.safe_load(mo.group(1)) if mo else {}
    corps = brut[mo.end() :] if mo else brut
    projet = str(entete.get("projet") or faits.get("id") or dossier.name)
    titre = str(entete.get("titre", projet)).strip()
    lib = LIBELLES[langue]

    resolveur = ResolveurComposants(faits, langue)
    sections = decouper_sections(resolveur.resoudre(corps), titre)

    fiche = []
    if entete.get("accroche"):
        fiche.append(f"{lib['accroche']} : {str(entete['accroche']).strip()}")
    if faits.get("periode"):
        fiche.append(f"{lib['periode']} : {rendu.periode(str(faits['periode']), langue)}.")
    if faits.get("personnes"):
        n = int(faits["personnes"])
        fiche.append(f"{lib['equipe']} : {n} {lib['personne'] if n == 1 else lib['personnes']}.")
    if faits.get("nature"):
        fiche.append(f"{lib['cadre']} : {lib.get(faits['nature'], faits['nature'])}.")
    if faits.get("stack"):
        fiche.append(f"{lib['stack']} : {', '.join(map(str, faits['stack']))}.")
    sections.insert(0, Section(titre, (lib["fiche"],), "", "\n\n".join(fiche), type="fiche"))

    for m in faits.get("metriques", []):
        publique = _metrique_publique(m)
        sections.append(
            Section(
                titre,
                (lib["chiffres"], _txt(m.get("libelle"), langue)),
                "",
                _phrase_metrique(publique, langue, True),
                type="metrique",
            )
        )
    return Page(_url(langue, f"projets/{projet}"), langue, projet, titre, tuple(sections))


def page_parcours(identite: dict, langue: str) -> Page:
    lib = LIBELLES[langue]
    fr = langue == "fr"
    titre_cv = _txt(identite.get("titre", {}).get("cv"), langue)
    sections = [
        Section(
            lib["parcours"],
            (lib["parcours"],),
            "",
            f"{identite.get('nom', '')}, {titre_cv}.",
            type="parcours",
        )
    ]
    d = identite.get("diplome") or {}
    if d:
        fin = rendu.mois(str(d.get("obtention", "")), langue)
        texte = (
            f"Formation : {d.get('intitule')}, {d.get('ecole')}, fin de formation en {fin}."
            if fr
            else f"Education: {d.get('intitule')}, {d.get('ecole')}, completed in {fin}."
        )
        sections.append(
            Section(
                lib["parcours"], ("Formation" if fr else "Education",), "", texte, type="parcours"
            )
        )
    a = identite.get("alternance") or {}
    if a:
        debut = rendu.mois(str(a.get("debut", "")), langue)
        de = "d'" if debut[:1].lower() in "aeiouéèh" else "de "
        texte = (
            f"Alternance {a.get('preposition', '')}{a.get('employeur')}, {a.get('ville')}, "
            f"à partir {de}{debut}."
            if fr
            else f"Work-study at {a.get('employeur')}, {a.get('ville')}, starting {debut}."
        )
        sections.append(
            Section(
                lib["parcours"], ("Alternance" if fr else "Work-study",), "", texte, type="parcours"
            )
        )
    if identite.get("disponibilite"):
        dispo = rendu.mois(str(identite["disponibilite"]), langue)
        ville = identite.get("ville", "")
        de = "d'" if dispo[:1].lower() in "aeiouéèh" else "de "
        texte = (
            f"Disponible à partir {de}{dispo}, {ville}."
            if fr
            else f"Available from {dispo}, {ville}."
        )
        sections.append(
            Section(
                lib["parcours"],
                ("Disponibilité" if fr else "Availability",),
                "",
                texte,
                type="parcours",
            )
        )
    if identite.get("langues"):
        lignes = []
        for item in identite["langues"]:
            precision = f" ({item['precision']})" if item.get("precision") else ""
            lignes.append(f"{item['langue']} : {item['niveau']}{precision}.")
        sections.append(
            Section(
                lib["parcours"],
                ("Langues" if fr else "Languages",),
                "",
                " ".join(lignes),
                type="parcours",
            )
        )
    for famille in identite.get("competences", []) or []:
        nom = _txt(famille.get("famille"), langue)
        elements = ", ".join(_txt(e, langue) for e in famille.get("elements", []))
        sections.append(
            Section(
                lib["parcours"],
                ("Compétences" if fr else "Skills", nom),
                "",
                f"{nom} : {elements}.",
                type="parcours",
            )
        )
    return Page(_url(langue, "parcours"), langue, "parcours", lib["parcours"], tuple(sections))


def coordonnees(identite: dict, site: str) -> dict:
    """Carte de contact affichee par le code, jamais recopiee par le modele."""
    cv = identite.get("cv") or {}
    return {
        "nom": identite.get("nom", ""),
        "courriel": identite.get("email", ""),
        "linkedin": (identite.get("profils") or {}).get("linkedin", ""),
        "cv": {k: f"{site.rstrip('/')}{v}" for k, v in cv.items()},
        "contact": {"fr": f"{site.rstrip('/')}/contact", "en": f"{site.rstrip('/')}/en/contact"},
    }


class SourceMdx:
    """Lit src/content du portfolio : projets/*/{fr,en}.mdx, faits.yml, identite.yml."""

    def __init__(self, racine: Path, site: str) -> None:
        self.racine = Path(racine)
        self.site = site

    def _identite(self) -> dict:
        chemin = self.racine / "identite.yml"
        return yaml.safe_load(chemin.read_text(encoding="utf-8")) if chemin.exists() else {}

    def pages(self) -> list[Page]:
        pages: list[Page] = []
        for dossier in sorted((self.racine / "projets").iterdir()):
            if not (dossier / "faits.yml").exists():
                continue
            for langue in ("fr", "en"):
                page = page_projet(dossier, langue)
                if page:
                    pages.append(page)
        identite = self._identite()
        if identite:
            pages.extend(page_parcours(identite, lg) for lg in ("fr", "en"))
        return pages

    def coordonnees(self) -> dict:
        return coordonnees(self._identite(), self.site)
