# generate_network_graph — Network Graph

## Overview
Visualizes relationships, node clustering, and topological linkages across complex relational datasets.

## Input Fields
### Required
- 
odes: array<object>, list of entities with id and label.
- edges: array<object>, connections with source and target.

### Optional
- theme: string, default default.
- title: string, graph title.

## Usage Recommendations
Use node coloring and edge thickness to encode community groupings and connection weights.

## Output
- Returns network graph image URL and spec in _meta.spec.
