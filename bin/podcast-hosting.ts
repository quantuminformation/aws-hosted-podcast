import * as cdk from "aws-cdk-lib";
import { PodcastHostingStack } from "../lib/podcast-hosting-stack.js";

const app = new cdk.App();

const customDomainName = app.node.tryGetContext("customDomainName") as string | undefined;
const certificateArn = app.node.tryGetContext("certificateArn") as string | undefined;

new PodcastHostingStack(app, "PodcastHostingStack", {
    env: {
        account: process.env.CDK_DEFAULT_ACCOUNT,
        region: process.env.CDK_DEFAULT_REGION,
    },
    customDomainName,
    certificateArn,
});
