# Assistant du portfolio

Assistant public de noan-ouedraogo.dev (mission 6) : on lui pose une question sur le parcours et les projets de Noan, en français ou en anglais ; il cherche dans le contenu public du site, cite ses sources et refuse quand il ne sait pas.

Cible : Cloud Run (FastAPI, réponses en flux SSE), Vertex AI (Gemini et embeddings), recherche vectorielle Firestore, Terraform, GitHub Actions sans clé (Workload Identity Federation). Étape 2 : le même conteneur sur GKE Autopilot avec Helm.

**État au 19/11/2026 : squelette local.** Aucune ressource Google Cloud n'existe, aucun appel au cloud n'a été fait, aucune clé n'est stockée. Rien n'est poussé.

## Ce qui existe et tourne en local

| Dossier | Contenu |
|---|---|
| `src/assistant/corpus/` | Ingestion relançable : MDX du portfolio + `faits.yml` + `identite.yml` (source `mdx`), ou site publié lu par son plan (source `site`). Chiffres des composants `<Metrique>` résolus sur liste blanche (jamais `source` ni `verification`). Découpage par section h2/h3, en-tête de contexte, ancre conservée. Garde : plus de 30 % de morceaux disparus, rien n'est écrit. |
| `src/assistant/embeddings.py`, `generation.py`, `recherche.py` | Interfaces, faux déterministes pour les tests, implémentations Vertex AI et Firestore. Recherche en mémoire et branche « tout le site dans la consigne ». |
| `src/assistant/pipeline.py` | Chemin fixé par le code : aiguillage (données personnelles, contact), recherche, refus sous le seuil sans appeler le modèle, rédaction en flux, refus du modèle (`[REFUS]`) remplacé par un texte écrit d'avance, sources citées. |
| `src/assistant/filtres.py` | Filtres en flux : chiffre absent des passages remplacé, liens hors liste blanche retirés, courriels, images, HTML, tirets longs. |
| `src/assistant/api.py` | `POST /session`, `POST /question` (SSE), `GET /sante`. Limites : 500 caractères, 450 jetons, 30 s, 5 questions par minute et 30 par jour par visiteur, 60 par jour au total. Journaux JSON sur liste blanche : ni IP, ni navigateur, ni texte de question. |
| `src/assistant/evaluation/` | Jeu de 40 questions (FR et EN, pièges compris), réserve scellée de 10, juge à consigne gelée par empreinte, mesures en taux absolus avec intervalle de Wilson, porte de la CI, calage du seuil, scellement. |
| `evaluation/` | `jeu/questions.yaml`, `jeu/reserve-scellee.yaml`, `jeu/sceau.json`, `juge/consigne.md`, `seuils.toml`. |
| `infra/terraform/` | Modules : API, Artifact Registry, Secret Manager, Firestore (index vectoriels, TTL), comptes de service, Workload Identity Federation, Cloud Run, budget, exclusion des journaux, GKE. Environnement `prod`. |
| `infra/helm/assistant/` | Chart de l'étape 2 : Deployment, Service LoadBalancer, HPA, PDB, sondes, Workload Identity. |
| `.github/workflows/` | `ci.yml` (lint, tests, évaluation hors ligne, Terraform, Helm), `deploiement.yml` (révision candidate sans trafic, évaluation, porte, bascule), `gke.yml` (manuel). |

Vérifié le 19/11/2026 sur le PC : 70 tests verts, ruff sans remarque, `terraform fmt -check` et `terraform validate` verts (image Docker officielle, fournisseur google 7.46.2, sans backend), `helm lint` vert, image Docker construite et lancée en mode local.

## Ce qui est écrit mais jamais exécuté

- `VertexEmbedder`, `VertexGenerateur`, `JugeVertex` (google-genai), `RechercheFirestore`, `MagasinFirestore`, `CompteurFirestore`, `JournalFirestore`, `Turnstile`, `IdentiteGoogle`. Les noms de paramètres du SDK (niveau de réflexion de Gemini 3 en particulier) sont à vérifier au premier appel.
- `SourceSite` : testée sur des pages factices, jamais sur le site en ligne.
- Les workflows GitHub, `terraform plan` et `apply`, `helm install`.
- Les identifiants de modèles (`gemini-3.1-flash-lite`, `gemini-3.8-flash`, `gemini-embedding-001`) et les tarifs viennent de la conception : à confirmer dans la console au jour 1.

## Ce qui manque encore

- Seuil de refus Vertex : non calé (`-1`, le service refuse de démarrer tant qu'il ne l'est pas).
- Rédacteur simulé aux délais réalistes pour le test de charge de l'étape 2, et le harnais de charge lui-même.
- Traces OpenTelemetry, tableau de bord et alertes Cloud Monitoring.
- Page `/assistant` du portfolio, script du widget, décision 24 (politique de sécurité), mention RGPD.
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

Les taux de l'évaluation locale ne mesurent rien de la qualité réelle : le faux rédacteur recopie la première phrase du meilleur passage. Ils prouvent que le harnais tourne et que les contrôles faits par le code tiennent.

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
   openssl rand -hex 32 | gcloud secrets versions add assistant-cle-session --data-file=-
   printf '%s' 'SECRET_TURNSTILE' | gcloud secrets versions add assistant-turnstile --data-file=-
   terraform apply
   ```
   Le secret Turnstile vient du widget créé dans le tableau de bord Cloudflare (domaine noan-ouedraogo.dev).
6. **Budget.** Vérifier que la devise du compte de facturation est l'euro (sinon changer `devise`), et que l'alerte arrive par courriel.
7. **Quota et cache Vertex.** Abaisser le quota de requêtes Gemini du projet dans la console ; vérifier la mise en cache de 24 h et la désactiver si elle s'applique. Vérifier que les modèles existent en europe-west1 et leurs tarifs.
8. **Valider le jeu d'évaluation.** Relire les 40 questions et la réserve (brouillons écrits d'après le portfolio du 19/11, à revoir après la réécriture du contenu), puis `uv run assistant-sceller --motif "validation par Noan"`.
9. **Caler le seuil Vertex** depuis le PC, une fois le corpus indexé (`assistant-indexer`, moins de 0,02 $) : `ASSISTANT_MOTEUR_MODE=vertex ASSISTANT_MOTEUR_PROJET_GCP=PROJET uv run assistant-caler-seuil`, recopier la valeur dans `seuil_distance` des tfvars, `terraform apply`.
10. **Dépôt GitHub** (privé ou public, à trancher) : créer le dépôt, pousser, puis dans les réglages :
    - variable `GCP_PROJET` ;
    - secrets `GCP_WIF_FOURNISSEUR` et `GCP_COMPTE_DEPLOIEMENT` (sorties `fournisseur_wif` et `compte_deploiement` de Terraform) ;
    - environnement `production` avec Noan en relecteur obligatoire ;
    - recommandé : épingler les actions par empreinte de commit plutôt que par étiquette.
11. **Portfolio.** Page `/assistant`, décision 24, et un `repository_dispatch` (type `portfolio-publie`) envoyé vers ce dépôt à chaque déploiement du site, avec un jeton GitHub à droits limités.
12. **Étape 2.** `activer_gke = true`, `terraform apply`, `kubectl create secret generic assistant-secrets -n assistant --from-literal=session=... --from-literal=turnstile=...`, workflow `gke.yml`, mesures, puis `activer_gke = false`, `terraform apply` et vérification qu'il ne reste ni disque, ni adresse, ni règle de transfert.

## Écarts avec la conception (`projets/mission6-conception.md`)

1. **Pas de boucle d'agent ni de LangGraph** (révision du 19/11) : chemin fixé, un seul appel au modèle par question. L'appel d'outils n'est pas gratuit : la conception l'estimait à 2,3 appels par question. La comparaison garde deux branches : chemin fixé contre tout le site dans la consigne.
2. **Corpus local** : les MDX, avec les chiffres résolus depuis `faits.yml`. Pas besoin d'exporter `faits.json` pour l'instant. Les ancres imitent github-slugger : à vérifier contre le site rendu. La source `site` reste celle de la CI.
3. **La version du corpus est inscrite dans l'image** : une révision Cloud Run sert un corpus fixe. On revient en arrière en reroutant le trafic, sans document `config/corpus_actif`.
4. **L'évaluation de la CI passe la porte avec un jeton d'identité Google** du compte de déploiement, et a son propre plafond (150 par jour). La conception ne disait pas comment elle franchissait Turnstile. Elle ne disait pas non plus que 40 questions par passage mangeraient les deux tiers du plafond public de 60.
5. **Terraform est appliqué depuis le PC, jamais par la CI.** Un `apply` depuis Actions demanderait un compte quasi propriétaire, ce qui contredit le moindre privilège.
6. **Exclusion des journaux** : elle retire tout le journal des requêtes Cloud Run du service, statut et latence compris. Les métriques intégrées restent.
7. **GKE** : avec 2 réplicas, le limiteur par visiteur en mémoire compte par pod. Le plafond global, dans Firestore, reste exact.
8. **Historique de conversation** non géré : une question à la fois, sans réécriture des questions de suite.
