import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars = {
  guidesSidebar: [
    'overview',
    {
      type: 'category',
      label: 'Getting Started',
      items: [
        'getting-started/quick-start',
        'getting-started/installation',
        'getting-started/configuration',
        'getting-started/first-agent',
      ],
    },
    {
      type: 'category',
      label: 'Core Concepts',
      collapsed: false,
      items: [
        'architecture',
        'core-concepts/agents',
        'core-concepts/tools',
        'core-concepts/memory',
        'core-concepts/sandbox',
        'core-concepts/subagents',
        'core-concepts/swarms',
        'core-concepts/goals',
        'core-concepts/workflows',
      ],
    },
    {
      type: 'category',
      label: 'Guides',
      items: [
        {
          type: 'category',
          label: 'Agents',
          items: [
            'guides/agents/overview',
            'guides/agents/creating-agents',
            'guides/agents/spawning-subagents',
            'guides/agents/agent-communication',
          ],
        },
        {
          type: 'category',
          label: 'Tools',
          items: [
            'guides/tools/built-in-tools',
            'guides/tools/custom-tools',
            'guides/tools/mcp-integration',
          ],
        },
        {
          type: 'category',
          label: 'Memory',
          items: [
            'guides/memory/working-with-memory',
            'guides/memory/context-window',
            'guides/memory/summarization',
          ],
        },
        {
          type: 'category',
          label: 'Sandbox',
          items: [
            'guides/sandbox/overview',
            'guides/sandbox/docker',
            'guides/sandbox/aio',
            'guides/sandbox/e2b',
            'guides/sandbox/security',
          ],
        },
        {
          type: 'category',
          label: 'Goals & Workflows',
          items: [
            'guides/goals/overview',
            'guides/goals/creating-goals',
            'guides/goals/verification',
            'guides/goals/subgoals',
            'guides/workflows/creating-workflows',
            'guides/workflows/workflow-patterns',
          ],
        },
        {
          type: 'category',
          label: 'Skills',
          items: [
            'guides/skills/overview',
            'guides/skills/creating-skills',
            'guides/skills/marketplace',
            'guides/skills/testing',
          ],
        },
        {
          type: 'category',
          label: 'Sandbox',
          items: [
            'guides/sandbox/overview',
            'guides/sandbox/docker',
            'guides/sandbox/aio',
            'guides/sandbox/e2b',
            'guides/sandbox/security',
          ],
        },
        {
          type: 'category',
          label: 'Deployment',
          items: [
            'deployment',
            'guides/deployment/docker',
            'guides/deployment/kubernetes',
            'guides/deployment/monitoring',
          ],
        },
        {
          type: 'category',
          label: 'Integrations',
          items: [
            'integrations/github',
            'integrations/slack',
            'integrations/jira',
            'integrations/custom-api',
          ],
        },
      ],
    },
  ],
};

export default sidebars;