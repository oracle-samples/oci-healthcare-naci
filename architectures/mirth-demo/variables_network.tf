# --- VCN Configuration ---
variable "vcn_name" {
  type        = string
  default     = "vcn-oke"
  description = "The name of the Virtual Cloud Network."
}

variable "vcn_cidrs" {
  type        = list(string)
  default     = ["10.0.0.0/16"]
  description = "The CIDR block for the VCN. Must be a JSON array in the UI, e.g., [\"10.0.0.0/16\"]"
}

# --- Subnet Configuration ---
variable "api_subnet_name" {
  type        = string
  default     = "apisub"
  description = "Name for the OKE API endpoint subnet."
}

variable "api_subnet_cidr" {
  type        = string
  default     = "10.0.0.0/30"
  description = "CIDR for the OKE API endpoint subnet."
}

variable "workers_subnet_name" {
  type        = string
  default     = "sub-workers"
  description = "Name for the OKE worker nodes subnet."
}

variable "workers_subnet_cidr" {
  type        = string
  default     = "10.0.1.0/24"
  description = "CIDR for the OKE worker nodes subnet."
}

variable "services_subnet_name" {
  type        = string
  default     = "sub-services"
  description = "Name for the OKE services subnet."
}

variable "services_subnet_cidr" {
  type        = string
  default     = "10.0.2.0/24"
  description = "CIDR for the OKE services subnet."
}

variable "pods_subnet_name" {
  type        = string
  default     = "sub-pods"
  description = "Name for the OKE pods subnet (for Native CNI)."
}

variable "pods_subnet_cidr" {
  type        = string
  default     = "10.0.32.0/19"
  description = "CIDR for the OKE pods subnet."
}

variable "mgmt_subnet_name" {
  type        = string
  default     = "mgmt-subnet"
  description = "Name for the management subnet."
}

variable "mgmt_subnet_cidr" {
  type        = string
  default     = "10.0.3.0/28"
  description = "CIDR for the management subnet."
}