# generate_histogram_chart — Histogram Chart

## Overview
Groups continuous data into discrete bins to display frequency distributions and statistical density.

## Input Fields
### Required
- data: array<number> or array<object>, continuous metric values.

### Optional
- binNumber: number, count of discrete bins.
- theme: string, default default.
- title: string, chart title.

## Usage Recommendations
Select appropriate bin widths to highlight underlying normal or skewed distributions.

## Output
- Returns histogram image URL and spec in _meta.spec.
