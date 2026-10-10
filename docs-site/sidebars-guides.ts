import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars = {
  guidesSidebar: [
    'overview',
    {
      type: 'category',
      label: 'Getting Started',
      items: [
        'getting-started/quick-start',
        'getting-started/docker',
        'getting-started/local-dev',
        'getting-started/configuration',
      ],
    },
    {
      type: 'category',
      label: 'Agents & Subagents',
      items: [
        'agents/creating-agents',
        'agents/spawning-subagents',
        'agents/agent-communication',
        'agents/agent-profiles',
        'agents/agent-monitoring',
      ],
    },
    {
      type: 'category',
      label: 'Tools & Skills',
      items: [
        'tools/built-in-tools',
        'tools/custom-tools',
        'tools/mcp-integration',
        'skills/creating-skills',
        'skills/skill-management',
        'skills/skill-testing',
      ],
    },
    {
      type: 'category',
      label: 'Memory & Context',
      items: [
        'memory/working-with-memory',
        'memory/context-window',
        'memory/summarization',
        'memory/cognitive-memory',
      ],
    },
    {
      type: 'category',
      label: 'Sandbox & Execution',
      items: [
        'sandbox/overview',
        'sandbox/docker',
        'sandbox/aio',
        'sandbox/e2b',
        'sandbox/custom-sandboxes',
      ],
    },
    {
      type: 'category',
      label: 'Goals & Planning',
      items: [
        'goals/creating-goals',
        'goals/goal-decomposition',
        'goals/monitoring-progress',
        'goals/goal-verification',
      ],
    },
    {
      type: 'category',
      label: 'Workflows & Swarms',
      items: [
        'swarms/creating-swarms',
        'swarms/swarm-patterns',
        'swarms/swarm-monitoring',
        'workflows/creating-workflows',
        'workflows/workflow-patterns',
      ],
    },
    {
      type: 'category',
      label: 'Memory & Context',
      items: [
        'memory/working-with-memory',
        'memory/context-window',
        'memory/summarization',
        'memory/cognitive-memory',
      ],
    },
    {
      type: 'category',
      label: 'Deployment & Operations',
      items: [
        'deployment/docker',
        'deployment/kubernetes',
        'deployment/monitoring',
        'deployment/scaling',
        'deployment/backup-restore',
      ],
    },
    {
      type: 'category',
      label: 'Security & Compliance',
      items: [
        'security/authentication',
        'security/authorization',
        'security/sandbox-hardening',
        'security/audit-logging',
        'security/compliance',
      ],
    },
    {
      type: 'category',
      label: 'Advanced Topics',
      items: [
        'advanced/custom-agents',
        'advanced/custom-tools',
        'advanced/custom-skills',
        'advanced/extensions',
        'advanced/performance-tuning',
      ],
    },
  ],
};

export default sidebars;