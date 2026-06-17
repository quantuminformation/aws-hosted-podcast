# AWS Hosted Podcast

Static AWS hosting scaffold for `The Nikos Show`.

## What this stack provides

- Public S3 bucket for podcast assets.
- HTTPS object URLs for `feed.xml`, episode audio, and artwork.
- Simple deploy path that avoids CloudFront for the MVP.

## Current MVP setup

The stack now uses S3 only.

Feed and media URLs are direct S3 HTTPS object URLs from the bucket output.

## Deploy

1. Install dependencies.
2. Bootstrap CDK in your AWS account if needed.
3. Deploy the stack.

```bash
pnpm install
pnpm bootstrap
pnpm deploy
```

## GitHub Actions

The repo includes a minimal workflow at [/.github/workflows/ci.yml](/Users/nikos/WebstormProjects/aws-hosted-podcast/.github/workflows/ci.yml).

- `pull_request` and `push` to `main` run `pnpm build` and `pnpm exec cdk synth`.
- `push` to `main` also assumes the AWS deploy role through GitHub OIDC and runs `cdk deploy`.

No GitHub secrets are required for the deploy workflow. The workflow assumes the fixed role ARN for account `180971085012`.

The stack creates the AWS role named `aws-hosted-podcast-github-deploy` and trusts the existing GitHub OIDC provider in the account. The first time, deploy the stack once with existing AWS credentials so the role exists before GitHub Actions can assume it.

## Next step

The feed generation and bulk upload pipeline should be added after the downloaded audio files and the current Simplecast RSS metadata are available.
