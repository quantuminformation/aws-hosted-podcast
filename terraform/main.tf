terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

locals {
  github_oidc_provider_arn = coalesce(
    var.github_oidc_provider_arn,
    "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"
  )
}

resource "aws_s3_bucket" "podcast" {
  bucket = var.podcast_bucket_name

  tags = merge(
    var.tags,
    {
      Project = "aws-hosted-podcast"
    }
  )
}

resource "aws_s3_bucket_versioning" "podcast" {
  bucket = aws_s3_bucket.podcast.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "podcast" {
  bucket = aws_s3_bucket.podcast.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "podcast" {
  bucket = aws_s3_bucket.podcast.id

  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = false
  restrict_public_buckets = false
}

data "aws_iam_policy_document" "podcast_bucket" {
  statement {
    sid = "AllowPublicPodcastReads"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions = [
      "s3:GetObject",
    ]

    resources = [
      "${aws_s3_bucket.podcast.arn}/*",
    ]
  }

  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions = [
      "s3:*",
    ]

    resources = [
      aws_s3_bucket.podcast.arn,
      "${aws_s3_bucket.podcast.arn}/*",
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "podcast" {
  bucket = aws_s3_bucket.podcast.id
  policy = data.aws_iam_policy_document.podcast_bucket.json

  depends_on = [
    aws_s3_bucket_public_access_block.podcast,
  ]
}

resource "aws_cloudfront_distribution" "podcast" {
  count = var.enable_cloudfront ? 1 : 0

  enabled         = true
  is_ipv6_enabled = true
  comment         = "The Nikos Show static podcast hosting"
  aliases         = var.cloudfront_alias == null || var.acm_certificate_arn == null ? [] : [var.cloudfront_alias]
  price_class     = var.cloudfront_price_class

  origin {
    domain_name = aws_s3_bucket.podcast.bucket_regional_domain_name
    origin_id   = "podcast-s3"

    s3_origin_config {
      origin_access_identity = ""
    }
  }

  default_cache_behavior {
    target_origin_id       = "podcast-s3"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = false

    forwarded_values {
      query_string = false

      cookies {
        forward = "none"
      }
    }

    min_ttl     = 0
    default_ttl = 3600
    max_ttl     = 86400
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = var.acm_certificate_arn == null
    acm_certificate_arn            = var.acm_certificate_arn
    minimum_protocol_version       = var.acm_certificate_arn == null ? null : "TLSv1.2_2021"
    ssl_support_method             = var.acm_certificate_arn == null ? null : "sni-only"
  }
}

data "aws_iam_policy_document" "github_assume_role" {
  count = var.create_github_deploy_role ? 1 : 0

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_owner}/${var.github_repo}:ref:refs/heads/${var.github_branch}"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  count = var.create_github_deploy_role ? 1 : 0

  name                 = var.github_deploy_role_name
  assume_role_policy   = data.aws_iam_policy_document.github_assume_role[0].json
  description          = "GitHub Actions role for deploying The Nikos Show podcast stack"
  max_session_duration = 3600

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "github_power_user" {
  count = var.create_github_deploy_role ? 1 : 0

  role       = aws_iam_role.github_deploy[0].name
  policy_arn = "arn:aws:iam::aws:policy/PowerUserAccess"
}

data "aws_iam_policy_document" "github_cdk_assume_role" {
  count = var.create_github_deploy_role ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]

    resources = [
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/cdk-hnb659fds-*",
    ]
  }
}

resource "aws_iam_role_policy" "github_cdk_assume_role" {
  count = var.create_github_deploy_role ? 1 : 0

  name   = "AllowCdkBootstrapRoleAssume"
  role   = aws_iam_role.github_deploy[0].id
  policy = data.aws_iam_policy_document.github_cdk_assume_role[0].json
}
