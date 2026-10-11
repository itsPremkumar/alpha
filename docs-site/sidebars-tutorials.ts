import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars = {
  tutorialsSidebar: [
    'overview',
    {
      type: 'category',
      label: 'Beginner Tutorials',
      items: [
        'beginner/hello-world',
        'beginner/first-agent',
        'beginner/first-subagent',
        'beginner/first-swarm',
        'beginner/first-goal',
      ],
    },
    {
      type: 'category',
      label: 'Intermediate Tutorials',
      items: [
        'intermediate/custom-tool',
        'intermediate/custom-skill',
        'intermediate/custom-agent',
        'intermediate/mcp-integration',
        'intermediate/memory-usage',
      ],
    },
    {
      type: 'category',
      label: 'Advanced Tutorials',
      items: [
        'advanced/custom-sandbox',
        'advanced/custom-tool',
        'advanced/multi-agent-workflow',
        'advanced/autonomous-goal',
        'advanced/swarm-orchestration',
      ],
    },
    {
      type: 'category',
      label: 'Integration Tutorials',
      items: [
        'integrations/github',
        'integrations/slack',
        'integrations/jira',
        'integrations/custom-api',
      ],
    },
    {
      type: 'category',
      label: 'Deployment Tutorials',
      items: [
        'deployment/local',
        'deployment/docker',
        'deployment/kubernetes',
        'deployment/monitoring',
      ],
    },
    {
      type: 'category',
      label: 'Best Practices',
      items: [
        'best-practices/agent-design',
        'best-practices/tool-design',
        'best-practices/memory-management',
        'best-practices/error-handling',
        'best-practices/testing',
      ],
    },
  ],
};

export default sidebars;
