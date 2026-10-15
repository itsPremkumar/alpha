import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

/**
 * The main documentation sidebar.
 *
 * Every entry here must resolve to a real file under `docs/`. This sidebar
 * previously listed ~29 documents that had never been written, which fails the
 * build outright: Docusaurus throws on a sidebar id with no document behind it.
 * Add the document first, then the entry.
 */
const sidebars: SidebarsConfig = {
  tutorialSidebar: [
    'intro',
    {
      type: 'category',
      label: 'Getting Started',
      collapsed: false,
      items: [
        'getting-started/installation',
        'getting-started/quick-start',
        'getting-started/configuration',
        'getting-started/first-agent',
      ],
    },
    {
      type: 'category',
      label: 'Core Concepts',
      collapsed: false,
      items: ['architecture', 'deployment', 'security'],
    },
    {
      type: 'category',
      label: 'Project',
      items: ['governance', 'community', 'roadmap', 'changelog'],
    },
  ],
};

export default sidebars;
