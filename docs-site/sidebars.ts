import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  tutorialSidebar: [
    'intro',
    {
      type: 'category',
      label: 'Getting Started',
      collapsed: false,
      items: [
        'getting-started/quick-start',
        'getting-started/installation',
        'getting-started/configuration',
        'getting-started/first-agent',
        'getting-started/deployment',
      ],
    },
    {
      type: 'category',
      label: 'Core Concepts',
      collapsed: false,
      items: [
        'core-concepts/architecture',
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
      label: 'Configuration',
      collapsed: true,
      items: [
        'configuration/overview',
        'configuration/models',
        'configuration/tools',
        'configuration/memory',
        'configuration/sandbox',
        'configuration/mcp',
        'configuration/skills',
        'configuration/extensions',
        'configuration/autonomy',
        'configuration/network',
      ],
    },
    {
      type: 'category',
      label: 'Interfaces',
      collapsed: true,
      items: [
        'interfaces/web-ui',
        'interfaces/cli',
        'interfaces/tui',
        'interfaces/api',
        'interfaces/im-bridges',
      ],
    },
  ],
};

export default sidebars;