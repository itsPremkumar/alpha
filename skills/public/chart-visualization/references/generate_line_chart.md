# generate_line_chart — Line Chart

## Overview
Plots continuous data points connected by straight segments, ideal for time series, trends, and continuous monitoring.

## Input Fields
### Required
- data: array<object>, items containing time or x (string) and value (number).

### Optional
- series: string, field separating multiple distinct lines.
- theme: string, default default.
- width: number, default 600.
- height: number, default 400.
- title: string, chart title.

## Usage Recommendations
Avoid plotting more than 5 lines on a single chart to preserve legibility.

## Output
- Returns line chart image URL and spec in _meta.spec.
