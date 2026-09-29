output "instance_id" {
  description = "OCI Compute instance OCID."
  value       = oci_core_instance.reference.id
}

output "public_ip" {
  description = "SSH endpoint. Port 4000 is only bound on VM loopback."
  value       = oci_core_instance.reference.public_ip
}

output "ssh_tunnel_command" {
  description = "Add -i /path/to/private/key if your key is not in your SSH agent. Keep this terminal open."
  value       = "ssh -o ExitOnForwardFailure=yes -N -L 4000:127.0.0.1:4000 ubuntu@${oci_core_instance.reference.public_ip}"
}

output "bootstrap_status_command" {
  description = "Wait for bootstrap, then inspect the service. Image builds can take 10-20 minutes."
  value       = "ssh ubuntu@${oci_core_instance.reference.public_ip} 'sudo cloud-init status --wait; sudo systemctl status oci-genai-phi --no-pager'"
}

output "api_key_command" {
  description = "Run locally to retrieve the VM-generated bearer token. The token is absent from Terraform state."
  value       = "ssh ubuntu@${oci_core_instance.reference.public_ip} \"sudo sed -n 's/^GATEWAY_API_KEY=//p' /opt/oci-genai-phi/.env\""
}

output "local_api_url" {
  description = "OpenAI-compatible API base URL after opening the SSH tunnel."
  value       = "http://127.0.0.1:4000/v1"
}

output "model_routes" {
  value = {
    basic     = var.basic_model
    reasoning = var.reasoning_model
    region    = local.genai_region
  }
}
