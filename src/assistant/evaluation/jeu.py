"""Jeu d'evaluation : chargement, validation, sceau de la reserve et du juge."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from assistant.config import RACINE

DOSSIER = RACINE / "evaluation"
JEU_TRAVAIL = DOSSIER / "jeu" / "questions.yaml"
# Jeu multi-tours : chaque question est jouee apres ses `tours`, dans la meme conversation.
JEU_CONVERSATIONS = DOSSIER / "jeu" / "conversations.yaml"
RESERVE = DOSSIER / "jeu" / "reserve-scellee.yaml"
# Brouillons du jeu v2 : ni scelles ni joues par la CI tant que Noan ne les a pas valides.
JEU_V2_BROUILLON = DOSSIER / "jeu" / "questions-v2-brouillon.yaml"
RESERVE_V2_BROUILLON = DOSSIER / "jeu" / "reserve-v2-brouillon.yaml"
SCEAU = DOSSIER / "jeu" / "sceau.json"
CONSIGNE_JUGE = DOSSIER / "juge" / "consigne.md"
# Juge v2 (etude, fiche 3.11, option 11b) : reference fixe, la meme pour A, B et C.
CONSIGNE_JUGE_V2 = DOSSIER / "juge" / "consigne-v2.md"
# Passages d'or (etape 0.1 du plan d'experience).
CONSIGNE_OR = DOSSIER / "juge" / "consigne-or.md"
# Regles de decision gelees (etude, section 2) : lues par assistant-comparer.
REGLES = DOSSIER / "regles-decision.toml"
# Banc d'aiguillage, a part du jeu (fiche 3.6).
BANC_AIGUILLAGE = DOSSIER / "aiguillage.yaml"
# Cle du sceau pour chaque version de la consigne du juge.
CLES_CONSIGNE = {"v1": "consigne_juge_sha256", "v2": "consigne_juge_v2_sha256"}

FAMILLES = {
    "fait_chiffre",
    "description",
    "transversale",
    "contact",
    "hors_sujet",
    "personnel",
    "opinion",
    "injection",
    "fausse_premisse",
    "competence_absente",
    "renseignement",  # disponibilite, contrat, lieu, langues, formation
    "incomprehensible",  # charabia, question sans objet : nature de refus de l'INSEI
    # Jeu multi-tours (conversations.yaml) : une famille par piege de la memoire.
    "suite_elliptique",
    "traduction",
    "reformulation",
    "changement_sujet",
    "hors_sujet_milieu",
    "retour_sujet",
    "comparaison",
    "suite_refus",
    "attaque_multitours",
    "autonome_apres_historique",
}
ATTENDUS = {"repondre", "carte", "refuser"}


class SceauRompu(RuntimeError):
    pass


@dataclass(frozen=True)
class QuestionEval:
    id: str
    langue: str
    famille: str
    question: str
    attendu: str
    reponse_attendue: str
    pages: tuple[str, ...] = ()
    chiffres: tuple[str, ...] = ()
    motifs_interdits: tuple[str, ...] = field(default_factory=tuple)
    # Faits cles attendus, courts : le juge verifie leur presence, pas une phrase modele.
    faits: tuple[str, ...] = ()
    a_valider: bool = False
    # Question d'origine d'une variante (bloc 1 du jeu v2) : les intervalles se
    # calculent par bootstrap groupe. Vide : la question est son propre groupe.
    groupe: str = ""
    note: str = ""
    # Questions jouees avant, dans la meme conversation (jeu multi-tours).
    tours: tuple[str, ...] = ()

    @property
    def a_repondre(self) -> bool:
        return self.attendu in {"repondre", "carte"}

    @property
    def question_jugee(self) -> str:
        """La question telle que le juge la lit : precedee des questions de la
        conversation s'il y en a. Sans tours, la question seule (jeu isole inchange)."""
        if not self.tours:
            return self.question
        avant = " ".join(f"{i}) {t}" for i, t in enumerate(self.tours, 1))
        return (
            f"Questions précédentes de la même conversation, dans l'ordre : {avant}\n"
            f"Question évaluée : {self.question}"
        )

    @property
    def faits_cles(self) -> tuple[str, ...]:
        """Faits cles, ou la phrase de reference des anciens jeux (reserve scellee v1)."""
        if self.faits:
            return self.faits
        return (self.reponse_attendue,) if self.reponse_attendue else ()


def empreinte_fichier(chemin: Path) -> str:
    # Fins de ligne normalisees : le sceau ne depend pas de la plateforme.
    return hashlib.sha256(chemin.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def charger(chemin: Path) -> list[QuestionEval]:
    brut = yaml.safe_load(chemin.read_text(encoding="utf-8")) or []
    questions = []
    for d in brut:
        q = QuestionEval(
            id=str(d["id"]),
            langue=d["langue"],
            famille=d["famille"],
            question=str(d["question"]).strip(),
            attendu=d["attendu"],
            reponse_attendue=str(d.get("reponse_attendue", "")).strip(),
            pages=tuple(d.get("pages") or ()),
            chiffres=tuple(map(str, d.get("chiffres") or ())),
            motifs_interdits=tuple(d.get("motifs_interdits") or ()),
            faits=tuple(str(f).strip() for f in d.get("faits") or ()),
            a_valider=bool(d.get("a_valider", False)),
            groupe=str(d.get("groupe") or d["id"]),
            note=str(d.get("note") or ""),
            tours=tuple(str(t).strip() for t in d.get("tours") or ()),
        )
        if q.langue not in {"fr", "en"} or q.famille not in FAMILLES or q.attendu not in ATTENDUS:
            raise ValueError(f"question {q.id} invalide")
        if q.attendu == "repondre" and not q.pages:
            raise ValueError(f"question {q.id} : pages attendues manquantes")
        if q.attendu == "repondre" and not q.faits_cles:
            raise ValueError(f"question {q.id} : faits attendus manquants")
        if any(not t or len(t) > 500 for t in q.tours):
            raise ValueError(f"question {q.id} : tour vide ou de plus de 500 caracteres")
        questions.append(q)
    ids = [q.id for q in questions]
    if len(ids) != len(set(ids)):
        raise ValueError(f"identifiants en double dans {chemin.name}")
    return questions


def lire_sceau() -> dict:
    return json.loads(SCEAU.read_text(encoding="utf-8"))


def verifier_reserve() -> str:
    attendu = lire_sceau()["reserve_sha256"]
    reel = empreinte_fichier(RESERVE)
    if reel != attendu:
        raise SceauRompu("la reserve scellee a ete modifiee : son empreinte ne correspond plus")
    return reel


def fichier_consigne(version: str = "v1") -> Path:
    return {"v1": CONSIGNE_JUGE, "v2": CONSIGNE_JUGE_V2}[version]


def consigne_juge(version: str = "v1") -> str:
    fichier = fichier_consigne(version)
    texte = fichier.read_text(encoding="utf-8")
    attendue = lire_sceau().get(CLES_CONSIGNE[version])
    if not attendue:
        raise SceauRompu(
            f"la consigne {version} du juge n'est pas scellee : "
            f"assistant-sceller --consigne-juge --version-juge {version} --motif ..."
        )
    if empreinte_fichier(fichier) != attendue:
        raise SceauRompu(f"la consigne {version} du juge a change : elle est gelee par empreinte")
    return texte
