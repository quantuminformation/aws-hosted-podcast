import * as cdk from "aws-cdk-lib";
import { Duration, RemovalPolicy, Stack, type StackProps } from "aws-cdk-lib";
import * as acm from "aws-cdk-lib/aws-certificatemanager";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as origins from "aws-cdk-lib/aws-cloudfront-origins";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3deploy from "aws-cdk-lib/aws-s3-deployment";
import { Construct } from "constructs";

export interface PodcastHostingStackProps extends StackProps {
    readonly customDomainName?: string;
    readonly certificateArn?: string;
    readonly githubOwner?: string;
    readonly githubRepo?: string;
    readonly githubBranch?: string;
}

export class PodcastHostingStack extends Stack {
    public constructor(scope: Construct, id: string, props: PodcastHostingStackProps = {}) {
        super(scope, id, props);

        const githubOwner = props.githubOwner ?? "quantuminformation";
        const githubRepo = props.githubRepo ?? "aws-hosted-podcast";
        const githubBranch = props.githubBranch ?? "main";

        const bucket = new s3.Bucket(this, "PodcastBucket", {
            blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
            encryption: s3.BucketEncryption.S3_MANAGED,
            enforceSSL: true,
            versioned: true,
            removalPolicy: RemovalPolicy.RETAIN,
            autoDeleteObjects: false,
        });

        const originAccessIdentity = new cloudfront.OriginAccessIdentity(this, "OriginAccessIdentity", {
            comment: "Access identity for The Nikos Show podcast assets",
        });

        const githubOidcProvider = new iam.OpenIdConnectProvider(this, "GithubOidcProvider", {
            url: "https://token.actions.githubusercontent.com",
            clientIds: ["sts.amazonaws.com"],
            thumbprints: ["6938fd4d98bab03faadb97b34396831e3780aea1"],
        });

        const githubDeployRole = new iam.Role(this, "GithubDeployRole", {
            roleName: "aws-hosted-podcast-github-deploy",
            assumedBy: new iam.OpenIdConnectPrincipal(githubOidcProvider).withConditions({
                StringEquals: {
                    "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                },
                StringLike: {
                    "token.actions.githubusercontent.com:sub": `repo:${githubOwner}/${githubRepo}:ref:refs/heads/${githubBranch}`,
                },
            }),
            description: "GitHub Actions role for deploying The Nikos Show podcast stack",
            managedPolicies: [iam.ManagedPolicy.fromAwsManagedPolicyName("AdministratorAccess")],
            maxSessionDuration: Duration.hours(1),
        });

        const defaultBehavior: cloudfront.BehaviorOptions = {
            origin: origins.S3BucketOrigin.withOriginAccessIdentity(bucket, {
                originAccessIdentity,
            }),
            viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
            cachedMethods: cloudfront.CachedMethods.CACHE_GET_HEAD,
            cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
            responseHeadersPolicy: cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
            compress: true,
        };

        const feedBehavior: cloudfront.BehaviorOptions = {
            origin: origins.S3BucketOrigin.withOriginAccessIdentity(bucket, {
                originAccessIdentity,
            }),
            viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
            cachedMethods: cloudfront.CachedMethods.CACHE_GET_HEAD,
            cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
            responseHeadersPolicy: cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
            compress: true,
        };

        const distributionProps: Omit<cloudfront.DistributionProps, "defaultBehavior"> = {
            defaultRootObject: undefined,
            comment: "The Nikos Show podcast hosting",
            priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
            enabled: true,
            errorResponses: [
                {
                    httpStatus: 403,
                    responseHttpStatus: 404,
                    responsePagePath: "/feed.xml",
                    ttl: Duration.minutes(1),
                },
                {
                    httpStatus: 404,
                    responseHttpStatus: 404,
                    responsePagePath: "/feed.xml",
                    ttl: Duration.minutes(1),
                },
            ],
        };

        const distribution =
            props.customDomainName && props.certificateArn
                ? new cloudfront.Distribution(this, "Distribution", {
                      ...distributionProps,
                      defaultBehavior,
                      domainNames: [props.customDomainName],
                      certificate: acm.Certificate.fromCertificateArn(this, "Certificate", props.certificateArn),
                      additionalBehaviors: {
                          "feed.xml": feedBehavior,
                      },
                  })
                : new cloudfront.Distribution(this, "Distribution", {
                      ...distributionProps,
                      defaultBehavior,
                      additionalBehaviors: {
                          "feed.xml": feedBehavior,
                      },
                  });

        new s3deploy.BucketDeployment(this, "SeedFeed", {
            destinationBucket: bucket,
            sources: [s3deploy.Source.data("feed.xml", this.renderFeedPlaceholder())],
            distribution,
            distributionPaths: ["/feed.xml"],
        });

        new cdk.CfnOutput(this, "BucketName", {
            value: bucket.bucketName,
        });

        new cdk.CfnOutput(this, "CloudFrontDomainName", {
            value: distribution.domainName,
        });

        new cdk.CfnOutput(this, "FeedUrl", {
            value: props.customDomainName ? `https://${props.customDomainName}/feed.xml` : `https://${distribution.domainName}/feed.xml`,
        });

        new cdk.CfnOutput(this, "GithubDeployRoleArn", {
            value: githubDeployRole.roleArn,
        });

        if (props.customDomainName) {
            new cdk.CfnOutput(this, "NamecheapCnameTarget", {
                value: distribution.domainName,
            });
        }
    }

    private renderFeedPlaceholder(): string {
        return [
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
            '<rss version="2.0">',
            "  <channel>",
            "    <title>The Nikos Show</title>",
            "    <description>Placeholder feed generated by CDK. Replace this with the migrated RSS feed.</description>",
            "    <link>https://example.com</link>",
            "  </channel>",
            "</rss>",
            "",
        ].join("\n");
    }
}
