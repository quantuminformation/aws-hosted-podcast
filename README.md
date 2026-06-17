# AWS Hosted Podcast

Static AWS hosting scaffold for `The Nikos Show`.

## What this stack provides

- Private S3 bucket for podcast assets.
- CloudFront distribution in front of the bucket.
- HTTPS-only delivery.
- Separate cache behavior for `feed.xml` so the RSS feed can change quickly.
- Long-lived edge caching for episode audio and artwork.

## Current MVP setup

This repository is wired for a quick deploy with the CloudFront default domain first.

Later, when you are ready to use `podcast.nikoskatsikanis.com`, create the ACM certificate in `us-east-1`, validate it with Namecheap DNS, then redeploy the stack with:

```bash
cdk deploy -c customDomainName=podcast.nikoskatsikanis.com -c certificateArn=arn:aws:acm:us-east-1:123456789012:certificate/...
```

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

The stack creates the AWS role named `aws-hosted-podcast-github-deploy` plus the GitHub OIDC provider. The first time, deploy the stack once with existing AWS credentials so those resources exist before GitHub Actions can assume the role.

## DNS for Namecheap later

Once the distribution is deployed and the cert is attached, add a `CNAME` record in Namecheap:

- Host: `podcast`
- Value: the CloudFront distribution domain name from the stack output
- TTL: default or 30 minutes

## Next step

The feed generation and bulk upload pipeline should be added after the downloaded audio files and the current Simplecast RSS metadata are available.
