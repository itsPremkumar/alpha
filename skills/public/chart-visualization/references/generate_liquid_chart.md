# generate_liquid_chart — Liquid Gauge Chart

## Overview
Displays progress towards a percentage goal or capacity quota as an animated liquid fill level.

## Input Fields
### Required
- value: number, percentage value between 0.0 and 1.0.

### Optional
- theme: string, default default.
- title: string, gauge title.

## Usage Recommendations
Best suited for single KPI completion targets or resource capacity meters.

## Output
- Returns liquid chart image URL and spec in _meta.spec.
