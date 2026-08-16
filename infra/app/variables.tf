variable "project_id" {
  type = string
}

variable "project_number" {
  type = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "frontend_service_name" {
  type    = string
  default = "kg-dashboard-frontend"
}

variable "backend_service_name" {
  type    = string
  default = "kg-dashboard-backend"
}

variable "model_service_name" {
  type    = string
  default = "cardinal-gemma4-26b-a4b-it"
}

variable "artifact_repository" {
  type    = string
  default = "cardinal-app"
}

variable "frontend_image" {
  type = string
}

variable "backend_image" {
  type = string
}

variable "model_image" {
  type    = string
  default = "ghcr.io/ggml-org/llama.cpp:server"
}

variable "model_bucket_name" {
  type = string
}

variable "model_gcs_prefix" {
  type    = string
  default = "gemma4-26b-a4b-q4"
}

variable "model_filename" {
  type    = string
  default = "gemma-4-26B_q4_0-it.gguf"
}

variable "backend_runtime_service_account" {
  type = string
}

variable "model_runtime_service_account" {
  type = string
}

variable "github_deployer_service_account" {
  type = string
}

variable "alert_email" {
  type    = string
  default = "aditya.bagayatkar09@gmail.com"
}

variable "network_name" {
  type    = string
  default = "cardinal-gemma-net"
}

variable "subnet_name" {
  type    = string
  default = "cardinal-gemma-subnet"
}

variable "subnet_cidr" {
  type    = string
  default = "10.42.0.0/24"
}
