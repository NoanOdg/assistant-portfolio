"""Reecriture de la question, a partir du deuxieme tour d'une conversation.

Un appel court, sortie JSON sous schema : le type de la question (suite,
nouveau_sujet, transformation, comparaison), la question autonome et, pour une
comparaison, deux sous-questions au plus. Le code decide ensuite seul :
- garde-fou deterministe : un nom (ou un chiffre) present dans la reecriture
  et absent de la question, des echanges et du sujet courant fait retomber sur
  la question d'origine ; pour une transformation, la question d'origine reste
  une transformation (elle n'ajoute rien, la reponse precedente porte le sujet) ;
- toute erreur, sortie illisible ou depassement de delai : question d'origine,
  sans rien montrer au visiteur ;
- filet deterministe : une demande courte de traduire, resumer ou reformuler
  la reponse precedente est une transformation, quoi qu'ait dit le modele.
La raison d'un repli (Plan.raison) ne sort que dans le diagnostic d'evaluation.
Le modele de redaction ne voit jamais l'historique (pipeline.py).
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from assistant.memoire import Echange, Sujet
from assistant.modeles import Morceau

CONSIGNE = Path(__file__).resolve().parents[2] / "prompts" / "reecriture.md"
COMMENTAIRE = re.compile(r"<!--.*?-->\s*", re.S)
TYPES = ("suite", "nouveau_sujet", "transformation", "comparaison")
CARACTERES_MAX = 500  # comme une question du visiteur
SOUS_QUESTIONS_MAX = 2

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "type": {"type": "STRING", "enum": list(TYPES)},
        "question_autonome": {"type": "STRING"},
        "sous_questions": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["type", "question_autonome", "sous_questions"],
    "propertyOrdering": ["type", "question_autonome", "sous_questions"],
}

# Mots a majuscule qui ne designent ni un projet ni un outil : jamais un ajout.
LIBRES = {
    "noan",
    "ouedraogo",
    "i",
    "francais",
    "anglais",
    "espagnol",
    "french",
    "english",
    "spanish",
}
# Un titre de section (contexte "Page > Section", rubrique du CV "A | B")
# commence comme une phrase.
FINS = ".!?;:>|\n"
_MOT = re.compile(r"[^\W_](?:[\w+#]|[.\-](?=[^\W_]))*|[.!?;:>|\n]")
# Au-dela, un mot tout en capitales est un titre du CV (« FORMATION »), pas un sigle.
SIGLE_MAX = 5


def charger_consigne() -> str:
    return COMMENTAIRE.sub("", CONSIGNE.read_text(encoding="utf-8"))


def normaliser(mot: str) -> str:
    sans = unicodedata.normalize("NFKD", mot)
    return "".join(c for c in sans if not unicodedata.combining(c)).lower()


def mots(texte: str) -> set[str]:
    return {normaliser(m) for m in _MOT.findall(texte) if m not in FINS}


def noms(texte: str, connus: set[str] = frozenset()) -> set[str]:
    """Ce qui designe un nom : mot a majuscule hors debut de phrase, mot qui
    contient un chiffre, ou nom connu du corpus a n'importe quelle place."""
    res, debut = set(), True
    for m in _MOT.findall(texte):
        if m in FINS:
            debut = True
            continue
        n = normaliser(m)
        if (
            (not debut and any(c.isupper() for c in m))
            or any(c.isdigit() for c in m)
            or n in connus
        ):
            res.add(n)
        debut = False
    return res - LIBRES


def noms_du_corpus(morceaux: Iterable[Morceau]) -> set[str]:
    """Projets et noms propres du site : un nom en tete de phrase reste un nom.

    Un mot que le site ecrit aussi en minuscules n'est pas un nom propre, meme
    s'il prend une majuscule dans un titre (« Pile technique », « En bref »,
    « Head of ») : sinon « en », « of », « projet » ou « langues » passaient
    pour des noms, et toute reecriture qui les employait sans qu'ils figurent
    dans les echanges retombait sur la question d'origine (mesure du 26/11).
    Un mot tout en capitales de plus de SIGLE_MAX lettres (titre du CV) non plus."""
    connus: set[str] = set()
    minuscules: set[str] = set()
    capitales: set[str] = set()
    ecrits: set[str] = set()  # avec une majuscule, hors capitales
    for m in morceaux:
        texte = f"{m.contexte}\n{m.titre}\n{m.texte}"
        jetons = _MOT.findall(texte)
        brut = [x for x in jetons if x not in FINS]
        connus.add(normaliser(m.projet))
        connus |= {n for n in noms(texte) if not any(c.isdigit() for c in n)}
        minuscules |= {normaliser(x) for x in brut if x == x.lower()}
        capitales |= {normaliser(x) for x in brut if x.isupper() and len(x) > SIGLE_MAX}
        ecrits |= {
            normaliser(x)
            for avant, x in zip(["\n", *jetons], jetons, strict=False)
            if avant not in FINS and x not in FINS and x != x.lower() and not x.isupper()
        }
    # « FORMATION » sort ; « LEYENDA » reste, le site l'ecrit aussi « Leyenda ».
    return connus - minuscules - (capitales - ecrits) - LIBRES


def noms_ajoutes(reecrite: str, sources: str, connus: set[str] = frozenset()) -> set[str]:
    """Ce que la reecriture nomme et que les sources ne nomment pas."""
    return noms(reecrite, connus) - mots(sources)


def ajoute_un_nom(reecrite: str, sources: str, connus: set[str] = frozenset()) -> bool:
    """Vrai si la reecriture nomme quelque chose que les sources ne nomment pas."""
    return bool(noms_ajoutes(reecrite, sources, connus))


# --- Filet deterministe des transformations --------------------------------------

# Demander de traduire, resumer, raccourcir ou reformuler (texte en minuscules,
# accents gardes : « resume » sans accent est le CV en anglais).
_TRANSFORMER = re.compile(
    r"""\b(?:
        tradui\w* | tradu(?:ction|ire) | translat\w*
      | (?:en|in|into|to)\s+(?:anglais|français|francais|espagnol|english|french|spanish)
      | résum\w* | resumer | resumez | summari[sz]\w* | sum\s+(?:it|that|this)\s+up | tl;?dr
      | plus\s+(?:court|courte|bref|brève|simple|simplement|clair|clairement|concis)
      | shorter | simpler | more\s+simply | in\s+plain\s+words | briefly | in\s+short
      | reformul\w* | rephras\w* | reword\w* | paraphras\w* | simplifi\w*
      | autrement | en\s+d'autres\s+(?:termes|mots) | in\s+other\s+words
      | en\s+une\s+phrase | in\s+one\s+sentence | sans\s+jargon | without\s+jargon
    )\b""",
    re.X,
)
# Une question qui commence par demander un fait n'est pas une transformation
# (« Quelles langues parle-t-il, en anglais ? »). En tete seulement : « pour
# quelqu'un qui n'est pas technique » reste une reformulation.
_INTERROGATIFS = re.compile(
    r"^\W*(?:(?:et|and|so|alors)\W+)?(?:quel(?:le)?s?|combien|pourquoi|où|qui|quand"
    r"|which|what(?!\s+about)|why|where|who|when|how\s+(?:many|much|long))\b"
)
MOTS_TRANSFORMATION_MAX = 12

_LANGUES = (
    ("en", re.compile(r"\b(?:anglais|english)\b")),
    ("fr", re.compile(r"\b(?:français|francais|french)\b")),
    ("es", re.compile(r"\b(?:espagnol|spanish)\b")),
)
NOMS_LANGUES = {
    "fr": {"en": "anglais", "fr": "français", "es": "espagnol"},
    "en": {"en": "English", "fr": "French", "es": "Spanish"},
}


def langue_demandee(question: str) -> str:
    """La langue que la question demande explicitement ("en", "fr", "es"), ou ""."""
    q = question.lower()
    trouvees = [code for code, motif in _LANGUES if motif.search(q)]
    return trouvees[0] if len(trouvees) == 1 else ""


def demande_de_transformation(question: str, connus: set[str] = frozenset()) -> bool:
    """Vrai si la question demande seulement de transformer la reponse precedente :
    un marqueur (traduis, translate, en anglais, resume, plus court, shorter,
    reformule, rephrase, autrement...), une question courte, aucun nom (elle
    n'apporte pas de nouveau sujet), pas de mot interrogatif en tete. Les noms
    de langues ne comptent jamais comme des noms (LIBRES)."""
    q = question.lower()
    return (
        bool(_TRANSFORMER.search(q))
        and len(question.split()) <= MOTS_TRANSFORMATION_MAX
        and not _INTERROGATIFS.search(q)
        and not noms(question, connus)
    )


def _donnees(texte: str) -> str:
    """Une balise fermante dans une donnee ne doit pas fermer le bloc qui la contient."""
    return texte.replace("</", "< /")


def contenu_reecriture(
    sujet: Sujet | None,
    projets: list[str],
    echanges: list[Echange],
    question: str,
    projets_echanges: list[list[str]] | None = None,
) -> str:
    """Le contenu envoye au modele de reecriture : des blocs de donnees balises.
    `projets_echanges` : les titres des projets cites par chaque echange, pour
    resoudre « ces deux projets » ou « l'autre » quand la reponse tronquee ne
    les nomme pas."""
    blocs = ["<sujet_courant>"]
    if sujet is not None:
        blocs.append(f"<question>{_donnees(sujet.question)}</question>")
        if projets:
            blocs.append(f"<projets>{_donnees(' ; '.join(projets))}</projets>")
    blocs += ["</sujet_courant>", "", "<echanges>"]
    for i, e in enumerate(echanges):
        blocs += ["<echange>", f"<question>{_donnees(e.question)}</question>"]
        titres = projets_echanges[i] if projets_echanges and i < len(projets_echanges) else []
        if titres:
            blocs.append(f"<projets>{_donnees(' ; '.join(titres))}</projets>")
        blocs += [f"<reponse>{_donnees(e.reponse)}</reponse>", "</echange>"]
    blocs += ["</echanges>", "", "<question_actuelle>", _donnees(question), "</question_actuelle>"]
    return "\n".join(blocs)


@dataclass
class Plan:
    """Ce que le code fait de la question. type : "" (premier tour, ou memoire
    coupee), suite, nouveau_sujet, transformation, comparaison, repli.
    raison : pourquoi le repli (ou le filet), pour le diagnostic d'evaluation
    seulement, jamais pour les journaux : delai, json, erreur:<classe>, forme,
    type, vide, longueur, garde_nom:<noms>, transformation_sans_precedente..."""

    type: str
    question: str
    sous_questions: tuple[str, ...] = ()
    precedente: Echange | None = None
    raison: str = ""


def plan_depuis(objet: object, question: str, sources: str, connus: set[str]) -> Plan:
    """Plan tire de la sortie du modele ; type "repli" (question d'origine) et
    sa raison si la sortie n'est pas valide."""

    def repli(raison: str) -> Plan:
        return Plan("repli", question, raison=raison)

    if not isinstance(objet, dict):
        return repli("forme")
    type_ = objet.get("type")
    if type_ not in TYPES:
        return repli("type")
    if type_ == "nouveau_sujet":
        return Plan("nouveau_sujet", question)
    autonome = str(objet.get("question_autonome") or "").strip()
    if not autonome:
        return repli("vide")
    if len(autonome) > CARACTERES_MAX:
        return repli("longueur")
    brutes = objet.get("sous_questions") or []
    sous = (
        tuple(str(s).strip() for s in brutes if str(s).strip())[:SOUS_QUESTIONS_MAX]
        if isinstance(brutes, list)
        else ()
    )
    if any(len(s) > CARACTERES_MAX for s in sous):
        return repli("longueur")
    if type_ == "comparaison" and len(sous) < SOUS_QUESTIONS_MAX:
        type_, sous = "suite", ()
    if type_ != "comparaison":
        sous = ()
    ajoutes = set().union(*(noms_ajoutes(t, sources, connus) for t in (autonome, *sous)))
    if ajoutes:
        raison = "garde_nom:" + ",".join(sorted(ajoutes))
        if type_ == "transformation":
            # La demande du visiteur n'ajoute rien : elle reste une transformation.
            return Plan("transformation", question, raison=raison)
        return repli(raison)
    return Plan(type_, autonome, sous)


@dataclass
class ResultatReecriture:
    objet: object
    jetons_entree: int = 0
    jetons_sortie: int = 0  # reflexion comprise
    jetons_cache: int = 0


class Reecrivain(Protocol):
    nom: str
    tarif: dict

    async def reecrire(
        self, consigne: str, contenu: str, max_jetons: int
    ) -> ResultatReecriture: ...


_ACTUELLE = re.compile(r"<question_actuelle>\n(.*?)\n</question_actuelle>", re.S)
_SUJET = re.compile(r"<sujet_courant>\n<question>(.*?)</question>", re.S)


class FauxReecrivain:
    """Hors reseau. Rejoue des sorties ecrites d'avance (liste ou fonction du
    contenu), sinon une regle simple : une question de six mots au plus, avec
    un sujet courant, est une suite (le sujet recopie apres la question)."""

    nom = "faux-reecriture-v1"

    def __init__(
        self,
        reponses: list[object] | Callable[[str], object] | None = None,
        erreur: Exception | None = None,
        attente_s: float = 0.0,
    ) -> None:
        self.reponses = reponses
        self.erreur = erreur
        self.attente_s = attente_s
        self.appels: list[dict] = []
        self.tarif = {"entree": 0.25, "sortie": 1.50, "cache": 0.025}

    @staticmethod
    def regle(contenu: str) -> dict:
        q = _ACTUELLE.search(contenu)
        s = _SUJET.search(contenu)
        question = q.group(1).strip() if q else ""
        if s and len(question.split()) <= 6:
            return {
                "type": "suite",
                "question_autonome": f"{question} ({s.group(1).strip()})",
                "sous_questions": [],
            }
        return {"type": "nouveau_sujet", "question_autonome": question, "sous_questions": []}

    async def reecrire(self, consigne: str, contenu: str, max_jetons: int) -> ResultatReecriture:
        import asyncio

        self.appels.append({"consigne": consigne, "contenu": contenu, "max_jetons": max_jetons})
        if self.attente_s:
            await asyncio.sleep(self.attente_s)
        if self.erreur is not None:
            raise self.erreur
        if callable(self.reponses):
            objet = self.reponses(contenu)
        elif self.reponses:
            objet = self.reponses.pop(0)
        else:
            objet = self.regle(contenu)
        return ResultatReecriture(objet, len((consigne + contenu).split()), 20)


class VertexReecrivain:
    """Gemini sur Vertex AI, sans flux, JSON sous schema. Non execute par les tests."""

    def __init__(
        self,
        projet: str,
        region: str,
        modele: str,
        reflexion: str = "",
        tarif: dict | None = None,
    ) -> None:
        from google import genai  # import paresseux : dependance optionnelle

        self._client = genai.Client(vertexai=True, project=projet, location=region)
        self.nom = modele
        self.reflexion = reflexion
        self.tarif = tarif or {}

    async def reecrire(self, consigne: str, contenu: str, max_jetons: int) -> ResultatReecriture:
        from google.genai import types

        from assistant.generation import usage_vertex

        config = types.GenerateContentConfig(
            system_instruction=consigne,
            max_output_tokens=max_jetons,
            temperature=0,
            response_mime_type="application/json",
            response_schema=SCHEMA,
        )
        if self.reflexion:
            config.thinking_config = types.ThinkingConfig(thinking_level=self.reflexion)
        r = await self._client.aio.models.generate_content(
            model=self.nom, contents=contenu, config=config
        )
        res = ResultatReecriture(json.loads(r.text or ""))
        if r.usage_metadata:
            u = usage_vertex(r.usage_metadata)
            res.jetons_entree = u.jetons_entree or 0
            res.jetons_sortie = (u.jetons_sortie or 0) + (u.jetons_reflexion or 0)
            res.jetons_cache = u.jetons_cache or 0
        return res


@dataclass
class ReglagesMemoire:
    tours: int = 3
    caracteres_reponse: int = 300
    delai_reecriture_s: float = 4.0
    jetons_reecriture: int = 256
    passages_comparaison: int = 8
    connus: set[str] = field(default_factory=set)
