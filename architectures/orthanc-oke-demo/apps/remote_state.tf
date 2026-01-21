############################################################
# Remote state: read outputs from ../infra
############################################################

data "terraform_remote_state" "infra" {
  backend = "local"
  config = {
    path = "../infra/terraform.tfstate"
  }
}
