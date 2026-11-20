# Etape 2 : le meme conteneur sur GKE Autopilot, le temps d'une mesure, puis destroy.
# Desactive par defaut (activer_gke = false dans l'environnement).

variable "projet" {
  type = string
}

variable "region" {
  type = string
}

variable "nom" {
  type    = string
  default = "assistant-mesure"
}

variable "compte_execution_nom" {
  description = "Nom complet du compte d'execution, emprunte par le compte Kubernetes."
  type        = string
}

variable "compte_deploiement" {
  description = "Courriel du compte de deploiement, qui lance helm upgrade."
  type        = string
}

variable "espace" {
  type    = string
  default = "assistant"
}

variable "compte_kubernetes" {
  type    = string
  default = "assistant"
}

variable "depot_images" {
  description = "Nom complet du depot Artifact Registry (projects/.../repositories/...)."
  type        = string
}

resource "google_service_account" "noeuds" {
  project      = var.projet
  account_id   = "assistant-noeuds-gke"
  display_name = "Noeuds GKE de la mesure"
}

resource "google_project_iam_member" "noeuds" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/stackdriver.resourceMetadata.writer",
  ])
  project = var.projet
  role    = each.value
  member  = "serviceAccount:${google_service_account.noeuds.email}"
}

resource "google_artifact_registry_repository_iam_member" "noeuds_lisent_les_images" {
  project    = var.projet
  location   = var.region
  repository = var.depot_images
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.noeuds.email}"
}

resource "google_container_cluster" "mesure" {
  project             = var.projet
  name                = var.nom
  location            = var.region
  enable_autopilot    = true
  deletion_protection = false # l'etape se termine par terraform destroy

  release_channel {
    channel = "REGULAR"
  }

  cluster_autoscaling {
    auto_provisioning_defaults {
      service_account = google_service_account.noeuds.email
      oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    }
  }
}

# helm upgrade depuis la CI. GKE n'a pas d'IAM par cluster : role au niveau du
# projet, retire avec le module au destroy.
resource "google_project_iam_member" "deploiement_helm" {
  project = var.projet
  role    = "roles/container.developer"
  member  = "serviceAccount:${var.compte_deploiement}"
}

# Workload Identity pour GKE : le compte Kubernetes emprunte le compte d'execution.
resource "google_service_account_iam_member" "workload_identity" {
  service_account_id = var.compte_execution_nom
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.projet}.svc.id.goog[${var.espace}/${var.compte_kubernetes}]"
  depends_on         = [google_container_cluster.mesure]
}

output "cluster" {
  value = google_container_cluster.mesure.name
}
