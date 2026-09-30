variable "tenancy_ocid" {
  description = "Tenancy OCID. Resource Manager populates this automatically."
  type        = string
  validation {
    condition     = can(regex("^ocid1\\.tenancy\\.[A-Za-z0-9._-]+$", var.tenancy_ocid))
    error_message = "Provide a tenancy OCID."
  }
}

variable "compartment_ocid" {
  description = "Compartment for the VM and network."
  type        = string
  validation {
    condition     = can(regex("^ocid1\\.(compartment|tenancy)\\.[A-Za-z0-9._-]+$", var.compartment_ocid))
    error_message = "Provide a compartment OCID (or tenancy OCID for the root compartment)."
  }
}

variable "region" {
  description = "OCI region for the VM and network."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]+[0-9]$", var.region))
    error_message = "Use an OCI region identifier, for example us-chicago-1."
  }
}

variable "genai_compartment_ocid" {
  description = "Compartment used for Generative AI inference and billing; blank uses the deployment compartment."
  type        = string
  default     = ""
  validation {
    condition     = var.genai_compartment_ocid == "" || can(regex("^ocid1\\.(compartment|tenancy)\\.[A-Za-z0-9._-]+$", var.genai_compartment_ocid))
    error_message = "Use a compartment OCID, a tenancy OCID, or leave blank."
  }
}

variable "genai_region" {
  description = "Region offering both selected on-demand models; blank uses the deployment region."
  type        = string
  default     = ""
  validation {
    condition     = var.genai_region == "" || can(regex("^[a-z][a-z0-9-]+[0-9]$", var.genai_region))
    error_message = "Use an OCI region identifier or leave blank."
  }
}

variable "basic_model" {
  description = "Bare OCI on-demand model ID for the basic route (without the oci/ prefix). Verify regional availability."
  type        = string
  default     = "google.gemini-2.5-flash-lite"
  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$", var.basic_model))
    error_message = "Use a model ID containing only letters, numbers, dots, underscores, colons, slashes and hyphens."
  }
}

variable "reasoning_model" {
  description = "Bare OCI on-demand frontier/reasoning model ID for the reasoning route. Verify regional availability."
  type        = string
  default     = "xai.grok-4.3"
  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$", var.reasoning_model))
    error_message = "Use a model ID containing only letters, numbers, dots, underscores, colons, slashes and hyphens."
  }
}

variable "ssh_public_key" {
  description = "SSH public key for the ubuntu user. Never provide the private key."
  type        = string
  validation {
    condition     = can(regex("^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)) [A-Za-z0-9+/=]+", trimspace(var.ssh_public_key)))
    error_message = "Provide an OpenSSH public key (ed25519, RSA or ECDSA)."
  }
}

variable "ssh_allowed_cidr" {
  description = "Trusted IPv4 source CIDR for SSH, normally your public IP with /32. Prefix must be /24 or narrower."
  type        = string
  validation {
    condition = try(
      can(cidrnetmask(var.ssh_allowed_cidr)) &&
      tonumber(split("/", var.ssh_allowed_cidr)[1]) >= 24,
      false
    )
    error_message = "Provide a valid IPv4 CIDR with prefix /24 to /32; SSH is never open to the internet."
  }
}

variable "display_name" {
  description = "Prefix for the resources created by this stack."
  type        = string
  default     = "oci-genai-litellm"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.display_name))
    error_message = "Use 3-31 lowercase letters, digits or hyphens, starting with a letter."
  }
}

variable "availability_domain" {
  description = "Availability domain name; blank selects the first AD in the region. Change if capacity is unavailable."
  type        = string
  default     = ""
}

variable "instance_shape" {
  description = "AMD x86 flexible shape. ARM shapes are intentionally excluded from this reference."
  type        = string
  default     = "VM.Standard.E5.Flex"
  validation {
    condition     = contains(["VM.Standard.E4.Flex", "VM.Standard.E5.Flex", "VM.Standard.E6.Flex"], var.instance_shape)
    error_message = "Choose an AMD E4, E5 or E6 flexible shape."
  }
}

variable "instance_ocpus" {
  description = "VM OCPUs. One is sufficient for a small reference deployment."
  type        = number
  default     = 1
  validation {
    condition     = var.instance_ocpus >= 1 && var.instance_ocpus <= 8 && floor(var.instance_ocpus) == var.instance_ocpus
    error_message = "Use an integer from 1 to 8 OCPUs."
  }
}

variable "instance_memory_gbs" {
  description = "VM RAM in GB; the Presidio NLP model and image build need at least 16 GB."
  type        = number
  default     = 16
  validation {
    condition     = var.instance_memory_gbs >= 16 && var.instance_memory_gbs <= 64
    error_message = "Use 16 to 64 GB of RAM."
  }
}

variable "boot_volume_gbs" {
  description = "Boot volume size in GB, including container images."
  type        = number
  default     = 50
  validation {
    condition     = var.boot_volume_gbs >= 50 && var.boot_volume_gbs <= 200
    error_message = "Use 50 to 200 GB."
  }
}

variable "image_ocid" {
  description = "Optional Ubuntu 24.04 AMD64 image OCID. Blank discovers the newest compatible platform image."
  type        = string
  default     = ""
  validation {
    condition     = var.image_ocid == "" || can(regex("^ocid1\\.image\\.[A-Za-z0-9._-]+$", var.image_ocid))
    error_message = "Provide an Ubuntu 24.04 AMD64 image OCID or leave blank."
  }
}
