# Workload Identity Federation pour GitHub Actions : aucune cle de compte de service.
# Seuls le depot nomme et sa branche main peuvent emprunter le compte de deploiement.

variable "projet" {
  type = string
}

variable "depot_github" {
  description = "proprietaire/depot, par exemple NoanOdg/assistant-portfolio"
  type        = string
}

variable "id_proprietaire_github" {
  description = "Identifiant numerique du proprietaire GitHub (resiste a un renommage du compte)."
  type        = string
}

variable "branche" {
  type    = string
  default = "refs/heads/main"
}

variable "compte_deploiement" {
  description = "Nom complet (projects/.../serviceAccounts/...) du compte de deploiement."
  type        = string
}

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.projet
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.projet
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"                = "assertion.sub"
    "attribute.repository"          = "assertion.repository"
    "attribute.repository_owner_id" = "assertion.repository_owner_id"
    "attribute.ref"                 = "assertion.ref"
  }

  attribute_condition = join(" && ", [
    "assertion.repository == '${var.depot_github}'",
    "assertion.repository_owner_id == '${var.id_proprietaire_github}'",
    "assertion.ref == '${var.branche}'",
  ])

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_emprunte_deploiement" {
  service_account_id = var.compte_deploiement
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.depot_github}"
}

output "fournisseur" {
  description = "A recopier dans le secret GitHub GCP_WIF_FOURNISSEUR."
  value       = google_iam_workload_identity_pool_provider.github.name
}
