variable "aws_region" {
  type    = string
  default = "ap-south-1"
}

variable "environment" {
  type    = string
  default = "production"
}

variable "project_name" {
  type    = string
  default = "payvault"
}

variable "eks_cluster_name" {
  type    = string
  default = "wallet-eks-cluster"
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "alert_email" {
  type    = string
  default = "devops@payvault.com"
}

variable "rds_instance_class" {
  type    = string
  default = "db.t3.micro"
}

variable "rds_password" {
  type        = string
  sensitive   = true
  description = "RDS master password – inject from Vault or TF Cloud"
}
