# generate_sankey_chart — Sankey Diagram

## Overview
Visualizes flow volumes, energy transfer, or budget allocation from source stages to destination buckets.

## Input Fields
### Required
- 
odes: array<object>, stage names and categories.
- links: array<object>, flows with source, 	arget, and alue.

### Optional
- 	heme: string, default default.
- 	itle: string, sankey title.

## Usage Recommendations
Ensure flow conservation across intermediate stages where applicable.

## Output
- Returns sankey diagram image URL and spec in _meta.spec.
