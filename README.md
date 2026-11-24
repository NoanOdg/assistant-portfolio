# Assistant du portfolio

Assistant public de noan-ouedraogo.dev (mission 6) : on lui pose une question sur le parcours et les projets de Noan, en français ou en anglais ; il cherche dans le contenu public du site, cite ses sources et refuse quand il ne sait pas.

Cible : Cloud Run (FastAPI, réponses en flux SSE), Vertex AI (Gemini et embeddings), recherche vectorielle Firestore, Terraform, GitHub Actions sans clé (Workload Identity Federation). Étape 2 : le même conteneur sur GKE Autopilot avec Helm.

**État au 19/11/2026 : squelette local.** Aucune ressource Google Cloud n'existe, aucun appel au cloud n'a été fait, aucune clé n'est stockée. Rien n'est poussé.

## Ce qui existe et tourne en local

| Dossier | Contenu |
|---|---|
| `src/assistant/corpus/` | Ingestion relançable : MDX du portfolio + `faits.yml` + `identite.yml` (source `mdx`), ou site publié lu par son plan (source `site`), plus les deux CV PDF liés depuis la page Contact (texte extrait par pypdf, téléphone, courriel, adresse et commune retirés avant indexation). Chiffres des composants `<Metrique>` résolus sur liste blanche (jamais `source` ni `verification`). Découpage par section h2/h3, en-tête de contexte, ancre conservée. Garde : plus de 30 % de morceaux disparus, rien n'est écrit. |
| `src/assistant/embeddings.py`, `generation.py`, `recherche.py` | Interfaces, faux déterministes pour les tests, implémentations Vertex AI et Firestore. Recherche en mémoire et branche « tout le site dans la consigne ». |
| `src/assistant/pipeline.py` | Chemin fixé par le code : aiguillage (données personnelles, contact), recherche, refus sous le seuil sans appeler le modèle, rédaction en flux, refus du modèle (`[REFUS]`) remplacé par un texte écrit d'avance, sources citées. |
| `src/assistant/agent.py`, `outils.py`, `prompts/agent.md` | Branche A de la comparaison (`moteur.branche = "agent"`, défaut `"chemin"`) : agent Gemini à appels de fonctions, quatre outils en lecture seule (recherche au même seuil que B, lecture d'une page du corpus, liste des projets, carte de contact rendue par le code), boucle bornée (`[agent]` des réglages), mêmes gardes que B appliquées par le code, consigne versionnée par empreinte. |
| `src/assistant/filtres.py` | Filtres en flux : chiffre absent des passages remplacé, liens hors liste blanche retirés, courriels, images, HTML, tirets longs. |
| `src/assistant/api.py` | `GET /defi` (défi ALTCHA signé), `POST /session` (solution ALTCHA vérifiée, jeton de session HMAC), `POST /question` (SSE), `GET /sante`. Contrat exact pour le widget du site : `docs/api-widget.md`. En public, l'événement `fin` ne porte que l'identifiant de trace : chemin, coût, jetons, durées et distance du meilleur passage restent dans le journal et dans le flux de l'évaluation (jeton d'identité Google du compte de déploiement). CORS limité au site, aux aperçus Cloudflare Pages du projet et à `localhost:4321`. Limites : 500 caractères, 450 jetons, 30 s, 5 questions par minute et 30 par jour par visiteur, 60 par jour au total. Journaux JSON sur liste blanche : ni IP, ni navigateur, ni texte de question. |
| `src/assistant/evaluation/` | Jeu de 40 questions (FR et EN, pièges compris), réserve scellée de 10, juge à consigne gelée par empreinte (faits clés, passages transmis, date du jour), cartes de contact notées par le code, mesures en taux absolus avec intervalle de Wilson, porte de la CI, calage du seuil, scellement, page de notation du juge par Noan et calcul de l'accord (accord et kappa de Cohen, échantillon stratifié de plusieurs passages). Instrument de la comparaison (étude du 24/11/2026) : reprise sur 429 et 5xx (`reprise.py`), juge v2 à référence fixe (`juge.py`), bruit et règle R0 (`comparaison.py`), rejugement et seuil simulé (`rejouer.py`), passages d'or et rappel hors ligne (`rappel.py`), banc d'aiguillage (`aiguillage.py`). |
| `evaluation/` | `jeu/questions.yaml`, `jeu/reserve-scellee.yaml`, `jeu/sceau.json` (empreintes et historique de la consigne du juge), `jeu/questions-v2-brouillon.yaml` et `jeu/reserve-v2-brouillon.yaml` (à valider : 100 questions de travail et une réserve de 100, jouée une seule fois pour le chiffre publié ; 65 à répondre, 35 à refuser, découpage de l'INSEI ; champ `groupe` sur les variantes du bloc 1), `juge/consigne.md`, `juge/consigne-v2.md` (référence fixe, à sceller), `juge/consigne-or.md`, `seuils.toml`, `regles-decision.toml` (règle R0 gelée, à sceller), `configs/` (une variante d'expérience par fichier superposé), `aiguillage.yaml` (banc de 60 tournures, brouillon). |
| `prompts/` | `redaction.md` (servie), `redaction-v2.md` (consigne par classes d'erreurs, option 9b), `etiquette.md` (sortie structurée de l'intention, option 6c), `agent.md`. |
| `infra/terraform/` | Modules : API, Artifact Registry, Secret Manager, Firestore (index vectoriels, TTL), comptes de service, Workload Identity Federation, Cloud Run, budget, exclusion des journaux, GKE. Environnement `prod`. |
| `infra/helm/assistant/` | Chart de l'étape 2 : Deployment, Service LoadBalancer, HPA, PDB, sondes, Workload Identity. |
| `.github/workflows/` | `ci.yml` (lint, tests, évaluation hors ligne, Terraform, Helm), `deploiement.yml` (révision candidate sans trafic, évaluation, porte, bascule), `gke.yml` (manuel), `calage-seuil.yml` (manuel), `comparaison.yml` (manuel : plan d'expérience, voir plus bas). |

Vérifié le 24/11/2026 sur le PC, après le codage de la partie (c) de l'étude : 266 tests verts, ruff sans remarque (`ruff check` et `ruff format --check`), aucun appel au cloud. Vérifié le 23/11/2026 sur le PC : 123 tests verts (instrument d'évaluation, extraction des CV, branche agent), ruff sans remarque. Après le passage à ALTCHA (23/11/2026) : 177 tests verts (défi, solution juste, fausse, expirée, rejouée, forgée, envois simultanés, CORS, `fin` publique réduite à la trace), ruff sans remarque, `terraform fmt -check` et `terraform validate` verts, `helm lint` vert. Le 19/11/2026 : ruff sans remarque, `terraform fmt -check` et `terraform validate` verts (image Docker officielle, fournisseur google 7.46.2, sans backend), `helm lint` vert, image Docker construite et lancée en mode local.

## Ce qui est écrit mais jamais exécuté

- `VertexEmbedder`, `VertexGenerateur`, `JugeVertex` (google-genai), `RechercheFirestore`, `MagasinFirestore`, `CompteurFirestore`, `JournalFirestore`, `IdentiteGoogle`. Les noms de paramètres du SDK (niveau de réflexion de Gemini 3 en particulier) sont à vérifier au premier appel.
- `SourceSite` : testée sur des pages factices, jamais sur le site en ligne.
- Les workflows GitHub, `terraform plan` et `apply`, `helm install`. Le workflow « Comparaison » en particulier : `gh run download` entre runs, `hashFiles`, artefact `comparaison`.
- Les identifiants de modèles (`gemini-3.1-flash-lite`, `gemini-3.8-flash`, `gemini-embedding-001`) et les tarifs viennent de la conception : à confirmer dans la console au jour 1. Tarifs de 3.5 Flash-Lite et 3.8 Flash : page de prix de l'API Gemini lue le 24/11/2026 (étude, section 8), à confirmer sur la première facture.
- Partie (c) de l'étude, côté Google : sortie structurée en flux (`response_schema` avec `propertyOrdering`), compteurs `cached_content_token_count` et `thoughts_token_count`, `title` et `QUESTION_ANSWERING` des embeddings, juge v2, `LecteurOrVertex`.

## Ce qui manque encore

- Seuil de refus Vertex : non calé (`-1`, le service refuse de démarrer tant qu'il ne l'est pas).
- Rédacteur simulé aux délais réalistes pour le test de charge de l'étape 2, et le harnais de charge lui-même.
- Traces OpenTelemetry, tableau de bord et alertes Cloud Monitoring.
- Widget du portfolio sur chaque page (contrat : `docs/api-widget.md`), origine du service dans `connect-src` de la politique de sécurité du site (`script-src` inchangé), mention RGPD.
- Nom du projet Cloudflare Pages dans `securite.projet_pages` de `config/reglages.toml` (vide : aucun aperçu admis par CORS).
- Référence de non-régression (`evaluation/reference.json`) : à enregistrer après la première évaluation réelle.

## En local

```sh
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .

# Corpus depuis le contenu du portfolio (lecture seule)
uv run assistant-ingerer --source mdx --contenu ../Carriere/portfolio/src/content

# Évaluation hors ligne (faux embeddings, faux rédacteur, juge heuristique)
uv run assistant-evaluer --cible local --juge heuristique --sortie build/eval-locale.json
uv run assistant-porte --resultats build/eval-locale.json --profil local

# Service local
uv run uvicorn assistant.api:app_depuis_env --factory --port 8080 --no-access-log
```

La comparaison A/B/C et les briques de l'étude se jouent comme décrit dans la section suivante.

Les taux de l'évaluation locale ne mesurent rien de la qualité réelle : le faux rédacteur recopie la première phrase du meilleur passage. Ils prouvent que le harnais tourne et que les contrôles faits par le code tiennent.

## Comparaison des configurations (étude du 24/11/2026)

L'étude `Carriere/projets/mission6-etude-rag-optimal.md` fait foi : options, défauts D1 à D11, règle R0, plan d'expérience. Tout ce que sa partie (c) demande est codé et testé hors ligne ; **rien n'est mesuré**. Les valeurs par défaut de `config/reglages.toml` reproduisent le service servi : aucune option ne change ce qui est servi tant que la mesure n'a pas tranché.

### Options et clés

Chaque option est une clé de `config/reglages.toml`, remplaçable par `ASSISTANT_<SECTION>_<CLE>`. Une variante d'expérience est un petit fichier de `evaluation/configs/`, superposé aux réglages (`--config`, répétable : les fichiers s'empilent dans l'ordre, chaque brique gagnante devient la base de la suivante). Un fichier superposé ne peut pas inventer de clé : une faute de frappe fait échouer le chargement au lieu de mesurer la base sous un autre nom.

| Option de l'étude | Clé | Défaut servi | Fichier |
|---|---|---|---|
| Architecture C, servie par configuration (4c, D11) | `recherche.mode = "tout_le_corpus"` | `"rag"` | `10-c0` |
| Passages avant la question, balises de données (D1, 10b) | `moteur.ordre_contenu = "passages_d_abord"` (obligatoire en C) | `"question_d_abord"` | `10-c0` |
| Renvois au-delà de [99] (D2) | toujours : renvois à trois chiffres, numérotation stable dans l'ordre du corpus | | |
| Distance du meilleur passage en C (D10) | toujours calculée et journalisée | | |
| Filtre de seuil avant C (7c) | `refus.prefiltre_seuil` | `false` | simulé : `assistant-simuler-seuil` |
| Date du jour (D7) | `moteur.date_du_jour` | `false` | `22-consigne-v2` |
| Consigne v2 par classes (9b, D8) | `moteur.consigne = "v2"` | `"v1"` | `22-consigne-v2` |
| Aiguillage par classes (6b) | `aiguillage.version = "classes"` | `"v1"` | `23b-aiguillage-classes` |
| Intention du modèle en sortie structurée (6c) | `aiguillage.etiquette_modele` | `false` | `23-etiquette` |
| Page Contact sans coordonnées (1b) | `corpus.inclure_contact` | `false` | `21-contact` |
| Corpus compact (1c) | `corpus.compact` | `false` | `26-compact` |
| Contrôle « aucune coordonnée » (1d) | toujours, bloquant à l'ingestion (code de sortie 3) | | |
| Coût avec cache et réflexion (D4) | toujours : `cout_usd` (réel) et `cout_production_usd` (sans cache) ; prix par modèle dans `[modeles."<id>"]` | | |
| Réflexion validée par modèle (point 5) | `[modeles."<id>"].reflexions` : 3.8 Flash refuse `minimal` au démarrage | | |
| Modèle et réflexion (8b, 8c, 8d) | `moteur.reflexion`, `moteur.modele_redaction` | 3.1 Flash-Lite, `minimal` | `24a`, `24b`, `24c` |
| Recherche hybride BM25 + KNN, RRF (4b) | `recherche.hybride`, `k_rrf`, `candidats_hybride` | `false`, 60, 20 | `31-hybride` |
| Tâche des questions (3b), titre des documents (3c) | `moteur.tache_question`, `moteur.titre_document` | `RETRIEVAL_QUERY`, `false` | `32`, `33` |
| Découpage (2b, 2c, 2d) | `corpus.decoupage = "section" \| "page" \| "petit_grand"` (les deux premiers à l'ingestion : `assistant-ingerer --decoupage`) | `"mots360"` | `34-petit-grand` |
| Chiffre attribué à un passage cité (10c) | toujours : mesure `chiffres_attribues`, non bloquante | | |
| Bornes de l'agent | `agent.max_tours` | 4 | `35-agent-tours-3` |

Non codés, conditionnels dans l'étude : le reclasseur Ranking API (5b, seulement si B gagne et que le rappel@6 reste sous 90 % ; point d'accès `global` seulement) et Model Armor (10d, seulement si une injection passe).

### Instrument

- `assistant-evaluer --config F1 --config F2 --repetitions K --parallele N --version-juge v2` : un fichier par passage (`nom-r1.json`, `nom-r2.json`...), avec l'empreinte de la configuration effective (identifiants masqués), de la consigne, du corpus servi, du corpus lu, du jeu et du juge.
- Reprise : 429, 5xx et coupures réseau rejoués jusqu'à 6 fois, attente de 2 s doublée à chaque essai, plafonnée à 60 s. Une erreur qui persiste devient `erreur_technique` : rejouée en fin de passage, jamais comptée comme fausse ; s'il en reste, `assistant-comparer` répond « a_rejouer ».
- Juge v2 (`--version-juge v2`, 11b) : la référence est le texte des pages attendues de la question dans sa langue, puis les passages cités, la même pour A, B et C, placée en tête. Champ de diagnostic « faits clés dans les passages transmis » : chaque échec se range en recherche ou en rédaction (`echecs` dans les mesures).
- `assistant-bruit` : σ des réussites entre les passages de la base et G = max(3, ⌈2σ⌉). `assistant-comparer` : règle R0 (six conditions, égalité au moins cher puis au plus simple), ou règle d'architecture avec `--architecture B=... C=... A=...` ; McNemar exact, Wilson et bootstrap groupé publiés sans décider. Paramètres : `evaluation/regles-decision.toml`, scellé par empreinte ; tant qu'il ne l'est pas, le rapport dit « verdict indicatif ».
- `assistant-rejuger` (nouveau juge, sans regénérer), `assistant-simuler-seuil` (7b contre 7c, sans appel), `assistant-or` (passages d'or), `assistant-rappel` (rappel@k, rang moyen réciproque, aire ROC de la distance, index en mémoire), `assistant-aiguillage` (banc de 60 tournures, sans modèle), `assistant-noter --resultats b0-r1.json c0-r1.json --echantillon 100` (tirage à parts égales stratifié par famille ; `assistant-accord` exige 90 % d'accord et un kappa d'au moins 0,7).

### Avant le premier passage (Noan)

1. Prérequis de l'étude (5.1) : refonte en ligne, CV PDF du site remplacés, page de l'assistant publiée (ou questions `depend_de_la_page_assistant` retirées de la décision), jeu v2 validé, mis à la place de `questions.yaml`, réserve v2 scellée (étape 8 ci-dessous).
2. Seuil Vertex recalé sur le site refondu (workflow « Calage du seuil », étape 9).
3. **Aiguillage à décider avant B0.** Avec `v1`, le brouillon du jeu v2 fait échouer les garde-fous du code dès la base : 4 cartes sur 6 et 2 refus personnels sur 3 (contact mal orthographié, téléphone, « combien il gagne »). La condition 1 de R0 bloquerait alors toute configuration. Banc du 24/11/2026, moitié gardée : `classes` 15 sur 16 en contact et personnel, `v1` 11 sur 16, aucun faux positif sur le jeu. Relire `evaluation/aiguillage.yaml`, relancer `uv run assistant-aiguillage --moitie verification --jeu evaluation/jeu/questions.yaml`, puis soit passer `aiguillage.version = "classes"` dans les réglages (décision écrite : le service servi change), soit ajouter `evaluation/configs/23b-aiguillage-classes.toml` à chaque passage.
4. Sceller le juge v2 et les règles, puis pousser :
   ```sh
   uv run assistant-sceller --consigne-juge --version-juge v2 --motif "reference fixe 11b, etude du 24/11/2026"
   uv run assistant-sceller --regles --motif "regle R0 gelee avant l'etape 0.2"
   ```
5. Si 3.8 Flash doit pouvoir gagner, relever `cout_production_max_usd` par une décision écrite avant sa mesure, puis resceller les règles.
6. Le workflow utilise le compte et le fournisseur d'identité de « Calage du seuil » (Vertex et Firestore) : rien à créer.

### Plan pas à pas depuis GitHub

Onglet Actions, « Comparaison », « Run workflow » sur `main`. Chaque run dépose l'artefact `comparaison` (sorties, corpus lu, `bruit.json`) et écrit son rapport dans sa page ; son identifiant est le nombre à la fin de l'URL du run. Un run qui reçoit `base_run_id` reprend le corpus et le bruit de la base : tous les passages d'une même chaîne jugent le même corpus, avec le G de B0. Les coûts sont ceux de l'étude (5.4), sans cache, prix de décembre 2026 ; le coût réel s'affiche dans chaque rapport (rédaction et juge).

| Étape | `operation` | `nom` | `configs` | `repetitions` | `base_run_id` / `base_nom` | Coût |
|---|---|---|---|---|---|---|
| 0.1 Passages d'or | `passages-or` | `or` | | | | 1,8 $ |
| 0.2 Base et bruit | `evaluer` | `b0` | (vide, ou `23b`, voir point 3) | 5 | | 4,5 $ |
| 1. C0 | `evaluer` | `c0` | `evaluation/configs/10-c0.toml` | 2 | run B0 / `b0` | 3,5 $ |
| 1. A0 | `evaluer` | `a0` | `evaluation/configs/11-a0.toml` | 2 | run B0 / `b0` | 2,2 $ |
| 0.3 Juge | sur le PC, voir ci-dessous | | | | | 0 $ (2 h de Noan) |
| 1. Décision | `architecture` | `archi` | | | run B0 / `b0`, `runs` : `c0=ID_C0 a0=ID_A0` | 0 $ |
| 2.5 Page Contact | `evaluer` | `c-contact` | `10-c0.toml 21-contact.toml` | 2 | run C0 / `c0` | 3,5 $ |
| 2.1 Consigne v2 | `evaluer` | `c-v2` | gagnantes + `22-consigne-v2.toml` | 2 | run gagnant / son nom | 3,5 $ |
| 2.2 Étiquette | `evaluer` | `c-etiq` | gagnantes + `23-etiquette.toml` | 2 | run gagnant / son nom | 3,5 $ |
| 2.3 Filtre de seuil | `simuler-seuil` | `c-seuil` | | | run gagnant / son nom | 0 $ |
| 2.4 Modèle | `evaluer` | `c-low`, `c-35`, `c-38` | gagnantes + `24a`, `24b` ou `24c` | 2 | run gagnant / son nom | 3,5, 3,9 et 7,4 $ |
| 2.6 Corpus compact | `evaluer`, seulement si C dépasse 0,012 $ | `c-compact` | gagnantes + `26-compact.toml` | 2 | run gagnant | 3,5 $ |
| 3. Combinaison | `evaluer` | `final` | toutes les gagnantes | 3 | run B0 / `b0` | 5,3 $ |

Total : environ 44 $ sans cache, 25 à 30 $ avec le cache implicite sur les rafales de C, jusqu'à 65 $ en janvier 2027 (le juge et 3.8 Flash doublent). Le faire en décembre économise environ 20 $. Les fichiers de `configs` se donnent par leur chemin complet (`evaluation/configs/...`), séparés par des espaces.

Étape 0.3, validation du juge, sur le PC : télécharger les artefacts des runs B0 et C0 (`gh run download ID -n comparaison -D build/b0`), puis
```sh
uv run assistant-noter --resultats build/b0/comparaison/b0-r1.json build/c0/comparaison/c0-r1.json --echantillon 100
# noter build/notation-juge.html a l'aveugle, exporter notes-noan.json
uv run assistant-accord --resultats build/b0/comparaison/b0-r1.json build/c0/comparaison/c0-r1.json --echantillon 100 --notes notes-noan.json
```
Sous 90 % d'accord ou sous un kappa de 0,7 : corriger `evaluation/juge/consigne-v2.md` par classe, resceller (`--version-juge v2`), pousser, puis `operation = rejuger` sur les runs B0, C0 et A0 (`base_run_id` : chaque run, `base_nom` : son nom ; environ 0,8 $ par passage, sans regénérer). Chaque run de rejugement remplace le run d'origine (mêmes noms de fichiers, bruit recalculé pour B0) : l'architecture se décide sur leurs identifiants.

Si B gagne l'étape 1 : d'abord `operation = rappel` (passages d'or contrôlés et versés dans `evaluation/or/passages-or.json`), avec `configs` `31-hybride`, `32-question-answering`, `33-titre-document`, `34-petit-grand` (moins de 0,02 $ chacun, sans rédaction), puis les deux meilleures en passage complet. Si A gagne : mêmes briques, plus `35-agent-tours-3`.

Étape 4, la réserve, ne passe jamais par ce workflow : une seule fois, sur le PC, après scellement, `uv run assistant-evaluer --jeu reserve --motif "..." --config ...` avec la configuration gelée (environ 1,8 $).

Les mêmes étapes se jouent sur le PC (`uv sync --extra gcp`, variables `ASSISTANT_MOTEUR_MODE=vertex`, `ASSISTANT_MOTEUR_PROJET_GCP`, `ASSISTANT_CORPUS_VERSION` donnée par `assistant-indexer --version auto`, `ASSISTANT_SECRET_SESSION`, `ASSISTANT_SECURITE_ANTI_ROBOT=desactive`), par exemple :
```sh
uv run assistant-evaluer --cible local --config evaluation/configs/10-c0.toml --repetitions 2 --parallele 2 \
  --juge vertex --version-juge v2 --fichier-jeu evaluation/jeu/questions.yaml --sortie build/comparaison/c0.json
uv run assistant-bruit "build/comparaison/b0-r*.json" --sortie build/comparaison/bruit.json
uv run assistant-comparer --base "build/comparaison/b0-r*.json" --challenger "build/comparaison/c0-r*.json" \
  --bruit build/comparaison/bruit.json --rapport build/comparaison/rapport-c0.md
```

## Ce que Noan doit faire lui-même

Dans cet ordre. Rien de tout cela n'a été fait.

1. **Projet et facturation.** Créer le projet GCP, le lier à un compte de facturation, noter l'identifiant, le numéro (`gcloud projects describe PROJET --format='value(projectNumber)'`) et l'identifiant du compte de facturation. Vérifier si le crédit d'essai s'applique (donnée manquante dans la conception).
2. **gcloud sur le PC.** `gcloud auth login`, `gcloud auth application-default login`, `gcloud config set project PROJET`, puis `gcloud services enable serviceusage.googleapis.com cloudresourcemanager.googleapis.com billingbudgets.googleapis.com`.
3. **Bucket d'état Terraform**, versionné : `gcloud storage buckets create gs://PROJET-tfstate --location=europe-west1 --uniform-bucket-level-access`, puis `gcloud storage buckets update gs://PROJET-tfstate --versioning`.
4. **Variables.** Copier `terraform.tfvars.example` en `terraform.tfvars` et `backend.hcl.example` en `backend.hcl` (tous deux ignorés par git). L'identifiant numérique GitHub : `gh api users/NoanOdg --jq .id`.
5. **Premier apply en deux temps** (les secrets doivent avoir une valeur avant la première révision Cloud Run) :
   ```sh
   cd infra/terraform/environnements/prod
   terraform init -backend-config=backend.hcl
   terraform apply -target=module.apis -target=module.comptes -target=module.secrets
   openssl rand -hex 32 | tr -d '\n' | gcloud secrets versions add assistant-cle-session --data-file=-
   openssl rand -hex 32 | tr -d '\n' | gcloud secrets versions add assistant-cle-altcha --data-file=-
   terraform apply
   ```
   `infra/installer-cloud-shell.sh` fait la même chose dans Cloud Shell. La clé ALTCHA signe les défis (HMAC) : elle ne quitte jamais Secret Manager, aucun compte tiers n'est nécessaire.

   **Si l'infrastructure existe déjà avec Turnstile** (passage à ALTCHA du 23/11/2026), dans cet ordre :
   ```sh
   cd infra/terraform/environnements/prod
   terraform apply -target='module.secrets.google_secret_manager_secret.secret["assistant-cle-altcha"]' \
     -target='module.secrets.google_secret_manager_secret_iam_member.lecture["assistant-cle-altcha"]'
   openssl rand -hex 32 | tr -d '\n' | gcloud secrets versions add assistant-cle-altcha --data-file=-
   terraform apply
   ```
   Le dernier `apply` bascule Cloud Run sur `ASSISTANT_SECRET_ALTCHA` et `anti_robot = "altcha"`, puis détruit le secret `assistant-turnstile`. Pousser ensuite le code : le déploiement construit l'image qui sait vérifier ALTCHA (l'ancienne image ne connaît pas `altcha` et tournerait sans anti-robots entre les deux : le site n'appelle pas encore le service, mais ne pas laisser traîner).
6. **Budget.** Vérifier que la devise du compte de facturation est l'euro (sinon changer `devise`), et que l'alerte arrive par courriel.
7. **Quota et cache Vertex.** Gemini sur Vertex fonctionne en quota partagé dynamique : aucun quota par projet à abaisser. Les garde-fous sont le plafond quotidien de l'application, une seule instance Cloud Run et l'alerte de budget. Le compte de facturation doit être payant (un compte d'essai n'a pas accès à Gemini sur Vertex).
8. **Valider le jeu, puis le juge, après la mise en ligne de la refonte** (rien n'est réglé sur le texte du site avant). Relire `jeu/questions-v2-brouillon.yaml` et `jeu/reserve-v2-brouillon.yaml` question par question (faits clés tirés du Profil, `a_valider: true` partout), puis les mettre à la place de `questions.yaml` et `reserve-scellee.yaml` et sceller : `uv run assistant-sceller --motif "validation par Noan"`. Valider ensuite le juge : jouer le jeu sur la révision en ligne (`assistant-evaluer --cible http ... --juge vertex --sortie build/eval-juge.json`), `uv run assistant-noter --resultats build/eval-juge.json`, ouvrir `build/notation-juge.html`, noter chaque réponse sans voir le juge, exporter `notes-noan.json`, puis `uv run assistant-accord --resultats build/eval-juge.json --notes notes-noan.json`. Sous 90 % d'accord, corriger `juge/consigne.md` et la resceller : `uv run assistant-sceller --consigne-juge --motif "..."`.
9. **Caler le seuil Vertex** (le service refuse de démarrer tant qu'il vaut -1) : onglet Actions, workflow « Calage du seuil », « Run workflow » sur `main`. Il indexe le site dans une version `calage-…` (moins de 0,02 $) et affiche le seuil proposé, avec les questions écartées. Recopier la valeur dans `seuil_distance_vertex` de `config/reglages.toml`, pousser : le déploiement repart seul. Seule source du seuil : ce fichier (plus de variable Terraform). La valeur du 23/11 est calée sur l'ancien site : relancer le workflow après la mise en ligne de la refonte, puis l'étape 8.
10. **Dépôt GitHub** (privé ou public, à trancher) : créer le dépôt, pousser, puis dans les réglages :
    - variable `GCP_PROJET` ;
    - secrets `GCP_WIF_FOURNISSEUR` et `GCP_COMPTE_DEPLOIEMENT` (sorties `fournisseur_wif` et `compte_deploiement` de Terraform) ;
    - environnement `production` avec Noan en relecteur obligatoire ;
    - recommandé : épingler les actions par empreinte de commit plutôt que par étiquette.
11. **Portfolio.** Widget sur chaque page d'après `docs/api-widget.md`, origine du service dans `connect-src` (décision du portfolio à écrire), nom du projet Pages dans `securite.projet_pages`, et un `repository_dispatch` (type `portfolio-publie`) envoyé vers ce dépôt à chaque déploiement du site, avec un jeton GitHub à droits limités.
12. **Étape 2.** `activer_gke = true`, `terraform apply`, `kubectl create secret generic assistant-secrets -n assistant --from-literal=session=$(openssl rand -hex 32) --from-literal=altcha=$(openssl rand -hex 32)`, workflow `gke.yml`, mesures, puis `activer_gke = false`, `terraform apply` et vérification qu'il ne reste ni disque, ni adresse, ni règle de transfert.

## Écarts avec la conception (`projets/mission6-conception.md`)

1. **Le service déployé reste un chemin fixé** (révision du 19/11) : un seul appel au modèle par question. L'agent (branche A) existe pour la comparaison, sans LangGraph : boucle écrite à la main sur les appels de fonctions de google-genai, gardes placées dans le code. Outil `faits_projet` remplacé par `lire_page` (les chiffres de `faits.yml` sont déjà dans les pages du corpus). Une carte demandée par l'agent clôt la question, comme l'aiguillage de contact. Si aucun outil ne rend de passage après un tour, refus écrit d'avance sans rappeler le modèle : l'agent ne reformule que s'il a déjà de la matière.
2. **Corpus local** : les MDX, avec les chiffres résolus depuis `faits.yml`. Pas besoin d'exporter `faits.json` pour l'instant. Les ancres imitent github-slugger : à vérifier contre le site rendu. La source `site` reste celle de la CI.
3. **La version du corpus est inscrite dans l'image** : une révision Cloud Run sert un corpus fixe. On revient en arrière en reroutant le trafic, sans document `config/corpus_actif`.
4. **L'évaluation de la CI passe la porte avec un jeton d'identité Google** du compte de déploiement, et a son propre plafond (150 par jour). La conception ne disait pas comment elle franchissait l'anti-robots. Elle ne disait pas non plus que 40 questions par passage mangeraient les deux tiers du plafond public de 60.
5. **Terraform est appliqué depuis le PC, jamais par la CI.** Un `apply` depuis Actions demanderait un compte quasi propriétaire, ce qui contredit le moindre privilège.
6. **Exclusion des journaux** : elle retire tout le journal des requêtes Cloud Run du service, statut et latence compris. Les métriques intégrées restent.
7. **GKE** : avec 2 réplicas, le limiteur par visiteur et le registre des défis ALTCHA déjà présentés, tous deux en mémoire, comptent par pod (un défi pourrait servir une fois par pod pendant ses 120 s). Le plafond global, dans Firestore, reste exact.
8. **Historique de conversation** non géré : une question à la fois, sans réécriture des questions de suite.
9. **ALTCHA au lieu de Turnstile** (décision du 23/11/2026) : l'assistant devient un widget sur chaque page, et Turnstile obligerait à ouvrir `script-src` de tout le site à Cloudflare, ce que la décision 05 du portfolio interdit. ALTCHA est une preuve de travail (PoW v2 de la bibliothèque officielle `altcha`, licence MIT, PBKDF2/SHA-256) : défi signé par HMAC avec une clé de Secret Manager, valable 120 s, utilisable une seule fois (solution juste ou fausse), vérifié sans appel extérieur. Le plafond quotidien, les limites par visiteur et la session signée ne changent pas. Une preuve de travail freine un robot sans l'arrêter : les vrais plafonds restent le compteur quotidien et l'instance unique.

## Écarts avec l'étude du 24/11/2026 (`projets/mission6-etude-rag-optimal.md`)

1. **Intention en sortie structurée JSON** (`{"intention", "reponse"}`), comme le demande la mission du 24/11/2026, au lieu de l'étiquette texte en tête de flux que l'étude recommandait (6c ; elle écartait 6d pour le code de recollage). Le recollage est écrit et testé (`etiquette.py` : fragments partiels, échappements, intention lue avant le premier mot) ; quatre intentions (contact, personnel, question, hors sujet) ; une sortie illisible donne le refus écrit d'avance. Le schéma passé à Gemini (`propertyOrdering`) est à vérifier au premier appel.
2. **Non codés** : reclasseur Ranking API (5b) et Model Armor (10d), conditionnels dans l'étude ; aucune clé réservée.
3. **Page Contact toujours ingérée** (type `contact`) et servie seulement si `corpus.inclure_contact` est vrai : un seul fichier de corpus sert les deux variantes. Le contrôle bloquant des coordonnées (1d) porte aussi sur elle : une coordonnée qui passerait le filtre arrêterait l'ingestion, donc le déploiement. Le motif du téléphone retire désormais un numéro suivi d'une virgule (il le laissait passer).
4. **Consigne v2 et étiquette** valent pour B et C ; A garde `prompts/agent.md`.
5. **Corpus compact (1c)** : une fiche « métrique » dont tous les chiffres sont déjà dans la prose de sa page est retirée (356 morceaux du corpus MDX local deviennent 270). La phrase de méthode de ces fiches disparaît : c'est le risque que l'étude nomme, mesuré à l'étape 2.6.
6. **`assistant-noter` et `assistant-accord`** acceptent plusieurs passages et un échantillon stratifié (étape 0.3), et exigent le kappa de Cohen d'au moins 0,7 en plus des 90 % d'accord (fiche 3.11).
