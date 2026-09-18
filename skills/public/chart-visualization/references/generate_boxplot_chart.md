# generate_boxplot_chart — Box Plot Chart

## Overview
Visualizes data distribution (medians, quartiles, outliers) across categories, suitable for statistical comparisons and variance analysis.

## Input Fields
### Required
- data: array<object>, elements containing category (string) and statistical values (low, q1, median, q3, high).

### Optional
- 	heme: string, default default, options: default/cademy/dark.
- width: number, default 600, chart width.
- height: number, default 400, chart height.
- 	itle: string, default empty string, chart title.
- xisXTitle: string, default empty string, X-axis title.
- xisYTitle: string, default empty string, Y-axis title.

## Usage Recommendations
Useful for identifying anomalies, skewness, and spread across disparate experimental or financial cohorts.

## Output
- Returns boxplot chart image URL with configuration in _meta.spec.
