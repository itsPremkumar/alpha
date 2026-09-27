# generate_flow_diagram — Flow Diagram

## Overview
Renders procedural flowcharts and sequence workflows illustrating decision nodes and process steps.

## Input Fields
### Required
- 
odes: array<object>, list of step identifiers and labels.
- edges: array<object>, transitions connecting source and target nodes.

### Optional
- theme: string, default default.
- direction: string, TB (top-to-bottom) or LR (left-to-right).
- title: string, flowchart title.

## Usage Recommendations
Maintain clear directional flow without circular deadlocks.

## Output
- Returns flow diagram image URL and spec in _meta.spec.
