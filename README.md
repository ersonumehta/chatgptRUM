# CloudWatch RUM interview demo

A five-page static website, served privately from S3 through CloudFront, with CloudWatch RUM telemetry. CloudFormation creates the app monitor and Cognito guest identity pool. A custom-resource Lambda reads the app monitor ID from SSM Parameter Store, injects the RUM client configuration into the HTML, and publishes the site files to S3.

## Project layout

- `site/`: five static pages; each includes a RUM configuration placeholder.
- `lambda/`: Python custom-resource handler that copies and configures the site.
- `infra/app.yaml`: deploys the site, CloudFront, RUM, Cognito, SSM parameter, and publisher Lambda.
- `infra/pipeline.yaml`: optional CodePipeline + CodeBuild CI/CD stack for a public GitHub repository.
- `buildspec.yml`: packages and deploys `infra/app.yaml` from CodeBuild.
- `pom.xml`: lightweight Maven project metadata so IntelliJ IDEA can import/open the repository as a project.

## Deploy the application once

Use an AWS account/region where CloudWatch RUM, CloudFront, Cognito, SSM, Lambda, and S3 are available. CloudFront is a global service. The deploying identity needs CloudFormation permissions to create the listed resources and the associated IAM roles/policies.

1. Package the publisher Lambda and site:

   ```bash
   zip -j lambda.zip lambda/handler.py
   zip -r site.zip site
   ```

2. Upload both ZIPs to a deployment bucket in the target region. Create a CloudFormation stack from `infra/app.yaml`, passing `ArtifactBucket`, `LambdaArtifactKey`, and `SiteArtifactKey`, with `CAPABILITY_IAM` and `CAPABILITY_NAMED_IAM`. The artifact bucket must be in the same region as the stack. CloudFormation sets the monitor's allowed domain to the generated CloudFront hostname.

3. Open the `WebsiteUrl` output. Browse the five pages, generate a JavaScript error on the Errors page, and inspect the app monitor in CloudWatch RUM. Data appears after real browser sessions and may take a few minutes.

The application stack stores the generated monitor ID in `/cloudwatch-rum-demo/<stack-name>/app-monitor-id`. The client config also includes the Cognito identity pool ID, region, and guest role ARN required by the CloudWatch RUM web client.

## Enable GitHub to AWS deployment

The source is intended for a **public GitHub repository**. Create the public repository first, then create and authorize an AWS CodeStar connection for GitHub. A GitHub personal access token is not needed by this design.

Deploy `infra/pipeline.yaml` and supply `GitHubConnectionArn`, `GitHubOwner`, `GitHubRepository`, and `GitHubBranch`. The connection must be in `ConnectionArn`'s AWS region and reach the repo. A CodeStar connection often remains `PENDING` until you finish authorization in the AWS console.

For example, after creating the public repository, create the AWS side of the GitHub connection and finish its authorization in the AWS console:

```bash
aws codestar-connections create-connection --provider-type GitHub --connection-name rum-demo-github
aws codestar-connections list-connections
```

Then deploy the pipeline stack (replace the example values):

```bash
aws cloudformation deploy --template-file infra/pipeline.yaml --stack-name rum-demo-pipeline --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM --parameter-overrides GitHubConnectionArn=arn:aws:codestar-connections:REGION:ACCOUNT:connection/CONNECTION_ID GitHubOwner=YOUR_GITHUB_OWNER GitHubRepository=YOUR_PUBLIC_REPO GitHubBranch=main
```

On each source revision, CodeBuild packages the Lambda and site, uploads them to the pipeline artifact bucket, and runs CloudFormation deployment for the application stack. Configure the bootstrap stack's `ApplicationStackName` and `GitHub*` parameters before deployment. The first deployment can take several minutes because CloudFront distribution creation is asynchronous. The pipeline stack contains a deployment role with broad S3/CloudFront/RUM permissions because CloudFormation creates resources whose physical names are generated during deployment; review and narrow this policy for your account before using it beyond an interview demo.

## IntelliJ IDEA

Open this directory (`File → Open`) or open `pom.xml` and import it as a Maven project. The AWS templates and Python Lambda remain ordinary project files; no Java runtime is required to deploy the site.

## Security and cost notes

- The website bucket has public access blocked; CloudFront Origin Access Control is its only read path.
- RUM uses Cognito unauthenticated identities. The guest role is restricted to `rum:PutRumEvents` for this monitor. Do not add secrets or credentials to the static pages.
- CloudFront, CloudWatch RUM events, CodePipeline, CodeBuild, and supporting AWS resources can incur charges. Remove the application and pipeline stacks when finished.
- This is an interview demo, not a production baseline. Add a custom domain/TLS certificate, CSP/security headers, retention choices, and tighter organization-specific deployment controls as needed.
