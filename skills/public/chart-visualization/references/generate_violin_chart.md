# generate_violin_chart — Violin Plot

## Overview
Combines box plots with kernel density estimation curves to show probability density across cohorts.

## Input Fields
### Required
- data: array<object>, records with category (string) and metric distributions.

### Optional
- 	heme: string, default default.
- 	itle: string, violin plot title.

## Usage Recommendations
Use when understanding multi-modal distributions is critical.

## Output
- Returns violin plot image URL and spec in _meta.spec.
