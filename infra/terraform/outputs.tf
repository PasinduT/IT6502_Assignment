output "resource_group_name" {
  value = azurerm_resource_group.main.name
}

output "container_app_name" {
  value = azurerm_container_app.api.name
}

output "static_web_app_name" {
  value = azurerm_static_web_app.frontend.name
}

output "search_endpoint" {
  value = "https://${azurerm_search_service.main.name}.search.windows.net"
}

output "frontend_url" {
  value = "https://${azurerm_static_web_app.frontend.default_host_name}"
}

output "backend_url" {
  value = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "storage_account_name" {
  value = azurerm_storage_account.main.name
}

output "guide_media_storage_account_name" {
  value = azurerm_storage_account.guide_media.name
}

output "guide_media_container_name" {
  value = azurerm_storage_container.guide_images.name
}

output "guide_media_base_url" {
  value = "${trimsuffix(azurerm_storage_account.guide_media.primary_blob_endpoint, "/")}/${azurerm_storage_container.guide_images.name}"
}

output "search_service_name" {
  value = azurerm_search_service.main.name
}

output "static_web_app_api_key" {
  value     = azurerm_static_web_app.frontend.api_key
  sensitive = true
}
