# generate_pie_chart — Pie / Donut Chart

## Overview
Shows proportional composition of categories relative to a 100% whole.

## Input Fields
### Required
- data: array<object>, items with category (string) and alue (number).

### Optional
- innerRadius: number, value between 0.0 and 1.0 (creates donut chart if > 0).
- 	heme: string, default default.
- 	itle: string, pie chart title.

## Usage Recommendations
Limit categories to 6 or fewer; group minor slices into an 'Other' category.

## Output
- Returns pie chart image URL and spec in _meta.spec.
