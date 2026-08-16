resource "google_compute_network" "gemma" {
  name                    = var.network_name
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "gemma" {
  name                     = var.subnet_name
  region                   = var.region
  network                  = google_compute_network.gemma.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true
}
