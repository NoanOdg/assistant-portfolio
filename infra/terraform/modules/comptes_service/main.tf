# Deux comptes de service, moindre privilege.
#
# execution  : ce que le service fait en tournant (Vertex AI, Firestore, journaux,
#              traces). La lecture des deux secrets est accordee secret par secret
#              dans le module secrets.
# deploiement: ce que la CI fait (pousser une image, deployer une revision,
#              indexer le corpus, jouer l'evaluation). Accessible seulement par
#              Workload Identity Federation (module wif_github), aucune cle.
#              Les droits sur le depot et le service sont poses sur la ressource,
#              pas sur le projet, dans leurs modules.

variable "projet" {
  type = string
}

resource "google_service_account" "execution" {
  project      = var.projet
  account_id   = "assistant-execution"
  display_name = "Assistant du portfolio, execution Cloud Run"
}

resource "google_service_account" "deploiement" {
  project      = var.projet
  account_id   = "assistant-deploiement"
  display_name = "Assistant du portfolio, deploiement GitHub Actions"
}

locals {
  roles_execution = [
    "roles/aiplatform.user",
    "roles/datastore.user",
    "roles/logging.logWriter",
    "roles/cloudtrace.agent",
  ]
  # Indexation du corpus (embeddings + ecriture Firestore) et juge de l'evaluation.
  roles_deploiement = [
    "roles/aiplatform.user",
    "roles/datastore.user",
  ]
}

resource "google_project_iam_member" "execution" {
  for_each = toset(local.roles_execution)
  project  = var.projet
  role     = each.value
  member   = "serviceAccount:${google_service_account.execution.email}"
}

resource "google_project_iam_member" "deploiement" {
  for_each = toset(local.roles_deploiement)
  project  = var.projet
  role     = each.value
  member   = "serviceAccount:${google_service_account.deploiement.email}"
}

# Le deploiement peut attacher le compte d'execution a une revision, et celui-la seulement.
resource "google_service_account_iam_member" "deploiement_utilise_execution" {
  service_account_id = google_service_account.execution.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deploiement.email}"
}

output "execution_email" {
  value = google_service_account.execution.email
}

output "execution_nom" {
  value = google_service_account.execution.name
}

output "deploiement_email" {
  value = google_service_account.deploiement.email
}

output "deploiement_nom" {
  value = google_service_account.deploiement.name
}
