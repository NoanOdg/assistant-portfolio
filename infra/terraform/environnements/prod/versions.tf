terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.10, < 8.0"
    }
  }

  # Etat dans un bucket GCS versionne, cree a la main avant le premier init :
  #   terraform init -backend-config=backend.hcl
  # (voir backend.hcl.example). Aucun identifiant ici.
  backend "gcs" {}
}

provider "google" {
  project = var.projet
  region  = var.region
  # Le budget passe par l'API de facturation : quota impute a ce projet.
  billing_project       = var.projet
  user_project_override = true
}
