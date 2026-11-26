"""Lecture des reglages : TOML versionne, fichiers d'experience superposes, environnement.

Ordre d'application, du plus faible au plus fort :
1. `config/reglages.toml` (ou le fichier nomme par ASSISTANT_REGLAGES) ;
2. les fichiers superposes (`evaluation/configs/NN-nom.toml`), dans l'ordre donne
   (argument `superpositions`, ou ASSISTANT_SUPERPOSITIONS separes par des virgules) ;
3. les variables d'environnement ASSISTANT_<SECTION>_<CLE>.

Un fichier superpose ne peut pas inventer de cle : une faute de frappe dans une
variante d'experience ferait mesurer la base en croyant mesurer la variante.
Seule exception : la table [modeles], ou l'on peut decrire un modele de plus.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RACINE = Path(os.environ.get("ASSISTANT_RACINE", Path(__file__).resolve().parents[2]))
FICHIER_DEFAUT = RACINE / "config" / "reglages.toml"

# Tables ou un fichier superpose peut ajouter des cles (un modele de plus).
TABLES_OUVERTES = {"modeles"}
# Valeurs retirees de la configuration effective enregistree dans les sorties.
MASQUEES = {("moteur", "projet_gcp"), ("securite", "compte_evaluation")}

MODES_RECHERCHE = {"rag", "tout_le_corpus"}
ORDRES_CONTENU = {"question_d_abord", "passages_d_abord"}
CONSIGNES = {"v1", "v2"}
AIGUILLAGES = {"v1", "classes"}
DECOUPAGES = {"mots360", "section", "page", "petit_grand"}
TACHES_QUESTION = {"RETRIEVAL_QUERY", "QUESTION_ANSWERING"}


def _convertir(brut: str, modele: Any) -> Any:
    """Convertit une variable d'environnement dans le type de la valeur TOML."""
    if isinstance(modele, bool):
        return brut.strip().lower() in {"1", "true", "oui", "vrai", "yes"}
    if isinstance(modele, int):
        return int(brut)
    if isinstance(modele, float):
        return float(brut)
    if isinstance(modele, list):
        return (
            json.loads(brut)
            if brut.strip().startswith("[")
            else [x.strip() for x in brut.split(",") if x.strip()]
        )
    return brut


def superposer(base: dict, dessus: dict, chemin: str = "") -> dict:
    """Fusion profonde ; une cle absente de la base est refusee (hors tables ouvertes)."""
    sortie = copy.deepcopy(base)
    for cle, valeur in dessus.items():
        nom = f"{chemin}.{cle}" if chemin else cle
        ouverte = chemin.split(".")[0] in TABLES_OUVERTES if chemin else cle in TABLES_OUVERTES
        if cle not in sortie:
            if not ouverte:
                raise KeyError(f"cle inconnue dans un fichier superpose : {nom}")
            sortie[cle] = copy.deepcopy(valeur)
        elif isinstance(sortie[cle], dict) and isinstance(valeur, dict):
            sortie[cle] = superposer(sortie[cle], valeur, nom)
        elif isinstance(sortie[cle], dict) != isinstance(valeur, dict):
            raise TypeError(f"{nom} : une table ne peut pas devenir une valeur, ni l'inverse")
        else:
            sortie[cle] = copy.deepcopy(valeur)
    return sortie


def _lire_toml(chemin: Path) -> dict:
    with Path(chemin).open("rb") as f:
        return tomllib.load(f)


def _superpositions(superpositions, env: dict[str, str]) -> list[Path]:
    if superpositions:
        return [Path(p) for p in superpositions]
    brut = env.get("ASSISTANT_SUPERPOSITIONS", "")
    return [Path(p.strip()) for p in brut.split(",") if p.strip()]


def charger_brut(
    chemin: Path | None = None,
    env: dict[str, str] | None = None,
    superpositions: list[Path] | tuple[Path, ...] | None = None,
) -> dict:
    env = dict(os.environ) if env is None else env
    chemin = Path(env.get("ASSISTANT_REGLAGES", chemin or FICHIER_DEFAUT))
    donnees = _lire_toml(chemin)
    for fichier in _superpositions(superpositions, env):
        p = fichier if fichier.is_absolute() else (Path.cwd() / fichier)
        if not p.exists():
            p = RACINE / fichier
        donnees = superposer(donnees, _lire_toml(p))
    for section, valeurs in donnees.items():
        if not isinstance(valeurs, dict):
            continue
        for cle, valeur in valeurs.items():
            if isinstance(valeur, dict):
                continue
            nom = f"ASSISTANT_{section}_{cle}".upper()
            if nom in env:
                valeurs[cle] = _convertir(env[nom], valeur)
    return donnees


def effective(donnees: dict) -> dict:
    """La configuration effective, sans les identifiants d'infrastructure."""
    sortie = copy.deepcopy(donnees)
    for section, cle in MASQUEES:
        if cle in sortie.get(section, {}):
            sortie[section][cle] = "(masque)"
    return sortie


def empreinte_donnees(donnees: dict) -> str:
    texte = json.dumps(effective(donnees), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class Reglages:
    moteur: dict
    corpus: dict
    recherche: dict
    limites: dict
    securite: dict
    journal: dict
    tarifs: dict
    site: dict
    agent: dict = field(default_factory=dict)
    altcha: dict = field(default_factory=dict)
    aiguillage: dict = field(default_factory=dict)
    refus: dict = field(default_factory=dict)
    memoire: dict = field(default_factory=dict)
    modeles: dict = field(default_factory=dict)
    superpositions: tuple[str, ...] = ()
    secrets: dict = field(default_factory=dict, repr=False)

    @property
    def mode(self) -> str:
        return self.moteur["mode"]

    @property
    def branche(self) -> str:
        """'chemin' (B, chemin fixe, service deploye) ou 'agent' (A, agent a outils)."""
        return self.moteur.get("branche", "chemin")

    @property
    def mode_recherche(self) -> str:
        """'rag' (passages trouves) ou 'tout_le_corpus' (C : le site de la langue)."""
        return self.recherche.get("mode", "rag")

    @property
    def architecture(self) -> str:
        """A (agent), B (chemin fixe) ou C (tout le corpus dans la consigne)."""
        if self.branche == "agent":
            return "A"
        return "C" if self.mode_recherche == "tout_le_corpus" else "B"

    @property
    def memoire_active(self) -> bool:
        """Memoire de conversation : branche chemin seulement."""
        return bool(self.memoire.get("active")) and self.branche == "chemin"

    @property
    def seuil_distance(self) -> float:
        cle = "seuil_distance_vertex" if self.mode == "vertex" else "seuil_distance_local"
        return float(self.recherche[cle])

    def tarif(self, modele: str) -> dict:
        """Dollars par million de jetons : entree, sortie (reflexion comprise), cache."""
        t = self.modeles.get(modele)
        if not t or not all(k in t for k in ("entree", "sortie", "cache")):
            raise ValueError(f'tarif absent pour le modele {modele} : table [modeles."{modele}"]')
        return {k: float(t[k]) for k in ("entree", "sortie", "cache")}

    def donnees(self) -> dict:
        return {
            "moteur": self.moteur,
            "corpus": self.corpus,
            "recherche": self.recherche,
            "limites": self.limites,
            "securite": self.securite,
            "journal": self.journal,
            "tarifs": self.tarifs,
            "site": self.site,
            "agent": self.agent,
            "altcha": self.altcha,
            "aiguillage": self.aiguillage,
            "refus": self.refus,
            "memoire": self.memoire,
            "modeles": self.modeles,
        }

    @property
    def empreinte(self) -> str:
        return empreinte_donnees(self.donnees())

    def verifier(self) -> None:
        """Refuse un demarrage incoherent plutot qu'un service qui repond mal."""
        if self.mode not in {"local", "vertex"}:
            raise ValueError(f"mode inconnu : {self.mode}")
        if self.branche not in {"chemin", "agent"}:
            raise ValueError(f"branche inconnue : {self.branche}")
        if self.securite["anti_robot"] not in {"altcha", "desactive"}:
            raise ValueError(f"anti-robots inconnu : {self.securite['anti_robot']}")
        self._verifier_options()
        if not 0 < self.seuil_distance < 2:
            raise ValueError(
                "seuil de distance non cale pour ce mode : lancer assistant-caler-seuil"
            )
        if self.mode == "vertex":
            if not self.moteur["projet_gcp"]:
                raise ValueError("ASSISTANT_MOTEUR_PROJET_GCP manquant")
            if not self.secrets.get("session"):
                raise ValueError("secret de session manquant (ASSISTANT_SECRET_SESSION)")
            if self.securite["anti_robot"] == "altcha" and not self.secrets.get("altcha"):
                raise ValueError("cle ALTCHA manquante (ASSISTANT_SECRET_ALTCHA)")

    def _verifier_options(self) -> None:
        m, rech = self.moteur, self.recherche
        if self.mode_recherche not in MODES_RECHERCHE:
            raise ValueError(f"recherche.mode inconnu : {self.mode_recherche}")
        ordre = m.get("ordre_contenu", "question_d_abord")
        if ordre not in ORDRES_CONTENU:
            raise ValueError(f"moteur.ordre_contenu inconnu : {ordre}")
        if self.architecture == "C" and ordre == "question_d_abord":
            # D1 de l'etude : question avant 35 000 jetons de corpus, le cache
            # implicite ne joue jamais. Rien a mesurer dans cet etat.
            raise ValueError(
                'tout_le_corpus exige moteur.ordre_contenu = "passages_d_abord" (cache implicite)'
            )
        if self.branche == "agent" and self.mode_recherche == "tout_le_corpus":
            raise ValueError("tout_le_corpus est une variante du chemin fixe : branche = chemin")
        if m.get("consigne", "v1") not in CONSIGNES:
            raise ValueError(f"moteur.consigne inconnue : {m.get('consigne')}")
        if m.get("tache_question", "RETRIEVAL_QUERY") not in TACHES_QUESTION:
            raise ValueError(f"moteur.tache_question inconnue : {m.get('tache_question')}")
        if self.aiguillage.get("version", "v1") not in AIGUILLAGES:
            raise ValueError(f"aiguillage.version inconnue : {self.aiguillage.get('version')}")
        if self.corpus.get("decoupage", "mots360") not in DECOUPAGES:
            raise ValueError(f"corpus.decoupage inconnu : {self.corpus.get('decoupage')}")
        if rech.get("hybride") and self.architecture == "C":
            raise ValueError("recherche.hybride n'a pas d'objet en tout_le_corpus")
        if self.refus.get("prefiltre_seuil") and self.architecture != "C":
            raise ValueError("refus.prefiltre_seuil vaut pour tout_le_corpus (en B le seuil joue)")
        mem = self.memoire
        if mem.get("active"):
            if int(mem.get("tours", 3)) < 1 or int(mem.get("caracteres_reponse", 300)) < 50:
                raise ValueError("memoire : tours >= 1 et caracteres_reponse >= 50")
            if not 0 < float(mem.get("delai_reecriture_s", 4)) <= 15:
                raise ValueError("memoire.delai_reecriture_s : entre 0 et 15 s")
            if mem.get("modele"):
                self.tarif(str(mem["modele"]))
        modele = m["modele_redaction"]
        self.tarif(modele)
        permises = self.modeles.get(modele, {}).get("reflexions")
        reflexion = str(m.get("reflexion", ""))
        if permises is not None and reflexion and reflexion not in permises:
            raise ValueError(
                f"reflexion {reflexion!r} refusee par {modele} (permises : {permises}, "
                "ou vide pour la valeur par defaut du modele)"
            )


SECTIONS = ("moteur", "corpus", "recherche", "limites", "securite", "journal", "tarifs", "site")
SECTIONS_FACULTATIVES = ("agent", "altcha", "aiguillage", "refus", "memoire", "modeles")


def charger(
    chemin: Path | None = None,
    env: dict[str, str] | None = None,
    superpositions: list[Path] | tuple[Path, ...] | None = None,
) -> Reglages:
    env = dict(os.environ) if env is None else env
    d = charger_brut(chemin, env, superpositions)
    secrets = {
        "session": env.get("ASSISTANT_SECRET_SESSION", ""),
        "altcha": env.get("ASSISTANT_SECRET_ALTCHA", ""),
    }
    return Reglages(
        **{k: d[k] for k in SECTIONS},
        **{k: d.get(k, {}) for k in SECTIONS_FACULTATIVES},
        superpositions=tuple(str(p) for p in _superpositions(superpositions, env)),
        secrets=secrets,
    )
