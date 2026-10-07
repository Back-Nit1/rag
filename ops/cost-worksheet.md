# Cost worksheet

**Every price below is an ASSUMPTION to be checked against the region's rate card on the deploy date.** Nothing here is measured. Prices are config, not facts (`docs/spec/cost.md`). Scenario = recordings per MONTH.

## Assumptions

| Item | Assumed value | Status |
| --- | --- | --- |
| Recording length | 60 s average (cap 300 s / 25 MiB) | assumption |
| ASR task billed time | 90 s per recording (image pull + model load + decode, one-minute minimum) | UNMEASURED; benchmark cold start and RTF |
| Fargate Linux x86 | $0.04048 per vCPU-hour, $0.004445 per GB-hour; 2 vCPU / 4 GiB = $0.0987/h | assumption (us-east-1 list) |
| Extraction | 1 call, 1,500 in / 300 out tokens, Nova Micro $0.035 / $0.14 per 1M | assumption; matches `METIS_INPUT/OUTPUT_MICRO_USD_PER_1K` defaults |
| Embeddings | 2 chunks x 400 tokens per recording, Titan v2 $0.02 per 1M | assumption |
| Daily plan | 1/day, 6,000 in / 1,500 out, Nova Lite $0.06 / $0.24 per 1M | assumption |
| Step Functions Standard | 6 transitions per recording, $0.025 per 1,000 | assumption |
| Interface VPC endpoints | 3 (ECR api, ECR docker, Logs) x 1 AZ x $0.01/h x 730 h = $21.90; prod uses 2 AZ = $43.80 | assumption; data processing fees excluded |
| KMS customer key | $1/month plus request charges | assumption |
| Logs, DynamoDB on-demand, S3, S3 Vectors | small allowances shown below | rough; excludes free tier |
| Reminders | zero model calls; Lambda and Scheduler within free-tier-scale volumes | assumption |
| Not included | CloudFront/web hosting (not in these stacks), data transfer out, Cognito beyond free tier, S3 Vectors storage/query at scale, Cognito Plus plan | excluded |

## Scenarios (USD per month, 1 AZ dev layout)

| Component | 30 | 300 | 600 |
| --- | --- | --- | --- |
| ASR Fargate (90 s each) | 0.07 | 0.74 | 1.48 |
| Extraction + embeddings | 0.00 | 0.03 | 0.06 |
| Daily plans (30/month) | 0.02 | 0.02 | 0.02 |
| Step Functions | 0.00 | 0.05 | 0.09 |
| Logs / DynamoDB / S3 allowance | 0.91 | 1.39 | 1.93 |
| **Variable subtotal** | **~1.0** | **~2.2** | **~3.6** |
| Interface endpoints (3, 1 AZ) | 21.90 | 21.90 | 21.90 |
| KMS key | 1.00 | 1.00 | 1.00 |
| **Estimated total** | **~24** | **~25** | **~26** |

(Earlier draft of the network stack had 5 endpoints = $36.50; two the ASR task never calls were removed.)

## What this says

- Fixed network cost (endpoints) is roughly 90% of the bill at every scenario. Per-recording ASR cost is about $0.0025 at the assumed timings, so self-hosting is cheap per recording and expensive in fixed plumbing.
- The default `metis:monthlyBudgetUsd` is 25. The estimate is at or above that in every scenario, so the budget alarm will fire. Either raise the budget (suggest 40) or remove the endpoints by running the task in a public subnet with a public IPv4 (billed per running hour) and locked-down egress. That is a design decision for the coordinator; it changes the network stack.
- A warm EC2 ASR service is not implemented (`warmSpeechEc2` is rejected by validation). Compare only after measuring cold start.
- The application budget (`METIS_DAILY_MICRO_USD`, `METIS_MONTHLY_MICRO_USD`) caps model usage only. The AWS Budget alarm is a delayed alert; neither caps infrastructure cost.

## To replace assumptions with measurements - UNRUN

1. Cold and warm ASR: task start-to-ready, model load, RTF on real browser-format recordings.
2. Actual token counts per extraction/plan from the per-operation usage records.
3. Cost Explorer / CUR by tag `app=metis` after one week.
