# generate_radar_chart — Radar Chart

## Overview
Compares multidimensional metrics across several categories or profiles on a spiderweb grid.

## Input Fields
### Required
- data: array<object>, items with dimension (string), alue (number), and group (string).

### Optional
- 	heme: string, default default.
- 	itle: string, radar chart title.

## Usage Recommendations
Normalize disparate metrics to a shared 0-100 scale for meaningful profile comparison.

## Output
- Returns radar chart image URL and spec in _meta.spec.
