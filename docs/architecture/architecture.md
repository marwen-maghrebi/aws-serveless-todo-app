# Architecture

## Overview

This project is a serverless To-Do application built with AWS managed services. Users authenticate with Amazon Cognito and access To-Do operations through Amazon API Gateway. AWS Lambda runs the application logic, and Amazon DynamoDB stores the To-Do items.

The frontend is hosted in a private Amazon S3 bucket and delivered through Amazon CloudFront. AWS WAF provides web request filtering. Amazon CloudWatch and AWS X-Ray support logging, monitoring, alarms, and tracing.

## Request flow

1. A user opens the frontend delivered by CloudFront from the private S3 origin.
2. The user signs in through the configured Amazon Cognito authentication flow.
3. The frontend sends authenticated API requests to API Gateway.
4. The API authorizer validates the user's identity and supplies claims to the Lambda integration.
5. The Lambda CRUD handler uses the authenticated user's `sub` claim to associate To-Do items with that user.
6. The handler reads and writes items in DynamoDB. A Global Secondary Index (GSI) supports the query pattern configured by the application.
7. DynamoDB Streams can invoke the stream processor Lambda when table records change.
8. CloudWatch collects logs and alarms; AWS X-Ray provides tracing where enabled. AWS WAF protects the configured web entry points with the rules that have been deployed.

## Main components

| Component | Responsibility |
|---|---|
| Amazon S3 | Stores the static frontend in a private bucket. |
| Amazon CloudFront | Delivers the frontend and, if configured, routes API traffic through a separate origin/behavior. |
| Amazon Cognito | Authenticates users and provides identity claims. |
| Amazon API Gateway | Exposes the application API and connects requests to Lambda. |
| AWS Lambda — CRUD handler | Creates, lists, updates, and deletes To-Do items. |
| Amazon DynamoDB | Persists To-Do records. |
| DynamoDB Global Secondary Index | Supports the application's additional indexed query pattern. |
| DynamoDB Streams | Emits item-change records for downstream processing. |
| AWS Lambda — stream processor | Processes DynamoDB stream records. |
| AWS WAF | Applies the configured web access rules, including any deployed geographic rule. |
| Amazon CloudWatch | Stores logs and supports alarms and operational monitoring. |
| AWS X-Ray | Traces supported requests when tracing is enabled. |

## Authentication and user isolation

The test events model an API Gateway authorizer context containing the Cognito `sub` claim. The CRUD handler is expected to use that identity when accessing user-owned records. The `no-auth` test event has no authorizer claims and is intended to exercise the unauthenticated-request path.

The `list-user2` test demonstrates a separate user identity. An empty result is expected only when that user has no To-Do items in the test data; it is not guaranteed if the table already contains records for that user.

## Security notes

- Keep the S3 frontend bucket private and use the intended CloudFront access configuration.
- Follow least privilege for Lambda execution roles and resource policies.
- Do not commit AWS access keys, passwords, private keys, or other credentials.
- Treat API endpoint URLs and resource identifiers as configuration information; review the repository for actual secrets before publishing.
- WAF protections apply only to the resources and stages associated with the deployed Web ACL.

## Monitoring

Use CloudWatch logs and alarms to investigate runtime errors and operational behavior. Use X-Ray traces when tracing is enabled for the relevant services.

## Deployment notes and limitations

This document describes the intended project architecture. Actual behavior depends on the AWS resources, permissions, API routes, authorizer, CloudFront behaviors, WAF associations, and Lambda configuration deployed in your account.

A usage plan does not enforce throttling or quotas on requests unless API methods are configured to require API keys and the plan is associated appropriately. Verify the deployed API configuration before describing usage-plan limits as enforced.
