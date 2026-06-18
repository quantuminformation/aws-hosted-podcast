# AWS Hosted Podcast

Static AWS hosting for migrating `The Nikos Show` from Simplecast without creating a duplicate podcast listing.

The repository now uses Terraform for infrastructure and Python for the migration pipeline.

## Infrastructure

Terraform lives in [terraform](/Users/nikos/WebstormProjects/aws-hosted-podcast/terraform).

It manages:

- An S3 bucket for `feed.xml`, `episodes/`, and `artwork/`.
- Bucket encryption, versioning, public read policy, and HTTPS-only access.
- Optional CloudFront distribution for a custom feed hostname.
- The GitHub OIDC deploy role used by CI.

Copy the example variables file before local Terraform work:

```bash
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
```

The live bucket was created before the Terraform conversion. Import it before applying Terraform against the existing account:

```bash
terraform -chdir=terraform init
terraform -chdir=terraform import aws_s3_bucket.podcast podcasthostingstack-podcastbucket09986f6a-7benur70xfx0
```

Terraform is not required for the asset migration itself.

## Migration

The migration runner is [scripts/sync_podcast_assets.py](/Users/nikos/WebstormProjects/aws-hosted-podcast/scripts/sync_podcast_assets.py), with a compatibility wrapper at [scripts/sync-podcast-assets.py](/Users/nikos/WebstormProjects/aws-hosted-podcast/scripts/sync-podcast-assets.py). It preserves episode GUIDs, resumes from [manifests/upload-manifest.ndjson](/Users/nikos/WebstormProjects/aws-hosted-podcast/manifests/upload-manifest.ndjson), streams missing Simplecast audio directly to S3, syncs artwork, and can generate the final `feed.xml`.

Use the live stack profile and bucket:

```bash
export AWS_PROFILE=nikos
export AWS_REGION=us-east-1
export PODCAST_BUCKET_NAME=podcasthostingstack-podcastbucket09986f6a-7benur70xfx0
export UPLOAD_CONCURRENCY=4
export FETCH_RETRIES=5
```

Resume asset sync:

```bash
python3 scripts/sync-podcast-assets.py
```

Generate, upload, and verify the new feed after all media and artwork are present:

```bash
python3 scripts/sync-podcast-assets.py --generate-feed --upload-feed --verify
```

Do not pass `--add-new-feed-url` until the new feed and media URLs have been tested. When it is time, set `NEW_FEED_URL` to the final feed URL first.

## CI

GitHub Actions compiles the Python scripts, validates Terraform, and runs a Terraform plan on pushes to `main`. It does not auto-apply infrastructure changes.

## Safety

Do not cancel Simplecast or enable its redirect until the AWS feed is working, media URLs resolve, and the podcast directories have been checked manually after the 301 redirect.
