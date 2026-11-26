"""Branche A : agent Gemini a outils (appels de fonctions), boucle bornee par le code.

Memes garanties que la branche B (`pipeline.py`), appliquees par le code :
1. Aiguiller avant le modele : donnees personnelles -> refus ecrit ; contact -> carte.
2. Le modele choisit ses outils (`outils.py`) : recherche (meme recherche, meme
   seuil que B), lecture d'une page du corpus, liste des projets, carte de contact.
3. Bornes : `max_appels_outils` et `max_tours` (au-dela, le dernier tour se joue
   sans outils), `budget_jetons` (au-dela, le modele n'est plus appele), delai total.
4. Decider (code) : si aucun outil n'a rendu de passage, refus ecrit d'avance et le
   modele n'est pas rappele. Une reponse sans passage n'est jamais affichee.
5. Filtrer en flux contre les seules sorties d'outils (chiffres, liens, courriels,
   tirets), refus du modele ([REFUS]) remplace par un texte ecrit d'avance,
   sources citees. La carte de contact est rendue par le code, jamais par le modele.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from assistant import textes
from assistant.embeddings import Embedder
from assistant.filtres import (
    FiltreFlux,
    chiffres_autorises,
    chiffres_non_attribues,
    citations_valides,
)
from assistant.generation import MARQUEUR, MARQUEUR_REFUS, usage_vertex, utile
from assistant.modeles import Bilan, Evenement, Morceau, Passage
from assistant.outils import CHERCHER, Corpus, Outils
from assistant.pipeline import Assistant, Parametres, compter
from assistant.recherche import Recherche

CONSIGNE_AGENT = Path(__file__).resolve().parents[2] / "prompts" / "agent.md"


# Conversation neutre, traduite par chaque modele dans son propre format.


@dataclass
class AppelOutil:
    nom: str
    arguments: dict = field(default_factory=dict)
    id: str | None = None


@dataclass
class ResultatOutil:
    nom: str
    reponse: dict
    id: str | None = None


@dataclass
class Message:
    role: str  # "utilisateur", "modele" ou "outil"
    texte: str = ""
    appels: list[AppelOutil] = field(default_factory=list)
    resultats: list[ResultatOutil] = field(default_factory=list)
    brut: Any = None  # parties brutes du modele (signatures de pensee), rendues telles quelles


@dataclass
class FragmentAgent:
    texte: str = ""
    appels: list[AppelOutil] = field(default_factory=list)
    jetons_entree: int | None = None
    jetons_sortie: int | None = None
    jetons_cache: int | None = None
    jetons_reflexion: int | None = None
    brut: Any = None


class ModeleAgent(Protocol):
    nom: str

    def tour(
        self,
        consigne: str,
        historique: list[Message],
        outils: list[dict],
        outils_actifs: bool,
        max_jetons: int,
    ) -> AsyncIterator[FragmentAgent]: ...


@dataclass
class Bornes:
    max_appels_outils: int = 6
    max_tours: int = 4
    budget_jetons: int = 30000
    caracteres_page: int = 12000


# Modeles : faux scenarise (tests, poste) et Gemini sur Vertex AI.


class FauxModeleAgent:
    """Faux deterministe, hors reseau.

    Sans scenario : cherche la question, puis recopie la premiere phrase du
    premier passage rendu, citee (comme `FauxGenerateur`). Avec un scenario :
    joue les tours dans l'ordre, puis repete le dernier (boucle infinie).
    Chaque tour du scenario : {"texte": str, "appels": [(nom, arguments), ...]}.
    """

    nom = "faux-agent-v1"

    def __init__(self, scenario: list[dict] | None = None) -> None:
        self.scenario = scenario
        self.appels: list[dict] = []

    def _defaut(self, historique: list[Message], outils_actifs: bool) -> dict:
        if historique[-1].role == "utilisateur" and outils_actifs:
            question = historique[-1].texte.removeprefix("Question : ")
            return {"appels": [(CHERCHER, {"requete": question})]}
        for m in reversed(historique):
            for r in m.resultats:
                for p in r.reponse.get("passages", []):
                    corps = " ".join(p["texte"].split())
                    phrase = re.split(r"(?<=[.!?])\s+", corps)[0]
                    return {"texte": f"{phrase} [{p['n']}]"}
        return {"texte": MARQUEUR_REFUS}

    async def tour(
        self,
        consigne: str,
        historique: list[Message],
        outils: list[dict],
        outils_actifs: bool,
        max_jetons: int,
    ) -> AsyncIterator[FragmentAgent]:
        self.appels.append(
            {
                "outils_actifs": outils_actifs,
                "messages": len(historique),
                "historique": historique,  # la liste vivante : les tests relisent la fin
                "max_jetons": max_jetons,
                "outils": [o["name"] for o in outils],
            }
        )
        if self.scenario:
            spec = self.scenario[min(len(self.appels), len(self.scenario)) - 1]
        else:
            spec = self._defaut(historique, outils_actifs)
        mots = spec.get("texte", "").split(" ") if spec.get("texte") else []
        mots = mots[:max_jetons]
        for i, mot in enumerate(mots):
            yield FragmentAgent(texte=mot if i == 0 else " " + mot)
        appels = [AppelOutil(nom, dict(args)) for nom, args in spec.get("appels", [])]
        if appels:
            yield FragmentAgent(appels=appels)
        entree = consigne + json.dumps([m.__dict__ for m in historique], default=str)
        yield FragmentAgent(
            jetons_entree=len(entree.split()), jetons_sortie=len(mots) + len(appels)
        )


def _schema(types: Any, d: dict) -> Any:
    """Schema JSON (minuscules) -> types.Schema de google-genai (types en majuscules)."""
    return types.Schema(
        type=d["type"].upper(),
        description=d.get("description"),
        enum=d.get("enum") or None,
        properties={k: _schema(types, v) for k, v in d.get("properties", {}).items()} or None,
        required=d.get("required") or None,
    )


class VertexModeleAgent:
    """Gemini sur Vertex AI, appels de fonctions declares, en flux. Non execute par les tests.

    Les appels de fonctions marchent avec generate_content_stream ; seul le texte
    part en flux. Les parties brutes du modele sont rendues telles quelles au tour
    suivant : Gemini 3 exige ses signatures de pensee avec les appels de fonctions.
    """

    def __init__(
        self, projet: str, region: str, modele: str, temperature: float = 0.2, reflexion: str = ""
    ) -> None:
        from google import genai  # import paresseux : dependance optionnelle

        self._client = genai.Client(vertexai=True, project=projet, location=region)
        self.nom = modele
        self.temperature = temperature
        self.reflexion = reflexion

    @staticmethod
    def _contenus(types: Any, historique: list[Message]) -> list:
        contenus = []
        for m in historique:
            if m.role == "utilisateur":
                contenus.append(types.Content(role="user", parts=[types.Part(text=m.texte)]))
            elif m.role == "modele":
                parties = m.brut or (
                    ([types.Part(text=m.texte)] if m.texte else [])
                    + [
                        types.Part(
                            function_call=types.FunctionCall(id=a.id, name=a.nom, args=a.arguments)
                        )
                        for a in m.appels
                    ]
                )
                contenus.append(types.Content(role="model", parts=parties))
            else:
                contenus.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                function_response=types.FunctionResponse(
                                    id=r.id, name=r.nom, response=r.reponse
                                )
                            )
                            for r in m.resultats
                        ],
                    )
                )
        return contenus

    async def tour(
        self,
        consigne: str,
        historique: list[Message],
        outils: list[dict],
        outils_actifs: bool,
        max_jetons: int,
    ) -> AsyncIterator[FragmentAgent]:
        from google.genai import types

        declarations = [
            types.FunctionDeclaration(
                name=o["name"],
                description=o["description"],
                parameters=_schema(types, o["parameters"]) if o.get("parameters") else None,
            )
            for o in outils
        ]
        config = types.GenerateContentConfig(
            system_instruction=consigne,
            max_output_tokens=max_jetons,
            temperature=self.temperature,
            tools=[types.Tool(function_declarations=declarations)],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode="AUTO" if outils_actifs else "NONE"
                )
            ),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        if self.reflexion:
            config.thinking_config = types.ThinkingConfig(thinking_level=self.reflexion)
        flux = await self._client.aio.models.generate_content_stream(
            model=self.nom, contents=self._contenus(types, historique), config=config
        )
        parties: list = []
        usage = None
        async for morceau in flux:
            if morceau.usage_metadata:
                usage = morceau.usage_metadata
            candidats = morceau.candidates or []
            contenu = candidats[0].content if candidats else None
            for part in (contenu.parts if contenu and contenu.parts else []) or []:
                parties.append(part)
                if part.function_call:
                    fc = part.function_call
                    yield FragmentAgent(
                        appels=[AppelOutil(fc.name or "", dict(fc.args or {}), fc.id)]
                    )
                elif part.text and not part.thought:
                    yield FragmentAgent(texte=part.text)
        compte = usage_vertex(usage) if usage else None
        yield FragmentAgent(
            brut=parties,
            jetons_entree=compte.jetons_entree if compte else 0,
            jetons_sortie=compte.jetons_sortie if compte else 0,
            jetons_cache=compte.jetons_cache if compte else 0,
            jetons_reflexion=compte.jetons_reflexion if compte else 0,
        )


# La boucle.


class Matiere:
    """Passages rendus par les outils, numerotes une fois pour toutes dans la question."""

    def __init__(self) -> None:
        self.passages: list[Passage] = []
        self._n: dict[str, int] = {}

    def ajouter(self, p: Passage) -> tuple[int, bool]:
        if p.morceau.id in self._n:
            return self._n[p.morceau.id], False
        self.passages.append(p)
        self._n[p.morceau.id] = len(self.passages)
        return len(self.passages), True


class AssistantAgent(Assistant):
    branche = "agent"

    def __init__(
        self,
        embedder: Embedder,
        recherche: Recherche,
        modele: ModeleAgent,
        parametres: Parametres,
        coordonnees: dict,
        morceaux: list[Morceau],
        bornes: Bornes | None = None,
        consigne: str | None = None,
    ) -> None:
        super().__init__(
            embedder,
            recherche,
            modele,  # type: ignore[arg-type]  # `generateur.nom` sert aux journaux
            parametres,
            coordonnees,
            consigne or CONSIGNE_AGENT.read_text(encoding="utf-8"),
        )
        self.modele = modele
        self.bornes = bornes or Bornes()
        self.corpus = Corpus(morceaux)
        self.declarations = self.corpus.declarations()

    def _outils(self, langue: str) -> Outils:
        return Outils(
            self.corpus,
            self.embedder,
            self.recherche,
            langue=langue,
            passages=self.p.passages,
            seuil_distance=self.p.seuil_distance,
            appliquer_seuil=self.p.appliquer_seuil,
            site=self.p.site,
            caracteres_page=self.bornes.caracteres_page,
        )

    async def repondre(
        self, question: str, langue: str, diagnostic: bool = False
    ) -> AsyncIterator[Evenement]:
        t0 = time.perf_counter()
        bilan = Bilan()
        matiere = Matiere()
        brut = ""
        affiche = ""

        def fin(chemin: str) -> list[Evenement]:
            bilan.passages = len(matiere.passages)
            return self._fin(chemin, bilan, t0, diagnostic, matiere.passages, brut, affiche)

        def refus(motif: str, cle: str, chemin: str) -> list[Evenement]:
            return [
                Evenement("refus", {"motif": motif, "texte": textes.texte(langue, cle)}),
                *fin(chemin),
            ]

        ecrit = self._aiguillage(question, langue)
        if ecrit:
            evts, chemin = ecrit
            bilan.aiguillage = "code"
            for e in evts + fin(chemin):
                yield e
            return

        b = self.bornes
        outils = self._outils(langue)
        historique = [Message("utilisateur", texte=f"Question : {question}")]
        filtre = FiltreFlux(set(), self.p.domaines, langue)
        retenu = ""  # texte tenu tant qu'il peut encore devenir [REFUS]
        demarre = False
        refus_modele = False
        redaction_annoncee = False
        carte_affichee = False
        chemin_final = "borne"

        def texte_sortant(sortie: str) -> list[Evenement]:
            nonlocal affiche, redaction_annoncee
            evts = []
            if not redaction_annoncee:
                redaction_annoncee = True
                evts.append(Evenement("etape", {"texte": textes.texte(langue, "etape_redaction")}))
            if bilan.premier_mot_ms is None:
                bilan.premier_mot_ms = round((time.perf_counter() - t0) * 1000, 1)
            affiche += sortie
            evts.append(Evenement("texte", {"texte": sortie}))
            return evts

        try:
            async with asyncio.timeout(self.p.delai_s):
                for numero in range(1, b.max_tours + 1):
                    if bilan.jetons_entree + bilan.jetons_sortie >= b.budget_jetons:
                        break  # budget epuise : le modele n'est plus appele
                    force = numero == b.max_tours or bilan.appels_outils >= b.max_appels_outils
                    bilan.appels_modele += 1
                    texte_tour = ""
                    appels: list[AppelOutil] = []
                    brut_tour = None
                    async for frag in self.modele.tour(
                        self.consigne,
                        historique,
                        self.declarations,
                        not force,
                        self.p.jetons_reponse,
                    ):
                        compter(bilan, frag)
                        if frag.brut is not None:
                            brut_tour = frag.brut
                        appels.extend(frag.appels)
                        if not frag.texte:
                            continue
                        texte_tour += frag.texte
                        brut += frag.texte
                        # Sans passage, rien ne s'affiche ; apres [REFUS], plus rien.
                        if refus_modele or not matiere.passages:
                            continue
                        if not demarre:
                            retenu += frag.texte
                            tete = retenu.lstrip()
                            if not utile(tete):
                                continue  # peut encore n'etre qu'un [REFUS]
                            if MARQUEUR.match(tete):
                                refus_modele = True
                                continue
                            demarre, morceau, retenu = True, tete, ""
                        else:
                            morceau = frag.texte
                        sortie = filtre.pousser(morceau)
                        if sortie:
                            for e in texte_sortant(sortie):
                                yield e
                    historique.append(
                        Message("modele", texte=texte_tour, appels=appels, brut=brut_tour)
                    )
                    if not matiere.passages and MARQUEUR.match(texte_tour):
                        refus_modele = True
                    if refus_modele:
                        for e in refus("modele", "refus_modele", "refus_modele"):
                            yield e
                        return

                    if appels and not force:
                        resultats, carte, nouveaux = [], False, []
                        for appel in appels:
                            if bilan.appels_outils >= b.max_appels_outils:
                                reponse = {"erreur": "nombre maximal d'appels atteint"}
                                resultats.append(ResultatOutil(appel.nom, reponse, appel.id))
                                continue
                            bilan.appels_outils += 1
                            ex = await outils.executer(appel.nom, appel.arguments)
                            if ex.meilleure_distance is not None:
                                d = round(ex.meilleure_distance, 4)
                                if bilan.meilleure_distance is None or d < bilan.meilleure_distance:
                                    bilan.meilleure_distance = d
                            if ex.etape:
                                cle, valeurs = ex.etape
                                texte_etape = textes.texte(langue, cle).format(**valeurs)
                                yield Evenement("etape", {"texte": texte_etape})
                            carte = carte or ex.carte
                            rendus = []
                            for p in ex.passages:
                                n, neuf = matiere.ajouter(p)
                                if neuf:
                                    nouveaux.append(p)
                                m = p.morceau
                                rendus.append(
                                    {
                                        "n": n,
                                        "titre": m.titre,
                                        "section": m.contexte,
                                        "url": m.lien,
                                        "texte": m.texte,
                                    }
                                )
                            reponse = {"passages": rendus, **ex.extra} if rendus else dict(ex.extra)
                            resultats.append(ResultatOutil(appel.nom, reponse, appel.id))
                        historique.append(Message("outil", resultats=resultats))
                        filtre.autorises |= chiffres_autorises(
                            p.morceau.texte_indexe for p in nouveaux
                        )
                        if carte and not demarre:
                            # La carte est rendue par le code et clot la question,
                            # comme l'aiguillage de contact de la branche B.
                            for e in self._evenements_carte(langue) + fin("carte"):
                                yield e
                            return
                        if carte and not carte_affichee:
                            carte_affichee = True  # au milieu d'une reponse deja commencee
                            yield Evenement("carte", self._carte(langue))
                        if not matiere.passages:
                            # Rien n'a passe le seuil : refus ecrit d'avance, modele non rappele.
                            for e in refus("hors_corpus", "hors_corpus", "refus_seuil"):
                                yield e
                            return
                        continue

                    if not matiere.passages:
                        # Reponse sans aucun passage consulte : jamais affichee.
                        for e in refus("hors_corpus", "hors_corpus", "refus_sans_source"):
                            yield e
                        return
                    if appels and not demarre and not retenu.strip():
                        break  # tour force, encore des appels et aucun texte : borne
                    chemin_final = "reponse"
                    break
        except TimeoutError:
            for e in [
                Evenement("erreur", {"texte": textes.texte(langue, "erreur")}),
                *fin("delai"),
            ]:
                yield e
            return

        if chemin_final != "reponse" and not demarre:
            for e in [
                Evenement("erreur", {"texte": textes.texte(langue, "erreur")}),
                *fin("borne"),
            ]:
                yield e
            return
        if not demarre and not utile(retenu):
            for e in refus("modele", "refus_modele", "refus_modele"):
                yield e
            return

        reste = (filtre.pousser(retenu.lstrip()) if not demarre else "") + filtre.terminer()
        if reste:
            for e in texte_sortant(reste):
                yield e
        cites = citations_valides(affiche, len(matiere.passages))
        bilan.sources_citees = len(cites)
        bilan.chiffres_remplaces = filtre.compteurs.chiffres_remplaces
        bilan.liens_retires = filtre.compteurs.liens_retires
        bilan.chiffres_non_attribues = chiffres_non_attribues(
            affiche, (matiere.passages[n - 1].morceau.texte_indexe for n in cites)
        )
        yield Evenement(
            "sources", {"citees": bool(cites), "sources": self._sources(matiere.passages, cites)}
        )
        for e in fin("reponse"):
            yield e
