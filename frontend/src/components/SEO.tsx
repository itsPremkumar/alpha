'use client';

import { useEffect } from 'react';
import Head from 'next/head';

interface SEOProps {
  title?: string;
  description?: string;
  canonical?: string;
  ogTitle?: string;
  ogDescription?: string;
  ogImage?: string;
  ogType?: 'website' | 'article';
  twitterCard?: 'summary' | 'summary_large_image';
  twitterSite?: string;
  twitterCreator?: string;
  jsonLd?: object;
  noindex?: boolean;
  nofollow?: boolean;
}

export function SEO({
  title = 'Alpha - Autonomous AI Agent Framework',
  description = 'Alpha - An autonomous AI agent framework with sandboxed execution, persistent memory, subagent delegation, and extensible tooling.',
  canonical = 'https://alpha.itsPremkumar.com',
  ogTitle = 'Alpha - Autonomous AI Agent Framework',
  ogDescription = 'Alpha - An autonomous AI agent framework with sandboxed execution, persistent memory, subagent delegation, and extensible tooling.',
  ogImage = 'https://alpha.itsPremkumar.com/og-image.png',
  ogType = 'website',
  twitterCard = 'summary_large_image',
  twitterSite = '@alpha_ai',
  twitterCreator = '@itsPremkumar',
  jsonLd,
  noindex = false,
  nofollow = false,
}: SEOProps) {
  const siteName = 'Alpha';
  const siteUrl = 'https://alpha.itsPremkumar.com';
  const ogUrl = typeof window !== 'undefined' ? window.location.href : 'https://alpha.itsPremkumar.com';

  // Generate JSON-LD structured data
  const structuredData = jsonLd || {
    '@context': 'https://schema.org',
    '@type': 'SoftwareApplication',
    name: 'Alpha',
    applicationCategory: 'DeveloperApplication',
    operatingSystem: 'Cross-platform',
    offers: {
      '@type': 'Offer',
      price: '0',
      priceCurrency: 'USD',
      availability: 'https://schema.org/InStock',
    },
    description: 'An autonomous AI agent framework with sandboxed execution, persistent memory, subagent delegation, and extensible tooling.',
    author: {
      '@type': 'Organization',
      name: 'Alpha',
      url: 'https://alpha.itsPremkumar.com',
      logo: 'https://alpha.itsPremkumar.com/logo.svg',
      sameAs: [
        'https://github.com/itsPremkumar/alpha',
        'https://twitter.com/alpha_ai',
        'https://discord.gg/alpha',
      ],
    },
    publisher: {
      '@type': 'Organization',
      name: 'Alpha',
      logo: {
        '@type': 'ImageObject',
        url: 'https://alpha.itsPremkumar.com/logo.svg',
      },
    },
    datePublished: '2024-01-01',
    dateModified: new Date().toISOString().split('T')[0],
    keywords: 'AI, agent, autonomous, LangGraph, sandbox, memory, tools, MCP, subagents, autonomous agents',
    featureList: [
      'Sandboxed execution',
      'Persistent memory',
      'Subagent delegation',
      'Extensible tooling',
      'MCP integration',
      'Multi-agent swarms',
      'Autonomous goals',
      'Persistent memory',
      'IM bridges',
    ],
    screenshot: 'https://alpha.itsPremkumar.com/screenshot.png',
    softwareVersion: '1.0.0',
    url: 'https://alpha.itsPremkumar.com',
  };

  return (
    <Head>
      {/* Basic Meta Tags */}
      <title>{title}</title>
      <meta name="description" content={description} />
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <link rel="canonical" href={canonical} />
      <meta name="theme-color" content="#091E42" />

      {/* Robots */}
      <meta name="robots" content={`${noindex ? 'noindex' : 'index'}, ${nofollow ? 'nofollow' : 'follow'}`} />
      <meta name="googlebot" content={`${noindex ? 'noindex' : 'index'}, ${nofollow ? 'nofollow' : 'follow'}`} />

      {/* Open Graph */}
      <meta property="og:type" content={ogType} />
      <meta property="og:url" content={ogUrl} />
      <meta property="og:title" content={ogTitle} />
      <meta property="og:description" content={ogDescription} />
      <meta property="og:image" content={ogImage} />
      <meta property="og:image:width" content="1200" />
      <meta property="og:image:height" content="630" />
      <meta property="og:image:alt" content={ogTitle} />
      <meta property="og:site_name" content={siteName} />
      <meta property="og:locale" content="en_US" />

      {/* Twitter Card */}
      <meta name="twitter:card" content={twitterCard} />
      <meta name="twitter:site" content={twitterSite} />
      <meta name="twitter:creator" content={twitterCreator} />
      <meta name="twitter:url" content={ogUrl} />
      <meta name="twitter:title" content={ogTitle} />
      <meta name="twitter:description" content={ogDescription} />
      <meta name="twitter:image" content={ogImage} />
      <meta name="twitter:image:alt" content={ogTitle} />

      {/* Additional Meta */}
      <meta name="theme-color" content="#091E42" />
      <meta name="msapplication-TileColor" content="#091E42" />
      <meta name="msapplication-config" content="/browserconfig.xml" />

      {/* JSON-LD Structured Data */}
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(structuredData) }}
      />

      {/* Preconnect for performance */}
      <link rel="preconnect" href="https://fonts.googleapis.com" crossOrigin="anonymous" />
      <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
      <link rel="dns-prefetch" href="https://alpha.itsPremkumar.com" />
      <link rel="dns-prefetch" href="https://api.alpha.itsPremkumar.com" />

      {/* Favicon & Icons */}
      <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
      <link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png" />
      <link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png" />
      <link rel="apple-touch-icon" href="/apple-touch-icon.png" />
      <link rel="manifest" href="/manifest.json" />
      <link rel="mask-icon" href="/safari-pinned-tab.svg" color="#091E42" />
    </Head>
  );
}

export default SEO;