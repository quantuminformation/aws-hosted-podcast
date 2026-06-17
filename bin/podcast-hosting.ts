import * as cdk from "aws-cdk-lib";
import { PodcastHostingStack } from "../lib/podcast-hosting-stack.js";

const app = new cdk.App();

new PodcastHostingStack(app, "PodcastHostingStack", {
    env: {
        account: process.env.CDK_DEFAULT_ACCOUNT,
        region: process.env.CDK_DEFAULT_REGION,
    },
});
