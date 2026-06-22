aws_region         = "ap-south-1"
environment        = "production"
project_name       = "payvault"
eks_cluster_name   = "wallet-eks-cluster"
vpc_cidr           = "10.0.0.0/16"
alert_email        = "devops@payvault.com"
rds_instance_class = "db.t3.micro"
# rds_password → set via: export TF_VAR_rds_password=$(vault kv get -field=password secret/wallet/rds)
