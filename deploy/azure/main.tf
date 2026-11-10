terraform {
  required_version = ">= 1.6"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "azurerm" {
  subscription_id = var.subscription_id

  features {}
}

resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

resource "random_password" "db" {
  length  = 24
  special = false
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${var.name}"
  location = var.location
}

resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${var.name}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
}

resource "azurerm_postgresql_flexible_server" "main" {
  name                          = "psql-${var.name}-${random_string.suffix.result}"
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  version                       = "16"
  sku_name                      = "B_Standard_B1ms"
  storage_mb                    = 32768
  backup_retention_days         = 7
  administrator_login           = "dockhand"
  administrator_password        = random_password.db.result
  public_network_access_enabled = true

  lifecycle {
    ignore_changes = [zone]
  }
}

resource "azurerm_postgresql_flexible_server_firewall_rule" "azure" {
  name             = "allow-azure-services"
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

resource "azurerm_postgresql_flexible_server_database" "main" {
  name      = "dockhand"
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

resource "azurerm_container_app_environment" "main" {
  name                       = "cae-${var.name}"
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
}

resource "azurerm_container_app" "main" {
  name                         = "ca-${var.name}"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"

  secret {
    name  = "database-url"
    value = "postgresql://dockhand:${random_password.db.result}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/dockhand?sslmode=require"
  }

  ingress {
    external_enabled = true
    target_port      = 8000

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    min_replicas = 1
    max_replicas = 1

    container {
      name    = "dockhand"
      image   = var.image
      cpu     = 0.5
      memory  = "1Gi"
      command = ["dockhand", "serve", "--demo"]

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }

      liveness_probe {
        transport        = "HTTP"
        port             = 8000
        path             = "/api/health/live"
        initial_delay    = 10
        interval_seconds = 10
      }

      readiness_probe {
        transport        = "HTTP"
        port             = 8000
        path             = "/api/health/ready"
        interval_seconds = 10
      }
    }
  }

  depends_on = [
    azurerm_postgresql_flexible_server_database.main,
    azurerm_postgresql_flexible_server_firewall_rule.azure,
  ]
}

output "url" {
  value = "https://${azurerm_container_app.main.ingress[0].fqdn}"
}
