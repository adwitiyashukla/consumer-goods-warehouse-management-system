variable "subscription_id" {
  type = string
}

variable "location" {
  type    = string
  default = "eastus2"
}

variable "name" {
  type    = string
  default = "wms"
}

variable "image" {
  type    = string
  default = "ghcr.io/adwitiyashukla/consumer-goods-warehouse-management-system:latest"
}
