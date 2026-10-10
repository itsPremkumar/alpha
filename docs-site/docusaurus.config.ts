import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

// This runs in Node.js - Don't use client-side code here (browser APIs, JSX...)

const config: Config = {
  title: 'Alpha Documentation',
  tagline: 'An autonomous AI agent framework with sandboxed execution, persistent memory, subagent delegation, and extensible tooling.',
  favicon: 'img/favicon.ico',

  // Set the production url of your site here
  url: 'https://docs.alpha.itsPremkumar.com',
  // Set the /<baseUrl>/ pathname under which your site is served
  baseUrl: '/',

  // GitHub pages deployment config
  organizationName: 'itsPremkumar',
  projectName: 'alpha',

  onBrokenLinks: 'throw',
  onBrokenMarkdownLinks: 'warn',

  // Even if you don't use internationalization, you can use this field to set
  // useful metadata like html lang. For example, if your site is Chinese, you
  // may want to replace "en" with "zh-Hans".
  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          editUrl: 'https://github.com/itsPremkumar/alpha/tree/main/docs-site/docs/',
          showLastUpdateTime: true,
          showLastUpdateAuthor: true,
          routeBasePath: '/',
        },
        blog: {
          showReadingTime: true,
          blogSidebarTitle: 'All posts',
          blogSidebarCount: 'ALL',
          feedOptions: {
            type: ['rss', 'atom'],
            xslt: true,
          },
          editUrl: 'https://github.com/itsPremkumar/alpha/tree/main/docs-site/blog/',
          onInlineTags: 'warn',
          onInlineAuthors: 'warn',
          onUntruncatedBlogPosts: 'warn',
        },
        theme: {
          customCss: './src/css/custom.css',
        },
        sitemap: {
          changefreq: 'weekly',
          priority: 0.5,
          ignorePatterns: ['/tags/**'],
          filename: 'sitemap.xml',
        },
        gtag: {
          trackingID: 'G-XXXXXXXXXX',
          anonymizeIP: true,
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    // Replace with your project's social card
    image: 'img/alpha-social-card.jpg',
    navbar: {
      title: 'Alpha',
      logo: {
        alt: 'Alpha Logo',
        src: 'img/logo.svg',
      },
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'tutorialSidebar',
          position: 'left',
          label: 'Documentation',
        },
        {to: '/api', label: 'API Reference', position: 'left'},
        {to: '/guides', label: 'Guides', position: 'left'},
        {to: '/tutorials', label: 'Tutorials', position: 'left'},
        {to: '/blog', label: 'Blog', position: 'left'},
        {
          type: 'dropdown',
          label: 'Community',
          position: 'left',
          items: [
            {label: 'GitHub', href: 'https://github.com/itsPremkumar/alpha'},
            {label: 'Discord', href: 'https://discord.gg/alpha'},
            {label: 'Twitter', href: 'https://twitter.com/alpha_ai'},
            {label: 'Discussions', href: 'https://github.com/itsPremkumar/alpha/discussions'},
          ],
        },
        {
          href: 'https://github.com/itsPremkumar/alpha',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            {label: 'Getting Started', to: '/getting-started'},
            {label: 'Architecture', to: '/architecture'},
            {label: 'API Reference', to: '/api'},
            {label: 'Guides', to: '/guides'},
            {label: 'Tutorials', to: '/tutorials'},
          ],
        },
        {
          title: 'Community',
          items: [
            {label: 'Discord', href: 'https://discord.gg/alpha'},
            {label: 'Discussions', href: 'https://github.com/itsPremkumar/alpha/discussions'},
            {label: 'Twitter', href: 'https://twitter.com/alpha_ai'},
            {label: 'GitHub', href: 'https://github.com/itsPremkumar/alpha'},
          ],
        },
        {
          title: 'More',
          items: [
            {label: 'Blog', to: '/blog'},
            {label: 'GitHub', href: 'https://github.com/itsPremkumar/alpha'},
            {label: 'Contributing', to: '/contributing'},
            {label: 'Changelog', to: '/changelog'},
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} Alpha. Built with Docusaurus.`,
      prismTheme: prismThemes.github,
      darkModeTheme: prismThemes.dracula,
      algolia: {
        appId: 'YOUR_APP_ID',
        apiKey: 'YOUR_API_KEY',
        indexName: 'alpha',
        contextualSearch: true,
      },
      colorMode: {
        defaultMode: 'light',
        disableSwitch: false,
        respectPrefersColorScheme: true,
      },
      announcementBar: {
        id: 'announcement',
        content: '⭐ Star us on GitHub! <a href="https://github.com/itsPremkumar/alpha" target="_blank" rel="noopener noreferrer">github.com/itsPremkumar/alpha</a>',
        backgroundColor: '#fafafa',
        textColor: '#091E42',
        isCloseable: true,
      },
      metadata: [
        {name: 'keywords', content: 'AI, agent, autonomous, LangGraph, sandbox, memory, tools, MCP, subagents'},
        {name: 'description', content: 'Alpha - An autonomous AI agent framework with sandboxed execution, persistent memory, subagent delegation, and extensible tooling.'},
        {name: 'twitter:card', content: 'summary_large_image'},
        {name: 'twitter:site', content: '@alpha_ai'},
        {name: 'og:title', content: 'Alpha Documentation'},
        {name: 'og:description', content: 'An autonomous AI agent framework with sandboxed execution, persistent memory, subagent delegation, and extensible tooling.'},
        {name: 'og:image', content: 'https://docs.alpha.itsPremkumar.com/img/alpha-social-card.jpg'},
      ],
    } satisfies Preset.ThemeConfig,
  },

  plugins: [
    [
      '@docusaurus/plugin-content-docs',
      {
        id: 'api',
        path: 'api',
        routeBasePath: 'api',
        sidebarPath: './sidebars-api.ts',
        editUrl: 'https://github.com/itsPremkumar/alpha/tree/main/docs-site/docs/api/',
      },
    ],
    [
      '@docusaurus/plugin-content-docs',
      {
        id: 'guides',
        path: 'guides',
        routeBasePath: 'guides',
        sidebarPath: './sidebars-guides.ts',
        editUrl: 'https://github.com/itsPremkumar/alpha/tree/main/docs-site/docs/guides/',
      },
    ],
    [
      '@docusaurus/plugin-content-docs',
      {
        id: 'tutorials',
        path: 'tutorials',
        routeBasePath: 'tutorials',
        sidebarPath: './sidebars-tutorials.ts',
        editUrl: 'https://github.com/itsPremkumar/alpha/tree/main/docs-site/docs/tutorials/',
      },
    ],
    [
      '@docusaurus/plugin-ideal-image',
      {
        quality: 70,
        max: 1030,
        min: 640,
        steps: 2,
        disableInDev: false,
      },
    ],
    [
      '@docusaurus/plugin-pwa',
      {
        debug: true,
        offlineModeActivationStrategies: ['appInstalled', 'standalone', 'queryString'],
        pwaHead: [
          {tagName: 'link', rel: 'icon', href: '/img/favicon.ico'},
          {tagName: 'link', rel: 'manifest', href: '/manifest.json'},
          {tagName: 'meta', name: 'theme-color', content: '#091E42'},
        ],
      },
    ],
    [
      '@docusaurus/plugin-client-redirects',
      {
        redirects: [
          {to: '/getting-started', from: '/docs/getting-started'},
          {to: '/architecture', from: '/docs/architecture'},
          {to: '/api', from: '/docs/api'},
        ],
        createRedirects: function(existingPath) {
          return undefined;
        },
      },
    ],
    [
      '@docusaurus/plugin-sitemap',
      {
        changefreq: 'weekly',
        priority: 0.5,
        ignorePatterns: ['/tags/**'],
        filename: 'sitemap.xml',
      },
    ],
    [
      '@docusaurus/plugin-google-analytics',
      {
        trackingID: 'G-XXXXXXXXXX',
        anonymizeIP: true,
      },
    ],
    [
      '@docusaurus/plugin-google-gtag',
      {
        trackingID: 'G-XXXXXXXXXX',
        anonymizeIP: true,
      },
    ],
    'docusaurus-plugin-sass',
  ],

  stylesheets: [
    'https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap',
    'https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap',
  ],

  scripts: [
    {
      src: 'https://buttons.github.io/buttons.js',
      async: true,
    },
  ],

  markdown: {
    mermaid: true,
    format: 'mdx',
    preprocess: ({filePath, fileContent}) => {
      return fileContent.replace(/\{\{VERSION\}\}/g, '1.0.0');
    },
  },

  themes: ['@docusaurus/theme-mermaid'],
};

export default config;