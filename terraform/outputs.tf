output "eks_cluster_name" {
  value = module.eks.cluster_name
}

output "eks_cluster_endpoint" {
  value     = module.eks.cluster_endpoint
  sensitive = true
}

output "rds_endpoint" {
  value     = aws_db_instance.wallet_db.endpoint
  sensitive = true
}

output "rds_port" {
  value = aws_db_instance.wallet_db.port
}

output "s3_backup_bucket" {
  value = aws_s3_bucket.backups.bucket
}

output "sns_alerts_arn" {
  value = aws_sns_topic.alerts.arn
}

output "configure_kubectl" {
  value = "aws eks update-kubeconfig --region ${var.aws_region} --name ${var.eks_cluster_name}"
}
