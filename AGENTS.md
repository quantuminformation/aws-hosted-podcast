# AGENTS.md

## Project

This repository supports migration of `The Nikos Show` from Simplecast to static AWS hosting.

The aim is to preserve the existing podcast identity, directory authority, subscriber continuity, and public platform history while reducing hosting cost.

## Current source feed

```txt
https://feeds.simplecast.com/EaEV0pvl
```

Do not create a new podcast listing unless explicitly asked. The correct migration path is to keep the same podcast identity and move the RSS feed.

## Target hosting model

Use static hosting only:

```txt
S3 bucket
  feed.xml
  artwork/
  episodes/

CloudFront distribution
  custom podcast feed URL
```

Avoid services that create ongoing fixed cost or needless runtime complexity:

```txt
No EC2
No RDS
No API Gateway
No Lambda per request
No MediaConvert unless explicitly requested
No database unless explicitly requested
```

## Migration rules

Do not change episode GUIDs.

Preserve as much of the original RSS metadata as possible:

```txt
title
description
pubDate
guid
enclosure URL replacement
duration
explicit flag
author
categories
artwork
episode number
season number
```

Episode audio files may move to new URLs, but GUIDs should remain stable to avoid duplicate episodes in podcast apps.

## Redirect plan

The new AWS feed must be working before enabling the Simplecast redirect.

Migration flow:

```txt
1. Download current Simplecast RSS feed.
2. Download all audio files locally.
3. Upload audio and artwork to S3.
4. Generate new feed.xml using the old metadata and new enclosure URLs.
5. Test the new feed and audio URLs.
6. Add itunes:new-feed-url to the feed.
7. Set the Simplecast 301 redirect to the new feed.
8. Check Apple Podcasts, Spotify, Amazon Music, YouTube Music, Pocket Casts, and Podcast Index.
9. Keep Simplecast alive for at least 4 weeks after redirect.
```

## Python and Terraform style

Use Python for migration scripts and Terraform for infrastructure.

Prefer:

```txt
python3
standard library modules
urllib.request
pathlib
subprocess with AWS CLI
terraform
```

Avoid:

```txt
large dependencies
changing episode GUIDs
AWS SDK dependencies unless AWS CLI is not enough
Terraform apply before importing existing live resources
```

## Safety rules

Never delete or cancel the Simplecast account before the new feed works and the redirect has been live long enough.

Never regenerate GUIDs.

Never create a second duplicate podcast listing unless explicitly asked.

Never assume a platform has migrated. Check the main directories manually.
