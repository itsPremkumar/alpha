# generate_word_cloud_chart — Word Cloud

## Overview
Renders text frequency visualizations where word font size corresponds to frequency or weight in a corpus.

## Input Fields
### Required
- data: array<object>, items with word (string) and weight (number).

### Optional
- 	heme: string, default default.
- 	itle: string, word cloud title.

## Usage Recommendations
Filter out common stop words before generating word cloud data.

## Output
- Returns word cloud image URL and spec in _meta.spec.
