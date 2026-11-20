# Assistant du portfolio, environnement unique. Applique depuis le poste de Noan
# (gcloud auth application-default login), jamais par la CI : la CI n'a que les
# droits de deploiement.

module "apis" {
  source      = "../../modules/apis"
  projet      = var.projet
  activer_gke = var.activer_gke
}

module "comptes" {
  source     = "../../modules/comptes_service"
  projet     = var.projet
  depends_on = [module.apis]
}

module "depot" {
  source     = "../../modules/artifact_registry"
  projet     = var.projet
  region     = var.region
  depends_on = [module.apis]
}

resource "google_artifact_registry_repository_iam_member" "ci_pousse_les_images" {
  project    = var.projet
  location   = var.region
  repository = module.depot.nom
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${module.comptes.deploiement_email}"
}

module "secrets" {
  source     = "../../modules/secrets"
  projet     = var.projet
  region     = var.region
  lecteur    = module.comptes.execution_email
  depends_on = [module.apis]
}

module "firestore" {
  source     = "../../modules/firestore"
  projet     = var.projet
  region     = var.region
  protection = var.protection
  depends_on = [module.apis]
}

module "wif" {
  source                 = "../../modules/wif_github"
  projet                 = var.projet
  depot_github           = var.depot_github
  id_proprietaire_github = var.id_proprietaire_github
  compte_deploiement     = module.comptes.deploiement_nom
  depends_on             = [module.apis]
}

module "service" {
  source             = "../../modules/cloud_run"
  projet             = var.projet
  region             = var.region
  compte_execution   = module.comptes.execution_email
  compte_deploiement = module.comptes.deploiement_email
  compte_evaluation  = module.comptes.deploiement_email
  secret_session     = module.secrets.ids["assistant-cle-session"]
  secret_turnstile   = module.secrets.ids["assistant-turnstile"]
  modele_redaction   = var.modele_redaction
  seuil_distance     = var.seuil_distance
  protection         = var.protection
  # Les valeurs des secrets doivent exister avant la premiere revision (README).
  depends_on = [module.secrets, module.firestore]
}

module "journaux" {
  source  = "../../modules/journaux"
  projet  = var.projet
  service = module.service.nom
}

module "budget" {
  source             = "../../modules/budget"
  compte_facturation = var.compte_facturation
  numero_projet      = var.numero_projet
  montant            = var.budget_mensuel
  devise             = var.devise
  depends_on         = [module.apis]
}

module "gke" {
  count                = var.activer_gke ? 1 : 0
  source               = "../../modules/gke"
  projet               = var.projet
  region               = var.region
  compte_execution_nom = module.comptes.execution_nom
  compte_deploiement   = module.comptes.deploiement_email
  depot_images         = module.depot.nom
  depends_on           = [module.apis]
}
