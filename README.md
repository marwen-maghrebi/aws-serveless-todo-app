# Serverless REST API with Cognito Auth, DynamoDB & WAF

A serverless to-do application on AWS. Users sign in with Amazon Cognito and call a REST API (API Gateway + Lambda) protected by AWS WAF. Data is stored per user in DynamoDB, and every table change triggers a stream consumer. The static frontend is served from a private S3 bucket through CloudFront.

**Region:** Europe (Stockholm), `eu-north-1` · **Built:** October 25–26, 2026

![Architecture](architecture/Architecture.png)

---

## 1. Architecture

| Layer | Service | Role |
|---|---|---|
| Edge | CloudFront (`todo-web`) | Serves the website from S3 and forwards `/prod/*` to API Gateway |
| Frontend | S3 (private, Origin Access Control) | Hosts `index.html` (plain HTML/JS) |
| Auth | Cognito User Pool | Sign-in, JWT (ID token), managed login page |
| API | API Gateway REST API (`todo-api`, stage `prod`) | Cognito authorizer, Lambda proxy integration, CORS; usage plan attached but not enforced without API-key-required methods |
| Protection | AWS WAF (regional web ACL on the API stage) | Rate limit, OWASP Common Rule Set, geo-block |
| Compute | Lambda `todo-api` (Python) | CRUD handler |
| Data | DynamoDB `todos` (on-demand) | Items, GSI, Streams |
| Events | Lambda `todo-stream-processor` | Consumes the DynamoDB stream |
| Observability | X-Ray, CloudWatch Logs, CloudWatch alarm | Tracing, logs, error alarm |

### Request flow

1. The user opens the website (HTTPS) through CloudFront.
2. CloudFront serves `index.html` from S3.
3. The user signs in and receives a JWT from Cognito.
4. The page calls `/prod/*` with the token in the `Authorization` header.
5. API Gateway validates the token and invokes Lambda.
6. Lambda reads or writes DynamoDB, scoped to the user's `sub` claim.
7. Each table change goes to the stream and is processed by `todo-stream-processor`.

### Design decisions

- **Per-user isolation:** Lambda reads `sub` from the verified token claims and uses it as the partition key, so a user can only reach their own items.
- **Private frontend bucket:** public access is fully blocked. Only the CloudFront distribution can read the bucket (OAC + bucket policy).
- **No edge caching for the API:** `/prod/*` uses `Managed-CachingDisabled` because responses are authenticated and per-user. Edge caching applies to the website files only.
- **Header forwarding:** `/prod/*` uses `Managed-AllViewerExceptHostHeader`, so the `Authorization` header reaches API Gateway.
- **WAF scope:** the web ACL is attached to the API stage, not to CloudFront. The API address can be called directly, and calls through CloudFront reach WAF with CloudFront IP addresses, so per-IP and per-country rules behave differently there.
- **Least privilege:** the Lambda role can only call five DynamoDB actions, on the `todos` table and its indexes.

---

## 2. Resource names

Names are case-sensitive (`userId`, not `userID`).

| Resource | Value |
|---|---|
| DynamoDB table | `todos` (partition key `userId`, sort key `todoId`, on-demand) |
| DynamoDB GSI | `userId-createdAt-index` (partition key `userId`, sort key `createdAt`, projection: all) |
| DynamoDB stream | On `todos`, view type: new and old images |
| Lambda role | `todo-lambda-role`, inline policy `todo-dynamodb-access` |
| Lambda (API) | `todo-api`, environment variable `TABLE_NAME=todos`, 128 MB |
| Lambda (stream) | `todo-stream-processor` (trigger: `todos` stream, batch size 100, starting position `LATEST`) |
| Cognito app client | `todo-web` (no client secret) |
| API Gateway | `todo-api`, stage `prod`, authorizer `cognito-authorizer` |
| WAF web ACL | `todo-api-waf` (regional, default action Allow) |
| WAF rules | `rate-limit` (0), `AWS-AWSManagedRulesCommonRuleSet` (1), `geo-block` (2) |
| Usage plan | `todo-usage-plan` (50 req/s, burst 100, 10,000 per month) |
| S3 bucket | `todo-web-marwen-2026` (use your own globally unique name) |
| CloudFront | distribution `todo-web`, origins `api-origin` and the S3 bucket |
| CloudWatch alarm | `todo-api-errors` |

---

## 3. Repository structure

```
.
├── README.md
├── LICENSE
├── architecture/
│   └── Architecture.png
├── backend/
│   ├── lambda_function.py            # CRUD handler (GET queries the GSI)
│   └── stream_processor.py           # DynamoDB stream consumer
├── frontend/
│   └── index.html                    # placeholders for CloudFront domain and client ID
├── policies/
│   ├── lambda-trust-policy.json
│   ├── apigateway-logs-trust-policy.json
│   ├── lambda-resource-policy.json   # API Gateway may invoke todo-api
│   ├── todo-dynamodb-access.json     # table + index permissions
│   ├── s3-bucket-policy.json         # CloudFront-only read access
│   └── waf-rules.json                # rate-limit, common rule set, geo-block
├── tests/
│   └── test_events.json              # Lambda test events
└── screenshots/
```

Before deployment, replace `<ACCOUNT_ID>`, `<API_ID>`, `<DISTRIBUTION_ID>`, `<CLOUDFRONT_DOMAIN>` and `<APP_CLIENT_ID>` wherever they appear in the policy files and frontend configuration. Use your own resource values, and never commit credentials, tokens, passwords or other secrets. Resource identifiers are not all secrets, but avoid publishing account-specific values if you do not want them public.

---

## 4. Build steps

### Step 1. DynamoDB table
Table `todos` with partition key `userId` (String) and sort key `todoId` (String), on-demand capacity, standard table class, deletion protection off. Each item holds `userId`, `todoId` (UUID), `title`, `done` and `createdAt` (ISO 8601, UTC).



![01-dynamodb-table](screenshots/01-dynamodb-table.png)

### Step 2. IAM role for Lambda
Role `todo-lambda-role`, trusted by `lambda.amazonaws.com` (`policies/lambda-trust-policy.json`), with:

- `AWSLambdaBasicExecutionRole` (AWS managed): writes logs to CloudWatch.
- Inline policy `todo-dynamodb-access`: `GetItem`, `PutItem`, `UpdateItem`, `DeleteItem` and `Query` on the `todos` table and its indexes (`policies/todo-dynamodb-access.json`). The final policy file includes both `table/todos` and `table/todos/index/*`. If reproducing the setup chronologically, add the index ARN permission when you create the GSI in Step 15; without it, the GSI query fails with `AccessDenied`.
- `AWSLambdaTracerAccessExecutionRole-…` (customer managed): created when X-Ray was enabled (Step 8).



![02-iam-role-permissions](screenshots/02-iam-role-permissions.png)

### Step 3. Lambda function `todo-api`
Python function (`backend/lambda_function.py`) with `TABLE_NAME=todos`, running as `todo-lambda-role`.

- Reads the user ID from `event["requestContext"]["authorizer"]["claims"]["sub"]`. Without it, it returns `401`.
- `POST` creates an item (title required, `201`), `PUT` updates `title` and `done`, `DELETE` removes an item. `PUT` and `DELETE` use `attribute_exists(todoId)`, so an unknown ID returns `404`.
- Every response carries the CORS headers. Invalid JSON returns `400`, DynamoDB errors return `500` and are logged.
- API Gateway may invoke the function through the resource policy in `policies/lambda-resource-policy.json` (one statement per method, scoped to the API's source ARN).



![03-lambda-config](screenshots/03-lambda-config.png)
![03b-lambda-permissions](screenshots/03b-lambda-permissions.png)
![03c-lambda-dynamodb-permissions](screenshots/03c-lambda-dynamodb-permissions.png)
![03d-lambda-xray-permissions](screenshots/03d-lambda-xray-permissions.png)

### Step 4. Test the function with test events
The events are in `tests/test_events.json` (replace `PASTE-TODO-ID` with the ID returned by `create-todo`).

| Event | Result | Proves |
|---|---|---|
| `create-todo` | `201`, new item for `test-user-1` | Create works |
| `list-todos` | `200`, list with the item | Read works |
| `no-auth` | `401` `{"message": "Unauthorized"}` | Missing claims are rejected |
| `list-user2` | `200`, expected `[]` in a clean test environment where `test-user-2` has no items | User 2 does not receive user 1's items |
| `update-todo` | `200`, `done: true` | Update works |
| `delete-todo` | `200`, `{"deleted": "<todoId>"}` | Delete works |



![04-lambda-create-201](screenshots/04-lambda-create-201.png)
![05-lambda-list-200](screenshots/05-lambda-list-200.png)
![06-lambda-no-auth-401](screenshots/06-lambda-no-auth-401.png)
![07-lambda-user-isolation](screenshots/07-lambda-user-isolation.png)
![08-lambda-update-200](screenshots/08-lambda-update-200.png)
![09-lambda-delete-200](screenshots/09-lambda-delete-200.png)

### Step 5. Cognito user pool
User pool with sign-in by email, self-registration disabled and one confirmed test user (email verified). App client `todo-web`:

- No client secret.
- Flows: username and password (`USER_PASSWORD_AUTH`), SRP, refresh tokens.
- ID and access tokens last 60 minutes, refresh tokens 5 days.
- Token revocation and "prevent user existence errors" enabled.



![13-cognito-app-client](screenshots/13-cognito-app-client.png)
![14-cognito-user](screenshots/14-cognito-user.png)

### Step 6. API Gateway
REST API `todo-api` with these resources:

| Path | Methods | Integration | Authorization |
|---|---|---|---|
| `/todos` | `GET`, `POST` | Lambda proxy (`todo-api`) | Cognito user pool |
| `/todos/{todoId}` | `PUT`, `DELETE` | Lambda proxy (`todo-api`) | Cognito user pool |
| `/todos`, `/todos/{todoId}` | `OPTIONS` | Mock (CORS preflight) | None |

The authorizer `cognito-authorizer` reads the token from the `Authorization` header and validates it against the user pool. The API is deployed to stage `prod`.



![10-api-resources](screenshots/10-api-resources.png)
![15-api-authorizer](screenshots/15-api-authorizer.png)
![11-api-stage-prod](screenshots/11-api-stage-prod.png)

### Step 7. Test the API
Without a token the API returns `401`.


In CloudShell, get an ID token and call the API with and without it:

```bash
TOKEN=$(aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH \
  --client-id <APP_CLIENT_ID> \
  --auth-parameters USERNAME=<EMAIL>,PASSWORD='<PASSWORD>' \
  --region eu-north-1 \
  --query 'AuthenticationResult.IdToken' --output text)

curl -i https://<API_ID>.execute-api.eu-north-1.amazonaws.com/prod/todos
curl -i -H "Authorization: $TOKEN" https://<API_ID>.execute-api.eu-north-1.amazonaws.com/prod/todos
```

Result: `401 UnauthorizedException` without the token, `200` with the user's items and the CORS headers with it.



![12-api-unauthorized](screenshots/12-api-unauthorized.png)
![16-api-auth-test](screenshots/16-api-auth-test.png)

### Step 8. Tracing, logging and alarm
- **X-Ray:** active tracing on the Lambda function (Lambda service traces enabled) and on the API stage. The trace list shows three nodes: `todo-api` (Lambda context), `todo-api` (Lambda function) and `todo-api/prod` (API Gateway stage).
- **Logging:** the API stage logs errors only. API Gateway writes to CloudWatch through a role trusted by `apigateway.amazonaws.com` (`policies/apigateway-logs-trust-policy.json`). Lambda logs go to `/aws/lambda/todo-api`.
- **Alarm:** `todo-api-errors` triggers on `Errors >= 1` for 1 datapoint within 5 minutes. It has no notification action.



![23-lambda-xray](screenshots/23-lambda-xray.png)
![26-xray-traces](screenshots/26-xray-traces.png)
![25-cloudwatch-alarm](screenshots/25-cloudwatch-alarm.png)

### Step 9. AWS WAF
Regional web ACL `todo-api-waf` in Stockholm, default action Allow, associated with the `prod` stage (`policies/waf-rules.json`):

| Priority | Rule | Action | Details |
|---|---|---|---|
| 0 | `rate-limit` | Block | Rate-based, 100 requests per 5 minutes per IP |
| 1 | `AWS-AWSManagedRulesCommonRuleSet` | Use rule actions | OWASP-style managed rules |

The two rules use 702 of 5,000 WCUs. A burst of test requests triggered the rate limit: over one day the dashboard counted 446 requests, 150 blocked (classified as volumetric) and 296 allowed.



![27-waf-rules](screenshots/27-waf-rules.png)
![28-waf-traffic](screenshots/28-waf-traffic.png)
![28b-waf-traffic-characteristics](screenshots/28b-waf-traffic-characteristics.png)

### Step 10. API Gateway caching
A cache cluster was provisioned on the stage and three identical calls were timed from CloudShell:

```bash
for i in 1 2 3; do curl -s -o /dev/null \
  -w "call $i: %{http_code} in %{time_total}s\n" \
  -H "Authorization: $TOKEN" \
  https://<API_ID>.execute-api.eu-north-1.amazonaws.com/prod/todos; done
```

| Call | Status | Time |
|---|---|---|
| 1 | 200 | 0.911 s |
| 2 | 200 | 0.073 s |
| 3 | 200 | 0.060 s |

Calls 2 and 3 were much faster. A warm Lambda and an open connection also reduce latency, so these timings alone do not prove cache hits. The cache cluster was removed after the test because it is billed hourly (the final stage shows it inactive).



![30-api-cache-provisioned](screenshots/30-api-cache-provisioned.png)
![31-api-cache-test](screenshots/31-api-cache-test.png)

### Step 11. S3 bucket for the frontend
Bucket `todo-web-marwen-2026` with **Block all public access** on and one object, `index.html` (5.2 KB). The bucket policy only lets the CloudFront service principal read objects, and only for this distribution (`policies/s3-bucket-policy.json`).

`frontend/index.html` is a single page with a sign-in form and the to-do list. It signs in with the Cognito `InitiateAuth` call, keeps the ID token in `sessionStorage` and sends it in the `Authorization` header. Set `API_URL` (`https://<CLOUDFRONT_DOMAIN>/prod`) and `CLIENT_ID` before uploading.



![17-s3-private-bucket](screenshots/17-s3-private-bucket.png)
![17b-s3-block-public-access](screenshots/17b-s3-block-public-access.png)

### Step 12. CloudFront
Distribution `todo-web`: all edge locations, HTTP/2 and HTTP/1.1, default root object `index.html`, logging off.

| Origin | Type | Access |
|---|---|---|
| S3 bucket | S3 | Origin Access Control |
| `api-origin` | API Gateway (`<API_ID>.execute-api.eu-north-1.amazonaws.com`) | Public API address |

| Precedence | Path | Origin | Viewer protocol | Cache policy | Origin request policy |
|---|---|---|---|---|---|
| 0 | `/prod/*` | `api-origin` | HTTPS only | `Managed-CachingDisabled` | `Managed-AllViewerExceptHostHeader` |
| 1 | Default (`*`) | S3 bucket | Redirect HTTP to HTTPS | `Managed-CachingOptimized` | none |



![18-cloudfront](screenshots/18-cloudfront.png)
![32-cloudfront-api-origin](screenshots/32-cloudfront-api-origin.png)
![33-cloudfront-api-behavior](screenshots/33-cloudfront-api-behavior.png)

### Step 13. End-to-end test
Open the CloudFront address, sign in, then add, tick and delete to-dos. The browser network log shows `GET`, `PUT` and `DELETE` calls returning `200` and `POST` returning `201`, all on `/prod/todos` of the CloudFront domain.



![20-frontend-signin](screenshots/20-frontend-signin.png)
![20b-frontend-signin-filled](screenshots/20b-frontend-signin-filled.png)
![21a-frontend-todo-list](screenshots/21a-frontend-todo-list.png)
![21b-frontend-todo-list-ticked](screenshots/21b-frontend-todo-list-ticked.png)
![22-frontend-network-200](screenshots/22-frontend-network-200.png)
![34-cloudfront-api-network](screenshots/34-cloudfront-api-network.png)

### Step 14. Cognito managed login (hosted UI)
The user pool has a managed login domain with a style assigned to the app client `todo-web`. **View login page** opens the hosted sign-in form. The web page does not use it: it keeps its own form and the `InitiateAuth` call from Step 11.



![38-cognito-managed-login](screenshots/38-cognito-managed-login.png)
![39-cognito-hosted-ui](screenshots/39-cognito-hosted-ui.png)

### Step 15. DynamoDB GSI
Global secondary index `userId-createdAt-index` (partition key `userId`, sort key `createdAt`, all attributes projected, on-demand). It lets `GET /todos` return a user's to-dos newest first:

```python
result = table.query(
    IndexName="userId-createdAt-index",
    KeyConditionExpression=Key("userId").eq(user_id),
    ScanIndexForward=False,
)
```

The IAM policy now lists both `table/todos` and `table/todos/index/*`. Without the index resource, the query fails with `AccessDenied`. A test with two items returned them newest first (`21:20:26` before `21:20:21`). The console shows size 0 and item count 0 because DynamoDB refreshes those values about every 6 hours.



![40-dynamodb-gsi](screenshots/40-dynamodb-gsi.png)
![41-iam-gsi-permission](screenshots/41-iam-gsi-permission.png)
![42-lambda-gsi-query](screenshots/42-lambda-gsi-query.png)

### Step 16. DynamoDB Streams with a consumer Lambda
1. Stream enabled on `todos` with view type **new and old images**.
2. Lambda `todo-stream-processor` (`backend/stream_processor.py`) logs the action (`INSERT`, `MODIFY`, `REMOVE`) and the item keys. Its role has the basic logging policy (scoped to its own log group) and `AWSLambdaDynamoDBExecutionRole`.
3. Trigger: DynamoDB `todos`, batch size 100, no batch window, starting position `LATEST`.
4. Test: add, tick twice and delete a to-do. The logs show `INSERT`, `MODIFY`, `MODIFY`, `REMOVE` for the same `todoId`, and the trigger reports **Last processing result: OK**.



![43-dynamodb-stream-enabled](screenshots/43-dynamodb-stream-enabled.png)
![43b-stream-processor-role](screenshots/43b-stream-processor-role.png)
![44-stream-trigger](screenshots/44-stream-trigger.png)
![44b-stream-trigger-ok](screenshots/44b-stream-trigger-ok.png)
![45-stream-logs](screenshots/45-stream-logs.png)

### Step 17. WAF geo-block rule
Rule `geo-block` (priority 2, Block) matches requests that originate from a country in the list. The list was set to Tunisia (`TN`), where the tests were run.

| Test | Result |
|---|---|
| Browser in Tunisia, direct API address | `403` `{"message":"Forbidden"}` |
| CloudShell in Stockholm, same address | `401` `{"message":"Unauthorized"}` |

The different results show that the block comes from the geo rule. Over the last 3 hours WAF counted 13 requests, 2 blocked and 11 allowed. The web ACL now has three rules. A real deployment would list the countries you do not serve instead of `TN`.



![46-waf-geo-rule](screenshots/46-waf-geo-rule.png)
![46b-waf-geo-rule-json](screenshots/46b-waf-geo-rule-json.png)
![47a-waf-geo-403](screenshots/47a-waf-geo-403.png)
![47b-waf-geo-sweden-401](screenshots/47b-waf-geo-sweden-401.png)
![47c-waf-geo-blocked-count](screenshots/47c-waf-geo-blocked-count.png)
![48-waf-three-rules](screenshots/48-waf-three-rules.png)

### Step 18. Usage plan
`todo-usage-plan`: 50 requests per second, burst 100, quota 10,000 requests per month, associated with `todo-api` / `prod`.

A usage plan only applies its throttling and quota to methods that require an API key. No method has **API key required** enabled (the methods table shows "Not required"), so the plan is attached but its limits are not enforced for these requests. It was not tested with an API key. The architecture diagram should be read as showing that the usage plan exists, not that it currently limits traffic.


---


![35-usage-plan](screenshots/35-usage-plan.png)

## 5. Results against the project requirements

| Requirement | Status | Evidence |
|---|---|---|
| REST API with Cognito JWT authorizer | Done | `401` without a token, `200` with one (Step 7) |
| Cognito user pool, JWT tokens | Done | App client, test user, ID token (Steps 5, 7) |
| Cognito hosted UI | Done, not used by the app | Managed login and hosted sign-in page (Step 14) |
| Lambda CRUD, environment variable, IAM role | Done | Six passing test events (Step 4) |
| DynamoDB table design | Done | Keys, per-user isolation (Steps 1, 4) |
| DynamoDB GSI | Done | Index query newest first (Step 15) |
| DynamoDB Streams, downstream processing | Done | `INSERT`, `MODIFY`, `REMOVE` in logs (Step 16) |
| WAF rate limiting | Done | 150 blocked requests (Step 9) |
| WAF OWASP managed rules | Done | Common Rule Set attached (Step 9) |
| WAF geo blocking | Done | `403` vs `401` test (Step 17) |
| API Gateway caching | Done, then turned off | Timing test (Step 10) |
| Usage plans | Created, not enforced | Plan attached, no API key required (Step 18) |
| CloudFront in front of API Gateway | Done | `/prod/*` behavior (Step 12) |
| S3 + CloudFront static frontend | Done (plain HTML) | Private bucket with OAC (Steps 11, 12) |
| X-Ray tracing | Partial | API stage and Lambda nodes, no DynamoDB node (Step 8) |
| WAF bot protection | Not done | See limitations |

---

## 6. Limitations

| Item | Status |
|---|---|
| WAF Bot Control | Not enabled. It is the most expensive WAF option, and the "bot detection" chart in the dashboard is only a traffic sample, not protection. |
| X-Ray SDK in Lambda | Not added, so traces show no DynamoDB node. It needs `aws_xray_sdk` with `patch_all()`. |
| Usage plan enforcement | The plan is attached but no method requires an API key. |
| Geo-block country list | Left on `TN` from the test. Use your real list in production. |
| Frontend | Plain HTML/JS instead of React or Vue. |
| Edge caching for the API | Disabled on purpose (authenticated, per-user data). |
| Stream trigger error handling | Retries are unlimited and no on-failure destination is set. In production, set a maximum retry age and an on-failure destination so a bad record cannot block a shard. |
| `PUT` without `title` | Overwrites the title with an empty string. The web page always sends a title. |

## 7. Security notes

- The ID token is kept in `sessionStorage`, which scripts on the page can read. Serve only trusted code, and prefer short token lifetimes.
- The API returns `Access-Control-Allow-Origin: *`. Restrict it to the CloudFront domain for production.
- The API address can be called directly, bypassing CloudFront. WAF on the stage still applies.
- `USER_PASSWORD_AUTH` sends the password to Cognito over HTTPS. SRP or the hosted UI avoids sending it from your own code.
- Do not commit tokens, account IDs, API IDs, client IDs or user IDs.

## 8. Costs

Lambda, DynamoDB (on-demand), Cognito, S3 and CloudFront cost almost nothing at this scale. These do not:

- **WAF** is billed per web ACL, per rule and per request, and keeps billing while it exists.
- The **API Gateway cache cluster** is billed hourly while provisioned.
- **WAF Bot Control** would add much more and was skipped.

## 9. Cleanup

Delete in this order to stop charges (WAF bills by the hour):

1. WAF: remove the association from the API stage, then delete `todo-api-waf`.
2. CloudFront: disable `todo-web`, then delete it.
3. API Gateway: delete `todo-api` and `todo-usage-plan`.
4. Lambda: delete `todo-api` and `todo-stream-processor`, then their log groups.
5. DynamoDB: delete `todos` (this removes the GSI and the stream).
6. Cognito: delete the user pool (and its domain).
7. S3: empty the bucket, then delete it.
8. CloudWatch: delete the alarm `todo-api-errors`.
9. IAM: delete `todo-lambda-role`, the stream consumer role and the API Gateway logging role.

## License

See `LICENSE`.