variable "subscription_id" {
  type = string
}

variable "location" {
  type    = string
  default = "eastus2"
}

variable "name" {
  type    = string
  default = "dockhand"
}

variable "image" {
  type    = string
  default = "ghcr.io/adwitiyashukla/dockhand:latest"
}
