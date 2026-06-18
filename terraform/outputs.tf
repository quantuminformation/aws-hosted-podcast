locals {
  feed_hostname = (
    var.enable_cloudfront
    ? coalesce(var.cloudfront_alias, aws_cloudfront_distribution.podcast[0].domain_name)
    : aws_s3_bucket.podcast.bucket_regional_domain_name
  )
}

output "bucket_name" {
  value = aws_s3_bucket.podcast.bucket
}

output "feed_url" {
  value = "https://${local.feed_hostname}/feed.xml"
}

output "cloudfront_distribution_domain_name" {
  value = var.enable_cloudfront ? aws_cloudfront_distribution.podcast[0].domain_name : null
}

output "github_deploy_role_arn" {
  value = var.create_github_deploy_role ? aws_iam_role.github_deploy[0].arn : null
}
