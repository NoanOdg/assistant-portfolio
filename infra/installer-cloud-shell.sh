#!/usr/bin/env bash
# Premier deploiement de l'infrastructure, a lancer dans Cloud Shell.
# Usage : bash infra/installer-cloud-shell.sh ID_PROJET ID_COMPTE_FACTURATION
# Relancable sans risque : chaque etape verifie ce qui existe deja.
set -euo pipefail

PROJET="${1:?Usage : bash infra/installer-cloud-shell.sh ID_PROJET ID_COMPTE_FACTURATION}"
FACTURATION="${2:?Usage : bash infra/installer-cloud-shell.sh ID_PROJET ID_COMPTE_FACTURATION}"
REGION="europe-west1"
DEPOT_GITHUB="NoanOdg/assistant-portfolio"
ID_GITHUB="116545649"
TF_VERSION="1.9.8"

etape() { printf '\n=== %s\n' "$1"; }
RACINE="$(cd "$(dirname "$0")/.." && pwd)"
PROD="$RACINE/infra/terraform/environnements/prod"

etape "1/8 Terraform ${TF_VERSION} ou plus"
if ! terraform version -json 2>/dev/null | python3 -c "import sys,json;v=json.load(sys.stdin)['terraform_version'].split('.');sys.exit(0 if (int(v[0]),int(v[1]))>=(1,9) else 1)"; then
  mkdir -p "$HOME/bin"
  curl -fsSLo /tmp/tf.zip "https://releases.hashicorp.com/terraform/${TF_VERSION}/terraform_${TF_VERSION}_linux_amd64.zip"
  unzip -o -q /tmp/tf.zip -d "$HOME/bin"
  export PATH="$HOME/bin:$PATH"
fi
terraform version | head -1

etape "2/8 Projet et API de base"
gcloud config set project "$PROJET" >/dev/null
gcloud services enable serviceusage.googleapis.com cloudresourcemanager.googleapis.com \
  cloudbilling.googleapis.com billingbudgets.googleapis.com secretmanager.googleapis.com
NUMERO="$(gcloud projects describe "$PROJET" --format='value(projectNumber)')"
LIE="$(gcloud billing projects describe "$PROJET" --format='value(billingEnabled)')"
[ "$LIE" = "True" ] || { echo "La facturation n'est pas activee sur $PROJET : la lier au compte $FACTURATION dans la console, puis relancer."; exit 1; }
DEVISE="$(gcloud billing accounts describe "$FACTURATION" --format='value(currencyCode)')"
echo "Projet $PROJET (numero $NUMERO), facturation en $DEVISE"

etape "3/8 Bucket d'etat Terraform"
BUCKET="${PROJET}-tfstate"
if ! gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access
fi
gcloud storage buckets update "gs://$BUCKET" --versioning >/dev/null
echo "gs://$BUCKET versionne"

etape "4/8 Fichiers de variables (hors git)"
cat > "$PROD/backend.hcl" <<EOF
bucket = "$BUCKET"
prefix = "prod"
EOF
cat > "$PROD/terraform.tfvars" <<EOF
projet                 = "$PROJET"
numero_projet          = "$NUMERO"
compte_facturation     = "$FACTURATION"
depot_github           = "$DEPOT_GITHUB"
id_proprietaire_github = "$ID_GITHUB"
region                 = "$REGION"
budget_mensuel         = 5
devise                 = "$DEVISE"
activer_gke            = false
EOF
cd "$PROD"
terraform init -input=false -backend-config=backend.hcl

etape "5/8 API, comptes de service et coffres a secrets"
terraform apply -target=module.apis -target=module.comptes -target=module.secrets

etape "6/8 Valeurs des secrets"
a_une_version() { [ -n "$(gcloud secrets versions list "$1" --limit=1 --format='value(name)' 2>/dev/null)" ]; }
if a_une_version assistant-cle-session; then echo "cle de session : deja presente"; else
  openssl rand -hex 32 | tr -d '\n' | gcloud secrets versions add assistant-cle-session --data-file=- >/dev/null
  echo "cle de session : creee"
fi
if a_une_version assistant-cle-altcha; then echo "cle ALTCHA : deja presente"; else
  openssl rand -hex 32 | tr -d '\n' | gcloud secrets versions add assistant-cle-altcha --data-file=- >/dev/null
  echo "cle ALTCHA : creee"
fi

etape "7/8 Reste de l'infrastructure (Firestore, Cloud Run, WIF, budget, journaux)"
terraform apply

etape "8/8 Sorties a me transmettre"
terraform output
echo
echo "Termine. Copie les quatre lignes ci-dessus dans la conversation (aucune n'est un secret)."
