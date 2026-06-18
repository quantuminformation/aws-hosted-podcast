variable "aws_region" {
  type        = string
  description = "AWS region for S3 and IAM resources."
  default     = "us-east-1"
}

variable "podcast_bucket_name" {
  type        = string
  description = "Podcast asset bucket name. Use the existing bucket name when importing the live stack."
  default     = null
}

variable "enable_cloudfront" {
  type        = bool
  description = "Create a CloudFront distribution in front of the podcast bucket."
  default     = false
}

variable "cloudfront_alias" {
  type        = string
  description = "Optional custom CloudFront hostname. Requires acm_certificate_arn in us-east-1."
  default     = null
}

variable "acm_certificate_arn" {
  type        = string
  description = "Optional ACM certificate ARN for cloudfront_alias."
  default     = null
}

variable "cloudfront_price_class" {
  type        = string
  description = "CloudFront price class."
  default     = "PriceClass_100"
}

variable "create_github_deploy_role" {
  type        = bool
  description = "Create the GitHub Actions OIDC deploy role."
  default     = true
}

variable "github_oidc_provider_arn" {
  type        = string
  description = "Existing GitHub OIDC provider ARN. Defaults to the provider in the current account."
  default     = null
}

variable "github_owner" {
  type        = string
  description = "GitHub repository owner allowed to assume the deploy role."
  default     = "quantuminformation"
}

variable "github_repo" {
  type        = string
  description = "GitHub repository name allowed to assume the deploy role."
  default     = "aws-hosted-podcast"
}

variable "github_branch" {
  type        = string
  description = "GitHub branch allowed to assume the deploy role."
  default     = "main"
}

variable "github_deploy_role_name" {
  type        = string
  description = "GitHub Actions deploy role name."
  default     = "aws-hosted-podcast-github-deploy"
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to supported resources."
  default     = {}
}
