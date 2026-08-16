data "google_project" "cardinal_env_control_project" {
  project_id = var.project_id
}

locals {
  cardinal_default_compute_service_account = "${data.google_project.cardinal_env_control_project.number}-compute@developer.gserviceaccount.com"
  cardinal_github_deployer_service_account = "github-cardinal-deployer@${var.project_id}.iam.gserviceaccount.com"
}

# Cloud Run validates iam.serviceAccounts.actAs on the service's runtime
# service account when GitHub changes service-level scaling.
#
# The current frontend service uses the project's default Compute Engine
# service account, so the GitHub deployer needs ONLY Service Account User
# on that specific service account. This is not a project-wide actAs grant.
resource "google_service_account_iam_member" "github_environment_control_act_as_default_compute" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${local.cardinal_default_compute_service_account}"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${local.cardinal_github_deployer_service_account}"
}
