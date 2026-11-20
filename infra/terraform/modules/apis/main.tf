# Active les API du projet. Rien n'est desactive a la destruction : couper une
# API casserait d'autres ressources du projet.

variable "projet" {
  type = string
}

variable "activer_gke" {
  type    = bool
  default = false
}

locals {
  apis = concat([
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "firestore.googleapis.com",
    "aiplatform.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ], var.activer_gke ? ["container.googleapis.com"] : [])
}

resource "google_project_service" "api" {
  for_each           = toset(local.apis)
  project            = var.projet
  service            = each.value
  disable_on_destroy = false
}

output "actives" {
  value = [for s in google_project_service.api : s.service]
}
