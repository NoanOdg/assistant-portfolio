from __future__ import annotations

from assistant.filtres import FiltreFlux, chiffres_autorises, citations_valides, formes

PASSAGES = [
    "Le harnais est passé de 27 % à 86 %.",
    "28 398 944 lignes, soit 2,12 Go.",
    "Taux : 0.9647 (en). TOEIC 950.",
]


def filtrer(texte: str, morceaux: int = 3, langue: str = "fr") -> tuple[str, FiltreFlux]:
    f = FiltreFlux(chiffres_autorises(PASSAGES), ["noan-ouedraogo.dev", "linkedin.com"], langue)
    taille = max(1, len(texte) // morceaux)
    sortie = "".join(f.pousser(texte[i : i + taille]) for i in range(0, len(texte), taille))
    return sortie + f.terminer(), f


def test_formes_des_nombres():
    assert "28398944" in formes("28 398 944")
    assert "28398944" in formes("28,398,944")
    assert "2.12" in formes("2,12")
    assert "0.9647" in formes("0,9647")


def test_les_chiffres_des_passages_passent():
    sortie, f = filtrer("Il est passé de 27 % à 86 % [1], sur 28 398 944 lignes [2].")
    assert sortie == "Il est passé de 27 % à 86 % [1], sur 28 398 944 lignes [2]."
    assert f.compteurs.chiffres_remplaces == 0


def test_un_chiffre_invente_est_remplace_meme_coupe_en_flux():
    for morceaux in (1, 3, 7, 40):
        sortie, f = filtrer("Il atteint 93,5 % de réussite [1].", morceaux)
        assert sortie == "Il atteint (voir la source) % de réussite [1]."
        assert f.compteurs.chiffres_remplaces == 1


def test_les_renvois_et_les_noms_ne_sont_pas_des_chiffres():
    sortie, f = filtrer("La V2 tourne sur une H100 [3].")
    assert sortie == "La V2 tourne sur une H100 [3]."
    assert f.compteurs.chiffres_remplaces == 0


def test_liens_courriels_images_html_et_tirets():
    texte = (
        "Voir [la page](https://noan-ouedraogo.dev/projets/insei) et "
        "[ceci](https://exemple.com/x) ou https://autre.org/y. "
        "Écrire à quelqu'un@exemple.com. ![img](https://x/y.png) <b>gras</b> "
        "Un point \N{EM DASH} puis un autre \N{EN DASH} fin."
    )
    sortie, f = filtrer(texte, 5)
    assert "exemple.com" not in sortie and "autre.org" not in sortie
    assert "la page" in sortie and "ceci" in sortie
    assert "@" not in sortie and "(voir la carte de contact)" in sortie
    assert "img" not in sortie and "<b>" not in sortie and "gras" in sortie
    assert "\N{EM DASH}" not in sortie and "\N{EN DASH}" not in sortie
    assert "Un point, puis un autre, fin." in sortie
    assert f.compteurs.liens_retires == 2


def test_message_en_anglais():
    sortie, _ = filtrer("It reaches 99 % [1].", langue="en")
    assert sortie == "It reaches (see the source) % [1]."


def test_citations_valides():
    assert citations_valides("A [1] B [3] C [1] D [9]", 6) == [1, 3]
    assert citations_valides("rien", 6) == []
