"""CV publics : extraction du texte PDF et retrait des coordonnees, sans reseau."""

from __future__ import annotations

import re

from assistant.corpus.decoupage import decouper
from assistant.corpus.pdf import page_cv, paragraphes, retirer_coordonnees, texte_pdf
from assistant.corpus.site import courriel_protege
from assistant.evaluation.executer import projet_de_url

from .pdf_factice import pdf_factice

# Coordonnees factices : aucune n'existe.
LIGNES_CV = [
    "I N G É N I E U R  E N  I N F O R M A T I Q U E",
    "P R É N O M  N O M",
    "C O M P É T E N C E S",
    "Python (IA/ML/Data), SQL",
    "- Employeur fictif, Villeneuve",
    "Avril 2023 - Octobre 2026",
    "Évaluation : 88 questions de référence ; routage mesuré à 96,5 %",
    "12,15 M de décisions tracées, 1 004 tests, PostgreSQL 16, TOEIC 950",
    "Anglais : C1 (TOEIC 950)",
    "+33 6 12 34 56 78",
    "prenom.nom@exemple.test",
    "Villefictive (Île-de-France)",
    "exemple.test",
    "- Projets scolaires | École, Ville",
    "Recherche : cross-",
    "encoder et heuristiques",
]


def test_extraction_du_texte_pdf():
    titre, texte = texte_pdf(pdf_factice(LIGNES_CV, titre="CV Prénom Nom Ingénieur IA"))
    assert titre == "CV Prénom Nom Ingénieur IA"
    assert "Python (IA/ML/Data), SQL" in texte
    assert "Évaluation : 88 questions" in texte


def test_coordonnees_retirees_du_cv():
    page = page_cv(pdf_factice(LIGNES_CV, titre="CV Prénom Nom"), "/CV_Exemple.pdf", "fr")
    assert (page.url, page.langue, page.projet, page.titre) == (
        "/CV_Exemple.pdf",
        "fr",
        "cv",
        "CV Prénom Nom",
    )
    texte = "\n".join(s.texte for s in page.sections)
    assert "12 34 56 78" not in texte and "+33" not in texte
    assert "@" not in texte
    assert "Villefictive" not in texte
    # Le reste du CV est intact, chiffres compris.
    for garde in ("96,5 %", "12,15 M", "1 004 tests", "PostgreSQL 16", "TOEIC 950", "2023 - Oct"):
        assert garde in texte, garde
    assert "exemple.test" in texte
    assert "COMPÉTENCES" in texte and "INGÉNIEUR EN INFORMATIQUE" in texte
    assert "cross-encoder" in texte


def test_formes_de_telephone_courriel_et_adresse():
    brut = "\n".join(
        [
            "+33 6 12 34 56 78",
            "Tél. 06.12.34.56.78",
            "0033 6 12 34 56 78",
            "+33 (0)6 12 34 56 78",
            "06 12 34 56 78",
            "Écrire à prenom.nom+cv@exemple.test",
            "12 rue des Lilas",
            "3 bis, avenue de la Gare",
            "75011 Villefictive",
            "Villefictive (Île-de-France) · +33 6 12 34 56 78 · a.b@exemple.test · exemple.test",
        ]
    )
    propre = retirer_coordonnees(brut)
    assert not re.search(r"\d{2}[ .]?\d{2}[ .]?\d{2}[ .]?\d{2}", propre)
    assert "@" not in propre
    assert "Lilas" not in propre and "Gare" not in propre and "75011" not in propre
    assert "Villefictive" not in propre
    assert propre.splitlines()[-1] == "exemple.test"


def test_les_chiffres_du_cv_ne_sont_pas_des_telephones():
    brut = (
        "2021 - 2023 puis 2023 - 2026\n"
        "AUC 0,9996 puis 0,7332, F1 0,489\n"
        "28 398 944 lignes, 1 682 tests, 77,9 millions de paris\n"
        "Python (IA/ML/Data), SQL\n"
        "Nanterre (Hauts-de-Seine)"  # forme de lieu, mais loin de tout telephone : gardee
    )
    assert retirer_coordonnees(brut) == brut


def test_paragraphes_par_titre_et_par_entree():
    p = paragraphes(
        "C O M P É T E N C E S\nPython, SQL\nDocker\n- INSEI, Ville\nIngénieur\n- Stage\nC#"
    )
    assert p == ["COMPÉTENCES", "Python, SQL Docker", "INSEI, Ville Ingénieur", "Stage C#"]


def test_cv_decoupe_en_morceaux_cites():
    page = page_cv(pdf_factice(LIGNES_CV, titre="Resume"), "/Resume_Exemple.pdf", "en")
    morceaux = decouper(page)
    assert morceaux and all(m.lien == "/Resume_Exemple.pdf" for m in morceaux)
    assert all(m.projet == "cv" and m.type == "cv" for m in morceaux)
    assert projet_de_url("https://noan-ouedraogo.dev/Resume_Exemple.pdf") == "cv"


def test_pdf_sans_texte():
    page = page_cv(pdf_factice([]), "/CV_Vide.pdf", "fr")
    assert page.sections == ()
    assert page.titre == "CV"


def test_courriel_masque_par_cloudflare():
    adresse = "prenom.nom@exemple.test"
    cle = 0x5A
    code = f"{cle:02x}" + "".join(f"{ord(c) ^ cle:02x}" for c in adresse)
    assert courriel_protege(code) == adresse
    assert courriel_protege("zz") == ""


def test_lieu_en_tete_de_la_ligne_de_coordonnees():
    # CV du 26/11/2026 : lieu, telephone et courriel sur une seule ligne, sans separateur.
    propre = retirer_coordonnees(
        "Villefictive (78) +33 7 00 00 00 00 contact@exemple.test exemple.test"
    )
    assert propre == "exemple.test"


def test_un_titre_en_capitales_ouvre_un_paragraphe():
    p = paragraphes(
        "EXPÉRIENCES PROFESSIONNELLES\nINGÉNIEUR IA | CDD | INSEI | 2026\nUn assistant\n"
        "CDD : la suite de la ligne\nINGÉNIEUR DATA | ALTERNANCE\nUn premier assistant"
    )
    assert p == [
        "EXPÉRIENCES PROFESSIONNELLES",
        "INGÉNIEUR IA | CDD | INSEI | 2026 Un assistant CDD : la suite de la ligne",
        "INGÉNIEUR DATA | ALTERNANCE Un premier assistant",
    ]
