"""CV publics du site (PDF) : extraction du texte et retrait des coordonnees.

Le CV porte un telephone, un courriel et la commune de residence (decision 22
du portfolio : le CV en porte, le site n'en porte aucune). Ce que l'assistant
indexe devient du contenu du site : tout est retire avant l'indexation. Les
coordonnees passent par la carte de contact, affichee par le code.

Extraction par pypdf (pur Python, maintenu). Le texte d'un CV mis en page sur
deux colonnes sort dans un ordre approximatif : on ne pretend pas retrouver ses
rubriques, on le coupe en paragraphes (titres, puis une entree par tiret).
"""

from __future__ import annotations

import io
import re

from pypdf import PdfReader

from assistant.modeles import Page, Section

# Telephone : forme internationale (+33 7 00 00 00 00, 0033 ...) ou nationale
# francaise (07 00 00 00 00, 07.00.00.00.00). Jamais un nombre du CV comme
# "12,15 M", "1 004 tests" ou "2023 - 2026".
TELEPHONE = re.compile(
    r"(?:(?<![\w+])(?:\+|00)\d{1,3}[\s.-]?(?:\(0\)[\s.-]?)?\d(?:[\s.-]?\d{2}){4}(?!\d))"
    r"|(?:(?<![\w,.])0[1-9](?:[\s.-]?\d{2}){4}(?!\d|,\d))"
)
COURRIEL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
VOIES = (
    r"rue|avenue|av\.|boulevard|bd|chemin|impasse|all[ée]e|place|quai|route|square"
    r"|cours|passage|r[ée]sidence|lotissement|street|st\.|road|rd\.|lane|drive|way"
)
# "12 rue des Lilas", "3 bis, avenue ...", jusqu'au bout de la ligne.
VOIE = re.compile(rf"\b\d{{1,4}}(?:\s?(?:bis|ter))?,?\s+(?:{VOIES})\b[^\n]*", re.I)
# Code postal suivi d'une commune : "78300 Ville".
CODE_POSTAL = re.compile(r"(?<![\d,.])\d{5}(?![\d,.])\s+[A-ZÀ-Ý][^\n]*")
# Ligne de lieu du bloc de coordonnees : "Commune (Region)", "Paris area (France)".
LIEU = re.compile(r"^[A-ZÀ-Ý][\w'’ -]{1,40}\s*\([A-ZÀ-Ý][\w'’ -]{1,40}\)$")
VOISINAGE_LIEU = 2  # lignes autour d'un telephone ou d'un courriel
TIRETS = re.compile(r"\s*[\N{EN DASH}\N{EM DASH}]\s*")
SEPARATEUR = re.compile(r"(\s+[·|•]\s+)")


def texte_pdf(contenu: bytes) -> tuple[str, str]:
    """(titre des metadonnees, texte de toutes les pages)."""
    lecteur = PdfReader(io.BytesIO(contenu))
    titre = ""
    if lecteur.metadata and lecteur.metadata.title:
        titre = str(lecteur.metadata.title).strip()
    pages = [(p.extract_text() or "") for p in lecteur.pages]
    return titre, "\n".join(pages)


def _despacer(ligne: str) -> str:
    """'C O M P É T E N C E S' -> 'COMPÉTENCES' (titres a lettres espacees)."""
    mots = re.split(r"\s{2,}", ligne.strip())
    sortie = []
    for mot in mots:
        jetons = mot.split(" ")
        if len(jetons) >= 2 and all(len(j) == 1 for j in jetons):
            mot = "".join(jetons)
        sortie.append(mot)
    return " ".join(sortie)


def _est_espacee(ligne: str) -> bool:
    jetons = ligne.split()
    return len(jetons) > 3 and sum(1 for j in jetons if len(j) == 1) >= 0.8 * len(jetons)


def _nettoyer_segment(segment: str, bloc_contact: bool) -> str:
    if bloc_contact and LIEU.match(segment.strip()):
        return ""
    for motif in (TELEPHONE, COURRIEL, VOIE, CODE_POSTAL):
        segment = motif.sub("", segment)
    return segment.strip()


def retirer_coordonnees(texte: str) -> str:
    """Retire telephones, courriels, adresses postales et la ligne de lieu du bloc
    de coordonnees (lignes voisines d'un telephone ou d'un courriel). Une ligne
    "a · b · c" est traitee segment par segment ; les lignes videes disparaissent."""
    lignes = texte.splitlines()
    contact = {
        i for i, ligne in enumerate(lignes) if TELEPHONE.search(ligne) or COURRIEL.search(ligne)
    }
    proches = {j for i in contact for j in range(i - VOISINAGE_LIEU, i + VOISINAGE_LIEU + 1)}
    gardees = []
    for i, ligne in enumerate(lignes):
        morceaux = SEPARATEUR.split(ligne)
        segments = [_nettoyer_segment(m, i in proches) for m in morceaux[::2]]
        separateurs = [m.strip() for m in morceaux[1::2]]
        sep = f" {separateurs[0]} " if separateurs else " "
        propre = sep.join(x for x in segments if x)
        if propre:
            gardees.append(propre)
    return "\n".join(gardees)


def paragraphes(texte: str) -> list[str]:
    """Lignes du PDF regroupees en paragraphes : un titre seul, une entree par tiret."""
    blocs: list[list[str]] = []
    for brute in texte.splitlines():
        ligne = brute.strip()
        if not ligne:
            continue
        if _est_espacee(ligne):
            blocs.append([_despacer(ligne)])
            blocs.append([])
            continue
        if ligne.startswith(("- ", "• ")) or not blocs:
            blocs.append([ligne.lstrip("-• ").strip()])
            continue
        blocs[-1].append(ligne)
    sortie = []
    for bloc in blocs:
        texte_bloc = ""
        for ligne in bloc:
            if texte_bloc.endswith("-") and ligne[:1].islower():
                texte_bloc += ligne  # mot coupe en fin de ligne : "cross-" + "encoder"
            else:
                texte_bloc = f"{texte_bloc} {ligne}" if texte_bloc else ligne
        if texte_bloc:
            sortie.append(texte_bloc)
    return sortie


def page_cv(contenu: bytes, chemin: str, langue: str) -> Page:
    """Un CV public devient une page du corpus, sans aucune coordonnee."""
    titre, brut = texte_pdf(contenu)
    brut = TIRETS.sub(" - ", brut)  # regle du depot : aucun tiret long dans le corpus
    titre = retirer_coordonnees(titre) or ("CV" if langue == "fr" else "Resume")
    texte = "\n\n".join(paragraphes(retirer_coordonnees(brut)))
    sections = (Section(titre, (), "", texte, "cv"),) if texte else ()
    return Page(chemin, langue, "cv", titre, sections)
