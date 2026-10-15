import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

/**
 * The guides sidebar (`docs/guides/`).
 *
 * It listed 64 ids against 8 real files, so every build failed on the first
 * missing document. Add the document first, then the entry.
 */
const sidebars: SidebarsConfig = {
  guidesSidebar: [
    {
      type: 'category',
      label: 'Agents',
      collapsed: false,
      items: ['agents/overview'],
    },
    {
      type: 'category',
      label: 'Tools',
      items: ['tools/built-in-tools'],
    },
    {
      type: 'category',
      label: 'Memory',
      items: ['memory/working-with-memory'],
    },
    {
      type: 'category',
      label: 'Goals',
      items: ['goals/overview'],
    },
    {
      type: 'category',
      label: 'Skills',
      items: ['skills/overview'],
    },
    {
      type: 'category',
      label: 'Sandbox',
      collapsed: false,
      items: ['sandbox/overview', 'sandbox/docker', 'sandbox/e2b'],
    },
  ],
};

export default sidebars;
