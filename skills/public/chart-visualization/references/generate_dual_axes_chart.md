# generate_dual_axes_chart — Dual Axes Chart

## Overview
Visualizes two distinct metrics with different units (e.g. revenue and growth percentage) over a shared timeline using dual Y-axes.

## Input Fields
### Required
- data: array<object>, records with time (string), valueLeft (number), and valueRight (number).

### Optional
- leftAxisTitle: string, title for left Y-axis.
- 
ightAxisTitle: string, title for right Y-axis.
- theme: string, default default.
- width: number, default 600.
- height: number, default 400.
- title: string, default empty string.

## Usage Recommendations
Clearly differentiate series colors and axis scales to avoid visual confusion.

## Output
- Returns dual axes chart image URL and spec in _meta.spec.
