# generate_mind_map — Mind Map

## Overview
Visualizes hierarchical concepts, brainstorming branches, and taxonomic breakdowns centered around a core root node.

## Input Fields
### Required
- data: object, nested node structure with id, label, and children.

### Optional
- theme: string, default default.
- title: string, mind map title.

## Usage Recommendations
Limit branching depth to 3-4 tiers to keep diagram legible.

## Output
- Returns mind map image URL and spec in _meta.spec.
