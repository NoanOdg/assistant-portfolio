output "url_service" {
  value = module.service.url
}

output "depot_images" {
  value = module.depot.url
}

output "compte_deploiement" {
  description = "A recopier dans le secret GitHub GCP_COMPTE_DEPLOIEMENT."
  value       = module.comptes.deploiement_email
}

output "fournisseur_wif" {
  description = "A recopier dans le secret GitHub GCP_WIF_FOURNISSEUR."
  value       = module.wif.fournisseur
}

output "cluster_gke" {
  value = var.activer_gke ? module.gke[0].cluster : null
}
