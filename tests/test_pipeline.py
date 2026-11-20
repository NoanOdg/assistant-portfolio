from __future__ import annotations

import asyncio

from assistant.generation import FauxGenerateur, Fragment

from .conftest import evenements


def types(evts) -> list[str]:
    return [e.type for e in evts]


def fin(evts) -> dict:
    return next(e for e in evts if e.type == "fin").donnees


async def test_reponse_sourcee(fabriquer_assistant):
    a = await fabriquer_assistant()
    evts = await evenements(a, "Quel taux de détection des pannes le moniteur atteint-il ?")
    assert types(evts)[:2] == ["etape", "etape"]
    texte = "".join(e.donnees["texte"] for e in evts if e.type == "texte")
    assert "[1]" in texte
    sources = next(e for e in evts if e.type == "sources").donnees
    assert sources["citees"] is True
    assert sources["sources"][0]["url"].startswith("https://noan-ouedraogo.dev/projets/exemple")
    assert fin(evts)["chemin"] == "reponse"


async def test_refus_sous_le_seuil_sans_appeler_le_modele(fabriquer_assistant):
    gen = FauxGenerateur()
    a = await fabriquer_assistant(generateur=gen, seuil=0.05)
    evts = await evenements(a, "Donne-moi une recette de crêpes.")
    assert "refus" in types(evts) and "texte" not in types(evts)
    assert fin(evts)["chemin"] == "refus_seuil"
    assert gen.appels == []


async def test_donnees_personnelles_refusees_sans_recherche(fabriquer_assistant):
    gen = FauxGenerateur()
    a = await fabriquer_assistant(generateur=gen)
    avant = a.embedder.appels  # l'index a deja vectorise le corpus
    evts = await evenements(a, "Quel salaire demande-t-il ?")
    assert types(evts) == ["refus", "fin"]
    assert a.embedder.appels == avant and gen.appels == []


async def test_carte_de_contact_par_le_code(fabriquer_assistant):
    a = await fabriquer_assistant()
    evts = await evenements(a, "Comment le contacter ?", "en")
    carte = next(e for e in evts if e.type == "carte").donnees
    assert carte["courriel"] == "contact@exemple.test"
    assert carte["cv"].endswith("/Resume_Exemple.pdf")
    assert fin(evts)["chemin"] == "carte"


async def test_le_modele_peut_refuser(fabriquer_assistant):
    a = await fabriquer_assistant(generateur=FauxGenerateur("[REFUS]"))
    evts = await evenements(a, "Le moniteur détecte-t-il les pannes ?")
    assert fin(evts)["chemin"] == "refus_modele"
    assert "texte" not in types(evts)


async def test_un_chiffre_invente_n_atteint_jamais_le_visiteur(fabriquer_assistant):
    gen = FauxGenerateur("Le moniteur détecte 99,9 % des pannes [1].")
    a = await fabriquer_assistant(generateur=gen)
    evts = await evenements(a, "Le moniteur détecte-t-il les pannes ?", diagnostic=True)
    texte = "".join(e.donnees["texte"] for e in evts if e.type == "texte")
    assert "99,9" not in texte and "(voir la source)" in texte
    assert fin(evts)["bilan"]["chiffres_remplaces"] == 1
    diag = next(e for e in evts if e.type == "diagnostic").donnees
    assert "99,9" in diag["brut"]


async def test_sans_renvoi_les_pages_consultees_sont_montrees(fabriquer_assistant):
    a = await fabriquer_assistant(generateur=FauxGenerateur("Le moniteur détecte les pannes."))
    evts = await evenements(a, "Le moniteur détecte-t-il les pannes ?")
    sources = next(e for e in evts if e.type == "sources").donnees
    assert sources["citees"] is False and sources["sources"]
    assert fin(evts)["bilan"]["sources_citees"] == 0


async def test_plafond_de_jetons_transmis(fabriquer_assistant):
    gen = FauxGenerateur()
    a = await fabriquer_assistant(generateur=gen, jetons_reponse=7)
    await evenements(a, "Quel taux de détection des pannes ?")
    assert gen.appels[0]["max_jetons"] == 7


class GenerateurLent:
    nom = "lent"

    async def rediger(self, consigne, contenu, max_jetons):
        yield Fragment(texte="Début")
        await asyncio.sleep(5)
        yield Fragment(texte=" fin")


async def test_delai_depasse(fabriquer_assistant):
    a = await fabriquer_assistant(generateur=GenerateurLent(), delai_s=0.05)
    evts = await evenements(a, "Le moniteur détecte-t-il les pannes ?")
    assert "erreur" in types(evts)
    assert fin(evts)["chemin"] == "delai"


async def test_la_consigne_est_versionnee(fabriquer_assistant):
    a = await fabriquer_assistant()
    assert "[REFUS]" in a.consigne
    assert len(a.version_consigne) == 12
