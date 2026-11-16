# Weekly Cloud Cost Report: 2026-11-09 to 2026-11-15

Compared with 2026-11-02 to 2026-11-08. All figures in USD. Providers: AWS, Azure, GCP.

## Headlines

- **Total spend was $66,842**, up $3,210 (+5.0%) versus the prior 7 days ($63,632).
- Largest cost driver: **AWS · Amazon Elastic Compute Cloud - Compute** at $11,624 (17% of spend).
- Biggest increase: **GCP · BigQuery**, ▲ $2,353 (+47.9%).
- **1 anomaly flagged.** Largest: BigQuery on 2026-11-12 at $2,987 vs ~$703 typical.
- Spend with no `team` tag: 5.9% ($3,977).
- Month-to-date $138,963; naive projection to month end **$277,925**.
- **Azure is projected $6,617 over budget** ($86,617 vs $80,000).

## Spend by cloud

| Cloud | This week | Prior week | Change | % | Share |
| --- | ---: | ---: | ---: | ---: | ---: |
| AWS | $27,769 | $27,605 | ▲ $164 | +0.6% | 41.5% |
| Azure | $20,459 | $20,140 | ▲ $319 | +1.6% | 30.6% |
| GCP | $18,614 | $15,887 | ▲ $2,727 | +17.2% | 27.8% |

## Spend by category

| Category | This week | Prior week | Change | % | Share |
| --- | ---: | ---: | ---: | ---: | ---: |
| Compute | $27,839 | $27,478 | ▲ $360 | +1.3% | 41.6% |
| Database | $14,594 | $14,615 | ▼ $21.23 | -0.1% | 21.8% |
| Analytics | $7,264 | $4,911 | ▲ $2,353 | +47.9% | 10.9% |
| Storage | $7,135 | $7,043 | ▲ $92.40 | +1.3% | 10.7% |
| Containers | $5,230 | $5,163 | ▲ $66.65 | +1.3% | 7.8% |
| AI/ML | $3,657 | $3,372 | ▲ $286 | +8.5% | 5.5% |
| Monitoring & Security | $1,123 | $1,050 | ▲ $73.53 | +7.0% | 1.7% |

Categories group similar services across clouds. The mapping is editable (`service_categories.toml`).

## Top cost drivers: services (top 10 of 16)

| Service | This week | Prior week | Change | % | Share |
| --- | ---: | ---: | ---: | ---: | ---: |
| AWS · Amazon Elastic Compute Cloud - Compute | $11,624 | $11,642 | ▼ $18.82 | -0.2% | 17.4% |
| Azure · Virtual Machines | $7,318 | $7,075 | ▲ $243 | +3.4% | 10.9% |
| GCP · BigQuery | $7,264 | $4,911 | ▲ $2,353 | +47.9% | 10.9% |
| AWS · Amazon Relational Database Service | $6,860 | $6,859 | ▲ $1.52 | +0.0% | 10.3% |
| GCP · Compute Engine | $5,119 | $5,025 | ▲ $93.96 | +1.9% | 7.7% |
| Azure · Azure Cosmos DB | $4,907 | $4,886 | ▲ $20.90 | +0.4% | 7.3% |
| GCP · Vertex AI | $3,657 | $3,372 | ▲ $286 | +8.5% | 5.5% |
| AWS · Amazon Simple Storage Service | $3,548 | $3,463 | ▲ $85.09 | +2.5% | 5.3% |
| Azure · Azure Kubernetes Service | $3,412 | $3,353 | ▲ $59.03 | +1.8% | 5.1% |
| Azure · Azure SQL Database | $2,826 | $2,870 | ▼ $43.66 | -1.5% | 4.2% |
| Other (6) | $10,306 | $10,176 | ▲ $130 | +1.3% | 15.4% |

## Top cost drivers: by `team`

| `team` | This week | Prior week | Change | % | Share |
| --- | ---: | ---: | ---: | ---: | ---: |
| platform | $21,800 | $20,832 | ▲ $968 | +4.6% | 32.6% |
| data | $15,635 | $14,885 | ▲ $750 | +5.0% | 23.4% |
| web | $12,686 | $12,167 | ▲ $520 | +4.3% | 19.0% |
| ml | $7,656 | $7,128 | ▲ $528 | +7.4% | 11.5% |
| security | $5,088 | $4,823 | ▲ $265 | +5.5% | 7.6% |
| (untagged) | $3,977 | $3,797 | ▲ $180 | +4.7% | 5.9% |

## Biggest movers

Changes of at least $100 and 5% versus the prior week.

**Increases**

| Service | Prior week | This week | Change | % |
| --- | ---: | ---: | ---: | ---: |
| GCP · BigQuery | $4,911 | $7,264 | ▲ $2,353 | +47.9% |
| GCP · Vertex AI | $3,372 | $3,657 | ▲ $286 | +8.5% |

**Decreases**

None.

## Anomalies

Days at least 3 standard deviations above the trailing 14-day baseline for that service.

| Date | Service | Cost | Typical | Excess |
| --- | --- | ---: | ---: | ---: |
| 2026-11-12 | BigQuery (GCP) | $2,987 | $703 | ▲ $2,284 |

## Month to date

- Spend 2026-11-01 to 2026-11-15: **$138,963** over 15 days of data.
- Naive run-rate projection for the full 30-day month: **$277,925**.
- The projection is linear and ignores weekday patterns, commitments and known changes.

## Budget

| Cloud | Monthly budget | Month to date | Used | Projected | vs budget | Status |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| AWS | $125,000 | $59,006 | 47% | $118,011 | ▼ $6,989 | On track |
| Azure | $80,000 | $43,309 | 54% | $86,617 | ▲ $6,617 | **Over** |
| GCP | $75,000 | $36,648 | 49% | $73,297 | ▼ $1,703 | On track |

## Data quality

- Untagged (`team`): $3,977, 5.9% of spend.
- All 7 days present.
- Recent days can still be revised by the clouds (billing lag up to ~24h or more).
