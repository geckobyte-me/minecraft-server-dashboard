# AGENT.md

Guidance for AI agents working in the Minecraft Server Dashboard repository.

## What This Project Is

A serverless web application for managing and monitoring Minecraft servers hosted on AWS EC2. Users authenticate via Google OAuth (Cognito), discover servers they have access to, start/stop them, view live metrics (CPU, memory, network), track uptime history, and configure automated shutdown policies (CPU-based, network-based, and scheduled).

The user is responsible for provisioning the EC2 instance and installing Minecraft and the CloudWatch agent themselves. This app does not provision servers or install packages — it controls access, start/stop, monitoring, and auto-shutdown.

**Stack**: Vue 3 + Vuetify frontend, AWS AppSync (GraphQL) API, Python Lambda functions, DynamoDB, Cognito, CloudFront.

## Repository Layout

```
webapp/      Vue.js frontend (Vite, Vuetify, Amplify SDK)
cfn/         AWS SAM CloudFormation templates (nested stacks)
lambdas/     Python Lambda function source (one dir per function)
layers/      Shared Lambda layer code (authHelper, ddbHelper, ec2Helper, utilHelper)
appsync/     GraphQL schema (schema.graphql) and JS resolvers
cloudwatch/  CloudWatch agent config (reference artifact)
images/      Screenshots and diagrams
```

## Common Commands

### Frontend (`webapp/`)
```bash
cd webapp
npm install        # install deps
npm run dev        # dev server with hot reload
npm run build      # production build
npm run preview    # preview production build
```

### Infrastructure (`cfn/`)
```bash
cd cfn
sam validate --lint   # validate/lint templates
sam build             # package Lambdas and layers
sam deploy            # deploy (use --guided first time)
```

### Lambda layers
```bash
cd layers/<layerName> && make   # installs requirements.txt into python/, SAM packages as layer
```

## Code Conventions

- **Vue components**: PascalCase filenames (`ServerCard.vue`). Use Vue 3 Composition API. ES6+, async/await.
- **Python (Lambdas/layers)**: snake_case files (`index.py`), docstrings, try/except error handling. Lambdas push status updates to AppSync for real-time UI feedback.
- **Lambda function dirs**: camelCase (`ec2Discovery/`), each with `index.py` (+ `requirements.txt` when it has pip dependencies).
- **CloudFormation**: kebab-case resource names, PascalCase types.
- **Frontend config**: environment variables prefixed `VITE_` (see `webapp/.env`).
- **GraphQL**: keep queries, mutations, subscriptions in separate files under `webapp/src/graphql/`.
- **Data separation**: server config and user data use different queries. Validate/format data at boundaries (e.g. cron expressions at backend entry).

## Architecture Essentials

### CloudFormation nested stacks (`cfn/template.yaml`)
Deploy order: **DynamoDB → Cognito → EC2 → Lambdas**. Each nested template lives in `cfn/templates/` (`dynamodb.yaml`, `cognito.yaml`, `ec2.yaml`, `lambdas.yaml`, `web.yaml`). Lambda functions and layers are both declared in `cfn/templates/lambdas.yaml`.

### Server control (synchronous start/stop)
`ec2ActionValidator` is the single AppSync-backed Lambda for writes and most reads. It validates Cognito auth and per-server membership, then executes **synchronously** — there is no SQS queue or separate worker. Flow:
- `startServer` / `stopServer` → validate state → `ec2:StartInstances` / `ec2:StopInstances` → broadcast progress via `putec2ActionValidatorStatus` (PROCESSING → COMPLETED/FAILED), which the frontend receives over the `onPutec2ActionValidatorStatus` subscription.
- `putServerConfig` / `updateServerConfig` → persist config to DynamoDB, then apply the auto-shutdown alarm and EventBridge schedule rules.

Scheduled EventBridge rules invoke `ec2ActionValidator` directly (not GraphQL) with `{"action": "startServer"|"stopServer", "instanceId", "source": "scheduled-..."}`; the handler detects this shape and runs as `system`.

> IAM profile management (`iamProfileManager`) is a separate synchronous Lambda. It requires `iam:PassRole` on the EC2 **role** ARN (not the instance profile ARN) and associates the instance profile so the CloudWatch agent can publish metrics.

### Discovery
`ec2Discovery` lists EC2 instances the caller has membership for (admins see all instances with the `App` tag), enriches each with type/specs/state/public IP, and auto-fixes the IAM profile if needed. It returns only the trimmed `ServerInfo` fields (no bootstrap/validation/cost fields).

### Auto-shutdown policies
A single CloudWatch metric alarm with the native `ec2:stop` action (no Lambda in the shutdown path). Configured in `layers/ec2Helper/ec2Helper.py` (`update_alarm` / `remove_alarm`), keyed by `{instanceId}-minecraft-server`. The `alarmMetric` config field selects the mode:
- **CPUUtilization** (default): `cpu_usage_active` from the `CWAgent` namespace (requires the CloudWatch agent the user installs).
- **NetworkOut**: `NetworkOut` from the native `AWS/EC2` namespace (no agent required).

The alarm fires when the metric stays `<=` threshold for `alarmEvaluationPeriod` consecutive minutes. A threshold of 0 removes the alarm.

- **Scheduled start/stop**: EventBridge rules (`start-{id}` / `shutdown-{id}`). Frontend sends 5-field cron; `ec2ActionValidator._format_schedule_expression` converts it to EventBridge 6-field cron (and local→UTC via `timezone`).

### Metrics (option B — CloudWatch agent)
Dashboard stats come from the standard CloudWatch agent the user installs:
- CPU: `cpu_usage_active` (`CWAgent`), Memory: `mem_used_percent` (`CWAgent`), Network: `NetworkOut` (`AWS/EC2`).
- `ec2MetricsHandler` serves historical metrics on demand; `ec2StateHandler` publishes periodic metrics and real-time state over subscriptions. There are no player/connection-count metrics.

### Uptime tracking
`ec2StateHandler` is triggered by the `EC2 Instance State-change Notification` EventBridge rule, so it captures **every** start/stop regardless of origin (app, console, CLI, alarm, schedule). On each `running`/`stopped` transition it writes a row to `LogAuditTable` namespaced `action="state:<state>"` with the event's own timestamp (`ddbHelper.LogAuditTable.log_state_transition`). The `getServerUptime(instanceId, days)` query (handled by `ec2ActionValidator`) reads these rows and computes current-session seconds and total running seconds over the window. Non-app transitions are attributed `"external"` (the raw EC2 event carries no user identity).

## GraphQL Surface (`appsync/schema.graphql`)

- **Types**: `ServerInfo`, `ServerMetric`, `ServerConfig`, `ServerUsers`, `UserMembership`, `LogAudit`, `ServerStateEvent`, `ServerUptime`, `ec2ActionValidatorStatus`.
- **Queries**: `ec2Discovery`, `getServerConfig`, `getServerUsers`, `getAdminUsers`, `searchUserByEmail`, `getLogAudit`, `getServerUptime`, `ec2MetricsHandler`.
- **Mutations**: `startServer`, `stopServer`, `putServerConfig`, `updateServerConfig`, `addUserToServer`, `updateUserRole`, `removeUserFromServer`, `iamProfileManager`, `verifyUserEmail`, `putLogAudit`, `putServerMetric`, `changeServerState`, `putec2ActionValidatorStatus`.
- **Subscriptions**: `onPutServerMetric`, `onChangeState`, `onPutec2ActionValidatorStatus`.

Secured with `@aws_cognito_user_pools` and `@aws_iam`. Admin group has full access; regular users see servers they are members of.

### Lambda inventory (`lambdas/`)
- `ec2ActionValidator` — AppSync entry point for start/stop, config, user management, and uptime; runs actions synchronously.
- `ec2Discovery` — lists accessible servers with specs/IP/state.
- `ec2MetricsHandler` — historical CloudWatch metrics for a server.
- `ec2StateHandler` — EventBridge-driven: real-time state push, periodic metrics, and uptime history logging.
- `iamProfileManager` — associates/disassociates the EC2 instance profile.

## Data Model

Two DynamoDB tables (`cfn/templates/dynamodb.yaml`):
- **CoreTable** — single-table design, `PK`/`SK` with a `SK-PK-index` GSI. Holds server metadata/config (`PK: SERVER#{id}`, `SK: METADATA`) and user membership records (`PK: USER#{sub}`, `SK: SERVER#{id}`). Prefer Query over Scan.
- **LogAudit** — key `id` (instanceId, HASH) + `timestamp` (epoch, RANGE). Holds app action audit rows and `state:*` transition rows used for uptime.

## Deployment (CircleCI)

- **dev-pipeline**: all branches except `main`, uses `dev-aws` context.
- **prod-pipeline**: `main` only, uses `prod-aws` context.

Backend job (`cimg/python:3.13`): validate templates → `sam build` → `sam deploy` → upload the CloudWatch agent config to S3.
Frontend job (`cimg/node:20.18`, requires backend): generate `.env` from stack outputs (`support_tasks/create-env-from-cfn-stack.sh`) → `npm ci` → `npm run build` → upload `dist/` to S3 → invalidate CloudFront.

## Agent Working Notes

- Lambda functions and layers are both defined in `cfn/templates/lambdas.yaml`; update CloudFormation when adding either.
- When changing a GraphQL operation, keep `appsync/schema.graphql`, the resolver wiring in `lambdas.yaml`, the Lambda handler, and the frontend `webapp/src/graphql/` files in sync. AppSync rejects response objects containing fields not in the schema — trim Lambda/`ddbHelper` return shapes to match.
- Start/stop/config run synchronously in `ec2ActionValidator`; there is no SQS worker. Scheduled rules invoke the same Lambda directly.
- `sam build` in some local environments fails because the layer Makefiles call `/usr/local/bin/python3.13`; CI provides that symlink. Template validation and `py_compile` are reliable local checks.
- Do not commit secrets. Sensitive config belongs in SSM Parameter Store; frontend values come from CloudFormation outputs via `VITE_`-prefixed env vars.
- Treat production deploys and destructive AWS operations as high-risk — confirm before acting.
- Don't add tests unless explicitly requested.
