# generate_funnel_chart — Funnel Chart

## Overview
Illustrates conversion stages and attrition rates across multi-step processes (sales pipelines, user onboarding, signups).

## Input Fields
### Required
- data: array<object>, items with stage (string) and value (number).

### Optional
- theme: string, default default.
- width: number, default 600.
- height: number, default 400.
- title: string, funnel title.

## Usage Recommendations
Order data stages sequentially from top-of-funnel to conversion goal.

## Output
- Returns funnel chart image URL and spec in _meta.spec.
