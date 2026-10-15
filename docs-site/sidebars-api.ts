import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

/**
 * The API reference sidebar (`docs/api/`).
 *
 * It listed 117 ids for a directory that holds two documents, so the build
 * could never pass. Add the document first, then the entry.
 */
const sidebars: SidebarsConfig = {
  apiSidebar: ['overview', 'agents'],
};

export default sidebars;
